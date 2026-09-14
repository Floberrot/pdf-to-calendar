from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.auth import CurrentUser, require_user
from app.main import app

PAGE_SIZE = landscape(A4)


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _override_user(*, given_name: str = "Sophie", family_name: str = "MARTIN") -> None:
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami@example.com",
        name="Ami",
        is_admin=False,
        given_name=given_name,
        family_name=family_name,
    )


def _build_planning_pdf(path) -> None:
    c = canvas.Canvas(str(path), pagesize=PAGE_SIZE)
    top = PAGE_SIZE[1] - 60
    for i, date_text in enumerate(["Lun 15", "Mar 16", "Mer 17", "Jeu 18", "Ven 19"]):
        c.drawString(150 + i * 90, top, date_text)
    y = top - 15
    c.line(50, y, PAGE_SIZE[0] - 50, y)
    for name in ["DUPONT Jean", "MARTIN Sophie"]:
        y -= 30
        c.drawString(50, y + 12, name)
        c.line(50, y, PAGE_SIZE[0] - 50, y)
    c.showPage()
    c.save()


def test_upload_form_requires_login(client):
    response = client.get("/upload", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_upload_pdf_success_shows_composed_result(client, tmp_path):
    _override_user(given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "MARTIN Sophie" in response.text


def test_upload_pdf_name_not_found_shows_name_form(client, tmp_path):
    _override_user(given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "n'a pas été trouvé" in response.text


def test_upload_pdf_too_large_shows_error(client):
    _override_user()
    big_content = b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024)

    response = client.post("/upload", files={"file": ("big.pdf", big_content, "application/pdf")})

    assert response.status_code == 200
    assert "10 Mo" in response.text


def test_manual_crop_unknown_upload_id_returns_404(client):
    _override_user()
    response = client.get("/upload/does-not-exist/manual")
    assert response.status_code == 404
