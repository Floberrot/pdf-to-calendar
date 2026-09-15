"""Dépôt du PDF, orchestration locate → crop, secours (plan, section 5A/5B/5B bis).

Non listé dans l'arborescence de la section 9, ajouté pour porter les routes
d'upload (voir description de la PR).
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import CurrentUser, require_user
from app.calendar_sync import (
    ExistingEvent,
    SyncError,
    SyncResult,
    format_heure,
    list_existing_events,
    sync_to_calendar,
)
from app.db import get_last_crop, get_pdf_name, set_last_crop, set_pdf_name
from app.llm import ExtractError, RateLimitError, extract
from app.log import log
from app.pdf.crop import compose_crop, crop_manual, redact_name
from app.pdf.locate import LocateResult, build_candidates, locate
from app.pdf.render import render_pages
from app.settings import settings
from app.validate import Creneau, ValidatedExtraction, ValidationError, validate

logger = logging.getLogger("app")

EXTRACTION_CACHE_NAME = "extraction.json"

router = APIRouter(prefix="/upload", tags=["upload"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Fixe pour la vie du process (charge une fois au demarrage) : global Jinja
# plutot que reinjecte dans le contexte de chaque TemplateResponse.
templates.env.globals["llm_model"] = settings.llm_model

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
UPLOAD_MAX_AGE_SECONDS = 30 * 60
BASE_UPLOAD_DIR = Path(tempfile.gettempdir()) / "pdf-to-calendar-uploads"

NAME_RETRY_REASONS = {"nom_introuvable", "nom_homonyme"}

ERROR_MESSAGES = {
    "extraction_echouee": (
        "Le modèle n'a pas réussi à lire l'image. Réessaie ou recadre à la main."
    ),
    "limite_atteinte": (
        "Le quota gratuit du modèle est atteint pour l'instant. Réessaie dans "
        "quelques minutes, ou demain si ça persiste."
    ),
    "periode_invalide": "La période renvoyée par le modèle est invalide.",
    "periode_trop_longue": (
        "La période détectée dépasse 6 semaines : la découpe est probablement mauvaise."
    ),
    "periode_hors_fenetre": "La période détectée est trop loin dans le passé ou le futur.",
    "creneau_invalide": "Un créneau renvoyé par le modèle est invalide.",
    "creneau_hors_periode": "Un créneau détecté tombe en dehors de la période de l'en-tête.",
    "creneau_duree_invalide": "Un créneau détecté dure moins d'1 h ou plus de 14 h.",
}


def get_extractor() -> Callable[..., dict]:
    """Point d'injection pour les tests (fournisseur factice)."""
    return extract


def get_calendar_lister() -> Callable[..., list[ExistingEvent]]:
    """Point d'injection pour les tests (client agenda factice)."""
    return list_existing_events


def get_calendar_syncer() -> Callable[..., SyncResult | SyncError]:
    """Point d'injection pour les tests (client agenda factice)."""
    return sync_to_calendar


def _purge_old_uploads() -> None:
    if not BASE_UPLOAD_DIR.exists():
        return
    cutoff = time.time() - UPLOAD_MAX_AGE_SECONDS
    for session_dir in BASE_UPLOAD_DIR.iterdir():
        if session_dir.is_dir() and session_dir.stat().st_mtime < cutoff:
            shutil.rmtree(session_dir, ignore_errors=True)


def _session_dir(request: Request) -> Path:
    session_id = request.session.get("upload_session_id")
    if not session_id:
        session_id = uuid.uuid4().hex
        request.session["upload_session_id"] = session_id
    _purge_old_uploads()
    path = BASE_UPLOAD_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_dir(request: Request, upload_id: str) -> Path:
    safe_id = Path(upload_id).name
    path = _session_dir(request) / safe_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Import introuvable ou expiré")
    return path


def _page_paths(upload_dir: Path) -> list[Path]:
    return sorted((upload_dir / "pages").glob("page_*.png"), key=lambda p: p.name)


@router.get("")
def upload_form(request: Request, user: Annotated[CurrentUser, Depends(require_user)]):
    return templates.TemplateResponse(request, "upload.html", {"user": user, "error": None})


def _run_locate(user: CurrentUser, pdf_path: Path, pdf_name_override: str | None = None):
    pdf_name = pdf_name_override if pdf_name_override is not None else get_pdf_name(user.email)
    candidates = build_candidates(
        pdf_name=pdf_name, family_name=user.family_name, given_name=user.given_name
    )
    return locate(str(pdf_path), candidates)


def _save_pdf_name_if_new(
    user: CurrentUser, candidate_used: str, pdf_name_override: str | None
) -> None:
    if pdf_name_override is not None and get_pdf_name(user.email) != candidate_used:
        set_pdf_name(user.email, candidate_used)


@dataclass(frozen=True)
class ExistingEventView:
    """Un `ExistingEvent` prêt pour l'affichage (demande utilisateur : les
    heures brutes de l'API étaient illisibles, et on ne voyait pas ce qui
    changeait par rapport aux créneaux sur le point d'être importés)."""

    summary: str
    range_display: str
    status: str  # "identique", "modifie" ou "supprime"


def _parse_event_moment(value: str) -> datetime | None:
    """None si `value` est une date sans heure (événement journée entière) :
    rien à comparer aux horaires d'un créneau dans ce cas."""
    if "T" not in value:
        return None
    return datetime.fromisoformat(value)


def _format_event_range(event: ExistingEvent) -> str:
    """« 15/09/2026 · 9h-17h30 », ou « 16/09/2026 (journée entière) » pour
    un événement sans heure."""
    start = _parse_event_moment(event.start)
    if start is None:
        return f"{date.fromisoformat(event.start).strftime('%d/%m/%Y')} (journée entière)"
    end = _parse_event_moment(event.end)
    debut = format_heure(start.strftime("%H:%M"))
    fin = format_heure(end.strftime("%H:%M"))
    if end.date() != start.date():
        return f"{start.strftime('%d/%m/%Y')} {debut} → {end.strftime('%d/%m/%Y')} {fin}"
    return f"{start.strftime('%d/%m/%Y')} · {debut}-{fin}"


def _event_status(event: ExistingEvent, creneaux: list[Creneau]) -> str:
    """« identique », « modifie » ou « supprime » : compare un événement déjà
    présent aux créneaux sur le point d'être importés."""
    start = _parse_event_moment(event.start)
    if start is None:
        return "modifie"
    same_day = [c for c in creneaux if c.date == start.date()]
    if not same_day:
        return "supprime"
    end = _parse_event_moment(event.end)
    debut, fin = start.strftime("%H:%M"), end.strftime("%H:%M")
    if any(c.debut == debut and c.fin == fin for c in same_day):
        return "identique"
    return "modifie"


def _fetch_existing_event_views(
    calendar_lister: Callable[..., list[ExistingEvent]],
    user: CurrentUser,
    extraction: ValidatedExtraction,
) -> list[ExistingEventView] | None:
    try:
        events = calendar_lister(user.email, extraction.periode_debut, extraction.periode_fin)
        return [
            ExistingEventView(
                summary=event.summary,
                range_display=_format_event_range(event),
                status=_event_status(event, extraction.creneaux),
            )
            for event in events
        ]
    except Exception:  # noqa: BLE001 - API/formatage, ne doit pas casser la prévisualisation
        return None


def _render_preview(
    request: Request,
    user: CurrentUser,
    upload_id: str,
    *,
    matched_text: str | None,
    extraction: ValidatedExtraction | None,
    error_message: str | None,
    existing_events: list[ExistingEventView] | None,
    sync_error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "upload_result.html",
        {
            "user": user,
            "upload_id": upload_id,
            "matched_text": matched_text,
            "extraction": extraction,
            "error_message": error_message,
            "existing_events": existing_events,
            "sync_error": sync_error,
        },
    )


def _log_llm_failure(request: Request, user: CurrentUser, message: str) -> None:
    log(
        request_id=getattr(request.state, "request_id", ""),
        email=user.email,
        step="llm",
        status="error",
        detail={"erreur": message},
    )


def _build_preview(
    request: Request,
    user: CurrentUser,
    upload_id: str,
    upload_dir: Path,
    model_image: bytes,
    *,
    matched_text: str | None,
    extractor: Callable[..., dict],
    calendar_lister: Callable[..., list[ExistingEvent]],
):
    """Appelle le modèle puis la validation locale (plan, sections 5C/5D).

    `model_image` : jamais la même image que celle montrée en
    prévisualisation (`/upload/{id}/image/composed.png`) sur la voie
    automatique, où elle est grisée au niveau du nom avant l'appel (voir
    `redact_name`) ; identique à celle montrée sur la voie manuelle, qui n'a
    pas de position de nom connue.

    L'extraction brute est mise en cache dans le dossier d'upload : le
    bouton Valider (Phase 4) réutilise exactement ce qui a été montré ici,
    sans rappeler le modèle (ses réponses ne sont pas garanties identiques
    d'un appel à l'autre)."""
    try:
        raw = extractor(model_image)
        extraction = validate(raw, validation_weeks=settings.validation_weeks)
    except RateLimitError as exc:
        logger.exception("Quota du modele atteint")
        _log_llm_failure(request, user, str(exc))
        extraction = ValidationError("limite_atteinte")
    except ExtractError as exc:
        logger.exception("Extraction du modele en echec")
        _log_llm_failure(request, user, str(exc))
        extraction = ValidationError("extraction_echouee")

    if isinstance(extraction, ValidationError):
        return _render_preview(
            request,
            user,
            upload_id,
            matched_text=matched_text,
            extraction=None,
            error_message=ERROR_MESSAGES[extraction.reason],
            existing_events=None,
        )

    (upload_dir / EXTRACTION_CACHE_NAME).write_text(json.dumps(raw), encoding="utf-8")

    existing_events = _fetch_existing_event_views(calendar_lister, user, extraction)

    return _render_preview(
        request,
        user,
        upload_id,
        matched_text=matched_text,
        extraction=extraction,
        error_message=None,
        existing_events=existing_events,
    )


def _result_response(
    request: Request,
    user: CurrentUser,
    upload_dir: Path,
    upload_id: str,
    result: LocateResult,
    *,
    extractor: Callable[..., dict],
    calendar_lister: Callable[..., list[ExistingEvent]],
):
    pages = _page_paths(upload_dir)
    composed = compose_crop(pages[result.page_index], result)
    composed_path = upload_dir / "composed.png"
    composed.save(composed_path)

    buffer = io.BytesIO()
    redact_name(composed, result).save(buffer, format="PNG")

    return _build_preview(
        request,
        user,
        upload_id,
        upload_dir,
        buffer.getvalue(),
        matched_text=result.matched_text,
        extractor=extractor,
        calendar_lister=calendar_lister,
    )


@router.post("")
async def upload_pdf(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    file: Annotated[UploadFile, File()],
    extractor: Annotated[Callable[..., dict], Depends(get_extractor)],
    calendar_lister: Annotated[Callable[..., list[ExistingEvent]], Depends(get_calendar_lister)],
):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return templates.TemplateResponse(
            request, "upload.html", {"user": user, "error": "Le fichier dépasse 10 Mo."}
        )

    upload_id = uuid.uuid4().hex
    upload_dir = _session_dir(request) / upload_id
    upload_dir.mkdir(parents=True)
    pdf_path = upload_dir / "source.pdf"
    pdf_path.write_bytes(content)

    try:
        pages = render_pages(pdf_path, upload_dir / "pages")
    except Exception:  # noqa: BLE001 - fichier utilisateur non fiable, type d'erreur non garanti
        shutil.rmtree(upload_dir, ignore_errors=True)
        return templates.TemplateResponse(
            request, "upload.html", {"user": user, "error": "PDF illisible."}
        )

    if len(pages) > MAX_PAGES:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return templates.TemplateResponse(
            request, "upload.html", {"user": user, "error": "Le PDF dépasse 10 pages."}
        )

    result = _run_locate(user, pdf_path)

    if isinstance(result, LocateResult):
        set_pdf_name(user.email, result.candidate_used)
        return _result_response(
            request,
            user,
            upload_dir,
            upload_id,
            result,
            extractor=extractor,
            calendar_lister=calendar_lister,
        )

    if result.reason in NAME_RETRY_REASONS:
        return templates.TemplateResponse(
            request,
            "upload_name.html",
            {
                "user": user,
                "upload_id": upload_id,
                "reason": result.reason,
                "matches": result.matches,
            },
        )

    return RedirectResponse(url=f"/upload/{upload_id}/manual", status_code=303)


@router.get("/{upload_id}/name")
def name_form(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    upload_id: str,
):
    _upload_dir(request, upload_id)  # 404 si l'import n'existe pas/plus
    return templates.TemplateResponse(
        request,
        "upload_name.html",
        {"user": user, "upload_id": upload_id, "reason": None, "matches": ()},
    )


@router.post("/{upload_id}/name")
def retry_with_name(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    upload_id: str,
    pdf_name: Annotated[str, Form()],
    extractor: Annotated[Callable[..., dict], Depends(get_extractor)],
    calendar_lister: Annotated[Callable[..., list[ExistingEvent]], Depends(get_calendar_lister)],
):
    upload_dir = _upload_dir(request, upload_id)
    pdf_path = upload_dir / "source.pdf"
    pdf_name = pdf_name.strip()

    result = _run_locate(user, pdf_path, pdf_name_override=pdf_name)

    if isinstance(result, LocateResult):
        _save_pdf_name_if_new(user, result.candidate_used, pdf_name)
        return _result_response(
            request,
            user,
            upload_dir,
            upload_id,
            result,
            extractor=extractor,
            calendar_lister=calendar_lister,
        )

    return RedirectResponse(url=f"/upload/{upload_id}/manual", status_code=303)


@router.get("/{upload_id}/manual")
def manual_crop_form(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    upload_id: str,
    page: int = 1,
):
    upload_dir = _upload_dir(request, upload_id)
    pages = _page_paths(upload_dir)
    page_index = max(0, min(page - 1, len(pages) - 1))
    last_crop = get_last_crop(user.email)
    return templates.TemplateResponse(
        request,
        "upload_manual.html",
        {
            "user": user,
            "upload_id": upload_id,
            "page_count": len(pages),
            "page_number": page_index + 1,
            "last_crop": last_crop,
        },
    )


@router.post("/{upload_id}/manual")
def manual_crop_submit(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    upload_id: str,
    page: Annotated[int, Form()],
    x: Annotated[float, Form()],
    y: Annotated[float, Form()],
    w: Annotated[float, Form()],
    h: Annotated[float, Form()],
    extractor: Annotated[Callable[..., dict], Depends(get_extractor)],
    calendar_lister: Annotated[Callable[..., list[ExistingEvent]], Depends(get_calendar_lister)],
):
    upload_dir = _upload_dir(request, upload_id)
    pages = _page_paths(upload_dir)
    page_index = max(0, min(page - 1, len(pages) - 1))

    crop = {"page": page_index, "x": x, "y": y, "w": w, "h": h}
    set_last_crop(user.email, crop)

    cropped = crop_manual(pages[page_index], x=x, y=y, w=w, h=h)
    composed_path = upload_dir / "composed.png"
    cropped.save(composed_path)

    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")

    return _build_preview(
        request,
        user,
        upload_id,
        upload_dir,
        buffer.getvalue(),
        matched_text=None,
        extractor=extractor,
        calendar_lister=calendar_lister,
    )


@router.post("/{upload_id}/confirm")
def confirm(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    upload_id: str,
    calendar_lister: Annotated[Callable[..., list[ExistingEvent]], Depends(get_calendar_lister)],
    calendar_syncer: Annotated[Callable[..., SyncResult | SyncError], Depends(get_calendar_syncer)],
):
    """Écrit dans l'agenda l'extraction montrée en prévisualisation (plan,
    section 6). Rejoue la validation sur l'extraction mise en cache plutôt
    que de faire confiance à un upload_id : une prévisualisation périmée ou
    déjà validée ne doit pas pouvoir réécrire l'agenda."""
    upload_dir = _upload_dir(request, upload_id)
    raw_path = upload_dir / EXTRACTION_CACHE_NAME
    if not raw_path.is_file():
        raise HTTPException(
            status_code=404, detail="Prévisualisation expirée, dépose le PDF à nouveau."
        )

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    extraction = validate(raw, validation_weeks=settings.validation_weeks)
    if isinstance(extraction, ValidationError):
        raise HTTPException(
            status_code=409, detail="Prévisualisation périmée, dépose le PDF à nouveau."
        )

    result = calendar_syncer(user.email, user.name, extraction)

    if isinstance(result, SyncError):
        logger.error("Ecriture agenda en echec : %s", result.detail)
        log(
            request_id=getattr(request.state, "request_id", ""),
            email=user.email,
            step="write",
            status="error",
            detail={"erreur": result.detail},
        )
        existing_events = _fetch_existing_event_views(calendar_lister, user, extraction)
        return _render_preview(
            request,
            user,
            upload_id,
            matched_text=None,
            extraction=extraction,
            error_message=None,
            existing_events=existing_events,
            sync_error=result.detail,
        )

    shutil.rmtree(upload_dir, ignore_errors=True)
    return templates.TemplateResponse(
        request,
        "upload_confirm.html",
        {
            "user": user,
            "inserted_count": result.inserted_count,
            "replaced_count": result.replaced_count,
            "uncertain_count": result.uncertain_count,
        },
    )


@router.get("/{upload_id}/image/{filename}")
def upload_image(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    upload_id: str,
    filename: str,
):
    upload_dir = _upload_dir(request, upload_id)
    safe_name = Path(filename).name
    for candidate in (upload_dir / safe_name, upload_dir / "pages" / safe_name):
        if candidate.is_file():
            return FileResponse(candidate, media_type="image/png")
    raise HTTPException(status_code=404, detail="Image introuvable")
