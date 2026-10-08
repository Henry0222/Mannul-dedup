"""Local publication trends and full-count rankings from normalized records."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .models import WosRecord
from .institution_fields import institution_terms
from .address_fields import country_address_terms, split_address_groups
from .year_filter import publication_year


DIMENSIONS = {"国家": "countries", "机构": "institutions", "期刊": "journals", "作者": "authors",
              "来源作者总被引量": "cited_authors", "被引作者频次": "cited_reference_authors",
              "被引期刊频次": "cited_journals", "被引文献频次": "cited_refs", "关键词排名": "keywords"}


def citation_count(record: WosRecord) -> int:
    for tag in ("TC", "Z9"):
        match = re.search(r"\d+", record.text(tag).replace(",", ""))
        if match:
            return int(match.group())
    return 0


def has_citation_count(record: WosRecord) -> bool:
    return any(re.search(r"\d+", record.text(tag).replace(",", ""))
               for tag in ("TC", "Z9"))


def annual_trend(records: list[WosRecord]) -> list[dict[str, int]]:
    counts: dict[int, dict[str, int]] = defaultdict(lambda: {"papers": 0, "citations": 0})
    for record in records:
        year = publication_year(record)
        if year is None:
            continue
        counts[year]["papers"] += 1
        counts[year]["citations"] += citation_count(record)
    return [{"year": year, **counts[year]} for year in sorted(counts)]


def rank_dimension(records: list[WosRecord], dimension: str, limit: int = 20,
                   *, keyword_source: str = "author") -> list[dict[str, int | str]]:
    if dimension not in DIMENSIONS.values():
        raise ValueError(f"不支持的排名维度：{dimension}")
    papers: Counter[str] = Counter()
    citations: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for record in records:
        terms = terms_for_record(record, dimension, keyword_source=keyword_source)
        for term in terms:
            key = term.casefold()
            labels.setdefault(key, term)
            papers[key] += 1
            citations[key] += citation_count(record)
    order = sorted(papers, key=lambda key: (-papers[key], -citations[key], labels[key].casefold()))
    return [{"name": labels[key], "papers": papers[key], "citations": citations[key]}
            for key in order[:max(0, limit)]]


def terms_for_record(record: WosRecord, dimension: str, *, keyword_source: str = "author") -> list[str]:
    """Return the counted terms for one publication, preserving CR occurrence counts."""
    if dimension == "cited_authors":
        dimension = "authors"
    if dimension == "cited_reference_authors":
        return [part.split(",", 1)[0].strip() for part in record.values("CR")
                if part.split(",", 1)[0].strip()]
    if dimension == "cited_journals":
        journals = []
        for citation in record.values("CR"):
            parts = [part.strip() for part in citation.split(",")]
            if len(parts) >= 3 and re.search(r"\b(?:18|19|20)\d{2}\b", parts[1]):
                journal = parts[2]
                if journal and not re.match(r"^(?:DOI|ISBN|P\d+|V\d+)", journal, re.I):
                    journals.append(journal)
        return journals
    if dimension == "cited_refs":
        return [re.sub(r"\s+", " ", item).strip() for item in record.values("CR")]
    if dimension == "keywords":
        raw = record.values("DE") + (record.values("ID") if keyword_source == "author_plus" else [])
    elif dimension == "journals":
        raw = record.values("SO")
    elif dimension == "authors":
        raw = record.values("AU") or record.values("AF")
    elif dimension == "institutions":
        raw = institution_terms(record)
        if not raw:
            raw = [re.sub(r"^\[[^]]+\]\s*", "", address).split(",", 1)[0]
                   for address in record.values("C1")]
    else:
        raw = record.values("CO")
        if not raw:
            raw = []
            for address in record.values("C1"):
                # WoS often joins several bracketed author-address groups into one C1 value.
                raw.extend(country_address_terms(address))
                for segment in split_address_groups(address)[::2]:
                    if "," in segment:
                        continue
                    if re.search(r"[\u4e00-\u9fff]", segment):
                        raw.append("China")
                    elif re.search(r"\b(?:China|USA|United States|United Kingdom|UK)\b", segment, re.I):
                        raw.append(segment)
            raw = [re.sub(r"\s*\d[\d\s-]*$", "", value).strip(" .") for value in raw]
    terms: dict[str, str] = {}
    for value in raw:
        for part in re.split(r"[;；]", value):
            cleaned = re.sub(r"\s+", " ", part).strip(" ,，.。")
            if cleaned:
                if dimension == "countries":
                    cleaned = _normalize_country(cleaned)
                terms.setdefault(cleaned.casefold(), cleaned)
    return list(terms.values())


def _normalize_country(value: str) -> str:
    key = re.sub(r"[^a-z]+", " ", value.casefold()).strip()
    if re.search(r"\bUSA\b$", value, re.I):
        return "USA"
    if value == "中国" or key in {"china", "peoples r china", "people s r china",
                                    "people s republic of china", "pr china", "p r china"}:
        return "China"
    if key in {"usa", "u s a", "us", "united states", "united states of america"}:
        return "USA"
    if key in {"uk", "u k", "united kingdom", "england", "scotland", "wales", "northern ireland"}:
        return "United Kingdom"
    return value
