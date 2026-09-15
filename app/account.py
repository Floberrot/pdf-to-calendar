"""Réglages du compte : nom recherché dans le planning.

Non listé dans l'arborescence de la section 9, ajouté suite à un retour
utilisateur : la détection automatique à partir du prénom/nom Google
(`build_candidates`, `app/pdf/locate.py`) n'est pas toujours fiable (profil
Google incomplet ou différent du nom imprimé sur le planning). Un panel
dédié permet de corriger `pdf_name` (déjà utilisé par `build_candidates`)
une fois pour toutes, sans repasser par l'écran « changer le nom » à chaque
dépôt raté.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates

from app.auth import CurrentUser, require_user
from app.db import get_pdf_name, set_pdf_name

router = APIRouter(prefix="/account", tags=["account"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _render(request: Request, user: CurrentUser, *, saved: bool = False):
    return templates.TemplateResponse(
        request,
        "account.html",
        {"user": user, "pdf_name": get_pdf_name(user.email), "saved": saved},
    )


@router.get("")
def account_form(request: Request, user: Annotated[CurrentUser, Depends(require_user)]):
    return _render(request, user)


@router.post("")
def account_save(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    pdf_name: Annotated[str, Form()],
):
    """Un champ vide efface l'enregistrement : `build_candidates` retombe
    alors sur la détection automatique (`if pdf_name:` y traite déjà None et
    "" de la même façon)."""
    set_pdf_name(user.email, pdf_name.strip())
    return _render(request, user, saved=True)
