from __future__ import annotations
import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: list[Callable] = []

    def subscribe(self, callback: Callable) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def publish(self, event: Any) -> None:
        for cb in list(self._subscribers):
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(event))
                else:
                    cb(event)
            except Exception:
                logger.exception("event_bus subscriber error event=%s", type(event).__name__)


event_bus = EventBus()
