from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app import upload as upload_module
from app.auth import CurrentUser, require_user
from app.calendar_sync import ExistingEvent, SyncError, SyncResult
from app.db import get_pdf_name, set_pdf_name, transaction, upsert_user_seen
from app.llm import ExtractError, RateLimitError
from app.main import app
from app.upload import ERROR_MESSAGES, get_calendar_lister, get_calendar_syncer, get_extractor

PAGE_SIZE = landscape(A4)


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _fake_extraction_payload() -> dict:
    """Dates relatives à aujourd'hui, pour rester dans la fenêtre de
    validation quel que soit le jour où la CI s'exécute."""
    today = datetime.now(UTC).date()
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


def _default_calendar_syncer(email, name, extraction) -> SyncResult:
    return SyncResult(
        inserted_count=len(extraction.creneaux),
        replaced_count=0,
        uncertain_count=len(extraction.jours_incertains),
    )


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
    app.dependency_overrides[get_calendar_syncer] = lambda: _default_calendar_syncer


def _upload_id_from(response_text: str, *, suffix: str) -> str:
    match = re.search(rf"/upload/([a-f0-9]+)/{suffix}", response_text)
    assert match is not None, f"pas de lien .../{suffix} dans la reponse"
    return match.group(1)


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


def test_upload_shows_jours_incertains_in_preview(client, tmp_path):
    """Retour utilisateur : une case non vide mais pas reconnue (ni horaire
    clair, ni absence habituelle) ne doit pas être perdue en silence."""

    def _extractor_with_jour_incertain(image_png: bytes) -> dict:
        payload = _fake_extraction_payload()
        creneau_date = date.fromisoformat(payload["creneaux"][0]["date"])
        jour_date = creneau_date + timedelta(days=1)
        payload["jours_incertains"] = [{"date": jour_date.isoformat(), "texte": "ASTR"}]
        return payload

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _extractor_with_jour_incertain
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "Jours à vérifier" in response.text
    assert "ASTR" in response.text


def test_upload_sends_redacted_image_to_model_but_shows_original(client, tmp_path):
    """Le nom ne doit jamais partir vers le modèle (voir _ai_notice.html),
    mais doit rester visible dans la prévisualisation montrée à la personne
    (vérification anti-décalage de ligne, plan section 10) : deux images
    distinctes."""
    captured: dict[str, bytes] = {}

    def _capturing_extractor(image_png: bytes) -> dict:
        captured["model_image"] = image_png
        return _fake_extraction_payload()

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _capturing_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})
    upload_id = _upload_id_from(response.text, suffix="name")

    shown_image = client.get(f"/upload/{upload_id}/image/composed.png").content

    assert "model_image" in captured
    assert captured["model_image"] != shown_image


def test_manual_crop_sends_unredacted_image_to_model(client, tmp_path):
    """Le recadrage manuel n'a pas de position de nom connue (rectangle
    tracé à la main) : pas de masquage possible sur cette voie, contrairement
    à la voie automatique (voir _ai_notice_manual.html)."""
    captured: dict[str, bytes] = {}

    def _capturing_extractor(image_png: bytes) -> dict:
        captured["model_image"] = image_png
        return _fake_extraction_payload()

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _capturing_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        upload_response = client.post(
            "/upload", files={"file": ("planning.pdf", f, "application/pdf")}
        )
    upload_id = _upload_id_from(upload_response.text, suffix="name")

    manual_response = client.post(
        f"/upload/{upload_id}/manual",
        data={"page": 1, "x": 0, "y": 0, "w": 200, "h": 100},
    )
    assert manual_response.status_code == 200

    shown_image = client.get(f"/upload/{upload_id}/image/composed.png").content
    assert captured["model_image"] == shown_image


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


def test_existing_event_date_formatted_not_raw_iso(client, tmp_path):
    """Les heures brutes de l'API (ISO) étaient illisibles (retour
    utilisateur) : le format affiché doit être lisible, pas la chaîne ISO."""

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
    assert "2026-09-15T08:00:00+02:00" not in response.text
    assert "15/09/2026" in response.text
    assert "8h-16h" in response.text


def test_existing_event_all_day_shows_journee_entiere(client, tmp_path):
    def _lister_all_day(email, periode_debut, periode_fin):
        return [ExistingEvent(id="evt1", summary="Repos", start="2026-09-16", end="2026-09-17")]

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_lister] = lambda: _lister_all_day
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "journée entière" in response.text


def test_existing_event_matching_new_creneau_shows_no_badge(client, tmp_path):
    """Mêmes horaires que le créneau importé : rien ne change pour cet
    événement, pas de badge (retour utilisateur : indiquer ce qui change)."""
    payload = _fake_extraction_payload()
    creneau = payload["creneaux"][0]

    def _lister_matching(email, periode_debut, periode_fin):
        return [
            ExistingEvent(
                id="evt1",
                summary="Sophie Martin — 9h-17h",
                start=f"{creneau['date']}T{creneau['debut']}:00+02:00",
                end=f"{creneau['date']}T{creneau['fin']}:00+02:00",
            )
        ]

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_lister] = lambda: _lister_matching
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "status-badge" not in response.text


def test_existing_event_different_hours_shows_modified_badge(client, tmp_path):
    """Même jour, horaires différents : la personne doit voir que ça change."""
    payload = _fake_extraction_payload()
    creneau = payload["creneaux"][0]

    def _lister_different_hours(email, periode_debut, periode_fin):
        return [
            ExistingEvent(
                id="evt1",
                summary="Sophie Martin — 8h-16h",
                start=f"{creneau['date']}T08:00:00+02:00",
                end=f"{creneau['date']}T16:00:00+02:00",
            )
        ]

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_lister] = lambda: _lister_different_hours
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "status-badge--modifie" in response.text


def test_existing_event_on_other_date_shows_removed_badge(client, tmp_path):
    """Aucun créneau ce jour-là parmi les nouveaux : l'événement va
    simplement disparaître, pas être remplacé par un autre horaire."""

    def _lister_other_date(email, periode_debut, periode_fin):
        return [
            ExistingEvent(
                id="evt1",
                summary="Sophie Martin — 9h-17h",
                start="2000-01-01T09:00:00+02:00",
                end="2000-01-01T17:00:00+02:00",
            )
        ]

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_lister] = lambda: _lister_other_date
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "status-badge--supprime" in response.text


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
    # Sous-chaine sans apostrophe : le message complet contient des apostrophes
    # que Jinja echappe en &#39; dans le HTML rendu.
    assert "Réessaie ou recadre à la main" in response.text


def test_upload_pdf_rate_limit_shows_distinct_message(client, tmp_path):
    """Un quota depasse n'est pas la meme situation qu'un echec quelconque :
    la personne doit comprendre qu'il faut attendre, pas recadrer a la main."""

    def _rate_limited_extractor(image_png: bytes) -> dict:
        raise RateLimitError("quota depasse simule")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _rate_limited_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    assert response.status_code == 200
    assert "Réessaie dans quelques minutes" in response.text


def test_upload_pdf_validation_failure_shows_error_message(client, tmp_path):
    def _bad_periode_extractor(image_png: bytes) -> dict:
        today = datetime.now(UTC).date()
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


def test_retry_with_unknown_name_shows_reason_instead_of_manual_crop(client, tmp_path):
    """Retour utilisateur (« ça me demande le recadrage ») : un nom tapé qui
    ne matchait pas renvoyait vers le recadrage manuel sans dire pourquoi.
    Le motif doit s'afficher, avec le nom tapé prérempli pour le corriger."""
    _override_user(given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)
    with open(pdf_path, "rb") as f:
        upload_response = client.post(
            "/upload", files={"file": ("planning.pdf", f, "application/pdf")}
        )
    upload_id = _upload_id_from(upload_response.text, suffix="name")

    response = client.post(f"/upload/{upload_id}/name", data={"pdf_name": "Zoé INCONNUE"})

    assert response.status_code == 200
    assert "« Zoé INCONNUE » n'a pas été trouvé" in response.text
    assert 'value="Zoé INCONNUE"' in response.text


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


def test_confirm_shows_valider_button_after_successful_preview(client, tmp_path):
    """Plan section 5E : bouton Valider disponible sur la prévisualisation."""
    _override_user(given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        response = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    upload_id = _upload_id_from(response.text, suffix="name")
    assert f'action="/upload/{upload_id}/confirm"' in response.text


def test_confirm_success_shows_summary_and_purges_upload(client, tmp_path):
    _override_user(given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        preview = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})
    upload_id = _upload_id_from(preview.text, suffix="name")

    response = client.post(f"/upload/{upload_id}/confirm")

    assert response.status_code == 200
    assert "C'est fait" in response.text
    assert "1 créneau" in response.text

    # Le dossier d'upload est purgé après une validation réussie.
    assert client.get(f"/upload/{upload_id}/manual").status_code == 404


def test_confirm_shows_uncertain_count_when_present(client, tmp_path):
    def _extractor_with_jour_incertain(image_png: bytes) -> dict:
        payload = _fake_extraction_payload()
        creneau_date = date.fromisoformat(payload["creneaux"][0]["date"])
        jour_date = creneau_date + timedelta(days=1)
        payload["jours_incertains"] = [{"date": jour_date.isoformat(), "texte": "ASTR"}]
        return payload

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _extractor_with_jour_incertain
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        preview = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})
    upload_id = _upload_id_from(preview.text, suffix="name")

    response = client.post(f"/upload/{upload_id}/confirm")

    assert response.status_code == 200
    assert "1 jour à vérifier" in response.text


def test_confirm_without_prior_preview_returns_404(client, tmp_path):
    """Rien à valider si la prévisualisation a échoué (pas d'extraction en cache)."""

    def _broken_extractor(image_png: bytes) -> dict:
        raise ExtractError("panne simulée")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _broken_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        preview = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})
    upload_id = _upload_id_from(preview.text, suffix="name")

    response = client.post(f"/upload/{upload_id}/confirm")

    assert response.status_code == 404


def test_confirm_unknown_upload_id_returns_404(client):
    _override_user()
    response = client.post("/upload/does-not-exist/confirm")
    assert response.status_code == 404


def test_confirm_sync_failure_shows_error_and_keeps_upload(client, tmp_path):
    def _failing_syncer(email, name, extraction):
        return SyncError("panne agenda simulée")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_syncer] = lambda: _failing_syncer
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        preview = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})
    upload_id = _upload_id_from(preview.text, suffix="name")

    response = client.post(f"/upload/{upload_id}/confirm")

    assert response.status_code == 200
    assert "panne agenda simulée" in response.text
    # Jamais d'ecriture partielle visible : la previsualisation reste
    # disponible pour reessayer, le dossier d'upload n'est pas purge.
    assert "09:00" in response.text
    assert client.get(f"/upload/{upload_id}/manual").status_code == 200


def test_upload_pdf_extraction_failure_is_logged(client, tmp_path):
    """La vraie cause d'un echec modele doit etre journalisee (visible via
    Railway et, plus tard, le journal admin) : sinon elle est indiagnosticable."""

    def _broken_extractor(image_png: bytes) -> dict:
        raise ExtractError("panne modele bien precise")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_extractor] = lambda: _broken_extractor
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})

    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM imports WHERE email = ? AND step = 'llm' AND status = 'error' "
            "ORDER BY ts DESC LIMIT 1",
            ("ami@example.com",),
        ).fetchone()
    assert row is not None
    assert "panne modele bien precise" in row["detail"]


def test_confirm_sync_failure_is_logged(client, tmp_path):
    def _failing_syncer(email, name, extraction):
        return SyncError("panne agenda bien precise")

    _override_user(given_name="Sophie", family_name="MARTIN")
    app.dependency_overrides[get_calendar_syncer] = lambda: _failing_syncer
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        preview = client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})
    upload_id = _upload_id_from(preview.text, suffix="name")

    client.post(f"/upload/{upload_id}/confirm")

    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM imports WHERE email = ? AND step = 'write' AND status = 'error' "
            "ORDER BY ts DESC LIMIT 1",
            ("ami@example.com",),
        ).fetchone()
    assert row is not None
    assert "panne agenda bien precise" in row["detail"]


def _override_known_user(email: str, *, given_name: str, family_name: str) -> None:
    """Utilisateur avec une ligne en base, comme après une vraie connexion."""
    upsert_user_seen(email)
    _override_user(given_name=given_name, family_name=family_name)
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email=email,
        name="Ami",
        is_admin=False,
        given_name=given_name,
        family_name=family_name,
    )


def _upload(client, pdf_path):
    with open(pdf_path, "rb") as f:
        return client.post("/upload", files={"file": ("planning.pdf", f, "application/pdf")})


def test_upload_form_shows_the_searched_name(client):
    """Demande utilisateur : le nom cherché, mis en avant là où on dépose."""
    _override_user(given_name="Sophie", family_name="MARTIN")

    response = client.get("/upload")

    assert "Sophie MARTIN" in response.text
    assert "repris de ton compte Google" in response.text


def test_name_not_found_says_what_was_searched_and_asks_for_first_name(client, tmp_path):
    """Demande utilisateur : on teste avec le nom Google, et si ça ne marche
    pas on demande le prénom."""
    _override_user(given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    response = _upload(client, pdf_path)

    assert "On a cherché « Inconnue PERSONNE » (ton compte Google)" in response.text
    assert "ton prénom seul suffit" in response.text
    assert 'placeholder="ex. Inconnue' in response.text


def test_retry_success_saves_the_typed_name_to_the_profile_and_says_so(client, tmp_path):
    email = "ami-upload-profil@example.com"
    _override_known_user(email, given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)
    upload_id = _upload_id_from(_upload(client, pdf_path).text, suffix="name")

    response = client.post(f"/upload/{upload_id}/name", data={"pdf_name": "MARTIN Sophie"})

    assert get_pdf_name(email) == "MARTIN Sophie"
    assert "« MARTIN Sophie » est enregistré dans" in response.text


def test_partial_name_match_warns_before_validation(client, tmp_path):
    """Repli mot par mot : seul « Sophie » est trouvé pour « Sophie INCONNUE ».
    Ça peut être la bonne ligne (nom de famille illisible)… ou un homonyme de
    prénom si la personne n'est pas sur ce planning : à faire vérifier."""
    _override_user(given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)
    upload_id = _upload_id_from(_upload(client, pdf_path).text, suffix="name")

    response = client.post(f"/upload/{upload_id}/name", data={"pdf_name": "Sophie INCONNUE"})

    assert "Seul « Sophie » a été trouvé" in response.text
    assert "vérifie sur l'image" in response.text


def test_exact_name_match_shows_no_partial_warning(client, tmp_path):
    _override_user(given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    response = _upload(client, pdf_path)

    assert "MARTIN Sophie" in response.text
    assert "a été trouvé sur le planning, pas" not in response.text


def test_upload_never_rewrites_a_name_already_saved(client, tmp_path):
    """Nom enregistré « Sophie MARTIN », planning « MARTIN Sophie » : trouvé
    via l'ordre inverse, mais le profil garde ce que la personne a tapé."""
    email = "ami-upload-garde-nom@example.com"
    _override_known_user(email, given_name="Sophie", family_name="MARTIN")
    set_pdf_name(email, "Sophie MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    response = _upload(client, pdf_path)

    assert "MARTIN Sophie" in response.text
    assert get_pdf_name(email) == "Sophie MARTIN"


def test_first_success_from_google_name_fills_the_profile(client, tmp_path):
    email = "ami-upload-premier-succes@example.com"
    _override_known_user(email, given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    _upload(client, pdf_path)

    assert get_pdf_name(email) == "MARTIN Sophie"


def _override_session_only_user(email: str, *, given_name: str, family_name: str) -> None:
    """Session valide mais aucune ligne en base : le cas réel derrière le
    retour « jamais reconnu même quand on save » (cookie de session qui
    survit à un redéploiement sur une base neuve)."""
    _override_user(given_name=given_name, family_name=family_name)
    app.dependency_overrides[require_user] = lambda: CurrentUser(
        email=email,
        name="Ami",
        is_admin=False,
        given_name=given_name,
        family_name=family_name,
    )


def test_name_saved_on_profile_is_found_on_next_upload_without_database_row(client, tmp_path):
    """Retour utilisateur : le nom enregistré au profil n'était jamais repris.
    Nom Google absent du planning, nom du planning enregistré au profil : le
    dépôt suivant doit trouver la ligne directement."""
    email = "ami-profil-puis-depot@example.com"
    _override_session_only_user(email, given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    saved = client.post("/account", data={"pdf_name": "MARTIN Sophie"})
    page = client.get("/upload")
    response = _upload(client, pdf_path)

    assert "cherchera « MARTIN Sophie »" in saved.text
    assert "sous le nom <strong>MARTIN Sophie</strong>" in page.text
    assert "Voici ce qui a été trouvé" in response.text
    assert "MARTIN Sophie" in response.text


def test_first_success_fills_the_profile_without_database_row(client, tmp_path):
    email = "ami-premier-succes-sans-ligne@example.com"
    _override_session_only_user(email, given_name="Sophie", family_name="MARTIN")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)

    _upload(client, pdf_path)

    assert get_pdf_name(email) == "MARTIN Sophie"


def test_typed_name_is_saved_without_database_row(client, tmp_path):
    email = "ami-nom-tape-sans-ligne@example.com"
    _override_session_only_user(email, given_name="Inconnue", family_name="PERSONNE")
    pdf_path = tmp_path / "planning.pdf"
    _build_planning_pdf(pdf_path)
    upload_id = _upload_id_from(_upload(client, pdf_path).text, suffix="name")

    response = client.post(f"/upload/{upload_id}/name", data={"pdf_name": "MARTIN Sophie"})

    assert get_pdf_name(email) == "MARTIN Sophie"
    assert "« MARTIN Sophie » est enregistré dans" in response.text


def test_upload_form_warns_that_analysis_can_take_minutes(client):
    """Demande utilisateur : pendant l'envoi, prévenir que ça peut prendre
    quelques minutes (app.js affiche data-loading sous le bouton)."""
    _override_user()

    response = client.get("/upload")

    assert response.status_code == 200
    assert "data-loading=" in response.text
    assert "quelques minutes" in response.text


def test_ai_notice_lists_every_model_of_the_chain(client, monkeypatch):
    """Demande utilisateur : la mention IA doit refléter LLM_MODELS, repli compris."""
    _override_user()
    monkeypatch.setitem(
        upload_module.templates.env.globals,
        "llm_models",
        ("gemini-a", "gemini-b", "gemini-c"),
    )

    response = client.get("/upload")

    assert "gemini-a, puis en repli s'il est saturé : gemini-b, gemini-c" in response.text


def test_ai_notice_single_model_mentions_no_fallback(client, monkeypatch):
    _override_user()
    monkeypatch.setitem(upload_module.templates.env.globals, "llm_models", ("gemini-seul",))

    response = client.get("/upload")

    assert "(gemini-seul)" in response.text
    assert "repli" not in response.text
