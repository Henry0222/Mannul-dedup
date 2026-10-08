"""Read complete institution names from WoS C3 fields, including legacy imports."""

from __future__ import annotations

import re

from .models import WosRecord


def institution_terms(record: WosRecord) -> list[str]:
    """Rejoin physical WoS C3 continuation lines before splitting institutions.

    Old project JSON kept each C3 continuation line as a separate value. Its
    original raw block still marks which lines continue the same C3 field.
    """
    values = record.values("C3")
    if not values:
        return []
    if record.source_format == "wos_tagged" and record.raw_block:
        groups: list[str] = []
        current: list[str] | None = None
        for line in record.raw_block.splitlines():
            if line.startswith("C3 "):
                current = [line[3:].strip()]
                groups.append(current)
            elif line.startswith("   ") and current is not None:
                current.append(line[3:].strip())
            elif re.match(r"^[A-Z0-9]{2}(?: |$)", line):
                current = None
        if groups:
            values = [" ".join(parts) for parts in groups]
    terms = []
    seen = set()
    for value in values:
        for part in re.split(r"[;；]", value):
            term = re.sub(r"\s+", " ", part).strip(" ,，.。")
            if term and term.casefold() not in seen:
                terms.append(term)
                seen.add(term.casefold())
    return terms
