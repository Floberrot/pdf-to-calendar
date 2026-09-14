from __future__ import annotations

import json

from app.db import purge_old_imports, transaction, upsert_user_seen
from app.log import log


def test_upsert_user_seen_creates_then_updates():
    upsert_user_seen("upsert@example.com")
    upsert_user_seen("upsert@example.com")
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE email = ?", ("upsert@example.com",)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["created_at"] is not None
    assert rows[0]["last_seen"] is not None


def test_log_inserts_row_with_json_detail():
    log(request_id="req-1", email="ami@example.com", step="login", status="ok", detail={"n": 1})
    with transaction() as conn:
        row = conn.execute("SELECT * FROM imports WHERE request_id = ?", ("req-1",)).fetchone()
    assert row is not None
    assert row["step"] == "login"
    assert row["status"] == "ok"
    assert json.loads(row["detail"]) == {"n": 1}


def test_log_without_detail_stores_null():
    log(request_id="req-2", email="ami@example.com", step="login", status="denied")
    with transaction() as conn:
        row = conn.execute("SELECT * FROM imports WHERE request_id = ?", ("req-2",)).fetchone()
    assert row["detail"] is None


def test_purge_old_imports_removes_only_old_rows():
    with transaction() as conn:
        conn.execute(
            "INSERT INTO imports (ts, request_id, email, step, status, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2000-01-01T00:00:00+00:00", "req-old", "ami@example.com", "login", "ok", None),
        )
    log(request_id="req-recent", email="ami@example.com", step="login", status="ok")

    purge_old_imports(days=90)

    with transaction() as conn:
        old = conn.execute("SELECT * FROM imports WHERE request_id = ?", ("req-old",)).fetchone()
        recent = conn.execute(
            "SELECT * FROM imports WHERE request_id = ?", ("req-recent",)
        ).fetchone()
    assert old is None
    assert recent is not None
