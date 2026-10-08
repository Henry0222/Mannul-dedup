"""Launch the bundled VOSviewer Online component in a local WebView2 window."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import base64
import threading
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from .vos_export import export_saved_vos_map_network
from .vos_defaults import graph_defaults_path, save_graph_defaults


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def viewer_html() -> Path:
    return resource_dir() / "vosviewer_frontend" / "dist" / "index.html"


def launch_viewer(data_path: str | Path) -> subprocess.Popen:
    if not viewer_html().is_file():
        raise RuntimeError("内置 VOSviewer 界面缺失，请重新构建或安装完整发布包。")
    try:
        import webview  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("未安装 pywebview，无法打开内置 VOSviewer 窗口。") from exc
    source = Path(data_path).resolve()
    child_env = os.environ.copy()
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--vosviewer", str(source)]
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        command = [sys.executable, str(resource_dir() / "main.py"), "--vosviewer", str(source)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(command, cwd=source.parent, creationflags=flags, env=child_env)


class _DataBridge:
    def __init__(self, data_path: Path, defaults_path: Path | None = None) -> None:
        self.data_path = data_path
        self.defaults_path = defaults_path
        self._lock = threading.Lock()
        self._last_state = ""

    def load_data(self) -> dict:
        data = json.loads(self.data_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("network"), dict):
            raise ValueError("VOS 图谱 JSON 缺少 network。")
        self._last_state = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return data

    def save_state(self, payload: str) -> dict:
        if len(payload) > 50_000_000:
            raise ValueError("VOS 图谱数据超过 50 MB，未自动保存。")
        data = json.loads(payload)
        network = data.get("network") if isinstance(data, dict) else None
        if not isinstance(network, dict) or not isinstance(network.get("items"), list):
            raise ValueError("VOSviewer 返回的图谱数据不完整。")
        strengths: Counter[int] = Counter()
        for link in network.get("links", []):
            strength = link.get("strength", 1)
            strengths[link["source_id"]] += strength
            strengths[link["target_id"]] += strength
        for item in network["items"]:
            item["weights"] = {**item.get("weights", {}),
                               "Total link strength": strengths[item["id"]]}
        with self._lock:
            # The VOSviewer component exports only its own fields. Preserve manual
            # styling when its ordinary autosave follows an editor save.
            if "mannul_editor" not in data and self.data_path.is_file():
                previous = json.loads(self.data_path.read_text(encoding="utf-8"))
                if isinstance(previous.get("mannul_editor"), dict):
                    data["mannul_editor"] = previous["mannul_editor"]
            canonical = json.dumps(data, ensure_ascii=False, sort_keys=True)
            if canonical != self._last_state:
                temporary = self.data_path.with_suffix(self.data_path.suffix + ".tmp")
                try:
                    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    os.replace(temporary, self.data_path)
                finally:
                    temporary.unlink(missing_ok=True)
                self._last_state = canonical
                changed = True
            else:
                changed = False
            if self.defaults_path is not None:
                save_graph_defaults(self.defaults_path, data)
        return {"changed": changed, "path": str(self.data_path)}

    def save_svg(self, source: str) -> dict:
        if len(source) > 10_000_000:
            raise ValueError("SVG 文件超过 10 MB。")
        root = ET.fromstring(source)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("导出内容不是 SVG 图像。")
        for element in root.iter():
            if element.tag in {"{http://www.w3.org/2000/svg}script", "{http://www.w3.org/2000/svg}foreignObject"}:
                raise ValueError("SVG 不允许脚本或嵌入网页。")
            if any(name.lower().startswith("on") or name.endswith("}href") or name == "href"
                   for name in element.attrib):
                raise ValueError("SVG 包含不允许的事件或外部链接。")
        stamp = datetime.now().strftime("%y%m%d%H%M")
        with self._lock:
            path = self.data_path.with_name(f"{self.data_path.stem}_{stamp}.editable.svg")
            suffix = 1
            while path.exists():
                path = self.data_path.with_name(f"{self.data_path.stem}_{stamp}_{suffix:02d}.editable.svg")
                suffix += 1
            path.write_text(source, encoding="utf-8")
        return {"path": str(path)}

    def export_map(self) -> dict:
        with self._lock:
            map_path, network_path = export_saved_vos_map_network(self.data_path)
        return {"map": str(map_path), "network": str(network_path)}

    def save_png(self, data_url: str) -> dict:
        prefix = "data:image/png;base64,"
        if not data_url.startswith(prefix):
            raise ValueError("截图数据不是 PNG 图像。")
        image = base64.b64decode(data_url[len(prefix):], validate=True)
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("截图数据不是有效的 PNG 文件。")
        stamp = datetime.now().strftime("%y%m%d%H%M")
        path = self.data_path.with_name(f"vos_screenshot_{stamp}.png")
        suffix = 1
        while path.exists():
            path = self.data_path.with_name(f"vos_screenshot_{stamp}_{suffix:02d}.png")
            suffix += 1
        path.write_bytes(image)
        return {"path": str(path)}


def run_viewer(data_path: str | Path) -> None:
    import webview
    from .gui import get_app_dir

    path = Path(data_path).resolve(strict=True)
    html = viewer_html()
    if not html.is_file():
        raise FileNotFoundError(f"VOSviewer 资源不存在：{html}")
    window = webview.create_window("Mannul-dedup · VOS 图谱", str(html),
                                   js_api=_DataBridge(path, graph_defaults_path(get_app_dir())), width=1250, height=840,
                                   min_size=(800, 560), background_color="#F3FAFA")
    report = os.environ.get("MANNUL_VOS_SELFTEST_REPORT")
    if report:
        def probe() -> None:
            try:
                for _ in range(30):
                    time.sleep(1)
                    state = window.evaluate_js("({text:document.body.innerText.slice(0,600),"
                                               "canvas:document.querySelectorAll('canvas').length,"
                                               "svg:document.querySelectorAll('svg').length,"
                                               "inputs:[...document.querySelectorAll('#vos-component input')].slice(0,30).map(el=>({type:el.type,value:el.value,label:el.labels?.[0]?.textContent||el.closest('.MuiFormControl-root')?.querySelector('label')?.textContent||''})),"
                                               "error:document.documentElement.dataset.error || ''})")
                    text = state.get("text", "") if state else ""
                    if state and (state.get("error") or "图谱读取失败" in text or
                                  (state.get("canvas") and "Running " not in text)):
                        break
                Path(report).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                Path(report).write_text(json.dumps({"error": str(exc)}, ensure_ascii=False), encoding="utf-8")
            finally:
                window.destroy()
        webview.start(probe, gui="edgechromium", http_server=True, debug=True)
    else:
        webview.start(gui="edgechromium", http_server=True, debug=False)
