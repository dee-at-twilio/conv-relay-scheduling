import json
import logging
from fastapi import APIRouter, Request, Response
from twilio.twiml.voice_response import VoiceResponse, Connect

from src.config import config
from src.session.session_manager import session_manager

logger = logging.getLogger(__name__)
router = APIRouter()

_RECONNECT_ERROR_CODE = 64105


def _evaluate_status_callback(data: dict) -> dict | None:
    """
    Decide whether a status callback is worth surfacing to the conversation.
    Returns the (possibly transformed) payload, or None if no action needed.
    Expand this as the orchestration layer grows.
    """
    call_sid = data.get("CallSid")
    call_status = data.get("CallStatus")
    logger.info("status callback callSid=%s status=%s", call_sid, call_status)
    return data


@router.post("/incoming-call")
async def incoming_call():
    """Return TwiML that connects the call to ConversationRelay."""
    logger.info("incoming call received")
    response = VoiceResponse()
    connect = Connect(action=config.http_url + "/action")
    connect.conversation_relay(
        url=config.ws_url,
        welcome_greeting="Hello. How can I help you today?",
        voice=config.tts_voice,
        language=config.tts_language,
        transcription_language=config.transcription_language
    )
    response.append(connect)
    logger.info("incoming-call → ConversationRelay ws=%s", config.ws_url)
    return Response(content=str(response), media_type="application/xml")


@router.post("/inbound-forward")
async def inbound_forward(request: Request):
    """
    Webhook for a Twilio number configured in the console.
    Bridges the inbound caller to config.forward_to_number.
    """
    form = await request.form()
    logger.info("inbound-forward from=%s to=%s → %s",
                form.get("From"), form.get("To"), config.forward_to_number)
    response = VoiceResponse()
    if config.forward_to_number:
        response.dial(config.forward_to_number)
    else:
        logger.warning("inbound-forward: FORWARD_TO_NUMBER unset — hanging up")
        response.hangup()
    return Response(content=str(response), media_type="application/xml")


@router.post("/simple-call")
async def simple_call(request: Request):
    """Return TwiML that bridges the caller to a second party read from `to` param."""
    form = await request.form()
    to = request.query_params.get("to") or form.get("to")
    logger.info("simple-call received to=%s", to)
    response = VoiceResponse()
    if to:
        response.dial(to)
    else:
        logger.warning("simple-call missing `to` param — hanging up")
        response.hangup()
    return Response(content=str(response), media_type="application/xml")


@router.post("/action")
async def action_callback(request: Request):
    """
    Twilio posts here when a ConversationRelay session ends.
    Three cases:
      - error 64105 → WS dropped unexpectedly, reconnect
      - handoffData present → enqueue to Flex
      - otherwise → hang up
    """
    form = await request.form()
    error_code = form.get("ErrorCode")
    handoff_data_raw = form.get("HandoffData")

    logger.info("action callback ErrorCode=%s HandoffData=%s", error_code, handoff_data_raw)

    response = VoiceResponse()

    if error_code and int(error_code) == _RECONNECT_ERROR_CODE:
        # Reconnect — issue a fresh ConversationRelay
        connect = Connect()
        connect.conversation_relay(
            url=config.ws_url,
            voice=config.tts_voice,
            language=config.tts_language,
            ttsProvider=config.tts_provider,
            transcriptionProvider=config.transcription_provider,
            stt_speechModel=config.speechModel,
            transcription_language=config.transcription_language
        )
        response.append(connect)
        return Response(content=str(response), media_type="application/xml")

    if handoff_data_raw:
        try:
            handoff = json.loads(handoff_data_raw)
            workflow_sid = handoff.get("workflowSid") or config.twilio_flex_workflow_sid
        except (json.JSONDecodeError, AttributeError):
            workflow_sid = config.twilio_flex_workflow_sid

        if workflow_sid:
            response.enqueue(None, workflow_sid=workflow_sid)
            return Response(content=str(response), media_type="application/xml")

    response.hangup()
    return Response(content=str(response), media_type="application/xml")


@router.post("/status-callback")
async def status_callback(request: Request):
    """
    Twilio posts call status updates here (ringing, in-progress, completed, etc.).
    If the call has an active session, the evaluated payload is injected as a
    system message so the LLM can react (e.g. notice a transferred leg dropped).
    """
    form = await request.form()
    data = dict(form)

    evaluated = _evaluate_status_callback(data)

    if evaluated:
        call_sid = data.get("CallSid")
        if call_sid:
            session = session_manager.get_session(call_sid)
            if session:
                from src.models.session import ConversationMessage
                session.messages.append(ConversationMessage(
                    role="system",
                    content=json.dumps(evaluated),
                ))

    return Response(content=json.dumps({"success": True}), media_type="application/json")
