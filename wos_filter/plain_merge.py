"""Independent format conversion and concatenation, without project rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .basic_export import default_export_name, default_output_dir, export_basic_records
from .file_drop import expand_bibliography_inputs
from .source_import import import_one

try:
    from tkinterdnd2 import DND_FILES
except (ImportError, OSError):
    DND_FILES = None


@dataclass
class PlainMergeResult:
    destination: Path
    records: int
    files: list[tuple[str, str, int, int]]
    warnings: list[str]


def merge_files_without_changes(paths: list[str], destination: str | Path) -> PlainMergeResult:
    """Convert and append every complete record; keep duplicates and source terms."""
    files, warnings = expand_bibliography_inputs(paths)
    if not files:
        raise ValueError("没有可转换的题录文件。")
    records = []
    summaries = []
    for path in files:
        try:
            imported, summary, notes = import_one(path)
        except (OSError, ValueError) as exc:
            summaries.append((Path(path).name, "未识别", 0, 1))
            warnings.append(f"{Path(path).name}：{exc}")
            continue
        valid = [record for record in imported if record.title.strip()]
        failed = len(imported) - len(valid)
        records.extend(valid)
        summaries.append((Path(path).name, summary.source, len(valid), failed))
        warnings.extend(notes)
        if failed:
            warnings.append(f"{Path(path).name}：{failed} 条题录缺少题名，未写入结果。")
    if not records:
        raise ValueError("文件中没有可导出的完整题录；请检查识别结果。")
    output = export_basic_records(destination, records, "")
    return PlainMergeResult(output, len(records), summaries, warnings)


class PlainMergeWindow(tk.Toplevel):
    def __init__(self, app) -> None:
        super().__init__(app.root)
        self.app = app
        self.paths: list[str] = []
        self._busy = False
        self.title("仅转换格式并合并文件")
        self.geometry("780x560")
        self.minsize(650, 360)
        self.transient(app.root)
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        ttk.Label(outer, text="仅转换格式并合并文件", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        description = ttk.Label(outer, text="逐个识别文件，按清单顺序写入同一个 WoS 兼容 TXT。保留重复题录与原始词语，不使用项目或历史合并规则。")
        description.grid(row=1, column=0, sticky="ew", pady=(5, 12))
        outer.bind("<Configure>", lambda event: description.configure(wraplength=max(300, event.width - 8)))
        bar = ttk.Frame(outer)
        bar.grid(row=2, column=0, sticky="ew")
        self.add_button = ttk.Button(bar, text="添加文件…", command=self._add_files)
        self.add_button.pack(side="left")
        self.folder_button = ttk.Button(bar, text="添加文件夹…", command=self._add_folder)
        self.folder_button.pack(side="left", padx=6)
        self.remove_button = ttk.Button(bar, text="移除选中", command=self._remove)
        self.remove_button.pack(side="left", padx=6)
        self.clear_button = ttk.Button(bar, text="清空", command=self._clear)
        self.clear_button.pack(side="left")
        frame = ttk.Frame(outer)
        frame.grid(row=3, column=0, sticky="nsew", pady=(12, 8))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("number", "name", "folder"), show="headings",
                                 selectmode="extended", height=4)
        for key, title, width in (("number", "序号", 55), ("name", "文件名", 300), ("folder", "所在文件夹", 380)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.status = tk.StringVar(value="尚未添加文件。")
        status = ttk.Label(outer, textvariable=self.status)
        status.grid(row=4, column=0, sticky="ew", pady=(0, 9))
        outer.bind("<Configure>", lambda event: status.configure(wraplength=max(300, event.width - 8)), add="+")
        self.export_button = ttk.Button(outer, text="选择位置并导出…", command=self._export)
        self.export_button.grid(row=5, column=0, sticky="e")
        if DND_FILES is not None and hasattr(self, "drop_target_register"):
            for widget in (self, outer, frame, self.tree):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
        self.update_idletasks()
        self.minsize(650, max(360, outer.winfo_reqheight() + 36))

    def _on_drop(self, event) -> str | None:
        if not self._busy:
            raw_data = getattr(event, "data", "")
            if Path(raw_data).exists():
                paths = [raw_data]
            else:
                try:
                    paths = list(self.tk.splitlist(raw_data))
                except tk.TclError:
                    paths = [raw_data] if raw_data else []
            self._add(paths)
        return getattr(event, "action", None)

    def _add(self, items: list[str]) -> None:
        expanded, warnings = expand_bibliography_inputs(items)
        known = {str(Path(item).resolve()).casefold() for item in self.paths}
        for item in expanded:
            if item.casefold() not in known:
                self.paths.append(item)
                known.add(item.casefold())
        self._refresh()
        if warnings:
            self.status.set(f"已添加 {len(self.paths)} 个文件；" + "；".join(warnings[:2]))

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, title="选择题录文件",
            filetypes=[("题录文件", "*.txt *.ris *.bib *.csv *.xls *.nbib"), ("所有文件", "*.*")])
        self._add(list(paths))

    def _add_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择题录文件夹")
        if path:
            self._add([path])

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, path in enumerate(self.paths):
            item = Path(path)
            self.tree.insert("", "end", iid=str(index), values=(index + 1, item.name, str(item.parent)))
        self.status.set(f"已添加 {len(self.paths)} 个文件；导出时显示每个文件的成功和失败数。")

    def _remove(self) -> None:
        selected = {int(item) for item in self.tree.selection()}
        self.paths = [path for index, path in enumerate(self.paths) if index not in selected]
        self._refresh()

    def _clear(self) -> None:
        self.paths.clear()
        self._refresh()

    def _export(self) -> None:
        if not self.paths or self._busy:
            return
        initial = default_output_dir(self.app.app_dir)
        destination = filedialog.asksaveasfilename(parent=self, title="导出 WoS 兼容题录 TXT",
            initialdir=str(initial), initialfile=default_export_name(),
            defaultextension=".txt", filetypes=[("文本文件", "*.txt")])
        if not destination:
            return
        self._busy = True
        for button in (self.add_button, self.folder_button, self.remove_button, self.clear_button, self.export_button):
            button.configure(state="disabled")
        self.status.set("正在转换文件并写入结果…")
        paths = list(self.paths)

        def worker() -> None:
            try:
                result = merge_files_without_changes(paths, destination)
                self.after(0, lambda: self._finish(result, None))
            except (OSError, ValueError) as exc:
                self.after(0, lambda error=str(exc): self._finish(None, error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result: PlainMergeResult | None, error: str | None) -> None:
        if not self.winfo_exists():
            return
        self._busy = False
        for button in (self.add_button, self.folder_button, self.remove_button, self.clear_button, self.export_button):
            button.configure(state="normal")
        if error:
            self.status.set("导出失败：" + error)
            messagebox.showerror("导出失败", error, parent=self)
            return
        assert result is not None
        lines = [f"{name} · {source}：成功 {success}，失败 {failed}"
                 for name, source, success, failed in result.files]
        self.status.set(f"已导出 {result.records} 条题录 → {result.destination}")
        self.app._log(f"仅转换格式并合并：{len(result.files)} 个文件，{result.records} 条题录，已导出 {result.destination}")
        messagebox.showinfo("合并完成", f"共导出 {result.records} 条题录。\n\n" +
            "\n".join(lines[:15]) + ("\n…" if len(lines) > 15 else "") +
            ("\n\n提示：" + "；".join(result.warnings[:3]) if result.warnings else ""), parent=self)
