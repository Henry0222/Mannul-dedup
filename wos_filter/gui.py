from __future__ import annotations

import os
import json
import queue
import shutil
import sys
import threading
import time
import traceback
import unicodedata
from collections import Counter
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, W, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except (ImportError, OSError):
    DND_FILES = None
    TkinterDnD = None

from .cache import JsonlAuditLog, ResultCache
from .deepseek import (
    API_KEY_ENV as DEEPSEEK_API_KEY_ENV,
    API_KEY_FILENAME as DEEPSEEK_API_KEY_FILENAME,
    API_KEY_PLACEHOLDER as DEEPSEEK_API_KEY_PLACEHOLDER,
    DEFAULT_MODEL as DEEPSEEK_MODEL,
    ApiKeyError as DeepSeekApiKeyError,
    DeepSeekClient,
    classify_with_cache as classify_with_deepseek_cache,
    load_api_key as load_deepseek_api_key,
)
from .jev import (
    API_KEY_ENV as JEV_API_KEY_ENV,
    API_KEY_FILENAME as JEV_API_KEY_FILENAME,
    API_KEY_PLACEHOLDER as JEV_API_KEY_PLACEHOLDER,
    DEFAULT_MODEL as JEV_MODEL,
    ApiKeyError as JevApiKeyError,
    JevClient,
    classify_with_cache as classify_with_jev_cache,
    load_api_key as load_jev_api_key,
)
from .hybrid import (
    DEFAULT_DEEPSEEK_REVIEW_CONCURRENCY,
    HybridClient,
    classify_with_cache as classify_with_hybrid_cache,
)
from .exporter import DISPLAY_DECISION, export_results
from .file_drop import expand_bibliography_inputs, normalized_path_key
from .models import Classification, DuplicateEntry, WosRecord
from .source_import import ImportFileSummary, ImportFormatError, import_one
from .citation_backfill import backfill_citation_fields
from .project_workspace import WorkspaceStore, active_records, duplicate_entries, record_from_json, record_key, record_to_json
from .dedupe_review import PRESET_LABELS, apply_review, new_run
from .dedupe_review import auto_review_run
from .basic_export import default_export_name, preferred_export_dir, export_basic_records
from .analysis_scope import DEDUPE_SCOPE, RAW_SCOPE
from .plot_workbench import PlotWorkbench
from .vos_panel import VOSPanel
from .import_preview import ImportPreviewWindow
from .plain_merge import PlainMergeWindow
from .merge_scope import historical_snapshot
from .vocabulary import _key, merge_vocabulary
from .vocabulary_impact import vocabulary_impact
from .shared_vocabulary import SharedVocabularyStore, SHARED_KINDS
from .vocabulary_review import list_merge_pairs
from . import ui_theme as ui
from .wos import ParsedWos, find_doi_conflicts
from .year_filter import format_year_range, parse_year_range, partition_records_by_year, publication_year


APP_TITLE = "多来源文献相关性筛选工具"
APP_VERSION = "1.24.1"
SOURCE_LABELS = {"wos": "Web of Science", "scopus": "Scopus", "pubmed": "PubMed",
                 "sciencedirect": "ScienceDirect", "cnki": "CNKI", "wanfang": "万方",
                 "vip": "维普", "yiigle": "中华医学库", "未识别": "未识别"}
PROVIDER_HYBRID = "Jev + DeepSeek 复核"
PROVIDER_JEV = "TypeSafe Jev"
PROVIDER_DEEPSEEK = "DeepSeek"
DEFAULT_WORKERS = 16
MAX_WORKERS = 32


def get_app_dir() -> Path:
    if os.environ.get("MANNUL_GUI_SELFTEST_DIR"):
        return Path(os.environ["MANNUL_GUI_SELFTEST_DIR"]).resolve()
    if getattr(sys, "frozen", False):
        folder = Path(sys.executable).resolve().parent
        return folder.parent if folder.name.endswith("-快速启动版") else folder
    return Path(__file__).resolve().parent.parent


def get_resource_dir() -> Path:
    """返回源码资源目录或 PyInstaller 单文件解包目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def ask_project_name(parent: tk.Tk, title: str, initial: str = "") -> str | None:
    """Project dialog sized to its text and controls at the current DPI scale."""
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.configure(bg=ui.BG)
    dialog.resizable(True, True)
    dialog.transient(parent)
    result: str | None = None
    panel = ttk.Frame(dialog, padding=(26, 24))
    panel.pack(fill=BOTH, expand=True, padx=8, pady=8)
    ttk.Label(panel, text=title, style="Title.TLabel").pack(anchor=W)
    ttk.Label(panel, text="为这个独立文献工作区取一个容易辨认的名称。",
              style="Hint.TLabel", wraplength=380).pack(anchor=W, pady=(6, 18))
    ttk.Label(panel, text="项目名称", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor=W)
    name_var = tk.StringVar(value=initial)
    entry = ttk.Entry(panel, textvariable=name_var, font=("Microsoft YaHei UI", 11))
    entry.pack(fill=X, pady=(5, 3))
    error_var = tk.StringVar()
    ttk.Label(panel, textvariable=error_var, foreground="#B45A51").pack(anchor=W)
    actions = ttk.Frame(panel)
    actions.pack(side=tk.BOTTOM, anchor=tk.E, pady=(11, 0))

    def accept(_event=None) -> None:
        nonlocal result
        name = name_var.get().strip()
        if not name:
            error_var.set("请输入项目名称。")
            entry.focus_set()
            return
        result = name
        dialog.destroy()

    ttk.Button(actions, text="取消", width=10, command=dialog.destroy).pack(side=LEFT, padx=(0, 9))
    ttk.Button(actions, text="创建项目" if not initial else "保存名称",
               width=10, style="Primary.TButton", command=accept).pack(side=LEFT)
    dialog.bind("<Return>", accept)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.update_idletasks()
    virtual_x, virtual_y = parent.winfo_vrootx(), parent.winfo_vrooty()
    virtual_w, virtual_h = parent.winfo_vrootwidth(), parent.winfo_vrootheight()
    width = min(max(430, dialog.winfo_reqwidth() + 12), max(320, virtual_w - 48))
    height = min(max(245, dialog.winfo_reqheight() + 12), max(220, virtual_h - 72))
    dialog.minsize(min(width, dialog.winfo_reqwidth()), min(height, dialog.winfo_reqheight()))
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    x = max(virtual_x + 12, min(x, virtual_x + virtual_w - width - 12))
    y = max(virtual_y + 12, min(y, virtual_y + virtual_h - height - 12))
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.grab_set()
    entry.focus_set()
    entry.selection_range(0, END)
    parent.wait_window(dialog)
    return result


class WosFilterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.app_dir = get_app_dir()
        self.root.title(f"{APP_TITLE} {APP_VERSION}")
        icon_path = get_resource_dir() / "assets" / "app_icon.png"
        if icon_path.exists():
            try:
                self._app_icon = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, self._app_icon)
            except tk.TclError:
                pass
        self.root.minsize(1050, 680)
        self.root.geometry("1360x850")
        self.workspace_store = WorkspaceStore(Path(os.environ.get("MANNUL_WORKSPACE_ROOT", str(self.app_dir / "data" / "projects"))))
        self.shared_vocabulary = SharedVocabularyStore(self.workspace_store.root.parent / "shared_vocabulary.json")
        self.project_data = self.workspace_store.load(self.workspace_store.last_project_id())
        self.raw_records: list[WosRecord] = []
        self._normalized_cache_key: tuple | None = None
        self._normalized_cache_records: list[WosRecord] = []
        self._normalized_cache_report: dict = {}
        self._normalized_cache_doi_conflicts: list[dict[str, object]] = []
        self.project_ids: list[str] = []
        self.files: list[str] = []
        self.parsed: ParsedWos | None = None
        self.import_signature: tuple[tuple[str, int, int], ...] | None = None
        self.vocabulary_spec: dict = {"countries": [], "organizations": [], "authors": [],
                                      "journals": [], "keywords": []}
        self._pending_catalog_removals: dict[str, set[str]] = {}
        self.vocabulary_report: dict = {}
        self.doi_conflicts: list[dict[str, object]] = []
        self.records: list[WosRecord] = []
        self.duplicates: list[DuplicateEntry] = []
        self.results: dict[str, Classification] = {}
        self.year_start: int | None = None
        self.year_end: int | None = None
        self.year_excluded_count = 0
        self.year_unknown_count = 0
        self.provider_used = PROVIDER_HYBRID
        self.model_used = f"{JEV_MODEL} -> {DEEPSEEK_MODEL}"
        self.started_at = datetime.now()
        self.audit_log_path = self.app_dir / "data" / "audit_pending.jsonl"
        self.last_output: Path | None = None
        self.review_draft: set[str] = set()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.cancel_event = threading.Event()
        self.running = False
        self.drop_enabled = False
        self.literature_window: tk.Toplevel | None = None
        self.literature_tree: ttk.Treeview | None = None
        self._chart_dirty = True
        self._tab_refresh_timer: str | None = None
        self._build_ui()
        self.root.bind_all("<MouseWheel>", self._route_mouse_wheel, add="+")
        self.root.bind("<Configure>", self._resize_file_table)
        self._load_project(self.project_data["id"], loaded=self.project_data)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_events()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        self.root.configure(bg=ui.BG)
        style.configure("TFrame", background=ui.SURFACE)
        style.configure("Shell.TFrame", background=ui.BG)
        style.configure("Sidebar.TFrame", background=ui.SIDEBAR)
        style.configure("TLabel", background=ui.SURFACE, foreground=ui.TEXT, font=(ui.FONT, 10))
        style.configure("Sidebar.TLabel", background=ui.SIDEBAR, foreground=ui.TEXT)
        style.configure("Title.TLabel", font=(ui.FONT, 18, "bold"), foreground=ui.TEXT)
        style.configure("Hint.TLabel", foreground=ui.MUTED)
        style.configure("TLabelframe", background=ui.SURFACE, foreground=ui.TEXT,
                        bordercolor=ui.BORDER, relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=ui.SURFACE, foreground=ui.TEXT,
                        font=(ui.FONT, 10, "bold"))
        style.configure("TButton", font=(ui.FONT, 10), padding=(12, 7),
                        background=ui.SURFACE, foreground=ui.TEXT, bordercolor=ui.BORDER,
                        relief="flat", borderwidth=1, focuscolor=ui.ACCENT_SOFT)
        style.map("TButton", background=[("disabled", ui.BG), ("pressed", ui.ACCENT_SOFT),
                                         ("active", ui.ACCENT_SOFT)],
                  foreground=[("disabled", "#97A8AA")])
        style.configure("Primary.TButton", font=(ui.FONT, 10, "bold"),
                        background=ui.ACCENT, foreground=ui.SURFACE,
                        bordercolor=ui.ACCENT, padding=(13, 8))
        style.map("Primary.TButton", background=[("disabled", "#B7D5D1"),
                                                 ("pressed", ui.ACCENT_HOVER),
                                                 ("active", ui.ACCENT_HOVER)],
                  foreground=[("disabled", ui.SURFACE), ("active", ui.SURFACE)])
        style.configure("Sidebar.TButton", background=ui.SIDEBAR,
                        bordercolor=ui.SIDEBAR, foreground=ui.TEXT)
        style.map("Sidebar.TButton", background=[("active", ui.ACCENT_SOFT),
                                                 ("pressed", ui.ACCENT_SOFT)])
        style.configure("Danger.TButton", background=ui.SIDEBAR,
                        bordercolor=ui.SIDEBAR, foreground=ui.DANGER)
        style.map("Danger.TButton", background=[("active", "#FCEFED"),
                                                ("pressed", "#FCEFED")])
        style.configure("TNotebook", background=ui.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=ui.BG, foreground=ui.MUTED,
                        padding=(18, 9), font=(ui.FONT, 10))
        style.map("TNotebook.Tab", background=[("selected", ui.SURFACE),
                                               ("active", ui.ACCENT_SOFT)],
                  foreground=[("selected", ui.ACCENT_HOVER)],
                  font=[("selected", (ui.FONT, 11, "bold"))])
        style.configure("Main.TNotebook", background=ui.BG, borderwidth=0)
        style.configure("Main.TNotebook.Tab", background=ui.BG, foreground=ui.MUTED,
                        padding=(16, 9), font=(ui.FONT, 10))
        style.map("Main.TNotebook.Tab", background=[("selected", ui.SURFACE),
                                                    ("active", ui.ACCENT_SOFT)],
                  foreground=[("selected", ui.ACCENT_HOVER)],
                  padding=[("selected", (20, 11))],
                  font=[("selected", (ui.FONT, 11, "bold"))])
        # Flat, compact scrollbars without the legacy arrow buttons or grip.
        style.layout("Vertical.TScrollbar", [(
            "Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"sticky": "nswe"})]})])
        style.configure("Vertical.TScrollbar", background="#84C7C2", troughcolor="#EDF7F6",
                        bordercolor="#EDF7F6", lightcolor="#84C7C2", darkcolor="#84C7C2",
                        gripcount=0, sliderlength=32, width=11)
        style.map("Vertical.TScrollbar", background=[("active", "#54AAA4"), ("pressed", "#338D89")])
        style.layout("Horizontal.TScrollbar", [(
            "Horizontal.Scrollbar.trough", {"sticky": "we", "children": [
                ("Horizontal.Scrollbar.thumb", {"sticky": "nswe"})]})])
        style.configure("Horizontal.TScrollbar", background="#84C7C2", troughcolor="#EDF7F6",
                        bordercolor="#EDF7F6", lightcolor="#84C7C2", darkcolor="#84C7C2",
                        gripcount=0, sliderlength=32, width=11)
        style.configure("Treeview", background=ui.SURFACE, fieldbackground=ui.SURFACE,
                        foreground=ui.TEXT, rowheight=36, bordercolor=ui.BORDER,
                        font=(ui.FONT, 10))
        style.map("Treeview", background=[("selected", ui.ACCENT_SOFT)],
                  foreground=[("selected", ui.TEXT)])
        style.configure("Treeview.Heading", background=ui.BG, foreground=ui.TEXT,
                        relief="flat", font=(ui.FONT, 10, "bold"), padding=(8, 9))
        style.configure("TEntry", fieldbackground=ui.SURFACE, bordercolor=ui.BORDER,
                        padding=(8, 6), foreground=ui.TEXT)
        style.configure("TCombobox", fieldbackground=ui.SURFACE, bordercolor=ui.BORDER,
                        padding=(8, 6), foreground=ui.TEXT)
        style.configure("TProgressbar", background=ui.ACCENT, troughcolor=ui.ACCENT_SOFT)
        style.configure("TCheckbutton", background=ui.SURFACE, foreground=ui.TEXT,
                        font=(ui.FONT, 10))

        shell = ttk.Frame(self.root, style="Shell.TFrame")
        shell.pack(fill=BOTH, expand=True)
        sidebar = ttk.Frame(shell, padding=(17, 18), width=240, style="Sidebar.TFrame")
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="文献项目", style="Sidebar.TLabel",
                  font=(ui.FONT, 14, "bold")).pack(anchor=W, pady=(2, 15))
        self.project_list = tk.Listbox(sidebar, exportselection=False, font=(ui.FONT, 10),
                                       relief="flat", bg=ui.SIDEBAR, fg=ui.TEXT,
                                       selectbackground=ui.ACCENT_SOFT,
                                       selectforeground=ui.ACCENT_HOVER,
                                       highlightthickness=0, bd=0,
                                       activestyle="none")
        self.project_list.pack(fill=BOTH, expand=True)
        self.project_list.bind("<<ListboxSelect>>", self._on_project_selected)
        ttk.Button(sidebar, text="＋ 新建项目", style="Primary.TButton",
                   command=self.create_project).pack(fill=X, pady=(12, 8))
        project_actions = ttk.Frame(sidebar, style="Sidebar.TFrame")
        project_actions.pack(fill=X)
        ttk.Button(project_actions, text="重命名", style="Sidebar.TButton",
                   command=self.rename_project).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(project_actions, text="删除", style="Danger.TButton",
                   command=self.delete_project).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
        ttk.Label(sidebar, text="项目各自保存题录与去重结果",
                  style="Sidebar.TLabel", wraplength=190,
                  foreground=ui.MUTED).pack(anchor=W, pady=(14, 0))

        main = ttk.Frame(shell, padding=(17, 17, 18, 14))
        main.pack(side=LEFT, fill=BOTH, expand=True)
        self.project_title = ttk.Label(main, text="", style="Title.TLabel")
        self.project_title.pack(anchor=W)
        ttk.Label(
            main,
            text="拖入文件后自动识别来源并按题名排序；查找重复后逐组核查，再统一去重。",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(4, 12))
        self.tabs = ttk.Notebook(main, style="Main.TNotebook")
        workspace_tab = ttk.Frame(self.tabs)
        workspace_canvas = tk.Canvas(workspace_tab, bg="#FFFFFF", highlightthickness=0, bd=0)
        workspace_scroll = ttk.Scrollbar(workspace_tab, orient=VERTICAL, command=workspace_canvas.yview)
        workspace_canvas.configure(yscrollcommand=workspace_scroll.set)
        workspace_scroll.pack(side=RIGHT, fill=Y)
        workspace_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        workspace = ttk.Frame(workspace_canvas, padding=8)
        workspace_window = workspace_canvas.create_window((0, 0), window=workspace, anchor="nw")
        workspace.bind("<Configure>", lambda _event: workspace_canvas.configure(scrollregion=workspace_canvas.bbox("all")))
        workspace_canvas.bind("<Configure>", lambda event: workspace_canvas.itemconfigure(workspace_window, width=event.width))
        self.workspace_canvas = workspace_canvas
        screening_tab = ttk.Frame(self.tabs)
        screening_canvas = tk.Canvas(screening_tab, bg="#FFFFFF", highlightthickness=0, bd=0)
        screening_scroll = ttk.Scrollbar(screening_tab, orient=VERTICAL, command=screening_canvas.yview)
        screening_canvas.configure(yscrollcommand=screening_scroll.set)
        screening_scroll.pack(side=RIGHT, fill=Y)
        screening_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        outer = ttk.Frame(screening_canvas, padding=8)
        screening_window = screening_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: screening_canvas.configure(scrollregion=screening_canvas.bbox("all")))
        screening_canvas.bind("<Configure>", lambda event: screening_canvas.itemconfigure(screening_window, width=event.width))
        self.screening_canvas = screening_canvas
        self._page_scrollbars = {workspace_scroll: workspace_canvas, screening_scroll: screening_canvas}
        metrics = ttk.Frame(self.tabs, padding=8)
        vos_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(workspace_tab, text="文献工作区")
        self.tabs.add(screening_tab, text="筛选与导出")
        self.tabs.add(metrics, text="发文趋势与排名")
        self.tabs.add(vos_tab, text="VOS 图谱")
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.year_start_var = tk.StringVar()
        self.year_end_var = tk.StringVar()
        self.year_scope_var = tk.StringVar(value="年份：不限")

        ttk.Label(workspace, text="项目题录总览 · 全部年份",
                  font=("Microsoft YaHei UI", 10, "bold"), foreground="#28636B").pack(anchor=W, pady=(0, 6))
        metric_row = ttk.Frame(workspace)
        metric_row.pack(fill=X, pady=(0, 6))
        self.metric_values: dict[str, tk.StringVar] = {}
        for column, (key, title, detail, background, accent) in enumerate((
            ("imported", "导入成功", "已解析的完整题录", "#EEF8FA", "#247990"),
            ("removed", "去重移除", "已应用的重复题录", "#FFF5F0", "#A76145"),
            ("retained", "目前保留", "项目中尚未移除", "#EDF9F5", "#198375"),
        )):
            metric_row.columnconfigure(column, weight=1, uniform="metric")
            card = tk.Frame(metric_row, bg=background, highlightthickness=1,
                            highlightbackground="#D6E9E8", padx=15, pady=8)
            card.grid(row=0, column=column, sticky="ew", padx=(0, 7) if column < 2 else 0)
            tk.Label(card, text=title, bg=background, fg="#42676D",
                     font=("Microsoft YaHei UI", 9, "bold")).pack(anchor=W)
            value = tk.StringVar(value="0")
            self.metric_values[key] = value
            tk.Label(card, textvariable=value, bg=background, fg=accent,
                     font=("Microsoft YaHei UI", 24, "bold")).pack(anchor=W, pady=(1, 0))
            tk.Label(card, text=detail, bg=background, fg="#69868A",
                     font=("Microsoft YaHei UI", 8)).pack(anchor=W)
        ttk.Label(workspace, text="导入成功 = 去重移除 + 目前保留；只计入已应用的去重结果，年份筛选不改变项目总量。",
                  style="Hint.TLabel").pack(anchor=W, pady=(0, 9))

        self.file_box = ttk.LabelFrame(workspace, text="题录文件清单", padding=8)
        self.file_box.pack(fill=X)
        file_toolbar = ttk.Frame(self.file_box)
        file_toolbar.pack(fill=X)
        ttk.Label(file_toolbar, text="↓ 将题录文件或文件夹拖到下方清单",
                  style="Hint.TLabel").pack(anchor=W, pady=(0, 9))
        file_actions = ttk.Frame(file_toolbar)
        file_actions.pack(fill=X)
        ttk.Button(file_actions, text="选择文件…", style="Primary.TButton",
                   command=self.choose_files).pack(side=LEFT, padx=(0, 7))
        ttk.Button(file_actions, text="仅转换格式并合并…",
                   command=lambda: PlainMergeWindow(self)).pack(side=LEFT)
        file_review_actions = ttk.Frame(file_toolbar)
        file_review_actions.pack(fill=X, pady=(7, 0))
        ttk.Button(file_review_actions, text="查看题录列表…",
                   command=self.open_literature_list).pack(side=LEFT, padx=(0, 7))
        ttk.Button(file_review_actions, text="查看导入预览",
                   command=self.preview_import).pack(side=LEFT)
        ttk.Button(file_review_actions, text="清空项目题录", style="Danger.TButton",
                   command=self.clear_files).pack(side=RIGHT)
        file_table_frame = ttk.Frame(self.file_box)
        file_table_frame.pack(fill=X, pady=(7, 0))
        self.file_tree = ttk.Treeview(file_table_frame,
            columns=("number", "name", "source", "success", "failed", "status"),
            show="headings", height=3, selectmode="browse")
        for col, title, width, anchor in (
            ("number", "序号", 48, "center"), ("name", "文件名", 360, "w"),
            ("source", "识别来源", 130, "center"), ("success", "成功", 65, "center"),
            ("failed", "失败", 65, "center"), ("status", "导入状态", 105, "center"),
        ):
            self.file_tree.heading(col, text=title)
            self.file_tree.column(col, width=width, minwidth=48, anchor=anchor)
        self.file_tree.tag_configure("error", foreground="#B42318")
        self.file_tree.tag_configure("partial", foreground="#A15C00")
        file_scroll = ttk.Scrollbar(file_table_frame, orient=VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_scroll.set)
        file_scroll.pack(side=RIGHT, fill=Y)
        self.file_tree.pack(fill=X, expand=True)
        self.file_tree.bind("<Double-1>", self._show_file_detail)
        self.file_notice_var = tk.StringVar(value="下方会逐项显示已接收文件的来源、题录数与导入状态；双击可核对详情。")
        ttk.Label(self.file_box, textvariable=self.file_notice_var, style="Hint.TLabel").pack(anchor=W, pady=(4, 0))
        self._enable_file_drop()

        import_options = ttk.Frame(self.file_box)
        import_options.pack(fill=X, pady=(8, 0))
        self.auto_keyword_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(import_options, text="自动合并英文关键词单复数及空格/连字符变体",
                        variable=self.auto_keyword_var, command=self._invalidate_import).pack(side=LEFT)
        ttk.Button(import_options, text="加载词汇规则 JSON…", command=self.choose_vocabulary_rules).pack(side=LEFT, padx=10)
        self.import_summary_var = tk.StringVar(value="尚未导入；支持 TXT、RIS、BIB、CSV、XLS、NBIB。")
        ttk.Label(self.file_box, textvariable=self.import_summary_var,
                  style="Hint.TLabel").pack(anchor=W, pady=(6, 0))

        stat_box = ttk.LabelFrame(workspace, text="各数据库导入情况", padding=(12, 15))
        stat_box.pack(fill=X, pady=(16, 0))
        stat_cols = ("source", "uploaded", "success", "failed", "failed_files", "dedupe")
        source_table_frame = ttk.Frame(stat_box)
        source_table_frame.pack(fill=X)
        self.source_tree = ttk.Treeview(source_table_frame, columns=stat_cols, show="headings", height=4)
        for col, title, width in (("source", "数据库", 170), ("uploaded", "上传题录", 110),
                                  ("success", "成功题录", 110), ("failed", "失败题录", 110),
                                  ("failed_files", "失败文件", 100), ("dedupe", "去重情况", 185)):
            self.source_tree.heading(col, text=title)
            self.source_tree.column(col, width=width, anchor="center")
        source_scroll = ttk.Scrollbar(source_table_frame, orient=VERTICAL, command=self.source_tree.yview)
        self.source_tree.configure(yscrollcommand=source_scroll.set)
        source_scroll.pack(side=RIGHT, fill=Y)
        self.source_tree.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(stat_box, text="无法读取的文件无法确定题录数，计入“失败文件”；PMID 编号列表不算完整题录。",
                  style="Hint.TLabel").pack(anchor=W, pady=(5, 0))

        year_limit = ttk.Frame(workspace)
        year_limit.pack(fill=X, pady=(10, 0))
        ttk.Label(year_limit, text="年份范围：").pack(side=LEFT)
        ttk.Label(year_limit, text="起始").pack(side=LEFT)
        ttk.Entry(year_limit, width=8, justify="center", textvariable=self.year_start_var).pack(side=LEFT, padx=(4, 8))
        ttk.Label(year_limit, text="截止").pack(side=LEFT)
        ttk.Entry(year_limit, width=8, justify="center", textvariable=self.year_end_var).pack(side=LEFT, padx=(4, 8))
        ttk.Button(year_limit, text="应用年份", command=self.apply_year_scope).pack(side=LEFT)
        ttk.Label(year_limit, textvariable=self.year_scope_var, style="Hint.TLabel").pack(side=LEFT, padx=12)

        dedupe_bar = ttk.Frame(workspace)
        dedupe_bar.pack(fill=X, pady=(12, 6))
        ttk.Label(dedupe_bar, text="去重模式：").pack(side=LEFT)
        self.dedupe_mode_var = tk.StringVar(value="严格")
        self.dedupe_mode_combo = ttk.Combobox(dedupe_bar, textvariable=self.dedupe_mode_var,
                                              values=("严格", "宽松", "期刊", "快速"), state="readonly", width=8)
        self.dedupe_mode_combo.pack(side=LEFT)
        self.find_dedupe_button = ttk.Button(dedupe_bar, text="查找重复", command=self.run_dedupe)
        self.find_dedupe_button.pack(side=LEFT, padx=(8, 4))
        self.one_click_dedupe_button = ttk.Button(dedupe_bar, text="一键去重", command=self.one_click_dedupe)
        self.one_click_dedupe_button.pack(side=LEFT, padx=4)
        ttk.Button(dedupe_bar, text="核查重复组", command=self.open_dedupe_review).pack(side=LEFT, padx=4)
        ttk.Button(dedupe_bar, text="去重历史", command=self.show_dedupe_history).pack(side=LEFT, padx=4)
        self.apply_dedupe_button = ttk.Button(dedupe_bar, text="统一去重", command=self.apply_dedupe)
        self.apply_dedupe_button.pack(side=LEFT, padx=4)
        ttk.Button(dedupe_bar, text="导出当前题录…", command=self.export_basic).pack(side=RIGHT)
        ttk.Button(dedupe_bar, text="查看发文分析", command=lambda: self.tabs.select(2)).pack(side=RIGHT, padx=6)
        self.dedupe_summary_var = tk.StringVar(value="尚未运行去重。")
        ttk.Label(workspace, textvariable=self.dedupe_summary_var, style="Hint.TLabel").pack(anchor=W)

        self._enable_workspace_drop(workspace)

        query_box = ttk.LabelFrame(outer, text="2. 原始检索式（唯一主题判断依据）", padding=10)
        query_box.pack(fill=X, pady=(12, 0))
        self.query_text = tk.Text(query_box, height=5, wrap="word", font=("Consolas", 10), undo=True)
        self.query_text.pack(fill=X)
        ttk.Label(
            query_box,
            text="示例：TS=((artificial intelligence OR machine learning) AND education) NOT TS=(medical education)",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(6, 0))
        year_row = ttk.Frame(query_box)
        year_row.pack(fill=X, pady=(8, 0))
        ttk.Label(year_row, text="发文年份（可选）：").pack(side=LEFT)
        ttk.Label(year_row, text="起始").pack(side=LEFT)
        ttk.Entry(year_row, width=8, justify="center", textvariable=self.year_start_var).pack(side=LEFT, padx=(4, 8))
        ttk.Label(year_row, text="截止").pack(side=LEFT)
        ttk.Entry(year_row, width=8, justify="center", textvariable=self.year_end_var).pack(side=LEFT, padx=(4, 10))
        ttk.Label(year_row, text="可只填一侧；年份缺失的记录会保留进入筛选", style="Hint.TLabel").pack(side=LEFT)

        key_box = ttk.LabelFrame(outer, text="3. API 与运行", padding=10)
        key_box.pack(fill=X, pady=(12, 0))
        key_row = ttk.Frame(key_box)
        key_row.pack(fill=X)
        self.key_label = ttk.Label(key_row, text="")
        self.key_label.pack(side=LEFT, fill=X, expand=True)
        ttk.Button(key_row, text="打开 API Key 文件", command=self.open_key_file).pack(side=RIGHT)

        settings = ttk.Frame(key_box)
        settings.pack(fill=X, pady=(9, 0))
        ttk.Label(settings, text="AI 后端：").pack(side=LEFT)
        self.provider_var = tk.StringVar(value=PROVIDER_HYBRID)
        self.provider_combo = ttk.Combobox(
            settings,
            textvariable=self.provider_var,
            values=(PROVIDER_HYBRID, PROVIDER_JEV, PROVIDER_DEEPSEEK),
            state="readonly",
            width=20,
        )
        self.provider_combo.pack(side=LEFT)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        ttk.Label(settings, text="  并发数：").pack(side=LEFT)
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        ttk.Spinbox(settings, from_=1, to=MAX_WORKERS, width=5, textvariable=self.workers_var).pack(side=LEFT)
        ttk.Label(settings, text="  请求超时（秒）：").pack(side=LEFT)
        self.timeout_var = tk.IntVar(value=300)
        ttk.Spinbox(settings, from_=60, to=900, increment=30, width=7, textvariable=self.timeout_var).pack(side=LEFT)
        self.model_label = ttk.Label(settings, text="", style="Hint.TLabel")
        self.model_label.pack(side=LEFT, padx=10)
        self._on_provider_changed()

        action_row = ttk.Frame(outer)
        action_row.pack(fill=X, pady=(14, 0))
        self.start_button = ttk.Button(action_row, text="开始分析", style="Primary.TButton", command=self.start_analysis)
        self.start_button.pack(side=LEFT)
        self.pause_button = ttk.Button(action_row, text="暂停", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side=LEFT, padx=8)
        self.cancel_button = ttk.Button(action_row, text="取消", command=self.cancel_analysis, state="disabled")
        self.cancel_button.pack(side=LEFT)
        self.review_button = ttk.Button(action_row, text="打开存疑复核", command=self.open_review, state="disabled")
        self.review_button.pack(side=LEFT, padx=(20, 8))
        self.output_button = ttk.Button(action_row, text="打开输出文件夹", command=self.open_output, state="disabled")
        self.output_button.pack(side=LEFT)
        ttk.Button(action_row, text="查看发文分析", command=lambda: self.tabs.select(2)).pack(side=RIGHT)
        self.ai_history_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.ai_history_var, style="Hint.TLabel").pack(anchor=W, pady=(6, 0))

        progress_row = ttk.Frame(outer)
        progress_row.pack(fill=X, pady=(12, 0))
        self.progress = ttk.Progressbar(progress_row, mode="determinate")
        self.progress.pack(side=LEFT, fill=X, expand=True)
        self.progress_label = ttk.Label(progress_row, text="0 / 0", width=14, anchor="e")
        self.progress_label.pack(side=RIGHT, padx=(10, 0))
        self.count_label = ttk.Label(outer, text="相关 0 · 存疑 0 · 不相关 0 · 失败 0", style="Hint.TLabel")
        self.count_label.pack(anchor=W, pady=(5, 0))

        self.plot_workbench = PlotWorkbench(metrics, self)
        self.plot_workbench.pack(fill=BOTH, expand=True)
        self.metrics_scope_var = self.plot_workbench.scope_var
        self.vos_panel = VOSPanel(vos_tab, self)
        self.vos_panel.pack(fill=BOTH, expand=True)

        self.log_box = ttk.LabelFrame(main, text="运行日志", padding=8)
        self.log_box.pack(side=tk.BOTTOM, fill=X, pady=(8, 0))
        self.log_text = tk.Text(self.log_box, height=3, state="disabled", wrap="word", font=("Microsoft YaHei UI", 9))
        scrollbar = ttk.Scrollbar(self.log_box, orient=VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text.pack(side=LEFT, fill=X, expand=True)
        self.tabs.pack(fill=BOTH, expand=True)

    def _route_mouse_wheel(self, event: tk.Event) -> str | None:
        """Scroll the page beneath the pointer, including its child controls."""
        delta = getattr(event, "delta", 0)
        if not delta:
            return None
        units = -max(1, round(abs(delta) / 120)) if delta > 0 else max(1, round(abs(delta) / 120))
        widget = event.widget
        target = self._page_scrollbars.get(widget)
        if isinstance(widget, (ttk.Treeview, tk.Listbox, tk.Text)):
            first, last = widget.yview()
            if (units < 0 and first > 0) or (units > 0 and last < 1):
                return None  # Let the widget's own class binding scroll its content.
        if target is None:
            current = widget
            while current is not None:
                if current in (self.workspace_canvas, self.screening_canvas):
                    target = current
                    break
                current = getattr(current, "master", None)
        if target is not None:
            target.yview_scroll(units, "units")
            return "break"
        return None

    def choose_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择文献题录文件",
            filetypes=[("文献题录", "*.txt *.ris *.bib *.csv *.xls *.nbib"), ("所有文件", "*.*")],
        )
        self._add_file_inputs(paths, source="选择")

    def _resize_file_table(self, event: tk.Event) -> None:
        if event.widget is self.root:
            rows = min(8, max(3, 3 + (event.height - 680) // 150))
            if int(self.file_tree.cget("height")) != rows:
                self.file_tree.configure(height=rows)

    def _refresh_file_table(self) -> None:
        self.file_tree.delete(*self.file_tree.get_children())
        results = {item["path"]: item for item in self.project_data["file_results"]}
        for index, path in enumerate(self.files, 1):
            item = results.get(path)
            if item is None:
                values = (index, Path(path).name, "识别中", "—", "—", "待处理")
                tag = ""
            else:
                failed = item.get("failed")
                if item.get("failed_files"):
                    status, tag = "导入失败", "error"
                elif item.get("format") == "pmid_list":
                    status, tag = "仅 PMID", "partial"
                elif not item.get("success"):
                    status, tag = "无完整题录", "partial"
                elif failed:
                    status, tag = "部分失败", "partial"
                else:
                    status, tag = "已导入", ""
                values = (index, Path(path).name, SOURCE_LABELS.get(item["source"], item["source"]),
                          item.get("success", 0), "—" if failed is None else failed, status)
            self.file_tree.insert("", END, iid=str(index), values=values, tags=(tag,) if tag else ())

    def _show_file_detail(self, _event: tk.Event | None = None) -> None:
        selection = self.file_tree.selection()
        if not selection:
            return
        path = self.files[int(selection[0]) - 1]
        item = next((entry for entry in self.project_data["file_results"] if entry["path"] == path), None)
        if item is None:
            detail = "尚未完成识别。"
        else:
            detail = (f"来源：{SOURCE_LABELS.get(item['source'], item['source'])}\n"
                      f"格式：{item.get('format') or '未识别'}\n"
                      f"上传题录：{item.get('uploaded') if item.get('uploaded') is not None else '未知'}\n"
                      f"成功：{item.get('success', 0)}\n"
                      f"失败：{item.get('failed') if item.get('failed') is not None else '未知'}")
            if item.get("error"):
                detail += f"\n错误：{item['error']}"
        messagebox.showinfo("题录文件详情", f"文件：{Path(path).name}\n路径：{path}\n\n{detail}", parent=self.root)

    def choose_vocabulary_rules(self) -> None:
        path = filedialog.askopenfilename(title="选择词汇规则 JSON", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            spec = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(spec, dict):
                raise ValueError("词汇规则必须是 JSON 对象")
        except (OSError, ValueError) as exc:
            messagebox.showerror("词汇规则无法读取", str(exc), parent=self.root)
            return
        if self.apply_vocabulary_spec(spec):
            self._log(f"已加载词汇规则：{Path(path).name}。")

    def _invalidate_import(self) -> None:
        if self.running or not self._commit_vocabulary_spec(self.vocabulary_spec):
            self.auto_keyword_var.set(self.project_data.get("auto_keyword", True))

    def preview_import(self, show_window: bool = True) -> bool:
        """Refresh pending files and local vocabulary; never remove records here."""
        if not self.files:
            messagebox.showwarning("缺少文件", "请先选择至少一个题录文件。", parent=self.root)
            return False
        imported = {item["path"] for item in self.project_data["file_results"]}
        pending = [path for path in self.files if path not in imported]
        if pending:
            self._import_file_paths(pending)
        if not self.raw_records:
            messagebox.showwarning("没有完整题录", "文件已识别，但未读到可筛选的完整题录。请检查导入统计和日志。", parent=self.root)
            return False
        if show_window:
            self._show_import_preview()
        return True

    def _show_import_preview(self) -> None:
        if self.parsed is None:
            return
        ImportPreviewWindow(self)

    def apply_vocabulary_spec(self, spec: dict, *, parent: tk.Widget | None = None) -> bool:
        """Apply edited terms while retaining completed reviews and AI history."""
        return self._commit_vocabulary_spec(spec, parent=parent)

    def apply_ai_vocabulary_spec(self, spec: dict, kind: str, *,
                                 parent: tk.Widget | None = None,
                                 refresh: bool = True) -> tuple[bool, bool]:
        """Persist reviewed AI terms with the same selective invalidation."""
        try:
            start, end = self._current_year_scope()
            if not self._dedupe_ready_for_scope(start, end):
                raise ValueError("当前题录状态已变化，请先完成基础去重后再保存建议。")
        except ValueError as exc:
            messagebox.showerror("保存 DeepSeek 合并失败", str(exc), parent=parent or self.root)
            return False, False
        saved = self._commit_vocabulary_spec(spec, parent=parent, refresh=refresh)
        return saved, bool(saved and self.project_data.get("needs_dedupe"))

    def _commit_vocabulary_spec(self, spec: dict, *, parent: tk.Widget | None = None,
                                refresh: bool = True) -> bool:
        """Save rules without deleting reviewed groups, exclusions, or AI output."""
        if self.running:
            return False
        try:
            normalized, new_report = merge_vocabulary(
                self.raw_records, {**spec, "autoKeywordVariants": self.auto_keyword_var.get()})
            before = self.parsed.records if self.parsed is not None else []
            if not before and self.raw_records:
                before, _ = merge_vocabulary(self.raw_records, {
                    **self.vocabulary_spec,
                    "autoKeywordVariants": self.project_data.get("auto_keyword", True),
                })
            runs = self.project_data.get("runs", [])
            saved_ai = self.project_data.get("ai_results", {})
            changed_dedupe, changed_ai = vocabulary_impact(before, normalized, runs, set(saved_ai))
            if runs and not runs[-1]["applied"] and changed_dedupe:
                raise ValueError("当前有待核查的重复组，且新规则改变了去重匹配字段。请先完成本轮核查。")
            removed_catalog_variants = {}
            for kind in SHARED_KINDS:
                previous = {_key(row["variant"]) for row in list_merge_pairs(
                    self.raw_records, self.vocabulary_spec, self.vocabulary_report, kind)}
                current = {_key(row["variant"]) for row in list_merge_pairs(
                    self.raw_records, spec, new_report, kind)}
                removed_catalog_variants[kind] = previous - current
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("合并规则无法应用", str(exc), parent=parent or self.root)
            return False
        fields = ("vocabulary_spec", "auto_keyword", "runs", "needs_dedupe",
                  "import_generation", "ai_import_generation", "ai_results_stale", "last_vos_path")
        old = {field: deepcopy(self.project_data[field]) for field in fields if field in self.project_data}
        previous_spec, previous_results = self.vocabulary_spec, self.results
        generation = self.project_data.get("import_generation", 0) + 1
        ai_current = bool(saved_ai and self.project_data.get("ai_import_generation") == generation - 1
                          and set(saved_ai) == {record.record_id for record in self.records}
                          and self.project_data.get("ai_year_start", "") == self.year_start_var.get()
                          and self.project_data.get("ai_year_end", "") == self.year_end_var.get())
        self.vocabulary_spec = deepcopy(spec)
        self.project_data.update(vocabulary_spec=self.vocabulary_spec,
                                 auto_keyword=self.auto_keyword_var.get(),
                                 import_generation=generation, last_vos_path="")
        if runs:
            if changed_dedupe:
                self.project_data["needs_dedupe"] = True
            else:
                runs[-1]["import_generation"] = generation
        if ai_current:
            self.project_data["ai_import_generation"] = generation
            self.project_data["ai_results_stale"] = bool(
                self.project_data.get("ai_results_stale") or changed_ai or changed_dedupe)
        elif saved_ai:
            self.project_data["ai_results_stale"] = True
        if self.project_data.get("ai_results_stale") or changed_dedupe:
            self.results = {}
            self.metrics_scope_var.set("基础去重结果")
            self.vos_panel.scope_var.set("基础去重结果")
        self._refresh_ai_history_notice()
        try:
            self.workspace_store.save(self.project_data)
        except OSError as exc:
            for field in fields:
                if field in old:
                    self.project_data[field] = old[field]
                else:
                    self.project_data.pop(field, None)
            self.vocabulary_spec, self.results = previous_spec, previous_results
            messagebox.showerror("保存合并规则失败", str(exc), parent=parent or self.root)
            return False
        for kind, variants in removed_catalog_variants.items():
            self._pending_catalog_removals.setdefault(kind, set()).update(variants)
        if refresh:
            self._refresh_import_state()
            self._remember_merge_catalog()
            self.vos_panel.refresh(quiet=True)
        self._log("合并规则已保存；去重核查和 AI 历史已保留。" +
                  (" 匹配字段已变化，请重新核查去重。" if changed_dedupe else "") +
                  (" AI 结果已标记需重新分析。" if self.project_data.get("ai_results_stale") else ""))
        return True

    def _remember_merge_catalog(self) -> None:
        if not self.raw_records:
            return
        pairs = {kind: list_merge_pairs(self.raw_records, self.vocabulary_spec,
                                        self.vocabulary_report, kind) for kind in SHARED_KINDS}
        try:
            count = self.shared_vocabulary.remember(
                self.raw_records, pairs, removed_variants=self._pending_catalog_removals)
        except (OSError, ValueError, KeyError) as exc:
            self._log(f"共享合并目录保存失败：{exc}")
        else:
            self._pending_catalog_removals.clear()
            self._log(f"国家、机构、作者、期刊共享合并目录已自动保存：{count} 条；关键词仅留在当前项目。")

    def _enable_file_drop(self) -> None:
        if DND_FILES is None:
            return
        try:
            self.file_tree.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.file_tree.dnd_bind("<<Drop>>", self._on_files_dropped)  # type: ignore[attr-defined]
        except (AttributeError, tk.TclError, OSError):
            return
        self.drop_enabled = True

    def _enable_workspace_drop(self, workspace: ttk.Frame) -> None:
        if DND_FILES is None:
            return
        queue = [workspace]
        while queue:
            widget = queue.pop()
            queue.extend(widget.winfo_children())
            try:
                widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                widget.dnd_bind("<<Drop>>", self._on_files_dropped)  # type: ignore[attr-defined]
            except (AttributeError, tk.TclError, OSError):
                pass

    def _on_files_dropped(self, event: tk.Event) -> str | None:
        if self.running:
            self._log("任务运行中，暂不接受新的拖入文件。")
            return getattr(event, "action", None)
        raw_data = getattr(event, "data", "")
        try:
            paths = self.root.tk.splitlist(raw_data)
        except tk.TclError:
            paths = (raw_data,) if raw_data else ()
        self._add_file_inputs(paths, source="拖入")
        return getattr(event, "action", None)

    def _add_file_inputs(self, inputs: tuple[str, ...] | list[str], *, source: str) -> None:
        expanded, warnings = expand_bibliography_inputs(inputs)
        existing = {normalized_path_key(path) for path in self.files}
        new_files = [path for path in expanded if normalized_path_key(path) not in existing]
        if new_files and self.project_data["runs"] and not self.project_data["runs"][-1]["applied"]:
            if not messagebox.askyesno("当前去重尚未完成", "新增文件会使当前候选失效。是否放弃本轮未完成的核查并导入？", parent=self.root):
                return
            self.project_data["runs"].pop()
            self._log("新增文件后，已取消未完成的去重候选。")
        added = 0
        duplicates = 0
        added_paths: list[str] = []
        for path in expanded:
            key = normalized_path_key(path)
            if key in existing:
                duplicates += 1
                continue
            existing.add(key)
            self.files.append(path)
            added_paths.append(path)
            added += 1
        if added:
            self.project_data["files"] = list(self.files)
            self.workspace_store.save(self.project_data)
            self._import_file_paths(added_paths)
        if inputs:
            details = [f"{source}后新增 {added} 个题录文件"]
            if duplicates:
                details.append(f"忽略重复 {duplicates} 个")
            if warnings:
                details.append(f"忽略无效路径 {len(warnings)} 个")
            self._log("；".join(details) + "。")
            if warnings or duplicates:
                self.file_notice_var.set(
                    f"⚠ 本次有 {duplicates} 个重复文件、{len(warnings)} 个无效路径未加入；请核对清单和下方日志。"
                )
            else:
                self.file_notice_var.set("可逐行核对文件名、来源、成功/失败题录与导入状态；双击查看完整路径。")
        for warning in warnings:
            self._log("提示：" + warning)

    def clear_files(self) -> None:
        if self.running:
            return
        if self.files and not messagebox.askyesno("清空项目题录", "将清空当前项目已导入的文件、题录和去重结果。确定继续？", parent=self.root):
            return
        self.files.clear()
        self.file_tree.delete(*self.file_tree.get_children())
        self.file_notice_var.set("下方会逐项显示已接收文件的来源、题录数与导入状态；双击可核对详情。")
        self.raw_records = []
        self.project_data.update(files=[], file_results=[], records=[], warnings=[], excluded_keys=[], runs=[],
                                 needs_dedupe=True, import_generation=0, ai_results={})
        self.results = {}
        self.workspace_store.save(self.project_data)
        self._invalidate_import()

    def _import_file_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        for path in paths:
            try:
                imported, summary, notes = import_one(path)
                valid = [record for record in imported if record.title.strip()]
                bad = len(imported) - len(valid)
                if summary.format == "pmid_list":
                    count = sum(bool(line.strip()) for line in Path(path).read_text(encoding="utf-8-sig").splitlines())
                    bad = count
                else:
                    count = len(imported)
                self.raw_records.extend(valid)
                self.project_data["file_results"].append({
                    "path": path, "source": summary.source, "format": summary.format,
                    "uploaded": count, "success": len(valid), "failed": bad, "failed_files": 0,
                })
                self.project_data["warnings"].extend(notes)
                self._log(f"识别 {Path(path).name}：{summary.source} / {summary.format}，成功 {len(valid)} 条，失败 {bad} 条。")
                for note in notes:
                    self._log("提示：" + note)
            except (OSError, ValueError, ImportFormatError) as exc:
                self.project_data["file_results"].append({
                    "path": path, "source": "未识别", "format": "", "uploaded": None,
                    "success": 0, "failed": None, "failed_files": 1, "error": str(exc),
                })
                self._log(f"导入失败 {Path(path).name}：{exc}")
        self.project_data["records"] = [record_to_json(record) for record in self.raw_records]
        try:
            inherited = self.shared_vocabulary.overlay(self.vocabulary_spec)
            merge_vocabulary(self.raw_records, {
                **inherited, "autoKeywordVariants": self.auto_keyword_var.get()})
            self.vocabulary_spec = inherited
            self.project_data["vocabulary_spec"] = inherited
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._log(f"本次未载入最新共享合并目录，仍使用项目现有规则：{exc}")
        self.project_data["import_generation"] = self.project_data.get("import_generation", 0) + 1
        self.project_data["needs_dedupe"] = True
        if self.project_data.get("ai_results"):
            self.project_data["ai_results_stale"] = True
        self.results = {}
        self.workspace_store.save(self.project_data)
        self._refresh_import_state()
        self._remember_merge_catalog()

    def _refresh_import_state(self, *, refresh_chart: bool = True) -> None:
        self._refresh_file_table()
        if not self.raw_records:
            self._normalized_cache_key = None
            self.parsed = None
            self.records = []
            self.duplicates = []
            self.vocabulary_report = {}
            self.doi_conflicts = []
            self.import_summary_var.set("尚无完整题录；拖入 TXT、RIS、BIB、CSV、XLS 或 NBIB 文件。")
        else:
            key = (self.project_data["id"], self.project_data.get("import_generation", 0),
                   self.auto_keyword_var.get(), len(self.raw_records))
            if key != self._normalized_cache_key:
                spec = {**self.vocabulary_spec, "autoKeywordVariants": self.auto_keyword_var.get()}
                normalized, report = merge_vocabulary(self.raw_records, spec)
                for record in normalized:
                    record.internal_id = record_key(record)
                self._normalized_cache_records = normalized
                self._normalized_cache_report = report
                self._normalized_cache_doi_conflicts = find_doi_conflicts(normalized)
                self._normalized_cache_key = key
            normalized = self._normalized_cache_records
            self.vocabulary_report = self._normalized_cache_report
            self.doi_conflicts = self._normalized_cache_doi_conflicts
            summaries = [ImportFileSummary(item["path"], item["source"], item["format"], item["success"])
                         for item in self.project_data["file_results"] if not item.get("failed_files")]
            self.parsed = ParsedWos(records=normalized, header="FN Clarivate Web of Science\nVR 1.0",
                                    warnings=list(self.project_data["warnings"]), imports=summaries)
            self.records = active_records(self.project_data, normalized)
            self.duplicates = duplicate_entries(self.project_data, normalized)
            self.import_summary_var.set(
                f"关键词自动归并 {len(self.vocabulary_report.get('automatic_keyword_groups', []))} 组；"
                "上方数字按项目全部年份统计。"
            )
            try:
                start, end = self._current_year_scope()
                ready = self._dedupe_ready_for_scope(start, end)
            except ValueError:
                ready = False
            if not ready:
                if self.plot_workbench.scope_var.get() == DEDUPE_SCOPE:
                    self.plot_workbench.scope_var.set(RAW_SCOPE)
                if self.vos_panel.scope_var.get() == DEDUPE_SCOPE:
                    self.vos_panel.scope_var.set(RAW_SCOPE)
        for key, count in (("imported", len(self.raw_records)),
                           ("removed", len(self.duplicates)),
                           ("retained", len(self.records))):
            self.metric_values[key].set(f"{count:,}")
        self._refresh_source_stats()
        self._refresh_literature_list()
        self._refresh_dedupe_summary()
        self._refresh_ai_history_notice()
        if refresh_chart:
            self._refresh_bibliometrics()

    def _on_tab_changed(self, _event: tk.Event | None = None) -> None:
        tab = self.tabs.index(self.tabs.select())
        if self._tab_refresh_timer is not None:
            self.root.after_cancel(self._tab_refresh_timer)
            self._tab_refresh_timer = None
        if tab in (2, 3):
            self.log_box.pack_forget()
            if tab == 2 and self._chart_dirty:
                self._tab_refresh_timer = self.root.after(40, self._refresh_visible_tab)
            elif tab == 3:
                self._tab_refresh_timer = self.root.after(40, self._refresh_visible_tab)
        elif not self.log_box.winfo_manager():
            self.log_box.pack(side=tk.BOTTOM, fill=X, pady=(8, 0), before=self.tabs)

    def _refresh_visible_tab(self) -> None:
        self._tab_refresh_timer = None
        tab = self.tabs.index(self.tabs.select())
        if tab == 2 and self._chart_dirty:
            self._refresh_bibliometrics()
        elif tab == 3:
            self.vos_panel.refresh(quiet=True)

    def _chart_settings(self) -> dict:
        return self.plot_workbench.settings()

    def _refresh_bibliometrics(self, _event: tk.Event | None = None) -> None:
        if self.tabs.index(self.tabs.select()) != 2:
            self._chart_dirty = True
            return
        self.plot_workbench.refresh()
        self._chart_dirty = False

    def _refresh_source_stats(self) -> None:
        self.source_tree.delete(*self.source_tree.get_children())
        counts: dict[str, dict[str, int]] = {}
        for item in self.project_data["file_results"]:
            source = item["source"]
            data = counts.setdefault(source, {"uploaded": 0, "success": 0, "failed": 0, "failed_files": 0})
            for field in data:
                data[field] += item.get(field) or 0
        deduped: Counter[str] = Counter()
        retained: Counter[str] = Counter()
        if self.parsed is not None:
            excluded = set(self.project_data["excluded_keys"])
            for record in self.parsed.records:
                (deduped if record_key(record) in excluded else retained)[record.source_kind] += 1
        for source, values in sorted(counts.items()):
            self.source_tree.insert("", END, values=(SOURCE_LABELS.get(source, source), values["uploaded"],
                                                     values["success"], values["failed"], values["failed_files"],
                                                     f"去重 {deduped[source]} · 保留 {retained[source]}"))

    def _refresh_literature_list(self) -> None:
        try:
            start, end = parse_year_range(self.year_start_var.get(), self.year_end_var.get())
            self.year_scope_var.set(f"年份：{format_year_range(start, end)}；年份缺失者保留")
        except ValueError:
            start = end = None
            self.year_scope_var.set("年份格式无效，请输入四位年份并点击“应用年份”")
        if self.literature_tree is None or not self.literature_tree.winfo_exists():
            return
        if self.literature_window is not None:
            self.literature_window.title(f"{self.project_data['name']} · 题录列表（按题名排序）")
        self.literature_tree.delete(*self.literature_tree.get_children())
        if self.parsed is None:
            return
        excluded = set(self.project_data["excluded_keys"])
        visible, _, _ = partition_records_by_year(self.parsed.records, start, end)
        for record in sorted(visible, key=lambda item: (self._title_sort_key(item.title), record_key(item))):
            key = record_key(record)
            self.literature_tree.insert("", END, iid=key, values=(
                "已去重" if key in excluded else "保留", record.title, record.text("PY"),
                record.source_kind, record.text("SO"), record.text("DI"),
            ))

    def open_literature_list(self) -> None:
        if self.literature_window is not None and self.literature_window.winfo_exists():
            self.literature_window.lift()
            self.literature_window.focus_force()
            return
        window = tk.Toplevel(self.root)
        window.title(f"{self.project_data['name']} · 题录列表（按题名排序）")
        window.geometry("1120x650")
        window.minsize(760, 420)
        self.literature_window = window
        ttk.Label(window, text="双击题录可查看详情；状态、年份和来源随当前项目及年份范围更新。",
                  padding=(10, 8)).pack(fill=X)
        frame = ttk.Frame(window, padding=(10, 0, 10, 10))
        frame.pack(fill=BOTH, expand=True)
        cols = ("status", "title", "year", "source", "journal", "doi")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, title, width in (("status", "状态", 70), ("title", "题名", 430), ("year", "年份", 65),
                                  ("source", "数据库", 90), ("journal", "期刊", 180), ("doi", "DOI", 150)):
            tree.heading(col, text=title)
            tree.column(col, width=width, minwidth=55, anchor=W)
        scroll = ttk.Scrollbar(frame, orient=VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree.bind("<Double-1>", self.show_literature_detail)
        self.literature_tree = tree
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_literature_list())
        self._refresh_literature_list()

    def _close_literature_list(self) -> None:
        if self.literature_window is not None:
            self.literature_window.destroy()
        self.literature_window = None
        self.literature_tree = None

    @staticmethod
    def _title_sort_key(title: str) -> str:
        text = unicodedata.normalize("NFKC", title).casefold().lstrip()
        return text.lstrip("\"'([{“‘《〈【—–- ·:：")

    def _refresh_dedupe_summary(self) -> None:
        self.find_dedupe_button.configure(state="normal")
        self.one_click_dedupe_button.configure(state="normal")
        self.dedupe_mode_combo.configure(state="readonly")
        runs = self.project_data["runs"]
        if not runs:
            self.dedupe_summary_var.set("尚未运行去重。")
            self.apply_dedupe_button.configure(state="disabled")
            return
        run = runs[-1]
        if run["applied"] and self.project_data.get("needs_dedupe", True):
            self.dedupe_summary_var.set(
                "合并规则已更新；已移除的重复题录仍保留记录，请按当前模式重新查找并核查新候选。")
            self.apply_dedupe_button.configure(state="disabled")
            return
        pending = sum(group["status"] == "pending" for group in run["groups"])
        approved = sum(len(group["record_keys"]) - 1 for group in run["groups"] if group["status"] == "deduped")
        self.dedupe_summary_var.set(
            f"本次{PRESET_LABELS[run['preset']]}模式（{format_year_range(*self._run_scope(run))}）："
            f"候选 {run['candidate_groups']} 组 / {run['candidate_duplicates']} 条重复；"
            f"待核查 {pending} 组；核查确认 {approved} 条；"
            f"{'已统一去重' if run['applied'] else '待统一去重'}。"
        )
        self.apply_dedupe_button.configure(state="normal" if not pending and not run["applied"] else "disabled")

    def _current_year_scope(self) -> tuple[int | None, int | None]:
        return parse_year_range(self.year_start_var.get(), self.year_end_var.get())

    @staticmethod
    def _run_scope(run: dict) -> tuple[int | None, int | None]:
        return run.get("year_start"), run.get("year_end")

    def _scoped_records(self, *, include_excluded: bool = False) -> tuple[list[WosRecord], int | None, int | None]:
        start, end = self._current_year_scope()
        source = self.parsed.records if include_excluded and self.parsed is not None else self.records
        included, _, _ = partition_records_by_year(source, start, end)
        return included, start, end

    def apply_year_scope(self) -> None:
        if self.running:
            messagebox.showwarning("分析进行中", "请等待当前分析完成后再调整年份范围。", parent=self.root)
            return
        try:
            start, end = self._current_year_scope()
        except ValueError as exc:
            messagebox.showwarning("年份范围", str(exc), parent=self.root)
            return
        runs = self.project_data["runs"]
        if (runs and not runs[-1]["applied"]
                and self._run_scope(runs[-1]) != (start, end)):
            if not messagebox.askyesno("切换年份范围", "当前去重候选尚未完成。切换年份会放弃本轮核查，是否继续？", parent=self.root):
                previous_start, previous_end = self._run_scope(runs[-1])
                self.year_start_var.set(str(previous_start or ""))
                self.year_end_var.set(str(previous_end or ""))
                return
            runs.pop()
        if runs and runs[-1]["applied"]:
            self.project_data["needs_dedupe"] = (
                self._run_scope(runs[-1]) != (start, end)
                or runs[-1].get("import_generation", 0) != self.project_data.get("import_generation", 0)
            )
        elif not runs:
            self.project_data["needs_dedupe"] = True
        self._save_project_view(persist_project=True)
        self._refresh_literature_list()
        self._refresh_dedupe_summary()
        self.results = {}
        self._refresh_bibliometrics()
        self._log(f"年份范围设为 {format_year_range(start, end)}；范围外题录仍保存在项目中。")

    def _dedupe_ready_for_scope(self, start: int | None, end: int | None) -> bool:
        runs = self.project_data["runs"]
        return bool(runs and runs[-1]["applied"] and not self.project_data.get("needs_dedupe", True)
                    and self._run_scope(runs[-1]) == (start, end)
                    and runs[-1].get("import_generation", 0) == self.project_data.get("import_generation", 0))

    def _save_project_view(self, *, only_if_view_changed: bool = False,
                           persist_project: bool = False) -> None:
        updates = {
            "query": self.query_text.get("1.0", END).strip(),
            "year_start": self.year_start_var.get(), "year_end": self.year_end_var.get(),
            "auto_keyword": self.auto_keyword_var.get(),
            "vocabulary_spec": self.vocabulary_spec,
            "chart_settings": self._chart_settings(), "vos_settings": self.vos_panel.settings(),
        }
        changed = any(self.project_data.get(key) != value for key, value in updates.items())
        self.project_data.update(updates)
        if changed or not only_if_view_changed:
            if persist_project:
                self.workspace_store.save(self.project_data)
            else:
                self.workspace_store.save_view(self.project_data)

    def _on_close(self) -> None:
        self._save_project_view()
        self.root.destroy()

    def _refresh_project_list(self) -> None:
        self._switching_project = True
        projects = self.workspace_store.list_project_summaries()
        self.project_ids = [project["id"] for project in projects]
        self.project_list.delete(0, END)
        for project in projects:
            self.project_list.insert(END, project["name"])
        if self.project_data["id"] in self.project_ids:
            index = self.project_ids.index(self.project_data["id"])
            self.project_list.selection_set(index)
            self.project_list.activate(index)
        self._switching_project = False

    def _load_project(self, project_id: str, *, loaded: dict | None = None) -> None:
        self._pending_catalog_removals.clear()
        for child in self.root.winfo_children():
            if isinstance(child, ImportPreviewWindow):
                child.destroy()
        self.project_data = loaded if loaded is not None else self.workspace_store.load(project_id)
        self._normalized_cache_key = None
        self.workspace_store.activate(project_id)
        self.files = list(self.project_data["files"])
        self.raw_records = [record_from_json(item) for item in self.project_data["records"]]
        if self.project_data.get("citation_backfill_version", 0) < 1:
            recovered, failures = backfill_citation_fields(self.raw_records)
            if recovered:
                self.project_data["records"] = [record_to_json(record) for record in self.raw_records]
            if not failures:
                self.project_data["citation_backfill_version"] = 1
            if recovered or not failures:
                self.workspace_store.save(self.project_data)
            if recovered:
                self._log(f"已从原始导出文件补回 {recovered} 条题录的被引次数，无需重新导入。")
            if failures:
                self._log(f"{len(failures)} 个原始文件暂不可读取，相关被引次数未补全。")
        saved_spec = self.project_data.get("vocabulary_spec", {
            "countries": [], "organizations": [], "authors": [], "journals": [], "keywords": []})
        self.vocabulary_spec = saved_spec
        if "historical_merge_pairs" not in self.project_data:
            try:
                self.project_data["historical_merge_pairs"] = historical_snapshot(
                    self.shared_vocabulary.load())
            except (OSError, ValueError, KeyError):
                self.project_data["historical_merge_pairs"] = {}
            self.workspace_store.save(self.project_data)
        if not self.project_data.get("runs") and not self.project_data.get("ai_results"):
            try:
                self.vocabulary_spec = self.shared_vocabulary.overlay(saved_spec)
            except (OSError, ValueError, KeyError) as exc:
                self._log(f"共享合并目录读取失败，已保留项目规则：{exc}")
        self.project_data["vocabulary_spec"] = self.vocabulary_spec
        self.auto_keyword_var.set(self.project_data.get("auto_keyword", True))
        self.file_notice_var.set("可逐行核对文件名、来源、成功/失败题录与导入状态；双击查看完整路径。")
        self.query_text.delete("1.0", END)
        self.query_text.insert("1.0", self.project_data.get("query", ""))
        self.year_start_var.set(self.project_data.get("year_start", ""))
        self.year_end_var.set(self.project_data.get("year_end", ""))
        self.plot_workbench.load_settings(self.project_data.get("chart_settings", {}))
        self.vos_panel.restore(self.project_data.get("vos_settings", {}))
        self.results = {}
        self.review_draft = set()
        self.last_output = None
        self.output_button.configure(state="disabled")
        self.review_button.configure(state="disabled")
        self.project_title.configure(text=self.project_data["name"])
        self._refresh_import_state(refresh_chart=False)
        self._restore_saved_ai_results()
        self._refresh_bibliometrics()
        if project_id not in self.project_ids:
            self._refresh_project_list()

    def _restore_saved_ai_results(self) -> None:
        self.results = {}
        saved_ai = self.project_data.get("ai_results", {})
        if (saved_ai and not self.project_data.get("ai_results_stale", False)
                and self.project_data.get("ai_import_generation") == self.project_data.get("import_generation")
                and self.project_data.get("ai_year_start", "") == self.year_start_var.get()
                and self.project_data.get("ai_year_end", "") == self.year_end_var.get()
                and set(saved_ai) == {record.record_id for record in self.records}):
            try:
                self.results = {key: Classification.from_dict(value) for key, value in saved_ai.items()}
            except (ValueError, TypeError, KeyError):
                pass
        self._refresh_ai_history_notice()

    def _refresh_ai_history_notice(self) -> None:
        if self.project_data.get("ai_results_stale"):
            self.ai_history_var.set("已保存的 AI 结果仍在项目中；词汇规则改变了分析输入，使用前请重新分析。")
        elif self.results:
            self.ai_history_var.set(f"已恢复当前题录的 {len(self.results):,} 条 AI 分析结果。")
        else:
            self.ai_history_var.set("")

    def _on_project_selected(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "_switching_project", False) or self.running:
            return
        selection = self.project_list.curselection()
        if not selection:
            return
        project_id = self.project_ids[selection[0]]
        if project_id != self.project_data["id"]:
            self._save_project_view(only_if_view_changed=True)
            self._load_project(project_id)

    def create_project(self) -> None:
        if self.running:
            return
        name = ask_project_name(self.root, "新建项目")
        if not name:
            return
        try:
            self._save_project_view(only_if_view_changed=True)
            project = self.workspace_store.create(name)
            self._load_project(project["id"], loaded=project)
            self.tabs.select(0)
        except ValueError as exc:
            messagebox.showerror("新建项目失败", str(exc), parent=self.root)

    def rename_project(self) -> None:
        if self.running:
            return
        name = ask_project_name(self.root, "重命名项目", self.project_data["name"])
        if not name:
            return
        try:
            self.project_data = self.workspace_store.rename(self.project_data["id"], name)
            self.project_title.configure(text=self.project_data["name"])
            self._refresh_project_list()
        except ValueError as exc:
            messagebox.showerror("重命名失败", str(exc), parent=self.root)

    def delete_project(self) -> None:
        if self.running:
            return
        name = self.project_data["name"]
        if not messagebox.askyesno(
                "删除项目", f"确定从项目列表删除“{name}”？\n\n"
                "项目数据会移至本地回收目录；原始题录文件和已导出的 output 文件不会删除。",
                parent=self.root):
            return
        try:
            self._save_project_view(only_if_view_changed=True)
            if self.literature_window is not None and self.literature_window.winfo_exists():
                self.literature_window.destroy()
                self.literature_window = None
                self.literature_tree = None
            project_id, _archive = self.workspace_store.delete(self.project_data["id"])
            self._load_project(project_id)
            self._refresh_project_list()
            self.tabs.select(0)
            self._log(f"项目“{name}”已从列表删除，并移至本地回收目录。")
        except (OSError, ValueError) as exc:
            messagebox.showerror("删除项目失败", str(exc), parent=self.root)

    def run_dedupe(self) -> None:
        if self.running:
            return
        if not self.records:
            messagebox.showwarning("没有题录", "请先拖入可识别的完整题录文件。", parent=self.root)
            return
        if self.project_data["runs"] and not self.project_data["runs"][-1]["applied"]:
            messagebox.showwarning("上次去重未完成", "请先核查上次的重复组并统一去重。", parent=self.root)
            return
        try:
            scoped, start, end = self._scoped_records()
        except ValueError as exc:
            messagebox.showwarning("年份范围", str(exc), parent=self.root)
            return
        if not scoped:
            messagebox.showwarning("年份范围无题录", "当前年份范围内没有可去重的题录。", parent=self.root)
            return
        preset = next(key for key, label in PRESET_LABELS.items() if label == self.dedupe_mode_var.get())
        run = new_run(scoped, preset)
        run["year_start"], run["year_end"] = start, end
        run["import_generation"] = self.project_data.get("import_generation", 0)
        if not run["groups"]:
            run["applied"] = True
            run["removed_count"] = 0
            self.project_data["needs_dedupe"] = False
            self.plot_workbench.scope_var.set(DEDUPE_SCOPE)
            self.vos_panel.scope_var.set(DEDUPE_SCOPE)
        self.project_data["runs"].append(run)
        self.workspace_store.save(self.project_data)
        self._refresh_dedupe_summary()
        self._refresh_bibliometrics()
        self._log(f"{PRESET_LABELS[preset]}模式发现 {run['candidate_groups']} 组候选，疑似重复 {run['candidate_duplicates']} 条。")
        if run["groups"]:
            self.open_dedupe_review()

    def one_click_dedupe(self) -> None:
        if self.running or not self.records:
            return
        try:
            scoped, start, end = self._scoped_records()
        except ValueError as exc:
            messagebox.showwarning("年份范围", str(exc), parent=self.root)
            return
        if not scoped:
            messagebox.showwarning("年份范围无题录", "当前年份范围内没有可去重的题录。", parent=self.root)
            return
        preset = next(key for key, label in PRESET_LABELS.items() if label == self.dedupe_mode_var.get())
        runs = self.project_data["runs"]
        pending_run = runs[-1] if runs and not runs[-1]["applied"] else None
        if pending_run and (pending_run["preset"] != preset or self._run_scope(pending_run) != (start, end)
                            or pending_run.get("import_generation", 0) != self.project_data.get("import_generation", 0)):
            messagebox.showwarning("上次去重未完成", "请先完成当前模式和年份范围的核查，再运行其他模式或年份范围。", parent=self.root)
            return
        run = pending_run or new_run(scoped, preset)
        run["year_start"], run["year_end"] = start, end
        run["import_generation"] = self.project_data.get("import_generation", 0)
        pending = [group for group in run["groups"] if group["status"] == "pending"]
        conflicts = sum(group["doi_conflict"] for group in pending)
        if not messagebox.askyesno(
            "一键去重",
            f"将按{PRESET_LABELS[preset]}模式处理 {len(scoped)} 条题录："
            f"候选 {run['candidate_groups']} 组 / {run['candidate_duplicates']} 条。"
            f"\n优先保留 WoS 来源，其次保留字段更完整的记录；"
            f"{conflicts} 组 DOI 冲突将全部保留。\n确认统一应用？",
            parent=self.root,
        ):
            return
        try:
            accepted, skipped = auto_review_run(run, scoped)
            removed = apply_review(self.project_data, run)
        except ValueError as exc:
            messagebox.showerror("一键去重失败", str(exc), parent=self.root)
            return
        if pending_run is None:
            runs.append(run)
        run["automatic"] = True
        self.project_data["needs_dedupe"] = False
        self.plot_workbench.scope_var.set(DEDUPE_SCOPE)
        self.vos_panel.scope_var.set(DEDUPE_SCOPE)
        self.results = {}
        self.workspace_store.save(self.project_data)
        self._refresh_import_state()
        self._log(f"一键去重完成：{PRESET_LABELS[preset]}模式，确认 {accepted} 条，本次实际排除 {removed} 条；"
                  f"DOI 冲突保留 {skipped} 组。")

    def open_dedupe_review(self) -> None:
        if not self.project_data["runs"]:
            messagebox.showinfo("去重核查", "请先运行查找重复。", parent=self.root)
            return
        run = self.project_data["runs"][-1]
        if not run["groups"]:
            messagebox.showinfo("去重核查", "本次没有找到重复候选。", parent=self.root)
            return
        DedupeReviewWindow(self, run)

    def show_dedupe_history(self) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"{self.project_data['name']} · 去重历史")
        window.geometry("780x400")
        tree = ttk.Treeview(window, columns=("time", "mode", "years", "groups", "candidates", "confirmed", "removed", "state"), show="headings")
        for col, label, width in (("time", "运行时间", 145), ("mode", "模式", 60), ("years", "年份", 100), ("groups", "候选组", 70),
                                  ("candidates", "疑似重复", 90), ("confirmed", "核查确认", 90),
                                  ("removed", "实际排除", 90), ("state", "状态", 90)):
            tree.heading(col, text=label)
            tree.column(col, width=width, anchor="center")
        tree.pack(fill=BOTH, expand=True, padx=10, pady=10)
        for run in self.project_data["runs"]:
            confirmed = sum(len(group["record_keys"]) - 1 for group in run["groups"] if group["status"] == "deduped")
            tree.insert("", END, values=(run.get("created_at", ""), PRESET_LABELS[run["preset"]],
                      format_year_range(*self._run_scope(run)),
                      run["candidate_groups"], run["candidate_duplicates"], confirmed,
                      run.get("removed_count", 0),
                      ("一键完成" if run.get("automatic") else "已应用") if run["applied"] else "核查中"))

    def apply_dedupe(self) -> None:
        if self.running or not self.project_data["runs"]:
            return
        run = self.project_data["runs"][-1]
        try:
            if any(group["status"] == "pending" for group in run["groups"]):
                raise ValueError("仍有重复组待核查。")
            approved = sum(len(group["record_keys"]) - 1 for group in run["groups"] if group["status"] == "deduped")
            if not messagebox.askyesno("统一去重", f"已核查全部 {len(run['groups'])} 组。确认从当前项目工作列表中排除 {approved} 条重复题录？原始题录仍会保留。", parent=self.root):
                return
            removed = apply_review(self.project_data, run)
            self.plot_workbench.scope_var.set(DEDUPE_SCOPE)
            self.vos_panel.scope_var.set(DEDUPE_SCOPE)
            try:
                self.project_data["needs_dedupe"] = (
                    self._run_scope(run) != self._current_year_scope()
                    or run.get("import_generation", 0) != self.project_data.get("import_generation", 0)
                )
            except ValueError:
                self.project_data["needs_dedupe"] = True
        except ValueError as exc:
            messagebox.showwarning("无法统一去重", str(exc), parent=self.root)
            return
        self.workspace_store.save(self.project_data)
        self.results = {}
        self._refresh_import_state()
        self._log(f"本次统一去重完成：移除 {removed} 条；当前工作列表 {len(self.records)} 条。")

    def export_basic(self) -> None:
        if self.running:
            return
        try:
            scoped, start, end = self._scoped_records()
        except ValueError as exc:
            messagebox.showwarning("年份范围", str(exc), parent=self.root)
            return
        if not self._dedupe_ready_for_scope(start, end):
            messagebox.showwarning("题录尚未就绪", "请先完成基础去重；如只需转换格式并合并，请使用独立合并功能。", parent=self.root)
            return
        if not scoped or self.parsed is None:
            messagebox.showwarning("没有可导出的题录", "当前年份范围内没有保留的题录。", parent=self.root)
            return
        initial_dir = preferred_export_dir(self.app_dir, self.project_data.get("basic_export_dir"))
        created_dir = not initial_dir.exists()
        initial_dir.mkdir(parents=True, exist_ok=True)
        selected = filedialog.asksaveasfilename(
            parent=self.root, title="导出当前题录", initialdir=str(initial_dir),
            initialfile=default_export_name(), defaultextension=".txt",
            filetypes=[("WoS 风格题录 TXT", "*.txt"), ("所有文件", "*.*")],
        )
        if not selected:
            if created_dir:
                try:
                    initial_dir.rmdir()
                except OSError:
                    pass
            return
        try:
            ordered = sorted(scoped, key=lambda record: (self._title_sort_key(record.title), record_key(record)))
            destination = export_basic_records(selected, ordered, self.parsed.header)
        except (OSError, ValueError) as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self.project_data["basic_export_dir"] = str(destination.parent)
        self.project_data["last_basic_export"] = str(destination)
        self.workspace_store.save(self.project_data)
        self._log(f"已去重题录已导出 {len(ordered)} 条：{destination}")
        messagebox.showinfo("导出完成", f"已导出 {len(ordered)} 条题录。\n{destination}", parent=self.root)

    def show_literature_detail(self, _event: tk.Event | None = None) -> None:
        if self.literature_tree is None:
            return
        selection = self.literature_tree.selection()
        if not selection or self.parsed is None:
            return
        record = next((item for item in self.parsed.records if record_key(item) == selection[0]), None)
        if record is None:
            return
        window = tk.Toplevel(self.root)
        window.title(record.title or "题录详情")
        window.geometry("900x620")
        viewer = tk.Text(window, wrap="word", padx=12, pady=12, font=("Microsoft YaHei UI", 10))
        viewer.pack(fill=BOTH, expand=True)
        viewer.insert("1.0", f"题名：{record.title}\n作者：{record.text('AU')}\n年份：{record.text('PY')}\n"
                  f"来源：{record.source_kind} / {Path(record.source_file).name}\n期刊：{record.text('SO')}\n"
                  f"DOI：{record.text('DI')}\n关键词：{record.keywords}\n\n摘要：\n{record.abstract}")
        viewer.configure(state="disabled")

    def open_key_file(self) -> None:
        provider = self.provider_var.get()
        if provider == PROVIDER_HYBRID:
            key_files = (
                (JEV_API_KEY_FILENAME, JEV_API_KEY_PLACEHOLDER),
                (DEEPSEEK_API_KEY_FILENAME, DEEPSEEK_API_KEY_PLACEHOLDER),
            )
        elif provider == PROVIDER_DEEPSEEK:
            key_files = ((DEEPSEEK_API_KEY_FILENAME, DEEPSEEK_API_KEY_PLACEHOLDER),)
        else:
            key_files = ((JEV_API_KEY_FILENAME, JEV_API_KEY_PLACEHOLDER),)
        for key_filename, placeholder in key_files:
            key_path = self.app_dir / key_filename
            if not key_path.exists():
                key_path.write_text(placeholder, encoding="utf-8")
            try:
                os.startfile(key_path)  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("无法打开", str(exc), parent=self.root)
                return

    def _on_provider_changed(self, _event: tk.Event | None = None) -> None:
        provider = self.provider_var.get()
        if provider == PROVIDER_HYBRID:
            key_text = (
                f"API Key：{self.app_dir / JEV_API_KEY_FILENAME} + "
                f"{self.app_dir / DEEPSEEK_API_KEY_FILENAME}"
            )
            model = f"{JEV_MODEL} → {DEEPSEEK_MODEL}"
            concurrency_hint = (
                f"建议总并发 16；DeepSeek 复核最多 {DEFAULT_DEEPSEEK_REVIEW_CONCURRENCY}"
            )
        elif provider == PROVIDER_DEEPSEEK:
            key_filename = DEEPSEEK_API_KEY_FILENAME
            key_environment = DEEPSEEK_API_KEY_ENV
            key_text = f"API Key：{self.app_dir / key_filename}（或环境变量 {key_environment}）"
            model = DEEPSEEK_MODEL
            concurrency_hint = "建议并发 3–8"
        else:
            key_filename = JEV_API_KEY_FILENAME
            key_environment = JEV_API_KEY_ENV
            key_text = f"API Key：{self.app_dir / key_filename}（或环境变量 {key_environment}）"
            model = JEV_MODEL
            concurrency_hint = "建议并发 8–16"
        self.key_label.configure(text=key_text)
        self.model_label.configure(text=f"模型：{model}；{concurrency_hint}")

    def start_analysis(self) -> None:
        if self.running:
            return
        query = self.query_text.get("1.0", END).strip()
        if not self.files:
            messagebox.showwarning("缺少文件", "请先选择至少一个题录文件。", parent=self.root)
            return
        if not query:
            messagebox.showwarning("缺少检索式", "请输入完整的 WOS 检索式。", parent=self.root)
            return
        try:
            year_start, year_end = parse_year_range(self.year_start_var.get(), self.year_end_var.get())
        except ValueError as exc:
            messagebox.showwarning("年份范围", str(exc), parent=self.root)
            return
        if not self._dedupe_ready_for_scope(year_start, year_end):
            messagebox.showwarning("题录尚未就绪", "请按当前年份范围完成基础去重。", parent=self.root)
            self.tabs.select(0)
            return
        if self.parsed is None:
            if not self.preview_import(show_window=False):
                return
        self._save_project_view()
        assert self.parsed is not None
        parsed = self.parsed
        records = self.records
        duplicates = self.duplicates
        provider = self.provider_var.get()
        try:
            if provider == PROVIDER_HYBRID:
                jev_key = load_jev_api_key(self.app_dir)
                deepseek_key = load_deepseek_api_key(self.app_dir)
                client = HybridClient(
                    jev=JevClient(api_key=jev_key, timeout=max(60, self.timeout_var.get())),
                    deepseek=DeepSeekClient(api_key=deepseek_key, timeout=max(60, self.timeout_var.get())),
                )
                classify_function = classify_with_hybrid_cache
                thread_name = "hybrid"
            elif provider == PROVIDER_DEEPSEEK:
                api_key = load_deepseek_api_key(self.app_dir)
                client: Any = DeepSeekClient(api_key=api_key, timeout=max(60, self.timeout_var.get()))
                classify_function = classify_with_deepseek_cache
                thread_name = "deepseek"
            else:
                api_key = load_jev_api_key(self.app_dir)
                client = JevClient(api_key=api_key, timeout=max(60, self.timeout_var.get()))
                classify_function = classify_with_jev_cache
                thread_name = "jev"
        except (JevApiKeyError, DeepSeekApiKeyError) as exc:
            messagebox.showwarning(f"{provider} API Key", str(exc), parent=self.root)
            self.open_key_file()
            return
        analysis_records, year_excluded, year_unknown = partition_records_by_year(records, year_start, year_end)
        year_range = format_year_range(year_start, year_end)
        self.year_start = year_start
        self.year_end = year_end
        self.year_excluded_count = len(year_excluded)
        self.year_unknown_count = len(year_unknown)
        self.provider_used = provider
        self.model_used = client.model
        self.results = {
            record.record_id: Classification(
                record_id=record.record_id,
                decision="irrelevant",
                confidence=1.0,
                reason=f"发文年份 {year} 不在设定范围 {year_range} 内，已按年份规则排除，未调用 API。",
                exclusion_reason=f"发文年份不在 {year_range} 内",
            )
            for record, year in year_excluded
        }
        self.review_draft = set()
        self.started_at = datetime.now()
        run_id = self.started_at.strftime("%Y%m%d_%H%M%S")
        self.audit_log_path = self.workspace_store.root / self.project_data["id"] / f"audit_{run_id}.jsonl"
        self.cancel_event.clear()
        self.pause_event.set()
        self.running = True
        self.progress.configure(maximum=max(1, len(records)), value=len(self.results))
        self.progress_label.configure(text=f"{len(self.results)} / {len(records)}")
        self.review_button.configure(state="disabled")
        self.output_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.provider_combo.configure(state="disabled")
        self.pause_button.configure(state="normal", text="暂停")
        self.cancel_button.configure(state="normal")
        self._clear_log()
        self._log(f"已读取 {len(parsed.records)} 条记录，去重后 {len(records)} 条，移除重复 {len(duplicates)} 条。")
        for item in parsed.imports:
            self._log(f"来源识别：{Path(item.path).name} → {item.source} / {item.format}，{item.count} 条。")
        self._log(f"关键词自动归并 {len(self.vocabulary_report.get('automatic_keyword_groups', []))} 组，"
                  f"修改 {self.vocabulary_report.get('affected_record_count', 0)} 条题录。")
        if self.doi_conflicts:
            self._log(f"警告：{len(self.doi_conflicts)} 组同题同年 DOI 不同的候选记录已保留，需人工核对。")
        if year_start is not None or year_end is not None:
            self._log(
                f"发文年份范围：{year_range}；规则排除 {len(year_excluded)} 条，"
                f"需进入主题筛选 {len(analysis_records)} 条。"
            )
            if year_unknown:
                self._log(f"警告：{len(year_unknown)} 条记录缺少可识别的四位年份，已保留进入主题筛选。")
        for warning in parsed.warnings:
            self._log("警告：" + warning)
        if analysis_records:
            review_limit = (
                f"；DeepSeek 复核并发上限 {client.deepseek_max_concurrency}"
                if provider == PROVIDER_HYBRID else ""
            )
            self._log(
                f"开始调用 {self.provider_used} / {self.model_used}；"
                f"总并发数 {max(1, min(MAX_WORKERS, self.workers_var.get()))}"
                f"{review_limit}。"
            )
        else:
            self._log("全部记录均已由年份规则处理，无需调用 API。")

        cache = ResultCache(self.workspace_store.root / self.project_data["id"] / "classification_cache.sqlite3")
        audit_log = JsonlAuditLog(self.audit_log_path)
        for record, year in year_excluded:
            audit_log.append({
                "event": "year_filter_excluded",
                "record_id": record.record_id,
                "publication_year": year,
                "year_start": year_start,
                "year_end": year_end,
                "reason": self.results[record.record_id].reason,
            })
        self._update_progress()
        thread = threading.Thread(
            target=self._analysis_worker,
            args=(
                analysis_records,
                query,
                client,
                classify_function,
                thread_name,
                cache,
                audit_log,
                max(1, min(MAX_WORKERS, self.workers_var.get())),
            ),
            daemon=True,
        )
        thread.start()

    def _analysis_worker(
        self,
        records: list[WosRecord],
        query: str,
        client: Any,
        classify_function: Callable[[WosRecord, str, Any, ResultCache, JsonlAuditLog], Classification],
        thread_name: str,
        cache: ResultCache,
        audit_log: JsonlAuditLog,
        workers: int,
    ) -> None:
        try:
            def do_one(record: WosRecord) -> tuple[WosRecord, Classification]:
                self.pause_event.wait()
                if self.cancel_event.is_set():
                    raise CancelledByUser()
                result = classify_function(record, query, client, cache, audit_log)
                return record, result

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=thread_name) as executor:
                futures = {executor.submit(do_one, record): record for record in records}
                for future in as_completed(futures):
                    if self.cancel_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        record, result = future.result()
                    except CancelledByUser:
                        continue
                    except Exception as exc:
                        record = futures[future]
                        result = Classification(
                            record_id=record.record_id, decision="uncertain", confidence=0,
                            reason="程序内部错误，已转入人工复核。", error=str(exc),
                        )
                    self.events.put(("result", (record, result)))
            self.events.put(("cancelled" if self.cancel_event.is_set() else "analysis_done", query))
        except Exception:
            self.events.put(("fatal", traceback.format_exc()))

    def toggle_pause(self) -> None:
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="继续")
            self._log("已暂停提交新的 API 分析；正在进行的请求会完成并保存。")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="暂停")
            self._log("继续分析。")

    def cancel_analysis(self) -> None:
        if messagebox.askyesno("取消分析", "确定取消？已经完成的结果仍保留在缓存中。", parent=self.root):
            self.cancel_event.set()
            self.pause_event.set()
            self.cancel_button.configure(state="disabled")
            self._log("正在取消；等待当前 API 请求结束……")

    def _poll_events(self) -> None:
        deadline = time.monotonic() + 0.025
        processed = 0
        try:
            while processed < 40 and time.monotonic() < deadline:
                event, payload = self.events.get_nowait()
                processed += 1
                if event == "result":
                    record, result = payload  # type: ignore[misc]
                    self.results[record.record_id] = result
                    self._update_progress()
                    cache_note = "（缓存）" if result.cached else ""
                    self._log(
                        f"[{len(self.results)}/{len(self.records)}] {DISPLAY_DECISION[result.decision]}{cache_note}："
                        f"{record.title[:70] or record.record_id}"
                    )
                elif event == "analysis_done":
                    self._analysis_finished(str(payload))
                elif event == "cancelled":
                    self._set_idle()
                    self._log("分析已取消；下次使用相同检索式开始时会复用已完成缓存。")
                elif event == "export_done":
                    self.last_output = Path(str(payload))
                    self._set_idle()
                    self.output_button.configure(state="normal")
                    self.project_data["ai_results"] = {key: value.to_dict() for key, value in self.results.items()}
                    self.project_data["ai_import_generation"] = self.project_data.get("import_generation", 0)
                    self.project_data["ai_results_stale"] = False
                    self._refresh_ai_history_notice()
                    self.project_data["ai_year_start"] = self.year_start_var.get()
                    self.project_data["ai_year_end"] = self.year_end_var.get()
                    self.workspace_store.save(self.project_data)
                    self.metrics_scope_var.set("AI 筛选相关")
                    self._refresh_bibliometrics()
                    self.tabs.select(2)
                    self._log(f"导出完成：{self.last_output}")
                    messagebox.showinfo(
                        "全部完成",
                        f"已生成 Excel、CiteSpace 文件和审核日志。\n\n{self.last_output}",
                        parent=self.root,
                    )
                elif event == "export_status":
                    self._log(str(payload))
                elif event == "fatal":
                    self._set_idle()
                    self._log(str(payload))
                    messagebox.showerror("程序错误", "处理失败，详细信息已写入运行日志。", parent=self.root)
        except queue.Empty:
            pass
        self.root.after(1 if not self.events.empty() else 120, self._poll_events)

    def _analysis_finished(self, query: str) -> None:
        self.running = False
        self.pause_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        uncertain = sum(result.decision == "uncertain" for result in self.results.values())
        self._load_review_draft(query)
        self._log(f"AI 初筛完成，共有 {uncertain} 条存疑记录。")
        if uncertain:
            self.review_button.configure(state="normal")
            self.open_review()
        else:
            self._start_export(query)

    def open_review(self) -> None:
        if not self.results:
            return
        uncertain = [record for record in self.records if self.results[record.record_id].decision == "uncertain"]
        if not uncertain:
            query = self.query_text.get("1.0", END).strip()
            self._start_export(query)
            return
        ReviewWindow(self, uncertain)

    def review_completed(self, excluded_ids: set[str]) -> None:
        uncertain_ids = {record.record_id for record in self.records if self.results[record.record_id].decision == "uncertain"}
        for record_id in uncertain_ids:
            result = self.results[record_id]
            result.final_decision = "irrelevant" if record_id in excluded_ids else "relevant"
            result.human_modified = True
            if record_id in excluded_ids and not result.exclusion_reason:
                result.exclusion_reason = "人工复核排除"
        self.review_button.configure(state="disabled")
        self.review_draft.clear()
        (self.workspace_store.root / self.project_data["id"] / "review_draft.json").unlink(missing_ok=True)
        self._update_progress()
        self._log(f"人工复核完成：排除 {len(excluded_ids)} 条，其余 {len(uncertain_ids) - len(excluded_ids)} 条纳入相关。")
        self._start_export(self.query_text.get("1.0", END).strip())

    def save_review_draft(self, excluded_ids: set[str]) -> None:
        self.review_draft = set(excluded_ids)
        path = self.workspace_store.root / self.project_data["id"] / "review_draft.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "query": self.query_text.get("1.0", END).strip(),
            "excluded_ids": sorted(excluded_ids),
            "uncertain_ids": sorted(
                record.record_id for record in self.records
                if self.results.get(record.record_id) and self.results[record.record_id].decision == "uncertain"
            ),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"已保存人工复核进度：勾选排除 {len(excluded_ids)} 条。")

    def _load_review_draft(self, query: str) -> None:
        path = self.workspace_store.root / self.project_data["id"] / "review_draft.json"
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            current_ids = {
                record.record_id for record in self.records
                if self.results.get(record.record_id) and self.results[record.record_id].decision == "uncertain"
            }
            if payload.get("query", "").strip() != query.strip() or set(payload.get("uncertain_ids", [])) != current_ids:
                return
            self.review_draft = set(payload.get("excluded_ids", [])) & current_ids
            if self.review_draft:
                self._log(f"已恢复上次保存的人工复核进度：勾选排除 {len(self.review_draft)} 条。")
        except (OSError, ValueError, TypeError):
            self._log("警告：人工复核草稿无法读取，已忽略。")

    def _start_export(self, query: str) -> None:
        if len(self.results) != len(self.records):
            messagebox.showerror("结果不完整", "仍有文献没有分类，无法导出。", parent=self.root)
            self._set_idle()
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self._log("开始生成 CiteSpace 文件、Excel 和审核日志。")

        def worker() -> None:
            try:
                resource_dir = get_resource_dir()
                node, modules = _find_bundled_node(resource_dir)
                output = export_results(
                    app_dir=resource_dir,
                    output_root=self.app_dir / "output",
                    header=self.parsed.header if self.parsed else "",
                    records=self.records,
                    results=self.results,
                    duplicates=self.duplicates,
                    query=query,
                    model=f"{self.provider_used} / {self.model_used}",
                    started_at=self.started_at,
                    audit_log_path=self.audit_log_path,
                    node_executable=node,
                    node_modules=modules,
                    diagnostics_dir=None,
                    on_status=lambda text: self.events.put(("export_status", text)),
                    year_start=self.year_start,
                    year_end=self.year_end,
                    year_excluded_count=self.year_excluded_count,
                    year_unknown_count=self.year_unknown_count,
                )
                self.events.put(("export_done", str(output)))
            except Exception:
                self.events.put(("fatal", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self) -> None:
        completed = len(self.results)
        self.progress.configure(value=completed)
        self.progress_label.configure(text=f"{completed} / {len(self.records)}")
        counts = {"relevant": 0, "uncertain": 0, "irrelevant": 0, "failed": 0}
        for result in self.results.values():
            counts[result.decision] += 1
            counts["failed"] += bool(result.error)
        self.count_label.configure(
            text=f"相关 {counts['relevant']} · 存疑 {counts['uncertain']} · 不相关 {counts['irrelevant']} · 失败 {counts['failed']}"
        )

    def _set_idle(self) -> None:
        self.running = False
        self.start_button.configure(state="normal")
        self.provider_combo.configure(state="readonly")
        self.pause_button.configure(state="disabled", text="暂停")
        self.cancel_button.configure(state="disabled")

    def open_output(self) -> None:
        if not self.last_output:
            return
        try:
            os.startfile(self.last_output)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开", str(exc), parent=self.root)

    def _log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"[{stamp}] {text}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")


class DedupeReviewWindow:
    def __init__(self, app: WosFilterApp, run: dict) -> None:
        self.app = app
        self.run = run
        self.project_id = app.project_data["id"]
        self.records = {record_key(record): record for record in app.parsed.records} if app.parsed else {}
        self.window = tk.Toplevel(app.root)
        self.window.title(f"{PRESET_LABELS[run['preset']]}模式 · 重复候选核查")
        self.window.geometry("1120x720")
        self.window.transient(app.root)
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=BOTH, expand=True)
        ttk.Label(outer, text="逐组比较题名、作者、年份、DOI 和摘要。选择保留记录后确认去重，或标记为非重复。全部核查完毕后回到工作区点击“统一去重”。",
                  wraplength=1050, style="Hint.TLabel").pack(anchor=W)
        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill=BOTH, expand=True, pady=(10, 0))
        group_box = ttk.LabelFrame(body, text="候选组", padding=5)
        record_box = ttk.LabelFrame(body, text="组内题录（选中要保留的一条）", padding=5)
        body.add(group_box, weight=1)
        body.add(record_box, weight=3)
        self.group_tree = ttk.Treeview(group_box, columns=("index", "status", "count", "title"), show="headings")
        for col, label, width in (("index", "组", 40), ("status", "状态", 72), ("count", "候选", 52), ("title", "题名", 220)):
            self.group_tree.heading(col, text=label)
            self.group_tree.column(col, width=width)
        self.group_tree.pack(fill=BOTH, expand=True)
        self.group_tree.bind("<<TreeviewSelect>>", self._show_group)
        self.record_tree = ttk.Treeview(record_box, columns=("keep", "source", "year", "title", "doi", "author"), show="headings")
        for col, label, width in (("keep", "保留", 52), ("source", "数据库", 90), ("year", "年份", 55),
                                  ("title", "题名", 300), ("doi", "DOI", 170), ("author", "作者", 160)):
            self.record_tree.heading(col, text=label)
            self.record_tree.column(col, width=width)
        scroll = ttk.Scrollbar(record_box, orient=VERTICAL, command=self.record_tree.yview)
        self.record_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=RIGHT, fill=Y)
        self.record_tree.pack(fill=BOTH, expand=True)
        self.record_tree.bind("<<TreeviewSelect>>", self._show_record)
        detail_box = ttk.LabelFrame(outer, text="题录详情", padding=6)
        detail_box.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.detail = tk.Text(detail_box, height=9, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
        self.detail.pack(fill=BOTH, expand=True)
        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=(9, 0))
        ttk.Button(actions, text="确认重复，保留选中题录", command=self._confirm_duplicate).pack(side=LEFT)
        ttk.Button(actions, text="非重复，全部保留", command=self._ignore_group).pack(side=LEFT, padx=8)
        ttk.Button(actions, text="撤销本组决定", command=self._reset_group).pack(side=LEFT)
        ttk.Button(actions, text="返回工作区", command=self.window.destroy).pack(side=RIGHT)
        self._fill_groups()
        if self.run["groups"]:
            self.group_tree.selection_set(self.run["groups"][0]["id"])
            self._show_group()
        self.window.grab_set()

    def _selected_group(self) -> dict | None:
        selection = self.group_tree.selection()
        return next((group for group in self.run["groups"] if selection and group["id"] == selection[0]), None)

    def _fill_groups(self) -> None:
        selected = self.group_tree.selection()
        self.group_tree.delete(*self.group_tree.get_children())
        for index, group in enumerate(self.run["groups"], 1):
            primary = self.records.get(group["primary"])
            status = {"pending": "待核查", "deduped": "确认重复", "ignored": "非重复"}[group["status"]]
            if group["doi_conflict"]:
                status += " ⚠"
            self.group_tree.insert("", END, iid=group["id"], values=(index, status,
                                   len(group["record_keys"]), primary.title if primary else ""))
        if selected and self.group_tree.exists(selected[0]):
            self.group_tree.selection_set(selected[0])

    def _show_group(self, _event: tk.Event | None = None) -> None:
        self.record_tree.delete(*self.record_tree.get_children())
        group = self._selected_group()
        if not group:
            return
        for key in group["record_keys"]:
            record = self.records.get(key)
            if not record:
                continue
            self.record_tree.insert("", END, iid=key, values=(
                "★" if key == group["primary"] else "", record.source_kind,
                record.text("PY"), record.title, record.text("DI"), record.text("AU"),
            ))
        self.record_tree.selection_set(group["primary"])
        self._show_record()

    def _show_record(self, _event: tk.Event | None = None) -> None:
        selection = self.record_tree.selection()
        if not selection:
            return
        record = self.records[selection[0]]
        group = self._selected_group()
        warning = "⚠ 本组 DOI 不同，请重点核对。\n\n" if group and group["doi_conflict"] else ""
        content = (warning + f"题名：{record.title}\n作者：{record.text('AU')}\n年份：{record.text('PY')}\n"
                   f"期刊：{record.text('SO')}\nDOI：{record.text('DI')}\n数据库：{record.source_kind}\n"
                   f"来源文件：{record.source_file}\n\n摘要：{record.abstract or '（无）'}")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", END)
        self.detail.insert("1.0", content)
        self.detail.configure(state="disabled")

    def _decide(self, status: str) -> None:
        if self.app.project_data["id"] != self.project_id:
            messagebox.showerror("项目已切换", "当前核查窗口不属于此项目，请关闭后重新打开。", parent=self.window)
            return
        if self.run["applied"]:
            messagebox.showinfo("已应用", "本次去重已经应用，无法修改核查结果。", parent=self.window)
            return
        group = self._selected_group()
        if not group:
            return
        if status == "deduped":
            selected = self.record_tree.selection()
            if not selected:
                messagebox.showwarning("请选择记录", "请选择要保留的一条题录。", parent=self.window)
                return
            group["primary"] = selected[0]
        group["status"] = status
        self.app.workspace_store.save(self.app.project_data)
        self.app._refresh_dedupe_summary()
        self._fill_groups()
        self._show_group()

    def _confirm_duplicate(self) -> None:
        self._decide("deduped")

    def _ignore_group(self) -> None:
        self._decide("ignored")

    def _reset_group(self) -> None:
        self._decide("pending")


class ReviewWindow:
    def __init__(self, app: WosFilterApp, records: list[WosRecord]) -> None:
        self.app = app
        self.records = records
        record_ids = {record.record_id for record in records}
        self.excluded: set[str] = set(app.review_draft) & record_ids
        self.window = tk.Toplevel(app.root)
        self.window.title(f"存疑文献人工复核（{len(records)} 条）")
        self.window.geometry("1200x780")
        self.window.minsize(960, 640)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close_without_finish)
        self.search_var = tk.StringVar()
        self.iid_to_record: dict[str, WosRecord] = {}
        self._build()
        self.populate()

    def _build(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=BOTH, expand=True)
        ttk.Label(
            outer,
            text="请勾选确定不相关的文献；未勾选者在完成复核后默认归入相关。双击第一列或按空格切换。",
            font=("Microsoft YaHei UI", 10, "bold"), foreground="#17365D",
        ).pack(anchor=W)
        tools = ttk.Frame(outer)
        tools.pack(fill=X, pady=(9, 7))
        ttk.Label(tools, text="搜索：").pack(side=LEFT)
        entry = ttk.Entry(tools, textvariable=self.search_var, width=45)
        entry.pack(side=LEFT)
        entry.bind("<KeyRelease>", lambda _e: self.populate())
        ttk.Button(tools, text="全选为不相关", command=self.select_all).pack(side=LEFT, padx=(12, 6))
        ttk.Button(tools, text="全部取消", command=self.clear_all).pack(side=LEFT)
        self.summary = ttk.Label(tools, text="")
        self.summary.pack(side=RIGHT)

        paned = ttk.Panedwindow(outer, orient=VERTICAL)
        paned.pack(fill=BOTH, expand=True)
        table_frame = ttk.Frame(paned)
        detail_frame = ttk.LabelFrame(paned, text="所选文献详情", padding=8)
        paned.add(table_frame, weight=3)
        paned.add(detail_frame, weight=2)

        columns = ("exclude", "confidence", "year", "title", "author", "source", "reason")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headers = {
            "exclude": "排除", "confidence": "置信度", "year": "年份", "title": "题名",
            "author": "作者", "source": "期刊", "reason": "AI 判断摘要",
        }
        widths = {"exclude": 60, "confidence": 75, "year": 60, "title": 350, "author": 170, "source": 150, "reason": 310}
        for col in columns:
            self.tree.heading(col, text=headers[col], command=(lambda c=col: self.sort_by(c)))
            self.tree.column(col, width=widths[col], minwidth=50, anchor="center" if col in {"exclude", "confidence", "year"} else W)
        yscroll = ttk.Scrollbar(table_frame, orient=VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side=RIGHT, fill=Y)
        xscroll.pack(side="bottom", fill=X)
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Double-1>", self.toggle_current)
        self.tree.bind("<space>", self.toggle_current)

        self.detail = tk.Text(detail_frame, wrap="word", height=12, state="disabled", font=("Microsoft YaHei UI", 9))
        detail_scroll = ttk.Scrollbar(detail_frame, orient=VERTICAL, command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=RIGHT, fill=Y)
        self.detail.pack(fill=BOTH, expand=True)

        bottom = ttk.Frame(outer)
        bottom.pack(fill=X, pady=(10, 0))
        ttk.Button(bottom, text="保存进度并稍后复核", command=self.save_and_close).pack(side=LEFT)
        ttk.Button(bottom, text="完成复核并导出", command=self.finish).pack(side=RIGHT)

    def populate(self) -> None:
        search = self.search_var.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        self.iid_to_record.clear()
        visible = 0
        for index, record in enumerate(self.records):
            result = self.app.results[record.record_id]
            haystack = " ".join([record.title, record.abstract, record.keywords, record.text("AU"), record.text("SO"), result.reason]).casefold()
            if search and search not in haystack:
                continue
            iid = f"r{index}"
            self.iid_to_record[iid] = record
            self.tree.insert("", END, iid=iid, values=(
                "☑" if record.record_id in self.excluded else "☐",
                f"{result.confidence:.0%}", record.text("PY"), record.title,
                record.text("AU"), record.text("SO"), result.reason,
            ))
            visible += 1
        self.summary.configure(text=f"显示 {visible}/{len(self.records)} · 已排除 {len(self.excluded)}")

    def toggle_current(self, event: tk.Event | None = None) -> None:
        if event and getattr(event, "x", None) is not None and self.tree.identify_column(event.x) not in {"#1", ""}:
            if str(event.type) == "4":
                return
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        record = self.iid_to_record[iid]
        if record.record_id in self.excluded:
            self.excluded.remove(record.record_id)
        else:
            self.excluded.add(record.record_id)
        values = list(self.tree.item(iid, "values"))
        values[0] = "☑" if record.record_id in self.excluded else "☐"
        self.tree.item(iid, values=values)
        self.summary.configure(text=f"显示 {len(self.iid_to_record)}/{len(self.records)} · 已排除 {len(self.excluded)}")

    def show_detail(self, _event: tk.Event | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        record = self.iid_to_record[selection[0]]
        result = self.app.results[record.record_id]
        content = (
            f"题名：{record.title or '（无）'}\n\n"
            f"作者：{record.text('AU') or '（无）'}\n"
            f"期刊/年份：{record.text('SO')} / {record.text('PY')}\n"
            f"DOI：{record.text('DI') or '（无）'}\n\n"
            f"关键词：{record.keywords or '（无）'}\n\n"
            f"摘要：\n{record.abstract or '（摘要缺失）'}\n\n"
            f"AI 判断摘要：{result.reason}\n"
            f"缺失信息：{'；'.join(result.missing_information) or '（无）'}\n"
            f"API 错误：{result.error or '（无）'}"
        )
        self.detail.configure(state="normal")
        self.detail.delete("1.0", END)
        self.detail.insert("1.0", content)
        self.detail.configure(state="disabled")

    def select_all(self) -> None:
        self.excluded.update(record.record_id for record in self.iid_to_record.values())
        self.populate()

    def clear_all(self) -> None:
        self.excluded.clear()
        self.populate()

    def sort_by(self, column: str) -> None:
        items = [(self.tree.set(iid, column), iid) for iid in self.tree.get_children("")]
        if column == "confidence":
            items.sort(key=lambda item: float(item[0].rstrip("%") or 0), reverse=True)
        else:
            items.sort(key=lambda item: item[0].casefold())
        for index, (_, iid) in enumerate(items):
            self.tree.move(iid, "", index)

    def finish(self) -> None:
        relevant_count = len(self.records) - len(self.excluded)
        if not messagebox.askyesno(
            "确认完成复核",
            f"将 {len(self.excluded)} 条归为不相关，未勾选的 {relevant_count} 条归为相关，并立即导出。是否继续？",
            parent=self.window,
        ):
            return
        excluded = set(self.excluded)
        self.window.destroy()
        self.app.review_completed(excluded)

    def save_and_close(self) -> None:
        self.app.save_review_draft(self.excluded)
        self.window.destroy()

    def close_without_finish(self) -> None:
        if messagebox.askyesno("保存复核进度", "是否保存当前勾选并关闭？稍后重新打开时会自动恢复。", parent=self.window):
            self.app.save_review_draft(self.excluded)
            self.window.destroy()


class CancelledByUser(Exception):
    pass


def _find_bundled_node(resource_dir: str | Path | None = None) -> tuple[str | None, str | None]:
    if resource_dir:
        embedded = Path(resource_dir) / "runtime" / "node"
        embedded_node = embedded / "node.exe"
        embedded_modules = embedded / "node_modules"
        if embedded_node.exists() and (embedded_modules / "@oai" / "artifact-tool").exists():
            return str(embedded_node), str(embedded_modules)
    runtime = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node"
    node = runtime / "bin" / "node.exe"
    modules = runtime / "node_modules"
    if node.exists() and modules.exists():
        return str(node), str(modules)
    return shutil.which("node"), None


def run_app() -> None:
    root = create_root()
    try:
        root.iconname(APP_TITLE)
    except tk.TclError:
        pass
    WosFilterApp(root)
    root.mainloop()


def create_root() -> tk.Tk:
    """Create a Tk root with native file drop support when TkDnD is available."""
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except (tk.TclError, OSError):
            pass
    return tk.Tk()
