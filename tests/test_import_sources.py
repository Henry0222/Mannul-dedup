from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wos_filter.file_drop import expand_bibliography_inputs
from wos_filter.models import WosRecord
from wos_filter.source_import import ImportFormatError, import_one, parse_many_sources
from wos_filter.citation_backfill import backfill_citation_fields
from wos_filter.bibliometrics import citation_count, has_citation_count
from wos_filter.vocabulary import merge_vocabulary
from wos_filter.wos import deduplicate


class SourceImportTests(unittest.TestCase):
    def test_ris_citation_notes_and_existing_project_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wos = root / "wos.ris"
            wos.write_text("TY  - JOUR\nTI  - Citation example\nAN  - WOS:1\n"
                           "N1  - Times Cited in Web of Science Core Collection:  12\n"
                           "Total Times Cited:  15\nER  -\n", encoding="utf-8")
            scopus = root / "scopus.ris"
            scopus.write_text("TY  - JOUR\nTI  - Scopus example\nDB  - Scopus\n"
                              "N1  - Export Date: 05 September 2026; Cited By: 4; Note: other data\n"
                              "ER  -\n", encoding="utf-8")
            first, _, _ = import_one(wos)
            second, _, _ = import_one(scopus)
            self.assertEqual((first[0].text("TC"), first[0].text("Z9")), ("12", "15"))
            self.assertEqual(second[0].text("TC"), "4")
            for record in first + second:
                record.fields.pop("TC", None)
                record.fields.pop("Z9", None)
            recovered, failed = backfill_citation_fields(first + second)
            self.assertEqual((recovered, failed), (2, []))
            self.assertEqual((first[0].text("TC"), second[0].text("TC")), ("12", "4"))
            self.assertEqual((citation_count(first[0]), has_citation_count(first[0])), (12, True))
            self.assertFalse(has_citation_count(WosRecord({"TI": ["No citation field"]}, "", "x", 1)))
            second[0].fields["TC"] = ["0"]
            self.assertTrue(has_citation_count(second[0]))
            self.assertEqual(citation_count(second[0]), 0)

    def test_pubmed_txt_and_sciencedirect_ris_txt_detected_by_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pubmed = root / "pubmed.txt"
            pubmed.write_text(
                "PMID- 39776243\nTI  - Oral health review\nAU  - Smith AB\nDP  - 2025 Mar\n"
                "AID - 10.1000/pubmed [doi]\nAB  - A review.\n\n", encoding="utf-8")
            sd = root / "sciencedirect.txt"
            sd.write_text(
                "TY  - JOUR\nT1  - Oral health review\nAU  - Smith, A.\nPY  - 2025\n"
                "DO  - https://doi.org/10.1000/pubmed\n"
                "UR  - https://www.sciencedirect.com/science/article/pii/S123456\nER  -\n",
                encoding="utf-8")
            first, summary_a, _ = import_one(pubmed)
            second, summary_b, _ = import_one(sd)
            self.assertEqual((summary_a.source, summary_a.format), ("pubmed", "pubmed_nbib"))
            self.assertEqual((summary_b.source, summary_b.format), ("sciencedirect", "ris"))
            self.assertEqual(first[0].record_id, "PUBMED:39776243")
            self.assertEqual(second[0].text("DI"), "10.1000/pubmed")
            self.assertEqual(len(deduplicate(first + second)[0]), 1)

    def test_wos_table_bibtex_and_ris_have_same_database_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "savedrecs.txt"
            table.write_text("PT\tAU\tTI\tPY\tDI\tUT\nJ\tLi, A\tExample\t2024\t10.1000/one\tWOS:1\n", encoding="utf-8")
            bib = root / "savedrecs.bib"
            bib.write_text("@article{ WOS:1,\nTitle = {Example},\nAuthor = {Li, A},\nYear = {2024},\n"
                           "DOI = {10.1000/one},\nUnique-ID = {WOS:1}\n}\n", encoding="utf-8")
            ris = root / "savedrecs.ris"
            ris.write_text("TY  - JOUR\nTI  - Example\nAU  - Li, A\nPY  - 2024\n"
                           "DO  - 10.1000/one\nAN  - WOS:1\nER  -\n", encoding="utf-8")
            imported = parse_many_sources([str(table), str(bib), str(ris)])
            self.assertEqual([item.source for item in imported.imports], ["wos"] * 3)
            self.assertEqual(len(imported.records), 3)
            unique, duplicates = deduplicate(imported.records)
            self.assertEqual((len(unique), len(duplicates)), (1, 2))

    def test_scopus_plain_text_and_nested_bibtex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain = root / "scopus.txt"
            plain.write_text(
                "Scopus\nEXPORT DATE: 26 September 2026\n\nSmith A., Li B.\n"
                "AUTHOR FULL NAMES: Smith, Alice; Li, Bob\n123; 456\n"
                "A {nested} title\n(2026) Journal of Tests, 3 (1), Cited 0 times.\n"
                "DOI: 10.1000/two\nhttps://www.scopus.com/pages/publications/123?origin=resultslist\n\n"
                "ABSTRACT: Example abstract\nAUTHOR KEYWORDS: water-waste; water waste\n"
                "INDEX KEYWORDS: papers; paper\nSOURCE: Scopus\nEID: 2-s2.0-123\n",
                encoding="utf-8")
            bib = root / "scopus.bib"
            bib.write_text(
                "Scopus\nEXPORT DATE: 26 September 2026\n"
                "@ARTICLE{Smith2026,\n title = {A {nested} title},\n"
                "doi = {10.1000/two},\n year = {2026},\n"
                "url = {https://www.scopus.com/pages/publications/123},\n"
                "author_keywords = {water waste},\n source = {Scopus}\n}\n",
                encoding="utf-8")
            first, a, _ = import_one(plain)
            second, b, _ = import_one(bib)
            self.assertEqual((a.format, b.format), ("scopus_plain", "bibtex"))
            self.assertEqual(first[0].text("SO"), "Journal of Tests")
            self.assertEqual(second[0].title, "A {nested} title")
            self.assertEqual(len(deduplicate(first + second)[0]), 1)

    def test_pmid_list_identified_but_not_sent_to_screening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pmid.txt"
            path.write_text("39776243\n31591341\n", encoding="utf-8")
            records, summary, warnings = import_one(path)
            self.assertEqual((summary.source, summary.format, summary.count), ("pubmed", "pmid_list", 0))
            self.assertEqual(records, [])
            self.assertIn("完整题录", warnings[0])
            with self.assertRaises(ImportFormatError):
                parse_many_sources([str(path)])

    def test_chinese_refworks_txt_auto_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cnki.txt"
            path.write_bytes((
                "RT Journal Article\nA1 张三;李四\nT1 中文文献去重\n"
                "JF 中国医学期刊\nYR 2023\nK1 文献;去重\nDS CNKI\n"
            ).encode("gb18030"))
            records, summary, _ = import_one(path)
            self.assertEqual((summary.source, summary.format, summary.count), ("cnki", "refworks", 1))
            self.assertEqual(records[0].values("AU"), ["张三", "李四"])
            self.assertEqual(records[0].values("DE"), ["文献", "去重"])

    def test_keyword_and_address_merging_is_previewable(self) -> None:
        record = WosRecord(
            fields={"TI": ["Example"], "DE": ["waterwaste", "water waste", "water-waste", "papers", "paper"],
                    "C1": ["Peking University, Beijing, PR China"]},
            raw_block="PT J\nTI Example\nER", source_file="one.txt", source_index=1)
        changed, preview = merge_vocabulary([record], {
            "organizations": [{"target": "北京大学", "variants": ["Peking University"]}],
            "countries": [{"target": "中国", "variants": ["PR China"]}],
        })
        self.assertEqual(changed[0].values("DE"), ["Water waste", "Paper"])
        self.assertEqual(changed[0].values("C1"), ["北京大学, Beijing, 中国"])
        self.assertEqual(record.values("DE")[0], "waterwaste")
        self.assertEqual(preview["affected_record_count"], 1)
        self.assertIn("DE Water waste", changed[0].raw_block)

    def test_same_title_year_with_conflicting_doi_is_not_deleted(self) -> None:
        first = WosRecord({"TI": ["Example"], "PY": ["2024"], "AU": ["Li, A"],
                           "DI": ["10.1000/one"]}, "PT J\nER", "a.ris", 1)
        second = WosRecord({"TI": ["Example"], "PY": ["2024"], "AU": ["Li, A"],
                            "DI": ["10.1000/two"]}, "PT J\nER", "b.ris", 1)
        self.assertEqual(len(deduplicate([first, second])[0]), 2)

    def test_drop_accepts_mixed_extensions_without_source_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a.txt", "b.ris", "c.bib", "d.csv", "e.xls", "f.nbib", "ignore.pdf"):
                (root / name).write_text("x", encoding="utf-8")
            accepted, warnings = expand_bibliography_inputs([root])
            self.assertEqual(len(accepted), 6)
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
