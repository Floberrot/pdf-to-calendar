"""Tests de calendar_sync.py avec un client Google Agenda factice (plan, section 13).

Aucun appel réseau : le service Calendar est remplacé par un double en mémoire.
"""

from __future__ import annotations

from datetime import date

from app.calendar_sync import APP_TAG, ExistingEvent, list_existing_events


class _FakeExecutable:
    def __init__(self, items: list[dict]):
        self._items = items

    def execute(self) -> dict:
        return {"items": self._items}


class _FakeEvents:
    def __init__(self, items: list[dict], calls: list[dict]):
        self._items = items
        self._calls = calls

    def list(self, **kwargs) -> _FakeExecutable:
        self._calls.append(kwargs)
        return _FakeExecutable(self._items)


class _FakeService:
    def __init__(self, items: list[dict], calls: list[dict]):
        self._events = _FakeEvents(items, calls)

    def events(self) -> _FakeEvents:
        return self._events


def test_list_existing_events_maps_response_items():
    items = [
        {
            "id": "evt1",
            "summary": "Florian Berrot — 9h-17h",
            "start": {"dateTime": "2026-09-15T09:00:00+02:00"},
            "end": {"dateTime": "2026-09-15T17:00:00+02:00"},
        }
    ]
    service = _FakeService(items, [])

    result = list_existing_events(
        "ami@example.com", date(2026, 9, 14), date(2026, 9, 20), service=service
    )

    assert result == [
        ExistingEvent(
            id="evt1",
            summary="Florian Berrot — 9h-17h",
            start="2026-09-15T09:00:00+02:00",
            end="2026-09-15T17:00:00+02:00",
        )
    ]


def test_list_existing_events_handles_all_day_events():
    items = [
        {
            "id": "evt2",
            "summary": "Repos",
            "start": {"date": "2026-09-16"},
            "end": {"date": "2026-09-17"},
        }
    ]
    service = _FakeService(items, [])

    result = list_existing_events(
        "ami@example.com", date(2026, 9, 14), date(2026, 9, 20), service=service
    )

    assert result[0].start == "2026-09-16"
    assert result[0].end == "2026-09-17"


def test_list_existing_events_empty_response_returns_empty_list():
    service = _FakeService([], [])

    result = list_existing_events(
        "ami@example.com", date(2026, 9, 14), date(2026, 9, 20), service=service
    )

    assert result == []


def test_list_existing_events_filters_on_tag_and_email():
    calls: list[dict] = []
    service = _FakeService([], calls)

    list_existing_events("ami@example.com", date(2026, 9, 14), date(2026, 9, 20), service=service)

    assert len(calls) == 1
    assert calls[0]["privateExtendedProperty"] == [f"app={APP_TAG}", "email=ami@example.com"]
    assert calls[0]["singleEvents"] is True


def test_list_existing_events_queries_period_plus_one_day():
    calls: list[dict] = []
    service = _FakeService([], calls)

    list_existing_events("ami@example.com", date(2026, 9, 14), date(2026, 9, 20), service=service)

    assert calls[0]["timeMin"] == "2026-09-14T00:00:00Z"
    assert calls[0]["timeMax"] == "2026-09-21T00:00:00Z"
