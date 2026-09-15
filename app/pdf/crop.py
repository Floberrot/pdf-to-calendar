"""Découpe et empilement en-tête + ligne (plan, section 5B, fin ; et 5B bis).

`compose_crop` : voie automatique, deux bandes (en-tête, ligne) empilées.
`crop_manual` : voie de secours, un seul rectangle tracé à la main.
`redact_name` : copie grisée de `compose_crop`, pour l'envoi au modèle
(jamais montrée) ; pas d'équivalent pour `crop_manual`, dont le rectangle
tracé à la main n'a pas de position de nom connue.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.pdf.locate import LocateResult
from app.pdf.render import SCALE

NAME_REDACT_MARGIN_PX = 6


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


def redact_name(composed: Image.Image, result: LocateResult) -> Image.Image:
    """Grise la zone du nom sur une copie de l'image composée : c'est cette
    copie qui part vers le modèle, jamais celle montrée en prévisualisation
    (compose_crop). Le modèle n'a de toute façon jamais besoin du nom, son
    prompt ne lui en demande pas (llm.py)."""
    redacted = composed.copy()
    left_px = round(result.table_left * SCALE)
    header_height = round(result.header.bottom * SCALE) - round(result.header.top * SCALE)
    x0 = max(0, round(result.name_x0 * SCALE) - left_px - NAME_REDACT_MARGIN_PX)
    x1 = min(redacted.width, round(result.name_x1 * SCALE) - left_px + NAME_REDACT_MARGIN_PX)
    ImageDraw.Draw(redacted).rectangle([x0, header_height, x1, redacted.height], fill="black")
    return redacted


def crop_manual(page_image_path: Path, *, x: float, y: float, w: float, h: float) -> Image.Image:
    """Découpe le rectangle tracé à la main (coordonnées en pixels de l'image rendue)."""
    with Image.open(page_image_path) as page_image:
        page_image.load()
        return page_image.crop((round(x), round(y), round(x + w), round(y + h)))
