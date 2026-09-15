"""Tests de calendar_sync.py avec un client Google Agenda factice (plan, section 13).

Aucun appel réseau : le service Calendar est remplacé par un double en mémoire.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.calendar_sync import (
    APP_TAG,
    ExistingEvent,
    SyncError,
    SyncResult,
    health_check,
    list_existing_events,
    sync_to_calendar,
)
from app.settings import settings
from app.validate import Creneau, ValidatedExtraction


class _FakeExecutable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FailingExecutable:
    def execute(self):
        raise RuntimeError("panne agenda simulee")


class _FakeEvents:
    def __init__(
        self,
        items: list[dict],
        calls: list[dict],
        *,
        fail_on_insert_index: int | None = None,
        fail_on_get: bool = False,
        fail_on_delete: bool = False,
    ):
        self._items = items
        self._calls = calls
        self._fail_on_insert_index = fail_on_insert_index
        self._fail_on_get = fail_on_get
        self._fail_on_delete = fail_on_delete
        self._insert_count = 0

    def list(self, **kwargs):
        self._calls.append({"op": "list", **kwargs})
        return _FakeExecutable({"items": self._items})

    def insert(self, **kwargs):
        self._calls.append({"op": "insert", **kwargs})
        index = self._insert_count
        self._insert_count += 1
        if index == self._fail_on_insert_index:
            return _FailingExecutable()
        return _FakeExecutable({"id": f"new{index}"})

    def get(self, **kwargs):
        self._calls.append({"op": "get", **kwargs})
        if self._fail_on_get:
            return _FailingExecutable()
        return _FakeExecutable({"id": kwargs.get("eventId")})

    def delete(self, **kwargs):
        self._calls.append({"op": "delete", **kwargs})
        if self._fail_on_delete:
            return _FailingExecutable()
        return _FakeExecutable({})


class _FakeService:
    def __init__(
        self,
        items: list[dict],
        calls: list[dict],
        *,
        fail_on_insert_index: int | None = None,
        fail_on_get: bool = False,
        fail_on_delete: bool = False,
    ):
        self._events = _FakeEvents(
            items,
            calls,
            fail_on_insert_index=fail_on_insert_index,
            fail_on_get=fail_on_get,
            fail_on_delete=fail_on_delete,
        )

    def events(self) -> _FakeEvents:
        return self._events


def _old_event(event_id: str, start_date: str, end_date: str) -> dict:
    return {"id": event_id, "summary": "", "start": {"date": start_date}, "end": {"date": end_date}}


def _extraction(*creneaux_args) -> ValidatedExtraction:
    creneaux = [
        Creneau(date=d, debut=debut, fin=fin, lieu=lieu, duration_hours=1)
        for d, debut, fin, lieu in creneaux_args
    ]
    return ValidatedExtraction(
        periode_debut=date(2026, 9, 14), periode_fin=date(2026, 9, 20), creneaux=creneaux
    )


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


def test_sync_returns_counts_on_success():
    old_items = [_old_event("old1", "2026-09-15", "2026-09-16")]
    service = _FakeService(old_items, [])
    extraction = _extraction(
        (date(2026, 9, 15), "09:00", "17:00", "Site B"),
        (date(2026, 9, 16), "09:00", "17:00", ""),
    )

    result = sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    assert result == SyncResult(inserted_count=2, replaced_count=1)


def test_sync_inserts_before_deleting_old_events():
    old_items = [_old_event("old1", "2026-09-15", "2026-09-16")]
    calls: list[dict] = []
    service = _FakeService(old_items, calls)
    extraction = _extraction((date(2026, 9, 15), "09:00", "17:00", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    ops = [call["op"] for call in calls]
    assert ops.index("insert") < ops.index("delete")


def test_sync_tags_inserted_events_with_app_and_email():
    calls: list[dict] = []
    service = _FakeService([], calls)
    extraction = _extraction((date(2026, 9, 15), "09:00", "17:00", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    insert_call = next(call for call in calls if call["op"] == "insert")
    tags = insert_call["body"]["extendedProperties"]["private"]
    assert tags == {"app": APP_TAG, "email": "ami@example.com"}


def test_sync_builds_title_with_name_and_formatted_hours():
    calls: list[dict] = []
    service = _FakeService([], calls)
    extraction = _extraction((date(2026, 9, 15), "09:00", "17:30", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    insert_call = next(call for call in calls if call["op"] == "insert")
    assert insert_call["body"]["summary"] == "Sophie Martin — 9h-17h30"


def test_sync_sets_explicit_timezone_never_utc():
    calls: list[dict] = []
    service = _FakeService([], calls)
    extraction = _extraction((date(2026, 9, 15), "09:00", "17:00", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    insert_call = next(call for call in calls if call["op"] == "insert")
    assert insert_call["body"]["start"]["timeZone"] == settings.tz
    assert insert_call["body"]["end"]["timeZone"] == settings.tz


def test_sync_overnight_creneau_ends_next_day():
    calls: list[dict] = []
    service = _FakeService([], calls)
    extraction = _extraction((date(2026, 9, 15), "21:00", "07:00", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    insert_call = next(call for call in calls if call["op"] == "insert")
    assert insert_call["body"]["start"]["dateTime"] == "2026-09-15T21:00:00"
    assert insert_call["body"]["end"]["dateTime"] == "2026-09-16T07:00:00"


def test_sync_rolls_back_inserted_events_when_one_insertion_fails():
    old_items = [_old_event("old1", "2026-09-15", "2026-09-16")]
    calls: list[dict] = []
    service = _FakeService(old_items, calls, fail_on_insert_index=1)
    extraction = _extraction(
        (date(2026, 9, 15), "09:00", "17:00", ""),
        (date(2026, 9, 16), "09:00", "17:00", ""),
    )

    result = sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    assert isinstance(result, SyncError)
    deleted_ids = [call["eventId"] for call in calls if call["op"] == "delete"]
    assert deleted_ids == ["new0"]


def test_sync_never_deletes_old_events_when_an_insertion_fails():
    old_items = [_old_event("old1", "2026-09-15", "2026-09-16")]
    calls: list[dict] = []
    service = _FakeService(old_items, calls, fail_on_insert_index=0)
    extraction = _extraction((date(2026, 9, 15), "09:00", "17:00", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    # La toute premiere insertion echoue : rien n'a ete cree, donc aucune
    # suppression ne doit avoir lieu, ni des anciens evenements ni du rollback.
    assert not any(call["op"] == "delete" for call in calls)


def test_sync_only_deletes_ids_returned_by_the_tagged_listing():
    old_items = [
        _old_event("old1", "2026-09-15", "2026-09-16"),
        _old_event("old2", "2026-09-16", "2026-09-17"),
    ]
    calls: list[dict] = []
    service = _FakeService(old_items, calls)
    extraction = _extraction((date(2026, 9, 15), "09:00", "17:00", ""))

    sync_to_calendar("ami@example.com", "Sophie Martin", extraction, service=service)

    deleted_ids = {call["eventId"] for call in calls if call["op"] == "delete"}
    assert deleted_ids == {"old1", "old2"}


def test_health_check_inserts_reads_and_deletes_in_order():
    calls: list[dict] = []
    service = _FakeService([], calls)

    health_check(service=service)

    ops = [call["op"] for call in calls]
    assert ops == ["insert", "get", "delete"]


def test_health_check_deletes_even_if_the_read_fails():
    calls: list[dict] = []
    service = _FakeService([], calls, fail_on_get=True)

    with pytest.raises(RuntimeError):
        health_check(service=service)

    ops = [call["op"] for call in calls]
    assert ops == ["insert", "get", "delete"]


def test_health_check_propagates_insert_failure():
    calls: list[dict] = []
    service = _FakeService([], calls, fail_on_insert_index=0)

    with pytest.raises(RuntimeError):
        health_check(service=service)

    assert [call["op"] for call in calls] == ["insert"]
