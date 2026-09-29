"""Tests de validate.py (plan, sections 5D et 13)."""

from __future__ import annotations

from datetime import date

from app.validate import ValidatedExtraction, ValidationError, _shift_years, validate

TODAY = date(2026, 9, 14)


def _raw(periode_debut, periode_fin, creneaux, jours_incertains=None):
    raw = {"periode": {"debut": periode_debut, "fin": periode_fin}, "creneaux": creneaux}
    if jours_incertains is not None:
        raw["jours_incertains"] = jours_incertains
    return raw


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


def test_validate_accepts_jour_incertain():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [],
        jours_incertains=[{"date": "2026-09-16", "texte": "ASTR"}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert len(result.jours_incertains) == 1
    assert result.jours_incertains[0].date == date(2026, 9, 16)
    assert result.jours_incertains[0].texte == "ASTR"


def test_validate_missing_jours_incertains_key_is_empty_list():
    """Clé absente de la réponse du modèle : pas une erreur, juste aucun jour
    ambigu (comme "creneaux" absent)."""
    raw = _raw("2026-09-14", "2026-09-20", [])

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert result.jours_incertains == []


def test_validate_drops_jour_incertain_outside_periode():
    """Repris au mieux : une entrée hors période ne fait pas échouer le reste
    de l'extraction (contrairement à un créneau hors période)."""
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [],
        jours_incertains=[{"date": "2026-10-01", "texte": "ASTR"}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert result.jours_incertains == []


def test_validate_drops_malformed_jour_incertain():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [],
        jours_incertains=[{"date": "pas-une-date", "texte": "ASTR"}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert result.jours_incertains == []


def test_validate_drops_jour_incertain_on_same_date_as_creneau():
    """Le modèle ne devrait jamais renvoyer les deux pour le même jour (le
    prompt le lui interdit), mais si ça arrive, le créneau concret l'emporte
    sur le jour ambigu plutôt que d'écrire les deux dans l'agenda."""
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-16", "debut": "09:00", "fin": "17:00", "lieu": ""}],
        jours_incertains=[{"date": "2026-09-16", "texte": "ASTR"}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert len(result.creneaux) == 1
    assert result.jours_incertains == []


def test_validate_normalizes_hours_written_like_on_the_planning():
    """Le modèle recopie parfois l'horaire tel qu'écrit (« 9h », « 8h30 ») au
    lieu de HH:MM : on normalise plutôt que de rejeter tout le planning."""
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [
            {"date": "2026-09-14", "debut": "9h", "fin": "17h30", "lieu": ""},
            {"date": "2026-09-15", "debut": "8.30", "fin": "16:00", "lieu": ""},
            {"date": "2026-09-16", "debut": "16:00", "fin": "24:00", "lieu": ""},
        ],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert [(c.debut, c.fin) for c in result.creneaux] == [
        ("09:00", "17:30"),
        ("08:30", "16:00"),
        ("16:00", "00:00"),
    ]
    assert result.creneaux[2].duration_hours == 8


def test_validate_rejects_a_range_given_as_start_hour():
    raw = _raw(
        "2026-09-14",
        "2026-09-20",
        [{"date": "2026-09-14", "debut": "9h-17h", "fin": "17:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "creneau_invalide"


def test_validate_accepts_french_dates():
    raw = _raw(
        "14/09/2026",
        "20/09/2026",
        [{"date": "15/09/2026", "debut": "09:00", "fin": "17:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidatedExtraction)
    assert result.periode_debut == date(2026, 9, 14)
    assert result.creneaux[0].date == date(2026, 9, 15)


def test_validate_accepts_a_monthly_planning_that_started_weeks_ago():
    """Planning de tout le mois déposé en fin de mois : il commence plus de
    deux semaines avant aujourd'hui, mais recoupe bien la fenêtre."""
    raw = _raw(
        "2026-09-01",
        "2026-09-30",
        [{"date": "2026-09-02", "debut": "09:00", "fin": "17:00", "lieu": ""}],
    )

    result = validate(raw, validation_weeks=8, today=date(2026, 9, 28))

    assert isinstance(result, ValidatedExtraction)


def test_validate_rejects_a_period_entirely_before_the_window():
    """Année écrite sur le planning mais hors fenêtre : aucun recoupement,
    rejet (on ne corrige que les années devinées, voir plus bas)."""
    raw = _raw("2025-09-14", "2025-09-20", [])
    raw["annee_visible"] = True

    result = validate(raw, validation_weeks=8, today=TODAY)

    assert isinstance(result, ValidationError)
    assert result.reason == "periode_hors_fenetre"


def test_validate_fixes_a_guessed_year_when_the_year_is_not_written():
    """Retour utilisateur : « période trop loin dans le passé » pour la semaine
    suivante. L'en-tête écrivait « Lun 05/10 » sans année, le modèle a deviné
    2025 : toutes les dates passent en 2026, et la prévisualisation le dit."""
    raw = _raw(
        "2025-10-05",
        "2025-10-11",
        [{"date": "2025-10-05", "debut": "09:00", "fin": "17:00", "lieu": ""}],
        jours_incertains=[{"date": "2025-10-06", "texte": "ASTR"}],
    )
    raw["annee_visible"] = False

    result = validate(raw, validation_weeks=8, today=date(2026, 9, 29))

    assert isinstance(result, ValidatedExtraction)
    assert (result.periode_debut, result.periode_fin) == (date(2026, 10, 5), date(2026, 10, 11))
    assert [c.date for c in result.creneaux] == [date(2026, 10, 5)]
    assert [j.date for j in result.jours_incertains] == [date(2026, 10, 6)]
    assert result.annee_corrigee == 2026


def test_validate_fixes_a_guessed_year_when_the_model_does_not_say():
    """Sans `annee_visible` (réponse d'un modèle qui l'oublie), l'année est
    traitée comme devinée."""
    raw = _raw("2025-10-05", "2025-10-11", [])

    result = validate(raw, validation_weeks=8, today=date(2026, 9, 29))

    assert isinstance(result, ValidatedExtraction)
    assert result.periode_debut == date(2026, 10, 5)


def test_validate_does_not_flag_a_period_already_in_the_window():
    raw = _raw("2026-10-05", "2026-10-11", [])
    raw["annee_visible"] = False

    result = validate(raw, validation_weeks=8, today=date(2026, 9, 29))

    assert isinstance(result, ValidatedExtraction)
    assert result.annee_corrigee is None


def test_validate_rejects_a_period_that_no_year_can_bring_into_the_window():
    """Mois mal lu (mars au lieu d'octobre) : aucune année ne convient."""
    raw = _raw("2026-03-05", "2026-03-11", [])

    result = validate(raw, validation_weeks=8, today=date(2026, 9, 29))

    assert isinstance(result, ValidationError)
    assert result.reason == "periode_hors_fenetre"


def test_shift_years_keeps_the_29th_of_february_valid():
    assert _shift_years(date(2028, 2, 29), -1) == date(2027, 2, 28)
