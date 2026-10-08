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
