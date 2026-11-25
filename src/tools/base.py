from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Any

from opentelemetry import trace

from src.models.session import SessionState
from src.models.tools import ToolResult

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema object for the tool's input parameters."""
        ...

    @abstractmethod
    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult: ...


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_openai_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    async def run(self, name: str, args: dict[str, Any], state: SessionState) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(tool_name=name, success=False, error=f"Unknown tool: {name}")
        try:
            return await tool.run(args, state)
        except Exception as exc:
            # Always hand back a result: an unanswered tool_call corrupts the history and every later
            # LLM request in the call fails with a 400.
            logger.exception("tool=%s raised callSid=%s args=%s", name, state.call_sid, args)
            trace.get_current_span().record_exception(exc)  # the tool span, when tracing
            return ToolResult(tool_name=name, success=False, error=f"Tool error: {exc}")
