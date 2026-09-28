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

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates

from app.auth import CurrentUser, require_user
from app.db import get_pdf_name, set_pdf_name

router = APIRouter(prefix="/account", tags=["account"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@dataclass(frozen=True)
class SearchName:
    """Nom sous lequel l'appli cherche la ligne de la personne, et d'où il
    vient : rappelé sur l'accueil, le dépôt et l'écran de reprise, pour que
    chacun sache quoi corriger quand sa ligne n'est pas trouvée."""

    name: str | None
    source: Literal["profil", "google", "aucun"]


def search_name_for(user: CurrentUser) -> SearchName:
    """Le nom enregistré au profil, sinon celui du compte Google — tel que
    `build_candidates` le cherche (sans nom de famille, aucun candidat)."""
    saved = get_pdf_name(user.email)
    if saved:
        return SearchName(saved, "profil")
    if user.family_name.strip():
        return SearchName(f"{user.given_name} {user.family_name}".strip(), "google")
    return SearchName(None, "aucun")


def _google_name(user: CurrentUser) -> str:
    return f"{user.given_name} {user.family_name}".strip()


def _render(request: Request, user: CurrentUser, *, saved: bool = False, welcome: bool = False):
    saved_name = get_pdf_name(user.email)
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "user": user,
            "search": search_name_for(user),
            "prefill": saved_name or _google_name(user),
            "saved": saved,
            "welcome": welcome,
        },
    )


@router.get("")
def account_form(request: Request, user: Annotated[CurrentUser, Depends(require_user)]):
    """`?bienvenue=1` : arrivée depuis la connexion tant qu'aucun nom n'est
    enregistré (`auth.after_login_url`), avec le nom Google pré-rempli à
    confirmer d'un clic."""
    return _render(request, user, welcome=request.query_params.get("bienvenue") == "1")


@router.post("")
def account_save(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    pdf_name: Annotated[str, Form()] = "",
):
    """Un champ vide efface l'enregistrement : `build_candidates` retombe
    alors sur la détection automatique (`if pdf_name:` y traite déjà None et
    "" de la même façon). Valeur par défaut nécessaire sur `pdf_name` : sans
    elle, un formulaire soumis avec le champ vide est rejeté (422) plutôt que
    reçu comme une chaîne vide."""
    set_pdf_name(user.email, pdf_name.strip())
    return _render(request, user, saved=True)
