"""Tests de account.py : corriger le nom recherché dans le planning, retour
utilisateur (la détection automatique depuis le prénom/nom Google n'est pas
toujours fiable)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.account import SearchName, search_name_for
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


def test_account_prefills_google_name_when_nothing_saved(client):
    """Demande utilisateur : à la connexion Google, on remplit — la personne
    n'a plus qu'à confirmer d'un clic."""
    _override_user("ami-account-nouveau@example.com")

    response = client.get("/account")

    assert response.status_code == 200
    assert 'value="Ami Profil"' in response.text
    assert "repris de ton compte" in response.text
    assert "C'est bien moi" in response.text


def test_account_welcome_banner_after_login(client):
    _override_user("ami-account-bienvenue@example.com")

    response = client.get("/account", params={"bienvenue": "1"})

    assert "Bienvenue" in response.text
    assert "Plus tard" in response.text


def test_account_without_google_family_name_explains_nothing_to_search(client):
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami-account-sans-nom@example.com", name="Ami", is_admin=False, given_name="Ami"
    )

    response = client.get("/account")

    assert "ne donne pas de nom de famille" in response.text
    assert 'value="Ami"' in response.text


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
    assert "repris de ton compte" in response.text
    assert not get_pdf_name(email)


def test_account_saved_links_to_upload(client):
    email = "ami-account-lien-depot@example.com"
    upsert_user_seen(email)
    _override_user(email)

    response = client.post("/account", data={"pdf_name": "Ami PROFIL"})

    assert 'href="/upload"' in response.text
    assert "(enregistré)" in response.text


def test_account_save_works_for_a_session_without_database_row(client):
    """Retour utilisateur : « jamais reconnu même quand on save ». Session
    valide sans ligne en base (redéploiement sur une base neuve) : le nom
    doit quand même être enregistré, et la page le dire avec le bon nom."""
    email = "ami-account-sans-ligne@example.com"
    _override_user(email)

    response = client.post("/account", data={"pdf_name": "MARTIN Sophie"})

    assert get_pdf_name(email) == "MARTIN Sophie"
    assert "Enregistré : l'appli cherchera « MARTIN Sophie »" in response.text
    assert "(enregistré)" in response.text
    assert "repris de ton compte" not in response.text


def test_account_clearing_the_name_says_so(client):
    email = "ami-account-effacement@example.com"
    set_pdf_name(email, "DUPONT J.")
    _override_user(email)

    response = client.post("/account", data={"pdf_name": "  "})

    assert get_pdf_name(email) is None
    assert "Nom effacé" in response.text


def test_search_name_prefers_saved_name_over_google():
    email = "ami-search-name@example.com"
    upsert_user_seen(email)
    user = CurrentUser(email=email, name="Ami", is_admin=False, given_name="Ami", family_name="G")
    assert search_name_for(user) == SearchName("Ami G", "google")

    set_pdf_name(email, "PROFIL A.")
    assert search_name_for(user) == SearchName("PROFIL A.", "profil")


def test_search_name_without_family_name_is_none():
    user = CurrentUser(
        email="ami-search-aucun@example.com", name="Ami", is_admin=False, given_name="Ami"
    )
    assert search_name_for(user) == SearchName(None, "aucun")
