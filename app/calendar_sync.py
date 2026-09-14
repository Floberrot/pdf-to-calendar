"""Lecture/écriture de l'agenda Google, par tags (plan, section 6).

Ce module ne porte pour l'instant que la lecture (liste des événements déjà
tagués, pour la comparaison "sera remplacé" en prévisualisation, section 6
étape 1). L'écriture (insertion puis suppression) arrive en Phase 4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.settings import settings

APP_TAG = "planning-import"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass(frozen=True)
class ExistingEvent:
    id: str
    summary: str
    start: str
    end: str


class _Events(Protocol):
    def list(self, **kwargs: Any) -> Any: ...


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
