"""Localisation de la personne dans un PDF de planning.

Recherches par contenu, sans hypothèse figée sur la mise en page :

1. le nom → une position par tableau où il figure (plusieurs semaines
   empilées, une semaine par page…) ;
2. les dates → une suite de numéros de jour qui se suivent (14, 15, 16…),
   alignés soit sur une ligne au-dessus du nom (une ligne par personne),
   soit dans une colonne à sa gauche, sous lui (une colonne par personne) ;
3. les bornes de la ligne (ou de la colonne) de la personne → les traits du
   tableau quand il y en a entre ses textes et ceux des voisins, sinon le
   milieu du blanc qui les sépare.

Fonction pure, testable sur un PDF en local. La banque de plannings
synthétiques (`tests/test_planning_bank.py`) couvre les formats pris en
charge : l'enrichir avant de toucher aux seuils ci-dessous.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, Protocol

import pdfplumber

# 3.0 était trop strict : un nom et un prénom dans des polices différentes
# (courant pour distinguer visuellement le nom de famille) peuvent avoir un
# `top` pdfplumber différent de quelques points sans être sur une autre
# ligne, ce qui empêchait tout candidat à plusieurs mots de jamais matcher.
LineTolerance = 5.0
DateAlignTolerance = 5.0
MinAlignedDates = 3
# Écart maximal entre deux dates voisines d'un en-tête : 3 jours, pour un
# planning de jours ouvrés (vendredi → lundi).
MaxDayStep = 3
# Au-delà de ce multiple de l'écart habituel entre deux dates, ce sont deux
# tableaux distincts (deux semaines empilées), pas un seul.
MaxRunGapRatio = 3.0
MaxNameWords = 6
# Nom renvoyé à la ligne dans sa case (« DUPONT » puis « Jean » dessous) :
# blanc maximal entre les deux lignes, en hauteur de texte.
StackedNameGapRatio = 0.8
# Blanc maximal entre deux lignes d'une même case (horaires sur deux lignes),
# en hauteur de texte ; au-delà, c'est la ligne suivante du tableau.
CellLineGapRatio = 0.5
# Blanc maximal entre deux mots d'une même case, en hauteur de texte : une
# espace en fait de 0,25 à 0,35 selon la police ; entre deux cases, il y a au
# moins leurs deux marges intérieures.
CellWordGapRatio = 0.4
# Part minimale de la largeur (ou hauteur) du tableau qu'un trait doit couvrir
# pour séparer deux lignes (ou colonnes).
RuleCoverage = 0.6
Pad = 3.0
MaxPad = 10.0

FailureReason = Literal["pdf_scanne", "nom_introuvable", "nom_homonyme", "pas_de_dates"]
Orientation = Literal["rows", "columns"]


class _Box(Protocol):
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class Rect:
    """Rectangle en points PDF, origine en haut à gauche (comme pdfplumber)."""

    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True)
class Block:
    """Un tableau où figure la personne.

    `orientation="rows"` (une ligne par personne) : `header` est la bande des
    dates au-dessus, `line` la ligne de la personne, sur la même largeur.
    `"columns"` (une colonne par personne) : `header` est la colonne des dates
    à gauche, `line` la colonne de la personne, sur la même hauteur. `name` :
    la case du nom dans `line`, en entier même si seule une partie du nom a
    été tapée ; grisée avant l'envoi au modèle (crop.py)."""

    page_index: int
    orientation: Orientation
    header: Rect
    line: Rect
    name: Rect


@dataclass(frozen=True)
class LocateResult:
    """Un bloc par tableau où figure la personne, dans l'ordre de lecture
    (pages, puis de haut en bas)."""

    blocks: tuple[Block, ...]
    matched_text: str
    candidate_used: str
    # Texte de la case du nom (« BERROT Florian » quand seul « Florian » a été
    # tapé) : ce qui est montré à la personne pour vérifier la ligne.
    name_text: str = ""


@dataclass(frozen=True)
class LocateFailure:
    reason: FailureReason
    matches: tuple[str, ...] = ()


def normalize(text: str) -> str:
    """Majuscules, sans accents ni ponctuation, espaces normalisés."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.upper()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def build_candidates(*, pdf_name: str | None, family_name: str, given_name: str) -> list[str]:
    """Candidats de nom, dans l'ordre.

    La casse, les accents et la ponctuation n'ont pas besoin de correspondre
    (`normalize`, appliqué aux deux côtés par `_search_name`). Seul l'ordre
    des mots compte : pour `pdf_name`, on essaie aussi le dernier mot en tête
    et le premier mot en fin (retour utilisateur : « NOM Prénom » enregistré
    ne fonctionnait pas quand le planning écrit « Prénom NOM » ; même chose
    pour « Marie de la Fontaine » face à « DE LA FONTAINE Marie »)."""
    if pdf_name:
        words = pdf_name.split()
        if len(words) < 2:
            return [pdf_name]
        rotations = [
            pdf_name,
            " ".join([words[-1], *words[:-1]]),
            " ".join([*words[1:], words[0]]),
        ]
        return list(dict.fromkeys(rotations))

    family = family_name.strip()
    if not family:
        # Les 5 gabarits ci-dessous contiennent tous le nom de famille : sans
        # lui, pas de candidat plutôt qu'une recherche sur le seul prénom, qui
        # risquerait de matcher quelqu'un d'autre (risque n°1).
        return []

    given = given_name.strip()
    initial = given[:1] if given else ""

    raw = [
        f"{family} {given}".strip(),
        f"{given} {family}".strip(),
        f"{family} {initial}".strip(),
        f"{initial} {family}".strip(),
        family,
    ]
    return [candidate for candidate in dict.fromkeys(raw) if candidate]


# --- Mots, lignes et traits d'une page ---


@dataclass(frozen=True)
class _Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class _Line:
    words: tuple[_Word, ...]
    top: float
    bottom: float


def _bbox(items: Iterable[_Box]) -> Rect:
    items = list(items)
    return Rect(
        min(i.x0 for i in items),
        min(i.top for i in items),
        max(i.x1 for i in items),
        max(i.bottom for i in items),
    )


def _x_overlap(a: _Box, x0: float, x1: float) -> float:
    return min(a.x1, x1) - max(a.x0, x0)


def _y_overlap(a: _Box, top: float, bottom: float) -> float:
    return min(a.bottom, bottom) - max(a.top, top)


def _inside(a: _Box, b: _Box, tolerance: float = 0.5) -> bool:
    return (
        a.x0 >= b.x0 - tolerance
        and a.x1 <= b.x1 + tolerance
        and a.top >= b.top - tolerance
        and a.bottom <= b.bottom + tolerance
    )


def _group_lines(words: Sequence[_Word], tolerance: float = LineTolerance) -> list[_Line]:
    """Groupe des mots par ligne visuelle (proximité verticale)."""
    groups: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if groups and abs(word.top - groups[-1][0].top) <= tolerance:
            groups[-1].append(word)
        else:
            groups.append([word])
    return [
        _Line(
            words=tuple(sorted(group, key=lambda w: w.x0)),
            top=min(w.top for w in group),
            bottom=max(w.bottom for w in group),
        )
        for group in groups
    ]


def _merge_rules(edges: Iterable[dict], *, horizontal: bool) -> list[Rect]:
    """Fusionne les traits alignés qui se touchent : un tableau dessiné case
    par case donne un trait par case, pas un par ligne du tableau."""
    if horizontal:
        segments = sorted((e["top"], e["x0"], e["x1"]) for e in edges)
    else:
        segments = sorted((e["x0"], e["top"], e["bottom"]) for e in edges)
    merged: list[list[float]] = []
    for position, start, end in segments:
        for rule in merged:
            if abs(rule[0] - position) <= 0.5 and start <= rule[2] + 1 and end >= rule[1] - 1:
                rule[1], rule[2] = min(rule[1], start), max(rule[2], end)
                break
        else:
            merged.append([position, start, end])
    if horizontal:
        return [Rect(start, pos, end, pos) for pos, start, end in merged]
    return [Rect(pos, start, pos, end) for pos, start, end in merged]


# --- Dates ---

_DAY_WORDS = frozenset(
    {
        # français : complet, abrégé, deux lettres, initiale
        *("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
        *("lun", "mar", "mer", "jeu", "ven", "sam", "dim"),
        *("lu", "ma", "me", "je", "ve", "sa", "di"),
        *("l", "m", "j", "v", "s", "d"),
        # anglais
        *("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
        *("mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"),
        *("mo", "tu", "we", "th", "fr", "su"),
    }
)
_MONTH_WORDS = frozenset(
    {
        *("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout"),
        *("septembre", "octobre", "novembre", "decembre"),
        *("janv", "jan", "fev", "fevr", "avr", "juil", "sep", "sept", "oct", "nov", "dec"),
        *("january", "february", "march", "april", "may", "june", "july", "august"),
        *("september", "october", "november", "december", "feb", "apr", "jun", "jul", "aug"),
    }
)
# Jour collé à la date : « Lun14 », « Lun.14/09 » (au moins deux lettres, pour
# ne pas prendre « S1 », « S2 »… — des semaines — pour des dates).
_GLUED = re.compile(r"([a-z]{2,})\.?(\d.*)")
_DAY_NUMBER = re.compile(r"(\d{1,2})(?:er|e|st|nd|rd|th)?")
_DAY_MONTH = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](?:\d{2}|\d{4}))?")
_ISO = re.compile(r"\d{4}[/.\-](\d{1,2})[/.\-](\d{1,2})")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip(".,;:()[]")


def _day_number(text: str) -> int | None:
    """Numéro du jour (1-31) si le mot est une date : « 14 », « 1er »,
    « 14/09 », « 14/09/2026 », « 14.09.26 », « 2026-09-14 », « Lun14 »,
    « Lun.14/09 »… Sinon None — en particulier pour un horaire (« 9h-17h »,
    « 09:00 », « 8h30/16h »)."""
    folded = _fold(text)
    glued = _GLUED.fullmatch(folded)
    if glued:
        if glued.group(1) not in _DAY_WORDS:
            return None
        folded = glued.group(2)
    if match := _ISO.fullmatch(folded):
        day, month = int(match.group(2)), int(match.group(1))
    elif match := _DAY_MONTH.fullmatch(folded):
        day, month = int(match.group(1)), int(match.group(2))
    elif match := _DAY_NUMBER.fullmatch(folded):
        day, month = int(match.group(1)), 1
    else:
        return None
    return day if 1 <= day <= 31 and 1 <= month <= 12 else None


def _is_dateish(text: str) -> bool:
    """Mot qui peut faire partie d'un libellé de date : jour, mois, année,
    numéro de jour."""
    folded = _fold(text)
    return bool(
        folded in _DAY_WORDS
        or folded in _MONTH_WORDS
        or re.fullmatch(r"\d{4}", folded)
        or _day_number(text) is not None
    )


def _valid_step(previous: int, following: int) -> bool:
    """Deux dates voisines d'un en-tête : le jour suivant (ou jusqu'à
    `MaxDayStep` jours plus tard), y compris d'un mois sur l'autre (30 → 1).
    Écarte une ligne de nombres qui ne sont pas des dates (« 8 8 8 7 »)."""
    if 1 <= following - previous <= MaxDayStep:
        return True
    return previous >= 28 and following <= MaxDayStep


@dataclass(frozen=True)
class _Anchor:
    word: _Word
    day: int


@dataclass(frozen=True)
class _Run:
    """Suite de dates qui se suivent : l'en-tête d'un tableau."""

    orientation: Orientation
    anchors: tuple[_Anchor, ...]

    @property
    def rect(self) -> Rect:
        return _bbox(a.word for a in self.anchors)

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(normalize(a.word.text) for a in self.anchors)


def _split_runs(
    anchors: list[_Anchor],
    orientation: Orientation,
    position: Callable[[_Anchor], float],
    interrupted: Callable[[_Anchor, _Anchor], bool] = lambda a, b: False,
) -> list[_Run]:
    if len(anchors) < MinAlignedDates:
        return []
    gaps = [position(b) - position(a) for a, b in pairwise(anchors)]
    limit = MaxRunGapRatio * statistics.median(gaps)
    runs = [[anchors[0]]]
    for (a, b), gap in zip(pairwise(anchors), gaps, strict=True):
        if _valid_step(a.day, b.day) and gap <= limit and not interrupted(a, b):
            runs[-1].append(b)
        else:
            runs.append([b])
    return [_Run(orientation, tuple(run)) for run in runs if len(run) >= MinAlignedDates]


# --- Page analysée ---


@dataclass
class _Page:
    index: int
    width: float
    height: float
    words: list[_Word]
    lines: list[_Line]
    h_rules: list[Rect]
    v_rules: list[Rect]
    text_height: float
    runs: list[_Run]


def _analyze(index: int, page, words: list[_Word]) -> _Page:
    heights = [w.bottom - w.top for w in words]
    analyzed = _Page(
        index=index,
        width=float(page.width),
        height=float(page.height),
        words=words,
        lines=_group_lines(words),
        h_rules=_merge_rules(page.horizontal_edges, horizontal=True),
        v_rules=_merge_rules(page.vertical_edges, horizontal=False),
        text_height=statistics.median(heights) if heights else 10.0,
        runs=[],
    )
    anchors = [_Anchor(w, day) for w in words if (day := _day_number(w.text)) is not None]
    analyzed.runs = _horizontal_runs(anchors) + _vertical_runs(analyzed, anchors)
    return analyzed


def _horizontal_runs(anchors: list[_Anchor]) -> list[_Run]:
    """En-têtes « une ligne par personne » : dates alignées sur une ligne."""
    lines: list[list[_Anchor]] = []
    for anchor in sorted(anchors, key=lambda a: (a.word.top, a.word.x0)):
        if lines and abs(anchor.word.top - lines[-1][0].word.top) <= DateAlignTolerance:
            lines[-1].append(anchor)
        else:
            lines.append([anchor])
    runs = []
    for line in lines:
        line.sort(key=lambda a: a.word.x0)
        runs += _split_runs(line, "rows", lambda a: a.word.x0)
    return runs


def _vertical_runs(page: _Page, anchors: list[_Anchor]) -> list[_Run]:
    """En-têtes « une colonne par personne » : dates alignées en colonne."""
    columns: list[list[_Anchor]] = []
    for anchor in sorted(anchors, key=lambda a: a.word.x0):
        for column in columns:
            if any(_x_overlap(anchor.word, a.word.x0, a.word.x1) > 0 for a in column):
                column.append(anchor)
                break
        else:
            columns.append([anchor])
    runs = []
    for column in columns:
        column.sort(key=lambda a: a.word.top)
        runs += _split_runs(
            column,
            "columns",
            lambda a: a.word.top,
            lambda a, b: _column_interrupted(page, a, b),
        )
    return runs


def _column_interrupted(page: _Page, a: _Anchor, b: _Anchor) -> bool:
    """Un texte qui n'est pas une date entre deux dates de la colonne (le coin
    « Date » d'un second tableau empilé dessous) : deux tableaux, pas un.
    Une deuxième ligne dans la case de la date (« Férié ») ne compte pas."""
    labels = _label_words(page, a) + _label_words(page, b)
    x0 = min(w.x0 for w in labels)
    x1 = max(w.x1 for w in labels)
    gap = CellLineGapRatio * page.text_height
    return any(
        w.top >= a.word.bottom + gap
        and w.bottom <= b.word.top
        and _x_overlap(w, x0, x1) > 0
        and not _is_dateish(w.text)
        for w in page.words
    )


def _label_words(page: _Page, anchor: _Anchor) -> list[_Word]:
    """Libellé complet d'une date dans une colonne de dates : les mots à sa
    gauche sur la même ligne (« Lun » de « Lun 14/09 », ou d'une colonne
    « Jour » séparée) et ceux, de date, collés à sa droite (« 14 sept. »)."""
    same_line = sorted(
        (w for w in page.words if _y_overlap(w, anchor.word.top, anchor.word.bottom) > 0),
        key=lambda w: w.x0,
    )
    label = [w for w in same_line if w.x1 <= anchor.word.x0 + 0.5] + [anchor.word]
    reach = 2 * page.text_height
    right = anchor.word.x1
    for word in same_line:
        if word.x0 >= anchor.word.x1 - 0.5 and word.x0 - right <= reach and _is_dateish(word.text):
            label.append(word)
            right = word.x1
    return label


# --- Recherche du nom ---


@dataclass(frozen=True)
class _NameMatch:
    page_index: int
    rect: Rect
    text: str


def _spans(line: Sequence[_Word], max_length: int) -> Iterable[Sequence[_Word]]:
    for length in range(1, max_length + 1):
        for start in range(len(line) - length + 1):
            yield line[start : start + length]


def _text(span: Iterable[_Word]) -> str:
    return " ".join(w.text for w in span)


def _compact(text: str) -> str:
    """Texte normalisé sans espaces : un nom dont les mots sont dessinés un
    par un, sans caractère espace entre eux, sort collé de pdfplumber
    (« DUPONTJean ») ; l'inverse arrive aussi (« DU PONT »)."""
    return normalize(text).replace(" ", "")


def _search_name(pages: Sequence[_Page], candidate: str) -> list[_NameMatch]:
    target = _compact(candidate)
    if not target:
        return []
    max_length = min(MaxNameWords, len(normalize(candidate).split()) + 1)
    matches: list[_NameMatch] = []
    for page in pages:
        # Un jeton fait uniquement de ponctuation (le « - » d'une case vide,
        # juste après le nom) ne dit rien du nom, mais inclus dans une
        # fenêtre il la faisait matcher une seconde fois (« Jean DUPONT » et
        # « Jean DUPONT - » se normalisent pareil) : une seule personne
        # passait pour un homonyme.
        lines = [[w for w in line.words if normalize(w.text)] for line in page.lines]
        found = [
            _NameMatch(page.index, _bbox(span), _text(span))
            for line in lines
            for span in _spans(line, max_length)
            if _compact(_text(span)) == target
        ]
        found += _stacked_matches(page, lines, target, max_length)
        for match in found:
            if not any(
                m.page_index == match.page_index and _overlaps(m.rect, match.rect) for m in matches
            ):
                matches.append(match)
    return matches


def _stacked_matches(
    page: _Page, lines: list[list[_Word]], target: str, max_length: int
) -> list[_NameMatch]:
    """Nom renvoyé à la ligne dans sa case : « DUPONT » puis « Jean » juste
    dessous, dans la même colonne."""
    if max_length < 2:
        return []
    matches = []
    for index, upper in enumerate(lines):
        for lower in lines[index + 1 : index + 4]:
            for first in _spans(upper, max_length - 1):
                first_box = _bbox(first)
                limit = StackedNameGapRatio * first_box.height
                for second in _spans(lower, max_length - len(first)):
                    second_box = _bbox(second)
                    if (
                        -1 <= second_box.top - first_box.bottom <= limit
                        and _x_overlap(second_box, first_box.x0, first_box.x1) > 0
                        and _compact(f"{_text(first)} {_text(second)}") == target
                    ):
                        text = f"{_text(first)} {_text(second)}"
                        matches.append(_NameMatch(page.index, _bbox([first_box, second_box]), text))
    return matches


def _overlaps(a: Rect, b: Rect) -> bool:
    return _x_overlap(a, b.x0, b.x1) > 0 and _y_overlap(a, b.top, b.bottom) > 0


def find_name(pages: Sequence[_Page], candidates: list[str]) -> tuple[list[_NameMatch], str] | None:
    """Essaie chaque candidat dans l'ordre ; s'arrête au premier qui matche,
    avec toutes ses occurrences (une par tableau, ou des homonymes : c'est
    `locate` qui tranche, une fois les tableaux repérés)."""
    for candidate in candidates:
        matches = _search_name(pages, candidate)
        if matches:
            return matches, candidate
    return None


@dataclass
class _LineHits:
    """Mots du nom tapé retrouvés sur une même ligne (voir `find_name_by_words`)."""

    page_index: int
    rect: Rect
    matches: dict[str, _NameMatch]

    def contains(self, match: _NameMatch) -> bool:
        return self.page_index == match.page_index and (
            _y_overlap(self.rect, match.rect.top, match.rect.bottom) > 0
        )

    def text(self) -> str:
        ordered = sorted(self.matches.values(), key=lambda m: m.rect.x0)
        return " ".join(m.text for m in ordered)


def find_name_by_words(
    pages: Sequence[_Page], words: Sequence[str]
) -> tuple[_NameMatch, str] | LocateFailure:
    """Repli quand aucun candidat complet ne matche : chaque mot est cherché
    seul, et la ligne où le plus de mots se retrouvent gagne.

    Retour utilisateur : sur son planning, « Jean DUPONT » ne matchait jamais
    alors que « Jean » seul, oui. Si le nom de famille n'est pas lisible comme
    texte pour pdfplumber (police sans table Unicode, texte vectorisé, lettres
    espacées... impossible à savoir d'ici), seul un repli sur les mots lisibles
    peut retrouver la ligne.

    Garde-fou (risque n°1 : ne jamais choisir quelqu'un d'autre) : deux lignes
    à égalité — « Jean » sur l'une, « DUPONT » sur l'autre — ne sont pas
    départagées, on renvoie `nom_homonyme` avec les deux textes."""
    if len(words) < 2:
        return LocateFailure("nom_introuvable")
    lines: list[_LineHits] = []
    for word in dict.fromkeys(words):
        for match in _search_name(pages, word):
            for line in lines:
                if line.contains(match):
                    line.matches.setdefault(word, match)
                    line.rect = _bbox([line.rect, match.rect])
                    break
            else:
                lines.append(_LineHits(match.page_index, match.rect, {word: match}))
    if not lines:
        return LocateFailure("nom_introuvable")
    best_score = max(len(line.matches) for line in lines)
    best = [line for line in lines if len(line.matches) == best_score]
    if len(best) > 1:
        return LocateFailure("nom_homonyme", tuple(line.text() for line in best))
    line = best[0]
    return _NameMatch(line.page_index, line.rect, line.text()), " ".join(words)


# --- Bornes d'une ligne ou d'une colonne du tableau ---


def _cluster(
    lines: Sequence[_Line], top: float, bottom: float, gap: float, allow: Callable[[_Line], bool]
) -> tuple[float, float, list[_Word]]:
    """Étend l'intervalle [top, bottom] aux lignes de texte qui le touchent
    (à moins de `gap`) et que `allow` accepte : les lignes d'une même case."""
    chosen: list[_Line] = []
    remaining = list(lines)
    changed = True
    while changed:
        changed = False
        for line in list(remaining):
            if line.top - bottom <= gap and top - line.bottom <= gap and allow(line):
                chosen.append(line)
                remaining.remove(line)
                top, bottom = min(top, line.top), max(bottom, line.bottom)
                changed = True
    return top, bottom, [w for line in chosen for w in line.words]


def _boundary(
    edge: float,
    neighbour: float | None,
    rules: Iterable[float],
    *,
    direction: int,
    reach: float,
) -> tuple[float, bool]:
    """Borne d'un côté d'un texte (`edge`), vers son voisin (`neighbour`,
    None s'il n'y en a pas) : le trait le plus proche entre les deux s'il y en
    a un, sinon le milieu du blanc (au plus `MaxPad`), sinon une marge.
    `direction` : -1 vers le haut ou la gauche, +1 vers le bas ou la droite.
    Renvoie aussi si un trait a été retenu."""
    far = neighbour if neighbour is not None else edge + direction * reach
    low, high = sorted((edge, far))
    between = [r for r in rules if low - 0.5 <= r <= high + 0.5]
    if between:
        return (max(between) if direction < 0 else min(between)), True
    if neighbour is not None:
        middle = (edge + neighbour) / 2
        return (edge + direction * min(abs(middle - edge), MaxPad)), False
    return edge + direction * Pad, False


def _vertical_bounds(
    page: _Page, top: float, bottom: float, x0: float, x1: float, exclude: set[_Word]
) -> tuple[float, float, list[Rect]]:
    """Bornes haute et basse d'une bande de texte [top, bottom] d'un tableau
    qui s'étend de x0 à x1."""
    words = [w for w in page.words if w not in exclude and _x_overlap(w, x0, x1) > 0]
    above = max((w.bottom for w in words if w.bottom <= top + 0.5), default=None)
    below = min((w.top for w in words if w.top >= bottom - 0.5), default=None)
    rules = [r for r in page.h_rules if _x_overlap(r, x0, x1) >= RuleCoverage * (x1 - x0)]
    reach = 3 * page.text_height
    new_top, top_ruled = _boundary(top, above, [r.top for r in rules], direction=-1, reach=reach)
    new_bottom, bottom_ruled = _boundary(
        bottom, below, [r.top for r in rules], direction=1, reach=reach
    )
    used = [
        r
        for r in rules
        if (top_ruled and r.top == new_top) or (bottom_ruled and r.top == new_bottom)
    ]
    return new_top, new_bottom, used


def _horizontal_bounds(
    page: _Page, x0: float, x1: float, top: float, bottom: float, exclude: set[_Word]
) -> tuple[float, float]:
    """Bornes gauche et droite d'une colonne de texte [x0, x1] d'un tableau
    qui s'étend de top à bottom."""
    words = [w for w in page.words if w not in exclude and _y_overlap(w, top, bottom) > 0]
    left = max((w.x1 for w in words if w.x1 <= x0 + 0.5), default=None)
    right = min((w.x0 for w in words if w.x0 >= x1 - 0.5), default=None)
    rules = [
        r.x0 for r in page.v_rules if _y_overlap(r, top, bottom) >= RuleCoverage * (bottom - top)
    ]
    reach = 3 * page.text_height
    new_x0, _ = _boundary(x0, left, rules, direction=-1, reach=reach)
    new_x1, _ = _boundary(x1, right, rules, direction=1, reach=reach)
    return new_x0, new_x1


def _clamp(page: _Page, rect: Rect) -> Rect:
    return Rect(
        max(0.0, rect.x0),
        max(0.0, rect.top),
        min(page.width, rect.x1),
        min(page.height, rect.bottom),
    )


def _same_line(a: _Box, b: _Box) -> bool:
    return _y_overlap(a, b.top, b.bottom) >= 0.5 * min(a.bottom - a.top, b.bottom - b.top)


def _rule_between(rules: Iterable[float], a: _Box, b: _Box) -> bool:
    low, high = sorted((a.x1, b.x0) if a.x1 <= b.x0 else (b.x1, a.x0))
    return any(low - 0.5 < x < high + 0.5 for x in rules)


def _name_cell(page: _Page, name: Rect) -> Rect:
    """La case du nom en entier, au-delà des mots qui ont matché.

    Retour utilisateur : « ne marche pas si je mets que le prénom, coupe les
    horaires ». Tapé seul, « Florian » ne couvre qu'un mot de la case
    « BERROT Florian » ; la colonne découpée se réduisait à ce mot, alors que
    les horaires sont alignés sous le début de la case. Sont ajoutés les mots
    de la même ligne collés au nom par un blanc d'une espace, sans trait
    vertical entre eux, et sans chiffre : dans un tableau serré, la case
    d'horaires voisine (« 7h-15h ») ne doit jamais être prise pour le nom, elle
    serait grisée avant l'envoi au modèle."""
    line_words = [w for w in page.words if _same_line(w, name)]
    cell = [w for w in line_words if _inside(w, name)]
    rules = [r.x0 for r in page.v_rules if r.top <= name.top + 1 and r.bottom >= name.bottom - 1]
    limit = CellWordGapRatio * page.text_height
    grown = True
    while grown:
        grown = False
        for word in line_words:
            if word in cell or any(ch.isdigit() for ch in word.text):
                continue
            if any(
                _same_line(word, c)
                and max(word.x0 - c.x1, c.x0 - word.x1) <= limit
                and not _rule_between(rules, c, word)
                for c in cell
            ):
                cell.append(word)
                grown = True
    return _bbox([name, *cell])


def _row_name_cell(page: _Page, cell: Rect, header_bottom: float) -> Rect:
    """Ajoute à la case du nom sa deuxième ligne (« DUPONT » puis « Jean »),
    quand un seul des deux mots a été tapé : sans elle, la ligne voisine
    passait pour celle d'une autre personne et ses horaires étaient coupés.

    Une ligne voisine dans la colonne du nom en fait partie si aucun trait ne
    les sépare, si elles sont à moins d'un interligne, et si l'une des deux ne
    contient que le nom — ou si des traits proches encadrent les deux, comme
    dans une grille. Sans cette dernière condition, deux personnes sur des
    lignes serrées et sans traits seraient confondues."""
    gap_limit = CellLineGapRatio * page.text_height

    def column_rules() -> list[float]:
        return [r.top for r in page.h_rules if r.x0 <= cell.x0 + 1 and r.x1 >= cell.x1 - 1]

    def only_name(lines: Iterable[_Line], box: Rect) -> bool:
        return all(_x_overlap(w, box.x0, box.x1) > 0 for line in lines for w in line.words)

    grown = True
    while grown:
        grown = False
        own_lines = [line for line in page.lines if _y_overlap(line, cell.top, cell.bottom) > 0]
        for line in page.lines:
            if line in own_lines or line.top < header_bottom - 0.5:
                continue
            in_column = [w for w in line.words if _x_overlap(w, cell.x0, cell.x1) > 0]
            gap = max(line.top - cell.bottom, cell.top - line.bottom)
            if not in_column or gap > gap_limit:
                continue
            union = _bbox([cell, *in_column])
            rules = column_rules()
            low, high = sorted((line.top, cell.top))
            if any(
                low + 0.5 < y < high - 0.5 and not (cell.top <= y <= cell.bottom) for y in rules
            ):
                continue
            ruled = any(union.top - MaxPad <= y <= union.top + 0.5 for y in rules) and any(
                union.bottom - 0.5 <= y <= union.bottom + MaxPad for y in rules
            )
            if ruled or only_name([line], cell) or only_name(own_lines, cell):
                cell = union
                grown = True
                break
    return cell


def _rows_block(page: _Page, run: _Run, match: _NameMatch) -> Block:
    """Une ligne par personne : en-tête de dates au-dessus, ligne du nom."""
    gap = CellLineGapRatio * page.text_height
    header_box = run.rect

    header_top, header_bottom, header_words = _cluster(
        page.lines,
        header_box.top,
        header_box.bottom,
        gap,
        lambda line: line.bottom <= match.rect.top + 0.5,
    )
    name = _row_name_cell(page, match.rect, header_bottom)
    span_x0, span_x1 = min(name.x0, header_box.x0), max(name.x1, header_box.x1)

    def same_row(line: _Line) -> bool:
        # Une autre personne dans la colonne des noms : ligne suivante.
        return line.top >= header_bottom - 0.5 and not any(
            _x_overlap(w, name.x0, name.x1) > 0 and not _inside(w, name) for w in line.words
        )

    row_top, row_bottom, row_words = _cluster(page.lines, name.top, name.bottom, gap, same_row)

    header_set, row_set = set(header_words), set(row_words)
    h_top, h_bottom, h_rules = _vertical_bounds(
        page, header_top, header_bottom, span_x0, span_x1, header_set
    )
    l_top, l_bottom, l_rules = _vertical_bounds(
        page, row_top, row_bottom, span_x0, span_x1, row_set
    )

    words = header_words + row_words
    x0 = min([w.x0 for w in words] + [name.x0]) - Pad
    x1 = max([w.x1 for w in words] + [name.x1]) + Pad
    for rule in h_rules + l_rules:
        x0, x1 = min(x0, rule.x0), max(x1, rule.x1)
    return Block(
        page_index=page.index,
        orientation="rows",
        header=_clamp(page, Rect(x0, h_top, x1, h_bottom)),
        line=_clamp(page, Rect(x0, l_top, x1, l_bottom)),
        name=name,
    )


def _columns_block(page: _Page, run: _Run, match: _NameMatch) -> Block:
    """Une colonne par personne : colonne de dates à gauche, colonne du nom."""
    name = match.rect
    gap = CellLineGapRatio * page.text_height
    first, last = run.anchors[0].word, run.anchors[-1].word
    labels = [w for anchor in run.anchors for w in _label_words(page, anchor)]
    label_x0 = min(w.x0 for w in labels)
    label_x1 = max(w.x1 for w in labels)

    def in_labels(word: _Word) -> bool:
        return _x_overlap(word, label_x0, label_x1) > 0

    # Rangée des noms, au-dessus de la première date.
    names_top, names_bottom, _ = _cluster(
        page.lines,
        name.top,
        name.bottom,
        gap,
        lambda line: line.bottom <= first.top + 0.5,
    )
    # Dernière rangée : la dernière date, et les lignes suivantes de ses cases.
    _, rows_bottom, _ = _cluster(
        page.lines,
        last.top,
        last.bottom,
        gap,
        lambda line: (
            line.top >= last.top - 0.5
            and (
                _y_overlap(line, last.top, last.bottom) > 0
                or not any(in_labels(w) for w in line.words)
            )
        ),
    )

    column_words = [
        w
        for w in page.words
        if _x_overlap(w, name.x0, name.x1) > 0 and _y_overlap(w, names_top, rows_bottom) > 0
    ]
    column_x0 = min(w.x0 for w in column_words + [name])
    column_x1 = max(w.x1 for w in column_words + [name])

    table_words = {w for w in page.words if _y_overlap(w, names_top, rows_bottom) > 0}
    top, bottom, _ = _vertical_bounds(
        page, names_top, rows_bottom, label_x0, column_x1, table_words
    )
    header_x0, header_x1 = _horizontal_bounds(page, label_x0, label_x1, top, bottom, set(labels))
    line_x0, line_x1 = _horizontal_bounds(
        page, column_x0, column_x1, top, bottom, set(column_words)
    )
    # Toute la case du nom est grisée, pas seulement le mot tapé : un nom sur
    # deux lignes (« DUPONT » puis « Jean ») y tient en entier.
    names_row_bottom = min(names_bottom + Pad, (names_bottom + first.top) / 2)
    return Block(
        page_index=page.index,
        orientation="columns",
        header=_clamp(page, Rect(header_x0, top, header_x1, bottom)),
        line=_clamp(page, Rect(line_x0, top, line_x1, bottom)),
        name=_clamp(page, Rect(line_x0, top, line_x1, names_row_bottom)),
    )


def _header_run(page: _Page, match: _NameMatch) -> _Run | None:
    """L'en-tête de dates du tableau du nom : la suite de dates la plus
    proche, soit sur une ligne au-dessus, soit dans une colonne à gauche et
    en dessous."""
    name = match.rect
    options: list[tuple[float, _Run]] = []
    for run in page.runs:
        box = run.rect
        if run.orientation == "rows" and box.bottom <= name.top + 1 and box.x1 > name.x0:
            options.append((name.top - box.bottom, run))
        if run.orientation == "columns" and box.x1 <= name.x0 + 1 and box.top >= name.bottom - 1:
            options.append((box.top - name.bottom, run))
    if not options:
        return None
    return min(options, key=lambda option: option[0])[1]


def _is_scanned(pages: Sequence[_Page]) -> bool:
    return all(not page.words for page in pages)


def _page_words(page) -> list[_Word]:
    return [
        _Word(w["text"], float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]))
        for w in page.extract_words()
    ]


def locate(
    pdf_path: str, candidates: list[str], *, fallback_words: Sequence[str] = ()
) -> LocateResult | LocateFailure:
    """Localise l'en-tête et la ligne (ou colonne) de la personne, dans
    chaque tableau où elle figure.

    `candidates` : voir `build_candidates`. `fallback_words` : les mots du nom
    tapé par la personne, cherchés un par un si aucun candidat ne matche
    (`find_name_by_words`) ; vide pour la détection automatique depuis le
    compte Google, dont la cascade ne doit jamais chercher le prénom seul
    (risque n°1). Retourne un `LocateResult` (bornes en points PDF, prêtes
    pour `crop.py`) ou un `LocateFailure` avec le motif.

    Le même nom dans plusieurs tableaux n'est accepté que si leurs dates
    diffèrent (plusieurs semaines) : deux fois dans le même tableau, ou dans
    deux tableaux de la même semaine (deux équipes), ce sont peut-être deux
    personnes — `nom_homonyme`.
    """
    with pdfplumber.open(pdf_path) as pdf:
        pages = [_analyze(i, page, _page_words(page)) for i, page in enumerate(pdf.pages)]

    if _is_scanned(pages):
        return LocateFailure("pdf_scanne")

    found = find_name(pages, candidates)
    if found is None:
        fallback = find_name_by_words(pages, fallback_words)
        if isinstance(fallback, LocateFailure):
            return fallback
        found = ([fallback[0]], fallback[1])
    matches, candidate_used = found

    located: list[tuple[_NameMatch, _Run, Block]] = []
    for found_match in matches:
        page = pages[found_match.page_index]
        match = _NameMatch(page.index, _name_cell(page, found_match.rect), found_match.text)
        run = _header_run(page, match)
        if run is None:
            # Nom hors d'un tableau daté (titre, légende…) : ignoré s'il
            # figure aussi dans un tableau.
            continue
        build = _rows_block if run.orientation == "rows" else _columns_block
        located.append((match, run, build(page, run, match)))

    if not located:
        return LocateFailure("pas_de_dates")
    signatures = [run.signature for _, run, _ in located]
    if len(set(signatures)) < len(signatures):
        return LocateFailure("nom_homonyme", tuple(match.text for match, _, _ in located))

    blocks = sorted(
        (block for _, _, block in located), key=lambda b: (b.page_index, b.line.top, b.line.x0)
    )
    first = blocks[0]
    return LocateResult(
        blocks=tuple(blocks),
        matched_text=located[0][0].text,
        candidate_used=candidate_used,
        name_text=_text_inside(pages[first.page_index], first.name),
    )


def _text_inside(page: _Page, rect: Rect) -> str:
    """Les mots d'une zone, dans l'ordre de lecture."""
    words = [w for w in page.words if _inside(w, rect, tolerance=1.0)]
    return " ".join(w.text for line in _group_lines(words) for w in line.words)
