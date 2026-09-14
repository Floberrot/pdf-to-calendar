"""Variables d'environnement factices, communes à tous les tests.

Doit s'exécuter avant tout import de `app.*` : pytest importe les conftest.py
avant les modules de test, donc ce module-ci fixe l'environnement en premier.
Aucune valeur ici n'est un vrai secret (interdits, CLAUDE.md).
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("ALLOWED_EMAILS", "ami@example.com")
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
os.environ.setdefault("CALENDAR_ID", "test@group.calendar.google.com")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pdf2cal-test-"))

import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    from app.db import init_db

    init_db()
