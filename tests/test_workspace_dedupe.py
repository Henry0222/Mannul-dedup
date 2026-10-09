from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from wos_filter.basic_export import default_export_name, default_output_dir, preferred_export_dir, export_basic_records
from wos_filter.bibliometrics import annual_trend, rank_dimension
from wos_filter.dedupe_review import PRESET_FIELDS, apply_review, auto_review_run, find_candidates, new_run
from wos_filter.models import WosRecord
from wos_filter.project_workspace import WorkspaceStore, active_records, record_from_json, record_key, record_to_json
from wos_filter.year_filter import partition_records_by_year


def record(file: str, index: int, *, title="A Study", year="2024", author="Li, A",
           journal="Medical Journal", abstract="This is a test", doi="", source="wos") -> WosRecord:
    fields = {"TI": [title], "PY": [year], "AU": [author], "SO": [journal], "AB": [abstract]}
    if doi:
        fields["DI"] = [doi]
    return WosRecord(fields, f"PT J\nTI {title}\nPY {year}\nER", file, index, source_kind=source)


class WorkspaceDedupeTests(unittest.TestCase):
    def test_bibliometrics_count_each_entity_once_per_retained_record(self) -> None:
        first = record("wos.txt", 1, year="2024")
        first.fields.update({"AU": ["Li, A", "li, a", "Wang, B"], "TC": ["12"],
                             "C1": ["[Li] Peking Univ, Beijing, Peoples R China",
                                    "[Wang] Peking Univ, Beijing, Peoples R China"]})
        second = record("ris.ris", 1, year="2023", source="pubmed")
        second.fields.update({"C1": ["Harvard Univ, Boston, USA"], "TC": ["3"]})
        unknown = record("third.txt", 1, year="", source="wos")
        unknown.fields["C3"] = ["Peking Univ"]
        unknown.fields["C1"] = ["北京大学"]
        records = [first, second, unknown]
        self.assertEqual(annual_trend(records), [
            {"year": 2023, "papers": 1, "citations": 3},
            {"year": 2024, "papers": 1, "citations": 12},
        ])
        self.assertEqual(rank_dimension(records, "countries")[0]["papers"], 2)
        self.assertEqual(rank_dimension(records, "institutions")[0]["papers"], 2)
        self.assertEqual(rank_dimension(records, "authors")[0]["papers"], 3)

    def test_presets_match_recovered_bibnexus_field_sets(self) -> None:
        self.assertEqual(PRESET_FIELDS, {
            "quick": ("identifiers",),
            "journal": ("title", "year", "authors", "source"),
            "strict": ("title", "year", "authors", "abstract"),
            "loose": ("title", "year"),
        })

    def test_four_modes_and_conflicting_doi_remain_reviewable(self) -> None:
        first = record("a.ris", 1, doi="10.1000/one")
        second = record("b.ris", 1, abstract="Other abstract", doi="10.1000/two")
        self.assertEqual(len(find_candidates([first, second], "quick")), 0)
        self.assertEqual(len(find_candidates([first, second], "strict")), 0)
        self.assertEqual(len(find_candidates([first, second], "journal")), 1)
        loose = find_candidates([first, second], "loose")
        self.assertEqual(len(loose), 1)
        self.assertTrue(loose[0]["doi_conflict"])

    def test_review_required_before_unified_dedupe(self) -> None:
        records = [record("a.ris", 1, doi="10.1000/same"), record("b.ris", 1, doi="10.1000/same")]
        project = {"excluded_keys": [], "runs": []}
        run = new_run(records, "quick")
        project["runs"].append(run)
        self.assertEqual(run["candidate_duplicates"], 1)
        with self.assertRaises(ValueError):
            apply_review(project, run)
        run["groups"][0]["status"] = "ignored"
        self.assertEqual(apply_review(project, run), 0)
        self.assertEqual(len(active_records(project, records)), 2)
        another = new_run(records, "quick")
        another["groups"][0]["status"] = "deduped"
        project["runs"].append(another)
        self.assertEqual(apply_review(project, another), 1)
        self.assertEqual(len(active_records(project, records)), 1)
        with self.assertRaises(ValueError):
            apply_review(project, another)

    def test_wos_priority_and_one_click_keeps_doi_conflicts(self) -> None:
        records = [
            record("scopus.ris", 1, doi="10.1000/same", abstract="Much longer abstract " * 10, source="scopus"),
            record("wos.txt", 1, doi="10.1000/same", source="wos"),
            record("a.ris", 2, title="Other", doi="10.1000/one", source="scopus"),
            record("b.ris", 2, title="Other", doi="10.1000/two", source="sciencedirect"),
        ]
        run = new_run(records, "loose")
        main = next(group for group in run["groups"] if not group["doi_conflict"])
        self.assertEqual(main["primary"], record_key(records[1]))
        accepted, skipped = auto_review_run(run, records)
        self.assertEqual((accepted, skipped), (1, 1))
        project = {"excluded_keys": [], "runs": [run]}
        self.assertEqual(apply_review(project, run), 1)
        self.assertEqual({record_key(item) for item in active_records(project, records)},
                         {record_key(records[1]), record_key(records[2]), record_key(records[3])})

    def test_year_scope_and_basic_export_name_content(self) -> None:
        records = [record("one.txt", 1, year="2024"), record("two.txt", 1, year="2020")]
        included, excluded, _ = partition_records_by_year(records, 2023, 2025)
        self.assertEqual((len(included), len(excluded)), (1, 1))
        self.assertEqual(default_export_name(datetime(2026, 9, 27, 1, 2)), "download_2609270102.txt")
        stamp = datetime(2026, 9, 27, 1, 2)
        self.assertEqual(default_output_dir("C:/app", stamp), Path("C:/app/output/2609270102"))
        self.assertEqual(preferred_export_dir("C:/app", "C:/app/output/old-project", stamp),
                         Path("C:/app/output/2609270102"))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / default_export_name(datetime(2026, 9, 27, 1, 2))
            export_basic_records(destination, included, "FN Clarivate Web of Science\nVR 1.0")
            text = destination.read_text(encoding="utf-8-sig")
            self.assertIn("TI A Study", text)
            self.assertEqual(text.count("PT J"), 1)
            self.assertTrue(text.rstrip().endswith("EF"))

    def test_projects_persist_separately_with_stable_record_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceStore(Path(directory))
            first = store.list_projects()[0]
            second = store.create("第二项目")
            item = record("a.ris", 1, doi="10.1000/same")
            first["records"] = [record_to_json(item)]
            store.save(first)
            store.activate(second["id"])
            reopened = WorkspaceStore(Path(directory))
            self.assertEqual(reopened.last_project_id(), second["id"])
            self.assertEqual(reopened.load(second["id"])["records"], [])
            restored = record_from_json(reopened.load(first["id"])["records"][0])
            self.assertEqual(record_key(restored), record_key(item))
            self.assertNotEqual(record_key(item), record_key(record("b.ris", 1, doi="10.1000/same")))

    def test_project_sidebar_reads_names_without_loading_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceStore(Path(directory))
            project = store.create('项目"二')
            store.load = lambda _project_id: (_ for _ in ()).throw(AssertionError("loaded full project"))
            summaries = store.list_project_summaries()
            self.assertEqual([item["name"] for item in summaries], ["默认项目", '项目"二'])
            self.assertEqual(summaries[1]["id"], project["id"])

    def test_delete_project_archives_data_and_keeps_a_live_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceStore(Path(directory))
            first = store.load(store.last_project_id())
            first["records"] = [record_to_json(record("first.ris", 1))]
            store.save(first)
            second = store.create("待删除")
            second["records"] = [record_to_json(record("second.ris", 2))]
            store.save(second)
            active_id, archive = store.delete(second["id"])
            self.assertEqual(active_id, first["id"])
            self.assertEqual([item["id"] for item in store.list_project_summaries()], [first["id"]])
            self.assertEqual(json.loads((archive / "project.json").read_text(encoding="utf-8"))["name"], "待删除")
            self.assertEqual(len(store.load(first["id"])["records"]), 1)
            next_id, archived_first = store.delete(first["id"])
            self.assertNotEqual(next_id, first["id"])
            self.assertEqual(store.load(next_id)["name"], "默认项目")
            self.assertEqual(len(store.list_project_summaries()), 1)
            self.assertTrue((archived_first / "project.json").is_file())

    def test_view_settings_save_without_rewriting_bibliography(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceStore(Path(directory))
            project = store.load(store.last_project_id())
            project["records"] = [record_to_json(record("large.ris", 1))]
            store.save(project)
            path = store._project_path(project["id"])
            before = path.read_bytes()
            project["vos_settings"] = {"kind": "国家合作", "min": "3", "max": "90"}
            project["chart_settings"] = {"view": "countries"}
            store.save_view(project)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.load(project["id"])["vos_settings"]["min"], "3")
            self.assertEqual(len(store.load(project["id"])["records"]), 1)
            project["vos_settings"]["min"] = "4"
            store.save(project)
            self.assertFalse(path.with_name("view.json").exists())
            self.assertEqual(store.load(project["id"])["vos_settings"]["min"], "4")

    def test_legacy_skip_dedupe_setting_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceStore(Path(directory))
            project = store.load(store.last_project_id())
            project["skip_dedupe"] = True
            store.save(project)
            self.assertNotIn("skip_dedupe", store.load(project["id"]))
            store.save_view(project)
            self.assertNotIn("skip_dedupe", store.load(project["id"]))


if __name__ == "__main__":
    unittest.main()
