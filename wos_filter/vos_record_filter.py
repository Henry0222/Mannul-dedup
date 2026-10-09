"""Choose the exact project records handed to every VOS output path."""

from __future__ import annotations

from .models import WosRecord
from .plot_engine import record_language


ALL_TITLES = "全部题录（不按题名筛选）"
CHINESE_TITLES = "中文题名"
ENGLISH_TITLES = "英文题名"
TITLE_CHOICES = (ALL_TITLES, CHINESE_TITLES, ENGLISH_TITLES)


def filter_vos_records(records: list[WosRecord], databases: set[str],
                       title_language: str = ALL_TITLES) -> list[WosRecord]:
    """Database is authoritative; title language is an optional document filter."""
    if not databases:
        raise ValueError("请至少勾选一个数据库用于 VOS 建图。")
    selected = [record for record in records if record.source_kind in databases]
    if title_language == CHINESE_TITLES:
        selected = [record for record in selected if record_language(record) == "中文"]
    elif title_language == ENGLISH_TITLES:
        selected = [record for record in selected if record_language(record) == "英文"]
    elif title_language != ALL_TITLES:
        raise ValueError("未知题名语言筛选选项。")
    if not selected:
        raise ValueError("所选数据库、题名语言和年份范围内没有题录，请调整建图选项。")
    return selected
