from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import config

logger = logging.getLogger(__name__)

BASE_URL = "https://memory.twilio.com/v1"


class MemoryClient:
    """Async wrapper around the Twilio Conversation Memory REST API."""

    def __init__(self, timeout: float = 10.0):
        self._auth = (config.twilio_account_sid, config.twilio_auth_token)
        self._timeout = timeout

    async def post_observations(
        self,
        store_sid: str,
        profile_id: str,
        observations: list[dict[str, Any]],
    ) -> httpx.Response:
        """observations = [{content, source, occurredAt, conversationIds}, ...] (max 10)."""
        url = f"{BASE_URL}/Services/{store_sid}/Profiles/{profile_id}/Observations"
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as c:
            resp = await c.post(url, json={"observations": observations})
        logger.info(
            "memory observations store=%s profile=%s count=%d status=%d",
            store_sid, profile_id, len(observations), resp.status_code,
        )
        return resp

    async def post_summary(
        self,
        store_sid: str,
        profile_id: str,
        conversation_id: str,
        content: str,
        source: str = "conversation_intelligence",
    ) -> httpx.Response:
        url = f"{BASE_URL}/Services/{store_sid}/Profiles/{profile_id}/ConversationSummaries"
        payload = {"conversationId": conversation_id, "content": content, "source": source}
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as c:
            resp = await c.post(url, json=payload)
        logger.info(
            "memory summary store=%s profile=%s conv=%s status=%d",
            store_sid, profile_id, conversation_id, resp.status_code,
        )
        return resp

    async def merge_traits(
        self,
        store_sid: str,
        profile_id: str,
        traits: dict[str, dict[str, Any]],
    ) -> httpx.Response:
        """Merge trait groups (e.g. {\"Contact\": {\"firstName\": \"...\"}})."""
        url = f"{BASE_URL}/Services/{store_sid}/Profiles/{profile_id}"
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as c:
            resp = await c.post(url, json={"traits": traits})
        logger.info("memory trait-merge store=%s profile=%s status=%d", store_sid, profile_id, resp.status_code)
        return resp


memory_client = MemoryClient()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
