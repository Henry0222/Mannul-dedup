from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from wos_filter.cache import JsonlAuditLog, ResultCache
from wos_filter.deepseek import (
    ABSTRACT_OMITTED_NOTE,
    FALLBACK_MAX_TOKENS,
    INITIAL_MAX_TOKENS,
    DeepSeekClient,
)
from wos_filter.file_drop import expand_txt_inputs
from wos_filter.jev import (
    CONSERVATIVE_INCLUDE_NOTE,
    DEFAULT_MODEL,
    MIN_CHOICE_CONFIDENCE,
    MIN_IRRELEVANT_PROBABILITY,
    JevClient,
    _parse_api_response,
    build_request,
    make_cache_key,
)
from wos_filter.hybrid import (
    DEFAULT_DEEPSEEK_REVIEW_CONCURRENCY,
    HybridClient,
    classify_with_cache as classify_with_hybrid_cache,
)
from wos_filter.models import Classification, WosRecord
from wos_filter.wos import build_citespace_text, deduplicate, parse_wos_file
from wos_filter.year_filter import format_year_range, parse_year_range, partition_records_by_year


SAMPLE = """FN Clarivate Web of Science
VR 1.0
PT J
AU Zhang, A
   Li, B
TI Artificial intelligence in higher education
   and student support
SO JOURNAL OF TESTS
AB This study investigates AI-assisted learning.
DE artificial intelligence
   higher education
PY 2025
DI 10.1000/TEST.1
UT WOS:0001
CR Smith J, 2020, TEST J, V1, P1
ER

PT J
AU Zhang, A
TI Artificial intelligence in higher education
SO JOURNAL OF TESTS
DE artificial intelligence
   higher education
PY 2025
DI https://doi.org/10.1000/test.1
UT WOS:0002
ER

PT J
AU Wang, C
TI Medical imaging systems
SO ANOTHER JOURNAL
PY 2024
UT WOS:0003
ER

EF
"""


class FakeHttpResponse:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.raw.encode("utf-8")


def api_response(
    *,
    choice: str = "relevant",
    confidence: float = 0.8,
    probabilities: dict[str, float] | None = None,
) -> str:
    probabilities = probabilities or {"relevant": 0.82, "irrelevant": 0.10, "uncertain": 0.08}
    return json.dumps({
        "model": "jev-1.13.0",
        "answers": {
            "relevance": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": probabilities,
            }
        },
        "usage": {"input_tokens": 320, "output_tokens": 34},
    }, ensure_ascii=False)


def deepseek_api_response(*, finish_reason: str = "stop") -> str:
    content = json.dumps({
        "record_id": "WOS:1",
        "decision": "relevant",
        "confidence": 0.9,
        "reason": "题名和关键词符合主题",
        "evidence": ["AI in education"],
    }, ensure_ascii=False)
    return json.dumps({
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}]
    }, ensure_ascii=False)


class CoreTests(unittest.TestCase):
    def test_expand_dropped_txt_files_and_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "文献 1.txt"
            first.write_text("first", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            second = nested / "SECOND.TXT"
            second.write_text("second", encoding="utf-8")
            ignored = root / "notes.csv"
            ignored.write_text("ignored", encoding="utf-8")
            missing = root / "missing.txt"

            accepted, warnings = expand_txt_inputs([first, root, ignored, missing])

        self.assertEqual(accepted, [str(first.resolve()), str(second.resolve())])
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("不是 TXT" in warning for warning in warnings))
        self.assertTrue(any("路径不存在" in warning for warning in warnings))

    def test_parse_multiline_and_deduplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "savedrecs.txt"
            path.write_text(SAMPLE, encoding="utf-8")
            parsed = parse_wos_file(path)
        self.assertEqual(len(parsed.records), 3)
        self.assertEqual(parsed.records[0].values("AU"), ["Zhang, A", "Li, B"])
        self.assertIn("higher education", parsed.records[0].text("DE"))
        self.assertEqual(parsed.records[0].title, "Artificial intelligence in higher education and student support")
        unique, logs = deduplicate(parsed.records)
        self.assertEqual(len(unique), 2)
        self.assertEqual(len(logs), 1)
        self.assertEqual(unique[0].record_id, "WOS:0001")

    def test_citespace_preserves_record_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "savedrecs.txt"
            path.write_text(SAMPLE, encoding="utf-8")
            parsed = parse_wos_file(path)
        output = build_citespace_text(parsed.header, [parsed.records[0], parsed.records[2]])
        self.assertEqual(output.count("PT J"), 2)
        self.assertEqual(output.count("\nEF\n"), 1)
        self.assertIn("CR Smith J, 2020", output)
        self.assertNotIn("WOS:0002", output)

    def test_jev_request_and_response(self) -> None:
        record = WosRecord(
            fields={"TI": ["AI in education"], "DE": ["AI"], "UT": ["WOS:1"]},
            raw_block="PT J\nTI AI in education\nER", source_file="x.txt", source_index=1,
        )
        request = build_request(record, "TS=(AI AND education)")
        self.assertEqual(request["model"], DEFAULT_MODEL)
        self.assertEqual(request["state"]["record"]["title"], "AI in education")
        self.assertEqual(request["questions"]["relevance"]["type"], "choice")
        self.assertEqual(
            set(request["questions"]["relevance"]["criteria"]),
            {"relevant", "irrelevant", "uncertain"},
        )
        self.assertEqual(make_cache_key(record, "Q"), make_cache_key(record, "Q"))

        with patch("wos_filter.jev.urllib.request.urlopen", return_value=FakeHttpResponse(api_response())) as urlopen:
            result = JevClient(api_key="test-key", retries=0).classify(record, "TS=(AI AND education)")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], DEFAULT_MODEL)
        self.assertEqual(payload["state"]["wos_query"], "TS=(AI AND education)")
        self.assertEqual(result.decision, "relevant")
        self.assertEqual(result.final_decision, "relevant")
        self.assertIn("相关 82.0%", result.reason)

    def test_jev_low_confidence_irrelevant_is_conservatively_included(self) -> None:
        result = _parse_api_response(
            api_response(
                choice="irrelevant",
                confidence=0.42,
                probabilities={"relevant": 0.25, "irrelevant": 0.65, "uncertain": 0.10},
            ),
            "WOS:1",
        )
        self.assertEqual(result.decision, "relevant")
        self.assertIn("高召回策略", result.reason)
        self.assertTrue(result.missing_information)

    def test_jev_uses_stricter_irrelevant_probability_threshold(self) -> None:
        below_threshold = MIN_IRRELEVANT_PROBABILITY - 0.01
        result = _parse_api_response(
            api_response(
                choice="irrelevant",
                confidence=0.80,
                probabilities={
                    "relevant": 0.21,
                    "irrelevant": below_threshold,
                    "uncertain": 0.10,
                },
            ),
            "WOS:1",
        )
        self.assertEqual(result.decision, "relevant")
        self.assertFalse(result.exclusion_reason)

        at_threshold = _parse_api_response(
            api_response(
                choice="irrelevant",
                confidence=MIN_CHOICE_CONFIDENCE,
                probabilities={
                    "relevant": 0.20,
                    "irrelevant": MIN_IRRELEVANT_PROBABILITY,
                    "uncertain": 0.05,
                },
            ),
            "WOS:2",
        )
        self.assertEqual(MIN_CHOICE_CONFIDENCE, 0.60)
        self.assertEqual(MIN_IRRELEVANT_PROBABILITY, 0.75)
        self.assertEqual(at_threshold.decision, "irrelevant")

    def test_jev_explicit_uncertain_still_routes_to_review(self) -> None:
        result = _parse_api_response(
            api_response(
                choice="uncertain",
                confidence=0.30,
                probabilities={"relevant": 0.30, "irrelevant": 0.25, "uncertain": 0.45},
            ),
            "WOS:1",
        )
        self.assertEqual(result.decision, "uncertain")
        self.assertTrue(result.missing_information)

    def test_jev_retries_remote_disconnect(self) -> None:
        record = WosRecord(
            fields={"TI": ["AI in education"], "UT": ["WOS:1"]},
            raw_block="PT J\nTI AI in education\nER", source_file="x.txt", source_index=1,
        )
        responses = [
            http.client.RemoteDisconnected("remote end closed connection"),
            FakeHttpResponse(api_response()),
        ]
        with (
            patch("wos_filter.jev.urllib.request.urlopen", side_effect=responses) as urlopen,
            patch("wos_filter.jev.time.sleep"),
        ):
            result = JevClient(api_key="test-key", retries=1).classify(record, "TS=(AI AND education)")

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(result.decision, "relevant")

    def test_hybrid_skips_deepseek_for_decisive_jev_result(self) -> None:
        record = WosRecord(
            fields={"TI": ["Unrelated topic"], "UT": ["WOS:1"]},
            raw_block="PT J\nTI Unrelated topic\nER", source_file="x.txt", source_index=1,
        )
        jev_result = Classification("WOS:1", "irrelevant", 0.91, "Jev 明确不相关")
        client = HybridClient(JevClient("jev-key"), DeepSeekClient("ds-key"))
        with tempfile.TemporaryDirectory() as temp:
            cache = ResultCache(Path(temp) / "cache.sqlite3")
            audit = JsonlAuditLog(Path(temp) / "audit.jsonl")
            with (
                patch("wos_filter.hybrid.classify_with_jev_cache", return_value=jev_result),
                patch("wos_filter.hybrid.classify_with_deepseek_cache") as deepseek_call,
            ):
                result = classify_with_hybrid_cache(record, "Q", client, cache, audit)

        self.assertEqual(result.decision, "irrelevant")
        deepseek_call.assert_not_called()

    def test_hybrid_uses_deepseek_for_jev_boundary_result(self) -> None:
        record = WosRecord(
            fields={"TI": ["Borderline topic"], "UT": ["WOS:1"]},
            raw_block="PT J\nTI Borderline topic\nER", source_file="x.txt", source_index=1,
        )
        jev_result = Classification(
            "WOS:1", "relevant", 0.36, "Jev 边界结果",
            missing_information=[CONSERVATIVE_INCLUDE_NOTE],
        )
        deepseek_result = Classification(
            "WOS:1", "irrelevant", 0.95, "DeepSeek 复核不相关",
            exclusion_reason="复核后排除",
        )
        client = HybridClient(JevClient("jev-key"), DeepSeekClient("ds-key"))
        with tempfile.TemporaryDirectory() as temp:
            cache = ResultCache(Path(temp) / "cache.sqlite3")
            audit = JsonlAuditLog(Path(temp) / "audit.jsonl")
            with (
                patch("wos_filter.hybrid.classify_with_jev_cache", return_value=jev_result),
                patch("wos_filter.hybrid.classify_with_deepseek_cache", return_value=deepseek_result) as deepseek_call,
            ):
                result = classify_with_hybrid_cache(record, "Q", client, cache, audit)

        self.assertEqual(result.decision, "irrelevant")
        self.assertEqual(result.exclusion_reason, "复核后排除")
        self.assertIn("DeepSeek 复核", result.reason)
        deepseek_call.assert_called_once()

    def test_hybrid_keeps_safe_jev_result_when_deepseek_fails(self) -> None:
        record = WosRecord(
            fields={"TI": ["Borderline topic"], "UT": ["WOS:1"]},
            raw_block="PT J\nTI Borderline topic\nER", source_file="x.txt", source_index=1,
        )
        jev_result = Classification(
            "WOS:1", "relevant", 0.31, "Jev 高召回纳入",
            missing_information=[CONSERVATIVE_INCLUDE_NOTE],
        )
        deepseek_result = Classification(
            "WOS:1", "uncertain", 0.0, "DeepSeek API 调用失败",
            error="temporary failure",
        )
        client = HybridClient(JevClient("jev-key"), DeepSeekClient("ds-key"))
        with tempfile.TemporaryDirectory() as temp:
            cache = ResultCache(Path(temp) / "cache.sqlite3")
            audit = JsonlAuditLog(Path(temp) / "audit.jsonl")
            with (
                patch("wos_filter.hybrid.classify_with_jev_cache", return_value=jev_result),
                patch("wos_filter.hybrid.classify_with_deepseek_cache", return_value=deepseek_result),
            ):
                result = classify_with_hybrid_cache(record, "Q", client, cache, audit)

        self.assertEqual(result.decision, "relevant")
        self.assertIn("DeepSeek 复核失败", result.reason)
        self.assertTrue(result.error)

    def test_hybrid_limits_deepseek_review_concurrency(self) -> None:
        active = 0
        max_active = 0
        active_lock = threading.Lock()

        def boundary_jev(record, *_args, **_kwargs):
            return Classification(
                record.record_id,
                "relevant",
                0.40,
                "Jev 边界结果",
                missing_information=[CONSERVATIVE_INCLUDE_NOTE],
            )

        def slow_deepseek(record, *_args, **_kwargs):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with active_lock:
                active -= 1
            return Classification(record.record_id, "relevant", 0.90, "DeepSeek 复核相关")

        records = [
            WosRecord(
                fields={"TI": [f"Boundary {index}"], "UT": [f"WOS:{index}"]},
                raw_block=f"PT J\nTI Boundary {index}\nER",
                source_file="x.txt",
                source_index=index,
            )
            for index in range(6)
        ]
        client = HybridClient(
            JevClient("jev-key"),
            DeepSeekClient("ds-key"),
            deepseek_max_concurrency=2,
        )
        with tempfile.TemporaryDirectory() as temp:
            cache = ResultCache(Path(temp) / "cache.sqlite3")
            audit = JsonlAuditLog(Path(temp) / "audit.jsonl")
            with (
                patch("wos_filter.hybrid.classify_with_jev_cache", side_effect=boundary_jev),
                patch("wos_filter.hybrid.classify_with_deepseek_cache", side_effect=slow_deepseek),
                ThreadPoolExecutor(max_workers=6) as executor,
            ):
                results = list(
                    executor.map(
                        lambda record: classify_with_hybrid_cache(record, "Q", client, cache, audit),
                        records,
                    )
                )

        self.assertEqual(DEFAULT_DEEPSEEK_REVIEW_CONCURRENCY, 8)
        self.assertEqual(max_active, 2)
        self.assertTrue(all(result.decision == "relevant" for result in results))

    def test_deepseek_retries_connection_reset(self) -> None:
        record = WosRecord(
            fields={"TI": ["AI in education"], "UT": ["WOS:1"]},
            raw_block="PT J\nTI AI in education\nER",
            source_file="x.txt",
            source_index=1,
        )
        responses = [
            ConnectionResetError(10054, "remote host reset the connection"),
            FakeHttpResponse(deepseek_api_response()),
        ]
        with (
            patch("wos_filter.deepseek.urllib.request.urlopen", side_effect=responses) as urlopen,
            patch("wos_filter.deepseek.time.sleep"),
        ):
            result = DeepSeekClient(api_key="sk-test", retries=1).classify(
                record, "TS=(AI AND education)"
            )

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(result.decision, "relevant")

    def test_deepseek_max_token_fallback_is_preserved(self) -> None:
        record = WosRecord(
            fields={
                "TI": ["AI in education"],
                "AB": ["A long abstract that is removed only on the final fallback."],
                "DE": ["AI"],
                "UT": ["WOS:1"],
            },
            raw_block="PT J\nTI AI in education\nER",
            source_file="x.txt",
            source_index=1,
        )
        responses = [
            FakeHttpResponse(deepseek_api_response(finish_reason="length")),
            FakeHttpResponse(deepseek_api_response(finish_reason="length")),
            FakeHttpResponse(deepseek_api_response()),
        ]

        with patch("wos_filter.deepseek.urllib.request.urlopen", side_effect=responses) as urlopen:
            result = DeepSeekClient(api_key="sk-test", retries=0).classify(record, "TS=(AI AND education)")

        payloads = [json.loads(call.args[0].data.decode("utf-8")) for call in urlopen.call_args_list]
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual((payloads[0]["reasoning_effort"], payloads[0]["max_tokens"]), ("high", INITIAL_MAX_TOKENS))
        self.assertEqual((payloads[1]["reasoning_effort"], payloads[1]["max_tokens"]), ("low", FALLBACK_MAX_TOKENS))
        self.assertEqual((payloads[2]["reasoning_effort"], payloads[2]["max_tokens"]), ("low", FALLBACK_MAX_TOKENS))
        final_record = json.loads(payloads[2]["messages"][1]["content"].split("\n", 1)[1])["record"]
        self.assertEqual(final_record["abstract"], "")
        self.assertIn(ABSTRACT_OMITTED_NOTE, result.missing_information)

    def test_year_range_validation_and_formatting(self) -> None:
        self.assertEqual(parse_year_range("", ""), (None, None))
        self.assertEqual(parse_year_range("2015", ""), (2015, None))
        self.assertEqual(parse_year_range("", "2024"), (None, 2024))
        self.assertEqual(parse_year_range("2015", "2024"), (2015, 2024))
        self.assertEqual(format_year_range(2015, 2024), "2015–2024 年")
        self.assertEqual(format_year_range(2015, None), "2015 年及以后")
        self.assertEqual(format_year_range(None, 2024), "2024 年及以前")
        with self.assertRaisesRegex(ValueError, "四位数字"):
            parse_year_range("15", "2024")
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            parse_year_range("2025", "2024")

    def test_year_partition_keeps_unknown_years_for_screening(self) -> None:
        records = [
            WosRecord(
                fields={"TI": ["Old"], "PY": ["2014"], "UT": ["WOS:OLD"]},
                raw_block="PT J\nTI Old\nPY 2014\nER", source_file="x.txt", source_index=1,
            ),
            WosRecord(
                fields={"TI": ["Current"], "PY": ["2020"], "UT": ["WOS:CURRENT"]},
                raw_block="PT J\nTI Current\nPY 2020\nER", source_file="x.txt", source_index=2,
            ),
            WosRecord(
                fields={"TI": ["Unknown"], "UT": ["WOS:UNKNOWN"]},
                raw_block="PT J\nTI Unknown\nER", source_file="x.txt", source_index=3,
            ),
        ]

        included, excluded, unknown = partition_records_by_year(records, 2015, 2024)

        self.assertEqual([record.record_id for record in included], ["WOS:CURRENT", "WOS:UNKNOWN"])
        self.assertEqual([(record.record_id, year) for record, year in excluded], [("WOS:OLD", 2014)])
        self.assertEqual([record.record_id for record in unknown], ["WOS:UNKNOWN"])


if __name__ == "__main__":
    unittest.main()
