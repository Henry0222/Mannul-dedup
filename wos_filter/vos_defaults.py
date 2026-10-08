"""Application-wide VOS preferences, kept apart from individual project data."""

from __future__ import annotations

import json
import os
from pathlib import Path


BUILD_KEYS = ("kind", "min", "max", "keyword_source", "scope")
NETWORK_ONLY_KEYS = {"json", "map", "network", "min_score", "max_score", "show_item"}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_if_changed(path: Path, value: dict) -> bool:
    if _read(path) == value:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def preferences_path(app_dir: Path) -> Path:
    return app_dir / "data" / "vos_preferences.json"


def graph_defaults_path(app_dir: Path) -> Path:
    return app_dir / "data" / "vos_graph_defaults.json"


def preferences(app_dir: Path) -> dict:
    return _read(preferences_path(app_dir))


def save_preferences(app_dir: Path, **changes) -> bool:
    value = preferences(app_dir)
    value.update(changes)
    return _write_if_changed(preferences_path(app_dir), value)


def graph_parameters(data: dict) -> dict:
    config = data.get("config", {})
    parameters = config.get("parameters", {}) if isinstance(config, dict) else {}
    if not isinstance(parameters, dict):
        return {}
    # Keep scalar presentation/layout choices. Scores and network data belong to the map.
    return {key: value for key, value in parameters.items()
            if key not in NETWORK_ONLY_KEYS and isinstance(value, (str, int, float, bool))}


def save_graph_defaults(path: Path, data: dict) -> bool:
    parameters = graph_parameters(data)
    if not parameters:
        return False
    return _write_if_changed(path, {"parameters": parameters})


def apply_graph_defaults(data: dict, path: Path) -> dict:
    stored = _read(path).get("parameters", {})
    if isinstance(stored, dict):
        data["config"]["parameters"].update({key: value for key, value in stored.items()
                                                if key not in NETWORK_ONLY_KEYS
                                                and isinstance(value, (str, int, float, bool))})
    return data
