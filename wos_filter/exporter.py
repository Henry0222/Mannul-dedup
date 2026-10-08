from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from .models import Classification, DuplicateEntry, WosRecord
from .wos import build_citespace_text
from .year_filter import format_year_range


DISPLAY_DECISION = {"relevant": "相关", "uncertain": "存疑", "irrelevant": "不相关"}

RECORD_HEADERS = [
    "记录ID", "最终分类", "AI初始分类", "AI置信度", "人工是否修改", "排除原因", "AI判断理由",
    "缺失信息/降级说明", "题名", "作者", "年份", "期刊", "摘要", "作者关键词", "Keywords Plus",
    "DOI", "来源数据库", "来源格式", "数据库编号", "WOS编号", "文献类型", "语言", "WOS分类",
    "被引次数", "参考文献数量", "作者单位", "通讯作者", "基金信息", "来源文件", "API错误",
]


def record_to_row(record: WosRecord, result: Classification) -> dict[str, object]:
    row = {
        "记录ID": record.record_id,
        "最终分类": DISPLAY_DECISION.get(result.final_decision, result.final_decision),
        "AI初始分类": DISPLAY_DECISION.get(result.decision, result.decision),
        "AI置信度": result.confidence,
        "人工是否修改": "是" if result.human_modified else "否",
        "排除原因": result.exclusion_reason,
        "AI判断理由": result.reason,
        "缺失信息/降级说明": "；".join(result.missing_information),
        "题名": record.title,
        "作者": record.text("AU") or record.text("AF"),
        "年份": _integer_or_text(record.text("PY")),
        "期刊": record.text("SO"),
        "摘要": record.abstract,
        "作者关键词": record.text("DE"),
        "Keywords Plus": record.text("ID"),
        "DOI": record.text("DI"),
        "来源数据库": record.source_kind,
        "来源格式": record.source_format,
        "数据库编号": record.text("UT"),
        "WOS编号": record.text("UT") if record.source_kind == "wos" else "",
        "文献类型": record.text("DT"),
        "语言": record.text("LA"),
        "WOS分类": record.text("WC") or record.text("SC"),
        "被引次数": _integer_or_text(record.text("TC") or record.text("Z9")),
        "参考文献数量": _integer_or_text(record.text("NR")),
        "作者单位": record.text("C1"),
        "通讯作者": record.text("RP"),
        "基金信息": record.text("FU") or record.text("FX"),
        "来源文件": Path(record.source_file).name,
        "API错误": result.error,
    }
    return {key: _excel_safe(value) for key, value in row.items()}


def export_results(
    app_dir: str | Path,
    output_root: str | Path,
    header: str,
    records: list[WosRecord],
    results: dict[str, Classification],
    duplicates: list[DuplicateEntry],
    query: str,
    model: str,
    started_at: datetime,
    audit_log_path: str | Path,
    node_executable: str | None = None,
    node_modules: str | None = None,
    diagnostics_dir: str | Path | None = None,
    on_status: Callable[[str], None] | None = None,
    year_start: int | None = None,
    year_end: int | None = None,
    year_excluded_count: int = 0,
    year_unknown_count: int = 0,
) -> Path:
    timestamp = datetime.now().strftime("%y%m%d%H%M")
    output_dir = Path(output_root) / timestamp
    suffix = 1
    while (output_dir / f"download_relevant_{timestamp}.txt").exists() or (output_dir / "文献筛选结果.xlsx").exists():
        output_dir = Path(output_root) / f"{timestamp}_{suffix:02d}"
        suffix += 1
    output_dir.mkdir(parents=True, exist_ok=True)

    relevant_records = [r for r in records if results[r.record_id].final_decision == "relevant"]
    citespace_path = output_dir / f"download_relevant_{timestamp}.txt"
    with citespace_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(build_citespace_text(header, relevant_records))

    audit_source = Path(audit_log_path)
    final_audit_path = output_dir / "筛选审核日志.jsonl"
    if audit_source.exists():
        shutil.copy2(audit_source, final_audit_path)

    failures = []
    rows = []
    for record in records:
        result = results[record.record_id]
        rows.append(record_to_row(record, result))
        if result.error:
            failures.append({"记录ID": record.record_id, "题名": record.title, "错误": result.error})

    with final_audit_path.open("a", encoding="utf-8") as handle:
        for record in records:
            result = results[record.record_id]
            handle.write(json.dumps({
                "event": "final_decision",
                "record_id": record.record_id,
                "ai_decision": result.decision,
                "final_decision": result.final_decision,
                "human_modified": result.human_modified,
                "exclusion_reason": result.exclusion_reason,
            }, ensure_ascii=False, separators=(",", ":")) + "\n")

    payload = {
        "record_headers": RECORD_HEADERS,
        "records": rows,
        "duplicates": [asdict(item) for item in duplicates],
        "failures": failures,
        "run_info": {
            "原始检索式": query,
            "模型": model,
            "发文年份范围": format_year_range(year_start, year_end),
            "年份规则排除": year_excluded_count,
            "年份缺失保留": year_unknown_count,
            "筛选开始时间": started_at.isoformat(timespec="seconds"),
            "导出时间": datetime.now().isoformat(timespec="seconds"),
            "输入记录数（去重后）": len(records),
            "相关": sum(results[r.record_id].final_decision == "relevant" for r in records),
            "不相关": sum(results[r.record_id].final_decision == "irrelevant" for r in records),
            "AI初判存疑": sum(results[r.record_id].decision == "uncertain" for r in records),
            "去重移除": len(duplicates),
            "API失败": len(failures),
        },
    }
    payload_path = output_dir / "workbook_data.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _write_duplicate_csv(output_dir / "去重日志.csv", duplicates)
    _write_failure_csv(output_dir / "API失败记录.csv", failures)

    if on_status:
        on_status("正在生成并校验 Excel 工作簿……")
    _run_workbook_builder(
        Path(app_dir), payload_path, output_dir / "文献筛选结果.xlsx",
        node_executable=node_executable, node_modules=node_modules, diagnostics_dir=diagnostics_dir,
    )
    payload_path.unlink(missing_ok=True)
    return output_dir


def _run_workbook_builder(
    app_dir: Path,
    payload_path: Path,
    output_path: Path,
    node_executable: str | None,
    node_modules: str | None,
    diagnostics_dir: str | Path | None,
) -> None:
    node = node_executable or shutil.which("node")
    if not node:
        raise RuntimeError("没有找到 Node.js，无法生成 Excel。请安装 Node.js 或使用已配置的运行环境。")
    temporary_diagnostics = diagnostics_dir is None
    diagnostics = Path(diagnostics_dir) if diagnostics_dir else Path(tempfile.mkdtemp(prefix="wos_xlsx_check_"))
    diagnostics.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    if node_modules:
        module = Path(node_modules) / "@oai" / "artifact-tool" / "dist" / "artifact_tool.mjs"
        if not module.is_file():
            raise RuntimeError(f"Excel 生成模块缺失：{module}")
        environment["MANNUL_ARTIFACT_MODULE_URL"] = module.resolve().as_uri()
    command = [node, str(app_dir / "tools" / "build_workbook.mjs"),
               str(payload_path), str(output_path), str(diagnostics)]
    completed = subprocess.run(command, cwd=app_dir, capture_output=True, text=True,
                               encoding="utf-8", env=environment)
    if temporary_diagnostics:
        shutil.rmtree(diagnostics, ignore_errors=True)
    Path(str(output_path) + ".inspect.ndjson").unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(f"Excel 生成失败：{completed.stderr.strip() or completed.stdout.strip()}")


def _write_duplicate_csv(path: Path, rows: list[DuplicateEntry]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["重复记录ID", "保留记录ID", "去重依据", "重复来源", "保留来源"])
        for row in rows:
            writer.writerow([row.duplicate_record_id, row.kept_record_id, row.reason, row.duplicate_source, row.kept_source])


def _write_failure_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["记录ID", "题名", "错误"])
        writer.writeheader()
        writer.writerows(rows)


def _integer_or_text(value: str) -> int | str:
    try:
        return int(value)
    except (ValueError, TypeError):
        return value


def _excel_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    cleaned = "".join(ch for ch in value if ch in "\t\n\r" or ord(ch) >= 32)
    if len(cleaned) > 32700:
        cleaned = cleaned[:32680] + "…（内容过长，已截断）"
    if cleaned.startswith(("=", "+", "-", "@")):
        return "'" + cleaned
    return cleaned
