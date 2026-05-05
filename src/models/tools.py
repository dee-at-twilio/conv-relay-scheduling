from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
