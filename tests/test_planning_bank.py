"""Banque de plannings synthétiques : un maximum de formats de tableau, de
dates et d'horaires, pour vérifier que `locate` découpe au bon endroit.

Demande utilisateur : « tester PLEIN de formats lignes/colonnes, types
d'horaires : noms en colonnes et horaires en lignes, plus d'une semaine,
d'autres formats de jour/heure… ». Chaque cas dessine un PDF (`plannings`),
cherche une personne, puis relit avec pdfplumber ce qui tombe réellement
dans chaque découpe :

- la ligne (ou la colonne) de la personne contient son nom et toutes ses
  cases, et rien qui n'appartienne qu'aux autres ;
- l'en-tête contient toutes les dates du tableau ;
- la zone du nom (grisée avant l'envoi au modèle) contient bien le nom ;
- un bloc par tableau où figure la personne (plusieurs semaines, pages).

Les horaires, eux, sont lus par le modèle, pas par ce code : ici on vérifie
seulement qu'ils sont bien dans l'image, quel que soit leur format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pdfplumber
import pytest

from app.pdf.locate import (
    Block,
    LocateFailure,
    LocateResult,
    Rect,
    build_candidates,
    locate,
    normalize,
)
from tests.plannings import (
    DAYS,
    LANDSCAPE,
    LUNDI,
    TEAM,
    Grid,
    draw_planning,
    hours,
    jour,
    mois,
    team,
    week,
)


@dataclass(frozen=True)
class Case:
    id: str
    pages: tuple[tuple[Grid, ...], ...]
    target: str
    typed: str | None = None
    google: tuple[str, str] = ("", "")
    expect: str = "ok"
    page_size: tuple[float, float] = LANDSCAPE


def _rows(dates, people=None, **kwargs) -> Grid:
    return Grid(dates=dates, people=people or team(len(dates)), **kwargs)


def _cols(dates, people=None, **kwargs) -> Grid:
    kwargs.setdefault("corner", "Date")
    return Grid(dates=dates, people=people or team(len(dates)), orientation="columns", **kwargs)


def _one(grid: Grid, target: str = "DUPONT Jean", **kwargs) -> dict:
    kwargs.setdefault("typed", target if "google" not in kwargs else None)
    return {"pages": ((grid,),), "target": target, **kwargs}


LUN_14 = week(lambda d: f"{jour(d)} {d.day}")
LUN_14_SLASH = week(lambda d: f"{jour(d)} {d.day:02d}/{d.month:02d}")


def _week_n(n: int, label=lambda d: f"{jour(d)} {d.day}"):
    return week(label, start=LUNDI + timedelta(weeks=n))


def _jour_long(d):
    return jour(d, 10).lower()


VARIED = (
    "9h-17h",
    "09:00-17:00",
    "8h30/16h",
    "7h-12h 13h-16h",
    "21h-7h",
    "RTT",
    "Repos",
)


def _varied_team() -> tuple:
    people = [("DUPONT Jean", VARIED)]
    for i, name in enumerate(TEAM[1:], start=1):
        people.append((name, hours(i, 7, fmt="colon", rest=(6,))))
    return tuple(people)


def _two_line_cells(i: int) -> tuple:
    return tuple(f"{7 + i}h-12h\n13h-{15 + i}h" if d < 5 else "Repos" for d in range(7))


CASES = [
    # --- Personnes en lignes, dates en colonnes : formats de date ---
    Case("semaine_classique", **_one(_rows(LUN_14))),
    Case(
        "nom_google_avec_initiale",
        **_one(
            _rows(LUN_14, people=team(7, names=("DUPONT J.", *TEAM[1:]))),
            target="DUPONT J.",
            google=("Jean", "DUPONT"),
        ),
    ),
    Case(
        "jours_complets_mois_annee",
        **_one(
            _rows(
                week(lambda d: f"{_jour_long(d)} {d.day} {mois(d)} {d.year}", days=5),
                people=team(5),
                font_size=7.5,
            )
        ),
    ),
    Case("dates_jj_mm", **_one(_rows(week(lambda d: f"{d.day:02d}/{d.month:02d}")))),
    Case("dates_jj_mm_aaaa", **_one(_rows(week(lambda d: d.strftime("%d/%m/%Y"))))),
    Case(
        "jour_et_date_complete",
        **_one(_rows(week(lambda d: f"{jour(d)} {d.strftime('%d/%m/%Y')}"), font_size=8)),
    ),
    Case("dates_avec_points", **_one(_rows(week(lambda d: d.strftime("%d.%m.%y"))))),
    Case("dates_iso", **_one(_rows(week(lambda d: d.isoformat())))),
    Case(
        "jour_colle_a_la_date",
        **_one(_rows(week(lambda d: f"{jour(d)}.{d.day:02d}/{d.month:02d}"))),
    ),
    Case("jour_colle_au_numero", **_one(_rows(week(lambda d: f"{jour(d)}{d.day}")))),
    Case("initiale_du_jour", **_one(_rows(week(lambda d: f"{jour(d, 1)} {d.day}")))),
    Case("deux_lettres", **_one(_rows(week(lambda d: f"{jour(d, 2)} {d.day}")))),
    Case("numeros_seuls", **_one(_rows(week(lambda d: str(d.day))))),
    Case("jour_puis_numero_sur_deux_lignes", **_one(_rows(week(lambda d: f"{jour(d)}\n{d.day}")))),
    Case("numero_puis_jour_sur_deux_lignes", **_one(_rows(week(lambda d: f"{d.day}\n{jour(d)}")))),
    Case("jours_en_anglais", **_one(_rows(week(lambda d: f"{DAYS[d.weekday()]} {d.day}")))),
    Case("mois_abrege", **_one(_rows(week(lambda d: f"{d.day} sept.")))),
    Case("semaine_ouvree", **_one(_rows(week(lambda d: f"{jour(d)} {d.day}", days=5)))),
    Case(
        "colonne_total",
        **_one(
            _rows(
                (*LUN_14, "Total"),
                people=tuple((n, (*c, f"{35 + i}h")) for i, (n, c) in enumerate(team(7))),
            )
        ),
    ),
    Case(
        "titre_et_legende",
        **_one(
            _rows(
                LUN_14_SLASH,
                caption="Planning équipe A — semaine du 14/09/2026 au 20/09/2026",
                footer="RTT : réduction du temps de travail — CP : congés payés",
            )
        ),
    ),
    Case(
        "ligne_de_nombres_au_dessus",
        **_one(
            _rows(
                LUN_14,
                people=(("BERNARD Paul", ("8", "8", "8", "8", "7", "0", "0")), *team(7)[:2]),
            )
        ),
    ),
    # --- Styles de tableau ---
    Case("traits_horizontaux_seulement", **_one(_rows(LUN_14, rules="rows"))),
    Case("trait_sous_en_tete_seulement", **_one(_rows(LUN_14, rules="header"))),
    Case("sans_aucun_trait", **_one(_rows(LUN_14, rules="none"))),
    Case("lignes_zebrees", **_one(_rows(LUN_14, rules="zebra"), target="BERNARD Paul")),
    Case("zebre_ligne_non_grisee", **_one(_rows(LUN_14, rules="zebra"), target="MARTIN Sophie")),
    Case("cases_dessinees_une_par_une", **_one(_rows(LUN_14, rules="cells"))),
    Case(
        "page_portrait",
        page_size=(LANDSCAPE[1], LANDSCAPE[0]),
        **_one(_rows(week(lambda d: f"{jour(d, 2)} {d.day}"), font_size=7)),
    ),
    # --- Horaires et cases ---
    Case("horaires_varies", **_one(_rows(LUN_14, people=_varied_team(), font_size=8))),
    Case(
        "cases_sur_deux_lignes",
        **_one(_rows(LUN_14, people=tuple((n, _two_line_cells(i)) for i, n in enumerate(TEAM)))),
    ),
    Case(
        "cases_sur_deux_lignes_sans_traits",
        **_one(
            _rows(
                LUN_14,
                rules="none",
                people=tuple((n, _two_line_cells(i)) for i, n in enumerate(TEAM)),
            )
        ),
    ),
    Case(
        "nom_centre_cases_sur_deux_lignes_sans_traits",
        **_one(
            _rows(
                LUN_14,
                rules="none",
                valign="middle",
                people=tuple((n, _two_line_cells(i)) for i, n in enumerate(TEAM)),
            ),
            target="MARTIN Sophie",
        ),
    ),
    Case(
        "codes_de_poste",
        **_one(
            _rows(
                LUN_14,
                people=tuple(
                    (n, tuple("MSNJRRM"[(d + i) % 7] for d in range(7))) for i, n in enumerate(TEAM)
                ),
            )
        ),
    ),
    Case(
        "case_vide_avec_tiret",
        **_one(_rows(LUN_14, people=tuple((n, ("-", *c[1:])) for n, c in team(7)))),
    ),
    # --- Noms ---
    Case(
        "nom_sur_deux_lignes",
        **_one(
            _rows(LUN_14, people=(("DUPONT\nJean", hours(0, 7)), *team(7)[1:])),
            target="DUPONT\nJean",
            typed="Jean DUPONT",
        ),
    ),
    Case(
        "nom_sur_deux_lignes_google",
        **_one(
            _rows(LUN_14, people=(("DUPONT\nJean", hours(0, 7)), *team(7)[1:])),
            target="DUPONT\nJean",
            google=("Jean", "DUPONT"),
        ),
    ),
    Case(
        "nom_et_prenom_dans_deux_colonnes",
        **_one(
            _rows(
                LUN_14,
                corner="Matricule|Nom|Prénom",
                people=tuple(
                    (f"{1040 + i}|{n.split()[0]}|{n.split()[1]}", hours(i, 7))
                    for i, n in enumerate(TEAM)
                ),
            ),
            target="1040|DUPONT|Jean",
            typed="Jean DUPONT",
        ),
    ),
    Case(
        "nom_compose_quatre_mots",
        **_one(
            _rows(LUN_14, people=(("DE LA FONTAINE Marie", hours(0, 7)), *team(7)[1:])),
            target="DE LA FONTAINE Marie",
            typed="Marie de la Fontaine",
        ),
    ),
    Case(
        "nom_avec_tiret_et_accents",
        **_one(
            _rows(LUN_14, people=(("MARTIN-DUBOIS Hélène", hours(0, 7)), *team(7)[1:])),
            target="MARTIN-DUBOIS Hélène",
            google=("Hélène", "Martin-Dubois"),
        ),
    ),
    Case("nom_de_famille_en_gras", **_one(_rows(LUN_14, family_font="Helvetica-Bold"))),
    Case(
        "prenoms_seuls",
        **_one(
            _rows(LUN_14, people=team(7, names=("Jean", "Sophie", "Paul", "Marie", "Luc"))),
            target="Jean",
        ),
    ),
    Case(
        "homonymes_dans_le_meme_tableau",
        expect="nom_homonyme",
        **_one(
            _rows(LUN_14, people=team(7, names=("DUPONT Jean", "MARTIN Sophie", "DUPONT Jean")))
        ),
    ),
    Case("nom_absent", expect="nom_introuvable", **_one(_rows(LUN_14), typed="Zoé INCONNUE")),
    Case(
        "pas_de_dates",
        expect="pas_de_dates",
        **_one(_rows(("Poste A", "Poste B", "Poste C", "Poste D", "Poste E"), people=team(5))),
    ),
    # --- Plusieurs semaines ---
    Case(
        "deux_semaines_en_une_ligne",
        **_one(
            _rows(week(lambda d: f"{jour(d, 2)} {d.day}", days=14), people=team(14), font_size=7)
        ),
    ),
    Case(
        "mois_complet_en_une_ligne",
        **_one(
            _rows(
                week(lambda d: str(d.day), start=LUNDI.replace(day=1), days=30),
                people=tuple(
                    (n, tuple("MSRN"[(d + i) % 4] for d in range(30))) for i, n in enumerate(TEAM)
                ),
                font_size=6,
                corner="Sept.",
            )
        ),
    ),
    Case(
        "deux_semaines_empilees",
        pages=((_rows(_week_n(0)), _rows(_week_n(1))),),
        target="DUPONT Jean",
        typed="DUPONT Jean",
    ),
    Case(
        "trois_semaines_empilees_sans_traits",
        pages=(
            (
                _rows(_week_n(0), rules="none", font_size=8),
                _rows(_week_n(1), rules="none", font_size=8),
                _rows(_week_n(2), rules="none", font_size=8),
            ),
        ),
        target="DUPONT Jean",
        google=("Jean", "DUPONT"),
    ),
    Case(
        "une_semaine_par_page",
        pages=((_rows(_week_n(0)),), (_rows(_week_n(1)),)),
        target="DUPONT Jean",
        typed="Jean DUPONT",
    ),
    Case(
        "absent_la_deuxieme_semaine",
        pages=((_rows(_week_n(0)), _rows(_week_n(1), people=team(7)[1:])),),
        target="DUPONT Jean",
        typed="DUPONT Jean",
    ),
    Case(
        "meme_semaine_deux_equipes",
        pages=((_rows(LUN_14, caption="Équipe A"), _rows(LUN_14, caption="Équipe B")),),
        target="DUPONT Jean",
        typed="DUPONT Jean",
        expect="nom_homonyme",
    ),
    # --- Personnes en colonnes, dates en lignes ---
    Case("noms_en_colonnes", **_one(_cols(LUN_14_SLASH))),
    Case("noms_en_colonnes_derniere_colonne", **_one(_cols(LUN_14_SLASH), target="THOMAS Luc")),
    Case("noms_en_colonnes_google", **_one(_cols(LUN_14_SLASH), google=("Jean", "DUPONT"))),
    Case(
        "noms_en_colonnes_deux_semaines",
        **_one(
            _cols(week(lambda d: f"{jour(d)} {d.day:02d}/{d.month:02d}", days=14), people=team(14))
        ),
    ),
    Case("noms_en_colonnes_sans_traits", **_one(_cols(LUN_14_SLASH, rules="none"))),
    Case("noms_en_colonnes_traits_horizontaux", **_one(_cols(LUN_14_SLASH, rules="rows"))),
    Case(
        "noms_en_colonnes_nom_sur_deux_lignes",
        **_one(
            _cols(
                LUN_14_SLASH,
                people=tuple((n.replace(" ", "\n"), hours(i, 7)) for i, n in enumerate(TEAM)),
            ),
            target="DUPONT\nJean",
            typed="Jean DUPONT",
        ),
    ),
    Case(
        "noms_en_colonnes_jour_et_date_separes",
        **_one(_cols(week(lambda d: f"{jour(d)}|{d.day:02d}/{d.month:02d}"), corner="Jour|Date")),
    ),
    Case(
        "noms_en_colonnes_horaires_sur_deux_lignes",
        **_one(
            _cols(LUN_14_SLASH, people=tuple((n, _two_line_cells(i)) for i, n in enumerate(TEAM)))
        ),
    ),
    Case(
        "noms_en_colonnes_deux_blocs",
        pages=(
            (
                _cols(
                    week(lambda d: f"{jour(d)} {d.day:02d}/{d.month:02d}", days=5), people=team(5)
                ),
                _cols(
                    week(
                        lambda d: f"{jour(d)} {d.day:02d}/{d.month:02d}",
                        start=LUNDI + timedelta(weeks=1),
                        days=5,
                    ),
                    people=team(5),
                ),
            ),
        ),
        target="DUPONT Jean",
        typed="DUPONT Jean",
    ),
]


def _compact(text: str) -> str:
    return normalize(text).replace(" ", "")


def _words(page, rect: Rect, *, touching: bool) -> set[str]:
    bbox = (
        max(0.0, rect.x0),
        max(0.0, rect.top),
        min(float(page.width), rect.x1),
        min(float(page.height), rect.bottom),
    )
    region = page.crop(bbox) if touching else page.within_bbox(bbox)
    return {w["text"] for w in region.extract_words()}


def _candidates(case: Case) -> tuple[list[str], tuple[str, ...]]:
    if case.typed is not None:
        pdf_name = case.typed
        return build_candidates(pdf_name=pdf_name, family_name="", given_name=""), tuple(
            pdf_name.split()
        )
    given, family = case.google
    return build_candidates(pdf_name=None, family_name=family, given_name=given), ()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_planning_bank(case: Case, tmp_path):
    pdf_path = tmp_path / f"{case.id}.pdf"
    draw_planning(pdf_path, case.pages, page_size=case.page_size)
    candidates, fallback = _candidates(case)

    result = locate(str(pdf_path), candidates, fallback_words=fallback)

    if case.expect != "ok":
        assert isinstance(result, LocateFailure), result
        assert result.reason == case.expect
        return
    assert isinstance(result, LocateResult), result
    expected = [
        (page_index, grid)
        for page_index, grids in enumerate(case.pages)
        for grid in grids
        if grid.has(case.target)
    ]
    assert len(result.blocks) == len(expected), result.blocks
    with pdfplumber.open(pdf_path) as pdf:
        for block, (page_index, grid) in zip(result.blocks, expected, strict=True):
            _check_block(
                pdf.pages[page_index], block, page_index, grid, case.target, result.matched_text
            )


def _check_block(
    page, block: Block, page_index: int, grid: Grid, target: str, matched_text: str
) -> None:
    assert block.page_index == page_index
    assert block.orientation == grid.orientation
    line = _words(page, block.line, touching=False)
    missing = grid.person_tokens(target) - line
    assert not missing, f"absent de la ligne découpée : {sorted(missing)}"
    leaked = grid.other_tokens(target) & _words(page, block.line, touching=True)
    assert not leaked, f"d'autres personnes dans la ligne découpée : {sorted(leaked)}"
    header = _words(page, block.header, touching=False)
    missing_dates = grid.date_tokens() - header
    assert not missing_dates, f"dates absentes de l'en-tête : {sorted(missing_dates)}"
    name_zone = Rect(
        block.name.x0 - 1, block.name.top - 1, block.name.x1 + 1, block.name.bottom + 1
    )
    assert _compact(matched_text) in _compact(target), f"nom trouvé ailleurs : {matched_text}"
    assert set(matched_text.split()) <= _words(page, name_zone, touching=False)
