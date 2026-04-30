from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import AsyncOpenAI

from src.config import config

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=config.openai_api_key)


@dataclass
class TextToken:
    type: Literal["text"] = "text"
    content: str = ""


@dataclass
class ToolCallEvent:
    type: Literal["tool_call"] = "tool_call"
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)


async def stream_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    interrupted: asyncio.Event | None = None,
) -> AsyncIterator[TextToken | ToolCallEvent]:
    """
    Yields TextToken for streamed text and ToolCallEvent when the LLM calls a tool.
    Stops early if `interrupted` is set (text tokens only — tool calls always complete).
    """
    kwargs: dict[str, Any] = {
        "model": config.openai_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools

    stream = await _client.chat.completions.create(**kwargs)

    # Accumulate tool call chunks — OpenAI streams them in pieces
    tool_calls: dict[int, dict] = {}

    async for chunk in stream:
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        finish_reason = chunk.choices[0].finish_reason

        # Stream text tokens
        if delta.content:
            if interrupted and interrupted.is_set():
                logger.info("stream interrupted — stopping early")
                await stream.close()
                return
            yield TextToken(content=delta.content)

        # Accumulate tool call deltas
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                if tc.id:
                    tool_calls[idx]["id"] = tc.id
                if tc.function.name:
                    tool_calls[idx]["name"] = tc.function.name
                if tc.function.arguments:
                    tool_calls[idx]["arguments"] += tc.function.arguments

        # Emit complete tool calls when the stream signals tool_calls finish
        if finish_reason == "tool_calls":
            for tc in tool_calls.values():
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                logger.info("tool call name=%s id=%s args=%s", tc["name"], tc["id"], args)
                yield ToolCallEvent(id=tc["id"], name=tc["name"], arguments=args)
