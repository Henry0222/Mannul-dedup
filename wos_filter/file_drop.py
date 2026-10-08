from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .source_import import SUPPORTED_SUFFIXES


def normalized_path_key(path: str | Path) -> str:
    """Return a stable, case-insensitive key for Windows path deduplication."""
    return os.path.normcase(str(Path(path).resolve())).casefold()


def _expand_inputs(inputs: Iterable[str | Path], suffixes: frozenset[str], label: str) -> tuple[list[str], list[str]]:
    """Expand files/folders with stable order and path deduplication."""
    accepted: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()

    def add_file(path: Path) -> None:
        resolved = path.resolve()
        key = normalized_path_key(resolved)
        if key not in seen:
            seen.add(key)
            accepted.append(str(resolved))

    for raw_input in inputs:
        path = Path(raw_input).expanduser()
        if not path.exists():
            warnings.append(f"路径不存在，已忽略：{path}")
            continue
        if path.is_file():
            if path.suffix.casefold() in suffixes:
                add_file(path)
            else:
                warnings.append(f"不是{label}文件，已忽略：{path}")
            continue
        if path.is_dir():
            try:
                matches = sorted(
                    (
                        candidate
                        for candidate in path.rglob("*")
                        if candidate.is_file() and candidate.suffix.casefold() in suffixes
                    ),
                    key=lambda candidate: str(candidate).casefold(),
                )
            except OSError as exc:
                warnings.append(f"无法读取文件夹，已忽略：{path}（{exc}）")
                continue
            if not matches:
                warnings.append(f"文件夹中没有{label}文件：{path}")
            for match in matches:
                add_file(match)
            continue
        warnings.append(f"不支持的路径类型，已忽略：{path}")

    return accepted, warnings


def expand_txt_inputs(inputs: Iterable[str | Path]) -> tuple[list[str], list[str]]:
    """Compatibility helper for callers explicitly requesting only TXT files."""
    return _expand_inputs(inputs, frozenset({".txt"}), " TXT ")


def expand_bibliography_inputs(inputs: Iterable[str | Path]) -> tuple[list[str], list[str]]:
    """Accept supported bibliographic file extensions without choosing a source."""
    return _expand_inputs(inputs, SUPPORTED_SUFFIXES, "支持的题录")
