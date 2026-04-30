from __future__ import annotations
from nicegui import ui

from src.events.event_bus import event_bus
from src.events.event_types import SessionEvent
from src.session.session_manager import session_manager


def create() -> None:
    @ui.page("/")
    def dashboard():
        ui.label("Scheduling Agent").classes("text-2xl font-bold mb-4")

        with ui.row().classes("gap-8 mb-6"):
            active_label = ui.label()

        def refresh_count():
            count = len(session_manager._sessions)
            active_label.set_text(f"Active calls: {count}")

        # Refresh on session events
        def on_session(event: SessionEvent):
            refresh_count()

        event_bus.subscribe(on_session)
        ui.timer(2.0, refresh_count)
        refresh_count()

        with ui.row().classes("gap-4"):
            ui.link("Live Calls", "/calls").classes("text-blue-600 underline text-sm")
