from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wos_filter.merge_scope import annotate_pairs, export_scope_pairs, historical_snapshot
from wos_filter.plain_merge import merge_files_without_changes
from wos_filter.wos import parse_wos_file


WOS = """FN Clarivate Analytics Web of Science
VR 1.0
PT J
AU Li, A
   Wu, B
TI Same title
DE water-waste; water waste
PY 2024
ER

EF
"""
RIS = """TY  - JOUR
TI  - Same title
AU  - Zhang, C
KW  - water-waste
PY  - 2024
ER  -
"""


class PlainMergeTests(unittest.TestCase):
    def test_cross_source_merge_keeps_duplicates_and_original_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wos = root / "wos.txt"
            ris = root / "second.ris"
            output = root / "download.txt"
            wos.write_text(WOS, encoding="utf-8")
            ris.write_text(RIS, encoding="utf-8")
            result = merge_files_without_changes([str(wos), str(ris)], output)
            raw = output.read_bytes()
            text = raw.decode("utf-8")
            parsed = parse_wos_file(output)
            self.assertEqual(result.records, 2)
            self.assertEqual(len(parsed.records), 2)
            self.assertEqual([row.title for row in parsed.records], ["Same title", "Same title"])
            self.assertEqual(parsed.records[0].values("DE"), ["water-waste; water waste"])
            self.assertEqual(parsed.records[1].values("DE"), ["water-waste"])
            self.assertTrue(text.startswith("FN Clarivate Analytics Web of Science\nVR 1.0\nPT J\n"))
            self.assertIn("AU Li, A\n   Wu, B\n", text)
            self.assertEqual(text.count("\nER\n"), 2)
            self.assertTrue(text.endswith("\nEF\n"))
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", raw)

    def test_history_and_current_file_membership_are_independent(self) -> None:
        snapshot = historical_snapshot({"countries": [{"target": "China", "variant": "PR China"}]})
        rows = annotate_pairs([
            {"target": "China", "variant": "PR China", "occurrences": 3},
            {"target": "China", "variant": "P. R. China", "occurrences": 0},
        ], "countries", snapshot)
        self.assertEqual(rows[0]["catalog_scope"], "历史沿用")
        self.assertEqual(rows[1]["catalog_scope"], "本项目生成")
        self.assertEqual(len(export_scope_pairs(rows, "all")), 2)
        self.assertEqual(len(export_scope_pairs(rows, "current")), 1)


if __name__ == "__main__":
    unittest.main()
