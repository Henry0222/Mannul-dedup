"""Split WoS C1 values that contain several author-address groups."""

from __future__ import annotations

import re


_GROUP_BOUNDARY = re.compile(r"(\s+(?=\[[^\]]+\]\s))")


def split_address_groups(value: str) -> list[str]:
    """Return alternating address groups and their original whitespace separators."""
    return _GROUP_BOUNDARY.split(value)


def country_address_terms(value: str) -> list[str]:
    """Read the trailing country from every bracketed C1 address group."""
    terms = []
    for group in split_address_groups(value)[::2]:
        if "," not in group:
            continue
        country = group.rsplit(",", 1)[-1].strip(" .")
        if country and "@" not in country and len(country) <= 60:
            terms.append(country)
    return terms
