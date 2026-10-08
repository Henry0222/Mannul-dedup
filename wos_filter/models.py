from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MULTI_VALUE_TAGS = {
    "AU", "AF", "BA", "BE", "BF", "CA", "GP", "C1", "EM", "RI", "OI",
    "CR", "DE", "ID", "WC", "SC", "FU", "FX",
}


@dataclass(slots=True)
class WosRecord:
    fields: dict[str, list[str]]
    raw_block: str
    source_file: str
    source_index: int
    source_kind: str = "wos"
    source_format: str = "wos_tagged"
    internal_id: str = ""

    def values(self, tag: str) -> list[str]:
        return [v.strip() for v in self.fields.get(tag, []) if v.strip()]

    def text(self, tag: str) -> str:
        values = self.values(tag)
        separator = "; " if tag in MULTI_VALUE_TAGS and tag not in {"AB", "TI"} else " "
        return separator.join(values).strip()

    @property
    def record_id(self) -> str:
        if self.internal_id:
            return self.internal_id
        if self.text("UT"):
            return self.text("UT")
        if self.text("DI"):
            return self.text("DI")
        source_hash = hashlib.sha1(self.source_file.encode("utf-8")).hexdigest()[:8]
        return f"{Path(self.source_file).name}#{self.source_index}@{source_hash}"

    @property
    def title(self) -> str:
        return self.text("TI")

    @property
    def abstract(self) -> str:
        return self.text("AB")

    @property
    def keywords(self) -> str:
        parts = [self.text("DE"), self.text("ID")]
        return "; ".join(p for p in parts if p)

    def completeness_score(self) -> int:
        populated = sum(1 for values in self.fields.values() if any(v.strip() for v in values))
        return populated * 100 + len(self.abstract) + len(self.text("CR"))

    def api_payload(self) -> dict[str, str]:
        return {
            "record_id": self.record_id,
            "title": self.title,
            "abstract": self.abstract,
            "author_keywords": self.text("DE"),
            "keywords_plus": self.text("ID"),
            "document_type": self.text("DT"),
            "year": self.text("PY"),
            "wos_categories": self.text("WC") or self.text("SC"),
        }


@dataclass(slots=True)
class Classification:
    record_id: str
    decision: str
    confidence: float
    reason: str
    matched_criteria: list[str] = field(default_factory=list)
    exclusion_reason: str = ""
    evidence: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    cached: bool = False
    error: str = ""
    final_decision: str = ""
    human_modified: bool = False

    def __post_init__(self) -> None:
        if not self.final_decision:
            self.final_decision = self.decision

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "decision": self.decision,
            "confidence": self.confidence,
            "reason": self.reason,
            "matched_criteria": self.matched_criteria,
            "exclusion_reason": self.exclusion_reason,
            "evidence": self.evidence,
            "missing_information": self.missing_information,
            "cached": self.cached,
            "error": self.error,
            "final_decision": self.final_decision,
            "human_modified": self.human_modified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Classification":
        return cls(
            record_id=str(data.get("record_id", "")),
            decision=str(data.get("decision", "uncertain")),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "")),
            matched_criteria=_string_list(data.get("matched_criteria")),
            exclusion_reason=str(data.get("exclusion_reason", "")),
            evidence=_string_list(data.get("evidence")),
            missing_information=_string_list(data.get("missing_information")),
            cached=bool(data.get("cached", False)),
            error=str(data.get("error", "")),
            final_decision=str(data.get("final_decision", "")),
            human_modified=bool(data.get("human_modified", False)),
        )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value).strip()]


@dataclass(slots=True)
class DuplicateEntry:
    duplicate_record_id: str
    kept_record_id: str
    reason: str
    duplicate_source: str
    kept_source: str


@dataclass(slots=True)
class ParsedWos:
    records: list[WosRecord]
    header: str
    warnings: list[str] = field(default_factory=list)
    imports: list[Any] = field(default_factory=list)
