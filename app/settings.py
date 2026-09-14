"""Variables d'environnement de l'application (plan, section 4).

Échoue explicitement si une variable requise est absente. Ne vérifie que la
présence, pas la validité : les clients Google et LLM se construisent à la
première utilisation, pas ici (sinon le smoke test Docker échouerait sur des
valeurs factices).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

REQUIRED_VARS = [
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "SESSION_SECRET",
    "ALLOWED_EMAILS",
    "ADMIN_EMAILS",
    "CALENDAR_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "LLM_API_KEY",
    "LLM_MODEL",
]


def _parse_emails(raw: str) -> frozenset[str]:
    return frozenset(email.strip().lower() for email in raw.split(",") if email.strip())


@dataclass(frozen=True)
class Settings:
    google_client_id: str
    google_client_secret: str
    session_secret: str
    allowed_emails: frozenset[str]
    admin_emails: frozenset[str]
    calendar_id: str
    google_service_account_json: str
    llm_api_key: str
    llm_model: str
    tz: str
    validation_weeks: int
    data_dir: str


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    missing = [name for name in REQUIRED_VARS if name not in env]
    if missing:
        raise RuntimeError("Variables d'environnement manquantes : " + ", ".join(missing))

    try:
        validation_weeks = int(env.get("VALIDATION_WEEKS", "8"))
    except ValueError as exc:
        raise RuntimeError("VALIDATION_WEEKS doit être un entier") from exc

    return Settings(
        google_client_id=env["GOOGLE_CLIENT_ID"],
        google_client_secret=env["GOOGLE_CLIENT_SECRET"],
        session_secret=env["SESSION_SECRET"],
        allowed_emails=_parse_emails(env["ALLOWED_EMAILS"]),
        admin_emails=_parse_emails(env["ADMIN_EMAILS"]),
        calendar_id=env["CALENDAR_ID"],
        google_service_account_json=env["GOOGLE_SERVICE_ACCOUNT_JSON"],
        llm_api_key=env["LLM_API_KEY"],
        llm_model=env["LLM_MODEL"],
        tz=env.get("TZ", "Europe/Paris"),
        validation_weeks=validation_weeks,
        data_dir=env.get("DATA_DIR", "./data"),
    )


settings = load_settings()
