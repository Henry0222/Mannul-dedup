"""Project-scoped VOS map controls and inspectable node selection."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .vos_desktop import launch_vosviewer_desktop
from .vos_defaults import (BUILD_KEYS, apply_graph_defaults, graph_defaults_path,
                           preferences, save_preferences)
from .vos_network import (CITED_TYPES, NETWORK_TYPES, build_vos_network, filter_vos_network, inherit_vos_parameters,
                          node_citation_coverage, save_vos_network)
from .vos_viewer import launch_viewer


class VOSPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, app) -> None:
        super().__init__(parent, padding=20)
        self.app = app
        self.kind_var = tk.StringVar(value=NETWORK_TYPES["keywords"])
        self.min_var = tk.StringVar(value="2")
        self.max_var = tk.StringVar(value="80")
        self.keyword_var = tk.StringVar(value="作者关键词")
        self.scope_var = tk.StringVar(value="基础去重结果")
        self.status_var = tk.StringVar(value="先预览节点并勾选，再创建图谱。")
        self.count_var = tk.StringVar(value="")
        self.data: dict | None = None
        self.coverage: dict[int, int] = {}
        self.selected: set[int] = set()
        self.excluded_labels: dict[str, list[str]] = {}
        self.sort_key = "frequency"
        self.sort_reverse = True
        self._preview_key: tuple | None = None
        self._settings_timer: str | None = None
        self._restoring = False
        self.desktop_exe = ""

        ttk.Label(self, text="VOS 图谱", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="基于当前项目已去重题录；节点筛选后可在内置窗口调整布局和聚类。",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 12))
        controls = ttk.LabelFrame(self, text="建图选项", padding=12)
        controls.pack(fill="x")
        self._row(controls, "题录范围", self.scope_var, ("基础去重结果", "AI 筛选相关"))
        self._row(controls, "图谱类型", self.kind_var, tuple(NETWORK_TYPES.values()))
        self._row(controls, "关键词来源", self.keyword_var, ("作者关键词", "作者 + Keywords Plus"))
        self._entry(controls, "最少出现次数", self.min_var)
        self._entry(controls, "最多节点数", self.max_var)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 7))
        ttk.Button(actions, text="刷新候选节点", command=self.refresh).pack(side="left")
        ttk.Button(actions, text="生成并打开图谱", command=self.create_map).pack(side="left", padx=8)
        ttk.Button(actions, text="打开上次图谱", command=self.reopen_last).pack(side="left")
        ttk.Button(actions, text="VOS 桌面版（原生叠加/密度）",
                   command=self.open_desktop).pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.count_var, style="Hint.TLabel").pack(side="right")

        table = ttk.LabelFrame(self, text="节点清单 · 点击首列纳入或排除，点击列标题排序", padding=8)
        table.pack(fill="both", expand=True)
        columns = ("include", "label", "frequency", "citations", "tls")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=13, selectmode="none")
        for key, label, width, anchor in (
            ("include", "纳入", 65, "center"), ("label", "名称", 380, "w"),
            ("frequency", "频次", 95, "e"), ("citations", "引用次数", 105, "e"),
            ("tls", "TLS", 95, "e")):
            self.tree.heading(key, text=label, command=lambda name=key: self.sort_by(name))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "label"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        ttk.Label(self, textvariable=self.status_var, wraplength=950, style="Hint.TLabel").pack(anchor="w", pady=(8, 0))

        for var in (self.kind_var, self.min_var, self.max_var, self.keyword_var, self.scope_var):
            var.trace_add("write", lambda *_args: self._settings_changed())

    @staticmethod
    def _row(parent, label, variable, values):
        line = ttk.Frame(parent)
        line.pack(fill="x", pady=3)
        ttk.Label(line, text=label, width=16).pack(side="left")
        ttk.Combobox(line, textvariable=variable, values=values, state="readonly", width=27).pack(side="left")

    @staticmethod
    def _entry(parent, label, variable):
        line = ttk.Frame(parent)
        line.pack(fill="x", pady=3)
        ttk.Label(line, text=label, width=16).pack(side="left")
        ttk.Entry(line, textvariable=variable, width=12).pack(side="left")

    def _invalidate(self):
        self._preview_key = None

    def _settings_changed(self):
        self._invalidate()
        if self._restoring:
            return
        if self._settings_timer is not None:
            self.after_cancel(self._settings_timer)
        self._settings_timer = self.after(450, self._save_settings)

    def _save_settings(self):
        self._settings_timer = None
        self.app.project_data["vos_settings"] = self.settings()
        self.app.workspace_store.save_view(self.app.project_data)
        save_preferences(self.app.app_dir, build={key: self.settings()[key] for key in BUILD_KEYS})

    def settings(self) -> dict:
        return {"kind": self.kind_var.get(), "min": self.min_var.get(), "max": self.max_var.get(),
                "keyword_source": self.keyword_var.get(), "scope": self.scope_var.get(),
                "excluded_labels": self.excluded_labels}

    def restore(self, settings: dict) -> None:
        if self._settings_timer is not None:
            self.after_cancel(self._settings_timer)
            self._settings_timer = None
        self._restoring = True
        global_preferences = preferences(self.app.app_dir)
        saved_exe = global_preferences.get("desktop_exe", "")
        old_exe = settings.get("desktop_exe", "")
        if not saved_exe and isinstance(old_exe, str) and Path(old_exe).is_file():
            saved_exe = old_exe
            save_preferences(self.app.app_dir, desktop_exe=old_exe)
        self.desktop_exe = saved_exe if isinstance(saved_exe, str) else ""
        if not settings:
            settings = global_preferences.get("build", {})
        if not isinstance(settings, dict):
            settings = {}
        self.excluded_labels = settings.get("excluded_labels", {}).copy()
        for name, variable, choices in (
            ("kind", self.kind_var, NETWORK_TYPES.values()),
            ("keyword_source", self.keyword_var, ("作者关键词", "作者 + Keywords Plus")),
            ("scope", self.scope_var, ("基础去重结果", "AI 筛选相关"))):
            if settings.get(name) in choices:
                variable.set(settings[name])
        for name, variable in (("min", self.min_var), ("max", self.max_var)):
            if isinstance(settings.get(name), str) and settings[name].isdecimal():
                variable.set(settings[name])
        self.data = None
        self.tree.delete(*self.tree.get_children())
        self._invalidate()
        self._restoring = False

    def _records(self):
        records, start, end = self.app._scoped_records()
        if not self.app._dedupe_ready_for_scope(start, end):
            raise ValueError("请先完成基础去重。")
        if self.scope_var.get() == "AI 筛选相关":
            if not self.app.results or set(self.app.results) != {r.record_id for r in self.app.records}:
                raise ValueError("当前项目尚无完整 AI 筛选结果。")
            records = [r for r in records if self.app.results[r.record_id].final_decision == "relevant"]
        return records

    def refresh(self, *, quiet=False):
        try:
            records = self._records()
            kind = next(key for key, label in NETWORK_TYPES.items() if label == self.kind_var.get())
            source = "author_plus" if self.keyword_var.get() == "作者 + Keywords Plus" else "author"
            key = (self.app.project_data["id"], self.app.project_data.get("import_generation"),
                   len(records), kind, source, self.min_var.get(), self.max_var.get(),
                   self.scope_var.get(), self.app.year_start_var.get(), self.app.year_end_var.get())
            if key == self._preview_key:
                return
            self.data = build_vos_network(records, kind, min_occurrences=int(self.min_var.get()),
                                          max_items=int(self.max_var.get()), keyword_source=source)
            self.coverage = node_citation_coverage(records, self.data, kind, source)
            excluded = set(self.excluded_labels.get(kind, []))
            self.selected = {item["id"] for item in self.data["network"]["items"]
                             if item["label"].casefold() not in excluded}
            self._preview_key = key
            self._render_rows()
            if kind in CITED_TYPES:
                detail = "原始 CR 往往不含文章题名，节点显示引文条目。" if kind == "cited_articles" else ""
                self.status_var.set("连线表示同一篇题录同时引用两个节点；“引用次数”为本项目题录集中的被引次数，不是全球被引量。" + detail)
            elif kind == "journals":
                self.status_var.set("连线表示不同来源期刊的文章引用了相同参考文献；“引用次数”为来源文章可读取的被引总量。")
            else:
                detail = "机构名称已按完整 WoS C3 字段重建；请生成新图谱，旧窗口不会自动更新。" if kind == "institutions" else ""
                if kind == "countries":
                    detail = " 国家规则已应用于多组 WoS 地址；已保存的旧图谱不会自动更新，请生成新图谱。"
                self.status_var.set("引用次数仅统计源题录中可读取的被引次数；“—”表示没有可用引用数据。" + detail)
        except (ValueError, OSError, StopIteration) as exc:
            self.data = None
            self.tree.delete(*self.tree.get_children())
            self.count_var.set("")
            self.status_var.set(str(exc))
            if not quiet:
                messagebox.showerror("VOS 节点预览", str(exc), parent=self.app.root)

    def _render_rows(self):
        if not self.data:
            return
        items = self.data["network"]["items"]
        def value(item):
            weights = item.get("weights", {})
            return {"include": item["id"] in self.selected, "label": item["label"].casefold(),
                    "frequency": weights.get("Documents", 0), "citations": weights.get("Citations", 0),
                    "tls": weights.get("Total link strength", 0)}[self.sort_key]
        sorted_items = sorted(items, key=value, reverse=self.sort_reverse)
        self.tree.delete(*self.tree.get_children())
        for item in sorted_items:
            weights = item.get("weights", {})
            citation = weights.get("Citations", 0) if self.coverage.get(item["id"], 0) else "—"
            self.tree.insert("", "end", iid=str(item["id"]), values=(
                "☑" if item["id"] in self.selected else "□", item["label"],
                weights.get("Documents", 0), citation, weights.get("Total link strength", 0)))
        self.count_var.set(f"纳入 {len(self.selected)} / {len(items)} 个节点")

    def sort_by(self, key):
        self.sort_reverse = not self.sort_reverse if self.sort_key == key else key != "label"
        self.sort_key = key
        self._render_rows()

    def _on_click(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid or not self.data:
            return
        node_id = int(iid)
        if node_id in self.selected:
            self.selected.remove(node_id)
        else:
            self.selected.add(node_id)
        kind = next(key for key, label in NETWORK_TYPES.items() if label == self.kind_var.get())
        self.excluded_labels[kind] = [item["label"].casefold() for item in self.data["network"]["items"]
                                      if item["id"] not in self.selected]
        self._render_rows()
        self.app._save_project_view()

    def _save_current_map(self) -> tuple[Path, dict, str]:
        self.refresh(quiet=True)
        if not self.data:
            raise ValueError(self.status_var.get())
        kind = next(key for key, label in NETWORK_TYPES.items() if label == self.kind_var.get())
        data = filter_vos_network(self.data, self.selected)
        data = inherit_vos_parameters(data, self.app.project_data.get("last_vos_path", ""))
        data = apply_graph_defaults(data, graph_defaults_path(self.app.app_dir))
        path = save_vos_network(data, self.app.app_dir / "output", kind)
        self.app.project_data["last_vos_path"] = str(path)
        self.app._save_project_view()
        return path, data, kind

    def create_map(self):
        try:
            path, data, kind = self._save_current_map()
            launch_viewer(path)
            nodes, links = len(data["network"]["items"]), len(data["network"]["links"])
            self.status_var.set(f"已建立 {nodes} 个节点、{links} 条连线；调整会自动保存至 {path.name}")
            self.app._log(f"VOS {NETWORK_TYPES[kind]}：{nodes} 个节点、{links} 条连线；{path}")
        except (ValueError, OSError, RuntimeError, StopIteration) as exc:
            self.status_var.set(str(exc))
            messagebox.showerror("VOS 图谱", str(exc), parent=self.app.root)

    def open_desktop(self):
        executable = preferences(self.app.app_dir).get("desktop_exe", "") or self.desktop_exe
        if not Path(executable).is_file():
            executable = filedialog.askopenfilename(
                parent=self.app.root, title="选择 VOSviewer 桌面版程序",
                filetypes=[("VOSviewer 程序", "VOSviewer.exe"), ("Windows 程序", "*.exe")])
            if not executable:
                return
        try:
            path, data, kind = self._save_current_map()
            map_path, network_path = launch_vosviewer_desktop(executable, path)
            self.desktop_exe = executable
            save_preferences(self.app.app_dir, desktop_exe=executable)
            nodes = len(data["network"]["items"])
            self.status_var.set(f"已将 {nodes} 个节点交给 VOSviewer 桌面版；可在其中切换原生叠加与密度视图。")
            self.app._log(f"VOS 桌面版 {NETWORK_TYPES[kind]}：{map_path}；{network_path}")
        except (ValueError, OSError, RuntimeError, StopIteration) as exc:
            self.status_var.set(str(exc))
            messagebox.showerror("VOS 桌面版", str(exc), parent=self.app.root)

    def reopen_last(self):
        path = Path(self.app.project_data.get("last_vos_path", ""))
        if path.is_file():
            try:
                launch_viewer(path)
            except (RuntimeError, OSError) as exc:
                messagebox.showerror("VOS 图谱", str(exc), parent=self.app.root)
        else:
            messagebox.showinfo("VOS 图谱", "当前项目还没有已保存的图谱。", parent=self.app.root)
