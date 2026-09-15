"""Point d'entrée FastAPI (plan, section 9)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.account import router as account_router
from app.admin import router as admin_router
from app.auth import CurrentUser, RedirectToLogin, require_user
from app.auth import router as auth_router
from app.db import init_db, purge_old_imports
from app.settings import settings
from app.upload import router as upload_router

logger = logging.getLogger("app")

app = FastAPI(title="Planning → Agenda")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(admin_router)
app.include_router(account_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    return await call_next(request)


@app.exception_handler(RedirectToLogin)
def handle_redirect_to_login(request: Request, exc: RedirectToLogin) -> RedirectResponse:
    return RedirectResponse(url="/auth/login", status_code=302)


@app.on_event("startup")
def on_startup() -> None:
    if not settings.allowed_emails:
        logger.warning("ALLOWED_EMAILS est vide : personne ne pourra se connecter.")
    if not settings.admin_emails:
        logger.warning("ADMIN_EMAILS est vide : personne n'aura accès à /admin.")
    init_db()
    purge_old_imports()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index(request: Request, user: Annotated[CurrentUser, Depends(require_user)]):
    return templates.TemplateResponse(request, "index.html", {"user": user})
