"""Searchable, editable review window for imported bibliography and merge rules."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .vocabulary import merge_vocabulary
from .ai_vocabulary import (DeepSeekVocabularyClient, retained_original_records,
                            stage_suggestions, suggest_category)
from .deepseek import ApiKeyError as DeepSeekApiKeyError, DeepSeekError, load_api_key
from .vocabulary_review import (LABELS, TAG_KINDS, export_pairs_txt, import_pairs_txt,
                                list_merge_pairs, refresh_keyword_canonicals,
                                remove_pair, upsert_pair)
from .merge_scope import annotate_pairs, export_scope_pairs
from . import ui_theme as ui


KINDS = tuple(LABELS)


class ImportPreviewWindow(tk.Toplevel):
    def __init__(self, app) -> None:
        super().__init__(app.root)
        self.app = app
        self.project_id = app.project_data["id"]
        self.draft = deepcopy(app.vocabulary_spec)
        self.draft_report: dict = {}
        self.pairs: dict[str, list[dict]] = {}
        self.selected_pair: dict | None = None
        self._ai_queue: queue.Queue = queue.Queue()
        self._ai_cancel = threading.Event()
        self._ai_busy = False
        self._ai_refreshing = False
        self.title("导入预览与词汇合并核查")
        self.geometry("1120x710")
        self.minsize(820, 520)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._recalculate()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("Preview.TNotebook", background=ui.BG, borderwidth=0)
        style.configure("Preview.TNotebook.Tab", background=ui.BG, foreground=ui.MUTED,
                        padding=(15, 9), font=(ui.FONT, 10))
        style.map("Preview.TNotebook.Tab",
                  background=[("selected", ui.SURFACE), ("active", ui.ACCENT_SOFT)],
                  foreground=[("selected", ui.ACCENT_HOVER)],
                  padding=[("selected", (20, 11))],
                  font=[("selected", (ui.FONT, 11, "bold"))])
        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.pack(fill="x")
        self.target_var = tk.StringVar()
        self.variant_var = tk.StringVar()
        self.search_var = tk.StringVar()
        edit_fields = ttk.Frame(toolbar)
        edit_fields.pack(fill="x")
        for label, variable in (("保留词", self.target_var), ("合并词", self.variant_var)):
            field = ttk.Frame(edit_fields)
            field.pack(side="left", fill="x", expand=True, padx=(0, 12))
            ttk.Label(field, text=label).pack(anchor="w", pady=(0, 4))
            ttk.Entry(field, textvariable=variable).pack(fill="x")
        search_row = ttk.Frame(toolbar)
        search_row.pack(fill="x", pady=(9, 0))
        ttk.Label(search_row, text="检索合并项").pack(side="left", padx=(0, 8))
        ttk.Entry(search_row, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_: self._filter())
        actions = ttk.Frame(self, padding=(10, 0, 10, 7))
        actions.pack(fill="x")
        ttk.Button(actions, text="保存这条合并", command=self._save_pair).pack(side="left")
        ttk.Button(actions, text="删除选中合并", style="Danger.TButton",
                   command=self._delete_pair).pack(side="left", padx=6)
        ttk.Button(actions, text="应用合并到项目", style="Primary.TButton",
                   command=self._apply).pack(side="left", padx=(14, 6))
        self.keyword_refresh_button = ttk.Button(actions, text="刷新关键词保留词",
                                                 command=self._refresh_keyword_targets)
        self.keyword_refresh_button.pack(side="left", padx=6)
        files = ttk.Frame(self, padding=(10, 0, 10, 7))
        files.pack(fill="x")
        file_actions = ttk.Frame(files)
        file_actions.pack(fill="x")
        ttk.Button(file_actions, text="导入当前类别 TXT…",
                   command=self._import_current).pack(side="left")
        ttk.Button(file_actions, text="导出当前类别 TXT…",
                   command=self._export_current).pack(side="left", padx=6)
        ttk.Button(file_actions, text="导出五类 TXT…",
                   command=self._export_all).pack(side="left")
        export_scope = ttk.Frame(files)
        export_scope.pack(fill="x", pady=(7, 0))
        ttk.Label(export_scope, text="导出范围：").pack(side="left", padx=(0, 5))
        self.export_scope_var = tk.StringVar(value="all")
        ttk.Radiobutton(export_scope, text="全部合并词", variable=self.export_scope_var,
                        value="all").pack(side="left")
        ttk.Radiobutton(export_scope, text="当前文件中出现的合并词", variable=self.export_scope_var,
                        value="current").pack(side="left", padx=7)
        ai_bar = ttk.Frame(self, padding=(10, 0, 10, 7))
        ai_bar.pack(fill="x")
        self.ai_button = ttk.Button(ai_bar, text="DeepSeek 分析当前类别", command=self._start_ai)
        self.ai_button.pack(side="left")
        self.ai_stop_button = ttk.Button(ai_bar, text="停止", command=self._stop_ai, state="disabled")
        self.ai_stop_button.pack(side="left", padx=6)
        self.ai_status_var = tk.StringVar(value="仅分析当前类别；有效建议通过校验后自动保存。")
        ttk.Label(ai_bar, textvariable=self.ai_status_var, style="Hint.TLabel").pack(side="left", padx=8)
        self.summary_var = tk.StringVar()
        ttk.Label(self, textvariable=self.summary_var, foreground=ui.MUTED,
                  padding=(10, 0)).pack(fill="x")
        self.tabs = ttk.Notebook(self, style="Preview.TNotebook")
        self.tabs.pack(fill="both", expand=True, padx=10, pady=8)
        self.trees: dict[str, ttk.Treeview] = {}
        self.empty_labels: dict[str, ttk.Label] = {}
        self.overview = self._text_tab("导入概览")
        for kind in KINDS:
            frame = ttk.Frame(self.tabs)
            self.tabs.add(frame, text=LABELS[kind])
            hint = ttk.Label(frame, text="", style="Hint.TLabel")
            hint.pack(side="bottom", anchor="w", padx=8, pady=6)
            self.empty_labels[kind] = hint
            tree = ttk.Treeview(frame, columns=("target", "variant", "scope", "current", "origin", "count", "reason"), show="headings")
            tree.tag_configure("needs_review", foreground="#A45334")
            for column, title, width in (("target", "保留词", 260), ("variant", "合并词", 260),
                                         ("scope", "规则归属", 105), ("current", "当前文件", 85),
                                         ("origin", "来源", 80), ("count", "原始出现次数", 110),
                                         ("reason", "合并依据", 230)):
                tree.heading(column, text=title)
                tree.column(column, width=width, anchor="w" if column in ("target", "variant", "reason") else "center")
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            tree.pack(fill="both", expand=True)
            tree.bind("<<TreeviewSelect>>", lambda _event, category=kind: self._select_pair(category))
            self.trees[kind] = tree
        self.changes = self._text_tab("题录变更明细")
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._update_ai_button()

    def _on_tab_changed(self, _event=None) -> None:
        self._filter()
        self._update_ai_button()

    def _update_ai_button(self) -> None:
        kind = self._category()
        self.ai_button.configure(text=f"DeepSeek 分析{LABELS[kind]}合并" if kind else "DeepSeek 分析当前类别",
                                 state="normal" if kind and not self._ai_busy else "disabled")
        self.keyword_refresh_button.configure(
            state="normal" if kind == "keywords" and not self._ai_busy else "disabled")

    def _text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=title)
        text = tk.Text(frame, wrap="word", font=("Microsoft YaHei UI", 10), state="disabled")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        return text

    @staticmethod
    def _write_text(widget: tk.Text, lines: list[str]) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", "\n".join(lines))
        widget.configure(state="disabled")

    def _category(self) -> str | None:
        index = self.tabs.index(self.tabs.select())
        return KINDS[index - 1] if 1 <= index <= len(KINDS) else None

    def _recalculate(self) -> None:
        try:
            _, self.draft_report = merge_vocabulary(self.app.raw_records, {
                **self.draft, "autoKeywordVariants": self.app.auto_keyword_var.get()})
        except (ValueError, TypeError) as exc:
            messagebox.showerror("合并规则无效", str(exc), parent=self)
            return
        snapshot = self.app.project_data.get("historical_merge_pairs", {})
        self.pairs = {kind: annotate_pairs(
            list_merge_pairs(self.app.raw_records, self.draft, self.draft_report, kind), kind, snapshot)
            for kind in KINDS}
        for index, kind in enumerate(KINDS, 1):
            self.tabs.tab(index, text=f"{LABELS[kind]} {len(self.pairs[kind])}")
        count = sum(len(items) for items in self.pairs.values())
        flagged = sum(row["reason"].startswith("待核查：")
                      for row in self.pairs.get("keywords", []))
        warning = f" · {flagged} 条 AI 合并待核查" if flagged else ""
        self.summary_var.set(f"{len(self.app.raw_records)} 篇题录 · {count} 条合并 · "
                             f"{self.draft_report['affected_record_count']} 篇字段变更" + warning)
        self._filter()

    def _filter(self) -> None:
        needle = self.search_var.get().strip().casefold()
        kind = self._category()
        if kind is not None:
            tree = self.trees[kind]
            tree.delete(*tree.get_children())
            for index, row in enumerate(self.pairs.get(kind, [])):
                if needle and needle not in " ".join(str(value) for value in row.values()).casefold():
                    continue
                tree.insert("", "end", iid=str(index), values=(row["target"], row["variant"],
                            row["catalog_scope"], "是" if row["current_file"] else "否",
                            row["origin"], row["occurrences"], row["reason"]),
                            tags=("needs_review",) if row["reason"].startswith("待核查：") else ())
            self.empty_labels[kind].configure(text=("未发现可安全自动合并的不同写法；可在上方手动添加合并。"
                                                    if not self.pairs.get(kind) else ""))
        elif self.tabs.index(self.tabs.select()) == 0:
            self._write_text(self.overview, self._overview_lines(needle))
        else:
            self._write_text(self.changes, self._change_lines(needle))

    def _overview_lines(self, needle: str) -> list[str]:
        app = self.app
        lines = [f"完整题录 {len(app.raw_records)} 条；已确认去重移除 {len(app.duplicates)} 条。",
                 "历史沿用：打开项目时已有的共享规则；当前文件：合并词在本项目原始题录中出现。",
                 "", "文件来源："]
        lines.extend(f"• {Path(item['path']).name}: {item['source']} / {item['format']}，"
                     f"成功 {item['success']}，失败 {item['failed']}"
                     for item in app.project_data["file_results"])
        lines.extend(["", "合并类别："])
        lines.extend(f"• {LABELS[kind]}：{len(self.pairs.get(kind, []))} 条合并对" for kind in KINDS)
        if app.project_data.get("warnings"):
            lines.extend(["", "导入提示：", *("• " + value for value in app.project_data["warnings"])])
        lines.extend(["", "已确认去重记录："])
        lines.extend(f"• {item.duplicate_record_id} → {item.kept_record_id}（{item.reason}）"
                     for item in app.duplicates)
        lines.extend(["", "同题同年但 DOI 不同："])
        lines.extend(f"• {group['title']} ({group['year']})：{' / '.join(group['dois'])}"
                     for group in app.doi_conflicts)
        return [line for line in lines if not needle or needle in line.casefold()]

    def _change_lines(self, needle: str) -> list[str]:
        lines = []
        for change in self.draft_report.get("changes", []):
            kind_fields = change.get("kind_fields") or {
                TAG_KINDS.get(tag, "countries"): {tag: delta}
                for tag, delta in change["fields"].items()}
            for category, fields in kind_fields.items():
                for tag, delta in fields.items():
                    line = (f"{LABELS[category]} · {change['title']} · {tag}\n"
                            f"  合并前：{' | '.join(delta['before'])}\n"
                            f"  合并后：{' | '.join(delta['after'])}")
                    if not needle or needle in line.casefold():
                        lines.append(line)
        return lines or ["当前检索下没有题录字段变更。"]

    def _select_pair(self, kind: str) -> None:
        tree = self.trees[kind]
        selection = tree.selection()
        if not selection:
            return
        row = self.pairs[kind][int(selection[0])]
        self.selected_pair = {**row, "kind": kind}
        self.target_var.set(row["target"])
        self.variant_var.set(row["variant"])

    def _save_pair(self) -> None:
        kind = self._category()
        if kind is None:
            messagebox.showinfo("选择类别", "请先切换到国家、机构、作者、期刊或关键词页。", parent=self)
            return
        try:
            draft = self.draft
            if self.selected_pair and self.selected_pair["kind"] == kind:
                old = self.selected_pair
                if (old["target"], old["variant"]) != (self.target_var.get().strip(), self.variant_var.get().strip()):
                    draft = remove_pair(draft, kind, old["target"], old["variant"],
                                        automatic=True)
            draft = upsert_pair(draft, kind, self.target_var.get(), self.variant_var.get())
            merge_vocabulary(self.app.raw_records, {**draft, "autoKeywordVariants": self.app.auto_keyword_var.get()})
        except (ValueError, TypeError) as exc:
            messagebox.showerror("无法保存合并", str(exc), parent=self)
            return
        self.draft = draft
        self.selected_pair = None
        self._recalculate()

    def _delete_pair(self) -> None:
        kind = self._category()
        if kind is None or not self.selected_pair or self.selected_pair["kind"] != kind:
            return
        row = self.selected_pair
        self.draft = remove_pair(self.draft, kind, row["target"], row["variant"],
                                 automatic=True)
        self.selected_pair = None
        self.target_var.set("")
        self.variant_var.set("")
        self._recalculate()

    def _apply(self) -> None:
        if self.app.project_data["id"] != self.project_id:
            messagebox.showerror("项目已切换", "请在当前项目重新打开导入预览。", parent=self)
            return
        if self.app.apply_vocabulary_spec(self.draft, parent=self):
            self.draft = deepcopy(self.app.vocabulary_spec)
            self._recalculate()
            note = ("；原 AI 结果已保留并标记需重新分析" if self.app.project_data.get("ai_results_stale")
                    else "；已完成的去重核查及有效 AI 结果保留")
            if self.app.project_data.get("needs_dedupe") and self.app.project_data.get("runs"):
                note += "；去重匹配字段变化，使用去重结果前需重新核查"
            messagebox.showinfo("已应用", "合并规则已保存到当前项目" + note +
                                "。国家、机构、作者、期刊目录自动共享；关键词仅保存于当前项目。", parent=self)

    def _refresh_keyword_targets(self) -> None:
        if self._ai_busy or self._category() != "keywords":
            return
        if self.app.project_data["id"] != self.project_id or self.app.running:
            messagebox.showerror("刷新保留词", "请回到当前项目并等待其他分析结束。", parent=self)
            return
        try:
            draft, changed = refresh_keyword_canonicals(self.draft)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("刷新保留词失败", str(exc), parent=self)
            return
        if draft == self.draft:
            self._recalculate()
            self.ai_status_var.set("关键词保留词已是最新格式。")
            return
        self.selected_pair = None
        self.target_var.set("")
        self.variant_var.set("")
        start, end = self.app._current_year_scope()
        if self.app.project_data["runs"] and self.app._dedupe_ready_for_scope(start, end):
            saved, _ = self.app.apply_ai_vocabulary_spec(draft, "keywords", parent=self, refresh=False)
            if not saved:
                return
            self.draft = deepcopy(self.app.vocabulary_spec)
            self._ai_busy = self._ai_refreshing = True
            self._update_ai_button()
            self.ai_status_var.set("已保存刷新后的关键词，正在更新题录和 VOS 节点预览…")
            summary = f"关键词保留词已刷新并保存；更新 {changed} 组 DeepSeek 规则及自动合并项。"
            generation = self.app.project_data["import_generation"]
            self.app.root.after(10, lambda: self._complete_ai_refresh(
                0, summary, generation, source="关键词规范词刷新"))
        elif not self.app.project_data["runs"]:
            if self.app.apply_vocabulary_spec(draft, parent=self):
                self.draft = deepcopy(self.app.vocabulary_spec)
                self._recalculate()
                self.app.vos_panel.refresh(quiet=True)
                self.ai_status_var.set(f"关键词保留词已刷新并保存；更新 {changed} 组 DeepSeek 规则及自动合并项。")
        else:
            self.draft = draft
            self._recalculate()
            self.ai_status_var.set("关键词保留词已刷新到草稿；当前去重待核查，完成后再应用到项目。")

    def _start_ai(self) -> None:
        if self._ai_busy:
            return
        kind = self._category()
        if kind is None:
            messagebox.showinfo("选择类别", "请先切换到国家、机构、作者、期刊或关键词页。", parent=self)
            return
        if self.app.project_data["id"] != self.project_id or self.app.running:
            messagebox.showerror("DeepSeek 合并", "请在当前项目停止其他分析后重试。", parent=self)
            return
        try:
            retained, start, end = self.app._scoped_records()
            if not self.app._dedupe_ready_for_scope(start, end):
                raise ValueError("请先在当前年份范围完成基础去重。")
            originals = retained_original_records(self.app.raw_records, retained)
            if not originals:
                raise ValueError("当前去重结果没有可分析的题录。")
            key = load_api_key(self.app.app_dir)
        except (ValueError, OSError, DeepSeekApiKeyError) as exc:
            messagebox.showerror("DeepSeek 合并", str(exc), parent=self)
            return
        self._ai_snapshot = (self.project_id, self.app.project_data.get("import_generation"),
                             self.app.year_start_var.get(), self.app.year_end_var.get())
        self._ai_draft_snapshot = deepcopy(self.draft)
        validation_records = list(self.app.raw_records)
        auto_keyword = self.app.auto_keyword_var.get()
        self._ai_busy = True
        self._ai_kind = kind
        self._ai_cancel.clear()
        self._update_ai_button()
        self.ai_stop_button.configure(state="normal")
        self._ai_started_at = time.monotonic()
        self._ai_progress_message = f"从 {len(originals)} 条已去重题录提取{LABELS[kind]}术语，准备发送 DeepSeek…"
        self.ai_status_var.set(self._ai_progress_message)
        self.app._log(f"DeepSeek {LABELS[kind]}合并开始：{len(originals)} 条已去重题录。")

        def worker() -> None:
            try:
                client = DeepSeekVocabularyClient(key)
                suggestions, stats = suggest_category(
                    originals, kind, client, progress=lambda message: self._ai_queue.put(("progress", message)),
                    cancelled=self._ai_cancel.is_set)
                draft, accepted, rejected = stage_suggestions(
                    self._ai_draft_snapshot, validation_records, suggestions,
                    auto_keyword=auto_keyword,
                    progress=lambda message: self._ai_queue.put(("progress", message)),
                    cancelled=self._ai_cancel.is_set)
                self._ai_queue.put(("done", (draft, len(accepted), rejected, stats)))
            except Exception as exc:
                self._ai_queue.put(("error", str(exc)))

        threading.Thread(target=worker, name="deepseek-vocabulary", daemon=True).start()
        self.after(150, self._poll_ai)

    def _poll_ai(self) -> None:
        try:
            while True:
                kind, payload = self._ai_queue.get_nowait()
                if kind == "progress":
                    self._ai_progress_message = payload
                elif kind == "error":
                    self._finish_ai()
                    self.ai_status_var.set(f"DeepSeek 合并失败：{payload}")
                    self.app._log(f"DeepSeek 词汇合并失败：{payload}")
                    messagebox.showerror("DeepSeek 合并", payload, parent=self)
                elif kind == "done":
                    if self._ai_cancel.is_set():
                        self._finish_ai()
                        self.ai_status_var.set("已停止；本次建议未导入。")
                        continue
                    current = (self.app.project_data["id"], self.app.project_data.get("import_generation"),
                               self.app.year_start_var.get(), self.app.year_end_var.get())
                    if current != self._ai_snapshot:
                        self._finish_ai()
                        self.ai_status_var.set("项目或年份范围已变化；本次建议未导入。")
                        continue
                    if self.draft != self._ai_draft_snapshot:
                        self._finish_ai()
                        self.ai_status_var.set("合并草稿已修改；本次建议未覆盖你的更改。")
                        continue
                    draft, accepted_count, rejected, stats = payload
                    saved = False
                    needs_review = False
                    if accepted_count:
                        saved, needs_review = self.app.apply_ai_vocabulary_spec(
                            draft, stats["kind"], parent=self, refresh=False)
                    summary = (f"{LABELS[stats['kind']]}：已分析 {stats['records']} 条题录、{stats['terms']} 个不同术语，"
                               f"共 {stats['batches']} 批；通过 {accepted_count} 条建议，跳过 {rejected} 条。")
                    if saved:
                        summary += ("已自动保存；新重复候选需先复核去重，随后重建 VOS 图谱。" if needs_review
                                    else "已自动保存，VOS 节点预览已更新；已打开的旧图谱需重新生成。")
                    elif accepted_count:
                        summary += "自动保存失败；建议仍在当前草稿，可核查后手动应用。"
                    else:
                        summary += "没有新增可保存的合并。"
                    if saved:
                        self._ai_refreshing = True
                        self.ai_stop_button.configure(state="disabled")
                        self.ai_status_var.set("规则已保存，正在更新题录、共享目录和 VOS 预览…")
                        generation = self.app.project_data["import_generation"]
                        self.app.root.after(10, lambda: self._complete_ai_refresh(0, summary, generation))
                        continue
                    self._finish_ai()
                    self.draft = draft
                    self.selected_pair = None
                    if accepted_count:
                        self._recalculate()
                    self.ai_status_var.set(summary)
                    self.app._log(f"DeepSeek 词汇合并：{summary}")
        except queue.Empty:
            pass
        if self._ai_busy and not self._ai_refreshing:
            elapsed = int(time.monotonic() - self._ai_started_at)
            message = ("已请求停止，等待当前请求结束" if self._ai_cancel.is_set()
                       else self._ai_progress_message)
            self.ai_status_var.set(f"{message} · 已用时 {elapsed} 秒")
            self.after(150, self._poll_ai)

    def _complete_ai_refresh(self, step: int, summary: str, generation: int, *,
                             source: str = "DeepSeek 词汇合并") -> None:
        current = (self.app.project_data["id"] == self.project_id
                   and self.app.project_data.get("import_generation") == generation)
        if not current:
            if self.winfo_exists():
                self._finish_ai()
                self.ai_status_var.set("项目已切换；规则已保存，重新打开项目后查看更新结果。")
            return
        steps = (self.app._refresh_import_state, self.app._remember_merge_catalog,
                 lambda: self.app.vos_panel.refresh(quiet=True))
        try:
            if step < len(steps):
                steps[step]()
                self.app.root.after(10, lambda: self._complete_ai_refresh(
                    step + 1, summary, generation, source=source))
                return
            if self.winfo_exists():
                self.draft = deepcopy(self.app.vocabulary_spec)
                self.selected_pair = None
                self._recalculate()
                self._finish_ai()
                self.ai_status_var.set(summary)
            self.app._log(f"{source}：{summary}")
        except Exception as exc:
            self.app._log(f"合并规则已保存，但界面刷新失败：{exc}")
            if self.winfo_exists():
                self._finish_ai()
                self.ai_status_var.set("规则已保存，但界面刷新失败；请重新打开项目。")
                messagebox.showerror("刷新失败", str(exc), parent=self)

    def _finish_ai(self) -> None:
        self._ai_busy = False
        self._ai_refreshing = False
        self._update_ai_button()
        self.ai_stop_button.configure(state="disabled")

    def _stop_ai(self) -> None:
        self._ai_cancel.set()
        self.ai_status_var.set("已请求停止；等待当前 DeepSeek 请求结束。")

    def _close(self) -> None:
        self._ai_cancel.set()
        self.destroy()

    def _import_current(self) -> None:
        kind = self._category()
        if kind is None:
            messagebox.showinfo("选择类别", "请先切换到国家、机构、作者、期刊或关键词页。", parent=self)
            return
        path = filedialog.askopenfilename(parent=self, title=f"导入{LABELS[kind]}合并文件",
                                          filetypes=[("合并文本", "*.txt *.tsv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            draft, count = import_pairs_txt(self.draft, kind, path, self.app.raw_records,
                                            auto_keyword=self.app.auto_keyword_var.get())
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("导入合并文件失败", str(exc), parent=self)
            return
        self.draft = draft
        self.selected_pair = None
        self._recalculate()
        self.app._log(f"已导入{LABELS[kind]}合并文件草稿：{Path(path).name}，{count} 条规则。")
        messagebox.showinfo("导入完成", f"已载入 {count} 条{LABELS[kind]}合并规则。请核查后点击“应用合并到项目”。", parent=self)

    def _export_current(self) -> None:
        kind = self._category()
        if kind is None:
            messagebox.showinfo("选择类别", "请先切换到要导出的合并类别。", parent=self)
            return
        destination = filedialog.asksaveasfilename(parent=self, title="导出合并文件",
            initialfile=f"合并_{LABELS[kind]}_{datetime.now():%y%m%d%H%M}.txt",
            defaultextension=".txt", filetypes=[("文本文件", "*.txt")])
        if destination:
            try:
                export_pairs_txt(destination, export_scope_pairs(self.pairs[kind], self.export_scope_var.get()))
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self)
                return
            messagebox.showinfo("导出完成", destination, parent=self)

    def _export_all(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="选择五类合并文件的导出位置")
        if not folder:
            return
        try:
            for kind in KINDS:
                export_pairs_txt(Path(folder) / f"合并_{LABELS[kind]}.txt",
                                 export_scope_pairs(self.pairs[kind], self.export_scope_var.get()))
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        messagebox.showinfo("导出完成", f"已导出五个 TXT 文件：\n{folder}", parent=self)
