"""Unified local bibliometric summaries and configurable Matplotlib figures."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .bibliometrics import citation_count, terms_for_record
from .models import WosRecord
from .year_filter import publication_year


VIEW_LABELS = {
    "trend": "年度趋势", "countries": "国家发文量排名", "institutions": "机构发文量排名",
    "journals": "期刊发文量排名", "authors": "作者发文量排名",
    "cited_authors": "来源作者总被引量", "cited_reference_authors": "被引作者频次",
    "cited_journals": "被引期刊频次", "cited_refs": "被引文献频次",
    "keywords": "关键词排名",
}
TREND_TYPES = {"bar": "柱状图", "line": "折线图", "point": "散点图", "area": "面积图",
               "dual_axis": "双 Y 轴柱线图"}
RANK_TYPES = {"bar": "总体条形图", "lollipop": "总体棒棒糖图", "overall_line": "总体折线图",
              "yearly_bar": "逐年柱状图", "yearly_line": "逐年折线图", "pie": "总体饼图", "radar": "总体雷达图"}


@dataclass
class PlotOptions:
    view: str = "trend"
    chart_type: str = "bar"
    metric: str = "papers"
    top_n: int = 10
    pie_top_n: int = 5
    include_database: bool = False
    keyword_source: str = "author"
    year_start: int | None = None
    year_end: int | None = None
    orientation: str = "horizontal"
    high_position: str = "top"
    year_interval: int = 1
    show_regression: bool = False
    regression_method: str = "lm"
    show_equation: bool = True
    show_title: bool = True
    title: str = ""
    subtitle: str = ""
    show_legend: bool = True
    legend_position: str = "right"
    show_grid: bool = True
    show_axis_titles: bool = True
    x_axis_title: str = ""
    y_axis_title: str = ""
    y2_axis_title: str = "引用量"
    show_labels: bool = True
    base_size: int = 12
    label_size: int = 9
    year_label_angle: int = 45
    institution_label_angle: int = 45
    colors: dict[str, str] | None = None
    pie_start: str = "#2F7295"
    pie_end: str = "#A8DCD4"


def prepare_plot_data(records: list[WosRecord], options: PlotOptions) -> list[dict]:
    """Return exactly the rows shown in the plot data table and CSV export."""
    if options.view not in VIEW_LABELS:
        raise ValueError("未知分析对象。")
    if options.view == "trend":
        buckets: dict[tuple[int, str], dict[str, int]] = defaultdict(lambda: {"papers": 0, "citations": 0})
        sources = {record.source_kind for record in records}
        grouped = options.include_database and len(sources) > 1
        for record in records:
            year = publication_year(record)
            if year is None:
                continue
            source = record.source_kind if grouped else "全部"
            buckets[(year, source)]["papers"] += 1
            buckets[(year, source)]["citations"] += citation_count(record)
        if not buckets:
            return []
        first = options.year_start if options.year_start is not None else min(year for year, _ in buckets)
        last = options.year_end if options.year_end is not None else max(year for year, _ in buckets)
        group_names = sorted({source for _, source in buckets})
        return [{"year": year, "source": source, **buckets[(year, source)]}
                for year in range(first, last + 1) for source in group_names]

    buckets: dict[tuple[str, str, int | None], dict[str, int]] = defaultdict(
        lambda: {"papers": 0, "citations": 0})
    for record in records:
        source = record.source_kind if options.include_database else "全部"
        year = publication_year(record)
        terms = terms_for_record(record, options.view, keyword_source=options.keyword_source)
        for term in terms:
            buckets[(term, source, year)]["papers"] += 1
            buckets[(term, source, year)]["citations"] += citation_count(record)
    metric = "citations" if options.view == "cited_authors" else options.metric
    totals: dict[str, int] = defaultdict(int)
    for (name, _, _), value in buckets.items():
        totals[name] += value[metric]
    visible_n = options.pie_top_n if options.chart_type == "pie" else options.top_n
    top = sorted(totals, key=lambda name: (-totals[name], name.casefold()))[:max(1, visible_n)]
    selected = set(top)
    grouped: dict[tuple[str, str, int | None], dict[str, int]] = defaultdict(
        lambda: {"papers": 0, "citations": 0})
    yearly = options.chart_type in {"yearly_bar", "yearly_line"}
    for (name, source, year), value in buckets.items():
        if name not in selected or (yearly and year is None):
            continue
        key = (name, source, year if yearly else None)
        grouped[key]["papers"] += value["papers"]
        grouped[key]["citations"] += value["citations"]
    if yearly and grouped:
        valid_years = [year for _, _, year in grouped if year is not None]
        if valid_years:
            first = options.year_start if options.year_start is not None else min(valid_years)
            last = options.year_end if options.year_end is not None else max(valid_years)
            for name in top:
                for source in {source for item_name, source, _ in grouped if item_name == name}:
                    for year in range(first, last + 1):
                        grouped[(name, source, year)]
    rank = {name: index for index, name in enumerate(top)}
    return [{"name": name, "source": source, "year": year, **value}
            for (name, source, year), value in sorted(grouped.items(),
                key=lambda item: (rank[item[0][0]], item[0][2] or 0, item[0][1]))]


def render_figure(figure, rows: list[dict], options: PlotOptions) -> None:
    """Render the selected view into an existing Matplotlib Figure."""
    import matplotlib as mpl
    import numpy as np

    mpl.rcParams["font.family"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    mpl.rcParams["axes.unicode_minus"] = False
    figure.clear()
    figure.patch.set_facecolor("#FFFFFF")
    if not rows:
        ax = figure.add_subplot(111)
        ax.text(.5, .5, "当前条件下没有可绘制数据", ha="center", va="center",
                transform=ax.transAxes, fontsize=options.base_size)
        ax.set_axis_off()
        return
    colors = {"papers": "#2F7295", "citations": "#65B7B1", "WOS": "#2F7295",
              "CNKI": "#70B9B2", "regression": "#B24C3E", "label": "#24495E"}
    colors.update(options.colors or {})
    if options.view == "trend":
        ax = figure.add_subplot(111)
        _render_trend(ax, rows, options, colors, np)
    elif options.chart_type == "radar":
        ax = figure.add_subplot(111, projection="polar")
        _render_radar(ax, rows, options, colors, np)
    elif options.chart_type == "pie":
        ax = figure.add_subplot(111)
        _render_pie(ax, rows, options, colors, np, mpl)
    else:
        ax = figure.add_subplot(111)
        _render_rank(ax, rows, options, colors, np)
    if options.show_title:
        figure.suptitle(options.title or VIEW_LABELS[options.view], fontsize=options.base_size + 3,
                        weight="bold", color="#24495E", y=.975, va="top")
    if options.subtitle:
        figure.text(.5, .91 if options.show_title else .965, options.subtitle,
                    ha="center", va="top", fontsize=max(8, options.base_size - 2), color="#5B6F7B")
    figure.tight_layout(rect=(.02, .03, .98, .85 if options.subtitle else .9))


def _series_color(name: str, index: int, colors: dict[str, str]) -> str:
    return colors.get(name.upper(), [colors["papers"], colors["citations"], "#8CB6CF", "#BD9BD0"][index % 4])


def _decorate(ax, options: PlotOptions, *, x_default: str, y_default: str, is_year: bool = False) -> None:
    ax.set_facecolor("#FFFFFF")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#D9E3E8")
        ax.spines[side].set_linewidth(.8)
    ax.tick_params(labelsize=max(7, options.base_size - 2), colors="#4A5F69", length=3)
    ax.grid(options.show_grid, axis="y", color="#D9E3E8", alpha=.8, linewidth=.65)
    ax.set_axisbelow(True)
    if options.show_axis_titles:
        ax.set_xlabel(options.x_axis_title or x_default, fontsize=options.base_size)
        ax.set_ylabel(options.y_axis_title or y_default, fontsize=options.base_size)
    if is_year:
        for label in ax.get_xticklabels():
            label.set_rotation(options.year_label_angle)
            label.set_ha("right" if options.year_label_angle else "center")
    if options.show_legend and ax.get_legend() is None and ax.get_legend_handles_labels()[1]:
        locations = {"top": "upper center", "bottom": "lower center", "left": "center left", "right": "upper left"}
        anchor = (1.01, 1) if options.legend_position == "right" else None
        ax.legend(loc=locations.get(options.legend_position, "upper left"), bbox_to_anchor=anchor,
                  fontsize=max(7, options.base_size - 2), frameon=False)


def _render_trend(ax, rows: list[dict], options: PlotOptions, colors: dict[str, str], np) -> None:
    years = sorted({row["year"] for row in rows})
    sources = sorted({row["source"] for row in rows})
    metrics = ["papers", "citations"] if options.metric == "both" else [options.metric]
    series = [(source, metric) for source in sources for metric in metrics]
    x = np.asarray(years, dtype=float)
    if options.chart_type == "dual_axis":
        by_year = {year: {"papers": 0, "citations": 0} for year in years}
        for row in rows:
            by_year[row["year"]]["papers"] += row["papers"]
            by_year[row["year"]]["citations"] += row["citations"]
        bars = ax.bar(x, [by_year[y]["papers"] for y in years], color=colors["papers"], label="发文量", width=.58)
        ax2 = ax.twinx()
        ax2.plot(x, [by_year[y]["citations"] for y in years], color=colors["citations"],
                 marker="o", linewidth=2, label="引用量")
        if options.show_axis_titles:
            ax2.set_ylabel(options.y2_axis_title or "引用量", fontsize=options.base_size)
        if options.show_labels:
            ax.bar_label(bars, fontsize=options.label_size, color=colors["label"])
        if options.show_regression and len(years) >= 3:
            _regression(ax, x, np.asarray([by_year[y]["papers"] for y in years], dtype=float),
                        options, colors, np, "发文量", .96)
            _regression(ax2, x, np.asarray([by_year[y]["citations"] for y in years], dtype=float),
                        options, {**colors, "regression": colors["citations"]}, np, "引用量", .84)
        if options.show_legend:
            handles1, labels1 = ax.get_legend_handles_labels()
            handles2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(handles1 + handles2, labels1 + labels2, fontsize=max(7, options.base_size - 2),
                      frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1))
    else:
        width = .82 / max(1, len(series))
        for index, (source, metric) in enumerate(series):
            values = [next((row[metric] for row in rows
                            if row["year"] == year and row["source"] == source), 0) for year in years]
            label = (source if source != "全部" else "") + (" · " if source != "全部" and len(metrics) > 1 else "") + (
                "发文量" if metric == "papers" else "引用量")
            color = _series_color(source, index, colors) if len(sources) > 1 else colors[metric]
            offsets = x + (index - (len(series) - 1) / 2) * width
            if options.chart_type == "line":
                ax.plot(x, values, marker="o", linewidth=2, label=label, color=color)
            elif options.chart_type == "point":
                ax.scatter(x, values, s=55, label=label, color=color)
            elif options.chart_type == "area":
                ax.fill_between(x, values, alpha=.5, label=label, color=color)
                ax.plot(x, values, linewidth=1.5, color=color)
            else:
                bars = ax.bar(offsets, values, width=width * .9, label=label, color=color)
                if options.show_labels and len(years) * len(series) <= 30:
                    ax.bar_label(bars, fontsize=options.label_size, color=colors["label"])
            if options.show_labels and options.chart_type in {"line", "point"} and len(years) * len(series) <= 30:
                for xi, yi in zip(x, values):
                    ax.annotate(str(yi), (xi, yi), xytext=(0, 6), textcoords="offset points",
                                ha="center", fontsize=options.label_size, color=colors["label"])
        if options.show_regression and len(years) >= 3:
            for index, metric in enumerate(metrics):
                totals = np.asarray([sum(row[metric] for row in rows if row["year"] == year)
                                     for year in years], dtype=float)
                series_colors = colors if metric == "papers" else {**colors, "regression": colors["citations"]}
                _regression(ax, x, totals, options, series_colors, np,
                            "发文量" if metric == "papers" else "引用量", .96 - index * .12)
    ax.set_xticks(x[::max(1, options.year_interval)], [str(y) for y in years[::max(1, options.year_interval)]])
    ax.set_xlim(x[0] - .6, x[-1] + .6)
    _decorate(ax, options, x_default="年份", y_default="发文量" if options.metric != "citations" else "引用量", is_year=True)


def _regression(ax, years, values, options: PlotOptions, colors: dict[str, str], np,
                series_name: str, text_y: float) -> None:
    x0 = years - years.min()
    if options.regression_method == "loess":
        smoothed = []
        bandwidth = max(3, math.ceil(len(years) * .55))
        for center in x0:
            distance = np.abs(x0 - center)
            radius = max(sorted(distance)[min(len(distance) - 1, bandwidth - 1)], 1)
            weights = np.maximum(0, 1 - (distance / radius) ** 3) ** 3
            design = np.column_stack((np.ones(len(x0)), x0 - center))
            coefficients, *_ = np.linalg.lstsq(design * np.sqrt(weights[:, None] + 1e-8),
                                                values * np.sqrt(weights + 1e-8), rcond=None)
            smoothed.append(float(coefficients[0]))
        ax.plot(years, smoothed, color=colors["regression"], linestyle="--", linewidth=2,
                label=f"{series_name} LOESS 平滑")
        return
    degree = 2 if options.regression_method == "quadratic" else 1
    coefficients = np.polyfit(x0, values, degree)
    fitted = np.polyval(coefficients, x0)
    ax.plot(years, fitted, color=colors["regression"], linestyle="--", linewidth=2,
            label=f"{series_name}回归曲线")
    if options.show_equation:
        if degree == 1:
            equation = f"y = {coefficients[0]:.3g}x {coefficients[1]:+.3g}"
        else:
            equation = f"y = {coefficients[0]:.3g}x² {coefficients[1]:+.3g}x {coefficients[2]:+.3g}"
        ax.text(.02, text_y, f"{series_name}: {equation}（x=年份−{int(years.min())}）",
                transform=ax.transAxes, va="top", fontsize=max(7, options.label_size),
                color=colors["regression"], bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none"})


def _render_rank(ax, rows: list[dict], options: PlotOptions, colors: dict[str, str], np) -> None:
    metric = "citations" if options.view == "cited_authors" else options.metric
    names = list(dict.fromkeys(row["name"] for row in rows))
    sources = sorted({row["source"] for row in rows})
    years = sorted({row["year"] for row in rows if row["year"] is not None})
    color = colors["citations" if metric == "citations" else "papers"]
    if options.chart_type in {"yearly_bar", "yearly_line"}:
        x = np.asarray(years, dtype=float)
        for index, name in enumerate(names):
            values = [sum(row[metric] for row in rows if row["name"] == name and row["year"] == year)
                      for year in years]
            item_color = _series_color(name, index, colors)
            if options.chart_type == "yearly_line":
                ax.plot(x, values, marker="o", label=name, color=item_color)
            else:
                width = .85 / max(1, len(names))
                ax.bar(x + (index - (len(names) - 1) / 2) * width, values,
                       width=width * .92, label=name, color=item_color)
        ax.set_xticks(x[::max(1, options.year_interval)], [str(y) for y in years[::max(1, options.year_interval)]])
        ax.set_xlim(x[0] - .6, x[-1] + .6)
        _decorate(ax, options, x_default="年份", y_default="引用量" if metric == "citations" else "发文量", is_year=True)
        return
    if options.chart_type == "overall_line":
        if options.high_position == "right":
            names = list(reversed(names))
        x = np.arange(len(names))
        for index, source in enumerate(sources):
            values = [sum(row[metric] for row in rows if row["name"] == name and row["source"] == source)
                      for name in names]
            ax.plot(x, values, marker="o", label=source if source != "全部" else "总量",
                    color=_series_color(source, index, colors))
        ax.set_xticks(x, names, rotation=options.institution_label_angle if options.view == "institutions" else 45,
                      ha="right")
        _decorate(ax, options, x_default="名称", y_default="引用量" if metric == "citations" else "发文量")
        return
    horizontal = options.orientation == "horizontal"
    if horizontal:
        names = names if options.high_position == "bottom" else list(reversed(names))
    elif options.high_position == "right":
        names = list(reversed(names))
    positions = np.arange(len(names), dtype=float)
    width = .78 / max(1, len(sources))
    for index, source in enumerate(sources):
        values = [sum(row[metric] for row in rows if row["name"] == name and row["source"] == source)
                  for name in names]
        offsets = positions + (index - (len(sources) - 1) / 2) * width
        color = _series_color(source, index, colors) if len(sources) > 1 else color
        label = source if source != "全部" else None
        if options.chart_type == "lollipop":
            if horizontal:
                ax.hlines(offsets, 0, values, color=color, linewidth=2)
                ax.scatter(values, offsets, color=color, s=45, label=label)
            else:
                ax.vlines(offsets, 0, values, color=color, linewidth=2)
                ax.scatter(offsets, values, color=color, s=45, label=label)
        elif horizontal:
            bars = ax.barh(offsets, values, height=width * .92, color=color, label=label)
            if options.show_labels and len(names) * len(sources) <= 30:
                ax.bar_label(bars, padding=3, fontsize=options.label_size, color=colors["label"])
        else:
            bars = ax.bar(offsets, values, width=width * .92, color=color, label=label)
            if options.show_labels and len(names) * len(sources) <= 30:
                ax.bar_label(bars, padding=3, fontsize=options.label_size, color=colors["label"])
        if options.show_labels and options.chart_type == "lollipop" and len(names) * len(sources) <= 30:
            for pos, value in zip(offsets, values):
                ax.annotate(str(value), (value, pos) if horizontal else (pos, value),
                            xytext=(4, 0) if horizontal else (0, 5), textcoords="offset points",
                            fontsize=options.label_size, color=colors["label"])
    if horizontal:
        ax.set_yticks(positions, names)
        _decorate(ax, options, x_default="引用量" if metric == "citations" else "发文量", y_default="名称")
    else:
        ax.set_xticks(positions, names, rotation=options.institution_label_angle if options.view == "institutions" else 45,
                      ha="right")
        _decorate(ax, options, x_default="名称", y_default="引用量" if metric == "citations" else "发文量")


def _render_pie(ax, rows: list[dict], options: PlotOptions, colors: dict[str, str], np, mpl) -> None:
    metric = "citations" if options.view == "cited_authors" else options.metric
    names = list(dict.fromkeys(row["name"] for row in rows))
    values = [sum(row[metric] for row in rows if row["name"] == name) for name in names]
    start = np.array(mpl.colors.to_rgb(options.pie_start))
    end = np.array(mpl.colors.to_rgb(options.pie_end))
    palette = [tuple(start * (1 - index / max(1, len(names) - 1)) + end * index / max(1, len(names) - 1))
               for index in range(len(names))]
    if not any(values):
        ax.text(.5, .5, "当前指标全部为零", transform=ax.transAxes, ha="center", va="center")
        ax.set_axis_off()
        return
    ax.pie(values, labels=names if options.show_labels else None,
           autopct="%1.1f%%" if options.show_labels else None, colors=palette,
           textprops={"fontsize": options.label_size, "color": colors["label"]}, startangle=90)
    if options.show_legend:
        ax.legend(names, loc="upper right", fontsize=max(7, options.base_size - 2))


def _render_radar(ax, rows: list[dict], options: PlotOptions, colors: dict[str, str], np) -> None:
    metric = "citations" if options.view == "cited_authors" else options.metric
    names = list(dict.fromkeys(row["name"] for row in rows))[:10]
    if len(names) < 3:
        ax.text(.5, .5, "雷达图至少需要 3 项", transform=ax.transAxes, ha="center")
        return
    values = [sum(row[metric] for row in rows if row["name"] == name) for name in names]
    angles = np.linspace(0, 2 * np.pi, len(names), endpoint=False).tolist()
    angles += angles[:1]
    values += values[:1]
    ax.plot(angles, values, color=colors["papers"], linewidth=2)
    ax.fill(angles, values, color=colors["papers"], alpha=.25)
    ax.set_xticks(angles[:-1], names, fontsize=max(7, options.base_size - 2))
    ax.grid(options.show_grid)
