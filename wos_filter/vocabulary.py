"""Reconstructed vocabulary merge used before deduplication and AI screening."""

from __future__ import annotations

import re
from collections import Counter

from .models import WosRecord
from .institution_fields import institution_terms
from .address_fields import country_address_terms, split_address_groups


KINDS = ("countries", "organizations", "authors", "journals", "keywords")
INVARIANTS = {"news", "series", "species", "diabetes", "physics", "economics",
              "mathematics", "statistics", "ethics", "politics", "genetics",
              "mechanics", "dynamics", "bias", "status", "virus", "aids", "sars", "mers"}
IRREGULAR = {"analyses": "analysis", "diagnoses": "diagnosis", "theses": "thesis",
             "hypotheses": "hypothesis", "crises": "crisis", "matrices": "matrix",
             "indices": "index", "children": "child", "women": "woman", "men": "man",
             "mice": "mouse", "geese": "goose", "teeth": "tooth", "feet": "foot",
             "buses": "bus", "gases": "gas", "viruses": "virus"}


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _compact(value: str) -> str | None:
    compact = re.sub(r"[\s\-\u2010-\u2015\u2212]+", "", value.casefold())
    return compact if len(compact) >= 3 and compact.isascii() and compact.isalpha() else None


def _singular(compact: str) -> str:
    if compact in INVARIANTS:
        return compact
    if compact in IRREGULAR:
        return IRREGULAR[compact]
    for plural in ("analyses", "diagnoses", "theses", "hypotheses", "crises", "matrices", "indices", "viruses"):
        if compact.endswith(plural):
            return compact[:-len(plural)] + IRREGULAR[plural]
    if len(compact) > 4 and compact.endswith("ies") and compact[-4] not in "aeiou":
        return compact[:-3] + "y"
    if len(compact) > 4 and compact.endswith(("ches", "shes", "sses", "xes", "zes")):
        return compact[:-2]
    if len(compact) > 3 and compact.endswith("s") and not compact.endswith(("ss", "us", "is", "as")):
        return compact[:-1]
    return compact


def keyword_canonical(target: str, variants: tuple[str, ...] | list[str] = ()) -> str:
    """Choose an equivalent unhyphenated spelling, then use sentence case."""
    target = target.strip()
    root = _compact(target)
    if root:
        forms = [value.strip() for value in (target, *variants)
                 if value.strip() and _compact(value) == root]
        target = min(forms, key=lambda value: (
            bool(re.search(r"[-\u2010-\u2015\u2212]", value)),
            " " not in value, len(value), value.casefold()))
    # When every source spelling is hyphenated, create a readable label while
    # retaining the original spellings as aliases. Keep numeric identifiers
    # such as IL-6 intact because the dash is part of the identifier.
    target = re.sub(r"(?<=[^\W\d_])[-\u2010-\u2015\u2212]+(?=[^\W\d_])", " ", target)
    target = re.sub(r"^[-\u2010-\u2015\u2212]+|[-\u2010-\u2015\u2212]+$", "", target)
    target = re.sub(r"\s+", " ", target).strip()
    return target.capitalize()


def keyword_scope_change(target: str, variant: str) -> bool:
    """Detect a likely broader/narrower keyword pair without judging synonyms."""
    left, right = _compact(target), _compact(variant)
    if left and right and _singular(left) == _singular(right):
        return False
    if left and right and (left in right or right in left):
        return True
    a = set(re.findall(r"[\w]+", target.casefold()))
    b = set(re.findall(r"[\w]+", variant.casefold()))
    return bool(a and b and (a < b or b < a))


def _parts(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;；\r\n]+", value) if part.strip()]


def _projects(spec: dict) -> list[dict]:
    if "projects" in spec:
        projects = spec["projects"]
    elif "objectType" in spec:
        projects = [spec]
    else:
        projects = [{"objectType": kind, "rules": spec.get(kind, [])} for kind in KINDS]
    if not isinstance(projects, list):
        raise ValueError("词汇项目 projects 必须是列表")
    return projects


def _compile(spec: dict) -> dict[str, tuple[dict[str, str], set[str]]]:
    compiled: dict[str, tuple[dict[str, str], set[str]]] = {}
    for project in _projects(spec):
        if not isinstance(project, dict):
            raise ValueError("词汇项目必须是对象")
        kind = project.get("objectType")
        if kind not in KINDS or kind in compiled:
            raise ValueError(f"词汇对象类型无效或重复：{kind}")
        raw_rules = project.get("rules", [])
        if not isinstance(raw_rules, list):
            raise ValueError(f"{kind} 规则必须是列表")
        mapping: dict[str, str] = {}
        for rule in raw_rules:
            if not isinstance(rule, dict):
                raise ValueError("每条词汇规则必须是对象")
            target = rule.get("target", rule.get("primaryOutputText", ""))
            if not isinstance(target, str) or not target.strip() or re.search(r"[;；\r\n]", target):
                raise ValueError("规范词 target 无效")
            target = target.strip()
            variants = rule.get("variants")
            if variants is None:
                secondary = rule.get("secondaryKeys", [])
                if not isinstance(secondary, list):
                    raise ValueError("secondaryKeys 必须是列表")
                variants = [rule.get("primaryKey", target), *secondary]
            if not isinstance(variants, list) or not all(isinstance(value, str) and value.strip() for value in variants):
                raise ValueError("variants 必须是非空字符串列表")
            for value in [target, *variants]:
                key = _key(value)
                if key in mapping and mapping[key] != target:
                    raise ValueError(f"{value} 被分配给不同规范词")
                mapping[key] = target
        excluded: set[str] = set()
        if kind == "keywords":
            values = project.get("exclude", project.get("exclusionKeys", []))
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError("关键词 exclude 必须是列表")
            excluded = {_key(value) for value in values if value.strip()}
            if excluded & mapping.keys():
                raise ValueError("同一关键词不能同时合并和排除")
        compiled[kind] = mapping, excluded
    return compiled


def _automatic_mapping(records: list[WosRecord], explicit: dict[str, str],
                       excluded: set[str], auto_excluded: set[str],
                       sentence_style: bool = False) -> tuple[dict[str, str], list[dict]]:
    buckets: dict[str, Counter[str]] = {}
    for record in records:
        for tag in ("DE", "ID"):
            for value in record.values(tag):
                for term in _parts(value):
                    if _key(term) in excluded:
                        continue
                    compact = _compact(term)
                    if compact:
                        buckets.setdefault(_singular(compact), Counter())[term] += 1
    mapping: dict[str, str] = {}
    groups = []
    for root, counts in sorted(buckets.items()):
        if len(counts) < 2 or any(_key(term) in auto_excluded for term in counts):
            continue
        targets = {explicit[_key(term)] for term in counts if _key(term) in explicit}
        if len(targets) > 1:
            raise ValueError(f"关键词自动归并与显式规则冲突：{', '.join(sorted(counts))}")
        canonical = next(iter(targets)) if targets else min(counts, key=lambda term: (
            _compact(term) != root, -counts[term],
            0 if " " in term else 1 if re.search(r"[-\u2010-\u2015\u2212]", term) else 2,
            term.casefold(), term))
        if sentence_style and not targets:
            canonical = keyword_canonical(canonical, list(counts))
        compact_forms = {_compact(term) for term in counts}
        reasons = []
        if len(compact_forms) < len(counts):
            reasons.append("空格或连字符写法")
        if len(compact_forms) > 1:
            reasons.append("英文单复数")
        for term in counts:
            mapping[_key(term)] = canonical
        groups.append({"canonical": canonical, "variants": dict(counts.most_common()), "reasons": reasons})
    return mapping, groups


COUNTRY_ALIASES = {
    "pr china": "China", "peoples r china": "China", "people's republic of china": "China",
    "中国": "China", "usa": "United States", "us": "United States",
    "united states of america": "United States", "uk": "United Kingdom",
}
COUNTRY_NAMES = {_key(name): name for name in COUNTRY_ALIASES.values()}


def _identity(kind: str, term: str) -> str:
    """Conservative identity key; never guess journal abbreviations or author initials."""
    value = _key(term).replace("&", " and ")
    if kind == "countries":
        return _key(COUNTRY_ALIASES.get(value.strip(" ."), term))
    if kind == "organizations":
        value = re.sub(r"\b(univ|hosp|dept)\.?(?=\s|$)", lambda m: {
            "univ": "university", "hosp": "hospital", "dept": "department"}[m.group(1)], value)
    return re.sub(r"[\s.,\-]+", "", value)


def _nonkeyword_terms(records: list[WosRecord], kind: str) -> Counter[str]:
    terms: Counter[str] = Counter()
    for record in records:
        if kind == "countries":
            found = [*record.values("CO"), *(term for value in record.values("C1")
                                             for term in country_address_terms(value))]
        elif kind == "organizations":
            found = [*institution_terms(record), *(value.split("]", 1)[-1].split(",", 1)[0]
                    for value in record.values("C1"))]
        elif kind == "authors":
            found = [part for tag in ("AU", "AF") for value in record.values(tag) for part in _parts(value)]
        else:
            found = [value for tag in ("SO", "JI", "J9") for value in record.values(tag)]
        terms.update(value.strip() for value in found if value.strip())
    return terms


def _automatic_nonkeyword(records: list[WosRecord], kind: str, explicit: dict[str, str],
                          excluded: set[str]) -> tuple[dict[str, str], list[dict]]:
    buckets: dict[str, Counter[str]] = {}
    for term, count in _nonkeyword_terms(records, kind).items():
        if _key(term) not in excluded:
            buckets.setdefault(_identity(kind, term), Counter())[term] += count
    mapping: dict[str, str] = {}
    groups: list[dict] = []
    for identity, counts in sorted(buckets.items()):
        alias = COUNTRY_NAMES.get(identity, "") if kind == "countries" else ""
        if len(counts) < 2 and not (alias and any(_key(term) != _key(alias) for term in counts)):
            continue
        targets = {explicit[_key(term)] for term in counts if _key(term) in explicit}
        if len(targets) > 1:
            continue  # Explicit user choices take priority over inference.
        canonical = next(iter(targets)) if targets else (alias or min(counts, key=lambda term: (-counts[term], len(term), term.casefold())))
        variants = {term: count for term, count in counts.items() if term != canonical}
        if not variants:
            continue
        for term in counts:
            if _key(term) not in explicit:
                mapping[_key(term)] = canonical
        groups.append({"canonical": canonical, "variants": variants,
                       "reasons": ["国家标准名称" if alias else "大小写、标点或常见机构缩写"]})
    return mapping, groups


def _replace_terms(values: list[str], mapping: dict[str, str], excluded: set[str]) -> list[str]:
    terms = [term for value in values for term in _parts(value)]
    if not any(_key(term) in mapping or _key(term) in excluded for term in terms):
        return values
    output = []
    seen = set()
    for term in terms:
        if _key(term) in excluded:
            continue
        updated = mapping.get(_key(term), term)
        if _key(updated) not in seen:
            seen.add(_key(updated))
            output.append(updated)
    return output


def _segment(value: str, mapping: dict[str, str]) -> str:
    core = value.strip()
    target = mapping.get(_key(core))
    end = ""
    if target is None and core.endswith("."):
        target = mapping.get(_key(core[:-1]))
        end = "."
    if target is None:
        return value
    return value[:len(value)-len(value.lstrip())] + target + end + value[len(value.rstrip()):]


def _address(value: str, kind: str, mapping: dict[str, str]) -> str:
    groups = split_address_groups(value)
    for position in range(0, len(groups), 2):
        groups[position] = _address_group(groups[position], kind, mapping)
    return "".join(groups)


def _address_group(value: str, kind: str, mapping: dict[str, str]) -> str:
    if kind == "organizations":
        prefix = ""
        rest = value
        if rest.lstrip().startswith("[") and "]" in rest:
            end = rest.index("]") + 1
            prefix, rest = rest[:end], rest[end:]
        head, comma, tail = rest.partition(",")
        return prefix + _segment(head, mapping) + comma + tail
    parts = value.split(",")
    for index in range(len(parts) - 1, max(-1, len(parts) - 4), -1):
        updated = _segment(parts[index], mapping)
        if updated != parts[index]:
            parts[index] = updated
            break
    return ",".join(parts)


def _serialize(record: WosRecord) -> str:
    lines = ["PT " + (record.text("PT") or "J")]
    for tag, values in record.fields.items():
        if tag == "PT":
            continue
        lines.extend(f"{tag} {value.replace(chr(10), ' ').replace(chr(13), ' ')}" for value in values)
    return "\n".join([*lines, "ER"])


def merge_vocabulary(records: list[WosRecord], spec: dict | None = None) -> tuple[list[WosRecord], dict]:
    """Return copies with merged terms and an auditable preview; inputs are unchanged."""
    spec = spec if spec is not None else {kind: [] for kind in KINDS}
    if not isinstance(spec, dict):
        raise ValueError("词汇规则必须是 JSON 对象")
    compiled = _compile(spec)
    auto = spec.get("autoKeywordVariants", True)
    if not isinstance(auto, bool):
        raise ValueError("autoKeywordVariants 必须是布尔值")
    auto_exclusions = spec.get("autoKeywordExclusions", [])
    if not isinstance(auto_exclusions, list) or not all(isinstance(value, str) for value in auto_exclusions):
        raise ValueError("autoKeywordExclusions 必须是字符串列表")
    keyword_map, excluded = compiled.get("keywords", ({}, set()))
    inferred, groups = _automatic_mapping(records, keyword_map, excluded,
                                          {_key(value) for value in auto_exclusions},
                                          spec.get("keywordCanonicalStyle", "sentence") == "sentence") if auto else ({}, [])
    keyword_map = {**inferred, **keyword_map}
    other_maps: dict[str, dict[str, str]] = {}
    other_groups: dict[str, list[dict]] = {}
    raw_exclusions = spec.get("autoMergeExclusions", {})
    if not isinstance(raw_exclusions, dict):
        raise ValueError("autoMergeExclusions 必须是对象")
    for kind in KINDS[:-1]:
        explicit = compiled.get(kind, ({}, set()))[0]
        values = raw_exclusions.get(kind, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"{kind} 自动合并排除项必须是字符串列表")
        inferred_map, inferred_groups = _automatic_nonkeyword(records, kind, explicit,
                                                              {_key(value) for value in values})
        other_maps[kind] = {**inferred_map, **explicit}
        other_groups[kind] = inferred_groups
    updated = []
    changes = []
    for record in records:
        fields = {tag: list(values) for tag, values in record.fields.items()}
        if fields.get("C3"):
            fields["C3"] = institution_terms(record)
        kind_changes: dict[str, dict[str, dict[str, list[str]]]] = {}
        def capture(kind: str, before: dict[str, list[str]], tags: tuple[str, ...]) -> None:
            changed = {tag: {"before": before.get(tag, []), "after": fields.get(tag, [])}
                       for tag in tags if before.get(tag, []) != fields.get(tag, [])}
            if changed:
                kind_changes[kind] = changed

        before = {tag: list(fields.get(tag, [])) for tag in ("DE", "ID")}
        for tag in ("DE", "ID"):
            if tag in fields:
                fields[tag] = _replace_terms(fields[tag], keyword_map, excluded)
        capture("keywords", before, ("DE", "ID"))
        before = {tag: list(fields.get(tag, [])) for tag in ("AU", "AF")}
        for tag in ("AU", "AF"):
            mapping = other_maps["authors"]
            if mapping and tag in fields:
                fields[tag] = _replace_terms(fields[tag], mapping, set())
        capture("authors", before, ("AU", "AF"))
        before = {tag: list(fields.get(tag, [])) for tag in ("SO", "JI", "J9")}
        for tag in ("SO", "JI", "J9"):
            mapping = other_maps["journals"]
            if mapping and tag in fields:
                fields[tag] = [_segment(value, mapping) for value in fields[tag]]
        capture("journals", before, ("SO", "JI", "J9"))
        for kind, tag in (("organizations", "C3"), ("countries", "CO")):
            before = {field: list(fields.get(field, [])) for field in (tag, "C1")}
            mapping = other_maps[kind]
            if mapping and tag in fields:
                fields[tag] = [_segment(value, mapping) for value in fields[tag]]
            if mapping and "C1" in fields:
                fields["C1"] = [_address(value, kind, mapping) for value in fields["C1"]]
            capture(kind, before, (tag, "C1"))
        copy = WosRecord(fields=fields, raw_block=record.raw_block,
                         source_file=record.source_file, source_index=record.source_index,
                         source_kind=record.source_kind, source_format=record.source_format)
        delta = {tag: {"before": record.fields.get(tag, []), "after": fields.get(tag, [])}
                 for tag in ("DE", "ID", "C1", "C3", "CO", "AU", "AF", "SO", "JI", "J9")
                 if record.fields.get(tag, []) != fields.get(tag, [])}
        if delta:
            copy.raw_block = _serialize(copy)
            changes.append({"record_id": record.record_id, "title": record.title,
                            "fields": delta, "kind_fields": kind_changes})
        updated.append(copy)
    return updated, {"scanned_record_count": len(records), "affected_record_count": len(changes),
                     "automatic_keyword_groups": groups, "automatic_groups": other_groups,
                     "changes": changes}
