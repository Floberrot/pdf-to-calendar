"""Validation locale du JSON renvoyé par le modèle (plan, section 5D).

Pure et indépendante des settings : `validation_weeks` est un paramètre, pas
une lecture globale, pour rester testable sans configuration.
"""

from __future__ import annotations

import re
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
    # Année retenue quand celle devinée par le modèle a été corrigée
    # (`_year_shift`), None sinon : à signaler sur la prévisualisation.
    annee_corrigee: int | None = None


@dataclass(frozen=True)
class ValidationError:
    reason: ValidationReason


_HEURE = re.compile(r"(\d{1,2})\s*(?:[h:.]\s*(\d{2})?)?")
_DATE_FR = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _normalize_heure(value: object) -> str:
    """« 9h », « 9h30 », « 09:00 », « 9.30 », « 9 » → « HH:MM ». Le prompt
    demande déjà HH:MM, mais le modèle recopie parfois l'horaire tel qu'écrit
    sur le planning : inutile de rejeter tout le planning pour ça. « 24:00 »
    (fin de journée) devient « 00:00 », le lendemain (voir la durée)."""
    match = _HEURE.fullmatch(str(value).strip().lower())
    if not match:
        raise ValueError(f"heure invalide : {value!r}")
    hours, minutes = int(match.group(1)), int(match.group(2) or 0)
    if hours == 24 and minutes == 0:
        hours = 0
    if hours > 23 or minutes > 59:
        raise ValueError(f"heure invalide : {value!r}")
    return f"{hours:02d}:{minutes:02d}"


def _parse_date(value: object) -> date:
    """AAAA-MM-JJ (demandé au modèle), avec une heure éventuelle derrière, ou
    JJ/MM/AAAA."""
    text = str(value).strip()
    match = _DATE_FR.fullmatch(text)
    if match:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    return date.fromisoformat(text[:10])


def _shift_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 29 février d'une année bissextile
        return value.replace(year=value.year + years, day=28)


def _year_shift(debut: date, fin: date, window_start: date, window_end: date) -> int:
    """Nombre d'années à ajouter pour que la période recoupe la fenêtre, 0 si
    aucun décalage d'un ou deux ans n'y suffit."""
    for years in (1, -1, 2, -2):
        if _shift_years(fin, years) >= window_start and _shift_years(debut, years) <= window_end:
            return years
    return 0


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
        periode_debut = _parse_date(raw["periode"]["debut"])
        periode_fin = _parse_date(raw["periode"]["fin"])
    except (KeyError, ValueError, TypeError):
        return ValidationError("periode_invalide")

    if periode_fin < periode_debut:
        return ValidationError("periode_invalide")

    if (periode_fin - periode_debut).days > MAX_PERIODE_WEEKS * 7:
        return ValidationError("periode_trop_longue")

    # La fenêtre attrape une année ou un mois mal lus (période sans aucun
    # rapport avec aujourd'hui). Il suffit que la période la recoupe : un
    # planning du mois entier, déposé en fin de mois, commence plus de deux
    # semaines avant aujourd'hui sans être faux pour autant.
    window_start = today - timedelta(weeks=2)
    window_end = today + timedelta(weeks=validation_weeks)
    year_shift = 0
    if periode_fin < window_start or periode_debut > window_end:
        # Retour utilisateur : « période trop loin dans le passé » pour la
        # semaine suivante. L'en-tête écrivait « Lun 05/10 », sans année, et le
        # modèle en a deviné une mauvaise. Quand l'année n'est pas écrite, celle
        # qui place la période dans la fenêtre est la bonne ; une année écrite
        # hors fenêtre reste, elle, rejetée.
        if raw.get("annee_visible") is not True:
            year_shift = _year_shift(periode_debut, periode_fin, window_start, window_end)
        if not year_shift:
            return ValidationError("periode_hors_fenetre")
        periode_debut = _shift_years(periode_debut, year_shift)
        periode_fin = _shift_years(periode_fin, year_shift)

    creneaux: list[Creneau] = []
    for raw_creneau in raw.get("creneaux") or []:
        try:
            creneau_date = _shift_years(_parse_date(raw_creneau["date"]), year_shift)
            debut = _normalize_heure(raw_creneau["debut"])
            fin = _normalize_heure(raw_creneau["fin"])
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
            jour_date = _shift_years(_parse_date(raw_jour["date"]), year_shift)
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
        annee_corrigee=periode_debut.year if year_shift else None,
    )
