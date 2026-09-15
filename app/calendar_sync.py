"""Lecture/écriture de l'agenda Google, par tags (plan, section 6).

Séquence de `sync_to_calendar` pour un import couvrant [debut, fin] : lister
les événements déjà tagués (ce qui sera remplacé), insérer les nouveaux
créneaux, puis seulement si tout est passé, supprimer les anciens. Une
insertion qui échoue déclenche un retour en arrière (suppression, au mieux,
de ce qui vient d'être inséré) sans jamais toucher aux anciens événements :
une panne à mi-chemin laisse l'ancienne semaine en place plutôt qu'un trou.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.settings import settings
from app.validate import Creneau, ValidatedExtraction

APP_TAG = "planning-import"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
COLOR_ID_COUNT = 11


@dataclass(frozen=True)
class ExistingEvent:
    id: str
    summary: str
    start: str
    end: str


@dataclass(frozen=True)
class SyncResult:
    inserted_count: int
    replaced_count: int


@dataclass(frozen=True)
class SyncError:
    detail: str


class _Events(Protocol):
    def list(self, **kwargs: Any) -> Any: ...
    def insert(self, **kwargs: Any) -> Any: ...
    def delete(self, **kwargs: Any) -> Any: ...


class _CalendarService(Protocol):
    def events(self) -> _Events: ...


def _build_service() -> _CalendarService:
    info = json.loads(settings.google_service_account_json)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def list_existing_events(
    email: str,
    periode_debut: date,
    periode_fin: date,
    *,
    service: _CalendarService | None = None,
) -> list[ExistingEvent]:
    """Événements déjà tagués pour cette personne dans la période : ce qui
    sera remplacé, à afficher en prévisualisation.

    `service` : injection pour les tests (faux client en mémoire, section 13
    du plan) ; sans lui, construit le vrai service Google Calendar.
    """
    if service is None:
        service = _build_service()

    time_min = f"{periode_debut.isoformat()}T00:00:00Z"
    time_max = f"{(periode_fin + timedelta(days=1)).isoformat()}T00:00:00Z"

    response = (
        service.events()
        .list(
            calendarId=settings.calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            privateExtendedProperty=[f"app={APP_TAG}", f"email={email}"],
            singleEvents=True,
        )
        .execute()
    )

    return [_to_existing_event(item) for item in response.get("items", [])]


def _to_existing_event(item: dict) -> ExistingEvent:
    start = item.get("start", {})
    end = item.get("end", {})
    return ExistingEvent(
        id=item["id"],
        summary=item.get("summary", ""),
        start=start.get("dateTime") or start.get("date", ""),
        end=end.get("dateTime") or end.get("date", ""),
    )


def sync_to_calendar(
    email: str,
    name: str,
    extraction: ValidatedExtraction,
    *,
    service: _CalendarService | None = None,
) -> SyncResult | SyncError:
    """Remplace les créneaux déjà importés pour cette personne sur la
    période par ceux de `extraction` (plan, section 6).

    `service` : injection pour les tests (faux client en mémoire, section 13
    du plan) ; sans lui, construit le vrai service Google Calendar.
    """
    if service is None:
        service = _build_service()

    try:
        to_replace = list_existing_events(
            email, extraction.periode_debut, extraction.periode_fin, service=service
        )
    except Exception as exc:  # noqa: BLE001 - API externe, type d'erreur non garanti
        return SyncError(f"Impossible de lire l'agenda existant : {exc}")

    inserted_ids: list[str] = []
    for creneau in extraction.creneaux:
        try:
            inserted_ids.append(_insert_event(service, email, name, creneau))
        except Exception as exc:  # noqa: BLE001 - API externe, type d'erreur non garanti
            _delete_events(service, inserted_ids)
            return SyncError(f"L'ajout d'un créneau a échoué, rien n'a été modifié : {exc}")

    replaced = _delete_events(service, [event.id for event in to_replace])

    return SyncResult(inserted_count=len(inserted_ids), replaced_count=replaced)


def _insert_event(service: _CalendarService, email: str, name: str, creneau: Creneau) -> str:
    start_dt, end_dt = _creneau_datetimes(creneau)
    body: dict[str, Any] = {
        "summary": f"{name} — {_format_heure(creneau.debut)}-{_format_heure(creneau.fin)}",
        "start": {"dateTime": start_dt, "timeZone": settings.tz},
        "end": {"dateTime": end_dt, "timeZone": settings.tz},
        "colorId": _color_id_for_email(email),
        "reminders": {"useDefault": True},
        "extendedProperties": {"private": {"app": APP_TAG, "email": email}},
    }
    if creneau.lieu:
        body["location"] = creneau.lieu

    response = service.events().insert(calendarId=settings.calendar_id, body=body).execute()
    return response["id"]


def _creneau_datetimes(creneau: Creneau) -> tuple[str, str]:
    """Combine la date de l'en-tête et les heures du créneau ; si l'heure de
    fin n'est pas après le début, le créneau se termine le lendemain (ex.
    21:00-07:00), comme dans `validate._creneau_duration_hours`."""
    start_time = datetime.strptime(creneau.debut, "%H:%M").replace(tzinfo=UTC).time()
    end_time = datetime.strptime(creneau.fin, "%H:%M").replace(tzinfo=UTC).time()

    end_date = creneau.date + timedelta(days=1) if end_time <= start_time else creneau.date

    start_dt = datetime.combine(creneau.date, start_time)
    end_dt = datetime.combine(end_date, end_time)
    return start_dt.isoformat(), end_dt.isoformat()


def _format_heure(hhmm: str) -> str:
    """« 09:00 » -> « 9h » ; « 17:30 » -> « 17h30 » (plan, section 6)."""
    heure, minute = hhmm.split(":")
    return f"{int(heure)}h{minute}" if int(minute) else f"{int(heure)}h"


def _color_id_for_email(email: str) -> str:
    """Un `colorId` Google Calendar (chaîne « 1 » à « 11 ») stable par email."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return str(int(digest, 16) % COLOR_ID_COUNT + 1)


def _delete_events(service: _CalendarService, event_ids: list[str]) -> int:
    """Suppression au mieux : une suppression qui échoue ne doit jamais faire
    remonter d'erreur (elle est déjà, selon l'appelant, soit un retour en
    arrière, soit un nettoyage d'anciens événements après un succès)."""
    deleted = 0
    for event_id in event_ids:
        try:
            service.events().delete(calendarId=settings.calendar_id, eventId=event_id).execute()
            deleted += 1
        except Exception:  # noqa: BLE001 - suppression au mieux, ne doit jamais remonter
            pass
    return deleted
