"""Découpe et assemblage en-tête + ligne, et recadrage manuel de secours.

`compose_crop` : voie automatique, un couple (en-tête, ligne) par tableau
où figure la personne, les couples empilés de haut en bas. Tableau « une
ligne par personne » : l'en-tête au-dessus de la ligne. « Une colonne par
personne » : la colonne des dates à gauche de celle de la personne.
`crop_manual` : voie de secours, un seul rectangle tracé à la main.
`redact_name` : copie grisée de `compose_crop`, pour l'envoi au modèle
(jamais montrée) ; pas d'équivalent pour `crop_manual`, dont le rectangle
tracé à la main n'a pas de position de nom connue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from app.pdf.locate import Block, LocateResult, Rect
from app.pdf.render import SCALE

NAME_REDACT_MARGIN_PX = 6
# Blanc entre deux tableaux (deux semaines) dans l'image assemblée.
BLOCK_GAP_PX = 24

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class _Piece:
    page_index: int
    source: Box
    x: int
    y: int

    @property
    def width(self) -> int:
        return self.source[2] - self.source[0]

    @property
    def height(self) -> int:
        return self.source[3] - self.source[1]


def _pixels(rect: Rect) -> Box:
    return (
        round(rect.x0 * SCALE),
        round(rect.top * SCALE),
        round(rect.x1 * SCALE),
        round(rect.bottom * SCALE),
    )


def _layout(result: LocateResult) -> tuple[list[tuple[Block, _Piece, _Piece]], int, int]:
    """Position de chaque morceau dans l'image assemblée, et sa taille."""
    placed = []
    width = height = 0
    for index, block in enumerate(result.blocks):
        if index:
            height += BLOCK_GAP_PX
        header_box, line_box = _pixels(block.header), _pixels(block.line)
        header = _Piece(block.page_index, header_box, 0, height)
        if block.orientation == "rows":
            line = _Piece(block.page_index, line_box, 0, height + header.height)
            width = max(width, header.width, line.width)
            height = line.y + line.height
        else:
            line = _Piece(block.page_index, line_box, header.width, height)
            width = max(width, header.width + line.width)
            height += max(header.height, line.height)
        placed.append((block, header, line))
    return placed, width, height


def compose_crop(page_images: Sequence[Path], result: LocateResult) -> Image.Image:
    """Découpe en-têtes et lignes dans les pages rendues (une image par page,
    dans l'ordre), et les assemble en une seule image."""
    placed, width, height = _layout(result)
    composed = Image.new("RGB", (max(width, 1), max(height, 1)), "white")
    pages: dict[int, Image.Image] = {}
    try:
        for _, header, line in placed:
            for piece in (header, line):
                if piece.page_index not in pages:
                    with Image.open(page_images[piece.page_index]) as image:
                        image.load()
                        pages[piece.page_index] = image.copy()
                composed.paste(pages[piece.page_index].crop(piece.source), (piece.x, piece.y))
    finally:
        for image in pages.values():
            image.close()
    return composed


def redact_name(composed: Image.Image, result: LocateResult) -> Image.Image:
    """Grise la zone du nom sur une copie de l'image composée : c'est cette
    copie qui part vers le modèle, jamais celle montrée en prévisualisation
    (compose_crop). Le modèle n'a de toute façon jamais besoin du nom, son
    prompt ne lui en demande pas (llm.py).

    Ligne d'une personne : toute la hauteur de la ligne, sur la largeur du
    nom. Colonne : toute la largeur de la colonne, sur la hauteur du nom."""
    redacted = composed.copy()
    draw = ImageDraw.Draw(redacted)
    placed, _, _ = _layout(result)
    margin = NAME_REDACT_MARGIN_PX
    for block, _, line in placed:
        name = _pixels(block.name)
        dx, dy = line.x - line.source[0], line.y - line.source[1]
        if block.orientation == "rows":
            box = [
                max(line.x, name[0] + dx - margin),
                line.y,
                min(line.x + line.width, name[2] + dx + margin),
                line.y + line.height,
            ]
        else:
            box = [
                line.x,
                max(line.y, name[1] + dy - margin),
                line.x + line.width,
                min(line.y + line.height, name[3] + dy + margin),
            ]
        draw.rectangle(box, fill="black")
    return redacted


def crop_manual(page_image_path: Path, *, x: float, y: float, w: float, h: float) -> Image.Image:
    """Découpe le rectangle tracé à la main (coordonnées en pixels de l'image rendue)."""
    with Image.open(page_image_path) as page_image:
        page_image.load()
        return page_image.crop((round(x), round(y), round(x + w), round(y + h)))
