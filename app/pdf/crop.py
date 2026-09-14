"""Découpe et empilement en-tête + ligne (plan, section 5B, fin ; et 5B bis).

`compose_crop` : voie automatique, deux bandes (en-tête, ligne) empilées.
`crop_manual` : voie de secours, un seul rectangle tracé à la main.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.pdf.locate import LocateResult
from app.pdf.render import SCALE


def compose_crop(page_image_path: Path, result: LocateResult) -> Image.Image:
    """Découpe l'en-tête et la ligne dans la page rendue, les empile en une image."""
    with Image.open(page_image_path) as page_image:
        page_image.load()
        left = round(result.table_left * SCALE)
        right = round(result.table_right * SCALE)
        header_crop = page_image.crop(
            (
                left,
                round(result.header.top * SCALE),
                right,
                round(result.header.bottom * SCALE),
            )
        )
        line_crop = page_image.crop(
            (
                left,
                round(result.line.top * SCALE),
                right,
                round(result.line.bottom * SCALE),
            )
        )

    width = max(header_crop.width, line_crop.width)
    height = header_crop.height + line_crop.height
    composed = Image.new("RGB", (width, height), "white")
    composed.paste(header_crop, (0, 0))
    composed.paste(line_crop, (0, header_crop.height))
    return composed


def crop_manual(page_image_path: Path, *, x: float, y: float, w: float, h: float) -> Image.Image:
    """Découpe le rectangle tracé à la main (coordonnées en pixels de l'image rendue)."""
    with Image.open(page_image_path) as page_image:
        page_image.load()
        return page_image.crop((round(x), round(y), round(x + w), round(y + h)))
