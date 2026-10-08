"""Recover citation counts omitted by older RIS/Scopus import versions."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .models import WosRecord
from .source_import import ImportFormatError, import_one
from .vocabulary import _serialize


def _title_key(value: str) -> str:
    return " ".join(value.casefold().split())


def backfill_citation_fields(records: list[WosRecord]) -> tuple[int, list[str]]:
    """Add only missing TC/Z9 values, matching the same source file and title."""
    by_file: dict[str, list[WosRecord]] = defaultdict(list)
    for record in records:
        if (record.source_kind in {"wos", "scopus"}
                and record.source_format in {"ris", "scopus_csv", "scopus_plain", "bibtex"}
                and not (record.text("TC") or record.text("Z9"))):
            by_file[record.source_file].append(record)
    changed = 0
    failed: list[str] = []
    for filename, old_records in by_file.items():
        try:
            incoming, _, _ = import_one(filename)
        except (OSError, ValueError, ImportFormatError):
            failed.append(Path(filename).name)
            continue
        indexed = {record.source_index: record for record in incoming}
        for record in old_records:
            fresh = indexed.get(record.source_index)
            if fresh is None or _title_key(fresh.title) != _title_key(record.title):
                continue
            added = False
            for tag in ("TC", "Z9"):
                if not record.text(tag) and fresh.text(tag):
                    record.fields[tag] = list(fresh.values(tag))
                    added = True
            if added:
                record.raw_block = _serialize(record)
                changed += 1
    return changed, failed
