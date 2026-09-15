"""Appel au modèle vision (plan, section 5C).

`extract(image_png) -> dict` est l'interface indépendante du fournisseur ;
le choix du modèle (Gemini) et son SDK restent internes à ce module, pour
pouvoir changer de fournisseur sans toucher aux appelants.
"""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from google import genai
from google.genai import types
from google.genai.errors import ServerError
from PIL import Image

from app.settings import settings

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 5

_HEALTH_CHECK_PROMPT = 'Réponds uniquement avec cet objet JSON, sans rien ajouter : {"ok": true}'

_PROMPT_TEMPLATE = """Tu lis un planning de travail dans cette image : une bande \
d'en-tête avec les dates de la semaine, empilée au-dessus de la ligne d'une \
seule personne.

Date d'aujourd'hui : {today}.

Renvoie uniquement un objet JSON avec :
- "periode" : {{"debut": "AAAA-MM-JJ", "fin": "AAAA-MM-JJ"}}, la plage de dates \
couverte par l'en-tête (pas la plage des créneaux : un jour de repos en fin de \
semaine doit être inclus).
- "creneaux" : liste d'objets {{"date": "AAAA-MM-JJ", "debut": "HH:MM", \
"fin": "HH:MM", "lieu": "..."}}, un par créneau de travail sur la ligne. \
"lieu" est une chaîne vide si rien n'est indiqué. Un jour sans créneau \
n'apparaît pas dans la liste. Une même journée peut avoir plusieurs créneaux \
(coupure). Si un créneau se termine après minuit, indique l'heure de fin \
telle qu'écrite sur le planning (ex. 21:00 à 07:00), sans changer la date."""


class ExtractError(Exception):
    """Levée quand l'appel au modèle échoue ou renvoie un JSON inexploitable."""


class RateLimitError(ExtractError):
    """Levée quand le quota de l'API (tier gratuit) est atteint : inutile de
    réessayer tout de suite, contrairement à un `ServerError` transitoire."""


class _GenerateContent(Protocol):
    def generate_content(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):
    models: _GenerateContent


def _redact(message: str) -> str:
    if settings.llm_api_key and settings.llm_api_key in message:
        return message.replace(settings.llm_api_key, "***")
    return message


def _is_rate_limit(message: str) -> bool:
    """Motif observé pour un quota dépassé (429 RESOURCE_EXHAUSTED), par
    analogie avec le 503 UNAVAILABLE confirmé en production."""
    return message.startswith("429") or "RESOURCE_EXHAUSTED" in message


def _call_model(contents: list[Any], *, client: _Client) -> dict:
    """Appelle le modèle, renvoie le JSON de la réponse.

    Un `ServerError` (5xx, ex. « experiencing high demand ») déclenche un
    réessai : Google indique explicitement que ces pics sont temporaires.
    Le SDK réessaie déjà une fois en interne ; ça ne suffit pas toujours.
    Un quota dépassé (429) ne réessaie jamais : ça n'aiderait pas.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=settings.llm_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            return json.loads(response.text)
        except ServerError as exc:
            if attempt == MAX_ATTEMPTS:
                raise ExtractError(f"Appel au modèle échoué : {_redact(str(exc))}") from exc
            time.sleep(RETRY_DELAY_SECONDS)
        except Exception as exc:
            message = _redact(str(exc))
            if _is_rate_limit(message):
                raise RateLimitError(f"Quota du modèle atteint : {message}") from exc
            raise ExtractError(f"Appel au modèle échoué : {message}") from exc

    raise ExtractError("Appel au modèle échoué : aucune tentative effectuée")


def extract(image_png: bytes, *, client: _Client | None = None) -> dict:
    """Envoie l'image découpée au modèle, renvoie le JSON {periode, creneaux}.

    `client` : injection pour les tests (fournisseur factice, section 13 du
    plan) ; sans lui, construit le vrai client Gemini.
    """
    prompt = _PROMPT_TEMPLATE.format(today=datetime.now(UTC).date().isoformat())
    if client is None:
        client = genai.Client(api_key=settings.llm_api_key)

    contents: list[Any] = [types.Part.from_bytes(data=image_png, mime_type="image/png"), prompt]
    data = _call_model(contents, client=client)

    if not isinstance(data, dict) or "periode" not in data or "creneaux" not in data:
        raise ExtractError("Réponse du modèle sans periode/creneaux")

    return data


def health_check(*, client: _Client | None = None) -> None:
    """Test de santé (page admin, plan section 4) : image minimale, prompt
    trivial indépendant du schéma periode/creneaux, pour ne pas dépendre de
    la capacité du modèle à reconnaître un vrai planning dans une image de
    test. Ne renvoie rien ; lève `ExtractError` si l'appel échoue."""
    if client is None:
        client = genai.Client(api_key=settings.llm_api_key)

    image = Image.new("RGB", (64, 64), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    contents: list[Any] = [
        types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png"),
        _HEALTH_CHECK_PROMPT,
    ]
    _call_model(contents, client=client)
