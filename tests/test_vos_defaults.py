from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wos_filter.vos_defaults import (apply_graph_defaults, graph_defaults_path,
                                     preferences, save_preferences)
from wos_filter.vos_viewer import _DataBridge


class VOSDefaultsTests(unittest.TestCase):
    def test_desktop_path_is_shared_across_projects(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            program = app_dir / "VOSviewer.exe"
            program.touch()
            save_preferences(app_dir, desktop_exe=str(program), build={"min": "3"})
            self.assertEqual(preferences(app_dir)["desktop_exe"], str(program))
            self.assertEqual(preferences(app_dir)["build"]["min"], "3")

    def test_viewer_autosave_updates_global_defaults_for_new_map(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = Path(folder)
            path = app_dir / "graph.json"
            base = {"network": {"items": [{"id": 1, "weights": {}}], "links": []},
                    "config": {"parameters": {"attraction": 2, "repulsion": -1}}}
            path.write_text(json.dumps(base), encoding="utf-8")
            bridge = _DataBridge(path, graph_defaults_path(app_dir))
            bridge.load_data()
            edited = json.loads(json.dumps(base))
            edited["config"]["parameters"].update(
                attraction=4, repulsion=-3, resolution=1.8, item_color=2,
                min_score=2018, max_score=2024, json="per-map")
            bridge.save_state(json.dumps(edited))
            fresh = json.loads(json.dumps(base))
            apply_graph_defaults(fresh, graph_defaults_path(app_dir))
            self.assertEqual(fresh["config"]["parameters"]["attraction"], 4)
            self.assertEqual(fresh["config"]["parameters"]["resolution"], 1.8)
            self.assertEqual(fresh["config"]["parameters"]["item_color"], 2)
            self.assertNotIn("min_score", fresh["config"]["parameters"])
            self.assertNotIn("json", fresh["config"]["parameters"])


if __name__ == "__main__":
    unittest.main()
