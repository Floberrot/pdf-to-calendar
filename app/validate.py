"""Validation locale du JSON renvoyé par le modèle (plan, section 5D).

Pure et indépendante des settings : `validation_weeks` est un paramètre, pas
une lecture globale, pour rester testable sans configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

MAX_PERIODE_WEEKS = 6
MIN_CRENEAU_HOURS = 1
MAX_CRENEAU_HOURS = 14

ValidationReason = Literal[
    "periode_invalide",
    "periode_trop_longue",
    "periode_hors_fenetre",
    "creneau_invalide",
    "creneau_hors_periode",
    "creneau_duree_invalide",
]


@dataclass(frozen=True)
class Creneau:
    date: date
    debut: str
    fin: str
    lieu: str
    duration_hours: float


@dataclass(frozen=True)
class JourIncertain:
    """Case non vide sur le planning, mais ni horaire clair ni absence
    reconnue (llm.py) : un événement journée entière est créé pour ce jour
    plutôt que de perdre l'information en silence (retour utilisateur)."""

    date: date
    texte: str


@dataclass(frozen=True)
class ValidatedExtraction:
    periode_debut: date
    periode_fin: date
    creneaux: list[Creneau]
    jours_incertains: list[JourIncertain]


@dataclass(frozen=True)
class ValidationError:
    reason: ValidationReason


def _creneau_duration_hours(debut: str, fin: str) -> float:
    """Durée en heures ; si fin <= début, le créneau se termine le lendemain
    (ex. 21:00-07:00 = 10h)."""
    start = datetime.strptime(debut, "%H:%M").replace(tzinfo=UTC)
    end = datetime.strptime(fin, "%H:%M").replace(tzinfo=UTC)
    if end <= start:
        end += timedelta(days=1)
    return (end - start).total_seconds() / 3600


def validate(
    raw: dict, *, validation_weeks: int, today: date | None = None
) -> ValidatedExtraction | ValidationError:
    today = today or datetime.now(UTC).date()

    try:
        periode_debut = date.fromisoformat(raw["periode"]["debut"])
        periode_fin = date.fromisoformat(raw["periode"]["fin"])
    except (KeyError, ValueError, TypeError):
        return ValidationError("periode_invalide")

    if periode_fin < periode_debut:
        return ValidationError("periode_invalide")

    if (periode_fin - periode_debut).days > MAX_PERIODE_WEEKS * 7:
        return ValidationError("periode_trop_longue")

    window_start = today - timedelta(weeks=2)
    window_end = today + timedelta(weeks=validation_weeks)
    if periode_debut < window_start or periode_fin > window_end:
        return ValidationError("periode_hors_fenetre")

    creneaux: list[Creneau] = []
    for raw_creneau in raw.get("creneaux") or []:
        try:
            creneau_date = date.fromisoformat(raw_creneau["date"])
            debut = raw_creneau["debut"]
            fin = raw_creneau["fin"]
        except (KeyError, ValueError, TypeError):
            return ValidationError("creneau_invalide")

        if creneau_date < periode_debut or creneau_date > periode_fin:
            return ValidationError("creneau_hors_periode")

        try:
            duration = _creneau_duration_hours(debut, fin)
        except ValueError:
            return ValidationError("creneau_invalide")

        if not (MIN_CRENEAU_HOURS <= duration <= MAX_CRENEAU_HOURS):
            return ValidationError("creneau_duree_invalide")

        creneaux.append(
            Creneau(
                date=creneau_date,
                debut=debut,
                fin=fin,
                lieu=raw_creneau.get("lieu") or "",
                duration_hours=duration,
            )
        )

    # Repris "au mieux" : une case ambiguë est déjà une donnée incertaine, une
    # entrée mal formée ou hors période ne doit pas faire échouer tout le
    # reste (contrairement à un créneau, dont une erreur est plus grave).
    creneau_dates = {creneau.date for creneau in creneaux}
    jours_incertains: list[JourIncertain] = []
    for raw_jour in raw.get("jours_incertains") or []:
        try:
            jour_date = date.fromisoformat(raw_jour["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if periode_debut <= jour_date <= periode_fin and jour_date not in creneau_dates:
            jours_incertains.append(
                JourIncertain(date=jour_date, texte=str(raw_jour.get("texte") or ""))
            )

    return ValidatedExtraction(
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        creneaux=creneaux,
        jours_incertains=jours_incertains,
    )
