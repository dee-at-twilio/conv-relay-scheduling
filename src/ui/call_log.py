from __future__ import annotations
from collections import defaultdict

from src.events.event_bus import event_bus
from src.events.event_types import SessionEvent, ToolCallEvent, TranscriptEvent

# All events for every call seen since server start, in arrival order.
# Keyed by call_sid so the live page can replay them on load.
_log: dict[str, list] = defaultdict(list)


def get(call_sid: str) -> list:
    return list(_log[call_sid])


def get_all_call_sids() -> list[str]:
    return list(_log.keys())


def _record(event: SessionEvent | TranscriptEvent | ToolCallEvent) -> None:
    if isinstance(event, (SessionEvent, TranscriptEvent, ToolCallEvent)):
        _log[event.call_sid].append(event)


event_bus.subscribe(_record)
