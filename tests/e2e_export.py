from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wos_filter.exporter import export_results
from wos_filter.models import Classification
from wos_filter.wos import deduplicate, parse_wos_file


def main() -> None:
    root = Path(os.environ["TEMP"]) / "wos_filter_e2e"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    sample = """FN Clarivate Web of Science
VR 1.0
PT J
AU Zhang, A
TI Artificial intelligence in higher education
SO JOURNAL OF TESTS
AB This study investigates AI-assisted learning.
DE artificial intelligence
   higher education
PY 2025
DI 10.1000/test.1
UT WOS:0001
CR Smith J, 2020, TEST J, V1, P1
ER

PT J
AU Wang, C
TI Medical imaging systems
SO ANOTHER JOURNAL
DE medical imaging
PY 2024
UT WOS:0002
CR Lee D, 2019, OTHER J, V2, P3
ER

PT J
AU Chen, D
TI Machine learning for student support
SO THIRD JOURNAL
DE machine learning
   student support
PY 2023
UT WOS:0003
ER

EF
"""
    source = root / "savedrecs.txt"
    source.write_text(sample, encoding="utf-8")
    parsed = parse_wos_file(source)
    records, duplicates = deduplicate(parsed.records)
    results = {
        "WOS:0001": Classification(
            "WOS:0001", "relevant", .96, "题名与关键词明确相关",
            missing_information=["Jev 测试说明"],
        ),
        "WOS:0002": Classification(
            "WOS:0002", "irrelevant", .92, "属于医学影像", exclusion_reason="命中排除主题"
        ),
        "WOS:0003": Classification(
            "WOS:0003", "uncertain", .62, "研究场景需复核", final_decision="relevant", human_modified=True
        ),
    }
    audit = root / "audit.jsonl"
    audit.write_text('{"event":"test"}\n', encoding="utf-8")
    output = export_results(
        Path.cwd(), root / "output", parsed.header, records, results, duplicates,
        "TS=((artificial intelligence OR machine learning) AND education) NOT TS=(medical)",
        "jev-latest", datetime.now(), audit,
        node_executable=r"C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe",
        node_modules=r"C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules",
        diagnostics_dir=root / "diagnostics",
        year_start=2023,
        year_end=2025,
        year_excluded_count=1,
        year_unknown_count=0,
    )
    assert re.fullmatch(r"\d{10}(?:_\d{2})?", output.name), output
    assert (output / f"download_relevant_{output.name[:10]}.txt").is_file()
    print(output)
    for child in sorted(output.iterdir()):
        print(f"{child.name}\t{child.stat().st_size}")


if __name__ == "__main__":
    main()
