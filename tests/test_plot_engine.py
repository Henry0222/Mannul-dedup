from __future__ import annotations

import unittest
from unittest.mock import Mock

from matplotlib.figure import Figure

from wos_filter.models import WosRecord
from wos_filter.plot_engine import PlotOptions, RANK_TYPES, TREND_TYPES, prepare_plot_data, render_figure
from wos_filter.vocabulary import merge_vocabulary
from wos_filter.plot_workbench import PlotWorkbench


def sample_records() -> list[WosRecord]:
    return [
        WosRecord({"TI": ["First"], "PY": ["2023"], "DE": ["Water waste; water-waste"],
                   "TC": ["3"], "CO": ["China"], "AU": ["Li, A"],
                   "CR": ["Smith J, 2020, JOURNAL A"]}, "", "wos.txt", 1, source_kind="wos"),
        WosRecord({"TI": ["Second"], "PY": ["2024"], "DE": ["waterwaste"],
                   "TC": ["5"], "CO": ["China"], "AU": ["Chen, B"],
                   "CR": ["Smith J, 2020, JOURNAL A"]}, "", "scopus.ris", 1, source_kind="scopus"),
    ]


class PlotEngineTests(unittest.TestCase):
    def test_chart_refresh_does_not_rewrite_unchanged_high_position(self) -> None:
        class Variable:
            def __init__(self, value):
                self.value = value
                self.writes = 0
            def get(self):
                return self.value
            def set(self, value):
                self.value = value
                self.writes += 1
        workbench = object.__new__(PlotWorkbench)
        workbench.orientation_var = Variable("horizontal")
        workbench.chart_type_var = Variable("bar")
        workbench.high_position_var = Variable("top")
        workbench.high_combo = Mock()
        workbench._update_high_choices()
        self.assertEqual(workbench.high_position_var.writes, 0)
        workbench.chart_type_var.set("overall_line")
        workbench._update_high_choices()
        self.assertEqual(workbench.high_position_var.get(), "left")
        self.assertEqual(workbench.high_position_var.writes, 1)

    def test_database_group_switch_and_merged_keywords(self) -> None:
        records = sample_records()
        merged = prepare_plot_data(records, PlotOptions(view="trend"))
        grouped = prepare_plot_data(records, PlotOptions(view="trend", include_database=True))
        self.assertEqual({row["source"] for row in merged}, {"全部"})
        self.assertEqual({row["source"] for row in grouped}, {"wos", "scopus"})
        normalized, _ = merge_vocabulary(records)
        rows = prepare_plot_data(normalized, PlotOptions(view="keywords"))
        self.assertEqual([(row["name"], row["papers"]) for row in rows], [("Water waste", 2)])

    def test_bilingual_titles_are_grouped_across_files_and_styles_render(self) -> None:
        records = [
            WosRecord({"TI": ["English paper A"], "PY": ["2021"]}, "", "mixed.txt", 1, source_kind="wos"),
            WosRecord({"TI": ["中文论文甲"], "PY": ["2021"]}, "", "mixed.txt", 2, source_kind="wos"),
            WosRecord({"TI": ["English paper B"], "PY": ["2023"]}, "", "other.ris", 1, source_kind="cnki"),
            WosRecord({"TI": ["中文论文乙"], "PY": ["2024"]}, "", "other.ris", 2, source_kind="cnki"),
        ]
        options = PlotOptions(chart_type="bilingual_quadratic", year_start=2021, year_end=2024)
        rows = prepare_plot_data(records, options)
        self.assertEqual({row["source"] for row in rows}, {"中文", "英文"})
        self.assertEqual([row["papers"] for row in rows if row["source"] == "中文"], [1, 0, 0, 1])
        self.assertEqual([row["papers"] for row in rows if row["source"] == "英文"], [1, 0, 1, 0])
        figure = Figure(figsize=(7, 4))
        render_figure(figure, rows, options)
        self.assertTrue({"中文", "英文"}.issubset(
            {text.get_text() for text in figure.axes[0].get_legend().get_texts()}))
        self.assertTrue(any("不足 3 个" in text.get_text() for text in figure.axes[0].texts))
        gradient = PlotOptions(chart_type="gradient_combo", trend_group="language")
        gradient_rows = prepare_plot_data(records, gradient)
        self.assertEqual({row["source"] for row in gradient_rows}, {"全部"})
        gradient_figure = Figure(figsize=(7, 4))
        render_figure(gradient_figure, gradient_rows, gradient)
        self.assertEqual(len(gradient_figure.axes[0].patches), 4)
        self.assertEqual(len(gradient_figure.axes[0].lines), 1)

    def test_bilingual_quadratic_uses_observed_years_for_each_language(self) -> None:
        rows = []
        for year, english, chinese in ((2020, 1, 2), (2021, 4, 3), (2022, 9, 6), (2023, 0, 0)):
            rows.extend([{"year": year, "source": source, "papers": count, "citations": 0}
                         for source, count in (("英文", english), ("中文", chinese))])
        figure = Figure(figsize=(7, 4))
        render_figure(figure, rows, PlotOptions(chart_type="bilingual_quadratic"))
        equation = next(text.get_text() for text in figure.axes[0].texts if "R²=" in text.get_text())
        self.assertIn("英文：", equation)
        self.assertIn("中文：", equation)
        self.assertIn("R²=1.000", equation)

    def test_long_bilingual_grouped_chart_labels_both_regressions_and_editable_legend(self) -> None:
        rows = []
        for year in range(1975, 2026):
            offset = year - 1975
            for source, count in (("英文", max(0, offset - 2)), ("中文", max(0, (offset - 8) * 2))):
                rows.append({"year": year, "source": source, "papers": count, "citations": 0})
        figure = Figure(figsize=(10, 5))
        options = PlotOptions(chart_type="bar", trend_group="language", show_labels=True,
                              label_size=15, label_interval=0,
                              show_regression=True, regression_method="quadratic",
                              legend_labels={"英文发文量": "English", "中文发文量": "中文资料"})
        render_figure(figure, rows, options)
        ax = figure.axes[0]
        self.assertEqual(len([line for line in ax.lines if "回归曲线" in line.get_label()]), 2)
        self.assertEqual(len([item for item in ax.texts if "R²=" in item.get_text()]), 2)
        numeric_labels = [item for item in ax.texts if item.get_text().isdecimal()]
        self.assertGreaterEqual(len(numeric_labels), 25)
        self.assertTrue(all(item.get_rotation() == 0 and item.get_fontsize() == 15
                            for item in numeric_labels))
        self.assertTrue({"English", "中文资料"}.issubset(
            {item.get_text() for item in ax.get_legend().get_texts()}))
        all_labels = Figure(figsize=(10, 5))
        options.label_interval = 1
        render_figure(all_labels, rows, options)
        self.assertGreater(len([item for item in all_labels.axes[0].texts
                                if item.get_text().isdecimal()]), len(numeric_labels))

    def test_dense_r_styles_use_selected_horizontal_label_size(self) -> None:
        rows = [{"year": year, "source": source, "papers": year - 1999 + index, "citations": 0}
                for year in range(2000, 2031) for index, source in enumerate(("英文", "中文"))]
        for chart_type in ("bilingual_quadratic", "gradient_combo"):
            with self.subTest(chart_type=chart_type):
                figure = Figure(figsize=(10, 5))
                render_figure(figure, rows, PlotOptions(chart_type=chart_type,
                                                       label_size=14, show_labels=True))
                numeric_labels = [item for item in figure.axes[0].texts if item.get_text().isdecimal()]
                self.assertGreater(len(numeric_labels), 10)
                self.assertTrue(all(item.get_rotation() == 0 and item.get_fontsize() == 14
                                    for item in numeric_labels))

    def test_annual_label_interval_keeps_latest_year_and_can_show_every_year(self) -> None:
        rows = [{"year": year, "source": "全部", "papers": year - 2019, "citations": 0}
                for year in range(2020, 2030)]
        for interval, expected in ((3, [1, 4, 7, 10]), (1, list(range(1, 11))),
                                   (None, list(range(1, 11)))):
            with self.subTest(interval=interval):
                figure = Figure(figsize=(5, 4))
                options = PlotOptions(label_size=16) if interval is None else PlotOptions(
                    label_interval=interval, label_size=16)
                render_figure(figure, rows, options)
                labels = [int(item.get_text()) for item in figure.axes[0].texts
                          if item.get_text().isdecimal()]
                self.assertEqual(labels, expected)

    def test_missing_calendar_years_are_zero_and_evenly_spaced(self) -> None:
        records = sample_records()
        records[0].fields["PY"] = ["1992"]
        records[1].fields["PY"] = ["2016"]
        options = PlotOptions(view="trend", chart_type="line", show_regression=True)
        rows = prepare_plot_data(records, options)
        self.assertEqual([row["year"] for row in rows], list(range(1992, 2017)))
        self.assertTrue(all(row["papers"] == 0 for row in rows[1:-1]))
        figure = Figure(figsize=(6, 4))
        render_figure(figure, rows, options)
        self.assertEqual(list(figure.axes[0].lines[0].get_xdata()), list(range(1992, 2017)))
        self.assertEqual(list(figure.axes[0].lines[1].get_xdata()), list(range(1992, 2017)))
        rank_options = PlotOptions(view="countries", chart_type="yearly_line")
        rank_rows = prepare_plot_data(records, rank_options)
        self.assertEqual([row["year"] for row in rank_rows], list(range(1992, 2017)))
        rank_figure = Figure(figsize=(6, 4))
        render_figure(rank_figure, rank_rows, rank_options)
        self.assertEqual(list(rank_figure.axes[0].lines[0].get_xdata()), list(range(1992, 2017)))

    def test_linear_regression_uses_calendar_year_and_matches_linear_counts(self) -> None:
        rows = [{"year": 1992 + index, "source": "全部", "papers": index + 1, "citations": 0}
                for index in range(5)]
        figure = Figure(figsize=(6, 4))
        render_figure(figure, rows, PlotOptions(view="trend", chart_type="line",
                                                show_regression=True, regression_method="lm"))
        fitted = figure.axes[0].lines[1]
        self.assertEqual(list(fitted.get_xdata()), [1992, 1993, 1994, 1995, 1996])
        self.assertTrue(all(abs(actual - expected) < 1e-8
                            for actual, expected in zip(fitted.get_ydata(), [1, 2, 3, 4, 5])))

    def test_every_chart_type_renders_from_same_data_rows(self) -> None:
        records = sample_records()
        for chart_type in TREND_TYPES:
            with self.subTest(view="trend", chart_type=chart_type):
                options = PlotOptions(chart_type=chart_type, metric="both")
                rows = prepare_plot_data(records, options)
                render_figure(Figure(figsize=(6, 4)), rows, options)
        for chart_type in RANK_TYPES:
            with self.subTest(view="countries", chart_type=chart_type):
                options = PlotOptions(view="countries", chart_type=chart_type)
                rows = prepare_plot_data(records, options)
                render_figure(Figure(figsize=(6, 4)), rows, options)

    def test_zero_citation_pie_and_cited_reference_frequency(self) -> None:
        records = sample_records()
        options = PlotOptions(view="cited_refs", chart_type="bar")
        rows = prepare_plot_data(records, options)
        self.assertEqual(rows[0]["papers"], 2)
        pie_options = PlotOptions(view="countries", chart_type="pie", metric="citations")
        zero_rows = [{"name": "China", "source": "全部", "year": None, "papers": 2, "citations": 0}]
        render_figure(Figure(figsize=(6, 4)), zero_rows, pie_options)

    def test_country_citation_line_high_values_move_left_or_right(self) -> None:
        rows = [{"name": "China", "source": "全部", "year": None, "papers": 3, "citations": 30},
                {"name": "USA", "source": "全部", "year": None, "papers": 2, "citations": 10}]
        for position, names, values in (("left", ["China", "USA"], [30, 10]),
                                        ("right", ["USA", "China"], [10, 30])):
            figure = Figure(figsize=(6, 4))
            render_figure(figure, rows, PlotOptions(view="countries", chart_type="overall_line",
                                                   metric="citations", high_position=position))
            self.assertEqual([label.get_text() for label in figure.axes[0].get_xticklabels()], names)
            self.assertEqual(list(figure.axes[0].lines[0].get_ydata()), values)


if __name__ == "__main__":
    unittest.main()
