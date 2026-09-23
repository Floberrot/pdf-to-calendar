"""Appel au modèle vision (plan, section 5C).

`extract(image_png) -> dict` est l'interface indépendante du fournisseur ;
le choix du modèle (Gemini) et son SDK restent internes à ce module, pour
pouvoir changer de fournisseur sans toucher aux appelants.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from google import genai
from google.genai import types
from google.genai.errors import ServerError
from PIL import Image

from app.settings import settings

logger = logging.getLogger("app")

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
telle qu'écrite sur le planning (ex. 21:00 à 07:00), sans changer la date.
- "jours_incertains" : liste d'objets {{"date": "AAAA-MM-JJ", "texte": "..."}}, \
un par jour où la case n'est ni vide, ni un horaire clair (HH:MM-HH:MM), ni une \
mention habituelle de repos ou d'absence (congé, RTT, repos, récupération, \
arrêt maladie...). "texte" reprend ce qui est écrit dans la case, tel quel, \
sans l'interpréter. Ces jours n'apparaissent jamais dans "creneaux"."""


class ExtractError(Exception):
    """Levée quand l'appel au modèle échoue ou renvoie un JSON inexploitable."""


class RateLimitError(ExtractError):
    """Levée quand le quota de l'API (tier gratuit) est atteint : inutile de
    réessayer tout de suite, contrairement à un `ServerError` transitoire."""


class _ModelUnavailable(Exception):
    """Interne à ce module : ce modèle est saturé (quota ou 5xx persistant),
    le suivant de la chaîne peut prendre le relais."""

    def __init__(self, message: str, *, rate_limited: bool):
        super().__init__(message)
        self.rate_limited = rate_limited


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


def _call_one_model(model: str, contents: list[Any], *, client: _Client) -> dict:
    """Appelle un modèle, renvoie le JSON de la réponse.

    Un `ServerError` (5xx, ex. « experiencing high demand ») déclenche un
    réessai : Google indique explicitement que ces pics sont temporaires.
    Le SDK réessaie déjà une fois en interne ; ça ne suffit pas toujours.
    Un 5xx persistant ou un quota dépassé (429) lèvent `_ModelUnavailable` :
    inutile d'insister sur ce modèle, mais un autre peut répondre.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            return json.loads(response.text)
        except ServerError as exc:
            if attempt == MAX_ATTEMPTS:
                raise _ModelUnavailable(_redact(str(exc)), rate_limited=False) from exc
            time.sleep(RETRY_DELAY_SECONDS)
        except Exception as exc:
            message = _redact(str(exc))
            if _is_rate_limit(message):
                raise _ModelUnavailable(message, rate_limited=True) from exc
            raise ExtractError(f"Appel au modèle échoué : {message}") from exc

    raise ExtractError("Appel au modèle échoué : aucune tentative effectuée")


def _call_model(contents: list[Any], *, client: _Client) -> dict:
    """Essaie chaque modèle de `settings.llm_models` dans l'ordre, passe au
    suivant dès que l'un est saturé (quota 429, ou 5xx persistant).

    Retour utilisateur : « le modèle plante car trop de demandes ». Les quotas
    du tier gratuit sont comptés par modèle : quand le premier est à sec, le
    suivant a encore les siens. Toute autre erreur (clé invalide, requête
    refusée, JSON illisible) remonte tout de suite sans changer de modèle :
    elle n'a rien à voir avec la charge et se reproduirait à l'identique.
    """
    failures: list[str] = []
    only_rate_limits = True
    for model in settings.llm_models:
        try:
            data = _call_one_model(model, contents, client=client)
        except _ModelUnavailable as exc:
            failures.append(f"{model} : {exc}")
            only_rate_limits = only_rate_limits and exc.rate_limited
            logger.warning("Modèle %s indisponible : %s", model, exc)
            continue
        if failures:
            logger.info("Réponse obtenue du modèle de repli %s", model)
        return data

    detail = " ; ".join(failures)
    several = len(failures) > 1
    if only_rate_limits:
        prefix = "Quota atteint sur tous les modèles" if several else "Quota du modèle atteint"
        raise RateLimitError(f"{prefix} : {detail}")
    prefix = "Appel échoué sur tous les modèles" if several else "Appel au modèle échoué"
    raise ExtractError(f"{prefix} : {detail}")


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
