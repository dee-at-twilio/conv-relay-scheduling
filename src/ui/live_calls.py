from __future__ import annotations
from nicegui import ui

from src.events.event_bus import event_bus
from src.events.event_types import SessionEvent, ToolCallEvent, TranscriptEvent
from src.session.session_manager import session_manager

# Per-call UI state held in process memory — keyed by call_sid
_transcript_containers: dict[str, ui.column] = {}
_tool_containers: dict[str, ui.column] = {}
_call_cards: dict[str, ui.card] = {}


def _add_transcript_line(call_sid: str, role: str, text: str) -> None:
    container = _transcript_containers.get(call_sid)
    if not container:
        return
    color = "text-blue-700" if role == "user" else "text-gray-800"
    prefix = "Patient: " if role == "user" else "Agent: "
    with container:
        ui.label(f"{prefix}{text}").classes(f"text-sm {color} py-0.5")


def _add_tool_line(call_sid: str, tool_name: str, success: bool | None, result: dict | None) -> None:
    container = _tool_containers.get(call_sid)
    if not container:
        return
    status = "✓" if success else "✗"
    color = "text-green-700" if success else "text-red-700"
    summary = str(result)[:120] if result else ""
    with container:
        ui.label(f"{status} {tool_name}  {summary}").classes(f"text-xs font-mono {color} py-0.5")


def _build_call_card(call_sid: str, from_number: str, page_container: ui.element) -> None:
    with page_container:
        with ui.card().classes("w-full mb-4") as card:
            _call_cards[call_sid] = card
            ui.label(f"Call {call_sid[-8:]}  ·  {from_number}").classes("font-semibold text-base mb-2")

            with ui.row().classes("w-full gap-4"):
                with ui.column().classes("flex-1"):
                    ui.label("Transcript").classes("font-medium text-xs text-gray-500 uppercase mb-1")
                    with ui.scroll_area().classes("h-64 border rounded p-2"):
                        col = ui.column().classes("w-full")
                        _transcript_containers[call_sid] = col

                with ui.column().classes("flex-1"):
                    ui.label("Tool Calls").classes("font-medium text-xs text-gray-500 uppercase mb-1")
                    with ui.scroll_area().classes("h-64 border rounded p-2"):
                        col = ui.column().classes("w-full")
                        _tool_containers[call_sid] = col


def _remove_call_card(call_sid: str) -> None:
    card = _call_cards.pop(call_sid, None)
    if card:
        card.delete()
    _transcript_containers.pop(call_sid, None)
    _tool_containers.pop(call_sid, None)


def create() -> None:
    @ui.page("/calls")
    def live_calls():
        ui.label("Live Calls").classes("text-2xl font-bold mb-4")
        ui.link("← Dashboard", "/pages/").classes("text-blue-600 underline text-sm mb-4 block")

        page_container = ui.column().classes("w-full")
        no_calls_label = ui.label("No active calls.").classes("text-gray-400")

        # Seed any calls already active when the page loads
        for call_sid, state in list(session_manager._sessions.items()):
            no_calls_label.set_visibility(False)
            _build_call_card(call_sid, state.from_number, page_container)
            for msg in state.messages:
                if msg.role in ("user", "assistant") and not (msg.content or "").startswith('{"tool_calls":'):
                    _add_transcript_line(call_sid, msg.role, msg.content or "")

        def on_event(event: SessionEvent | TranscriptEvent | ToolCallEvent):
            if isinstance(event, SessionEvent):
                if event.event == "started":
                    no_calls_label.set_visibility(False)
                    _build_call_card(event.call_sid, event.from_number, page_container)
                elif event.event == "ended":
                    _remove_call_card(event.call_sid)
                    if not _call_cards:
                        no_calls_label.set_visibility(True)

            elif isinstance(event, TranscriptEvent):
                _add_transcript_line(event.call_sid, event.role, event.text)

            elif isinstance(event, ToolCallEvent):
                _add_tool_line(event.call_sid, event.tool_name, event.success, event.result)

        event_bus.subscribe(on_event)

        # Unsubscribe when the page is closed so we don't accumulate dead subscribers
        ui.context.client.on_disconnect(lambda: event_bus.unsubscribe(on_event))
