from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .exporter import export_results
from .gui import _find_bundled_node, get_resource_dir
from .models import Classification, WosRecord


def run_packaged_self_test(output_root: str | Path) -> Path:
    """离线验证打包后的 Python、Node、工作簿模块和文件写入链路。"""
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    resource_dir = get_resource_dir()
    node, modules = _find_bundled_node(resource_dir)
    if not node or not modules:
        raise RuntimeError("打包资源中没有找到内置 Node.js 与工作簿模块。")
    try:
        Path(node).resolve().relative_to(resource_dir)
        Path(modules).resolve().relative_to(resource_dir)
    except ValueError as exc:
        raise RuntimeError("自检发现程序使用了外部 Node.js，而不是 EXE 内置运行库。") from exc

    records = [
        WosRecord(
            fields={
                "PT": ["J"], "AU": ["Zhang, A"],
                "TI": ["Artificial intelligence in higher education"],
                "SO": ["JOURNAL OF TESTS"], "AB": ["This study investigates AI-assisted learning."],
                "DE": ["artificial intelligence", "higher education"], "PY": ["2025"],
                "DI": ["10.1000/test.1"], "UT": ["WOS:SELFTEST1"],
                "CR": ["Smith J, 2020, TEST J, V1, P1"],
            },
            raw_block=(
                "PT J\nAU Zhang, A\nTI Artificial intelligence in higher education\n"
                "SO JOURNAL OF TESTS\nAB This study investigates AI-assisted learning.\n"
                "DE artificial intelligence; higher education\nPY 2025\nDI 10.1000/test.1\n"
                "UT WOS:SELFTEST1\nCR Smith J, 2020, TEST J, V1, P1\nER"
            ),
            source_file="packaged_selftest.txt", source_index=1,
        ),
        WosRecord(
            fields={
                "PT": ["J"], "AU": ["Wang, B"], "TI": ["Unrelated medical imaging study"],
                "SO": ["ANOTHER JOURNAL"], "AB": ["This study concerns diagnostic imaging."],
                "DE": ["medical imaging"], "PY": ["2024"], "UT": ["WOS:SELFTEST2"],
            },
            raw_block=(
                "PT J\nAU Wang, B\nTI Unrelated medical imaging study\nSO ANOTHER JOURNAL\n"
                "AB This study concerns diagnostic imaging.\nDE medical imaging\nPY 2024\n"
                "UT WOS:SELFTEST2\nER"
            ),
            source_file="packaged_selftest.txt", source_index=2,
        ),
    ]
    results = {
        "WOS:SELFTEST1": Classification(
            "WOS:SELFTEST1", "relevant", 0.96, "题名、摘要和关键词符合检索主题。"
        ),
        "WOS:SELFTEST2": Classification(
            "WOS:SELFTEST2", "irrelevant", 0.94, "研究主题不符。", exclusion_reason="人工测试排除"
        ),
    }
    audit_path = output_root / "packaged_selftest_audit.jsonl"
    audit_path.write_text('{"event":"packaged_selftest"}\n', encoding="utf-8")
    output_dir = export_results(
        app_dir=resource_dir,
        output_root=output_root,
        header="FN Clarivate Web of Science\nVR 1.0",
        records=records,
        results=results,
        duplicates=[],
        query="TS=(artificial intelligence AND education)",
        model="jev-latest",
        started_at=datetime.now(),
        audit_log_path=audit_path,
        node_executable=node,
        node_modules=modules,
        diagnostics_dir=output_root / "diagnostics",
    )
    (output_dir / "SELFTEST_OK.txt").write_text(
        "Packaged Python + embedded Node.js + artifact-tool export passed.\n", encoding="utf-8"
    )
    audit_path.unlink(missing_ok=True)
    return output_dir
