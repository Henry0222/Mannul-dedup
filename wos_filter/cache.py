from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import Classification


class ResultCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS classifications (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def get(self, cache_key: str) -> Classification | None:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT result_json FROM classifications WHERE cache_key = ?", (cache_key,)
                ).fetchone()
            finally:
                connection.close()
        if row is None:
            return None
        result = Classification.from_dict(json.loads(row[0]))
        result.cached = True
        return result

    def put(self, cache_key: str, result: Classification) -> None:
        payload = result.to_dict()
        payload["cached"] = False
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    "INSERT OR REPLACE INTO classifications(cache_key, result_json) VALUES (?, ?)",
                    (cache_key, json.dumps(payload, ensure_ascii=False)),
                )
                connection.commit()
            finally:
                connection.close()


class JsonlAuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, item: dict) -> None:
        line = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
