"""Dépôt du PDF, orchestration locate → crop, secours (plan, section 5A/5B/5B bis).

Non listé dans l'arborescence de la section 9, ajouté pour porter les routes
d'upload (voir description de la PR).
"""

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import CurrentUser, require_user
from app.db import get_last_crop, get_pdf_name, set_last_crop, set_pdf_name
from app.pdf.crop import compose_crop, crop_manual
from app.pdf.locate import LocateResult, build_candidates, locate
from app.pdf.render import render_pages

router = APIRouter(prefix="/upload", tags=["upload"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
UPLOAD_MAX_AGE_SECONDS = 30 * 60
BASE_UPLOAD_DIR = Path(tempfile.gettempdir()) / "pdf-to-calendar-uploads"

NAME_RETRY_REASONS = {"nom_introuvable", "nom_homonyme"}


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


def _result_response(
    request: Request,
    user: CurrentUser,
    upload_dir: Path,
    upload_id: str,
    result: LocateResult,
):
    pages = _page_paths(upload_dir)
    composed = compose_crop(pages[result.page_index], result)
    composed_path = upload_dir / "composed.png"
    composed.save(composed_path)
    return templates.TemplateResponse(
        request,
        "upload_result.html",
        {"user": user, "upload_id": upload_id, "matched_text": result.matched_text},
    )


@router.post("")
async def upload_pdf(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    file: Annotated[UploadFile, File()],
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
        return _result_response(request, user, upload_dir, upload_id, result)

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
):
    upload_dir = _upload_dir(request, upload_id)
    pdf_path = upload_dir / "source.pdf"
    pdf_name = pdf_name.strip()

    result = _run_locate(user, pdf_path, pdf_name_override=pdf_name)

    if isinstance(result, LocateResult):
        _save_pdf_name_if_new(user, result.candidate_used, pdf_name)
        return _result_response(request, user, upload_dir, upload_id, result)

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
):
    upload_dir = _upload_dir(request, upload_id)
    pages = _page_paths(upload_dir)
    page_index = max(0, min(page - 1, len(pages) - 1))

    crop = {"page": page_index, "x": x, "y": y, "w": w, "h": h}
    set_last_crop(user.email, crop)

    cropped = crop_manual(pages[page_index], x=x, y=y, w=w, h=h)
    composed_path = upload_dir / "composed.png"
    cropped.save(composed_path)

    return templates.TemplateResponse(
        request,
        "upload_result.html",
        {"user": user, "upload_id": upload_id, "matched_text": None},
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
