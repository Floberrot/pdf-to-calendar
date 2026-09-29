"""Créneaux détectés, présentés semaine par semaine pour la prévisualisation.

Retour utilisateur : « dans le cas d'un gros import, les créneaux ne sont
pas visibles ». Une liste d'une ligne par créneau s'étire sur plusieurs
écrans pour un mois : une grille du lundi au dimanche, comme sur le
planning, se lit d'un coup d'œil. Fonctions pures, sans dépendance web.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.validate import Creneau, ValidatedExtraction

JOURS_COURTS = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
MOIS_COURTS = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)


@dataclass(frozen=True)
class Jour:
    """Une case de la grille. `dans_periode` : faux pour les jours qui ne
    font que compléter la première ou la dernière semaine. `libelle` : le
    numéro du jour, avec le mois au premier jour affiché et au 1er du mois,
    comme dans un agenda (tient dans une case étroite sur téléphone)."""

    date: date
    dans_periode: bool
    creneaux: tuple[Creneau, ...]
    incertain: str | None
    libelle: str


@dataclass(frozen=True)
class Semaine:
    jours: tuple[Jour, ...]
    heures: float


@dataclass(frozen=True)
class Resume:
    creneaux: int
    heures: float
    jours_travailles: int
    jours_incertains: int


def semaines(extraction: ValidatedExtraction) -> list[Semaine]:
    """Les semaines de la période, du lundi au dimanche."""
    par_jour: dict[date, list[Creneau]] = {}
    for creneau in extraction.creneaux:
        par_jour.setdefault(creneau.date, []).append(creneau)
    incertains = {jour.date: jour.texte for jour in extraction.jours_incertains}

    debut = extraction.periode_debut - timedelta(days=extraction.periode_debut.weekday())
    result: list[Semaine] = []
    while debut <= extraction.periode_fin:
        jours = []
        for offset in range(7):
            jour = debut + timedelta(days=offset)
            creneaux = tuple(sorted(par_jour.get(jour, ()), key=lambda c: c.debut))
            with_month = jour == extraction.periode_debut or jour.day == 1
            jours.append(
                Jour(
                    date=jour,
                    dans_periode=extraction.periode_debut <= jour <= extraction.periode_fin,
                    creneaux=creneaux,
                    incertain=incertains.get(jour),
                    libelle=f"{jour.day} {MOIS_COURTS[jour.month - 1]}"
                    if with_month
                    else str(jour.day),
                )
            )
        heures = sum(c.duration_hours for j in jours for c in j.creneaux)
        result.append(Semaine(jours=tuple(jours), heures=heures))
        debut += timedelta(weeks=1)
    return result


def resume(extraction: ValidatedExtraction) -> Resume:
    return Resume(
        creneaux=len(extraction.creneaux),
        heures=sum(c.duration_hours for c in extraction.creneaux),
        jours_travailles=len({c.date for c in extraction.creneaux}),
        jours_incertains=len(extraction.jours_incertains),
    )


def format_duree(heures: float) -> str:
    """« 8 h », « 7 h 30 » plutôt que « 8.0 h » ou « 7.5 h »."""
    minutes = round(heures * 60)
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d}" if m else f"{h} h"
