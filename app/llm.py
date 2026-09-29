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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from google import genai
from google.genai import types
from google.genai.errors import ServerError
from PIL import Image

from app.settings import settings

logger = logging.getLogger("app")

# Pauses entre deux tentatives sur un même modèle saturé (5xx) : un pic de
# demande dure souvent plus de quelques secondes, la dernière attend donc plus.
RETRY_DELAYS_SECONDS = (5, 15)
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1

_HEALTH_CHECK_PROMPT = 'Réponds uniquement avec cet objet JSON, sans rien ajouter : {"ok": true}'

_PROMPT_TEMPLATE = """Tu lis un planning de travail dans cette image, découpée \
autour d'une seule personne. Elle contient un ou plusieurs blocs empilés de \
haut en bas (un par tableau, par exemple une semaine chacun), de l'une de ces \
deux formes :
- une bande d'en-tête avec les dates, au-dessus de la ligne de la personne ;
- une colonne de dates à gauche, à côté de la colonne de la personne.
La zone noire masque son nom : ignore-la.

Date d'aujourd'hui : {today}.

Les dates peuvent être écrites de bien des façons (« Lun 14 », « 14/09 », \
« 14.09.26 », « 2026-09-14 », « lundi 14 septembre », « L 14 », « Mon 14 », \
numéro seul…). Si l'année (ou le mois) n'est écrite nulle part, prends celle \
qui place ces dates au plus près d'aujourd'hui, le plus souvent l'année en \
cours, et vérifie que les jours de la semaine écrits tombent bien ces jours-là \
(ex. « Lun 05/10 » : l'année où le 5 octobre est un lundi).

Renvoie uniquement un objet JSON avec :
- "annee_visible" : true si l'année est écrite quelque part dans l'image, \
false si tu l'as déduite.
- "periode" : {{"debut": "AAAA-MM-JJ", "fin": "AAAA-MM-JJ"}}, de la première à \
la dernière date de l'ensemble des en-têtes (pas la plage des créneaux : un \
jour de repos en fin de semaine doit être inclus).
- "creneaux" : liste d'objets {{"date": "AAAA-MM-JJ", "debut": "HH:MM", \
"fin": "HH:MM", "lieu": "..."}}, un par créneau de travail de la personne. \
Convertis toujours les heures en HH:MM (« 9h » → "09:00", « 8h30 » → \
"08:30"). "lieu" est une chaîne vide si rien n'est indiqué. Un jour sans \
créneau n'apparaît pas dans la liste. Une même journée peut avoir plusieurs \
créneaux (coupure, ex. « 7h-12h 13h-16h » ou deux lignes dans la case). Si un \
créneau se termine après minuit, indique l'heure de fin telle qu'écrite sur le \
planning (ex. 21:00 à 07:00), sans changer la date.
- "jours_incertains" : liste d'objets {{"date": "AAAA-MM-JJ", "texte": "..."}}, \
un par jour où la case n'est ni vide, ni un horaire clair, ni une mention \
habituelle de repos ou d'absence (congé, RTT, repos, récupération, arrêt \
maladie...) — par exemple un code de poste (« M », « S », « N ») sans légende \
visible qui donne ses horaires. "texte" reprend ce qui est écrit dans la case, \
tel quel, sans l'interpréter. Ces jours n'apparaissent jamais dans "creneaux"."""


class ExtractError(Exception):
    """Levée quand l'appel au modèle échoue ou renvoie un JSON inexploitable."""


class ModelUnavailableError(ExtractError):
    """Aucun modèle de LLM_MODELS n'a pu répondre : tous saturés (5xx
    persistant, quota) ou introuvables (404, retirés par Google). L'image
    n'y est pour rien et réessayer plus tard peut marcher, contrairement à
    une réponse illisible."""


class RateLimitError(ModelUnavailableError):
    """Levée quand le quota de l'API (tier gratuit) est atteint : inutile de
    réessayer tout de suite, contrairement à un `ServerError` transitoire."""


class _ModelUnavailable(Exception):
    """Interne à ce module : ce modèle est saturé (quota ou 5xx persistant)
    ou introuvable (404), le suivant de la chaîne peut prendre le relais."""

    def __init__(self, message: str, *, rate_limited: bool, not_found: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited
        self.not_found = not_found


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


def _is_model_not_found(message: str) -> bool:
    """404 NOT_FOUND : nom de modèle faux ou modèle retiré par Google. Ne
    concerne que ce modèle-là, contrairement à une clé invalide."""
    return message.startswith("404") or "NOT_FOUND" in message


def _call_one_model(model: str, contents: list[Any], *, client: _Client) -> dict:
    """Appelle un modèle, renvoie le JSON de la réponse.

    Un `ServerError` (5xx, ex. « experiencing high demand ») déclenche un
    réessai : Google indique explicitement que ces pics sont temporaires.
    Le SDK réessaie déjà une fois en interne ; ça ne suffit pas toujours.
    Un 5xx persistant, un quota dépassé (429) ou un modèle introuvable (404)
    lèvent `_ModelUnavailable` : inutile d'insister sur ce modèle, mais un
    autre peut répondre.
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
            time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
        except Exception as exc:
            message = _redact(str(exc))
            if _is_rate_limit(message):
                raise _ModelUnavailable(message, rate_limited=True) from exc
            if _is_model_not_found(message):
                raise _ModelUnavailable(message, rate_limited=False, not_found=True) from exc
            raise ExtractError(f"Appel au modèle échoué : {message}") from exc

    raise ExtractError("Appel au modèle échoué : aucune tentative effectuée")


def _call_model(contents: list[Any], *, client: _Client) -> dict:
    """Essaie chaque modèle de `settings.llm_models` dans l'ordre, passe au
    suivant dès que l'un est saturé (quota 429, ou 5xx persistant) ou
    introuvable (404, nom faux dans LLM_MODELS).

    Retour utilisateur : « le modèle plante car trop de demandes ». Les quotas
    du tier gratuit sont comptés par modèle : quand le premier est à sec, le
    suivant a encore les siens. Toute autre erreur (clé invalide, requête
    refusée, JSON illisible) remonte tout de suite sans changer de modèle :
    elle n'a rien à voir avec un modèle en particulier et se reproduirait à
    l'identique sur les suivants.
    """
    failures: list[str] = []
    only_rate_limits = True
    for model in settings.llm_models:
        try:
            data = _call_one_model(model, contents, client=client)
        except _ModelUnavailable as exc:
            failures.append(f"{model} : {exc}")
            only_rate_limits = only_rate_limits and exc.rate_limited
            if exc.not_found:
                logger.warning(
                    "Modèle %s introuvable chez Google (nom faux ou modèle retiré) : "
                    "le retirer de LLM_MODELS. %s",
                    model,
                    exc,
                )
            else:
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
    raise ModelUnavailableError(f"{prefix} : {detail}")


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


@dataclass(frozen=True)
class ModelHealth:
    model: str
    ok: bool
    detail: str


def health_check(*, client: _Client | None = None) -> list[ModelHealth]:
    """Test de santé (page admin, plan section 4) : image minimale, prompt
    trivial indépendant du schéma periode/creneaux, pour ne pas dépendre de
    la capacité du modèle à reconnaître un vrai planning dans une image de
    test.

    Chaque modèle de LLM_MODELS est testé séparément, sans repli : avec le
    repli, un nom faux en deuxième position passerait inaperçu tant que le
    premier répond, et ne se révélerait que le jour où il sature."""
    if client is None:
        client = genai.Client(api_key=settings.llm_api_key)

    image = Image.new("RGB", (64, 64), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    contents: list[Any] = [
        types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png"),
        _HEALTH_CHECK_PROMPT,
    ]
    results = []
    for model in settings.llm_models:
        try:
            _call_one_model(model, contents, client=client)
        except (_ModelUnavailable, ExtractError) as exc:
            results.append(ModelHealth(model, ok=False, detail=str(exc)))
        else:
            results.append(ModelHealth(model, ok=True, detail="réponse JSON reçue"))
    return results
