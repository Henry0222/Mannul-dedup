"""Keep inherited catalog provenance separate from matches in this project's files."""

from __future__ import annotations

from .shared_vocabulary import SHARED_KINDS
from .vocabulary import _key


def pair_key(target: str, variant: str) -> str:
    return _key(target) + "\t" + _key(variant)


def historical_snapshot(catalog: dict[str, list[dict]]) -> dict[str, list[str]]:
    return {kind: sorted({pair_key(row["target"], row["variant"])
                          for row in catalog.get(kind, [])}) for kind in SHARED_KINDS}


def annotate_pairs(pairs: list[dict], kind: str, snapshot: dict[str, list[str]]) -> list[dict]:
    inherited = set(snapshot.get(kind, []))
    return [{**row,
             "catalog_scope": ("历史沿用" if pair_key(row["target"], row["variant"]) in inherited
                               else "本项目生成"),
             "current_file": bool(row.get("occurrences", 0))}
            for row in pairs]


def export_scope_pairs(pairs: list[dict], scope: str) -> list[dict]:
    if scope == "current":
        return [row for row in pairs if row.get("current_file")]
    if scope == "all":
        return pairs
    raise ValueError("未知合并词导出范围。")
