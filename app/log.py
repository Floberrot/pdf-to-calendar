"""Journal d'import : table `imports` en ajout seul (plan, section 7).

Règle : jamais de contenu de PDF ni d'image dans `detail`, jamais de secret.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from app.db import transaction

Step = Literal["login", "upload", "locate", "crop", "llm", "validate", "write"]
Status = Literal["ok", "error", "denied"]


def log(
    *,
    request_id: str,
    email: str,
    step: Step,
    status: Status,
    detail: dict[str, Any] | None = None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO imports (ts, request_id, email, step, status, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                request_id,
                email,
                step,
                status,
                json.dumps(detail) if detail is not None else None,
            ),
        )
