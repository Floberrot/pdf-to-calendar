"""Connexion SQLite et schéma (plan, section 7).

Non listé dans l'arborescence de la section 9, ajouté pour centraliser la
connexion partagée entre `log.py` et `auth.py` (voir description de la PR).
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
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


def get_pdf_name(email: str) -> str | None:
    with transaction() as conn:
        row = conn.execute("SELECT pdf_name FROM users WHERE email = ?", (email,)).fetchone()
    return row["pdf_name"] if row else None


def set_pdf_name(email: str, pdf_name: str | None) -> None:
    """Enregistre le nom recherché ; vide ou None l'efface.

    Crée la ligne au besoin : elle n'était créée qu'à la connexion Google,
    alors qu'une session peut lui survivre (cookie toujours valide après un
    redéploiement sur une base neuve). Un simple UPDATE ne trouvait alors
    aucune ligne et n'enregistrait rien, sans erreur — retour utilisateur :
    « jamais reconnu même quand on save »."""
    now = datetime.now(UTC).isoformat()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO users (email, pdf_name, created_at, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET pdf_name = excluded.pdf_name
            """,
            (email, pdf_name or None, now, now),
        )


def get_last_crop(email: str) -> dict | None:
    with transaction() as conn:
        row = conn.execute("SELECT last_crop FROM users WHERE email = ?", (email,)).fetchone()
    if row is None or row["last_crop"] is None:
        return None
    return json.loads(row["last_crop"])


def set_last_crop(email: str, crop: dict) -> None:
    """Crée la ligne au besoin, pour la même raison que `set_pdf_name`."""
    now = datetime.now(UTC).isoformat()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO users (email, last_crop, created_at, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET last_crop = excluded.last_crop
            """,
            (email, json.dumps(crop), now, now),
        )


def list_users() -> list[sqlite3.Row]:
    """Les comptes connus et leur nom enregistré (page admin)."""
    with transaction() as conn:
        return conn.execute(
            "SELECT email, pdf_name, last_seen FROM users ORDER BY last_seen DESC, email"
        ).fetchall()


@dataclass(frozen=True)
class StorageStatus:
    """Où vit la base, et si elle survit aux redéploiements : sans volume
    monté, elle est dans le système de fichiers du conteneur, recréé à chaque
    déploiement — profils et journal compris."""

    path: Path
    mount_point: Path | None


def _mount_point(path: Path) -> Path | None:
    """Premier dossier monté (un volume) qui contient `path`, racine exclue."""
    for candidate in (path, *path.parents):
        if candidate == Path(candidate.anchor):
            return None
        if os.path.ismount(candidate):
            return candidate
    return None


def storage_status() -> StorageStatus:
    path = db_path().resolve()
    return StorageStatus(path=path, mount_point=_mount_point(path.parent))


def list_recent_imports(*, email: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
    """Les dernières lignes du journal, plus récentes d'abord (plan, section 7)."""
    query = "SELECT * FROM imports"
    params: list[str | int] = []
    if email:
        query += " WHERE email = ?"
        params.append(email)
    query += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with transaction() as conn:
        return conn.execute(query, params).fetchall()
