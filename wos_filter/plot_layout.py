"""Persistent draggable text and legend placement for Matplotlib figures."""

from __future__ import annotations

from matplotlib.text import Text
from matplotlib.transforms import ScaledTranslation


def layout_artists(figure):
    """Stable keys for visible labels, titles, annotations and legends."""
    texts = [(f"text:{index}:{artist.get_text()}", artist)
             for index, artist in enumerate(figure.findobj(match=Text))
             if artist.get_visible() and artist.get_text()]
    legends = [(f"legend:{index}", ax.get_legend()) for index, ax in enumerate(figure.axes)
               if ax.get_legend() is not None]
    return texts, legends


def apply_layout(figure, text_offsets: dict[str, list[float]],
                 legend_positions: dict[str, list[float]]) -> dict[str, object]:
    """Apply normalized figure offsets to a fresh preview or export figure."""
    originals = {}
    texts, legends = layout_artists(figure)
    for key, artist in texts:
        originals[key] = artist.get_transform()
        if key in text_offsets:
            dx, dy = text_offsets[key]
            artist.set_transform(originals[key] + ScaledTranslation(
                dx * figure.get_figwidth(), dy * figure.get_figheight(), figure.dpi_scale_trans))
    for key, legend in legends:
        if key in legend_positions:
            legend.set_loc("lower left")
            legend.set_bbox_to_anchor(tuple(legend_positions[key]), transform=figure.transFigure)
    return originals
