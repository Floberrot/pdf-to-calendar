"""Tests de llm.py avec un fournisseur factice (plan, section 13).

Aucun appel réseau : le client Gemini est remplacé par un double.
"""

from __future__ import annotations

import json

import pytest
from google.genai.errors import ServerError

from app.llm import MAX_ATTEMPTS, ExtractError, extract
from app.settings import settings


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModels:
    def __init__(self, text: str):
        self._text = text

    def generate_content(self, **kwargs):
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str):
        self.models = _FakeModels(text)


def test_extract_returns_parsed_json():
    payload = {
        "periode": {"debut": "2026-09-14", "fin": "2026-09-20"},
        "creneaux": [{"date": "2026-09-15", "debut": "09:00", "fin": "17:30", "lieu": "Site B"}],
    }
    client = _FakeClient(json.dumps(payload))

    result = extract(b"fake-png-bytes", client=client)

    assert result == payload


def test_extract_malformed_json_raises_clean_error():
    client = _FakeClient("ceci n'est pas du json")

    with pytest.raises(ExtractError):
        extract(b"fake-png-bytes", client=client)


def test_extract_missing_keys_raises_clean_error():
    client = _FakeClient(json.dumps({"autre_chose": True}))

    with pytest.raises(ExtractError):
        extract(b"fake-png-bytes", client=client)


def test_extract_provider_exception_raises_clean_error():
    class _BrokenModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("panne réseau simulée")

    class _BrokenClient:
        models = _BrokenModels()

    with pytest.raises(ExtractError):
        extract(b"fake-png-bytes", client=_BrokenClient())


def test_extract_redacts_api_key_from_error_message():
    """Jamais de secret dans les logs (CLAUDE.md) : le message d'erreur peut
    finir journalisé, la clé API ne doit jamais y apparaître en clair."""

    class _BrokenModels:
        def generate_content(self, **kwargs):
            raise RuntimeError(f"401 unauthorized, key={settings.llm_api_key}")

    class _BrokenClient:
        models = _BrokenModels()

    with pytest.raises(ExtractError) as exc_info:
        extract(b"fake-png-bytes", client=_BrokenClient())

    assert settings.llm_api_key not in str(exc_info.value)
    assert "***" in str(exc_info.value)


def _server_error() -> ServerError:
    return ServerError(503, {"error": {"code": 503, "message": "experiencing high demand"}}, None)


def test_extract_retries_on_server_error_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda seconds: None)
    payload = {"periode": {"debut": "2026-09-14", "fin": "2026-09-20"}, "creneaux": []}
    calls = []

    class _FlakyModels:
        def generate_content(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _server_error()
            return _FakeResponse(json.dumps(payload))

    class _FlakyClient:
        models = _FlakyModels()

    result = extract(b"fake-png-bytes", client=_FlakyClient())

    assert result == payload
    assert len(calls) == 2


def test_extract_gives_up_after_max_attempts_on_persistent_server_error(monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda seconds: None)
    calls = []

    class _AlwaysDownModels:
        def generate_content(self, **kwargs):
            calls.append(1)
            raise _server_error()

    class _AlwaysDownClient:
        models = _AlwaysDownModels()

    with pytest.raises(ExtractError):
        extract(b"fake-png-bytes", client=_AlwaysDownClient())

    assert len(calls) == MAX_ATTEMPTS


def test_extract_does_not_retry_non_server_errors():
    calls = []

    class _BrokenModels:
        def generate_content(self, **kwargs):
            calls.append(1)
            raise RuntimeError("panne non reessayable")

    class _BrokenClient:
        models = _BrokenModels()

    with pytest.raises(ExtractError):
        extract(b"fake-png-bytes", client=_BrokenClient())

    assert len(calls) == 1
