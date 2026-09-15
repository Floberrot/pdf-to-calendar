"""Tests de account.py : corriger le nom recherché dans le planning, retour
utilisateur (la détection automatique depuis le prénom/nom Google n'est pas
toujours fiable)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, require_user
from app.db import get_pdf_name, set_pdf_name, upsert_user_seen
from app.main import app


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _override_user(email: str) -> None:
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email=email, name="Ami Profil", is_admin=False, given_name="Ami", family_name="Profil"
    )


def test_account_requires_login(client):
    response = client.get("/account", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_account_shows_nothing_saved_by_default(client):
    _override_user("ami-account-nouveau@example.com")

    response = client.get("/account")

    assert response.status_code == 200
    assert "Rien d'enregistré" in response.text


def test_account_shows_currently_saved_name(client):
    email = "ami-account-existant@example.com"
    upsert_user_seen(email)
    set_pdf_name(email, "DUPONT J.")
    _override_user(email)

    response = client.get("/account")

    assert response.status_code == 200
    assert "DUPONT J." in response.text


def test_account_save_persists_name(client):
    email = "ami-account-sauvegarde@example.com"
    upsert_user_seen(email)
    _override_user(email)

    response = client.post("/account", data={"pdf_name": "MARTIN Sophie"})

    assert response.status_code == 200
    assert "Enregistré" in response.text
    assert get_pdf_name(email) == "MARTIN Sophie"


def test_account_save_strips_whitespace(client):
    email = "ami-account-espaces@example.com"
    upsert_user_seen(email)
    _override_user(email)

    client.post("/account", data={"pdf_name": "  DUPONT J.  "})

    assert get_pdf_name(email) == "DUPONT J."


def test_account_save_empty_resets_to_automatic(client):
    email = "ami-account-reset@example.com"
    upsert_user_seen(email)
    set_pdf_name(email, "DUPONT J.")
    _override_user(email)

    response = client.post("/account", data={"pdf_name": ""})

    assert response.status_code == 200
    assert "Rien d'enregistré" in response.text
    assert not get_pdf_name(email)
