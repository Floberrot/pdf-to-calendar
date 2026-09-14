"""Localisation de la ligne d'une personne dans un PDF de planning (plan, section 5B).

Trois recherches par contenu, aucune hypothèse sur la mise en page :
nom → position de la ligne, traits du tableau → bornes de la ligne,
dates → bande d'en-tête. Fonction pure, testable sur un PDF en local.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import pdfplumber

LineTolerance = 3.0
DateAlignTolerance = 5.0
MinEdgeWidthRatio = 1 / 3
MinAlignedDates = 3
HeaderMargin = 3.0

FailureReason = Literal[
    "pdf_scanne",
    "nom_introuvable",
    "nom_homonyme",
    "pas_de_traits",
    "pas_de_dates",
]


@dataclass(frozen=True)
class Band:
    top: float
    bottom: float


@dataclass(frozen=True)
class LocateResult:
    page_index: int
    table_left: float
    table_right: float
    header: Band
    line: Band
    matched_text: str
    candidate_used: str


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
    """Candidats de nom, dans l'ordre (plan, section 5B-1)."""
    if pdf_name:
        return [pdf_name]

    family = family_name.strip()
    given = given_name.strip()
    initial = given[:1] if given else ""

    raw = [
        f"{family} {given}".strip(),
        f"{given} {family}".strip(),
        f"{family} {initial}".strip(),
        f"{initial} {family}".strip(),
        family,
    ]
    seen: set[str] = set()
    candidates = []
    for candidate in raw:
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def _group_lines(words: list[dict], tolerance: float = LineTolerance) -> list[list[dict]]:
    """Groupe des mots pdfplumber par ligne visuelle (proximité verticale)."""
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for word in ordered:
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _window_bbox(span: list[dict]) -> dict:
    return {
        "x0": min(w["x0"] for w in span),
        "x1": max(w["x1"] for w in span),
        "top": min(w["top"] for w in span),
        "bottom": max(w["bottom"] for w in span),
    }


@dataclass(frozen=True)
class _NameMatch:
    page_index: int
    bbox: dict
    text: str


def _search_name(pages_words: list[list[dict]], candidate: str) -> list[_NameMatch]:
    target = normalize(candidate)
    if not target:
        return []
    matches = []
    for page_index, words in enumerate(pages_words):
        for line in _group_lines(words):
            n = len(line)
            for length in (1, 2, 3):
                for start in range(n - length + 1):
                    span = line[start : start + length]
                    text = " ".join(w["text"] for w in span)
                    if normalize(text) == target:
                        matches.append(_NameMatch(page_index, _window_bbox(span), text))
    return matches


def find_name(
    pages_words: list[list[dict]], candidates: list[str]
) -> tuple[_NameMatch, str] | LocateFailure:
    """Essaie chaque candidat dans l'ordre ; s'arrête au premier qui matche."""
    for candidate in candidates:
        matches = _search_name(pages_words, candidate)
        if len(matches) == 1:
            return matches[0], candidate
        if len(matches) > 1:
            return LocateFailure("nom_homonyme", tuple(m.text for m in matches))
    return LocateFailure("nom_introuvable")


def _long_edges(edges: list[dict], page_width: float) -> list[dict]:
    min_length = page_width * MinEdgeWidthRatio
    return [e for e in edges if (e["x1"] - e["x0"]) >= min_length]


def _line_bounds(long_edges: list[dict], name_top: float, name_bottom: float) -> Band | None:
    above = [e for e in long_edges if e["top"] <= name_top]
    below = [e for e in long_edges if e["top"] >= name_bottom]
    if not above or not below:
        return None
    return Band(top=max(e["top"] for e in above), bottom=min(e["top"] for e in below))


_DAY_ABBR = r"(?:lun|mar|mer|jeu|ven|sam|dim)\.?"
_DAY_FULL = r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
_MONTH_ABBR = r"(?:janv|fevr|mars|avr|mai|juin|juil|aout|sept|oct|nov|dec)\.?"

_DATE_PATTERN = re.compile(
    rf"^(?:"
    rf"{_DAY_ABBR}\s+\d{{1,2}}"
    rf"|{_DAY_FULL}\s+\d{{1,2}}(?:\s+{_MONTH_ABBR})?"
    rf"|\d{{1,2}}/\d{{1,2}}"
    rf"|\d{{1,2}}\s+{_MONTH_ABBR}"
    rf")$",
    re.IGNORECASE,
)


def _fold_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _is_date_like(text: str) -> bool:
    return bool(_DATE_PATTERN.match(_fold_accents(text).strip()))


def _date_windows_in_line(line: list[dict]) -> list[dict]:
    """Fenêtres de mots adjacents (1 à 3) qui ressemblent à une date, sans chevauchement."""
    matches = []
    used: set[int] = set()
    n = len(line)
    for length in (3, 2, 1):
        for start in range(n - length + 1):
            span_range = range(start, start + length)
            if any(i in used for i in span_range):
                continue
            span = line[start : start + length]
            text = " ".join(w["text"] for w in span)
            if _is_date_like(text):
                matches.append(_window_bbox(span))
                used.update(span_range)
    return matches


def _header_band(words_above: list[dict], edges: list[dict]) -> Band | None:
    lines = _group_lines(words_above, tolerance=DateAlignTolerance)
    best: tuple[list[list[dict]], list[dict]] | None = None
    for index, line in enumerate(lines):
        date_matches = _date_windows_in_line(line)
        if len(date_matches) >= MinAlignedDates:
            best = (lines[index:], date_matches)
            break
    if best is None:
        return None

    remaining_lines, date_matches = best
    header_top = min(m["top"] for m in date_matches) - HeaderMargin
    header_bottom = max(m["bottom"] for m in date_matches)

    # Jours et dates sont souvent sur deux lignes : inclut la ligne suivante si
    # aucun trait ne la sépare de l'en-tête déjà trouvé.
    if len(remaining_lines) > 1:
        next_line = remaining_lines[1]
        next_top = min(w["top"] for w in next_line)
        separated = any(header_bottom < e["top"] < next_top for e in edges)
        if not separated:
            header_bottom = max(w["bottom"] for w in next_line)

    return Band(top=header_top, bottom=header_bottom + HeaderMargin)


def _is_scanned(pages_words: list[list[dict]]) -> bool:
    return all(len(words) == 0 for words in pages_words)


def locate(pdf_path: str, candidates: list[str]) -> LocateResult | LocateFailure:
    """Localise l'en-tête et la ligne de la personne dans le PDF.

    `candidates` : voir `build_candidates`. Retourne un `LocateResult` (bornes
    en points PDF, prêtes pour `crop.py`) ou un `LocateFailure` avec le motif.
    """
    with pdfplumber.open(pdf_path) as pdf:
        pages_words = [page.extract_words() for page in pdf.pages]

        if _is_scanned(pages_words):
            return LocateFailure("pdf_scanne")

        name_result = find_name(pages_words, candidates)
        if isinstance(name_result, LocateFailure):
            return name_result
        name_match, candidate_used = name_result

        page = pdf.pages[name_match.page_index]
        edges = page.horizontal_edges
        long_edges = _long_edges(edges, page.width)

        line = _line_bounds(long_edges, name_match.bbox["top"], name_match.bbox["bottom"])
        if line is None:
            return LocateFailure("pas_de_traits")

        words_above = [w for w in pages_words[name_match.page_index] if w["bottom"] <= line.top]
        header = _header_band(words_above, edges)
        if header is None:
            return LocateFailure("pas_de_dates")

        table_left = min(e["x0"] for e in long_edges)
        table_right = max(e["x1"] for e in long_edges)

        return LocateResult(
            page_index=name_match.page_index,
            table_left=table_left,
            table_right=table_right,
            header=header,
            line=line,
            matched_text=name_match.text,
            candidate_used=candidate_used,
        )
