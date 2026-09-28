"""Plannings synthétiques pour la banque de tests (`test_planning_bank.py`).

Aucun vrai PDF dans le dépôt (CLAUDE.md) : chaque planning est décrit en
Python — personnes, dates, cases, style de tableau — puis dessiné avec
reportlab. Le même descriptif sert ensuite à vérifier la découpe : on sait
quels textes doivent tomber dans l'en-tête et dans la ligne de la personne,
et lesquels ne doivent surtout pas y être (ceux des autres).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

Rules = Literal["grid", "rows", "header", "none", "zebra", "cells"]
Orientation = Literal["rows", "columns"]

LANDSCAPE = landscape(A4)
MARGIN = 36.0
PAD_X = 6.0
PAD_Y = 5.0
GRID_GAP = 30.0
MIN_CELL_WIDTH = 18.0
FONT = "Helvetica"

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
LUNDI = date(2026, 9, 14)

TEAM = ("DUPONT Jean", "MARTIN Sophie", "BERNARD Paul", "DUBOIS Marie", "THOMAS Luc")


@dataclass(frozen=True)
class Grid:
    """Un tableau de planning.

    `orientation="rows"` : une ligne d'en-tête de dates, puis une ligne par
    personne. `"columns"` : l'inverse, une colonne par personne (noms dans la
    ligne d'en-tête) et une ligne par date. Dans un texte, « \\n » passe à la
    ligne dans la case ; dans un nom (lignes) ou une date (colonnes), « | » le
    répartit sur plusieurs colonnes (ex. « 1042|DUPONT|Jean »)."""

    dates: tuple[str, ...]
    people: tuple[tuple[str, tuple[str, ...]], ...]
    orientation: Orientation = "rows"
    rules: Rules = "grid"
    font_size: float = 9.0
    corner: str = "Nom"
    caption: str = ""
    footer: str = ""
    valign: Literal["top", "middle"] = "top"
    family_font: str = ""

    def has(self, name: str) -> bool:
        return any(person == name for person, _ in self.people)

    def cells_of(self, name: str) -> tuple[str, ...]:
        return next(cells for person, cells in self.people if person == name)

    def name_tokens(self, name: str) -> set[str]:
        return _tokens(name)

    def date_tokens(self) -> set[str]:
        return set().union(*(_tokens(label) for label in self.dates))

    def person_tokens(self, name: str) -> set[str]:
        return self.name_tokens(name).union(*(_tokens(c) for c in self.cells_of(name)))

    def other_tokens(self, name: str) -> set[str]:
        """Textes des autres personnes, qui ne doivent pas déborder dans la
        découpe de `name` (moins ceux qu'elle partage, dates et en-têtes)."""
        others: set[str] = set()
        for person, cells in self.people:
            if person != name:
                others |= _tokens(person).union(*(_tokens(c) for c in cells))
        return others - self.person_tokens(name) - self.date_tokens() - _tokens(self.corner)


def _tokens(text: str) -> set[str]:
    return set(text.replace("|", " ").split())


def week(label: Callable[[date], str], *, start: date = LUNDI, days: int = 7) -> tuple[str, ...]:
    return tuple(label(start + timedelta(days=i)) for i in range(days))


def jour(d: date, length: int = 3) -> str:
    return JOURS[d.weekday()][:length].capitalize()


def mois(d: date) -> str:
    return MOIS[d.month - 1]


def hours(person_index: int, days: int, *, fmt: str = "h", rest: Sequence[int] = ()) -> tuple:
    """Horaires distincts par personne (pour repérer une fuite d'une ligne à
    l'autre) : 7h-15h pour la première, 8h-16h pour la deuxième…"""
    start, end = 7 + person_index, 15 + person_index
    text = {
        "h": f"{start}h-{end}h",
        "colon": f"{start:02d}:00-{end:02d}:00",
        "slash": f"{start}h30/{end}h",
    }[fmt]
    return tuple("Repos" if day in rest else text for day in range(days))


def team(days: int, *, names: Sequence[str] = TEAM, fmt: str = "h") -> tuple:
    return tuple((name, hours(i, days, fmt=fmt, rest=(5, 6))) for i, name in enumerate(names))


def draw_planning(
    path: Path, pages: Sequence[Sequence[Grid]], *, page_size: tuple[float, float] = LANDSCAPE
) -> None:
    """Dessine chaque page (ses tableaux empilés de haut en bas) dans `path`."""
    c = canvas.Canvas(str(path), pagesize=page_size)
    for grids in pages:
        y = page_size[1] - MARGIN
        for grid in grids:
            y = _draw_grid(c, grid, y, page_size) - GRID_GAP
        c.showPage()
    c.save()


def _matrix(grid: Grid) -> list[list[str]]:
    """Le tableau en cases de texte, ligne d'en-tête comprise."""
    if grid.orientation == "rows":
        label_cols = max(len(name.split("|")) for name, _ in grid.people)
        corner = (grid.corner.split("|") + [""] * label_cols)[:label_cols]
        rows = [corner + list(grid.dates)]
        for name, cells in grid.people:
            labels = (name.split("|") + [""] * label_cols)[:label_cols]
            rows.append(labels + list(cells) + [""] * (len(grid.dates) - len(cells)))
        return rows
    label_cols = max(len(label.split("|")) for label in grid.dates)
    corner = (grid.corner.split("|") + [""] * label_cols)[:label_cols]
    rows = [corner + [name for name, _ in grid.people]]
    for index, label in enumerate(grid.dates):
        labels = (label.split("|") + [""] * label_cols)[:label_cols]
        rows.append(labels + [cells[index] for _, cells in grid.people])
    return rows


def _width(text: str, size: float, font: str = FONT) -> float:
    return max(stringWidth(line, font, size) for line in text.split("\n"))


def _draw_grid(c: canvas.Canvas, grid: Grid, top: float, page_size: tuple[float, float]) -> float:
    size = grid.font_size
    leading = size * 1.2
    matrix = _matrix(grid)
    n_cols = len(matrix[0])
    widths = [
        max(MIN_CELL_WIDTH, max(_width(row[col], size) for row in matrix) + 2 * PAD_X)
        for col in range(n_cols)
    ]
    heights = [max(cell.count("\n") + 1 for cell in row) * leading + 2 * PAD_Y for row in matrix]
    left = MARGIN
    right = left + sum(widths)
    if right > page_size[0] - MARGIN:
        raise ValueError(f"tableau trop large ({right:.0f} pt) : réduire font_size")

    if grid.caption:
        c.setFont(FONT, size + 1)
        c.drawString(left, top - size - 1, grid.caption)
        top -= size + 1 + 10

    bounds = [top]
    for height in heights:
        bounds.append(bounds[-1] - height)
    xs = [left]
    for width in widths:
        xs.append(xs[-1] + width)
    bottom = bounds[-1]
    if bottom < MARGIN:
        raise ValueError("tableau trop haut pour la page")

    if grid.rules == "zebra":
        c.setFillGray(0.88)
        for index in range(2, len(matrix), 2):
            c.rect(left, bounds[index + 1], right - left, heights[index], stroke=0, fill=1)
        c.setFillGray(0)

    for r, row in enumerate(matrix):
        for col, cell in enumerate(row):
            _draw_cell(
                c, grid, cell, xs[col], bounds[r], heights[r], is_name=_is_name(grid, r, col)
            )

    if grid.rules in ("grid", "rows"):
        for y in bounds:
            c.line(left, y, right, y)
    if grid.rules == "grid":
        for x in xs:
            c.line(x, top, x, bottom)
    if grid.rules == "header":
        for y in (bounds[0], bounds[1], bounds[-1]):
            c.line(left, y, right, y)
    if grid.rules == "cells":
        for r in range(len(matrix)):
            for col in range(n_cols):
                c.rect(xs[col], bounds[r + 1], widths[col], heights[r], stroke=1, fill=0)

    if grid.footer:
        c.setFont(FONT, size)
        c.drawString(left, bottom - size - 8, grid.footer)
        bottom -= size + 8
    return bottom


def _is_name(grid: Grid, r: int, col: int) -> bool:
    if grid.orientation == "rows":
        return r > 0 and col == 0
    return r == 0 and col > 0


def _draw_cell(c, grid: Grid, cell: str, x: float, row_top: float, height: float, *, is_name):
    size = grid.font_size
    leading = size * 1.2
    lines = cell.split("\n")
    first_baseline = row_top - PAD_Y - size * 0.8
    if grid.valign == "middle":
        block = len(lines) * leading
        first_baseline = row_top - (height - block) / 2 - size * 0.8
    for index, line in enumerate(lines):
        baseline = first_baseline - index * leading
        if is_name and grid.family_font:
            _draw_mixed_fonts(c, grid, line, x + PAD_X, baseline)
        else:
            c.setFont(FONT, size)
            c.drawString(x + PAD_X, baseline, line)


def _draw_mixed_fonts(c, grid: Grid, line: str, x: float, baseline: float) -> None:
    """Mots tout en capitales (nom de famille) dans `family_font`, le reste en
    police normale : un mot par appel, comme dans bien des exports. L'espace
    est dessiné avec le mot : sans lui, pdfplumber colle les deux mots
    (« DUPONTJean »), cas couvert à part dans test_pdf_locate.py."""
    size = grid.font_size
    for word in line.split(" "):
        font = grid.family_font if word.isupper() else FONT
        c.setFont(font, size)
        c.drawString(x, baseline, word + " ")
        x += stringWidth(word + " ", font, size)
