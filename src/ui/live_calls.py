from __future__ import annotations
from collections import deque

from nicegui import ui

from src.events.event_bus import event_bus
from src.events.event_types import SessionEvent, ToolCallEvent, TranscriptEvent
from src.session.session_manager import session_manager
from src.ui.call_log import get_all_call_sids, get as get_call_log

_POLL_INTERVAL = 0.3  # seconds


def create() -> None:
    @ui.page("/calls")
    def live_calls():
        ui.label("Live Calls").classes("text-2xl font-bold mb-4")
        ui.link("← Dashboard", "/pages/").classes("text-blue-600 underline text-sm mb-4 block")

        page_container = ui.column().classes("w-full")
        no_calls_label = ui.label("No active calls.").classes("text-gray-400")

        transcript_containers: dict[str, ui.column] = {}
        tool_containers: dict[str, ui.column] = {}
        call_cards: dict[str, ui.card] = {}

        # Events from the bus land here; ui.timer drains it inside the client context
        pending: deque = deque()

        def build_call_card(call_sid: str, from_number: str) -> None:
            no_calls_label.set_visibility(False)
            with page_container:
                with ui.card().classes("w-full mb-4") as card:
                    call_cards[call_sid] = card
                    with ui.row().classes("w-full items-center justify-between mb-2"):
                        ui.label(f"Call {call_sid[-8:]}  ·  {from_number}").classes("font-semibold text-base")
                        with ui.row().classes("gap-2"):
                            lang_select = ui.select(
                                {"en-US": "English", "es-US": "Spanish"},
                                value="en-US",
                                label="Language",
                            ).classes("w-32")
                            async def on_lang_change(e, cid=call_sid, sel=lang_select):
                                sender = session_manager.get_sender(cid)
                                if sender:
                                    await sender.switch_language(sel.value, sel.value)
                            lang_select.on("update:model-value", on_lang_change)
                            async def on_end_call(cid=call_sid):
                                sender = session_manager.get_sender(cid)
                                if sender:
                                    await sender.end_session()
                            ui.button("End Call", on_click=on_end_call).classes("bg-red-500 text-white text-xs")
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("flex-1"):
                            ui.label("Transcript").classes("font-medium text-xs text-gray-500 uppercase mb-1")
                            with ui.scroll_area().classes("h-64 border rounded p-2"):
                                transcript_containers[call_sid] = ui.column().classes("w-full")
                        with ui.column().classes("flex-1"):
                            ui.label("Tool Calls").classes("font-medium text-xs text-gray-500 uppercase mb-1")
                            with ui.scroll_area().classes("h-64 border rounded p-2"):
                                tool_containers[call_sid] = ui.column().classes("w-full")

        def remove_call_card(call_sid: str) -> None:
            card = call_cards.pop(call_sid, None)
            if card:
                card.delete()
            transcript_containers.pop(call_sid, None)
            tool_containers.pop(call_sid, None)
            if not call_cards:
                no_calls_label.set_visibility(True)

        def add_transcript_line(call_sid: str, role: str, text: str) -> None:
            col = transcript_containers.get(call_sid)
            if not col:
                return
            color = "text-blue-700" if role == "user" else "text-gray-800"
            prefix = "Patient: " if role == "user" else "Agent: "
            with col:
                ui.label(f"{prefix}{text}").classes(f"text-sm {color} py-0.5")

        def add_tool_line(call_sid: str, tool_name: str, success: bool | None, result: dict | None) -> None:
            col = tool_containers.get(call_sid)
            if not col:
                return
            status = "✓" if success else "✗"
            color = "text-green-700" if success else "text-red-700"
            summary = str(result)[:120] if result else ""
            with col:
                ui.label(f"{status} {tool_name}  {summary}").classes(f"text-xs font-mono {color} py-0.5")

        # Seed from the in-memory log so history survives page navigation
        for call_sid in get_all_call_sids():
            for event in get_call_log(call_sid):
                if isinstance(event, SessionEvent) and event.event == "started":
                    build_call_card(call_sid, event.from_number)
                elif isinstance(event, TranscriptEvent):
                    if call_sid not in transcript_containers:
                        build_call_card(call_sid, "")
                    add_transcript_line(call_sid, event.role, event.text)
                elif isinstance(event, ToolCallEvent):
                    add_tool_line(call_sid, event.tool_name, event.success, event.result)

        def on_event(event: SessionEvent | TranscriptEvent | ToolCallEvent) -> None:
            pending.append(event)

        def drain() -> None:
            while pending:
                event = pending.popleft()
                if isinstance(event, SessionEvent):
                    if event.event == "started":
                        build_call_card(event.call_sid, event.from_number)
                    elif event.event == "ended":
                        remove_call_card(event.call_sid)
                elif isinstance(event, TranscriptEvent):
                    if event.call_sid not in transcript_containers:
                        build_call_card(event.call_sid, "")
                    add_transcript_line(event.call_sid, event.role, event.text)
                elif isinstance(event, ToolCallEvent):
                    add_tool_line(event.call_sid, event.tool_name, event.success, event.result)

        event_bus.subscribe(on_event)
        ui.timer(_POLL_INTERVAL, drain)
        ui.context.client.on_disconnect(lambda: event_bus.unsubscribe(on_event))
