from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from wos_filter.chart_export import export_ranking_png, export_trend_png, validate_export_settings


class ChartExportTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is not installed")
    def test_both_charts_export_at_selected_size_and_dpi(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            trend = Path(directory) / "trend.png"
            ranking = Path(directory) / "ranking.png"
            export_trend_png(trend, [{"year": 2023, "papers": 2, "citations": 3},
                                     {"year": 2024, "papers": 4, "citations": 6}],
                             title="发文趋势", subtitle="基础去重结果", color="#009E73",
                             width_in=5, height_in=4, dpi=72)
            export_ranking_png(ranking, [{"name": "北京大学", "papers": 3, "citations": 4}],
                               title="机构发文量排名", subtitle="基础去重结果", color="#E69F00",
                               width_in=5, height_in=4, dpi=72)
            for path, expected in ((trend, (0, 158, 115)), (ranking, (230, 159, 0))):
                with Image.open(path) as image:
                    self.assertEqual(image.size, (360, 288))
                    self.assertEqual(image.format, "PNG")
                    self.assertAlmostEqual(image.info["dpi"][0], 72, delta=1)
                    self.assertIn(expected, image.get_flattened_data())
            self.assertNotEqual(trend.read_bytes(), ranking.read_bytes())

    def test_oversized_export_is_rejected_before_allocating_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "过大"):
            validate_export_settings(30, 20, 600)


if __name__ == "__main__":
    unittest.main()
