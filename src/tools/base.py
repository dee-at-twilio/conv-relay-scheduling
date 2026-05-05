from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

from src.models.session import SessionState
from src.models.tools import ToolResult


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
        return await tool.run(args, state)
