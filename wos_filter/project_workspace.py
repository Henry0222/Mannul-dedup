"""Persistent, isolated bibliography workspaces for the desktop application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from .models import DuplicateEntry, WosRecord


def record_key(record: WosRecord) -> str:
    identity = f"{record.source_file}\0{record.source_index}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def record_to_json(record: WosRecord) -> dict:
    return {
        "fields": record.fields, "raw_block": record.raw_block,
        "source_file": record.source_file, "source_index": record.source_index,
        "source_kind": record.source_kind, "source_format": record.source_format,
    }


def record_from_json(item: dict) -> WosRecord:
    return WosRecord(
        fields={str(k): [str(v) for v in values] for k, values in item["fields"].items()},
        raw_block=str(item["raw_block"]), source_file=str(item["source_file"]),
        source_index=int(item["source_index"]), source_kind=str(item.get("source_kind", "wos")),
        source_format=str(item.get("source_format", "wos_tagged")),
    )


def active_records(project: dict, records: list[WosRecord]) -> list[WosRecord]:
    excluded = set(project.get("excluded_keys", []))
    return [record for record in records if record_key(record) not in excluded]


def duplicate_entries(project: dict, records: list[WosRecord]) -> list[DuplicateEntry]:
    by_key = {record_key(record): record for record in records}
    entries: list[DuplicateEntry] = []
    for run in project.get("runs", []):
        if not run.get("applied"):
            continue
        for group in run.get("groups", []):
            if group.get("status") != "deduped":
                continue
            primary = by_key.get(group.get("primary"))
            if primary is None:
                continue
            for key in group["record_keys"]:
                if key == group["primary"] or key not in by_key:
                    continue
                duplicate = by_key[key]
                entries.append(DuplicateEntry(
                    duplicate_record_id=duplicate.record_id,
                    kept_record_id=primary.record_id, reason=run["preset"],
                    duplicate_source=duplicate.source_file, kept_source=primary.source_file,
                ))
    return entries


class WorkspaceStore:
    VIEW_FIELDS = ("query", "year_start", "year_end", "auto_keyword", "chart_settings",
                   "vos_settings", "last_vos_path", "chart_export_dir")
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        if not self.index_path.exists():
            project = self.create("默认项目")
            self._write(self.index_path, {"project_ids": [project["id"]], "last_project_id": project["id"]})

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def _index(self) -> dict:
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _project_path(self, project_id: str) -> Path:
        uuid.UUID(project_id)
        return self.root / project_id / "project.json"

    def list_projects(self) -> list[dict]:
        return [self.load(project_id) for project_id in self._index()["project_ids"]]

    def list_project_summaries(self) -> list[dict[str, str]]:
        """Read sidebar labels without deserializing every bibliography."""
        summaries = []
        for project_id in self._index()["project_ids"]:
            path = self._project_path(project_id)
            with path.open("r", encoding="utf-8") as stream:
                header = stream.read(16384)
            match = re.search(r'^\s*"name"\s*:\s*("(?:\\.|[^"\\])*")\s*,?\s*$', header, re.MULTILINE)
            name = json.loads(match.group(1)) if match else self.load(project_id)["name"]
            summaries.append({"id": project_id, "name": name})
        return summaries

    def last_project_id(self) -> str:
        return self._index()["last_project_id"]

    def create(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("项目名称不能为空。")
        project = {
            "id": str(uuid.uuid4()), "name": name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": [], "file_results": [], "records": [], "warnings": [],
            "excluded_keys": [], "runs": [], "needs_dedupe": True,
            "ai_results_stale": False, "import_generation": 0,
            "query": "", "year_start": "", "year_end": "",
        }
        self.save(project)
        if self.index_path.exists():
            index = self._index()
            index["project_ids"].append(project["id"])
            index["last_project_id"] = project["id"]
            self._write(self.index_path, index)
        return project

    def load(self, project_id: str) -> dict:
        project_path = self._project_path(project_id)
        project = json.loads(project_path.read_text(encoding="utf-8"))
        view_path = project_path.with_name("view.json")
        if view_path.exists() and view_path.stat().st_mtime_ns >= project_path.stat().st_mtime_ns:
            view = json.loads(view_path.read_text(encoding="utf-8"))
            if isinstance(view, dict):
                project.update({key: view[key] for key in self.VIEW_FIELDS if key in view})
        project.pop("skip_dedupe", None)
        return project

    def save_view(self, project: dict) -> None:
        view = {key: project[key] for key in self.VIEW_FIELDS if key in project}
        self._write(self._project_path(project["id"]).with_name("view.json"), view)

    def save(self, project: dict) -> None:
        self._write(self._project_path(project["id"]), project)
        self._project_path(project["id"]).with_name("view.json").unlink(missing_ok=True)

    def activate(self, project_id: str) -> None:
        index = self._index()
        if project_id not in index["project_ids"]:
            raise ValueError("项目不存在。")
        if index["last_project_id"] == project_id:
            return
        index["last_project_id"] = project_id
        self._write(self.index_path, index)

    def rename(self, project_id: str, name: str) -> dict:
        project = self.load(project_id)
        project["name"] = name.strip()
        if not project["name"]:
            raise ValueError("项目名称不能为空。")
        self.save(project)
        return project

    def delete(self, project_id: str) -> tuple[str, Path]:
        """Remove a project from the sidebar and archive its data locally."""
        index = self._index()
        if project_id not in index["project_ids"]:
            raise ValueError("项目不存在。")
        if len(index["project_ids"]) == 1:
            self.create("默认项目")
            index = self._index()
        source = self._project_path(project_id).parent.resolve(strict=True)
        root = self.root.resolve(strict=True)
        if source.parent != root:
            raise ValueError("项目目录超出工作区，已取消删除。")
        trash_root = root / ".trash"
        trash_root.mkdir(exist_ok=True)
        if trash_root.resolve(strict=True).parent != root:
            raise ValueError("项目回收目录超出工作区，已取消删除。")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archive = trash_root / f"{project_id}_{stamp}"
        suffix = 1
        while archive.exists():
            archive = trash_root / f"{project_id}_{stamp}_{suffix:02d}"
            suffix += 1
        os.replace(source, archive)
        try:
            index["project_ids"].remove(project_id)
            if index["last_project_id"] == project_id:
                index["last_project_id"] = index["project_ids"][0]
            self._write(self.index_path, index)
        except OSError:
            os.replace(archive, source)
            raise
        return index["last_project_id"], archive
