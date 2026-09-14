"""Connexion SQLite et schéma (plan, section 7).

Non listé dans l'arborescence de la section 9, ajouté pour centraliser la
connexion partagée entre `log.py` et `auth.py` (voir description de la PR).
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  email       TEXT PRIMARY KEY,
  pdf_name    TEXT,
  last_crop   TEXT,
  created_at  TEXT NOT NULL,
  last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS imports (
  id          INTEGER PRIMARY KEY,
  ts          TEXT NOT NULL,
  request_id  TEXT NOT NULL,
  email       TEXT NOT NULL,
  step        TEXT NOT NULL,
  status      TEXT NOT NULL,
  detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_imports_ts ON imports(ts DESC);
"""


def db_path() -> Path:
    return Path(settings.data_dir) / "app.db"


def get_connection() -> sqlite3.Connection:
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


@contextlib.contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Connexion à usage unique : commit/rollback automatique, puis fermeture."""
    conn = get_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with transaction() as conn:
        conn.executescript(SCHEMA)


def purge_old_imports(days: int = 90) -> None:
    """Purge les lignes de plus de `days` jours (plan, section 7)."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with transaction() as conn:
        conn.execute("DELETE FROM imports WHERE ts < ?", (cutoff,))


def upsert_user_seen(email: str) -> None:
    """Crée l'utilisateur au besoin et met à jour `last_seen` (appelé au login)."""
    now = datetime.now(UTC).isoformat()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO users (email, created_at, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET last_seen = excluded.last_seen
            """,
            (email, now, now),
        )
