"""OAuth Google + ALLOWED_EMAILS / ADMIN_EMAILS (plan, section 3).

Les listes sont vérifiées à chaque requête (via `settings`, chargé une fois
au démarrage du process), jamais mises en cache dans la session au-delà de
l'email lui-même.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.db import upsert_user_seen
from app.log import log
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


class RedirectToLogin(Exception):
    """Levée par `require_user` quand la session ne porte aucun utilisateur."""


@dataclass(frozen=True)
class CurrentUser:
    email: str
    name: str
    is_admin: bool
    given_name: str = ""
    family_name: str = ""


def is_allowed(
    email: str,
    allowed: frozenset[str] | None = None,
    admin: frozenset[str] | None = None,
) -> bool:
    allowed = settings.allowed_emails if allowed is None else allowed
    admin = settings.admin_emails if admin is None else admin
    email = email.strip().lower()
    return email in allowed or email in admin


def is_admin(email: str, admin: frozenset[str] | None = None) -> bool:
    admin = settings.admin_emails if admin is None else admin
    return email.strip().lower() in admin


def evaluate_login(userinfo: dict) -> tuple[bool, str]:
    """Décide l'accès à partir des informations OAuth. Pure, donc testable
    sans appel réseau : `email_verified` doit être vrai, l'email doit être
    dans l'une des deux listes."""
    email = (userinfo.get("email") or "").strip().lower()
    email_verified = bool(userinfo.get("email_verified"))
    if not email or not email_verified:
        return False, email
    return is_allowed(email), email


@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    # prompt=select_account : Google connecte sinon en silence avec la session
    # de navigateur active, ce qui donne l'impression que /auth/logout ne
    # fait rien (la session de l'app est bien vidée, mais Google ne redemande
    # jamais de choisir un compte).
    return await oauth.google.authorize_redirect(request, redirect_uri, prompt="select_account")


@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    name = userinfo.get("name") or userinfo.get("email") or ""
    request_id = getattr(request.state, "request_id", "")

    ok, email = evaluate_login(userinfo)
    if not ok:
        log(request_id=request_id, email=email, step="login", status="denied")
        raise HTTPException(status_code=403, detail="Accès refusé")

    request.session["user"] = {
        "email": email,
        "name": name,
        "given_name": userinfo.get("given_name") or "",
        "family_name": userinfo.get("family_name") or "",
    }
    upsert_user_seen(email)
    log(request_id=request_id, email=email, step="login", status="ok")
    return RedirectResponse(url="/")


@router.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/")


def require_user(request: Request) -> CurrentUser:
    user = request.session.get("user")
    if not user:
        raise RedirectToLogin()
    email = user["email"]
    if not is_allowed(email):
        raise HTTPException(status_code=403, detail="Accès refusé")
    return CurrentUser(
        email=email,
        name=user.get("name") or email,
        given_name=user.get("given_name") or "",
        family_name=user.get("family_name") or "",
        is_admin=is_admin(email),
    )


def require_admin(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Accès réservé à l'admin")
    return user
