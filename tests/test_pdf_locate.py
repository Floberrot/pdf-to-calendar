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


def _build_pdf_with_empty_cell_after_name(path: Path) -> None:
    """Ligne « Jean DUPONT » suivie d'un « - » dans la première case (jour de
    repos), comme sur un vrai planning."""
    c = canvas.Canvas(str(path), pagesize=PAGE_SIZE)
    top = PAGE_SIZE[1] - 60
    for i, date_text in enumerate(DEFAULT_DATES):
        c.drawString(LEFT + 120 + i * DATE_GAP, top, date_text)
    y = top - 15
    c.line(LEFT, y, RIGHT, y)
    for name in ["MARTIN Sophie", "Jean DUPONT", "BERNARD Paul"]:
        y -= ROW_HEIGHT
        c.drawString(LEFT, y + 12, name)
        if name == "Jean DUPONT":
            c.drawString(LEFT + 120, y + 12, "-")
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


def test_locate_exposes_name_horizontal_bounds(tmp_path):
    """Position du nom dans la ligne (plan, section 10) : nécessaire pour le
    griser avant l'envoi au modèle (crop.py::redact_name)."""
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES)

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateResult)
    assert result.name_x0 < result.name_x1
    assert result.table_left <= result.name_x0
    assert result.name_x1 <= result.table_right


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


def test_locate_handles_full_day_month_year_dates(tmp_path):
    """Format réel observé : 'lundi 14 septembre 2026' (jour + numéro + mois
    complet + année, 4 mots) plutôt que l'abrégé 'Lun 15' du plan."""
    pdf_path = tmp_path / "planning.pdf"
    dates = [
        "lundi 14 septembre 2026",
        "mardi 15 septembre 2026",
        "mercredi 16 septembre 2026",
    ]
    c = canvas.Canvas(str(pdf_path), pagesize=PAGE_SIZE)
    top = PAGE_SIZE[1] - 60
    for i, date_text in enumerate(dates):
        c.drawString(LEFT + i * 220, top, date_text)
    y = top - 15
    c.line(LEFT, y, RIGHT, y)
    for name in NAMES:
        y -= ROW_HEIGHT
        c.drawString(LEFT, y + 12, name)
        c.line(LEFT, y, RIGHT, y)
    c.showPage()
    c.save()

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateResult)
    assert result.header.top < result.header.bottom


def test_locate_finds_two_word_name_despite_small_vertical_offset(tmp_path):
    """PDF réel (retour utilisateur) : prénom et nom de famille pas toujours
    à l'exacte même hauteur (ex. nom de famille dans une police différente
    pour le distinguer visuellement) — un léger décalage ne doit pas
    empêcher `_group_lines` de les traiter comme une seule ligne, sans quoi
    aucun candidat à deux mots ne peut plus jamais matcher. Les fixtures
    `NAMES` ci-dessus dessinent tout le nom en un seul `drawString`, donc un
    même « top » pour tous les mots : elles ne pouvaient pas révéler ce cas."""
    pdf_path = tmp_path / "planning.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=PAGE_SIZE)
    top = PAGE_SIZE[1] - 60
    for i, date_text in enumerate(DEFAULT_DATES):
        c.drawString(LEFT + 120 + i * DATE_GAP, top, date_text)
    y = top - 15
    c.line(LEFT, y, RIGHT, y)
    y -= ROW_HEIGHT
    c.drawString(LEFT, y + 12, "Jean")
    c.drawString(LEFT + 45, y + 12 - 3.5, "DUPONT")
    c.line(LEFT, y, RIGHT, y)
    c.showPage()
    c.save()

    candidates = build_candidates(pdf_name="Jean DUPONT", family_name="", given_name="")
    result = locate(str(pdf_path), candidates=candidates)

    assert isinstance(result, LocateResult)
    assert result.matched_text.upper() == "JEAN DUPONT"


def test_locate_ignores_punctuation_only_cell_after_name(tmp_path):
    """Retour utilisateur : « jean » seul marchait, « Jean DUPONT » jamais.
    Cause : le « - » de la case vide qui suit le nom entrait dans une fenêtre
    de trois mots, « Jean DUPONT - » se normalise comme « Jean DUPONT », et
    une seule personne passait pour un homonyme (`nom_homonyme`)."""
    pdf_path = tmp_path / "planning.pdf"
    _build_pdf_with_empty_cell_after_name(pdf_path)

    candidates = build_candidates(pdf_name="Jean DUPONT", family_name="", given_name="")
    result = locate(str(pdf_path), candidates=candidates)

    assert isinstance(result, LocateResult)
    assert result.matched_text == "Jean DUPONT"


def test_locate_family_name_next_to_empty_cell_is_not_a_false_homonym(tmp_path):
    """Même cause, un seul mot : « DUPONT » et « DUPONT - » comptaient pour
    deux lignes — c'est pourquoi le nom de famille seul échouait aussi, alors
    que le prénom (rien de vide à sa droite) passait."""
    pdf_path = tmp_path / "planning.pdf"
    _build_pdf_with_empty_cell_after_name(pdf_path)

    result = locate(str(pdf_path), candidates=["DUPONT"])

    assert isinstance(result, LocateResult)
    assert result.matched_text == "DUPONT"


def test_locate_few_dates_falls_back(tmp_path):
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES, dates=["Lun 15", "Mar 16"])

    result = locate(str(pdf_path), candidates=["BERNARD PAUL"])

    assert isinstance(result, LocateFailure)
    assert result.reason == "pas_de_dates"


def test_build_candidates_order_and_dedup():
    candidates = build_candidates(pdf_name=None, family_name="Dupont", given_name="Jean")
    assert candidates == ["Dupont Jean", "Jean Dupont", "Dupont J", "J Dupont", "Dupont"]


def test_build_candidates_pdf_name_ignores_google_names():
    candidates = build_candidates(pdf_name="Dupont Jean", family_name="Autre", given_name="Nom")
    assert candidates[0] == "Dupont Jean"


def test_build_candidates_pdf_name_tries_both_word_orders():
    """Retour utilisateur : "NOM Prénom" enregistré dans /account ne
    fonctionnait pas quand le planning écrit "Prénom NOM"."""
    candidates = build_candidates(pdf_name="Dupont Jean", family_name="", given_name="")
    assert candidates == ["Dupont Jean", "Jean Dupont"]


def test_build_candidates_pdf_name_single_word_not_reordered():
    candidates = build_candidates(pdf_name="Dupont", family_name="", given_name="")
    assert candidates == ["Dupont"]


def test_build_candidates_no_given_name():
    candidates = build_candidates(pdf_name=None, family_name="Dupont", given_name="")
    assert candidates == ["Dupont"]


def test_build_candidates_no_family_name_returns_empty():
    """Sans nom de famille, aucun candidat : chercher le seul prénom
    risquerait de matcher un homonyme de prénom (risque n°1 du plan)."""
    candidates = build_candidates(pdf_name=None, family_name="", given_name="Florian")
    assert candidates == []


def test_locate_succeeds_when_pdf_name_order_is_reversed_from_pdf(tmp_path):
    """Retour utilisateur : « Paul BERNARD » enregistré dans /account doit
    quand même matcher un planning qui écrit « BERNARD Paul »."""
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path, names=NAMES)

    candidates = build_candidates(pdf_name="Paul BERNARD", family_name="", given_name="")
    result = locate(str(pdf_path), candidates=candidates)

    assert isinstance(result, LocateResult)
    assert result.matched_text.upper() == "BERNARD PAUL"


def test_locate_without_family_name_never_matches_first_name_homonym(tmp_path):
    """Deux personnes prénommées Florian dans le PDF : sans nom de famille,
    ne doit jamais en choisir un au hasard (risque n°1 du plan)."""
    pdf_path = tmp_path / "planning.pdf"
    names = ["MARTIN Florian", "DUPONT Florian", "BERNARD Paul"]
    _build_planning_pdf(pdf_path, names=names)

    candidates = build_candidates(pdf_name=None, family_name="", given_name="Florian")
    result = locate(str(pdf_path), candidates=candidates)

    assert isinstance(result, LocateFailure)
    assert result.reason == "nom_introuvable"
