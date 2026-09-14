from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import CurrentUser, require_admin, require_user
from app.main import app

client = TestClient(app)


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_anonymous_redirects_to_login():
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_admin_anonymous_redirects_to_login():
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_admin_forbidden_for_non_admin_user():
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami@example.com", name="Ami", is_admin=False
    )
    try:
        response = client.get("/admin")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_ok_for_admin_user():
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="admin@example.com", name="Admin", is_admin=True
    )
    try:
        response = client.get("/admin")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_index_ok_for_authenticated_user():
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami@example.com", name="Ami", is_admin=False
    )
    try:
        response = client.get("/")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_index_ok_for_admin_user_via_require_admin_override():
    app.dependency_overrides[require_admin] = lambda: CurrentUser(
        email="admin@example.com", name="Admin", is_admin=True
    )
    try:
        response = client.get("/admin")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
