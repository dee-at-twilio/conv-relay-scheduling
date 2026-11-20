import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from nicegui import ui
from src.telemetry import setup_tracing
from src.twilio.call_controller import router as twilio_router
from src.twilio.websocket_receiver import handle_relay_websocket
from src.ui import call_log, dashboard, live_calls, appointments
from src.webhook_router import router as intelligence_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    stream=sys.stdout,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.getLogger(__name__).info("server starting")
    tracer_provider = setup_tracing()
    yield
    logging.getLogger(__name__).info("server stopping")
    if tracer_provider:
        tracer_provider.shutdown()  # flush buffered spans


app = FastAPI(title="Scheduling Agent", lifespan=lifespan)
app.include_router(twilio_router)
app.include_router(intelligence_router)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await handle_relay_websocket(ws)


@app.websocket("/ws-bridge")
async def bridge_ws(ws: WebSocket):
    await handle_relay_websocket(ws)

dashboard.create()
live_calls.create()
appointments.create()
ui.run_with(app, mount_path="/pages", storage_secret="scheduling-agent")
