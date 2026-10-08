"""High-resolution PNG export for the two local bibliometric charts."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


MAX_PIXELS = 36_000_000

PALETTES = {
    "湖蓝薄荷": {"trend": "#2F7295", "ranking": "#65B7B1", "WOS": "#2F7295",
                 "CNKI": "#70B9B2", "regression": "#B24C3E", "label": "#24495E"},
    "经典蓝橙": {"trend": "#4C78A8", "ranking": "#F58518"},
    "色盲友好": {"trend": "#009E73", "ranking": "#E69F00"},
    "学术灰蓝": {"trend": "#557A95", "ranking": "#B87D4B"},
    "高对比": {"trend": "#1B9E77", "ranking": "#E7298A"},
}


def validate_export_settings(width_in: float, height_in: float, dpi: int) -> tuple[int, int]:
    if not 5 <= width_in <= 30 or not 4 <= height_in <= 20 or not 72 <= dpi <= 600:
        raise ValueError("宽度须为 5–30 英寸，高度为 4–20 英寸，DPI 为 72–600。")
    width, height = round(width_in * dpi), round(height_in * dpi)
    if width * height > MAX_PIXELS:
        raise ValueError("当前尺寸会生成过大的图片；请降低宽度、高度或 DPI。")
    return width, height


def validate_color(value: str) -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("图表颜色必须是 #RRGGBB 格式。")
    return value.upper()


def export_trend_png(path: str | Path, rows: list[dict[str, int]], *,
                     title: str, subtitle: str, color: str,
                     width_in: float = 10, height_in: float = 7, dpi: int = 300) -> Path:
    if not rows:
        raise ValueError("暂无年度发文数据可导出。")
    from PIL import Image, ImageDraw

    width, height = validate_export_settings(width_in, height_in, dpi)
    color = validate_color(color)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    _header(draw, width, height, title, subtitle)
    left, right = round(width * .09), round(width * .96)
    top, bottom = round(height * .22), round(height * .85)
    max_count = max(row["papers"] for row in rows)
    axis_font = _font(round(height * .021))
    tick_values = list(range(max_count + 1)) if max_count <= 4 else [round(max_count * tick / 4) for tick in range(5)]
    for tick_value in dict.fromkeys(tick_values):
        y = bottom - (bottom - top) * tick_value / max_count
        draw.line((left, y, right, y), fill="#E6E8EB", width=max(1, round(dpi / 120)))
        draw.text((left - round(width * .015), y), str(tick_value),
                  anchor="rm", fill="#52606D", font=axis_font)
    slot = (right - left) / len(rows)
    label_every = max(1, (len(rows) + 15) // 16)
    for index, row in enumerate(rows):
        x = left + (index + .5) * slot
        bar_h = (bottom - top) * row["papers"] / max_count
        half = min(slot * .36, width * .028)
        draw.rectangle((round(x - half), round(bottom - bar_h), round(x + half), bottom), fill=color)
        if index % label_every == 0 or index == len(rows) - 1:
            draw.text((x, bottom + round(height * .018)), str(row["year"]),
                      anchor="mt", fill="#34495E", font=axis_font)
        if len(rows) <= 16:
            draw.text((x, bottom - bar_h - round(height * .008)), str(row["papers"]),
                      anchor="mb", fill="#17365D", font=axis_font)
    draw.text((left, round(height * .93)), "年份", fill="#52606D", font=axis_font)
    draw.text((right, round(height * .93)), "发文量", anchor="ra", fill="#52606D", font=axis_font)
    return _save(image, path, dpi)


def export_ranking_png(path: str | Path, rows: list[dict[str, int | str]], *,
                       title: str, subtitle: str, color: str,
                       width_in: float = 10, height_in: float = 7, dpi: int = 300) -> Path:
    if not rows:
        raise ValueError("暂无排名数据可导出。")
    from PIL import Image, ImageDraw

    width, height = validate_export_settings(width_in, height_in, dpi)
    color = validate_color(color)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    _header(draw, width, height, title, subtitle)
    shown = rows[:20]
    left, right = round(width * .39), round(width * .94)
    top, bottom = round(height * .22), round(height * .92)
    slot = (bottom - top) / len(shown)
    max_count = max(int(row["papers"]) for row in shown)
    row_font = _font(min(round(height * .022), round(slot * .48)))
    number_font = _font(min(round(height * .022), round(slot * .48)), bold=True)
    for index, row in enumerate(shown):
        y = top + (index + .5) * slot
        label = _fit_text(draw, str(row["name"]), row_font, left - round(width * .07))
        draw.text((round(width * .03), y), f"{index + 1:>2}.  {label}", anchor="lm",
                  fill="#273444", font=row_font)
        bar_width = (right - left) * int(row["papers"]) / max_count
        half = max(2, min(round(slot * .26), round(height * .013)))
        draw.rounded_rectangle((left, round(y - half), round(left + bar_width), round(y + half)),
                               radius=half, fill=color)
        draw.text((min(width - round(width * .03), left + bar_width + round(width * .01)), y),
                  str(row["papers"]), anchor="lm", fill="#17365D", font=number_font)
    return _save(image, path, dpi)


def _header(draw: Any, width: int, height: int, title: str, subtitle: str) -> None:
    title_font = _font(round(height * .045), bold=True)
    subtitle_font = _font(round(height * .021))
    available = round(width * .91)
    draw.text((round(width * .04), round(height * .045)),
              _fit_text(draw, title, title_font, available), fill="#17365D", font=title_font)
    draw.text((round(width * .04), round(height * .118)),
              _fit_text(draw, subtitle, subtitle_font, available), fill="#52606D", font=subtitle_font)


def _fit_text(draw: Any, text: str, font: Any, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_width:
        text = text[:-1]
    return text + "…"


def _font(size: int, bold: bool = False) -> Any:
    from PIL import ImageFont

    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in (("msyhbd.ttc", "simhei.ttf") if bold else ("msyh.ttc", "simhei.ttf")):
        path = font_dir / name
        if path.exists():
            return ImageFont.truetype(str(path), size=max(9, size))
    return ImageFont.load_default()


def _save(image: Any, path: str | Path, dpi: int) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", dpi=(dpi, dpi), optimize=True)
    return destination
