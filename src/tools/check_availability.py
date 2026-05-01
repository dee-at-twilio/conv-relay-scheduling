from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from typing import Any

from src.airtable.appointment_repository import appointment_repo
from src.airtable.client import airtable_client
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

_PROVIDERS_TABLE = "Providers"

# Business hours and slot duration — adjust as needed for the demo
_SLOT_DURATION_MINS = 30
_BUSINESS_START_HOUR = 9
_BUSINESS_END_HOUR = 17
_HONORIFICS = {"dr", "dr.", "doctor", "mr", "mr.", "mrs", "mrs.", "ms", "ms."}


def _generate_slots(from_date: date, to_date: date, booked: list) -> list[dict]:
    booked_ranges = [(a.start_time, a.end_time) for a in booked]
    slots = []
    current_date = from_date

    while current_date <= to_date:
        # Skip weekends
        if current_date.weekday() < 5:
            slot_start = datetime(current_date.year, current_date.month, current_date.day, _BUSINESS_START_HOUR, 0)
            while slot_start.hour < _BUSINESS_END_HOUR:
                slot_end = slot_start + timedelta(minutes=_SLOT_DURATION_MINS)
                # Skip slots in the past
                if slot_start > datetime.now():
                    overlaps = any(
                        slot_start < b_end and slot_end > b_start
                        for b_start, b_end in booked_ranges
                    )
                    if not overlaps:
                        slots.append({
                            "start": slot_start.isoformat(),
                            "end": slot_end.isoformat(),
                        })
                slot_start = slot_end
        current_date += timedelta(days=1)

    return slots


class CheckAvailabilityTool(BaseTool):
    name = "check_availability"
    description = (
        "Check available appointment times for a provider. "
        "A provider is free unless they already have a booked appointment. "
        "Returns open 30-minute slots within business hours (Mon–Fri, 9 AM–5 PM). "
        "Call this before scheduling."
    )

    parameters = {
        "type": "object",
        "properties": {
            "provider_name": {
                "type": "string",
                "description": "Name of the provider, e.g. 'Dr. Smith'",
            },
            "from_date": {
                "type": "string",
                "description": "Start of date range, ISO format YYYY-MM-DD. Defaults to today.",
            },
            "to_date": {
                "type": "string",
                "description": "End of date range, ISO format YYYY-MM-DD. Defaults to 7 days from today.",
            },
        },
        "required": ["provider_name"],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        provider_name = args.get("provider_name", "")
        today = date.today()
        from_date = date.fromisoformat(args["from_date"]) if args.get("from_date") else today
        to_date = date.fromisoformat(args["to_date"]) if args.get("to_date") else today + timedelta(days=7)

        words = [w.lower() for w in provider_name.split() if w.lower() not in _HONORIFICS]

        logger.info("tool=%s resolving provider name=%s words=%s", self.name, provider_name, words)
        if not words:
            return ToolResult(tool_name=self.name, success=False, error=f"No provider found matching '{provider_name}'. Please ask the patient for more details.")

        if len(words) == 1:
            formula = f"SEARCH('{words[0]}',LOWER({{Name}}))"
        else:
            parts = ",".join(f"SEARCH('{w}',LOWER({{Name}}))" for w in words)
            formula = f"AND({parts})"
        records = airtable_client.get_all(_PROVIDERS_TABLE, formula)

        if not records:
            return ToolResult(tool_name=self.name, success=False, error=f"No provider found matching '{provider_name}'. Please ask the patient for more details.")

        # Multiple matches — let the LLM confirm with the patient before proceeding
        if len(records) > 1:
            candidates = [r["fields"].get("Name", "") for r in records]
            logger.info("tool=%s multiple providers matched: %s", self.name, candidates)
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=f"Multiple providers match '{provider_name}': {', '.join(candidates)}. Ask the patient which one they mean.",
            )

        provider_id = records[0]["id"]
        provider_name_resolved = records[0]["fields"].get("Name", provider_name)
        logger.info("tool=%s resolved provider='%s' id=%s from=%s to=%s", self.name, provider_name_resolved, provider_id, from_date, to_date)

        # Fetch existing booked appointments for this provider in the range
        booked = appointment_repo.get_booked_for_provider(provider_id, from_date, to_date)
        logger.info("tool=%s provider=%s has %d booked appointment(s) in range", self.name, provider_name, len(booked))

        slots = _generate_slots(from_date, to_date, booked)

        if not slots:
            return ToolResult(
                tool_name=self.name,
                success=True,
                data={"provider_id": provider_id, "slots": [], "message": f"No available slots for {provider_name_resolved} in that range."},
            )

        logger.info("tool=%s found %d open slot(s) for provider=%s", self.name, len(slots), provider_name)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={"provider_id": provider_id, "slots": slots},
        )
