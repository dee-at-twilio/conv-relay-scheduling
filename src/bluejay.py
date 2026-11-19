import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from twilio.rest import Client

from src.config import config

logger = logging.getLogger(__name__)
router = APIRouter()

BLUEJAY_TRACES_URL = "https://otlp.getbluejay.ai/v1/traces"
BLUEJAY_LINK_URL = "https://api.getbluejay.ai/v1/update-simulation-result"

# Tool calls and metadata recorded during each call, keyed by call SID; sent when the call is linked.
_recorded: dict[str, dict] = {}
# Test calls this app placed: idempotency key -> call SID, and call SID -> (result ID, idempotency key).
_triggers: dict[str, str] = {}
_simulations: dict[str, tuple[str, str]] = {}


def _record(call_sid: str) -> dict:
    return _recorded.setdefault(call_sid, {"tool_calls": [], "metadata": {}})


def record_tool(call_sid: str, name: str, parameters: dict, output: Any) -> None:
    """Record a tool call for Bluejay's Tool Call metric."""
    if config.bluejay_api_key:
        _record(call_sid)["tool_calls"].append(
            {"name": name, "parameters": parameters, "output": output, "time_ns": time.time_ns()}
        )


def set_metadata(call_sid: str, metadata: dict) -> None:
    """Set call-level values; each key fills a {{variable}} in Bluejay metric prompts."""
    if config.bluejay_api_key:
        _record(call_sid)["metadata"].update(metadata)


def setup() -> None:
    """Send OpenTelemetry traces to Bluejay. Does nothing unless BLUEJAY_API_KEY is set."""
    api_key = config.bluejay_api_key
    if not api_key:
        return
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "conv-relay-scheduling"}))
    # Batches on a background thread, so calls never block on Bluejay.
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=BLUEJAY_TRACES_URL, headers={"X-API-KEY": api_key})
        )
    )
    trace.set_tracer_provider(provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    logger.info("Bluejay tracing enabled")


@router.post("/bluejay/trigger-call")
async def trigger_call(request: Request) -> dict:
    """Bluejay's outbound trigger: call the Digital Human and remember which result the call belongs to."""
    
    data = await request.json()
    phone_number = data.get("phone_number")
    result_id = str(data.get("sim_conversation_id") or "")
    
    if not phone_number or not result_id:
        raise HTTPException(status_code=400, detail="phone_number and sim_conversation_id are required")
    trigger_key = request.headers.get("X-Bluejay-Idempotency-Key") or result_id
    
    if trigger_key in _triggers:  
        return {"status": "ok", "call_sid": _triggers[trigger_key]}

    client = Client(config.twilio_account_sid, config.twilio_auth_token)

    call = await asyncio.to_thread(
        client.calls.create, to=phone_number, from_=config.twilio_phone_number, url=config.http_url + "/incoming-call"
    )
    _triggers[trigger_key] = call.sid
    _simulations[call.sid] = (result_id, trigger_key)
    logger.info("Bluejay test call placed to=%s callSid=%s result_id=%s", phone_number, call.sid, result_id)
    return {"status": "ok", "call_sid": call.sid}


async def link_simulation(call_span, call_sid: str) -> None:
    """Send a finished call's trace, tool calls and metadata to its Bluejay simulation result."""
    if not config.bluejay_api_key:
        return
    recorded = _recorded.pop(call_sid, {"tool_calls": [], "metadata": {}})
    simulation = _simulations.pop(call_sid, None)
    body: dict[str, Any] = {"trace_ids": [format(call_span.get_span_context().trace_id, "032x")]}
    if simulation is not None:  # placed by trigger_call, so the result ID is known
        result_id, trigger_key = simulation
        _triggers.pop(trigger_key, None)
        body["simulation_result_id"] = result_id
    else:  # let Bluejay match the call by time
        start, end = call_span.start_time / 1e9, call_span.end_time / 1e9  # SDK spans record ns
        body["start_time"] = datetime.fromtimestamp(start, timezone.utc).isoformat()
        body["end_time"] = datetime.fromtimestamp(end, timezone.utc).isoformat()
        body["duration"] = end - start
    if recorded["tool_calls"]:
        body["tool_calls"] = [
            {
                "name": tc["name"],
                "parameters": tc["parameters"],
                "output": tc["output"],
                "start_offset_ms": max(0, (tc["time_ns"] - call_span.start_time) // 1_000_000),
            }
            for tc in recorded["tool_calls"]
        ]
    if recorded["metadata"]:
        body["metadata"] = recorded["metadata"]

    # Spans are batched; push them out before Bluejay looks the trace up.
    await asyncio.to_thread(trace.get_tracer_provider().force_flush)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                BLUEJAY_LINK_URL,
                content=json.dumps(body, default=str),  # tool outputs may not be plain JSON
                headers={"X-API-Key": config.bluejay_api_key, "Content-Type": "application/json"},
            )
    except httpx.HTTPError:
        logger.exception("Bluejay link request failed")
        return
    if resp.is_error:
        logger.warning("Bluejay link failed status=%s body=%s", resp.status_code, resp.text)
    else:
        logger.info(
            "Bluejay trace linked trace_id=%s result_id=%s tool_calls=%d",
            body["trace_ids"][0],
            body.get("simulation_result_id", "matched by time"),
            len(body.get("tool_calls", [])),
        )
