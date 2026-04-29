import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket

from src.twilio.call_controller import router as twilio_router
from src.twilio.relay_receiver import handle_relay_websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    stream=sys.stdout,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.getLogger(__name__).info("server starting")
    yield
    logging.getLogger(__name__).info("server stopping")


app = FastAPI(title="Scheduling Agent", lifespan=lifespan)
app.include_router(twilio_router)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await handle_relay_websocket(ws)
