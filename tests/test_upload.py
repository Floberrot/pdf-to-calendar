from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.auth import CurrentUser, require_user
from app.calendar_sync import ExistingEvent
from app.llm import ExtractError
from app.main import app
from app.upload import ERROR_MESSAGES, get_calendar_lister, get_extractor

PAGE_SIZE = landscape(A4)


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _fake_extraction_payload() -> dict:
    """Dates relatives à aujourd'hui, pour rester dans la fenêtre de
    validation quel que soit le jour où la CI s'exécute."""
    today = date.today()
    creneau_date = today + timedelta(days=1)
    periode_fin = today + timedelta(days=6)
    return {
        "periode": {"debut": today.isoformat(), "fin": periode_fin.isoformat()},
        "creneaux": [
            {"date": creneau_date.isoformat(), "debut": "09:00", "fin": "17:00", "lieu": "Site B"}
        ],
    }


def _default_extractor(image_png: bytes) -> dict:
    return _fake_extraction_payload()


def _default_calendar_lister(email, periode_debut, periode_fin) -> list[ExistingEvent]:
    return []


def _override_user(*, given_name: str = "Sophie", family_name: str = "MARTIN") -> None:
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email="ami@example.com",
        name="Ami",
        is_admin=False,
        given_name=given_name,
        family_name=family_name,
    )
    app.dependency_overrides[get_extractor] = lambda: _default_extractor
    app.dependency_overrides[get_calendar_lister] = lambda: _default_calendar_lister


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


def test_upload_pdf_success_shows_creneaux_table(client, tmp_path):
    """Plan section 5E : les créneaux détectés en tableau sur la prévisualisation."""
    _override_user(given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "09:00" in response.text
    assert "17:00" in response.text
    assert "Site B" in response.text


def test_upload_pdf_shows_existing_events_to_be_replaced(client, tmp_path):
    """Plan section 5E : les anciens créneaux qui seront remplacés."""

    def _lister_with_one_event(email, periode_debut, periode_fin):
        return [
            ExistingEvent(
                id="evt1",
                summary="Sophie Martin — 8h-16h",
                start="2026-09-15T08:00:00+02:00",
                end="2026-09-15T16:00:00+02:00",
            )
        ]

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_lister] = lambda: _lister_with_one_event
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "Sophie Martin — 8h-16h" in response.text


def test_upload_pdf_extraction_failure_shows_error_message(client, tmp_path):
    def _broken_extractor(image_png: bytes) -> dict:
        raise ExtractError("panne simulée")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _broken_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert ERROR_MESSAGES["extraction_echouee"] in response.text


def test_upload_pdf_validation_failure_shows_error_message(client, tmp_path):
    def _bad_periode_extractor(image_png: bytes) -> dict:
        today = date.today()
        return {
            "periode": {
                "debut": today.isoformat(),
                "fin": (today + timedelta(weeks=10)).isoformat(),
            },
            "creneaux": [],
        }

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _bad_periode_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert ERROR_MESSAGES["periode_trop_longue"] in response.text


def test_upload_pdf_calendar_lookup_failure_falls_back_gracefully(client, tmp_path):
    def _broken_lister(email, periode_debut, periode_fin):
        raise RuntimeError("agenda indisponible")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_lister] = lambda: _broken_lister
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "Impossible de vérifier les anciens événements" in response.text
    assert "09:00" in response.text


def test_change_searched_name_form_available_after_success(client, tmp_path):
    """Plan section 5E : bouton "Changer le nom recherché" sur la
    prévisualisation, même quand l'automatique a réussi."""
    _override_user(given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)
    with open(pdf_path, "rb") as f:
        upload_response = client.post(
            "/upload", files={"file": ("planning.pdf", f, "application/pdf")}
        )
    assert "Changer le nom recherché" in upload_response.text

    # upload_id n'est pas dans l'URL (pas de redirection sur un succès) ;
    # on le lit dans le lien "Changer le nom recherché" du résultat.
    match = re.search(r"/upload/([a-f0-9]+)/name", upload_response.text)
    assert match is not None
    upload_id = match.group(1)

    name_form_response = client.get(f"/upload/{upload_id}/name")
    assert name_form_response.status_code == 200
    assert "Changer le nom recherché" in name_form_response.text


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
