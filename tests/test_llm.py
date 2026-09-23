"""Tests de llm.py avec un fournisseur factice (plan, section 13).

Aucun appel réseau : le client Gemini est remplacé par un double.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from google.genai.errors import ServerError

from app.llm import MAX_ATTEMPTS, ExtractError, RateLimitError, extract, health_check
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


def test_extract_raises_rate_limit_error_on_quota_exhausted():
    calls = []

    class _QuotaExhaustedModels:
        def generate_content(self, **kwargs):
            calls.append(1)
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
                "'message': 'quota exceeded', 'status': 'RESOURCE_EXHAUSTED'}}"
            )

    class _QuotaExhaustedClient:
        models = _QuotaExhaustedModels()

    with pytest.raises(RateLimitError):
        extract(b"fake-png-bytes", client=_QuotaExhaustedClient())

    # Reessayer tout de suite n'aiderait pas contre un quota depasse.
    assert len(calls) == 1


def test_health_check_succeeds_on_trivial_json_without_periode_creneaux():
    """Le test de sante ne doit pas dependre du schema periode/creneaux
    reel : un JSON trivial suffit, contrairement a extract()."""
    client = _FakeClient(json.dumps({"ok": True}))

    health_check(client=client)  # ne doit pas lever


def test_health_check_raises_extract_error_on_provider_exception():
    class _BrokenModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("panne modele simulee")

    class _BrokenClient:
        models = _BrokenModels()

    with pytest.raises(ExtractError):
        health_check(client=_BrokenClient())


PAYLOAD = {"periode": {"debut": "2026-09-14", "fin": "2026-09-20"}, "creneaux": []}


def _with_models(monkeypatch, *models: str) -> None:
    """Chaîne de modèles pour un test : `settings` est figé, on injecte une
    copie modifiée dans le module llm."""
    monkeypatch.setattr("app.llm.settings", dataclasses.replace(settings, llm_models=models))


def _quota_error() -> RuntimeError:
    return RuntimeError(
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
        "'message': 'quota exceeded', 'status': 'RESOURCE_EXHAUSTED'}}"
    )


class _ChainClient:
    """Faux client qui répond selon le modèle demandé : une exception à lever
    ou un texte à renvoyer. Garde la trace des modèles appelés, dans l'ordre."""

    def __init__(self, behaviours: dict[str, Exception | str]):
        self.calls: list[str] = []
        self._behaviours = behaviours
        self.models = self

    def generate_content(self, **kwargs):
        model = kwargs["model"]
        self.calls.append(model)
        behaviour = self._behaviours[model]
        if isinstance(behaviour, Exception):
            raise behaviour
        return _FakeResponse(behaviour)


def test_extract_falls_back_to_next_model_on_quota_exhausted(monkeypatch):
    """Retour utilisateur : « le modèle plante car trop de demandes ». Les
    quotas du tier gratuit sont comptés par modèle : le suivant répond."""
    _with_models(monkeypatch, "premier", "second")
    client = _ChainClient({"premier": _quota_error(), "second": json.dumps(PAYLOAD)})

    result = extract(b"fake-png-bytes", client=client)

    assert result == PAYLOAD
    assert client.calls == ["premier", "second"]


def test_extract_falls_back_after_server_error_retries_exhausted(monkeypatch):
    monkeypatch.setattr("app.llm.time.sleep", lambda seconds: None)
    _with_models(monkeypatch, "premier", "second")
    client = _ChainClient({"premier": _server_error(), "second": json.dumps(PAYLOAD)})

    result = extract(b"fake-png-bytes", client=client)

    assert result == PAYLOAD
    assert client.calls == ["premier"] * MAX_ATTEMPTS + ["second"]


def test_extract_raises_rate_limit_error_when_every_model_is_exhausted(monkeypatch):
    _with_models(monkeypatch, "premier", "second")
    client = _ChainClient({"premier": _quota_error(), "second": _quota_error()})

    with pytest.raises(RateLimitError) as exc_info:
        extract(b"fake-png-bytes", client=client)

    assert client.calls == ["premier", "second"]
    assert "premier" in str(exc_info.value)
    assert "second" in str(exc_info.value)


def test_extract_mixed_failures_raise_generic_extract_error(monkeypatch):
    """Quota sur l'un, panne sur l'autre : ce n'est plus un simple « quota
    atteint », le message d'erreur générique convient mieux à la personne."""
    monkeypatch.setattr("app.llm.time.sleep", lambda seconds: None)
    _with_models(monkeypatch, "premier", "second")
    client = _ChainClient({"premier": _quota_error(), "second": _server_error()})

    with pytest.raises(ExtractError) as exc_info:
        extract(b"fake-png-bytes", client=client)

    assert not isinstance(exc_info.value, RateLimitError)


def test_extract_does_not_fall_back_on_non_capacity_errors(monkeypatch):
    """Clé invalide, requête refusée... : ça se reproduirait à l'identique sur
    le modèle suivant, autant échouer tout de suite et clairement."""
    _with_models(monkeypatch, "premier", "second")
    client = _ChainClient({"premier": RuntimeError("400 INVALID_ARGUMENT"), "second": "{}"})

    with pytest.raises(ExtractError):
        extract(b"fake-png-bytes", client=client)

    assert client.calls == ["premier"]
