from __future__ import annotations

from .models import WosRecord


MIN_PUBLICATION_YEAR = 1000
MAX_PUBLICATION_YEAR = 9999


def parse_year_range(start_text: str, end_text: str) -> tuple[int | None, int | None]:
    start = _parse_year_bound(start_text, "起始年份")
    end = _parse_year_bound(end_text, "截止年份")
    if start is not None and end is not None and start > end:
        raise ValueError("起始年份不能晚于截止年份。")
    return start, end


def format_year_range(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "不限"
    if start is None:
        return f"{end} 年及以前"
    if end is None:
        return f"{start} 年及以后"
    if start == end:
        return f"{start} 年"
    return f"{start}–{end} 年"


def publication_year(record: WosRecord) -> int | None:
    value = record.text("PY").strip()
    if len(value) != 4 or not value.isascii() or not value.isdigit():
        return None
    year = int(value)
    if not MIN_PUBLICATION_YEAR <= year <= MAX_PUBLICATION_YEAR:
        return None
    return year


def partition_records_by_year(
    records: list[WosRecord],
    start: int | None,
    end: int | None,
) -> tuple[list[WosRecord], list[tuple[WosRecord, int]], list[WosRecord]]:
    included: list[WosRecord] = []
    excluded: list[tuple[WosRecord, int]] = []
    unknown: list[WosRecord] = []
    for record in records:
        year = publication_year(record)
        if year is None:
            included.append(record)
            if start is not None or end is not None:
                unknown.append(record)
            continue
        if (start is not None and year < start) or (end is not None and year > end):
            excluded.append((record, year))
        else:
            included.append(record)
    return included, excluded, unknown


def _parse_year_bound(value: str, label: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) != 4 or not cleaned.isascii() or not cleaned.isdigit():
        raise ValueError(f"{label}必须是四位数字，例如 2015。")
    year = int(cleaned)
    if not MIN_PUBLICATION_YEAR <= year <= MAX_PUBLICATION_YEAR:
        raise ValueError(f"{label}必须在 {MIN_PUBLICATION_YEAR}–{MAX_PUBLICATION_YEAR} 之间。")
    return year
