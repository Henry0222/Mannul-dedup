"""Export the manually or automatically deduplicated library before AI screening."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from .models import WosRecord
from .wos import build_citespace_text


def default_export_name(now: datetime | None = None) -> str:
    return "download_" + (now or datetime.now()).strftime("%y%m%d%H%M") + ".txt"


def default_output_dir(app_dir: str | Path, now: datetime | None = None) -> Path:
    return Path(app_dir) / "output" / (now or datetime.now()).strftime("%y%m%d%H%M")


def preferred_export_dir(app_dir: str | Path, saved: str | None = None,
                         now: datetime | None = None) -> Path:
    """Keep custom destinations; replace old project-ID output folders with timestamps."""
    destination = default_output_dir(app_dir, now)
    if not saved:
        return destination
    custom = Path(saved)
    if custom.resolve().is_relative_to((Path(app_dir) / "output").resolve()):
        return destination
    return custom


def export_basic_records(path: str | Path, records: list[WosRecord], header: str) -> Path:
    if not records:
        raise ValueError("当前年份范围内没有可导出的题录。")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(build_citespace_text(header, records))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
