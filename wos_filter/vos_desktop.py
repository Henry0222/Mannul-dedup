"""Hand a generated map and network to the native VOSviewer desktop application."""

from __future__ import annotations

from pathlib import Path
import subprocess

from .basic_export import export_basic_records
from .models import WosRecord
from .vos_export import export_saved_vos_map_network


def launch_vosviewer_desktop(executable: str | Path, json_path: str | Path) -> tuple[Path, Path]:
    program = Path(executable).expanduser().resolve()
    if not program.is_file() or program.suffix.casefold() != ".exe":
        raise ValueError("请选择已安装的 VOSviewer.exe。")
    source = Path(json_path).resolve(strict=True)
    map_path, network_path = export_saved_vos_map_network(source)
    subprocess.Popen([str(program), "-map", str(map_path), "-network", str(network_path)],
                     cwd=str(source.parent))
    return map_path, network_path


def launch_vosviewer_with_bibliography(executable: str | Path, destination: str | Path,
                                       records: list[WosRecord], header: str) -> Path:
    """Prepare source records for the native Create wizard and open VOSviewer."""
    program = Path(executable).expanduser().resolve()
    if not program.is_file() or program.suffix.casefold() != ".exe":
        raise ValueError("请选择已安装的 VOSviewer.exe。")
    bibliography = export_basic_records(destination, records, header)
    subprocess.Popen([str(program)], cwd=str(bibliography.parent))
    return bibliography
