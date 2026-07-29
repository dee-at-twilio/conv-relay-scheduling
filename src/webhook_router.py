from __future__ import annotations
import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request
from twilio.request_validator import RequestValidator

from src.config import config
from src.db import webhook_log
from src.intelligence.mapper import MemoryWrite, to_memory_writes
from src.memory.memory_client import memory_client, utc_now_iso

logger = logging.getLogger(__name__)

router = APIRouter()

_validator = RequestValidator(config.twilio_auth_token)


@router.post("/webhook")
async def rule_execution(
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
):
    body_bytes = await request.body()
    url = str(request.url)

    if not _verify_signature(url, body_bytes, x_twilio_signature):
        logger.warning("webhook: bad or missing signature url=%s", url)
        raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(body_bytes)
        logger.info(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    await _dispatch(payload)
    return {"status": "ok"}


def _verify_signature(url: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    # Twilio JSON webhooks: URL carries ?bodySHA256=<hex>. The SDK only
    # takes the body-hash branch when the 2nd arg is a str — pass decoded body.
    try:
        return _validator.validate(url, body.decode("utf-8"), signature)
    except Exception:
        logger.exception("signature validator raised")
        return False


async def _dispatch(payload: dict) -> None:
    conversation_id = payload.get("conversationId") or ""
    operator_results = payload.get("operatorResults") or []
    logger.info(
        "webhook received conversation=%s operator_results=%d config=%s",
        conversation_id, len(operator_results),
        (payload.get("intelligenceConfiguration") or {}).get("id"),
    )

    for op_result in operator_results:
        result_id = op_result.get("id") or ""
        operator_id = (op_result.get("operator") or {}).get("id") or ""

        if not result_id:
            logger.warning("webhook: missing operator result id — skipping")
            continue

        if webhook_log.already_processed(result_id):
            logger.info("webhook: %s already processed — skipping", result_id)
            continue

        writes = to_memory_writes(op_result, conversation_id, utc_now_iso())
        status = "ok"
        try:
            await _apply_writes(op_result, writes)
        except Exception:
            logger.exception("webhook: write failed result_id=%s", result_id)
            status = "error"

        webhook_log.record(
            operator_result_id=result_id,
            conversation_id=conversation_id,
            operator_id=operator_id,
            memory_writes=[asdict(w) for w in writes],
            status=status,
        )


async def _apply_writes(op_result: dict, writes: list[MemoryWrite]) -> None:
    if not writes:
        return

    resolved = ((op_result.get("executionDetails") or {}).get("resolvedContext") or {}).get("memory") or {}
    profile_id = resolved.get("profileId")
    store_sid = resolved.get("memoryStoreId") or config.memory_store_sid
    if not profile_id or not store_sid:
        logger.warning(
            "webhook: cannot resolve memory target profile=%r store=%r — skipping writes",
            profile_id, store_sid,
        )
        return

    observations = [w.payload for w in writes if w.kind == "observation"]
    if observations:
        for batch_start in range(0, len(observations), 10):
            batch = observations[batch_start:batch_start + 10]
            resp = await memory_client.post_observations(store_sid, profile_id, batch)
            resp.raise_for_status()

    for w in writes:
        if w.kind == "summary":
            resp = await memory_client.post_summary(
                store_sid,
                profile_id,
                w.payload["conversationId"],
                w.payload["content"],
                source=w.payload.get("source", "conversation_intelligence"),
            )
            resp.raise_for_status()
        elif w.kind == "trait":
            resp = await memory_client.merge_traits(store_sid, profile_id, w.payload)
            resp.raise_for_status()
