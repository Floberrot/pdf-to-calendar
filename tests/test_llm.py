"""Tests de llm.py avec un fournisseur factice (plan, section 13).

Aucun appel réseau : le client Gemini est remplacé par un double.
"""

from __future__ import annotations

import json

import pytest

from app.llm import ExtractError, extract


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
