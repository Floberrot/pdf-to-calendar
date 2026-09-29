"""Tests de preview.py : créneaux présentés semaine par semaine (retour
utilisateur : sur un gros import, la liste des créneaux était illisible)."""

from __future__ import annotations

from datetime import date, timedelta

from app.preview import format_duree, resume, semaines
from app.validate import Creneau, JourIncertain, ValidatedExtraction


def _creneau(day: date, debut: str, fin: str, heures: float) -> Creneau:
    return Creneau(date=day, debut=debut, fin=fin, lieu="", duration_hours=heures)


def _octobre() -> ValidatedExtraction:
    """Tout octobre 2026 (du jeudi 1er au samedi 31) : du lundi au vendredi,
    6h-14h ; une coupure le 7 ; un code sans légende le 8."""
    creneaux = []
    day = date(2026, 10, 1)
    while day <= date(2026, 10, 31):
        if day.weekday() < 5 and day != date(2026, 10, 8):
            creneaux.append(_creneau(day, "06:00", "14:00", 8))
        day += timedelta(days=1)
    creneaux.append(_creneau(date(2026, 10, 7), "16:00", "18:30", 2.5))
    return ValidatedExtraction(
        periode_debut=date(2026, 10, 1),
        periode_fin=date(2026, 10, 31),
        creneaux=creneaux,
        jours_incertains=[JourIncertain(date=date(2026, 10, 8), texte="ASTR")],
    )


def test_semaines_start_on_monday_and_cover_the_whole_period():
    weeks = semaines(_octobre())

    assert len(weeks) == 5
    assert weeks[0].jours[0].date == date(2026, 9, 28)
    assert weeks[-1].jours[-1].date == date(2026, 11, 1)
    assert all(len(week.jours) == 7 for week in weeks)


def test_days_outside_the_period_are_flagged():
    first_week = semaines(_octobre())[0]

    assert [j.dans_periode for j in first_week.jours] == [False] * 3 + [True] * 4


def test_day_labels_show_the_month_only_where_it_changes():
    weeks = semaines(_octobre())

    assert weeks[0].jours[3].libelle == "1 oct."
    assert weeks[0].jours[4].libelle == "2"
    assert weeks[-1].jours[-1].libelle == "1 nov."


def test_a_week_starting_mid_month_names_its_first_day():
    week = ValidatedExtraction(
        periode_debut=date(2026, 10, 5),
        periode_fin=date(2026, 10, 11),
        creneaux=[],
        jours_incertains=[],
    )

    labels = [j.libelle for j in semaines(week)[0].jours]

    assert labels == ["5 oct.", "6", "7", "8", "9", "10", "11"]


def test_split_shift_and_uncertain_day_land_on_their_day():
    second_week = semaines(_octobre())[1]
    mercredi, jeudi = second_week.jours[2], second_week.jours[3]

    assert [(c.debut, c.fin) for c in mercredi.creneaux] == [("06:00", "14:00"), ("16:00", "18:30")]
    assert jeudi.creneaux == ()
    assert jeudi.incertain == "ASTR"


def test_week_totals_add_up_each_week():
    weeks = semaines(_octobre())

    assert weeks[0].heures == 16  # jeudi 1er et vendredi 2
    assert weeks[1].heures == 4 * 8 + 2.5  # le jeudi 8 est à vérifier


def test_resume_counts_slots_hours_and_days():
    summary = resume(_octobre())

    assert summary.creneaux == 22
    assert summary.heures == 21 * 8 + 2.5
    assert summary.jours_travailles == 21
    assert summary.jours_incertains == 1


def test_format_duree_reads_like_a_person_would_write_it():
    assert format_duree(8.0) == "8 h"
    assert format_duree(7.5) == "7 h 30"
    assert format_duree(10.75) == "10 h 45"
    assert format_duree(170.5) == "170 h 30"
