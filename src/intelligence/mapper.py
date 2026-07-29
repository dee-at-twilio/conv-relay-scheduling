from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Twilio-authored operator IDs (from twilio-conversation-intelligence skill)
OP_SENTIMENT = "intelligence_operator_01kcrvw16kfa88qvgrfmr7y151"
OP_SUMMARY = "intelligence_operator_01kcv35pnkeysaf6z6cqtbpegn"
OP_SCRIPT_ADHERENCE = "intelligence_operator_01kf34tcyefpyb1t4m0nbd8rxg"
OP_NEXT_BEST_RESPONSE = "intelligence_operator_01kea27sy7ffsafmtsfp17nzx4"


@dataclass
class MemoryWrite:
    kind: Literal["observation", "summary", "trait"]
    payload: dict[str, Any]


def to_memory_writes(operator_result: dict, conversation_id: str, occurred_at: str) -> list[MemoryWrite]:
    """Map one operator result to zero or more Memory API writes.

    - Twilio Summary → ConversationSummary
    - Twilio Sentiment / Script Adherence → Observation with a labeled prefix
    - Twilio Next Best Response → skip (coaching, not memory)
    - Custom TEXT/CLASSIFICATION/JSON → Observation with the display name as prefix
    """
    op = operator_result.get("operator") or {}
    op_id = op.get("id") or ""
    op_name = op.get("displayName") or "Operator"
    output_format = operator_result.get("outputFormat")
    result = operator_result.get("result") or {}

    if op_id == OP_NEXT_BEST_RESPONSE:
        return []

    if op_id == OP_SUMMARY and output_format == "TEXT":
        text = (result.get("text") or "").strip()
        if not text:
            return []
        return [MemoryWrite(
            kind="summary",
            payload={"conversationId": conversation_id, "content": text, "source": "conversation_intelligence"},
        )]

    content = _render_content(op_name, output_format, result)
    if not content:
        return []
    return [MemoryWrite(
        kind="observation",
        payload={
            "content": content,
            "source": "conversation_intelligence",
            "occurredAt": occurred_at,
            "conversationIds": [conversation_id],
        },
    )]


def _render_content(op_name: str, output_format: str | None, result: dict) -> str:
    if output_format == "CLASSIFICATION":
        label = result.get("label")
        return f"{op_name}: {label}" if label else ""
    if output_format == "TEXT":
        text = (result.get("text") or "").strip()
        return f"{op_name}: {text}" if text else ""
    if output_format == "JSON":
        return f"{op_name}: {json.dumps(result, separators=(',', ':'))}"
    if output_format == "EXTRACTION":
        entities = result.get("entities") or []
        if not entities:
            return ""
        parts = [f"{e.get('label')}={e.get('text')}" for e in entities if e.get("text")]
        return f"{op_name}: {', '.join(parts)}" if parts else ""
    logger.info("mapper: unhandled outputFormat=%r op=%s — skipping", output_format, op_name)
    return ""
