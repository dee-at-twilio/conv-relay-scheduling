from __future__ import annotations
import json
import logging

from src.events.event_bus import event_bus
from src.events.event_types import ToolCallEvent as ToolCallUIEvent
from src.llm.llm_service import ToolCallEvent
from src.models.session import ConversationMessage, SessionState
from src.tools.registry import tool_registry

logger = logging.getLogger(__name__)


async def run_tool_executor_node(
    state: SessionState,
    tool_calls: list[ToolCallEvent],
) -> None:
    for tc in tool_calls:
        logger.info("executing tool=%s callSid=%s", tc.name, state.call_sid)
        result = await tool_registry.run(tc.name, tc.arguments, state)

        event_bus.publish(ToolCallUIEvent(
            call_sid=state.call_sid,
            tool_name=tc.name,
            arguments=tc.arguments,
            result=result.data,
            success=result.success,
        ))

        state.messages.append(ConversationMessage(
            role="tool",
            content=json.dumps(result.model_dump()),
            tool_call_id=tc.id,
            tool_name=tc.name,
        ))
