"""Page admin : tests de santé + journal des imports (plan, sections 4 et 7)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import CurrentUser, require_admin
from app.calendar_sync import health_check as _calendar_health_check
from app.db import list_recent_imports
from app.llm import health_check as _model_health_check

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_calendar_health_check() -> Callable[..., None]:
    """Point d'injection pour les tests (client agenda factice)."""
    return _calendar_health_check


def get_model_health_check() -> Callable[..., None]:
    """Point d'injection pour les tests (fournisseur factice)."""
    return _model_health_check


def _render_admin(
    request: Request,
    user: CurrentUser,
    *,
    calendar_result: str | None = None,
    model_result: str | None = None,
):
    email_filter = request.query_params.get("email") or None
    imports = list_recent_imports(email=email_filter)
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user": user,
            "imports": imports,
            "email_filter": email_filter,
            "calendar_result": calendar_result,
            "model_result": model_result,
        },
    )


@router.get("")
def admin_home(request: Request, user: Annotated[CurrentUser, Depends(require_admin)]):
    return _render_admin(request, user)


@router.post("/health/calendar")
def health_calendar(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_admin)],
    calendar_health_check: Annotated[Callable[..., None], Depends(get_calendar_health_check)],
):
    try:
        calendar_health_check()
        result = "OK : événement créé, lu, supprimé."
    except Exception as exc:  # noqa: BLE001 - diagnostic admin, la cause doit s'afficher
        result = f"Échec : {exc}"
    return _render_admin(request, user, calendar_result=result)


@router.post("/health/model")
def health_model(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_admin)],
    model_health_check: Annotated[Callable[..., None], Depends(get_model_health_check)],
):
    try:
        model_health_check()
        result = "OK : réponse JSON reçue."
    except Exception as exc:  # noqa: BLE001 - diagnostic admin, la cause doit s'afficher
        result = f"Échec : {exc}"
    return _render_admin(request, user, model_result=result)
