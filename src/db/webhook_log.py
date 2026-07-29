from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any

from src.db.database import db


def already_processed(operator_result_id: str) -> bool:
    row = db.fetchone(
        "SELECT operator_result_id FROM intelligence_webhook_log WHERE operator_result_id = ?",
        (operator_result_id,),
    )
    return row is not None


def record(
    operator_result_id: str,
    conversation_id: str,
    operator_id: str,
    memory_writes: list[dict[str, Any]],
    status: str,
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO intelligence_webhook_log "
        "(operator_result_id, conversation_id, operator_id, received_at, memory_writes_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            operator_result_id,
            conversation_id,
            operator_id,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(memory_writes),
            status,
        ),
    )


def recent(limit: int = 50) -> list[dict[str, Any]]:
    rows = db.fetchall(
        "SELECT * FROM intelligence_webhook_log ORDER BY received_at DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
