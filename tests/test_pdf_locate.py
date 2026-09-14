"""Tests de locate.py sur des PDF synthétiques générés par reportlab (plan, section 13).

Aucun vrai PDF ici (interdits, CLAUDE.md) : tout est généré par le test.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.pdf.locate import LocateFailure, LocateResult, build_candidates, locate

PAGE_SIZE = landscape(A4)
LEFT = 50
RIGHT = PAGE_SIZE[0] - 50
ROW_HEIGHT = 30
DATE_GAP = 90
DEFAULT_DATES = ["Lun 15", "Mar 16", "Mer 17", "Jeu 18", "Ven 19"]
NAMES = ["DUPONT Jean", "MARTIN Sophie", "BERNARD Paul", "DUBOIS Marie", "THOMAS Luc"]


def _build_planning_pdf(
    path: Path,
    *,
    names: list[str],
    dates: list[str] = DEFAULT_DATES,
    draw_lines: bool = True,
    tall_row_index: int | None = None,
) -> None:
    c = canvas.Canvas(str(path), pagesize=PAGE_SIZE)
    top = PAGE_SIZE[1] - 60

    for i, date_text in enumerate(dates):
        c.drawString(LEFT + 120 + i * DATE_GAP, top, date_text)

    y = top - 15
    if draw_lines:
        c.line(LEFT, y, RIGHT, y)

    for index, name in enumerate(names):
        height = ROW_HEIGHT * 2 if index == tall_row_index else ROW_HEIGHT
        y -= height
        c.drawString(LEFT, y + height - 18, name)
        if draw_lines:
            c.line(LEFT, y, RIGHT, y)

    c.showPage()
    c.save()


def test_locate_finds_name_line_and_header(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES)

    result = locate(str(pdf_path), candidates=["BERNARD PAUL", "BERNARD P", "BERNARD"])

    assert isinstance(result, LocateResult)
    assert result.matched_text.upper() == "BERNARD PAUL"
    assert result.line.top < result.line.bottom
    assert result.header.top < result.header.bottom
    assert result.header.bottom <= result.line.top
    assert result.table_left < result.table_right


def test_locate_person_spanning_two_row_heights(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES, tall_row_index=2)

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateResult)
    assert (result.line.bottom - result.line.top) > ROW_HEIGHT * 1.5


def test_locate_two_homonyms_returns_ambiguous(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    names = ["DUPONT Jean", "MARTIN Sophie", "DUPONT Jean", "DUBOIS Marie"]
    _build_planning_pdf(pdf_path, names=names)

    result = locate(str(pdf_path), candidates=["DUPONT JEAN"])

    assert isinstance(result, LocateFailure)
    assert result.reason == "nom_homonyme"
    assert len(result.matches) == 2


def test_locate_table_without_lines_falls_back(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES, draw_lines=False)

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateFailure)
    assert result.reason == "pas_de_traits"


def test_locate_name_not_found(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES)

    result = locate(str(pdf_path), candidates=["INCONNU PERSONNE"])

    assert isinstance(result, LocateFailure)
    assert result.reason == "nom_introuvable"


def test_locate_scanned_pdf_has_no_text(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=PAGE_SIZE)
    c.line(50, 50, 200, 200)  # dessin sans texte : simule un scan
    c.showPage()
    c.save()

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateFailure)
    assert result.reason == "pdf_scanne"


def test_locate_few_dates_falls_back(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES, dates=["Lun 15", "Mar 16"])

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateFailure)
    assert result.reason == "pas_de_dates"


def test_build_candidates_order_and_dedup():
    candidates = build_candidates(pdf_name=None, family_name="Dupont", given_name="Jean")
    assert candidates == ["Dupont Jean", "Jean Dupont", "Dupont J", "J Dupont", "Dupont"]


def test_build_candidates_uses_pdf_name_only():
    candidates = build_candidates(pdf_name="D. Jean", family_name="Dupont", given_name="Jean")
    assert candidates == ["D. Jean"]


def test_build_candidates_no_given_name():
    candidates = build_candidates(pdf_name=None, family_name="Dupont", given_name="")
    assert candidates == ["Dupont"]
