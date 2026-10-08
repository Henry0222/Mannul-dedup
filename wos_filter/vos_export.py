"""Save an edited VOSviewer network as desktop-compatible map/network files."""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path


def export_vos_map_network(data: dict, json_path: str | Path) -> tuple[Path, Path]:
    """Write the pair of tab-separated files used by VOSviewer desktop."""
    network = data.get("network", {})
    items = network.get("items", [])
    links = network.get("links", [])
    if not isinstance(items, list) or not isinstance(links, list) or not items:
        raise ValueError("当前 VOS 图谱没有可导出的节点或连线。")
    base = Path(json_path)
    map_path = base.with_name(base.stem + "_map.txt")
    network_path = base.with_name(base.stem + "_network.txt")
    weight_names = sorted({name for item in items for name in item.get("weights", {})})
    score_names = sorted({name for item in items for name in item.get("scores", {})})
    has_coordinates = all(isinstance(item.get(axis), (int, float)) and
                          math.isfinite(item[axis]) for item in items for axis in ("x", "y"))
    has_clusters = all(isinstance(item.get("cluster"), int) and item["cluster"] > 0
                       for item in items)
    columns = ["id", "label"]
    if has_coordinates:
        columns += ["x", "y"]
    if has_clusters:
        columns.append("cluster")
    columns += [f"weight<{name}>" for name in weight_names]
    columns += [f"score<{name}>" for name in score_names]
    temporary_map = map_path.with_suffix(map_path.suffix + ".tmp")
    temporary_network = network_path.with_suffix(network_path.suffix + ".tmp")
    try:
        with temporary_map.open("w", encoding="utf-8", newline="") as handle:
            handle.write("\t".join(columns) + "\n")
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n",
                                quoting=csv.QUOTE_NONNUMERIC)
            for item in items:
                row = [item.get("id", ""), item.get("label", "")]
                if has_coordinates:
                    row += [item["x"], item["y"]]
                if has_clusters:
                    row.append(item["cluster"])
                row += [item.get("weights", {}).get(name, "") for name in weight_names]
                row += [item.get("scores", {}).get(name, "") for name in score_names]
                writer.writerow(row)
        with temporary_network.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            for link in links:
                writer.writerow((link["source_id"], link["target_id"], link.get("strength", 1)))
        os.replace(temporary_map, map_path)
        os.replace(temporary_network, network_path)
    finally:
        temporary_map.unlink(missing_ok=True)
        temporary_network.unlink(missing_ok=True)
    return map_path, network_path


def export_saved_vos_map_network(json_path: str | Path) -> tuple[Path, Path]:
    path = Path(json_path)
    return export_vos_map_network(json.loads(path.read_text(encoding="utf-8")), path)
