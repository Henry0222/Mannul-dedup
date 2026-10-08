from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wos_filter.models import WosRecord
from wos_filter.bibliometrics import terms_for_record
from wos_filter.vocabulary import merge_vocabulary
from wos_filter.vos_network import build_vos_network
from wos_filter.vocabulary_review import (LABELS, export_pairs_txt, import_pairs_txt,
                                          list_merge_pairs, refresh_keyword_canonicals,
                                          remove_pair, upsert_pair)
from wos_filter.shared_vocabulary import SharedVocabularyStore


class VocabularyReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [WosRecord(
            {"TI": ["Example"], "DE": ["water waste", "water-waste"],
             "AU": ["Smith J", "J. Smith"], "SO": ["Journal of Tests"],
             "C3": ["Peking University"], "CO": ["PR China"],
             "C1": ["Peking University, Beijing, PR China"]},
            "", "sample.txt", 1)]

    def test_country_rule_reaches_every_c1_address_group_and_vos(self) -> None:
        address = ("[Lin, A] National Taiwan University, Taipei, Taiwan. "
                   "[Chen, B] State University, Boston, USA. "
                   "[Wu, C] Taiwan Medical Center, Taichung, Taiwan.")
        record = WosRecord({"TI": ["Several addresses"], "PY": ["2024"],
                            "C1": [address], "C3": ["National Taiwan University"]},
                           "", "sample.txt", 1, source_kind="wos")
        spec = upsert_pair({}, "countries", "China", "Taiwan")
        normalized, report = merge_vocabulary([record], spec)
        country_pairs = list_merge_pairs([record], spec, report, "countries")
        self.assertEqual(next(row["occurrences"] for row in country_pairs
                              if row["variant"] == "Taiwan"), 2)
        self.assertEqual(normalized[0].text("C1").count(", China."), 2)
        self.assertNotIn(", Taiwan.", normalized[0].text("C1"))
        self.assertEqual(normalized[0].values("C3"), ["National Taiwan University"])
        self.assertEqual(set(terms_for_record(normalized[0], "countries")), {"China", "USA"})
        network = build_vos_network(normalized, "countries", min_occurrences=1)
        self.assertEqual({item["label"] for item in network["network"]["items"]},
                         {"China", "USA"})

    def test_all_five_categories_can_be_edited_and_recomputed(self) -> None:
        spec = {}
        for kind, target, variant in (
            ("countries", "China", "PR China"),
            ("organizations", "Peking University", "Peking Univ"),
            ("authors", "Smith J", "J. Smith"),
            ("journals", "Journal of Tests", "J Tests"),
            ("keywords", "water waste", "water-waste"),
        ):
            spec = upsert_pair(spec, kind, target, variant)
        self.assertEqual(len(LABELS), 5)
        changed, report = merge_vocabulary(self.records, spec)
        self.assertEqual(changed[0].values("AU"), ["Smith J"])
        self.assertEqual(changed[0].values("DE"), ["water waste"])
        self.assertEqual(changed[0].values("CO"), ["China"])
        self.assertEqual(len(list_merge_pairs(self.records, spec, report, "authors")), 1)
        self.assertIn("AU", report["changes"][0]["fields"])
        self.assertIn("CO", report["changes"][0]["fields"])
        self.assertIn("C1", report["changes"][0]["kind_fields"]["countries"])

    def test_pair_export_is_exactly_two_tab_separated_columns(self) -> None:
        spec = upsert_pair({}, "authors", "Smith J", "J. Smith")
        _, report = merge_vocabulary(self.records, spec)
        pairs = list_merge_pairs(self.records, spec, report, "authors")
        with tempfile.TemporaryDirectory() as directory:
            destination = export_pairs_txt(Path(directory) / "authors.txt", pairs)
            self.assertEqual(destination.read_text(encoding="utf-8"), "Smith J\tJ. Smith\n")
            imported, count = import_pairs_txt({}, "authors", destination, self.records)
            self.assertEqual(count, 1)
            changed, _ = merge_vocabulary(self.records, imported)
            self.assertEqual(changed[0].values("AU"), ["Smith J"])

    def test_txt_import_is_atomic_and_accepts_chinese_gb18030(self) -> None:
        original = {"countries": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.txt"
            path.write_bytes("China\tPR China\n北京大学\t北大\n".encode("gb18030"))
            imported, count = import_pairs_txt(original, "countries", path, self.records)
            self.assertEqual(count, 2)
            self.assertEqual(original, {"countries": []})
            self.assertEqual(len(imported["countries"]), 2)
            path.write_text("China\tPR China\nwrong line\n", encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "第 2 行"):
                import_pairs_txt(original, "countries", path, self.records)
            self.assertEqual(original, {"countries": []})

    def test_auto_merge_can_be_removed_and_manual_target_replaces_it(self) -> None:
        _, report = merge_vocabulary(self.records)
        pairs = list_merge_pairs(self.records, {}, report, "keywords")
        self.assertTrue(pairs)
        row = pairs[0]
        excluded = remove_pair({}, "keywords", row["target"], row["variant"], automatic=True)
        _, suppressed = merge_vocabulary(self.records, excluded)
        self.assertFalse(list_merge_pairs(self.records, excluded, suppressed, "keywords"))
        manual = upsert_pair(excluded, "keywords", "Water Waste", row["variant"])
        changed, _ = merge_vocabulary(self.records, manual)
        self.assertIn("Water Waste", changed[0].values("DE"))

    def test_refresh_restyles_saved_ai_and_inferred_keywords_but_preserves_manual_target(self) -> None:
        records = [WosRecord({"TI": ["Example"], "DE": [
            "-polylysine; POLYLYSINE; ABDOMINAL FAT; abdominal fat; water-waste; water waste; DNA Repair"]},
            "", "sample.txt", 1)]
        spec = upsert_pair({}, "keywords", "WATER-WASTE", "water waste")
        spec["aiPairSources"] = [{"kind": "keywords", "target": "WATER-WASTE",
                                  "variant": "water waste", "reason": "AI"}]
        spec = upsert_pair(spec, "keywords", "DNA Repair", "dna-repair")
        refreshed, changed = refresh_keyword_canonicals(spec)
        self.assertEqual(changed, 1)
        updated, report = merge_vocabulary(records, refreshed)
        self.assertEqual(updated[0].values("DE"), ["Polylysine", "Abdominal fat",
                                                      "Water waste", "DNA Repair"])
        pairs = list_merge_pairs(records, refreshed, report, "keywords")
        self.assertTrue(any(row["target"] == "Water waste" and row["origin"] == "DeepSeek"
                            for row in pairs))
        self.assertEqual(refresh_keyword_canonicals(refreshed)[1], 0)

    def test_automatic_keyword_targets_default_to_sentence_case_without_hyphens(self) -> None:
        records = [WosRecord({"TI": ["Example"], "DE": [
            "WATER-WASTE; water-waste; waterwaste; water waste; "
            "ADENO-ASSOCIATED VIRUS; adeno-associated viruses"]},
            "", "sample.txt", 1)]
        normalized, report = merge_vocabulary(records)
        self.assertIn("Water waste", normalized[0].values("DE"))
        self.assertIn("Adeno associated virus", normalized[0].values("DE"))
        targets = {row["target"] for row in list_merge_pairs(records, {}, report, "keywords")}
        self.assertTrue(all(target == target.capitalize() for target in targets))
        self.assertTrue(all("-" not in target for target in targets))

    def test_saved_ai_keyword_scope_change_is_flagged_for_review(self) -> None:
        spec = upsert_pair({}, "keywords", "16S rRNA", "16S rRNA sequencing")
        spec["aiPairSources"] = [{"kind": "keywords", "target": "16S rRNA",
                                  "variant": "16S rRNA sequencing", "reason": "同一概念"}]
        rows = list_merge_pairs(self.records, spec, {}, "keywords")
        self.assertIn("待核查", rows[0]["reason"])

    def test_refresh_old_ai_target_without_unhyphenated_source(self) -> None:
        spec = upsert_pair({}, "keywords", "ANTI-MICROBIAL", "anti-microbials")
        spec["aiPairSources"] = [{"kind": "keywords", "target": "ANTI-MICROBIAL",
                                  "variant": "anti-microbials", "reason": "单复数"}]
        refreshed, changed = refresh_keyword_canonicals(spec)
        self.assertEqual(changed, 1)
        self.assertEqual(list_merge_pairs(self.records, refreshed, {}, "keywords")[0]["target"],
                         "Anti microbial")

    def test_nonkeyword_auto_merges_and_shared_catalog_excludes_keywords(self) -> None:
        second = WosRecord({"TI": ["Other"], "C3": ["Peking Univ"],
                            "AU": ["Smith, J."], "SO": ["Journal-of Tests."]},
                           "", "sample.txt", 2)
        records = [*self.records, second]
        normalized, report = merge_vocabulary(records)
        for kind in ("countries", "organizations", "authors", "journals"):
            self.assertTrue(list_merge_pairs(records, {}, report, kind), kind)
        self.assertEqual(normalized[0].values("CO"), ["China"])
        self.assertEqual(normalized[0].values("C3"), normalized[1].values("C3"))
        self.assertEqual(normalized[0].values("SO"), normalized[1].values("SO"))
        with tempfile.TemporaryDirectory() as directory:
            store = SharedVocabularyStore(Path(directory) / "shared.json")
            pairs = {kind: list_merge_pairs(records, {}, report, kind)
                     for kind in LABELS if kind != "keywords"}
            store.remember(records, pairs)
            content = store.path.read_text(encoding="utf-8")
            self.assertNotIn("keywords", content)
            self.assertNotIn("water waste", content)
            inherited = store.overlay({"keywords": []})
            _, inherited_report = merge_vocabulary(records, inherited)
            self.assertTrue(list_merge_pairs(records, inherited, inherited_report, "countries"))
            removed = remove_pair(inherited, "countries", "China", "PR China", automatic=True)
            _, removed_report = merge_vocabulary(records, removed)
            store.remember(records, {kind: list_merge_pairs(records, removed, removed_report, kind)
                                     for kind in pairs}, removed_variants={"countries": {"PR China"}})
            self.assertFalse(store.load()["countries"])

    def test_unrelated_project_cannot_erase_shared_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SharedVocabularyStore(Path(directory) / "shared.json")
            store.remember([], {"countries": [{"target": "China", "variant": "PR China"}]})
            other = WosRecord({"TI": ["Other"], "CO": ["PR China"]}, "", "other.txt", 1)
            store.remember([other], {"countries": []})
            self.assertEqual(store.load()["countries"],
                             [{"target": "China", "variant": "PR China"}])


if __name__ == "__main__":
    unittest.main()
