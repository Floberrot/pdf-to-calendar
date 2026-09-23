from __future__ import annotations

import pytest

from app.settings import load_settings

BASE_ENV = {
    "GOOGLE_CLIENT_ID": "id",
    "GOOGLE_CLIENT_SECRET": "secret",
    "SESSION_SECRET": "session",
    "ALLOWED_EMAILS": "a@b.com, C@D.com",
    "ADMIN_EMAILS": "admin@b.com",
    "CALENDAR_ID": "cal@group.calendar.google.com",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
    "LLM_API_KEY": "key",
    "LLM_MODEL": "model",
}


def test_load_settings_ok():
    settings = load_settings(dict(BASE_ENV))
    assert settings.allowed_emails == frozenset({"a@b.com", "c@d.com"})
    assert settings.admin_emails == frozenset({"admin@b.com"})
    assert settings.tz == "Europe/Paris"
    assert settings.validation_weeks == 8
    assert settings.data_dir == "./data"
    assert settings.llm_models == ("model",)


def test_load_settings_llm_model_accepts_a_comma_separated_chain():
    """Plusieurs modèles, du préféré au repli (llm.py les essaie dans l'ordre)."""
    env = dict(BASE_ENV, LLM_MODEL=" gemini-a, gemini-b ,gemini-c ")
    settings = load_settings(env)
    assert settings.llm_models == ("gemini-a", "gemini-b", "gemini-c")


def test_load_settings_empty_llm_model_raises():
    with pytest.raises(RuntimeError):
        load_settings(dict(BASE_ENV, LLM_MODEL=" , "))


def test_load_settings_missing_var_raises():
    with pytest.raises(RuntimeError):
        load_settings({"GOOGLE_CLIENT_ID": "id"})


def test_load_settings_empty_email_lists_allowed():
    env = dict(BASE_ENV, ALLOWED_EMAILS="", ADMIN_EMAILS="")
    settings = load_settings(env)
    assert settings.allowed_emails == frozenset()
    assert settings.admin_emails == frozenset()


def test_load_settings_invalid_validation_weeks_raises():
    env = dict(BASE_ENV, VALIDATION_WEEKS="pas-un-nombre")
    with pytest.raises(RuntimeError):
        load_settings(env)


def test_load_settings_custom_overrides():
    env = dict(BASE_ENV, TZ="UTC", VALIDATION_WEEKS="4", DATA_DIR="/data")
    settings = load_settings(env)
    assert settings.tz == "UTC"
    assert settings.validation_weeks == 4
    assert settings.data_dir == "/data"
