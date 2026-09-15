"""Tests de admin.py : accès admin, tests de santé factices, journal (plan, section 13)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.admin import get_calendar_health_check, get_model_health_check
from app.auth import CurrentUser, require_admin, require_user
from app.log import log
from app.main import app


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _override_admin() -> None:
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="admin@example.com", name="Admin", is_admin=True
    )


def _noop() -> None:
    return None


def test_admin_anonymous_redirects_to_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_admin_forbidden_for_non_admin_user(client):
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami@example.com", name="Ami", is_admin=False
    )
    response = client.get("/admin")
    assert response.status_code == 403


def test_admin_ok_for_admin_user(client):
    _override_admin()
    response = client.get("/admin")
    assert response.status_code == 200


def test_admin_ok_for_admin_user_via_require_admin_override(client):
    app.dependency_overrides[require_admin] = lambda: CurrentUser(
        email="admin@example.com", name="Admin", is_admin=True
    )
    response = client.get("/admin")
    assert response.status_code == 200


def test_admin_shows_recent_import(client):
    _override_admin()
    log(
        request_id="req-admin-journal-1",
        email="journal-test-1@example.com",
        step="llm",
        status="error",
        detail={"erreur": "motif precis du test"},
    )

    response = client.get("/admin")

    assert response.status_code == 200
    assert "journal-test-1@example.com" in response.text
    assert "llm" in response.text


def test_admin_email_filter_only_shows_matching_rows(client):
    _override_admin()
    log(request_id="req-filter-a", email="journal-filter-a@example.com", step="upload", status="ok")
    log(request_id="req-filter-b", email="journal-filter-b@example.com", step="upload", status="ok")

    response = client.get("/admin", params={"email": "journal-filter-a@example.com"})

    assert response.status_code == 200
    assert "journal-filter-a@example.com" in response.text
    assert "journal-filter-b@example.com" not in response.text


def test_health_calendar_success_shows_ok(client):
    _override_admin()
    app.dependency_overrides[get_calendar_health_check] = lambda: _noop

    response = client.post("/admin/health/calendar")

    assert response.status_code == 200
    assert "OK" in response.text


def test_health_calendar_failure_shows_error(client):
    _override_admin()

    def _failing_check() -> None:
        raise RuntimeError("agenda hors service")

    app.dependency_overrides[get_calendar_health_check] = lambda: _failing_check

    response = client.post("/admin/health/calendar")

    assert response.status_code == 200
    assert "agenda hors service" in response.text


def test_health_model_success_shows_ok(client):
    _override_admin()
    app.dependency_overrides[get_model_health_check] = lambda: _noop

    response = client.post("/admin/health/model")

    assert response.status_code == 200
    assert "OK" in response.text


def test_health_model_failure_shows_error(client):
    _override_admin()

    def _failing_check() -> None:
        raise RuntimeError("modele hors service")

    app.dependency_overrides[get_model_health_check] = lambda: _failing_check

    response = client.post("/admin/health/model")

    assert response.status_code == 200
    assert "modele hors service" in response.text


def test_health_calendar_forbidden_for_non_admin(client):
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami@example.com", name="Ami", is_admin=False
    )

    response = client.post("/admin/health/calendar")

    assert response.status_code == 403
