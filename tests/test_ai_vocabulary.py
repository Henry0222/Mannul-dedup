from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from wos_filter.ai_vocabulary import (DeepSeekVocabularyClient, Suggestion, collect_inventory, plan_batches,
                                      needs_new_dedupe_review, retained_original_records,
                                      stage_suggestions, suggest_category)
from wos_filter.deepseek import DeepSeekError
from wos_filter.models import WosRecord
from wos_filter.project_workspace import record_key
from wos_filter.vocabulary import merge_vocabulary
from wos_filter.vocabulary_review import list_merge_pairs, upsert_pair


def record(index: int, *, keywords: str, source: str = "sample.txt") -> WosRecord:
    return WosRecord({"TI": [f"Paper {index}"], "DE": [keywords],
                      "AU": ["J Smith"], "C3": ["Peking University"],
                      "CO": ["China"], "SO": ["Journal of Tests"], "SN": ["1234-5678"]},
                     "", source, index)


class FakeClient:
    def __init__(self):
        self.seen = []

    def suggest(self, kind, batch, anchors):
        self.seen.append((kind, [item["term"] for item in batch]))
        if kind == "keywords" and {"water waste", "water-waste"} <= set(self.seen[-1][1]):
            return {"merges": [
                {"keep": "water waste", "merge": "water-waste", "reason": "词形相同"},
                {"keep": "made up", "merge": "water-waste", "reason": "幻觉"},
            ]}
        return {"merges": []}


class AIVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.records = [record(1, keywords="water waste; water-waste"),
                        record(2, keywords="climate")]

    def test_retained_original_records_uses_source_identity_not_shared_doi(self):
        first = record(1, keywords="water waste", source="one.txt")
        second = record(1, keywords="water-waste", source="two.txt")
        first.fields["DI"] = ["10.1/same"]
        second.fields["DI"] = ["10.1/same"]
        self.assertEqual(retained_original_records([first, second], [first]), [first])

    def test_each_category_inventory_and_batches_cover_only_its_terms(self):
        kinds = ("countries", "organizations", "authors", "journals", "keywords")
        inventories = {kind: collect_inventory(self.records, kind) for kind in kinds}
        self.assertEqual({item["term"] for item in inventories["keywords"]},
                         {"water waste", "water-waste", "climate"})
        self.assertIn("China", inventories["organizations"][0]["context"])
        for kind, entries in inventories.items():
            batches = plan_batches(entries)
            self.assertEqual([row["term"] for batch, _ in batches for row in batch],
                             [row["term"] for row in entries], kind)

    def test_model_output_is_grounded_and_staged_with_source(self):
        client = FakeClient()
        suggestions, stats = suggest_category(self.records, "keywords", client)
        self.assertEqual(stats["records"], 2)
        self.assertEqual(stats["kind"], "keywords")
        self.assertEqual([kind for kind, _ in client.seen], ["keywords"])
        self.assertEqual(len(suggestions), 1)
        draft, accepted, rejected = stage_suggestions({}, self.records, suggestions)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, 0)
        changed, report = merge_vocabulary(self.records, draft)
        self.assertEqual(changed[0].values("DE"), ["Water waste"])
        pairs = list_merge_pairs(self.records, draft, report, "keywords")
        self.assertTrue(any(pair["origin"] == "DeepSeek" for pair in pairs))

    def test_existing_operator_rule_cannot_be_overwritten(self):
        spec = upsert_pair({}, "keywords", "preferred", "water-waste")
        draft, accepted, rejected = stage_suggestions(
            spec, self.records, [Suggestion("keywords", "water waste", "water-waste", "似乎相同")])
        self.assertFalse(accepted)
        self.assertEqual(rejected, 1)
        self.assertEqual(draft, spec)

    def test_cross_batch_coverage_and_conflicting_suggestions(self):
        records = [record(index, keywords=f"topic{index}") for index in range(185)]
        batches = plan_batches(collect_inventory(records, "keywords"))
        keywords = [row["term"] for batch, _ in batches for row in batch]
        self.assertEqual(len(keywords), 185)
        self.assertEqual(len(set(keywords)), 185)
        choices = [Suggestion("keywords", "water waste", "water-waste", "a"),
                   Suggestion("keywords", "climate", "water-waste", "b")]
        _, accepted, rejected = stage_suggestions({}, self.records, choices)
        self.assertFalse(accepted)
        self.assertEqual(rejected, 2)

    def test_empty_selected_category_makes_no_api_request(self):
        client = FakeClient()
        suggestions, stats = suggest_category([WosRecord({"TI": ["Paper"]}, "", "empty.txt", 1)],
                                              "journals", client)
        self.assertEqual(suggestions, [])
        self.assertEqual(stats["terms"], 0)
        self.assertEqual(client.seen, [])

    def test_batch_staging_validates_full_bibliography_once(self):
        records = [WosRecord({"TI": [f"Paper {index}"], "CO": [country]}, "", "countries.txt", index)
                   for index, country in enumerate(("Country A", "Country B", "Country C", "Country D"), 1)]
        proposals = [Suggestion("countries", "Country A", variant, "same entity")
                     for variant in ("Country B", "Country C", "Country D")]
        with patch("wos_filter.ai_vocabulary.merge_vocabulary", wraps=merge_vocabulary) as validate:
            _, accepted, rejected = stage_suggestions({}, records, proposals)
        self.assertEqual((len(accepted), rejected), (3, 0))
        self.assertEqual(validate.call_count, 1)

    def test_keyword_style_prefers_unhyphenated_target_and_keeps_ai_provenance(self):
        records = [record(1, keywords="water-waste; water waste")]
        draft, accepted, rejected = stage_suggestions({}, records, [
            Suggestion("keywords", "water-waste", "water waste", "same spelling")])
        self.assertEqual((len(accepted), rejected), (1, 0))
        changed, report = merge_vocabulary(records, draft)
        self.assertEqual(changed[0].values("DE"), ["Water waste"])
        pairs = list_merge_pairs(records, draft, report, "keywords")
        self.assertTrue(all(row["origin"] == "DeepSeek" for row in pairs))
        self.assertEqual({row["variant"] for row in pairs}, {"water-waste", "water waste"})

    def test_ai_keyword_scope_changes_are_rejected_and_hyphenated_target_is_restyled(self):
        records = [record(1, keywords="16S rRNA; 16S rRNA sequencing; cancer; breast cancer; "
                             "water; wastewater; adeno-associated virus; adenoassociated viruses")]
        proposals = [
            Suggestion("keywords", "16S rRNA", "16S rRNA sequencing", "related"),
            Suggestion("keywords", "cancer", "breast cancer", "related"),
            Suggestion("keywords", "water", "wastewater", "related"),
            Suggestion("keywords", "adeno-associated virus", "adenoassociated viruses", "same"),
        ]
        draft, accepted, rejected = stage_suggestions({}, records, proposals)
        self.assertEqual((len(accepted), rejected), (1, 3))
        normalized, _ = merge_vocabulary(records, draft)
        self.assertIn("Adeno associated virus", normalized[0].values("DE"))
        self.assertIn("16S rRNA sequencing", normalized[0].values("DE"))
        self.assertIn("breast cancer", normalized[0].values("DE"))

    def test_existing_operator_target_spelling_is_preserved(self):
        spec = upsert_pair({}, "keywords", "DNA repair", "DNA repairs")
        draft, accepted, rejected = stage_suggestions(spec, self.records, [
            Suggestion("keywords", "DNA repair", "D.N.A. repair", "same term")])
        self.assertEqual((len(accepted), rejected), (1, 0))
        self.assertEqual(list_merge_pairs(self.records, draft, {}, "keywords")[0]["target"],
                         "DNA repair")

    def test_large_conflicting_batch_uses_group_validation(self):
        records = [record(1, keywords="water waste; water-waste")]
        proposals = [Suggestion("keywords", f"topic{i}", f"alias{i}", "same")
                     for i in range(797)]
        proposals.extend([Suggestion("keywords", "alpha", "water waste", "same"),
                          Suggestion("keywords", "beta", "water-waste", "same")])
        with patch("wos_filter.ai_vocabulary.merge_vocabulary", wraps=merge_vocabulary) as validate:
            _, accepted, rejected = stage_suggestions({}, records, proposals)
        self.assertEqual((len(accepted), rejected), (798, 1))
        self.assertLess(validate.call_count, 40)

    def test_staging_can_be_cancelled(self):
        with self.assertRaises(DeepSeekError):
            stage_suggestions({}, self.records,
                              [Suggestion("keywords", "water waste", "water-waste", "test")],
                              cancelled=lambda: True)

    def test_new_journal_match_requires_dedupe_review_but_keyword_merge_does_not(self):
        records = [WosRecord({"TI": ["Same paper"], "PY": ["2024"], "AU": ["Smith A"],
                              "SO": [journal]}, "", f"source{index}.txt", 1)
                   for index, journal in enumerate(("Journal of Tests", "J Test"), 1)]
        project = {"excluded_keys": [], "runs": [{"preset": "journal", "groups": [], "applied": True}]}
        spec = upsert_pair({}, "journals", "Journal of Tests", "J Test")
        normalized, _ = merge_vocabulary(records, spec)
        self.assertTrue(needs_new_dedupe_review(project, normalized, "journals", None, None))
        self.assertFalse(needs_new_dedupe_review(project, normalized, "keywords", None, None))
        project["runs"][0]["groups"] = [{"record_keys": [record_key(item) for item in records],
                                         "status": "ignored"}]
        self.assertFalse(needs_new_dedupe_review(project, normalized, "journals", None, None))

    def test_deepseek_transport_requests_json_and_only_sends_term_inventory(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"choices": [{"finish_reason": "stop", "message": {
                    "content": '{"merges":[]}'}}]}).encode("utf-8")

        def fake_urlopen(request, *, timeout):
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("wos_filter.ai_vocabulary.urllib.request.urlopen", fake_urlopen):
            response = DeepSeekVocabularyClient("test-key", retries=0).suggest(
                "keywords", [{"term": "water-waste", "count": 2, "context": []}], [])
        self.assertEqual(response, {"merges": []})
        self.assertEqual(captured["body"]["model"], "deepseek-flash")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertNotIn("Paper 1", json.dumps(captured["body"]))


if __name__ == "__main__":
    unittest.main()
