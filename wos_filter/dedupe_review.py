"""BibNexus preset candidate matching; humans decide which records to remove."""

from __future__ import annotations

import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime

from .models import WosRecord
from .project_workspace import record_key
from .wos import normalize_doi


PRESET_FIELDS = {
    "quick": ("identifiers",),
    "journal": ("title", "year", "authors", "source"),
    "strict": ("title", "year", "authors", "abstract"),
    "loose": ("title", "year"),
}
PRESET_LABELS = {"quick": "快速", "journal": "期刊", "strict": "严格", "loose": "宽松"}


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in value if char.isalnum())


def _identifiers(record: WosRecord) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(record.text("DI"))
    if doi:
        keys.add("doi:" + doi)
    ut = record.text("UT").strip().casefold()
    if ut:
        keys.add("ut:" + ut)
    return keys


def _compound_key(record: WosRecord, preset: str) -> tuple[str, ...] | None:
    fields = {
        "title": record.title, "year": record.text("PY"),
        "authors": record.text("AU") or record.text("AF"),
        "source": record.text("SO"), "abstract": record.abstract,
    }
    values = tuple(_normal(fields[field]) for field in PRESET_FIELDS[preset])
    # Do not let missing fields turn a strict preset into an implicit loose one.
    return values if all(values) else None


def dedupe_match_key(record: WosRecord, preset: str) -> tuple[str, ...] | None:
    """Return the exact record key used by a deduplication preset."""
    if preset not in PRESET_FIELDS:
        raise ValueError(f"未知去重模式：{preset}")
    return tuple(sorted(_identifiers(record))) if preset == "quick" else _compound_key(record, preset)


def preferred_record(records: list[WosRecord]) -> WosRecord:
    """Prefer a WoS export, then the most complete record within that source."""
    return min(records, key=lambda record: (
        record.source_kind.casefold() != "wos", -record.completeness_score(),
        record.title.casefold(), record_key(record),
    ))


def find_candidates(records: list[WosRecord], preset: str) -> list[dict]:
    if preset not in PRESET_FIELDS:
        raise ValueError(f"未知去重模式：{preset}")
    if preset == "quick":
        parent = list(range(len(records)))

        def root(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        alias: dict[str, int] = {}
        for index, record in enumerate(records):
            for identifier in _identifiers(record):
                if identifier in alias:
                    parent[root(index)] = root(alias[identifier])
                else:
                    alias[identifier] = index
        buckets: dict[int, list[WosRecord]] = defaultdict(list)
        for index, record in enumerate(records):
            buckets[root(index)].append(record)
        matches = [items for items in buckets.values() if len(items) > 1]
    else:
        buckets: dict[tuple[str, ...], list[WosRecord]] = defaultdict(list)
        for record in records:
            key = _compound_key(record, preset)
            if key:
                buckets[key].append(record)
        matches = [items for items in buckets.values() if len(items) > 1]

    groups = []
    for items in matches:
        items.sort(key=lambda record: (
            record.source_kind.casefold() != "wos", -record.completeness_score(),
            record.title.casefold(), record_key(record),
        ))
        dois = {normalize_doi(record.text("DI")) for record in items if record.text("DI")}
        groups.append({
            "id": str(uuid.uuid4()), "record_keys": [record_key(record) for record in items],
            "primary": record_key(items[0]), "status": "pending",
            "doi_conflict": len(dois) > 1,
        })
    groups.sort(key=lambda group: (
        next((record.title.casefold() for record in records if record_key(record) == group["primary"]), ""),
        group["id"],
    ))
    return groups


def new_run(records: list[WosRecord], preset: str) -> dict:
    groups = find_candidates(records, preset)
    return {
        "id": str(uuid.uuid4()), "preset": preset, "groups": groups,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_groups": len(groups),
        "candidate_duplicates": sum(len(group["record_keys"]) - 1 for group in groups),
        "applied": False,
    }


def auto_review_run(run: dict, records: list[WosRecord]) -> tuple[int, int]:
    """Decide pending groups only; conflicting DOI groups remain in the library."""
    if run["applied"]:
        raise ValueError("本次去重已经应用。")
    by_key = {record_key(record): record for record in records}
    accepted = skipped_conflicts = 0
    for group in run["groups"]:
        if group["status"] != "pending":
            continue
        if group["doi_conflict"]:
            group["status"] = "ignored"
            skipped_conflicts += 1
            continue
        members = [by_key[key] for key in group["record_keys"] if key in by_key]
        if len(members) != len(group["record_keys"]):
            raise ValueError("重复组的题录已变化，请重新查找重复。")
        group["primary"] = record_key(preferred_record(members))
        group["status"] = "deduped"
        accepted += len(members) - 1
    run["auto_ignored_conflicts"] = skipped_conflicts
    return accepted, skipped_conflicts


def apply_review(project: dict, run: dict) -> int:
    if any(group["status"] == "pending" for group in run["groups"]):
        raise ValueError("还有重复组未经核查，请逐组确认或标记非重复。")
    if run["applied"]:
        raise ValueError("本次去重已经应用。")
    excluded = set(project.get("excluded_keys", []))
    before = len(excluded)
    for group in run["groups"]:
        if group["status"] == "deduped":
            if group["primary"] not in group["record_keys"]:
                raise ValueError("保留记录不在重复组内。")
            excluded.update(key for key in group["record_keys"] if key != group["primary"])
    project["excluded_keys"] = sorted(excluded)
    run["applied"] = True
    run["removed_count"] = len(excluded) - before
    return run["removed_count"]
