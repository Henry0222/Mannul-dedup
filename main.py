import sys
import os
import shutil
import traceback
import json
from pathlib import Path

from wos_filter.gui import create_root, run_app


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--vosviewer":
        from wos_filter.vos_viewer import run_viewer

        run_viewer(sys.argv[2])
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test-vos-launch":
        from wos_filter.vos_viewer import launch_viewer

        launch_viewer(sys.argv[2])
        raise SystemExit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == "--self-test-import":
        target = Path(sys.argv[2]).resolve()
        target.mkdir(parents=True, exist_ok=True)
        try:
            from wos_filter.source_import import parse_many_sources
            from wos_filter.vocabulary import merge_vocabulary
            from wos_filter.wos import deduplicate, find_doi_conflicts

            parsed = parse_many_sources(sys.argv[3:])
            normalized, vocabulary = merge_vocabulary(parsed.records)
            unique, duplicates = deduplicate(normalized)
            result = {
                "files": [{"name": Path(item.path).name, "source": item.source,
                           "format": item.format, "records": item.count} for item in parsed.imports],
                "records": len(parsed.records), "unique": len(unique),
                "duplicates": len(duplicates),
                "automatic_keyword_groups": len(vocabulary["automatic_keyword_groups"]),
                "doi_conflicts": len(find_doi_conflicts(normalized)),
                "warnings": parsed.warnings,
            }
            (target / "IMPORT_SELFTEST_OK.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(0)
        except Exception:
            (target / "IMPORT_SELFTEST_FAILED.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise SystemExit(5)
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test-gui":
        target = Path(sys.argv[2]).resolve()
        target.mkdir(parents=True, exist_ok=True)
        os.environ["MANNUL_WORKSPACE_ROOT"] = str(target / "projects")
        os.environ["MANNUL_GUI_SELFTEST_DIR"] = str(target)
        try:
            from wos_filter.gui import PROVIDER_DEEPSEEK, PROVIDER_HYBRID, PROVIDER_JEV, WosFilterApp

            root = create_root()
            root.withdraw()
            app = WosFilterApp(root)
            if not app.drop_enabled:
                raise RuntimeError("文件选择框没有启用题录文件拖放")
            if not app.project_ids or app.literature_tree is not None or not hasattr(app, "source_tree"):
                raise RuntimeError("项目工作区、题录列表或来源统计未初始化")
            if len(app.tabs.tabs()) != 4 or "dedupe" not in app.source_tree.cget("columns"):
                raise RuntimeError("第三界面或来源去重统计列未初始化")
            from tkinter import ttk
            main_style = ttk.Style(root)
            if (main_style.lookup("Main.TNotebook.Tab", "padding", ("selected",)) ==
                    main_style.lookup("Main.TNotebook.Tab", "padding", ())):
                raise RuntimeError("主界面当前标签没有放大")
            if not hasattr(app, "vos_panel") or app.vos_panel.kind_var.get() != "关键词共现":
                raise RuntimeError("VOS 图谱入口或默认类型未初始化")
            from wos_filter.vos_network import NETWORK_TYPES
            if not {"期刊", "被引作者", "被引期刊", "被引文章"}.issubset(NETWORK_TYPES.values()):
                raise RuntimeError("VOS 新增图谱类型未初始化")
            if not hasattr(app, "plot_workbench") or not hasattr(app.plot_workbench, "chart"):
                raise RuntimeError("统一图表工作台未初始化")
            if app.plot_workbench.trend_group_var.get() != "language":
                raise RuntimeError("年度趋势没有默认按题名语言分组")
            if not {"gradient_combo", "bilingual_quadratic"}.issubset(
                    app.plot_workbench._type_choices()):
                raise RuntimeError("两种新增年度图样式未显示")
            if hasattr(app, "skip_dedupe_var"):
                raise RuntimeError("已移除的跳过去重开关仍显示")
            if len(app.plot_workbench.content_tabs.tabs()) != 2 or hasattr(app.plot_workbench, "alias_text"):
                raise RuntimeError("图表预览、绘图数据或重复术语规则界面不正确")
            if app.log_text.master.master in (app.tabs.nametowidget(app.tabs.tabs()[1]),
                                              app.tabs.nametowidget(app.tabs.tabs()[2])):
                raise RuntimeError("运行日志未固定在标签页下方")
            if tuple(app.dedupe_mode_combo.cget("values")) != ("严格", "宽松", "期刊", "快速"):
                raise RuntimeError("四种去重模式未显示")
            if not all(hasattr(app, name) for name in ("one_click_dedupe", "apply_year_scope", "export_basic")):
                raise RuntimeError("一键去重、年份范围或基础结果导出未初始化")
            if not app.auto_keyword_var.get() or not hasattr(app, "preview_import"):
                raise RuntimeError("导入预览或关键词自动合并没有启用")
            if app.provider_var.get() != PROVIDER_HYBRID:
                raise RuntimeError("默认后端不是 Jev + DeepSeek 复核")
            if app.workers_var.get() != 16:
                raise RuntimeError("默认并发数不是 16")
            if "DeepSeek 复核最多 8" not in app.model_label.cget("text"):
                raise RuntimeError("混合模式没有显示 DeepSeek 复核并发上限 8")
            key_text = app.key_label.cget("text")
            if "typesafe_api_key.txt" not in key_text or "api_key.txt" not in key_text:
                raise RuntimeError("混合模式没有显示两套 API Key 路径")
            app.provider_var.set(PROVIDER_JEV)
            app._on_provider_changed()
            if "typesafe_api_key.txt" not in app.key_label.cget("text") or "jev" not in app.model_label.cget("text").lower():
                raise RuntimeError("Jev 后端或 Key 路径切换异常")
            app.provider_var.set(PROVIDER_DEEPSEEK)
            app._on_provider_changed()
            if "api_key.txt" not in app.key_label.cget("text") or "deepseek" not in app.model_label.cget("text").lower():
                raise RuntimeError("DeepSeek 后端或 Key 路径切换异常")
            from wos_filter.dedupe_review import apply_review, auto_review_run, new_run
            from wos_filter.models import Classification, WosRecord
            from wos_filter.project_workspace import record_to_json

            wos = WosRecord({"TI": ["A study"], "PY": ["2024"], "AU": ["Li, A"],
                             "SO": ["Journal"], "DI": ["10.1000/same"],
                             "DE": ["aquatic systems; water systems; other topic"],
                             "C1": ["Peking Univ, Beijing, Peoples R China"]},
                            "PT J\nTI A study\nPY 2024\nAU Li, A\nSO Journal\nDI 10.1000/same\nDE aquatic systems; water systems; other topic\nER",
                            "wos.txt", 1, source_kind="wos")
            scopus = WosRecord({"TI": ["A study"], "PY": ["2024"], "AU": ["Li, A"],
                                "SO": ["Journal"], "DI": ["10.1000/same"]},
                               "PT J\nTI A study\nPY 2024\nAU Li, A\nSO Journal\nDI 10.1000/same\nER",
                               "scopus.ris", 1, source_kind="scopus")
            app.raw_records = [wos, scopus]
            app.project_data["records"] = [record_to_json(item) for item in app.raw_records]
            app.project_data["files"] = [r"C:\sample\wos.txt", r"E:\sample\scopus.ris"]
            app.project_data["import_generation"] = 1
            app.project_data["file_results"] = [
                {"path": "wos.txt", "source": "wos", "format": "wos_tagged", "uploaded": 1,
                 "success": 1, "failed": 0, "failed_files": 0},
                {"path": "scopus.ris", "source": "scopus", "format": "ris", "uploaded": 1,
                 "success": 1, "failed": 0, "failed_files": 0},
            ]
            app._refresh_import_state()
            from wos_filter.analysis_scope import RAW_SCOPE
            if app.plot_workbench.scope_var.get() != RAW_SCOPE or app.vos_panel.scope_var.get() != RAW_SCOPE:
                raise RuntimeError("未去重项目没有自动切换到全部导入题录")
            app.plot_workbench.refresh()
            if sum(row["papers"] for row in app.plot_workbench.rows) != 2:
                raise RuntimeError("未去重题录未进入发文分析")
            app.vos_panel.min_var.set("1")
            app.vos_panel.refresh(quiet=True)
            if app.vos_panel.data is None:
                raise RuntimeError("未去重题录未进入 VOS 建图")
            run = new_run(app.records, "quick")
            run["import_generation"] = app.project_data["import_generation"]
            auto_review_run(run, app.records)
            app.project_data["runs"].append(run)
            apply_review(app.project_data, run)
            app.project_data["needs_dedupe"] = False
            app.workspace_store.save(app.project_data)
            from wos_filter.analysis_scope import DEDUPE_SCOPE
            app.plot_workbench.scope_var.set(DEDUPE_SCOPE)
            app.vos_panel.scope_var.set(DEDUPE_SCOPE)
            app._refresh_import_state()
            if {key: var.get() for key, var in app.metric_values.items()} != {
                    "imported": "2", "removed": "1", "retained": "1"}:
                raise RuntimeError("项目题录数字卡片与已应用的去重结果不一致")
            source_rows = [app.source_tree.item(item, "values") for item in app.source_tree.get_children()]
            if not any(row[0] == "Scopus" and row[-1] == "去重 1 · 保留 0" for row in source_rows):
                raise RuntimeError("Scopus 来源去重统计不正确")
            if (len(app.records) != 1 or not app._dedupe_ready_for_scope(None, None) or
                    str(app.find_dedupe_button.cget("state")) == "disabled"):
                raise RuntimeError("基础去重结果或按钮状态不正确")
            app.plot_workbench.refresh()
            if sum(row["papers"] for row in app.plot_workbench.rows) != 1:
                raise RuntimeError("基础去重结果没有正确进入年度趋势")
            app.plot_workbench.label_size_var.set("15")
            app.plot_workbench.load_settings({"label_interval": "0"})
            if app.plot_workbench.label_interval_var.get() != "1":
                raise RuntimeError("旧项目自动避让设置没有迁移为逐年显示")
            app.plot_workbench.load_settings({"label_interval": "0", "label_interval_version": 2})
            app.plot_workbench.label_interval_var.set("0")
            chart_options = app.plot_workbench._plot_options()
            if chart_options.label_size != 15 or chart_options.label_interval != 0:
                raise RuntimeError("年度数值标签的字号或自动间隔没有进入绘图设置")
            if not app.plot_workbench.legend_name_vars:
                raise RuntimeError("图例名称编辑框没有生成")
            edited_legend = next(iter(app.plot_workbench.legend_name_vars))
            app.plot_workbench.legend_name_vars[edited_legend].set("Publication count")
            app.plot_workbench._render()
            if "Publication count" not in [item.get_text() for item in
                                             app.plot_workbench.figure.axes[0].get_legend().get_texts()]:
                raise RuntimeError("图例名称修改未应用到预览")
            app.vos_panel.min_var.set("1")
            app.vos_panel.refresh(quiet=True)
            if app.vos_panel.data is None:
                raise RuntimeError("基础去重结果没有进入 VOS 节点预览")
            from wos_filter import gui as gui_module
            original_file_dialog = gui_module.filedialog.asksaveasfilename
            original_info_dialog = gui_module.messagebox.showinfo
            try:
                gui_module.messagebox.showinfo = lambda *args, **kwargs: None
                gui_module.filedialog.asksaveasfilename = lambda *args, **kwargs: str(target / "deduped-records.txt")
                app.export_basic()
            finally:
                gui_module.filedialog.asksaveasfilename = original_file_dialog
                gui_module.messagebox.showinfo = original_info_dialog
            from wos_filter.wos import parse_wos_file
            if len(parse_wos_file(target / "deduped-records.txt").records) != 1:
                raise RuntimeError("基础导出未遵循去重结果")
            app._save_project_view()
            app._load_project(app.project_data["id"])
            if len(app.records) != 1 or len(app.project_data["runs"]) != 1:
                raise RuntimeError("重新加载后没有恢复去重记录")
            if app.plot_workbench.legend_names.get(edited_legend) != "Publication count":
                raise RuntimeError("图例名称没有随项目保存")
            if app.plot_workbench.label_interval_var.get() != "0":
                raise RuntimeError("新版本用户主动选择自动避让没有保存")
            app.plot_workbench.refresh()
            if len(app.plot_workbench.rows) != 1 or app.plot_workbench.rows[0]["papers"] != 1:
                raise RuntimeError("基础去重后的年度发文量不正确")
            app.results = {app.records[0].record_id: Classification(app.records[0].record_id,
                            "relevant", 1, "GUI 自检")}
            app.metrics_scope_var.set("AI 筛选相关")
            app.plot_workbench.view_var.set("countries")
            app.plot_workbench.refresh()
            if len(app.plot_workbench.rows) != 1 or app.plot_workbench.rows[0]["papers"] != 1:
                raise RuntimeError("AI 相关记录的国家排名不正确")
            from wos_filter.chart_export import PALETTES
            from wos_filter import gui as gui_module
            app.plot_workbench.palette_var.set("色盲友好")
            app.plot_workbench._apply_palette()
            app._save_project_view()
            app._load_project(app.project_data["id"])
            file_rows = [app.file_tree.item(item, "values") for item in app.file_tree.get_children()]
            if [row[1] for row in file_rows] != ["wos.txt", "scopus.ris"]:
                raise RuntimeError("题录文件列表没有只显示文件名")
            app.open_literature_list()
            if app.literature_tree is None or len(app.literature_tree.get_children()) != 2:
                raise RuntimeError("独立题录窗口没有显示当前项目题录")
            app._close_literature_list()
            from wos_filter.plain_merge import PlainMergeWindow
            plain_window = PlainMergeWindow(app)
            if plain_window.paths or str(plain_window.export_button.cget("state")) != "normal":
                raise RuntimeError("独立格式转换合并窗口未正确初始化")
            plain_window.geometry("650x360")
            root.update()
            if plain_window.export_button.winfo_y() + plain_window.export_button.winfo_height() > plain_window.winfo_height():
                raise RuntimeError("独立合并窗口缩小时遮挡了导出按钮")
            if app.drop_enabled and not plain_window.tree.dnd_bind("<<Drop>>"):
                raise RuntimeError("独立合并窗口的文件区未启用拖放")
            drop_sample = target / "plain-merge-drop.ris"
            drop_sample.write_text("TY  - JOUR\nTI  - Dropped record\nPY  - 2024\nER  -\n", encoding="utf-8")
            from types import SimpleNamespace
            plain_window._on_drop(SimpleNamespace(data=str(drop_sample)))
            if plain_window.paths != [str(drop_sample.resolve())]:
                raise RuntimeError(f"独立合并窗口没有接收拖入的题录文件：{plain_window.paths!r}")
            plain_window.destroy()
            original_scale = root.tk.call("tk", "scaling")
            try:
                root.tk.call("tk", "scaling", 2.0)
                scaled_window = PlainMergeWindow(app)
                scaled_window.geometry("650x360")
                root.update()
                if (scaled_window.export_button.winfo_y() + scaled_window.export_button.winfo_height()
                        > scaled_window.winfo_height()):
                    raise RuntimeError("高缩放下独立合并窗口遮挡了导出按钮")
                scaled_window.destroy()
            finally:
                root.tk.call("tk", "scaling", original_scale)
            from wos_filter.import_preview import ImportPreviewWindow
            preview = ImportPreviewWindow(app)
            if len(preview.tabs.tabs()) != 7:
                raise RuntimeError("导入预览没有列出五类合并和题录变更")
            if (preview.export_scope_var.get() != "all" or
                    "scope" not in preview.trees["countries"].cget("columns")):
                raise RuntimeError("历史规则归属或合并词导出范围未初始化")
            from tkinter import ttk
            preview_style = ttk.Style(root)
            if (preview_style.lookup("Preview.TNotebook.Tab", "padding", ("selected",)) ==
                    preview_style.lookup("Preview.TNotebook.Tab", "padding", ())):
                raise RuntimeError("导入预览的当前标签没有放大")
            from wos_filter import import_preview as preview_module
            original_load_key = preview_module.load_api_key
            original_client = preview_module.DeepSeekVocabularyClient
            seen_vocabulary_kinds = []
            class FakeVocabularyClient:
                def __init__(self, _key):
                    pass

                def suggest(self, kind, batch, _anchors):
                    seen_vocabulary_kinds.append(kind)
                    terms = {row["term"] for row in batch}
                    if kind == "keywords" and {"aquatic systems", "water systems"} <= terms:
                        return {"merges": [{"keep": "aquatic systems", "merge": "water systems",
                                            "reason": "GUI 自检"}]}
                    return {"merges": []}
            try:
                preview_module.load_api_key = lambda _app_dir: "self-test-key"
                preview_module.DeepSeekVocabularyClient = FakeVocabularyClient
                preview.tabs.select(5)  # 0 overview, then the five vocabulary categories.
                root.update()
                preview._start_ai()
                import time
                deadline = time.monotonic() + 10
                while preview._ai_busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                if preview._ai_busy or not preview.draft.get("aiPairSources"):
                    raise RuntimeError("DeepSeek 合并建议未进入可编辑草稿")
                if seen_vocabulary_kinds != ["keywords"]:
                    raise RuntimeError(f"DeepSeek 调用了非当前类别：{seen_vocabulary_kinds}")
                if (not app.project_data.get("vocabulary_spec", {}).get("aiPairSources")
                        or app.project_data["needs_dedupe"] or len(app.project_data["excluded_keys"]) != 1):
                    raise RuntimeError("DeepSeek 建议未自动保存，或不必要地清除了已完成去重")
                if "已自动保存" not in preview.ai_status_var.get():
                    raise RuntimeError("DeepSeek 完成状态未说明自动保存")
                persisted = app.workspace_store.load(app.project_data["id"])
                if not persisted.get("vocabulary_spec", {}).get("aiPairSources"):
                    raise RuntimeError("DeepSeek 建议未持久保存到项目")
                if "water systems" in app.records[0].values("DE"):
                    raise RuntimeError("DeepSeek 合并未更新项目保留题录")
                app.vos_panel.min_var.set("1")
                app.vos_panel.refresh(quiet=True)
                vos_labels = {row["label"] for row in app.vos_panel.data["network"]["items"]}
                if "water systems" in vos_labels or "Aquatic systems" not in vos_labels:
                    raise RuntimeError("VOS 节点仍使用 DeepSeek 合并前的关键词")
                preview.keyword_refresh_button.invoke()
                deadline = time.monotonic() + 10
                while preview._ai_refreshing and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                if (preview._ai_refreshing or
                        preview.draft.get("keywordCanonicalStyle") != "sentence" or
                        app.project_data.get("vocabulary_spec", {}).get("keywordCanonicalStyle") != "sentence"):
                    raise RuntimeError("关键词保留词刷新未保存到当前项目")
            finally:
                preview_module.load_api_key = original_load_key
                preview_module.DeepSeekVocabularyClient = original_client
            preview.destroy()
            from copy import deepcopy
            retained_id = app.records[0].record_id
            saved_result = Classification(retained_id, "relevant", 1, "保留结果自检")
            app.results = {retained_id: saved_result}
            app.project_data["ai_results"] = {retained_id: saved_result.to_dict()}
            app.project_data["ai_import_generation"] = app.project_data["import_generation"]
            app.project_data["ai_year_start"] = app.year_start_var.get()
            app.project_data["ai_year_end"] = app.year_end_var.get()
            app.workspace_store.save(app.project_data)
            prior_runs = len(app.project_data["runs"])
            prior_exclusions = list(app.project_data["excluded_keys"])
            if not app.apply_vocabulary_spec(deepcopy(app.vocabulary_spec)):
                raise RuntimeError("重复应用相同合并规则失败")
            if (len(app.project_data["runs"]) != prior_runs or
                    app.project_data["excluded_keys"] != prior_exclusions or
                    app.project_data.get("ai_results_stale") or
                    set(app.results) != {retained_id}):
                raise RuntimeError("无影响的词汇规则仍清除了去重或 AI 结果")
            if app.plot_workbench.color_vars["papers"].get() != PALETTES["色盲友好"]["trend"].upper():
                raise RuntimeError("项目图表配色没有保存")
            app.plot_workbench.view_combo.set("关键词排名")
            app.plot_workbench.view_combo.event_generate("<<ComboboxSelected>>")
            root.update()
            if app.plot_workbench.view_var.get() != "keywords":
                raise RuntimeError("图表分析对象切换没有生效")
            app.plot_workbench.view_var.set("trend")
            app.plot_workbench.refresh()
            app.plot_workbench.width_var.set("5")
            app.plot_workbench.height_var.set("4")
            app.plot_workbench.dpi_var.set("72")
            original_file_dialog = gui_module.filedialog.asksaveasfilename
            original_info_dialog = gui_module.messagebox.showinfo
            try:
                gui_module.messagebox.showinfo = lambda *args, **kwargs: None
                gui_module.filedialog.asksaveasfilename = lambda *args, **kwargs: str(target / "trend.png")
                app.plot_workbench.export_png()
                app.plot_workbench.view_var.set("countries")
                app.plot_workbench.refresh()
                gui_module.filedialog.asksaveasfilename = lambda *args, **kwargs: str(target / "ranking.png")
                app.plot_workbench.export_png()
                gui_module.filedialog.asksaveasfilename = lambda *args, **kwargs: str(target / "ranking.csv")
                app.plot_workbench.export_csv()
            finally:
                gui_module.filedialog.asksaveasfilename = original_file_dialog
                gui_module.messagebox.showinfo = original_info_dialog
            if not all((target / name).is_file() for name in ("trend.png", "ranking.png", "ranking.csv")):
                raise RuntimeError("图表 PNG 或绘图数据 CSV 导出失败")
            from matplotlib.backend_bases import MouseEvent
            from wos_filter.plot_layout import layout_artists
            canvas = app.plot_workbench.chart
            canvas.draw()
            title_artist = next((item for item in layout_artists(app.plot_workbench.figure)[0]
                                 if item[1].get_text() == "国家发文量排名"), None)
            if title_artist is None:
                raise RuntimeError("图表标题不可选中")
            bounds = title_artist[1].get_window_extent(canvas.get_renderer())
            cx, cy = int((bounds.x0 + bounds.x1) / 2), int((bounds.y0 + bounds.y1) / 2)
            app.plot_workbench._drag_start(MouseEvent("button_press_event", canvas, cx, cy, button=1))
            app.plot_workbench._drag_move(MouseEvent("motion_notify_event", canvas, cx + 25, cy + 15, button=1))
            app.plot_workbench._drag_end(MouseEvent("button_release_event", canvas, cx + 25, cy + 15, button=1))
            if title_artist[0] not in app.plot_workbench.text_offsets:
                raise RuntimeError("图表元素拖动位置没有保存")
            try:
                gui_module.messagebox.showinfo = lambda *args, **kwargs: None
                gui_module.filedialog.asksaveasfilename = lambda *args, **kwargs: str(target / "dragged.png")
                app.plot_workbench.export_png()
            finally:
                gui_module.filedialog.asksaveasfilename = original_file_dialog
                gui_module.messagebox.showinfo = original_info_dialog
            if not (target / "dragged.png").is_file():
                raise RuntimeError("拖动后的图表导出失败")
            root.deiconify()
            for size in ("1360x850", "1050x680"):
                root.geometry(size)
                for tab in app.tabs.tabs():
                    app.tabs.select(tab)
                    root.update()
                    if app.tabs.index(tab) in (2, 3):
                        if app.log_box.winfo_ismapped():
                            raise RuntimeError("图表界面仍显示运行日志")
                    else:
                        if not app.log_text.winfo_ismapped() or app.log_text.winfo_height() < 40:
                            raise RuntimeError("运行日志在工作界面不可见")
                        if app.log_text.winfo_rooty() + app.log_text.winfo_height() > root.winfo_rooty() + root.winfo_height():
                            raise RuntimeError("运行日志被挤出主窗口")
                app.tabs.select(0)
                root.update()
                expected_file_rows = 4 if size == "1360x850" else 3
                if int(app.file_tree.cget("height")) != expected_file_rows:
                    raise RuntimeError("题录文件清单没有随窗口高度调整")
                if app.file_tree.winfo_height() < 60 or app.source_tree.winfo_height() < 40:
                    raise RuntimeError(f"导入清单被挤得过小：窗口 {size}")
            root.update_idletasks()
            def fill_project_dialog():
                dialogs = [child for child in root.winfo_children() if isinstance(child, __import__("tkinter").Toplevel)]
                if not dialogs:
                    raise RuntimeError("新建项目对话框没有打开")
                def descendants(widget):
                    return [child for item in widget.winfo_children() for child in [item, *descendants(item)]]
                entries = [item for item in descendants(dialogs[-1]) if isinstance(item, ttk.Entry)]
                buttons = [item for item in descendants(dialogs[-1]) if isinstance(item, ttk.Button)]
                if not entries or not buttons:
                    raise RuntimeError("新建项目对话框缺少输入框或操作按钮")
                entries[0].insert(0, "自检新项目")
                buttons[-1].invoke()
            root.after(50, fill_project_dialog)
            app.create_project()
            if app.project_data["name"] != "自检新项目" or len(app.project_ids) != 2:
                raise RuntimeError("新项目创建或项目列表刷新失败")
            if not any(rule.get("target") == "China" and "Peoples R China" in rule.get("variants", [])
                       for rule in app.vocabulary_spec.get("countries", [])):
                raise RuntimeError("新项目没有自动继承已保存的国家合并规则")
            app.project_list.selection_clear(0, "end")
            app.project_list.selection_set(0)
            app._on_project_selected()
            if app.project_data["name"] != "默认项目":
                raise RuntimeError("项目切换失败")
            app.vos_panel.kind_var.set("国家合作")
            app.vos_panel.min_var.set("3")
            import time
            time.sleep(0.5)
            root.update()
            restored_view = app.workspace_store.load(app.project_data["id"]).get("vos_settings", {})
            if restored_view.get("kind") != "国家合作" or restored_view.get("min") != "3":
                raise RuntimeError("VOS 建图选项没有自动保存")
            app.shared_vocabulary.remember([], {
                "countries": [{"target": "United Kingdom", "variant": "UK."}]})
            added_file = target / "shared-import.txt"
            added_file.write_text("FN Clarivate Web of Science\nVR 1.0\n\nPT J\nTI Shared catalog check\n"
                                  "PY 2025\nCO UK.\nER\n\nEF\n", encoding="utf-8")
            app._import_file_paths([str(added_file)])
            if (not any("UK." in rule.get("variants", [])
                        for rule in app.vocabulary_spec.get("countries", [])) or
                    not any(record.title == "Shared catalog check" and record.text("CO") == "United Kingdom"
                            for record in app.records)):
                raise RuntimeError("旧项目新增文件没有自动应用最新共享合并目录")
            root.destroy()
            (target / "GUI_SELFTEST_OK.txt").write_text("Packaged Tk GUI initialization passed.\n", encoding="utf-8")
            raise SystemExit(0)
        except Exception as exc:
            details = [traceback.format_exc()]
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                bundle_root = Path(sys._MEIPASS)  # type: ignore[attr-defined]
                tcl_source = bundle_root / "_tcl_data"
                tk_source = bundle_root / "_tk_data"
                details.extend([
                    f"MEIPASS={bundle_root}",
                    f"TCL_LIBRARY={os.environ.get('TCL_LIBRARY', '')}",
                    f"TK_LIBRARY={os.environ.get('TK_LIBRARY', '')}",
                    f"TCL_INIT_EXISTS={(tcl_source / 'init.tcl').exists()}",
                    f"TK_INIT_EXISTS={(tk_source / 'tk.tcl').exists()}",
                ])
                if tcl_source.exists():
                    shutil.copytree(tcl_source, target / "_tcl_data", dirs_exist_ok=True)
                if tk_source.exists():
                    shutil.copytree(tk_source, target / "_tk_data", dirs_exist_ok=True)
            (target / "GUI_SELFTEST_FAILED.txt").write_text("\n".join(details), encoding="utf-8")
            raise SystemExit(4)
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test-export":
        target = Path(sys.argv[2]).resolve()
        try:
            from wos_filter.selftest import run_packaged_self_test

            run_packaged_self_test(target)
            raise SystemExit(0)
        except Exception as exc:
            target.mkdir(parents=True, exist_ok=True)
            (target / "SELFTEST_FAILED.txt").write_text(str(exc), encoding="utf-8")
            raise SystemExit(3)
    try:
        run_app()
    except Exception as exc:
        if exc.__class__.__name__ == "TclError":
            print("[错误] 无法启动桌面窗口：当前 Python 的 Tcl/Tk 组件不可用。", file=sys.stderr)
            print("请通过 python.org 安装完整的 Python（包含 Tcl/Tk and IDLE），或使用打包版程序。", file=sys.stderr)
            print(f"详细信息：{exc}", file=sys.stderr)
            raise SystemExit(2)
        raise
