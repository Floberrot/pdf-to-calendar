"""Tests de validate.py (plan, sections 5D et 13)."""

from __future__ import annotations

from datetime import date

from app.validate import ValidatedExtraction, ValidationError, validate

TODAY = date(2026, 9, 14)


def _raw(periode_debut, periode_fin, creneaux):
    return {"periode": {"debut": periode_debut, "fin": periode_fin}, "creneaux": creneaux}


def test_validate_accepts_simple_creneau():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-15", "debut": "09:00", "fin": "17:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert len(result.creneaux) == 1
    assert result.creneaux[0].duration_hours == 8


def test_validate_accepts_overnight_shift_as_ten_hours():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-15", "debut": "21:00", "fin": "07:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert result.creneaux[0].duration_hours == 10


def test_validate_accepts_split_shifts_same_day():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [
            {"date": "2026-09-15", "debut": "09:00", "fin": "13:00", "lieu": ""},
            {"date": "2026-09-15", "debut": "15:00", "fin": "19:00", "lieu": ""},
        ],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert len(result.creneaux) == 2


def test_validate_rejects_periode_too_long():
    raw = _raw("2026-09-14", "2026-11-14", [])

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "periode_trop_longue"


def test_validate_rejects_creneau_outside_periode():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-25", "debut": "09:00", "fin": "17:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "creneau_hors_periode"


def test_validate_rejects_periode_outside_window():
    raw = _raw("2027-01-01", "2027-01-07", [])

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "periode_hors_fenetre"


def test_validate_rejects_creneau_too_short():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-15", "debut": "09:00", "fin": "09:30", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "creneau_duree_invalide"


def test_validate_rejects_creneau_too_long():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-15", "debut": "06:00", "fin": "22:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "creneau_duree_invalide"


def test_validate_accepts_rest_day_within_periode_with_no_creneau():
    raw = _raw("2026-09-14", "2026-09-20", [])

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert result.creneaux == []
