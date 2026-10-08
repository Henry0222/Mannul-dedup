"""Cross-project catalog of applied non-keyword vocabulary merges."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from .vocabulary import KINDS, _key
from .vocabulary_review import _rules


SHARED_KINDS = KINDS[:-1]


class SharedVocabularyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, list[dict[str, str]]]:
        if not self.path.exists():
            return {kind: [] for kind in SHARED_KINDS}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("共享合并目录必须是 JSON 对象")
        result = {}
        for kind in SHARED_KINDS:
            rows = payload.get(kind, [])
            if not isinstance(rows, list):
                raise ValueError(f"共享合并目录 {kind} 必须是列表")
            result[kind] = [{"target": str(row["target"]), "variant": str(row["variant"])}
                            for row in rows if isinstance(row, dict) and row.get("target") and row.get("variant")]
        return result

    def overlay(self, project_spec: dict) -> dict:
        """Apply shared pairs only where this project's own rule has no decision."""
        result = deepcopy(project_spec)
        catalog = self.load()
        for kind in SHARED_KINDS:
            assigned = set()
            for rule in _rules(deepcopy(project_spec), kind):
                values = rule.get("variants")
                if values is None:
                    values = [rule.get("primaryKey", rule.get("target", "")), *rule.get("secondaryKeys", [])]
                assigned.update(_key(value) for value in values if value)
            assigned.update(_key(value) for value in project_spec.get("autoMergeExclusions", {}).get(kind, []))
            rules = _rules(result, kind)
            by_target = {_key(str(rule.get("target", rule.get("primaryOutputText", "")))): rule
                         for rule in rules}
            inherited: dict[str, dict[str, str]] = {}
            for row in catalog[kind]:
                if _key(row["variant"]) not in assigned:
                    inherited[_key(row["variant"])] = row
            for row in inherited.values():
                target, variant = row["target"], row["variant"]
                target_key = _key(target)
                rule = by_target.get(target_key)
                if rule is None:
                    rule = {"target": target, "variants": []}
                    rules.append(rule)
                    by_target[target_key] = rule
                elif "variants" not in rule:
                    rule["variants"] = [rule.get("primaryKey", rule.get("target", "")),
                                        *rule.get("secondaryKeys", [])]
                    rule["target"] = rule.get("target", rule.get("primaryOutputText", ""))
                    for old in ("primaryOutputText", "primaryKey", "secondaryKeys"):
                        rule.pop(old, None)
                rule["variants"].append(variant)
        return result

    def remember(self, records: list, pairs: dict[str, list[dict]], *,
                 removed_variants: dict[str, set[str]] | None = None) -> int:
        """Persist applied pairs and only remove pairs explicitly deleted here."""
        catalog = self.load()
        total = 0
        removed_variants = removed_variants or {}
        for kind in SHARED_KINDS:
            removed = {_key(value) for value in removed_variants.get(kind, set())}
            current = {_key(row["variant"]): {"target": row["target"], "variant": row["variant"]}
                       for row in catalog[kind] if _key(row["variant"]) not in removed}
            for row in pairs.get(kind, []):
                if row["target"] != row["variant"]:
                    current[_key(row["variant"])] = {"target": row["target"], "variant": row["variant"]}
            catalog[kind] = sorted(current.values(), key=lambda row: (_key(row["target"]), _key(row["variant"])))
            total += len(catalog[kind])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)
        return total
