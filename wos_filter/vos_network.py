"""Build a VOSviewer Online network from reviewed, deduplicated records."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from itertools import combinations
from pathlib import Path

from .bibliometrics import citation_count, has_citation_count, terms_for_record
from .models import WosRecord
from .year_filter import publication_year


NETWORK_TYPES = {
    "keywords": "关键词共现",
    "authors": "作者合作",
    "countries": "国家合作",
    "institutions": "机构合作",
    "journals": "期刊",
    "cited_authors": "被引作者",
    "cited_journals": "被引期刊",
    "cited_articles": "被引文章",
}

CITED_TYPES = {"cited_authors", "cited_journals", "cited_articles"}


def _reference_key(reference: str) -> str:
    match = re.search(r"\bDOI\s*[: ]\s*(10\.\d{4,9}/\S+)", reference, re.I)
    if match:
        return "doi:" + match.group(1).rstrip(".,; ").casefold()
    return "ref:" + re.sub(r"\s+", " ", reference).strip().casefold()


def _cited_terms(record: WosRecord, kind: str) -> list[tuple[str, str]]:
    terms: dict[str, str] = {}
    for raw in record.values("CR"):
        reference = re.sub(r"\s+", " ", raw).strip(" ,;")
        parts = [part.strip() for part in reference.split(",")]
        if len(parts) < 2 or not re.search(r"\b(?:18|19|20)\d{2}\b", parts[1]):
            continue
        if kind == "cited_authors":
            label = parts[0]
            if not label or label.casefold() in {"anonymous", "[anonymous]"}:
                continue
            key = label.casefold()
        else:
            if len(parts) < 3 or not parts[2] or re.match(r"^(?:DOI|ISBN|P\d+|V\d+)", parts[2], re.I):
                continue
            if not re.search(r"(?:,\s*(?:V|P)\d+|\bDOI\s*[: ]\s*10\.)", reference, re.I):
                continue
            if kind == "cited_journals":
                label = parts[2]
                key = label.casefold()
            else:
                label = reference
                key = _reference_key(reference)
        terms.setdefault(key, label)
    return list(terms.items())


def build_vos_network(records: list[WosRecord], kind: str = "keywords", *,
                      min_occurrences: int = 2, max_items: int = 80,
                      keyword_source: str = "author") -> dict:
    """Count each term and pair once per paper, then create VOSviewer JSON."""
    if kind not in NETWORK_TYPES:
        raise ValueError("未知 VOS 图谱类型。")
    if not 1 <= min_occurrences <= 1000 or not 2 <= max_items <= 300:
        raise ValueError("最少出现次数须为 1–1000，最大节点数须为 2–300。")
    documents: list[tuple[list[str], int | None]] = []
    journal_references: list[tuple[list[str], set[str]]] = []
    counts: Counter[str] = Counter()
    citations: Counter[str] = Counter()
    labels: dict[str, str] = {}
    years: dict[str, list[int]] = {}
    has_references = False
    for record in records:
        if kind in CITED_TYPES or kind == "journals":
            has_references = has_references or bool(record.values("CR"))
        if kind in CITED_TYPES:
            cited = _cited_terms(record, kind)
            unique = list(dict.fromkeys(key for key, _label in cited))
            for key, label in cited:
                labels.setdefault(key, label)
        else:
            terms = terms_for_record(record, kind, keyword_source=keyword_source)
            unique = list(dict.fromkeys(term.casefold() for term in terms if term.strip()))
            for term in terms:
                labels.setdefault(term.casefold(), term)
        year = publication_year(record)
        documents.append((unique, year))
        if kind == "journals":
            journal_references.append((unique, {_reference_key(ref) for ref in record.values("CR") if ref.strip()}))
        counts.update(unique)
        if kind in CITED_TYPES:
            citations.update(unique)
        elif has_citation_count(record):
            for key in unique:
                citations[key] += citation_count(record)
        if year is not None:
            for key in unique:
                years.setdefault(key, []).append(year)
    selected = sorted((key for key, count in counts.items() if count >= min_occurrences),
                      key=lambda key: (-counts[key], labels[key].casefold()))[:max_items]
    if not counts:
        if kind == "journals":
            raise ValueError("当前题录没有 SO 来源期刊数据，无法生成期刊图谱。")
        if kind in CITED_TYPES:
            detail = ("没有 CR 参考文献字段" if not has_references else
                      f"CR 字段中没有可识别的{NETWORK_TYPES[kind]}")
            raise ValueError(f"当前题录{detail}，无法生成{NETWORK_TYPES[kind]}图谱。")
        raise ValueError(f"当前题录没有可识别的相关数据，无法生成{NETWORK_TYPES[kind]}图谱。")
    if kind == "journals" and not has_references:
        raise ValueError("当前题录没有 CR 参考文献字段，无法计算期刊之间的文献耦合。")
    if len(selected) < 2:
        raise ValueError("符合阈值的节点少于 2 个；请降低最少出现次数或更换图谱类型。")
    ids = {key: index for index, key in enumerate(selected, 1)}
    edges: Counter[tuple[int, int]] = Counter()
    if kind == "journals":
        cited_by_journal: dict[str, Counter[int]] = {}
        for terms, references in journal_references:
            present = {ids[key] for key in terms if key in ids}
            for reference in references:
                cited_by_journal.setdefault(reference, Counter()).update(present)
        for source_counts in cited_by_journal.values():
            for source, target in combinations(sorted(source_counts), 2):
                edges[(source, target)] += source_counts[source] * source_counts[target]
    else:
        for terms, _year in documents:
            present = sorted(ids[key] for key in terms if key in ids)
            edges.update(combinations(present, 2))
    if not edges:
        detail = ("来源期刊没有共同参考文献" if kind == "journals" else
                  "这些节点未在同一篇文献中共同出现")
        raise ValueError(f"已有相关节点，但{detail}，无法形成网络。")
    total_link_strength: Counter[int] = Counter()
    for (source, target), strength in edges.items():
        total_link_strength[source] += strength
        total_link_strength[target] += strength
    items = []
    for key in selected:
        item = {"id": ids[key], "label": labels[key],
                "weights": {"Documents": counts[key], "Citations": citations[key],
                            "Total link strength": total_link_strength[ids[key]]}}
        if years.get(key):
            item["scores"] = {"Avg. pub. year": round(sum(years[key]) / len(years[key]), 2)}
        items.append(item)
    links = [{"source_id": source, "target_id": target, "strength": strength}
             for (source, target), strength in sorted(edges.items())]
    relation = "文献耦合" if kind == "journals" else "共被引" if kind in CITED_TYPES else "同篇共现"
    return {"network": {"items": items, "links": links},
            "config": {"parameters": {"largest_component": False, "simple_ui": False}},
            "info": {"title": NETWORK_TYPES[kind],
                     "description": f"来自 {len(records)} 条题录；连线为{relation}；最少出现 {min_occurrences} 次。"}}


def node_citation_coverage(records: list[WosRecord], data: dict, kind: str,
                           keyword_source: str = "author") -> dict[int, int]:
    """Number of papers contributing an observed citation count to each node."""
    if kind in CITED_TYPES:
        return {item["id"]: item["weights"]["Documents"] for item in data["network"]["items"]}
    ids = {item["label"].casefold(): item["id"] for item in data["network"]["items"]}
    coverage: Counter[int] = Counter()
    for record in records:
        if not has_citation_count(record):
            continue
        terms = {term.casefold() for term in terms_for_record(record, kind, keyword_source=keyword_source)}
        coverage.update(ids[key] for key in terms if key in ids)
    return dict(coverage)


def filter_vos_network(data: dict, selected_ids: set[int]) -> dict:
    """Apply operator exclusions without changing the source data or node IDs."""
    items = [item.copy() for item in data["network"]["items"] if item["id"] in selected_ids]
    links = [link.copy() for link in data["network"]["links"]
             if link["source_id"] in selected_ids and link["target_id"] in selected_ids]
    if len(items) < 2 or not links:
        raise ValueError("至少保留两个有连线的节点才能生成 VOS 图谱。")
    strengths: Counter[int] = Counter()
    for link in links:
        strengths[link["source_id"]] += link["strength"]
        strengths[link["target_id"]] += link["strength"]
    for item in items:
        item["weights"] = {**item["weights"], "Total link strength": strengths[item["id"]]}
    return {**data, "network": {**data["network"], "items": items, "links": links}}


def inherit_vos_parameters(data: dict, previous_path: str | Path) -> dict:
    """Carry the previous map's display and layout choices into a new map."""
    path = Path(previous_path)
    if not path.is_file():
        return data
    try:
        parameters = json.loads(path.read_text(encoding="utf-8")).get("config", {}).get("parameters", {})
    except (OSError, ValueError, TypeError):
        return data
    if isinstance(parameters, dict):
        data["config"]["parameters"].update({key: value for key, value in parameters.items()
                                               if key not in {"json", "map", "network"}})
    return data


def save_vos_network(data: dict, output_root: str | Path, kind: str,
                     now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%y%m%d%H%M")
    folder = Path(output_root) / stamp
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"vos_{kind}_{stamp}.json"
    suffix = 1
    while path.exists():
        path = folder / f"vos_{kind}_{stamp}_{suffix:02d}.json"
        suffix += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
