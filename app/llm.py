"""Appel au modèle vision (plan, section 5C).

`extract(image_png) -> dict` est l'interface indépendante du fournisseur ;
le choix du modèle (Gemini) et son SDK restent internes à ce module, pour
pouvoir changer de fournisseur sans toucher aux appelants.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from google import genai
from google.genai import types
from google.genai.errors import ServerError

from app.settings import settings

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 5

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


def extract(image_png: bytes, *, client: _Client | None = None) -> dict:
    """Envoie l'image découpée au modèle, renvoie le JSON {periode, creneaux}.

    `client` : injection pour les tests (fournisseur factice, section 13 du
    plan) ; sans lui, construit le vrai client Gemini.

    Un `ServerError` (5xx, ex. « experiencing high demand ») déclenche un
    réessai : Google indique explicitement que ces pics sont temporaires.
    Le SDK réessaie déjà une fois en interne ; ça ne suffit pas toujours.
    """
    prompt = _PROMPT_TEMPLATE.format(today=datetime.now(UTC).date().isoformat())
    if client is None:
        client = genai.Client(api_key=settings.llm_api_key)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=settings.llm_model,
                contents=[
                    types.Part.from_bytes(data=image_png, mime_type="image/png"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(response.text)
            break
        except ServerError as exc:
            if attempt == MAX_ATTEMPTS:
                raise ExtractError(f"Appel au modèle échoué : {_redact(str(exc))}") from exc
            time.sleep(RETRY_DELAY_SECONDS)
        except Exception as exc:
            message = _redact(str(exc))
            if _is_rate_limit(message):
                raise RateLimitError(f"Quota du modèle atteint : {message}") from exc
            raise ExtractError(f"Appel au modèle échoué : {message}") from exc

    if not isinstance(data, dict) or "periode" not in data or "creneaux" not in data:
        raise ExtractError("Réponse du modèle sans periode/creneaux")

    return data
