"""Single configurable bibliometric chart workspace for the desktop UI."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.transforms import ScaledTranslation

from .chart_export import PALETTES, validate_color, validate_export_settings
from .basic_export import preferred_export_dir
from .analysis_scope import AI_SCOPE, ANALYSIS_SCOPES, DEDUPE_SCOPE, RAW_SCOPE
from .bibliometrics import has_citation_count
from .plot_engine import PlotOptions, RANK_TYPES, TREND_TYPES, VIEW_LABELS, prepare_plot_data, render_figure
from .plot_layout import apply_layout, layout_artists
from .year_filter import format_year_range, publication_year


COLOR_DEFAULTS = {"WOS": "#2F7295", "CNKI": "#70B9B2", "papers": "#2F7295",
                  "citations": "#65B7B1", "regression": "#B24C3E", "label": "#24495E",
                  "英文": "#86C058", "中文": "#00BFE0"}
COLOR_LABELS = {"WOS": "WOS", "CNKI": "CNKI", "papers": "发文量", "citations": "引用量",
                "regression": "回归曲线", "label": "数据标签", "pie_start": "饼图起始", "pie_end": "饼图结束",
                "英文": "英文", "中文": "中文"}


class PlotWorkbench(ttk.Frame):
    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent)
        self.app = app
        self.rows: list[dict] = []
        self.hidden_rows: set[int] = set()
        self._refresh_timer: str | None = None
        self._canvas_resize_timer: str | None = None
        self._canvas_size: tuple[int, int] | None = None
        self._applied_canvas_size: tuple[int, int] | None = None
        self._building = True
        self._variables()
        self._build()
        self._building = False
        self._update_choices()

    def _variables(self) -> None:
        defaults = PlotOptions()
        self.scope_var = tk.StringVar(value=DEDUPE_SCOPE)
        self.source_var = tk.StringVar(value="全部数据库")
        self.status_var = tk.StringVar(value="导入题录后即可查看图表；也可选择去重结果。")
        self.view_var = tk.StringVar(value="trend")
        self.metric_var = tk.StringVar(value="papers")
        self.chart_type_var = tk.StringVar(value="bar")
        self.top_n_var = tk.StringVar(value="10")
        self.pie_top_n_var = tk.StringVar(value="5")
        self.group_var = tk.BooleanVar(value=False)
        self.trend_group_var = tk.StringVar(value="language")
        self.keyword_source_var = tk.StringVar(value="author")
        self.orientation_var = tk.StringVar(value="horizontal")
        self.high_position_var = tk.StringVar(value="top")
        self.year_interval_var = tk.StringVar(value="1")
        self.regression_var = tk.BooleanVar(value=False)
        self.regression_method_var = tk.StringVar(value="lm")
        self.equation_var = tk.BooleanVar(value=True)
        self.show_title_var = tk.BooleanVar(value=True)
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        self.show_legend_var = tk.BooleanVar(value=True)
        self.legend_position_var = tk.StringVar(value="right")
        self.show_grid_var = tk.BooleanVar(value=True)
        self.show_axis_var = tk.BooleanVar(value=True)
        self.x_axis_var = tk.StringVar()
        self.y_axis_var = tk.StringVar()
        self.y2_axis_var = tk.StringVar(value="引用量")
        self.show_labels_var = tk.BooleanVar(value=True)
        self.base_size_var = tk.StringVar(value=str(defaults.base_size))
        self.label_size_var = tk.StringVar(value=str(defaults.label_size))
        self.year_angle_var = tk.StringVar(value="45")
        self.institution_angle_var = tk.StringVar(value="45")
        self.width_var = tk.StringVar(value="10")
        self.height_var = tk.StringVar(value="7")
        self.dpi_var = tk.StringVar(value="300")
        self.palette_var = tk.StringVar(value="湖蓝薄荷")
        self.color_vars = {key: tk.StringVar(value=value) for key, value in COLOR_DEFAULTS.items()}
        self.color_vars["pie_start"] = tk.StringVar(value="#2F7295")
        self.color_vars["pie_end"] = tk.StringVar(value="#A8DCD4")
        self.text_offsets: dict[str, list[float]] = {}
        self.legend_positions: dict[str, list[float]] = {}
        self.legend_names: dict[str, str] = {}
        self.legend_name_vars: dict[str, tk.StringVar] = {}
        self._legend_editor_keys: tuple[str, ...] = ()
        self._drag: dict | None = None
        self._text_originals: dict[str, object] = {}

    def _build(self) -> None:
        filter_row = ttk.Frame(self)
        filter_row.pack(fill="x", pady=(0, 5))
        ttk.Label(filter_row, text="分析题录").pack(side="left")
        scope = ttk.Combobox(filter_row, textvariable=self.scope_var,
            values=ANALYSIS_SCOPES, state="readonly", width=23)
        scope.pack(side="left", padx=(5, 15))
        scope.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Label(filter_row, text="数据库").pack(side="left")
        self.source_combo = ttk.Combobox(filter_row, textvariable=self.source_var,
            values=("全部数据库",), state="readonly", width=18)
        self.source_combo.pack(side="left", padx=5)
        self.source_combo.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Button(filter_row, text="刷新", command=self.refresh).pack(side="right")
        ttk.Label(self, textvariable=self.status_var, foreground="#52606D").pack(fill="x", pady=(0, 5))

        pane = tk.PanedWindow(self, orient="horizontal", sashwidth=5, bg="#D7DEE5", relief="flat")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, width=286)
        right = ttk.Frame(pane)
        pane.add(left, minsize=240, width=286)
        pane.add(right, minsize=400)

        scroller = tk.Canvas(left, highlightthickness=0, width=275)
        scroll = ttk.Scrollbar(left, orient="vertical", command=scroller.yview)
        scroller.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        scroller.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(scroller, padding=(5, 2, 8, 8))
        control_id = scroller.create_window((0, 0), window=controls, anchor="nw")
        controls.bind("<Configure>", lambda _event: scroller.configure(scrollregion=scroller.bbox("all")))
        scroller.bind("<Configure>", lambda event: scroller.itemconfigure(control_id, width=event.width))
        self.controls_scroller = scroller

        data = self._group(controls, "数据与图形")
        self.view_combo = self._combo(data, "分析对象", self.view_var, VIEW_LABELS)
        self.view_combo.bind("<<ComboboxSelected>>", self._view_changed, add="+")
        self.metric_combo = self._combo(data, "选择数据", self.metric_var,
                                        {"papers": "发文量", "citations": "引用量", "both": "发文量 + 引用量"})
        self.metric_combo.bind("<<ComboboxSelected>>", self._metric_changed)
        self.type_combo = self._combo(data, "图形类型", self.chart_type_var, TREND_TYPES)
        self.type_combo.bind("<<ComboboxSelected>>", self._type_changed)
        self._entry(data, "显示前 N 项（3–50）", self.top_n_var)
        self._entry(data, "饼图前 N 项（2–20）", self.pie_top_n_var)
        self._combo(data, "年度数据分组", self.trend_group_var,
                    {"language": "自动区分中文 / 英文", "none": "合并全部题录", "database": "按数据库"})
        self._check(data, "排名图按数据库分组", self.group_var)
        self._combo(data, "关键词来源", self.keyword_source_var,
                    {"author": "作者关键词", "author_plus": "作者 + Keywords Plus"})
        self._combo(data, "排名图方向", self.orientation_var,
                    {"horizontal": "横向", "vertical": "纵向"})
        self.high_combo = self._combo(data, "高值位置", self.high_position_var,
                                      {"top": "顶部", "bottom": "底部"})
        self._entry(data, "年份刻度间隔（1–20）", self.year_interval_var)
        trend = self._group(controls, "趋势拟合")
        self._check(trend, "绘制回归 / 平滑曲线", self.regression_var)
        self._combo(trend, "方式", self.regression_method_var,
                    {"lm": "线性回归", "quadratic": "二次多项式", "loess": "LOESS 平滑"})
        self._check(trend, "标注回归方程", self.equation_var)

        labels = self._group(controls, "标题、标签与图例")
        self._check(labels, "显示图表标题", self.show_title_var)
        self._entry(labels, "标题", self.title_var)
        self._entry(labels, "副标题", self.subtitle_var)
        self._check(labels, "显示图例", self.show_legend_var)
        self._combo(labels, "图例位置", self.legend_position_var,
                    {"top": "上方", "bottom": "下方", "left": "左侧", "right": "右侧"})
        ttk.Label(labels, text="图例名称（生成图表后可逐项修改）").pack(anchor="w", pady=(5, 0))
        self.legend_editor = ttk.Frame(labels)
        self.legend_editor.pack(fill="x")
        self._check(labels, "显示背景网格线", self.show_grid_var)
        self._check(labels, "显示坐标轴标题", self.show_axis_var)
        self._entry(labels, "X 轴标题", self.x_axis_var)
        self._entry(labels, "Y 轴标题", self.y_axis_var)
        self._entry(labels, "右 Y 轴标题", self.y2_axis_var)
        self._check(labels, "显示数据标签", self.show_labels_var)
        self._entry(labels, "整体字体大小（8–30）", self.base_size_var)
        self._entry(labels, "数据标签大小（1–20）", self.label_size_var)
        self._entry(labels, "年份文字角度（0–90）", self.year_angle_var)
        self._entry(labels, "机构名称角度（0–90）", self.institution_angle_var)

        palette = self._group(controls, "颜色")
        self.palette_combo = self._combo(palette, "一键配色", self.palette_var,
                                          {key: key for key in PALETTES} | {"自定义": "自定义"})
        self.palette_combo.bind("<<ComboboxSelected>>", self._apply_palette, add="+")
        for key, var in self.color_vars.items():
            line = ttk.Frame(palette)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=COLOR_LABELS[key]).pack(side="left")
            button = tk.Button(line, width=4, bg=var.get(), relief="solid",
                               command=lambda item=key: self._choose_color(item))
            button.pack(side="right")
            setattr(self, f"color_button_{key}", button)

        export = self._group(controls, "图片导出")
        self._entry(export, "宽度（英寸，5–30）", self.width_var)
        self._entry(export, "高度（英寸，4–20）", self.height_var)
        self._entry(export, "DPI（72–600）", self.dpi_var)

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(0, 3))
        ttk.Label(actions, text="图表预览", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(actions, text="导出绘图数据 CSV…", command=self.export_csv).pack(side="right")
        ttk.Button(actions, text="导出图片 PNG…", command=self.export_png).pack(side="right", padx=6)
        ttk.Button(actions, text="重置元素位置", command=self._reset_layout).pack(side="right")
        self.content_tabs = ttk.Notebook(right)
        self.content_tabs.pack(fill="both", expand=True)
        chart_frame = ttk.Frame(self.content_tabs)
        data_frame = ttk.Frame(self.content_tabs)
        self.content_tabs.add(chart_frame, text="图表预览")
        self.content_tabs.add(data_frame, text="绘图数据")
        ttk.Label(chart_frame, text="可拖动标题、图例、坐标文字和数据标签调整位置；导出图片会保留调整。",
                  foreground="#52606D").pack(fill="x", pady=(2, 3))
        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor="white")
        self.chart = FigureCanvasTkAgg(self.figure, master=chart_frame)
        canvas_widget = self.chart.get_tk_widget()
        canvas_widget.bind("<Configure>", self._defer_canvas_resize)
        canvas_widget.pack(fill="both", expand=True)
        self.chart.mpl_connect("button_press_event", self._drag_start)
        self.chart.mpl_connect("motion_notify_event", self._drag_move)
        self.chart.mpl_connect("button_release_event", self._drag_end)
        table_bar = ttk.Frame(data_frame)
        table_bar.pack(fill="x", pady=(4, 0))
        ttk.Label(table_bar, text="绘图数据 · 双击首列切换是否绘制", foreground="#52606D").pack(side="left")
        ttk.Button(table_bar, text="全部显示", command=self._show_all_rows).pack(side="right")
        self.table = ttk.Treeview(data_frame, show="headings")
        table_scroll = ttk.Scrollbar(data_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=table_scroll.set)
        table_scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", self._toggle_row)

        watched = [self.top_n_var, self.pie_top_n_var, self.group_var, self.trend_group_var, self.keyword_source_var,
                   self.orientation_var, self.high_position_var, self.year_interval_var,
                   self.regression_var, self.regression_method_var, self.equation_var,
                   self.show_title_var, self.title_var, self.subtitle_var, self.show_legend_var,
                   self.legend_position_var, self.show_grid_var, self.show_axis_var,
                   self.x_axis_var, self.y_axis_var, self.y2_axis_var, self.show_labels_var,
                   self.base_size_var, self.label_size_var, self.year_angle_var, self.institution_angle_var]
        for var in watched:
            var.trace_add("write", lambda *_: self.schedule_render())
        self.orientation_var.trace_add("write", lambda *_: self._update_high_choices())
        self._bind_control_wheel(scroller)

    def _bind_control_wheel(self, widget: tk.Widget) -> None:
        """Scroll the control column even when the pointer is over a child control."""
        widget.bind("<MouseWheel>", self._scroll_controls, add="+")
        for child in widget.winfo_children():
            self._bind_control_wheel(child)

    def _scroll_controls(self, event) -> str:
        delta = -1 if event.delta > 0 else 1
        self.controls_scroller.yview_scroll(delta, "units")
        return "break"

    @staticmethod
    def _group(parent: tk.Widget, title: str) -> ttk.LabelFrame:
        group = ttk.LabelFrame(parent, text=title, padding=6)
        group.pack(fill="x", pady=(0, 7))
        return group

    @staticmethod
    def _entry(parent: tk.Widget, label: str, variable: tk.Variable) -> ttk.Entry:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(5, 0))
        field = ttk.Entry(parent, textvariable=variable)
        field.pack(fill="x")
        return field

    @staticmethod
    def _check(parent: tk.Widget, label: str, variable: tk.BooleanVar) -> None:
        ttk.Checkbutton(parent, text=label, variable=variable).pack(anchor="w", pady=2)

    @staticmethod
    def _combo(parent: tk.Widget, label: str, variable: tk.StringVar, choices: dict[str, str]) -> ttk.Combobox:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(5, 0))
        combo = ttk.Combobox(parent, state="readonly", values=tuple(choices.values()))
        combo.pack(fill="x")
        combo.set(choices.get(variable.get(), next(iter(choices.values()))))
        combo.bind("<<ComboboxSelected>>", lambda _event: variable.set(next(
            (key for key, display in choices.items() if display == combo.get()), next(iter(choices)))))
        variable.trace_add("write", lambda *_: combo.set(choices.get(variable.get(), combo.get())))
        return combo

    def _view_changed(self, _event=None) -> None:
        self._update_choices()
        self.refresh()

    def _metric_changed(self, _event=None) -> None:
        selected = next((key for key, label in self._metric_choices().items()
                         if label == self.metric_combo.get()), None)
        if selected is not None:
            self.metric_var.set(selected)
        self._update_choices()
        self.refresh()

    def _type_changed(self, _event=None) -> None:
        selected = next((key for key, label in self._type_choices().items()
                         if label == self.type_combo.get()), None)
        if selected is not None:
            self.chart_type_var.set(selected)
        self._update_high_choices()
        self.refresh()

    def _update_high_choices(self) -> None:
        horizontal = self.orientation_var.get() == "horizontal"
        if self.chart_type_var.get() == "overall_line":
            choices = {"left": "左侧", "right": "右侧"}
        else:
            choices = {"top": "顶部", "bottom": "底部"} if horizontal else {"left": "左侧", "right": "右侧"}
        self.high_combo.configure(values=tuple(choices.values()))
        self.high_combo.bind("<<ComboboxSelected>>", lambda _event: self.high_position_var.set(next(
            (key for key, display in choices.items() if display == self.high_combo.get()), next(iter(choices)))))
        selected = self.high_position_var.get()
        if selected not in choices:
            self.high_position_var.set(next(iter(choices)))
        self.high_combo.set(choices[self.high_position_var.get()])

    def _metric_choices(self) -> dict[str, str]:
        view = self.view_var.get()
        metric_choices = {"papers": "发文量", "citations": "引用量"}
        if view == "trend":
            metric_choices["both"] = "发文量 + 引用量"
        elif view == "cited_authors":
            metric_choices = {"citations": "总被引量"}
        elif view in {"cited_reference_authors", "cited_journals", "cited_refs"}:
            metric_choices = {"papers": "被引频次"}
        elif view == "keywords":
            metric_choices = {"papers": "关键词频次"}
        return metric_choices

    def _type_choices(self) -> dict[str, str]:
        if self.view_var.get() == "trend":
            return TREND_TYPES
        if self.view_var.get() == "cited_authors":
            return {key: RANK_TYPES[key] for key in ("bar", "lollipop")}
        return RANK_TYPES

    def _update_choices(self) -> None:
        metric_choices = self._metric_choices()
        self.metric_combo.configure(values=tuple(metric_choices.values()))
        if self.view_var.get() == "trend" and self.chart_type_var.get() in {"gradient_combo", "bilingual_quadratic"}:
            self.metric_var.set("papers")
            self.metric_combo.configure(state="disabled")
        else:
            self.metric_combo.configure(state="readonly")
        if self.metric_var.get() not in metric_choices:
            self.metric_var.set(next(iter(metric_choices)))
        self.metric_combo.set(metric_choices[self.metric_var.get()])
        types = self._type_choices()
        self.type_combo.configure(values=tuple(types.values()))
        if self.chart_type_var.get() not in types:
            self.chart_type_var.set(next(iter(types)))
        self.type_combo.set(types[self.chart_type_var.get()])
        self._update_high_choices()

    def _apply_palette(self, _event=None) -> None:
        colors = PALETTES.get(self.palette_var.get())
        if colors:
            mapping = {"papers": colors["trend"], "citations": colors["ranking"]}
            mapping.update({key: value for key, value in colors.items() if key in self.color_vars})
            for key, value in mapping.items():
                self.color_vars[key].set(value)
                getattr(self, f"color_button_{key}").configure(bg=value)
        self.schedule_render()

    def _choose_color(self, key: str) -> None:
        _, value = colorchooser.askcolor(initialcolor=self.color_vars[key].get(), parent=self.app.root)
        if value:
            self.color_vars[key].set(value.upper())
            getattr(self, f"color_button_{key}").configure(bg=value)
            self.palette_var.set("自定义")
            self.schedule_render()

    def schedule_render(self) -> None:
        if self._building:
            return
        if self._refresh_timer is not None:
            self.after_cancel(self._refresh_timer)
            self._refresh_timer = None
        if self.app.tabs.index(self.app.tabs.select()) != 2:
            self.app._chart_dirty = True
            return
        self._refresh_timer = self.after(300, self.refresh)

    def _defer_canvas_resize(self, event) -> None:
        size = (event.width, event.height)
        if size == self._canvas_size and size == self._applied_canvas_size:
            return
        self._canvas_size = size
        if self._canvas_resize_timer is not None:
            self.after_cancel(self._canvas_resize_timer)
        # Wait until live window dragging settles; Matplotlib redraws on resize.
        self._canvas_resize_timer = self.after(900, self._finish_canvas_resize)

    def _finish_canvas_resize(self) -> None:
        self._canvas_resize_timer = None
        if (self._canvas_size is not None and self._canvas_size != self._applied_canvas_size
                and self.app.tabs.index(self.app.tabs.select()) == 2):
            self.chart.resize(SimpleNamespace(width=self._canvas_size[0], height=self._canvas_size[1]))
            self._applied_canvas_size = self._canvas_size

    def _plot_options(self) -> PlotOptions:
        def integer(var: tk.StringVar, low: int, high: int, label: str) -> int:
            try:
                value = int(var.get())
            except ValueError as exc:
                raise ValueError(f"{label}需要整数。") from exc
            if not low <= value <= high:
                raise ValueError(f"{label}须在 {low}–{high} 之间。")
            return value

        colors = {key: validate_color(var.get()) for key, var in self.color_vars.items()}
        return PlotOptions(
            view=self.view_var.get(), chart_type=self.chart_type_var.get(), metric=self.metric_var.get(),
            top_n=integer(self.top_n_var, 3, 50, "前 N 项"),
            pie_top_n=integer(self.pie_top_n_var, 2, 20, "饼图前 N 项"),
            include_database=self.group_var.get(), trend_group=self.trend_group_var.get(),
            keyword_source=self.keyword_source_var.get(),
            orientation=self.orientation_var.get(),
            high_position=self.high_position_var.get(),
            year_interval=integer(self.year_interval_var, 1, 20, "年份间隔"),
            show_regression=self.regression_var.get(), regression_method=self.regression_method_var.get(),
            show_equation=self.equation_var.get(), show_title=self.show_title_var.get(),
            title=self.title_var.get().strip(), subtitle=self.subtitle_var.get().strip(),
            show_legend=self.show_legend_var.get(), legend_position=self.legend_position_var.get(),
            legend_labels=self.legend_names.copy(),
            show_grid=self.show_grid_var.get(), show_axis_titles=self.show_axis_var.get(),
            x_axis_title=self.x_axis_var.get().strip(), y_axis_title=self.y_axis_var.get().strip(),
            y2_axis_title=self.y2_axis_var.get().strip(), show_labels=self.show_labels_var.get(),
            base_size=integer(self.base_size_var, 8, 30, "整体字体"),
            label_size=integer(self.label_size_var, 1, 20, "标签字体"),
            year_label_angle=integer(self.year_angle_var, 0, 90, "年份角度"),
            institution_label_angle=integer(self.institution_angle_var, 0, 90, "机构角度"),
            colors={key: value for key, value in colors.items() if key not in {"pie_start", "pie_end"}},
            pie_start=colors["pie_start"], pie_end=colors["pie_end"],
        )

    def refresh(self, _event=None) -> None:
        if self._building:
            return
        self._finish_canvas_resize()
        if self._refresh_timer is not None:
            self.after_cancel(self._refresh_timer)
            self._refresh_timer = None
        preview_records = self.app.parsed.records if self.scope_var.get() == RAW_SCOPE and self.app.parsed else self.app.records
        sources = sorted({record.source_kind for record in preview_records})
        choices = ("全部数据库", *sources)
        self.source_combo.configure(values=choices)
        if self.source_var.get() not in choices:
            self.source_var.set("全部数据库")
        try:
            self._update_choices()
            raw_scope = self.scope_var.get() == RAW_SCOPE
            records, start, end = self.app._scoped_records(include_excluded=raw_scope)
            options = self._plot_options()
            options.year_start = start
            options.year_end = end
        except ValueError as exc:
            self._empty(str(exc))
            return
        if not raw_scope and not self.app._dedupe_ready_for_scope(start, end):
            self._empty("请先按当前年份范围完成基础去重。")
            return
        if self.scope_var.get() == AI_SCOPE:
            if not self.app.results or set(self.app.results) != {record.record_id for record in self.app.records}:
                self._empty("当前项目尚无完整 AI 筛选结果；请选择基础去重结果。")
                return
            records = [record for record in records
                       if self.app.results[record.record_id].final_decision == "relevant"]
        if self.source_var.get() != "全部数据库":
            records = [record for record in records if record.source_kind == self.source_var.get()]
        if options.view == "cited_authors":
            records = [record for record in records if record.source_kind == "wos"]
        citation_note = ""
        if options.metric in {"citations", "both"} or options.view == "cited_authors":
            available = sum(has_citation_count(record) for record in records)
            missing = len(records) - available
            if not available and records:
                self._empty("这些题录未提供被引次数字段，无法计算引用量；空值不等于 0 次引用。")
                return
            if missing:
                citation_note = f" · 被引次数仅覆盖 {available}/{len(records)} 条，其余未提供"
            if options.metric == "citations":
                records = [record for record in records if has_citation_count(record)]
        self.status_var.set(
            f"{self.scope_var.get()} · {self.source_var.get()} · {format_year_range(start, end)}："
            f"{len(records)} 条题录，{sum(publication_year(r) is None for r in records)} 条年份未知{citation_note}。"
        )
        try:
            self.rows = prepare_plot_data(records, options)
            self.hidden_rows.clear()
            self._populate_table()
            self._render(options)
        except (ValueError, KeyError) as exc:
            self._empty(str(exc))

    def _empty(self, message: str) -> None:
        self.status_var.set(message)
        self.rows = []
        self.hidden_rows.clear()
        self.table.delete(*self.table.get_children())
        self.figure.clear()
        render_figure(self.figure, [], PlotOptions())
        self._text_originals = apply_layout(self.figure, self.text_offsets, self.legend_positions)
        self.chart.draw_idle()

    def _populate_table(self) -> None:
        self.table.delete(*self.table.get_children())
        is_trend = self.view_var.get() == "trend"
        yearly = self.chart_type_var.get() in {"yearly_bar", "yearly_line"}
        fields = ("show", "year", "source", "papers", "citations") if is_trend else (
            ("show", "name", "year", "source", "papers", "citations") if yearly else
            ("show", "name", "source", "papers", "citations"))
        labels = {"show": "绘制", "name": "名称", "year": "年份", "source": "数据库 / 语言",
                  "papers": "发文/频次", "citations": "引用量"}
        self.table.configure(columns=fields)
        for field in fields:
            self.table.heading(field, text=labels[field])
            self.table.column(field, width=45 if field == "show" else 210 if field == "name" else 85,
                              anchor="w" if field == "name" else "center")
        for index, row in enumerate(self.rows[:500]):
            self.table.insert("", "end", iid=str(index), values=tuple(
                ("☐" if index in self.hidden_rows else "☑") if field == "show" else
                row.get(field, "") if row.get(field) is not None else "" for field in fields))

    def _toggle_row(self, event) -> None:
        if self.table.identify_column(event.x) != "#1":
            return
        selection = self.table.selection()
        if not selection:
            return
        index = int(selection[0])
        if index in self.hidden_rows:
            self.hidden_rows.remove(index)
        else:
            self.hidden_rows.add(index)
        self._populate_table()
        self._render()

    def _show_all_rows(self) -> None:
        self.hidden_rows.clear()
        self._populate_table()
        self._render()

    def _selected_rows(self) -> list[dict]:
        return [row for index, row in enumerate(self.rows) if index not in self.hidden_rows]

    def _render(self, options: PlotOptions | None = None) -> None:
        try:
            render_figure(self.figure, self._selected_rows(), options or self._plot_options())
            self._sync_legend_editor()
            self._text_originals = apply_layout(self.figure, self.text_offsets, self.legend_positions)
            self.chart.draw_idle()
        except (ValueError, KeyError) as exc:
            self.status_var.set(str(exc))

    def _sync_legend_editor(self) -> None:
        keys = []
        if any(axis.get_legend() is not None for axis in self.figure.axes):
            for axis in self.figure.axes:
                _handles, labels = axis.get_legend_handles_labels()
                keys.extend(label for label in labels if label and not label.startswith("_") and label not in keys)
        current = tuple(keys)
        if current == self._legend_editor_keys:
            return
        for child in self.legend_editor.winfo_children():
            child.destroy()
        self.legend_name_vars.clear()
        self._legend_editor_keys = current
        if not current:
            ttk.Label(self.legend_editor, text="当前图表没有可编辑的图例项。", style="Hint.TLabel").pack(anchor="w")
            return
        for key in current:
            row = ttk.Frame(self.legend_editor)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=key, width=18).pack(side="left")
            variable = tk.StringVar(value=self.legend_names.get(key, key))
            variable.trace_add("write", lambda *_args, name=key, var=variable: self._legend_name_changed(name, var))
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
            self.legend_name_vars[key] = variable
            self._bind_control_wheel(row)

    def _legend_name_changed(self, name: str, variable: tk.StringVar) -> None:
        self.legend_names[name] = variable.get()
        self.schedule_render()

    def _drag_start(self, event) -> None:
        if event.button != 1 or event.x is None or event.y is None:
            return
        texts, legends = layout_artists(self.figure)
        renderer = self.chart.get_renderer()
        for key, legend in reversed(legends):
            box = legend.get_window_extent(renderer)
            if box.contains(event.x, event.y):
                position = self.figure.transFigure.inverted().transform((box.x0, box.y0))
                self._drag = {"kind": "legend", "key": key, "artist": legend,
                              "origin": (event.x, event.y), "position": position}
                return
        for key, artist in reversed(texts):
            if key in self._text_originals and artist.contains(event)[0]:
                self._drag = {"kind": "text", "key": key, "artist": artist,
                              "origin": (event.x, event.y),
                              "position": self.text_offsets.get(key, [0.0, 0.0])}
                return

    def _drag_move(self, event) -> None:
        if self._drag is None or event.x is None or event.y is None:
            return
        item = self._drag
        dx = (event.x - item["origin"][0]) / self.figure.bbox.width
        dy = (event.y - item["origin"][1]) / self.figure.bbox.height
        position = [float(item["position"][0] + dx), float(item["position"][1] + dy)]
        if item["kind"] == "text":
            self.text_offsets[item["key"]] = position
            item["artist"].set_transform(self._text_originals[item["key"]] + ScaledTranslation(
                position[0] * self.figure.get_figwidth(), position[1] * self.figure.get_figheight(),
                self.figure.dpi_scale_trans))
        else:
            self.legend_positions[item["key"]] = position
            item["artist"].set_loc("lower left")
            item["artist"].set_bbox_to_anchor(position, transform=self.figure.transFigure)
        self.chart.draw_idle()

    def _drag_end(self, _event) -> None:
        if self._drag is None:
            return
        self._drag = None
        self.app.project_data["chart_settings"] = self.settings()
        self.app.workspace_store.save_view(self.app.project_data)

    def _reset_layout(self) -> None:
        self.text_offsets.clear()
        self.legend_positions.clear()
        self._render()
        self.app.project_data["chart_settings"] = self.settings()
        self.app.workspace_store.save_view(self.app.project_data)

    def _export_path(self, suffix: str, title: str) -> str:
        initial_dir = preferred_export_dir(self.app.app_dir, self.app.project_data.get("chart_export_dir"))
        created_dir = not initial_dir.exists()
        initial_dir.mkdir(parents=True, exist_ok=True)
        selected = filedialog.asksaveasfilename(parent=self.app.root, title=title, initialdir=str(initial_dir),
            initialfile=f"{VIEW_LABELS[self.view_var.get()]}_{datetime.now():%y%m%d%H%M}.{suffix}",
            defaultextension=f".{suffix}", filetypes=[(suffix.upper(), f"*.{suffix}")])
        if not selected and created_dir:
            try:
                initial_dir.rmdir()
            except OSError:
                pass
        return selected

    def export_png(self) -> None:
        rows = self._selected_rows()
        if not rows:
            messagebox.showwarning("无图表数据", "当前没有勾选可绘制的行。", parent=self.app.root)
            return
        try:
            width, height, dpi = float(self.width_var.get()), float(self.height_var.get()), int(self.dpi_var.get())
            validate_export_settings(width, height, dpi)
            options = self._plot_options()
        except ValueError as exc:
            messagebox.showwarning("导出设置", str(exc), parent=self.app.root)
            return
        destination = self._export_path("png", "导出图表 PNG")
        if not destination:
            return
        try:
            figure = Figure(figsize=(width, height), dpi=dpi, facecolor="white")
            render_figure(figure, rows, options)
            apply_layout(figure, self.text_offsets, self.legend_positions)
            figure.savefig(destination, dpi=dpi, facecolor="white")
        except (OSError, ValueError) as exc:
            messagebox.showerror("图片导出失败", str(exc), parent=self.app.root)
            return
        self._saved(destination)

    def export_csv(self) -> None:
        rows = self._selected_rows()
        if not rows:
            messagebox.showwarning("无绘图数据", "当前没有勾选的绘图数据。", parent=self.app.root)
            return
        destination = self._export_path("csv", "导出绘图数据 CSV")
        if not destination:
            return
        try:
            with Path(destination).open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            messagebox.showerror("数据导出失败", str(exc), parent=self.app.root)
            return
        self._saved(destination)

    def _saved(self, destination: str) -> None:
        self.app.project_data["chart_export_dir"] = str(Path(destination).parent)
        self.app.project_data["chart_settings"] = self.settings()
        self.app.workspace_store.save_view(self.app.project_data)
        self.app._log(f"图表已导出：{destination}")
        messagebox.showinfo("导出完成", destination, parent=self.app.root)

    def settings(self) -> dict:
        names = ("scope", "source", "view", "metric", "chart_type", "top_n", "pie_top_n", "group", "trend_group", "keyword_source",
                 "orientation", "high_position", "year_interval", "regression", "regression_method",
                 "equation", "show_title", "title", "subtitle", "show_legend", "legend_position",
                 "show_grid", "show_axis", "x_axis", "y_axis", "y2_axis", "show_labels", "base_size",
                 "label_size", "year_angle", "institution_angle", "width", "height", "dpi", "palette")
        result = {name: getattr(self, f"{name}_var").get() for name in names}
        result["colors"] = {name: var.get() for name, var in self.color_vars.items()}
        result["text_offsets"] = self.text_offsets
        result["legend_positions"] = self.legend_positions
        result["legend_names"] = self.legend_names.copy()
        return result

    def load_settings(self, data: dict | None) -> None:
        data = data or {}
        if data.get("palette") == "经典蓝橙" and data.get("colors", {}).get("papers") == "#4C78A8":
            data = {**data, "palette": "湖蓝薄荷", "colors": dict(COLOR_DEFAULTS)}
        self._building = True
        for name, value in data.items():
            variable = getattr(self, f"{name}_var", None)
            if variable is not None:
                try:
                    variable.set(value)
                except tk.TclError:
                    pass
        saved_colors = dict(data.get("colors", {}))
        if "trend_color" in data:
            saved_colors.setdefault("papers", data["trend_color"])
        if "ranking_color" in data:
            saved_colors.setdefault("citations", data["ranking_color"])
        for name, value in saved_colors.items():
            if name in self.color_vars:
                try:
                    color = validate_color(value)
                    self.color_vars[name].set(color)
                    getattr(self, f"color_button_{name}").configure(bg=color)
                except ValueError:
                    pass
        self.text_offsets = {key: list(value) for key, value in data.get("text_offsets", {}).items()}
        self.legend_positions = {key: list(value) for key, value in data.get("legend_positions", {}).items()}
        self.legend_names = {str(key): str(value) for key, value in data.get("legend_names", {}).items()}
        self._legend_editor_keys = ()
        self._building = False
        self._update_choices()
