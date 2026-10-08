from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from wos_filter.models import WosRecord
from wos_filter.vos_network import (build_vos_network, filter_vos_network,
                                    inherit_vos_parameters, node_citation_coverage,
                                    save_vos_network)
from wos_filter.vos_export import export_vos_map_network
from wos_filter.vos_desktop import launch_vosviewer_desktop
from wos_filter.vos_viewer import _DataBridge
from wos_filter.wos import _parse_record_block
from wos_filter.institution_fields import institution_terms
from wos_filter.vocabulary import merge_vocabulary
from wos_filter.ai_vocabulary import collect_inventory


def record(index: int, keywords: str, authors: list[str], country: str) -> WosRecord:
    return WosRecord({"TI": [f"Paper {index}"], "PY": ["2024"], "DE": [keywords],
                      "AU": authors, "C1": [f"University, {country}"]},
                     "", "sample.txt", index, source_kind="wos")


class VOSNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            record(1, "water waste; climate", ["Li, A", "Wang, B"], "China"),
            record(2, "climate; water waste", ["Li, A", "Smith, C"], "USA"),
            record(3, "climate; energy", ["Wang, B", "Smith, C"], "USA"),
        ]

    def test_keyword_network_counts_unique_papers_and_pairs(self) -> None:
        data = build_vos_network(self.records, min_occurrences=1)
        labels = {item["label"]: item for item in data["network"]["items"]}
        self.assertEqual(labels["climate"]["weights"]["Documents"], 3)
        self.assertEqual(labels["water waste"]["weights"]["Documents"], 2)
        ids = {item["id"]: item["label"] for item in data["network"]["items"]}
        links = {frozenset((ids[edge["source_id"]], ids[edge["target_id"]])): edge["strength"]
                 for edge in data["network"]["links"]}
        self.assertEqual(links[frozenset(("climate", "water waste"))], 2)
        self.assertEqual(links[frozenset(("climate", "energy"))], 1)

    def test_author_network_and_empty_network_message(self) -> None:
        data = build_vos_network(self.records, "authors", min_occurrences=1)
        self.assertEqual(len(data["network"]["items"]), 3)
        self.assertEqual(len(data["network"]["links"]), 3)
        with self.assertRaisesRegex(ValueError, "节点少于 2"):
            build_vos_network(self.records, "countries", min_occurrences=3)

    def test_country_network_uses_all_wos_address_groups(self) -> None:
        record = WosRecord({"TI": ["International paper"], "PY": ["2024"],
                            "C1": ["[Li, A] Peking Univ, Beijing, Peoples R China. "
                                   "[Smith, B] State Univ, CA 90001, USA."]},
                           "", "wos.txt", 1, source_kind="wos")
        data = build_vos_network([record], "countries", min_occurrences=1)
        self.assertEqual({item["label"] for item in data["network"]["items"]}, {"China", "USA"})
        self.assertEqual(data["network"]["links"][0]["strength"], 1)

    def test_journal_coupling_and_cited_reference_networks(self) -> None:
        shared = "Smith J, 2020, JOURNAL X, V1, P1, DOI 10.1000/shared"
        second = "Lee L, 2019, JOURNAL Y, V2, P2, DOI 10.1000/second"
        third = "Chen C, 2018, JOURNAL Z, V3, P3, DOI 10.1000/third"
        records = [
            WosRecord({"TI": ["A"], "SO": ["SOURCE A"], "PY": ["2021"],
                       "CR": [shared, second]}, "", "a.txt", 1),
            WosRecord({"TI": ["B"], "SO": ["SOURCE B"], "PY": ["2022"],
                       "CR": [shared.replace("Smith J", "SMITH J"), third]}, "", "b.txt", 1),
            WosRecord({"TI": ["C"], "SO": ["SOURCE C"], "PY": ["2023"],
                       "CR": [shared, second]}, "", "c.txt", 1),
        ]
        journals = build_vos_network(records, "journals", min_occurrences=1)
        labels = {item["id"]: item["label"] for item in journals["network"]["items"]}
        strengths = {frozenset((labels[edge["source_id"]], labels[edge["target_id"]])): edge["strength"]
                     for edge in journals["network"]["links"]}
        self.assertEqual(strengths[frozenset(("SOURCE A", "SOURCE C"))], 2)
        for kind, expected in (("cited_authors", "Smith J"),
                               ("cited_journals", "JOURNAL X"),
                               ("cited_articles", shared)):
            data = build_vos_network(records, kind, min_occurrences=1)
            items = data["network"]["items"]
            leader = next(item for item in items if item["label"].casefold() == expected.casefold())
            self.assertEqual(leader["weights"]["Documents"], 3)
            self.assertEqual(leader["weights"]["Citations"], 3)
            self.assertEqual(node_citation_coverage(records, data, kind)[leader["id"]], 3)
            self.assertTrue(data["network"]["links"])

    def test_missing_reference_fields_report_what_is_unavailable(self) -> None:
        records = [WosRecord({"TI": [str(i)], "SO": [f"JOURNAL {i}"]}, "", "x.txt", i)
                   for i in (1, 2)]
        for kind in ("cited_authors", "cited_journals", "cited_articles"):
            with self.assertRaisesRegex(ValueError, "没有 CR 参考文献字段"):
                build_vos_network(records, kind, min_occurrences=1)
        with self.assertRaisesRegex(ValueError, "没有 CR 参考文献字段"):
            build_vos_network(records, "journals", min_occurrences=1)

    def test_wrapped_wos_c3_keeps_complete_institutions_in_old_and_new_projects(self) -> None:
        lines = ["PT J", "TI Collaboration", "C3 Nanjing Agricultural University; University of",
                 "   North Carolina; Warsaw University of Life", "   Sciences", "ER"]
        parsed = _parse_record_block(lines, "wos.txt", 1)
        self.assertEqual(parsed.values("C3"), [
            "Nanjing Agricultural University; University of North Carolina; "
            "Warsaw University of Life Sciences"])
        legacy = WosRecord({**parsed.fields, "C3": [
            "Nanjing Agricultural University; University of",
            "North Carolina; Warsaw University of Life", "Sciences"]},
            "\n".join(lines), "wos.txt", 1)
        expected = ["Nanjing Agricultural University", "University of North Carolina",
                    "Warsaw University of Life Sciences"]
        self.assertEqual(institution_terms(legacy), expected)
        self.assertEqual({row["term"] for row in collect_inventory([legacy], "organizations")},
                         set(expected))
        normalized, _ = merge_vocabulary([legacy])
        self.assertEqual(normalized[0].values("C3"), expected)
        network = build_vos_network(normalized, "institutions", min_occurrences=1)
        self.assertEqual({item["label"] for item in network["network"]["items"]}, set(expected))

    def test_output_timestamp_and_no_overwrite(self) -> None:
        data = build_vos_network(self.records, min_occurrences=1)
        with tempfile.TemporaryDirectory() as folder:
            one = save_vos_network(data, folder, "keywords", datetime(2026, 9, 27, 22, 30))
            two = save_vos_network(data, folder, "keywords", datetime(2026, 9, 27, 22, 30))
            self.assertEqual(one.parent.name, "2609272230")
            self.assertNotEqual(one, two)
            self.assertEqual(len(json.loads(one.read_text(encoding="utf-8"))["network"]["items"]), 3)

    def test_new_map_inherits_saved_vos_layout_parameters(self) -> None:
        data = build_vos_network(self.records, min_occurrences=1)
        with tempfile.TemporaryDirectory() as folder:
            prior = Path(folder) / "prior.json"
            prior.write_text(json.dumps({"config": {"parameters": {
                "attraction": 4, "repulsion": -2, "resolution": 1.7,
                "min_cluster_size": 3, "merge_small_clusters": True,
                "json": "ignored"}}}), encoding="utf-8")
            updated = inherit_vos_parameters(data, prior)
            self.assertEqual(updated["config"]["parameters"]["resolution"], 1.7)
            self.assertEqual(updated["config"]["parameters"]["repulsion"], -2)
            self.assertNotIn("json", updated["config"]["parameters"])

    def test_node_metrics_selection_and_desktop_map_export(self) -> None:
        self.records[0].fields["TC"] = ["12"]
        self.records[1].fields["TC"] = ["3"]
        data = build_vos_network(self.records, min_occurrences=1)
        labels = {item["label"]: item for item in data["network"]["items"]}
        self.assertEqual(labels["climate"]["weights"]["Citations"], 15)
        self.assertEqual(labels["climate"]["weights"]["Total link strength"], 3)
        retained = filter_vos_network(data, {labels["climate"]["id"], labels["water waste"]["id"]})
        self.assertEqual(len(retained["network"]["items"]), 2)
        self.assertEqual(retained["network"]["items"][0]["weights"]["Total link strength"], 2)
        with tempfile.TemporaryDirectory() as folder:
            map_path, network_path = export_vos_map_network(retained, Path(folder) / "graph.json")
            map_text = map_path.read_text(encoding="utf-8")
            self.assertIn("weight<Citations>", map_text)
            self.assertIn("score<Avg. pub. year>", map_text)
            self.assertEqual(map_text.splitlines()[0].split("\t")[:2], ["id", "label"])
            self.assertNotIn("\tx\ty\t", map_text.splitlines()[0])
            self.assertEqual(len(network_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_desktop_launcher_passes_native_map_and_network(self) -> None:
        data = build_vos_network(self.records, min_occurrences=1)
        data["network"]["items"][0]["label"] = "University, Department; Branch"
        with tempfile.TemporaryDirectory() as folder:
            program = Path(folder) / "VOSviewer.exe"
            program.touch()
            source = Path(folder) / "graph.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            with patch("wos_filter.vos_desktop.subprocess.Popen") as launch:
                map_path, network_path = launch_vosviewer_desktop(program, source)
            self.assertTrue(map_path.is_file())
            self.assertTrue(network_path.is_file())
            self.assertIn('"University, Department; Branch"', map_path.read_text(encoding="utf-8"))
            launch.assert_called_once_with(
                [str(program), "-map", str(map_path), "-network", str(network_path)],
                cwd=str(source.parent))

    def test_bridge_autosave_retains_tls_and_adjusted_position(self) -> None:
        data = build_vos_network(self.records, min_occurrences=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "graph.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            bridge = _DataBridge(path)
            bridge.load_data()
            changed = json.loads(json.dumps(data))
            changed["network"]["items"][0]["x"] = 2.5
            changed["config"]["parameters"].update(
                attraction=4, repulsion=-2, resolution=1.7,
                min_cluster_size=3, merge_small_clusters=True)
            for item in changed["network"]["items"]:
                del item["weights"]["Total link strength"]
            self.assertTrue(bridge.save_state(json.dumps(changed))["changed"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["network"]["items"][0]["x"], 2.5)
            self.assertEqual(saved["config"]["parameters"]["repulsion"], -2)
            self.assertEqual(bridge.load_data()["config"]["parameters"]["resolution"], 1.7)
            self.assertIn("Total link strength", saved["network"]["items"][0]["weights"])
            self.assertFalse(bridge.save_state(json.dumps(changed))["changed"])

    def test_editor_metadata_survives_native_autosave_and_svg_export(self) -> None:
        data = build_vos_network(self.records, min_occurrences=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "graph.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            bridge = _DataBridge(path)
            bridge.load_data()
            edited = json.loads(json.dumps(data))
            edited["mannul_editor"] = {"version": 1, "nodes": {"1": {"color": "#123456"}}}
            bridge.save_state(json.dumps(edited))
            bridge.save_state(json.dumps(data))
            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(restored["mannul_editor"]["nodes"]["1"]["color"], "#123456")
            self.assertEqual(bridge.load_data()["mannul_editor"]["nodes"]["1"]["color"], "#123456")
            svg = ('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" '
                   'viewBox="0 0 1000 700"><g id="nodes"><circle id="node-1" '
                   'cx="10" cy="20" r="5"/></g><text>A &amp; B</text></svg>')
            first = Path(bridge.save_svg(svg)["path"])
            second = Path(bridge.save_svg(svg)["path"])
            self.assertTrue(first.is_file())
            self.assertNotEqual(first, second)
            self.assertIn(".editable.svg", first.name)
            with self.assertRaisesRegex(ValueError, "不允许"):
                bridge.save_svg('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')


if __name__ == "__main__":
    unittest.main()
