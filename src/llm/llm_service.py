from __future__ import annotations
import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from src.config import config

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=config.openai_api_key)


async def stream_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    interrupted: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """
    Yields text tokens from the LLM. Stops early if `interrupted` is set.
    Tool calls are not handled here — this milestone is text-only.
    """
    kwargs: dict[str, Any] = {
        "model": config.openai_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools

    stream = await _client.chat.completions.create(**kwargs)

    async for chunk in stream:
        if interrupted and interrupted.is_set():
            logger.info("stream interrupted — stopping early")
            await stream.close()
            return

        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
