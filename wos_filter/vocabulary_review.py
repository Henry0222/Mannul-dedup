"""Editable, source-traceable vocabulary merge pairs for the import preview."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

from .models import WosRecord
from .institution_fields import institution_terms
from .address_fields import country_address_terms
from .vocabulary import KINDS, _compile, _key, _parts, keyword_canonical, keyword_scope_change, merge_vocabulary


LABELS = {"countries": "国家", "organizations": "机构", "authors": "作者",
          "journals": "期刊", "keywords": "关键词"}
TAG_KINDS = {"DE": "keywords", "ID": "keywords", "C1": "organizations",
             "C3": "organizations", "CO": "countries", "AU": "authors",
             "AF": "authors", "SO": "journals", "JI": "journals", "J9": "journals"}


def _rules(spec: dict, kind: str) -> list[dict]:
    if "projects" in spec:
        for project in spec["projects"]:
            if project.get("objectType") == kind:
                return project.setdefault("rules", [])
        project = {"objectType": kind, "rules": []}
        spec["projects"].append(project)
        return project["rules"]
    if spec.get("objectType") == kind:
        return spec.setdefault("rules", [])
    if "objectType" in spec:
        existing = {"objectType": spec.pop("objectType"), "rules": spec.pop("rules", [])}
        spec["projects"] = [existing, {"objectType": kind, "rules": []}]
        return spec["projects"][-1]["rules"]
    return spec.setdefault(kind, [])


def _observed(records: list[WosRecord], kind: str) -> Counter[str]:
    found: Counter[str] = Counter()
    for record in records:
        if kind == "keywords":
            terms = [part for tag in ("DE", "ID") for value in record.values(tag) for part in _parts(value)]
        elif kind == "authors":
            terms = [part for tag in ("AU", "AF") for value in record.values(tag) for part in _parts(value)]
        elif kind == "journals":
            terms = [value.strip() for tag in ("SO", "JI", "J9") for value in record.values(tag)]
        elif kind == "organizations":
            terms = institution_terms(record)
            terms += [value.split("]", 1)[-1].split(",", 1)[0].strip()
                      for value in record.values("C1")]
        else:
            terms = [value.strip() for value in record.values("CO")]
            terms += [term for value in record.values("C1") for term in country_address_terms(value)]
        found.update(_key(term) for term in terms if term)
    return found


def list_merge_pairs(records: list[WosRecord], spec: dict, report: dict, kind: str) -> list[dict]:
    """List explicit and conservative inferred pairs, without truncation."""
    if kind not in KINDS:
        raise ValueError("未知词汇类别。")
    observed = _observed(records, kind)
    ai_sources = {(_key(str(row.get("target", ""))), _key(str(row.get("variant", "")))): row
                  for row in spec.get("aiPairSources", [])
                  if isinstance(row, dict) and row.get("kind") == kind}
    rows: dict[tuple[str, str], dict] = {}
    automatic = (report.get("automatic_keyword_groups", []) if kind == "keywords"
                 else report.get("automatic_groups", {}).get(kind, []))
    if automatic:
        for group in automatic:
            target = group["canonical"]
            for variant in group["variants"]:
                if variant != target:
                    rows[(_key(target), variant)] = {
                        "target": target, "variant": variant, "origin": "自动",
                        "reason": "、".join(group.get("reasons", [])),
                        "occurrences": observed[_key(variant)],
                    }
    for rule in _rules(deepcopy(spec), kind):
        target = str(rule.get("target", rule.get("primaryOutputText", ""))).strip()
        variants = rule.get("variants")
        if variants is None:
            variants = [rule.get("primaryKey", target), *rule.get("secondaryKeys", [])]
        for variant in variants:
            variant = str(variant).strip()
            if variant and variant != target:
                ai = ai_sources.get((_key(target), _key(variant)))
                reason = str(ai.get("reason") or "AI 建议") if ai else "项目规则"
                if ai and kind == "keywords" and keyword_scope_change(target, variant):
                    reason = "待核查：疑似上下位或限定词关系；" + reason
                rows[(_key(target), variant)] = {
                    "target": target, "variant": variant, "origin": "DeepSeek" if ai else "人工",
                    "reason": reason,
                    "occurrences": observed[_key(variant)],
                }
    return sorted(rows.values(), key=lambda row: (row["target"].casefold(), row["variant"].casefold()))


def upsert_pair(spec: dict, kind: str, target: str, variant: str) -> dict:
    """Set one pair, moving a variant away from any conflicting explicit rule."""
    target, variant = target.strip(), variant.strip()
    if kind not in KINDS or not target or not variant or target == variant:
        raise ValueError("请选择类别并填写两个不同的词语。")
    if any(char in target + variant for char in "\t\r\n;；\x00"):
        raise ValueError("词语不能包含制表符、换行或分号。")
    result = deepcopy(spec)
    result["aiPairSources"] = [row for row in result.get("aiPairSources", [])
                               if not (row.get("kind") == kind and _key(row.get("variant", "")) == _key(variant))]
    rules = _rules(result, kind)
    for rule in rules:
        values = rule.get("variants")
        if values is None:
            values = [rule.get("primaryKey", rule.get("target", "")), *rule.get("secondaryKeys", [])]
        rule["variants"] = [value for value in values if _key(value) != _key(variant)]
        rule["target"] = rule.get("target", rule.get("primaryOutputText", ""))
        for old in ("primaryOutputText", "primaryKey", "secondaryKeys"):
            rule.pop(old, None)
    rules[:] = [rule for rule in rules if rule["variants"]]
    existing = next((rule for rule in rules if _key(rule["target"]) == _key(target)), None)
    if existing is None:
        rules.append({"target": target, "variants": [variant]})
    else:
        existing["variants"].append(variant)
    if kind == "keywords":
        result["autoKeywordExclusions"] = [value for value in result.get("autoKeywordExclusions", [])
                                           if _key(value) != _key(variant)]
    else:
        exclusions = result.setdefault("autoMergeExclusions", {})
        exclusions[kind] = [value for value in exclusions.get(kind, [])
                            if _key(value) != _key(variant)]
    return result


def refresh_keyword_canonicals(spec: dict) -> tuple[dict, int]:
    """Restyle inferred and AI keyword targets without rewriting operator rules."""
    draft = deepcopy(spec)
    draft["keywordCanonicalStyle"] = "sentence"
    existing = _compile(draft).get("keywords", ({}, set()))[0]
    ai_rows = [row for row in draft.get("aiPairSources", [])
               if isinstance(row, dict) and row.get("kind") == "keywords"]
    ai_pairs = {(_key(str(row.get("target", ""))), _key(str(row.get("variant", ""))))
                for row in ai_rows}
    changed = 0
    for rule in _rules(draft, "keywords"):
        old_target = str(rule.get("target", rule.get("primaryOutputText", ""))).strip()
        variants = rule.get("variants")
        if variants is None:
            variants = [rule.get("primaryKey", old_target), *rule.get("secondaryKeys", [])]
        if not variants or not all((_key(old_target), _key(str(value))) in ai_pairs
                                   for value in variants):
            continue
        target = keyword_canonical(old_target, list(variants))
        if target == old_target:
            continue
        owner = existing.get(_key(target))
        if owner is not None and owner != old_target:
            continue
        rule["target"] = target
        rule["variants"] = list(variants)
        for old in ("primaryOutputText", "primaryKey", "secondaryKeys"):
            rule.pop(old, None)
        if _key(old_target) != _key(target) and _key(old_target) not in {_key(value) for value in variants}:
            rule["variants"].append(old_target)
            marker = {"kind": "keywords", "target": target,
                      "variant": old_target, "reason": "刷新规范词"}
            draft.setdefault("aiPairSources", []).append(marker)
            ai_rows.append(marker)
        for row in ai_rows:
            if _key(str(row.get("target", ""))) == _key(old_target):
                row["target"] = target
        for key in [_key(old_target), *(_key(value) for value in rule["variants"])]:
            existing[key] = target
        existing[_key(target)] = target
        changed += 1
    _compile(draft)
    return draft, changed


def remove_pair(spec: dict, kind: str, target: str, variant: str, *, automatic: bool = False) -> dict:
    result = deepcopy(spec)
    result["aiPairSources"] = [row for row in result.get("aiPairSources", [])
                               if not (row.get("kind") == kind and _key(row.get("variant", "")) == _key(variant))]
    rules = _rules(result, kind)
    for rule in rules:
        if _key(str(rule.get("target", rule.get("primaryOutputText", "")))) != _key(target):
            continue
        values = rule.get("variants")
        if values is None:
            values = [rule.get("primaryKey", target), *rule.get("secondaryKeys", [])]
        rule["variants"] = [value for value in values if _key(value) != _key(variant)]
        rule["target"] = target
        for old in ("primaryOutputText", "primaryKey", "secondaryKeys"):
            rule.pop(old, None)
    rules[:] = [rule for rule in rules if rule.get("variants")]
    if kind == "keywords" and automatic:
        excluded = result.setdefault("autoKeywordExclusions", [])
        if _key(variant) not in {_key(value) for value in excluded}:
            excluded.append(variant)
    elif automatic:
        excluded = result.setdefault("autoMergeExclusions", {}).setdefault(kind, [])
        if _key(variant) not in {_key(value) for value in excluded}:
            excluded.append(variant)
    return result


def read_pairs_txt(path: str | Path) -> list[tuple[int, str, str]]:
    """Read the same two-column tab-delimited format used by export."""
    raw = Path(path).read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("文件编码无法识别；请保存为 UTF-8 或 GB18030 文本。") from exc
    rows: list[tuple[int, str, str]] = []
    for number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(f"第 {number} 行格式错误：每行须为“保留词<Tab>合并词”。")
        rows.append((number, parts[0].strip(), parts[1].strip()))
    if not rows:
        raise ValueError("合并文件为空，没有可导入的规则。")
    return rows


def import_pairs_txt(spec: dict, kind: str, path: str | Path, records: list[WosRecord],
                     *, auto_keyword: bool = True) -> tuple[dict, int]:
    """Stage all pairs with the same validation as manual edits; never partially import."""
    if kind not in KINDS:
        raise ValueError("请先选择合并类别。")
    draft = deepcopy(spec)
    rows = read_pairs_txt(path)
    for number, target, variant in rows:
        try:
            draft = upsert_pair(draft, kind, target, variant)
        except ValueError as exc:
            raise ValueError(f"第 {number} 行：{exc}") from exc
    merge_vocabulary(records, {**draft, "autoKeywordVariants": auto_keyword})
    return draft, len(rows)


def export_pairs_txt(path: str | Path, pairs: list[dict]) -> Path:
    destination = Path(path)
    lines = [f"{row['target']}\t{row['variant']}" for row in pairs]
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return destination
