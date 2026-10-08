"""Determine which completed work a vocabulary change can actually affect."""

from __future__ import annotations

from .dedupe_review import dedupe_match_key
from .models import WosRecord
from .project_workspace import record_key
from .vocabulary import _compact, _key, _parts


def _screening_signature(record: WosRecord) -> tuple:
    payload = record.api_payload()
    payload.pop("record_id", None)
    for field in ("author_keywords", "keywords_plus"):
        payload[field] = tuple(sorted({_compact(term) or _key(term)
                                       for term in _parts(payload[field])}))
    return tuple(sorted(payload.items()))


def vocabulary_impact(before: list[WosRecord], after: list[WosRecord],
                      runs: list[dict], ai_record_ids: set[str]) -> tuple[bool, bool]:
    """Return (dedupe changed, screening input changed) for existing records."""
    old = {record_key(record): record for record in before}
    new = {record_key(record): record for record in after}
    if old.keys() != new.keys():
        return True, bool(ai_record_ids)
    presets = {run["preset"] for run in runs}
    dedupe_changed = any(dedupe_match_key(old[key], preset) != dedupe_match_key(new[key], preset)
                         for preset in presets for key in old)
    ai_changed = any(_screening_signature(old[key]) != _screening_signature(new[key])
                     for key in ai_record_ids if key in old)
    if any(key not in old for key in ai_record_ids):
        ai_changed = True
    return dedupe_changed, ai_changed
