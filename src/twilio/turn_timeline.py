"""
Per-call voice timeline: turns ConversationRelay speaker events into trace spans and latency attributes.

    call
    ├── greeting                          setup → welcome greeting finished playing
    ├── turn                              caller stops ─────────────────────────► agent audio ends
    │   ├── stt · end-of-speech (twilio)  caller stops → prompt received
    │   ├── llm / tool: ...               (created by conversation_graph + the OpenAI instrumentor)
    │   └── tts · first audio (twilio)    first text sent → agent audio starts
    └── turn ...

A turn that is still processing when the next prompt arrives stays open until its processing finishes,
so overlapping turns are visible as overlapping bars (and flagged with turn.overlapped / turn.superseded_by).

All timestamps are taken on this server when each message arrives, so every interval is on one clock.
Without `events="speaker-events"` in the TwiML, STT/TTS spans are skipped and the turn ends when processing does.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from difflib import SequenceMatcher
from typing import Any

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_MAX_STT_GAP_NS = 10_000_000_000  # ignore a "caller stopped" older than 10s — it belongs to some other utterance
_ECHO_OVERLAP = 0.75  # share of the prompt's words found, in order, in something the agent just said
_ECHO_MIN_WORDS = 3   # shorter prompts ("yes", "thank you") match agent speech by chance
_LONG_SILENCE_MS = 3000  # dead air worth flagging (silence.long / call.long_silence_count)
_INTERRUPT_GRACE_NS = 500_000_000  # how long after "agent stopped" an interrupt still counts for that turn
_WORD = re.compile(r"[a-z0-9']+")


def _ms(start_ns: int, end_ns: int) -> float:
    return round((end_ns - start_ns) / 1e6, 1)


def _is_on(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("on", "true", "start", "started", "1")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _echo_overlap(prompt: str, agent_text: str) -> float:
    """Longest run of the prompt's words that appears verbatim in agent_text, as a share of the prompt."""
    p, a = _words(prompt), _words(agent_text)
    if not p or not a:
        return 0.0
    match = SequenceMatcher(None, p, a, autojunk=False).find_longest_match(0, len(p), 0, len(a))
    return match.size / len(p)


def _ok_if_unset(span: Span) -> None:
    status = getattr(span, "status", None)
    if status is not None and status.status_code == StatusCode.UNSET:
        span.set_status(Status(StatusCode.OK))


def _stage_span(name: str, parent: Span, start_ns: int, end_ns: int, attributes: dict | None = None) -> None:
    """Record a stage that happened outside our process (Twilio STT/TTS) as a span with explicit times."""
    span = tracer.start_span(
        name,
        context=trace.set_span_in_context(parent),
        start_time=start_ns,
        attributes={SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.UNKNOWN.value, **(attributes or {})},
    )
    span.end(end_time=end_ns)


class VoiceTurn:
    def __init__(self, timeline: TurnTimeline, index: int, span: Span, prompt_ns: int, start_ns: int, stt_measured: bool):
        self._timeline = timeline
        self.index = index
        self.span = span
        self._prompt_ns = prompt_ns
        self._start_ns = start_ns
        self._stt_measured = stt_measured
        self._first_speech_ns: int | None = None
        self._agent_audio_ns: int | None = None    # first agent audio of this turn
        self._audio_segment_ns: int | None = None  # start of the agent audio currently playing
        self._finish_ns: int | None = None  # agent audio stopped; end held briefly in case an interrupt follows
        self._agent_speech_ns = 0
        self._tts_measured = True
        self._llm_ms = 0.0
        self._tools_ms = 0.0
        self._sent: list[str] = []
        self._sent_chunks = 0
        self._sent_last = False
        self._heard: list[str] = []
        self._played_words = 0  # how far into sent_text the tokensPlayed events have reached
        self._history_start: int | None = None
        self._own_messages = 0
        self._interrupted = False
        self._superseded = False
        self.processing_done = False
        self.ended = False

    @property
    def sent_text(self) -> str:
        return "".join(self._sent).strip()

    # ── called from conversation_graph ──────────────────────────────────────

    def processing_started(self, history_len: int) -> None:
        self._history_start = history_len
        if not self.ended:
            self.span.set_attribute("turn.history_len_at_start", history_len)

    def message_added(self) -> None:
        """This turn appended one message to the shared session history."""
        self._own_messages += 1

    def _foreign_messages(self, history_len: int) -> int:
        """Messages other (overlapping) turns added to the shared history since this turn started."""
        if self._history_start is None:
            return 0
        return history_len - self._history_start - self._own_messages

    def llm_round(self, round_num: int, history_len: int) -> None:
        if self._sent and not self._sent[-1][-1:].isspace():
            self._sent.append(" ")  # keep text from successive rounds from running together
        if self.ended:
            return
        self.span.add_event("llm round started", {
            "round": round_num,
            "history_len": history_len,
            "messages_from_other_turns": self._foreign_messages(history_len),
        })

    def text_sent(self, token: str, last: bool) -> None:
        if token:
            self._sent.append(token)
            self._sent_chunks += 1
        if last:
            self._sent_last = True
        if not token or self._first_speech_ns or self.ended:
            return
        self._first_speech_ns = time.time_ns()
        self._timeline.queue_playback(self)
        self.span.add_event("first text sent to Twilio")
        self.span.set_attribute("latency.agent_ms", _ms(self._prompt_ns, self._first_speech_ns))
        if self._timeline.agent_speaking:
            # Earlier audio (greeting / an overlapping turn) is still playing; our audio queues behind it,
            # so Twilio may never send a fresh "agent speaking on" for this turn.
            self._tts_measured = False
            self.span.add_event("first text sent while other agent audio still playing")

    def add_llm_ms(self, ms: float) -> None:
        self._llm_ms += ms

    def add_tools_ms(self, ms: float) -> None:
        self._tools_ms += ms

    def processing_finished(self, history_len: int) -> None:
        """All text for this turn has been sent. End now unless we're still waiting on agent audio."""
        self.processing_done = True
        if self.ended:
            return
        self.span.set_attribute("turn.messages_from_other_turns", self._foreign_messages(history_len))
        self.span.add_event("processing finished")
        if not self._timeline.speaker_events or not self._first_speech_ns or self._interrupted:
            self.end("superseded; processing finished after next prompt" if self._superseded else "processing finished")
        elif self.fully_played and not self._audio_segment_ns:
            self.playback_done(time.time_ns())  # every word already played before the last text went out
        # else: our audio is queued or playing; it ends via agent-audio / tokensPlayed / interrupt events,
        # even if a newer turn has started meanwhile.

    # ── called from TurnTimeline ────────────────────────────────────────────

    def supersede(self, next_index: int) -> None:
        if self.ended:
            return
        self.span.set_attribute("turn.superseded_by", next_index)
        if self._finish_ns:
            self.end("agent finished speaking", end_ns=self._finish_ns)
        elif self.processing_done:
            if self._agent_audio_ns and not self._timeline.agent_speaking:  # our audio played and stopped
                self.end("superseded by next prompt")
            # else: our audio is playing or still queued (TTS not started); it ends via audio/interrupt events
        else:
            self._superseded = True
            self.span.set_attribute("turn.superseded_while_processing", True)
            self.span.add_event("next prompt arrived while still processing", {"next_turn": next_index})

    def on_agent_audio(self, on: bool, now: int) -> None:
        if self.ended:
            return
        if on:
            if self._finish_ns:  # audio resumed right after stopping: same utterance continues
                self._finish_ns = None
                return
            # Audio before our first text belongs to the greeting / an earlier turn
            if not self._first_speech_ns or self._audio_segment_ns:
                return
            self._audio_segment_ns = now
            if not self._agent_audio_ns:
                self._agent_audio_ns = now
                self.span.add_event("agent audio started")
                self.span.set_attribute("latency.caller_wait_ms", _ms(self._start_ns, now))
                if self._tts_measured:
                    _stage_span("tts · first audio (twilio)", self.span, self._first_speech_ns, now)
                    self.span.set_attribute("latency.tts_ms", _ms(self._first_speech_ns, now))
            return
        if self.processing_done and self._agent_audio_ns:
            # Twilio sends "agent stopped" a few ms *before* the interrupt message on a barge-in, so don't
            # decide yet: settle() ends it as finished, on_interrupt() ends it as interrupted.
            self._finish_ns = now
            self.span.add_event("agent audio stopped", timestamp=now)
            return
        if self._audio_segment_ns:
            self._close_audio_segment(now)  # pause between LLM rounds; more audio may follow

    def settle(self, now: int, force: bool = False) -> None:
        """End a turn whose audio stopped and no interrupt followed within the grace window."""
        if self._finish_ns and not self.ended and (force or now - self._finish_ns > _INTERRUPT_GRACE_NS):
            self.end("agent finished speaking", end_ns=self._finish_ns)

    def _close_audio_segment(self, now: int, cut_short: bool = False) -> None:
        start, self._audio_segment_ns = self._audio_segment_ns, None
        if start is None:
            return
        self._agent_speech_ns += now - start
        _stage_span("agent speaking", self.span, start, now, {"audio.cut_short": cut_short})

    def consume_played(self, words: list[str]) -> bool:
        """If `words` (a tokensPlayed chunk) is the next unplayed part of our text, claim it."""
        sent = _words(self.sent_text)
        n = len(words)
        if not n:
            return False
        for i in range(self._played_words, len(sent) - n + 1):
            if sent[i:i + n] == words:
                self._played_words = i + n
                return True
        return False

    @property
    def fully_played(self) -> bool:
        return self.processing_done and 0 < len(_words(self.sent_text)) <= self._played_words

    def on_tokens_played(self, value: Any) -> None:
        if self.ended or value is None:
            return
        self._heard.append(value if isinstance(value, str) else json.dumps(value))

    def playback_done(self, now: int) -> None:
        """All our text has played. Twilio may roll straight into the next turn's audio without an off/on."""
        if self.ended or self._finish_ns or not self.processing_done:
            return
        self._finish_ns = now
        self.span.add_event("agent audio finished (all text played)", timestamp=now)
        self._timeline.hand_off_audio(self, now)

    def on_interrupt(self, utterance: str | None, duration_ms: int | None, playing: bool = True) -> None:
        """`playing`: this turn's audio was the one playing. Queued turns' audio is discarded unheard."""
        if self.ended:
            return
        self._interrupted = True  # don't wait for agent audio that won't come
        self.span.add_event("caller interrupted", {
            "utterance_until_interrupt": (utterance or "") if playing else "",
            "duration_until_interrupt_ms": (duration_ms or 0) if playing else 0,
            "was_playing": playing,
        })
        if self._finish_ns:
            self.span.set_attribute("turn.interrupt_after_audio_stop_ms", _ms(self._finish_ns, time.time_ns()))
        self.span.set_attribute("turn.interrupted", True)
        if playing and utterance:
            self.span.set_attribute("turn.heard_before_interrupt", utterance)
        if self._first_speech_ns and not (playing and utterance):
            # Text was sent but none of it was heard: queued behind another turn, or cut before audio began
            self.span.set_attribute("turn.unheard", True)
        if self.processing_done:
            self.end("interrupted")
        # else: processing is being cancelled; processing_finished closes the turn

    def end(self, reason: str, end_ns: int | None = None) -> None:
        if self.ended:
            return
        self.ended = True
        self._timeline.dequeue_playback(self)
        if self.sent_text:
            self._timeline.remember_agent_text(self.sent_text)
        if not self.span.is_recording():
            return
        end_ns = end_ns or time.time_ns()
        # Close the last agent-audio bar where the audio actually stopped (or where the turn ends, if it
        # was still playing); anything but a natural finish means the caller didn't hear all of it.
        self._close_audio_segment(self._finish_ns or end_ns, cut_short=reason != "agent finished speaking")
        if self._agent_speech_ns:
            self.span.set_attribute("turn.agent_speech_ms", round(self._agent_speech_ns / 1e6, 1))
        self.span.set_attributes({
            "turn.end_reason": reason,
            "turn.sent_text": self.sent_text,
            "turn.sent_chunks": self._sent_chunks,
            "turn.sent_last": self._sent_last,
            "latency.llm_ms": round(self._llm_ms, 1),
            "latency.tools_ms": round(self._tools_ms, 1),
            "latency.stt_measured": self._stt_measured,
            "latency.tts_measured": self._tts_measured and self._agent_audio_ns is not None,
        })
        if self._heard:
            self.span.set_attribute("turn.heard", " ".join(self._heard))
        _ok_if_unset(self.span)
        self.span.end(end_time=end_ns)


class TurnTimeline:
    """One per call. Fed by the receive loop; hands a VoiceTurn to each process_turn task.

    Create it inside the call span's context so greeting and turn spans nest under the call.
    """

    def __init__(self, call_sid: str):
        self._call_sid = call_sid
        self.speaker_events = False
        self.agent_speaking = False
        self._caller_started_ns: int | None = None
        self._caller_stopped_ns: int | None = None
        self._caller_started_during_agent = False
        self._turns: list[VoiceTurn] = []
        self._current: VoiceTurn | None = None
        # Turns whose text has been sent, in send order. Twilio plays audio FIFO, so the head is the turn
        # actually being heard; agent-audio / tokensPlayed / interrupt events are routed by this, not by
        # which turn is newest.
        self._playback: list[VoiceTurn] = []
        self._recent_agent_text: deque[str] = deque(maxlen=3)
        self._seen: set[str | None] = set()
        self.greeting_text: str | None = None
        self._greeting: Span | None = None
        self._greeting_start_ns = 0
        self._greeting_audio_ns: int | None = None
        self._prompts_during_greeting = 0
        # Gaps between turns are drawn as call-level spans so the waterfall has no unexplained white space
        self._call_span = trace.get_current_span()
        self._agent_audio_ended_ns: int | None = None
        self.caller_speaking = False
        self._quiet_since: int | None = None  # both sides silent since (cleared when a prompt / any speech arrives)
        self._quiet_after = ""
        self._silence_before_caller_ms: float | None = None
        self._caller_segment_ns: int | None = None
        self._caller_segment_during_agent = False
        # A finished caller segment waits here until we know whether Twilio turned it into a prompt
        self._pending_caller: tuple[int, int, bool] | None = None
        self._silences_ms: list[float] = []
        self._untranscribed_speech = 0

    def _open_turns(self) -> list[VoiceTurn]:
        return [t for t in self._turns if not t.ended]

    def remember_agent_text(self, text: str) -> None:
        self._recent_agent_text.append(text)

    # ── playback queue ──────────────────────────────────────────────────────

    def queue_playback(self, turn: VoiceTurn) -> None:
        self._playback.append(turn)

    def dequeue_playback(self, turn: VoiceTurn) -> None:
        if turn in self._playback:
            self._playback.remove(turn)

    def _queued(self) -> list[VoiceTurn]:
        return [t for t in self._playback if not t.ended]

    def _playing_turn(self) -> VoiceTurn | None:
        """The turn whose audio is (or is next to be) playing: oldest queued turn not already finished."""
        queued = self._queued()
        return next((t for t in queued if not t._finish_ns), queued[0] if queued else None)

    def hand_off_audio(self, finished: VoiceTurn, now: int) -> None:
        """`finished` played its last word while audio continues: that audio belongs to the next queued turn."""
        if not self.agent_speaking:
            return
        nxt = next((t for t in self._queued() if t is not finished and not t._finish_ns), None)
        if nxt:
            nxt.on_agent_audio(True, now)

    def _route_tokens_played(self, value: Any, now: int) -> None:
        queued = self._queued()
        if not queued:
            return
        words = _words(value) if isinstance(value, str) else []
        owner = next((t for t in queued if t.consume_played(words)), None) or self._playing_turn() or queued[0]
        for earlier in queued[:queued.index(owner)]:
            earlier.playback_done(now)  # FIFO: anything queued before the owner has finished playing
        owner.on_tokens_played(value)
        if owner.fully_played:
            owner.playback_done(now)

    # ── greeting ────────────────────────────────────────────────────────────

    def start_greeting(self, text: str) -> None:
        self.greeting_text = text
        self.remember_agent_text(text)
        self._greeting_start_ns = time.time_ns()
        self._greeting = tracer.start_span("greeting", start_time=self._greeting_start_ns, attributes={
            SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.CHAIN.value,
            SpanAttributes.SESSION_ID: self._call_sid,
            SpanAttributes.OUTPUT_VALUE: text,
        })

    def _end_greeting(self, reason: str) -> None:
        span, self._greeting = self._greeting, None
        if not span or not span.is_recording():
            return
        span.set_attributes({"greeting.end_reason": reason, "greeting.prompts_during": self._prompts_during_greeting})
        _ok_if_unset(span)
        span.end()

    def _greeting_audio(self, on: bool, now: int) -> None:
        if not self._greeting:
            return
        if on and not self._greeting_audio_ns:
            self._greeting_audio_ns = now
            self._greeting.add_event("greeting audio started")
            self._greeting.set_attribute("latency.tts_ms", _ms(self._greeting_start_ns, now))
        elif not on and self._greeting_audio_ns:
            self._greeting.add_event("greeting audio finished")
            self._greeting.set_attribute("greeting.audio_ms", _ms(self._greeting_audio_ns, now))
            self._end_greeting("finished playing")

    # ── turns ───────────────────────────────────────────────────────────────

    def start_turn(self, user_text: str, lang: str | None = None, last: bool | None = None, raw: str | None = None) -> VoiceTurn:
        """Call when a prompt arrives, inside the call span's context so the turn nests under it."""
        now = time.time_ns()
        index = len(self._turns) + 1
        open_turns = self._open_turns()
        still_processing = [t.index for t in open_turns if not t.processing_done]

        # Echo check against what the agent said recently or is saying right now
        candidates = list(self._recent_agent_text) + [t.sent_text for t in open_turns if t.sent_text]
        overlap, matched = max(((_echo_overlap(user_text, c), c) for c in candidates), default=(0.0, ""))
        suspected_echo = overlap >= _ECHO_OVERLAP and len(_words(user_text)) >= _ECHO_MIN_WORDS

        if self._greeting:
            self._prompts_during_greeting += 1
            self._greeting.add_event("prompt received during greeting", {"text": user_text})
            if not self.speaker_events:
                self._end_greeting("first prompt (no speaker events)")

        for t in open_turns:
            t.supersede(index)

        # The caller's speech produced this prompt; the time until the agent answers is latency, not silence
        self._flush_caller_speech(transcript=user_text)
        self._quiet_since = None

        stopped = self._caller_stopped_ns
        stt_measured = stopped is not None and 0 <= now - stopped <= _MAX_STT_GAP_NS
        start_ns = stopped if stt_measured else now

        attributes: dict[str, Any] = {
            SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.CHAIN.value,
            SpanAttributes.SESSION_ID: self._call_sid,
            SpanAttributes.INPUT_VALUE: user_text,
            "turn.index": index,
            "turn.overlapped": bool(still_processing),
            "stt.agent_speaking_at_prompt": self.agent_speaking,
            "stt.during_greeting": self._greeting is not None,
            "stt.echo_overlap": round(overlap, 2),
            "stt.suspected_echo": suspected_echo,
        }
        if still_processing:
            attributes["turn.overlapped_with"] = still_processing
        if overlap > 0:
            attributes["stt.echo_match"] = matched[:500]
        if lang is not None:
            attributes["prompt.lang"] = lang
        if last is not None:
            attributes["prompt.last"] = last
        if raw is not None:
            attributes["prompt.raw"] = raw[:2000]
        if self._caller_started_ns:
            attributes["stt.caller_started_during_agent_speech"] = self._caller_started_during_agent
            attributes["stt.caller_start_to_prompt_ms"] = _ms(self._caller_started_ns, now)
        ended = self._agent_audio_ended_ns
        if ended and ended <= start_ns and not self.agent_speaking:
            attributes["turn.gap_before_ms"] = _ms(ended, start_ns)  # agent went quiet → this turn began
        if self._silence_before_caller_ms is not None:
            attributes["turn.silence_before_caller_ms"] = self._silence_before_caller_ms
            self._silence_before_caller_ms = None

        span = tracer.start_span("turn", start_time=start_ns, attributes=attributes)
        span.add_event("prompt received", timestamp=now)
        if stt_measured:
            _stage_span("stt · end-of-speech (twilio)", span, stopped, now)
            span.set_attribute("latency.stt_ms", _ms(stopped, now))
            if self._caller_started_ns and self._caller_started_ns < stopped:
                span.set_attribute("turn.caller_speech_ms", _ms(self._caller_started_ns, stopped))

        if suspected_echo:
            logger.warning("possible echo callSid=%s turn=%d prompt=%r matches agent text %r",
                           self._call_sid, index, user_text, matched)
        if still_processing:
            logger.info("overlapping turn callSid=%s turn=%d still_processing=%s", self._call_sid, index, still_processing)

        self._caller_started_ns = self._caller_stopped_ns = None
        self._caller_started_during_agent = False
        self._current = VoiceTurn(self, index, span, now, start_ns, stt_measured)
        self._turns.append(self._current)
        return self._current

    # ── relay events ────────────────────────────────────────────────────────

    def _settle(self, now: int, force: bool = False) -> None:
        for t in self._open_turns():
            t.settle(now, force)

    def on_info(self, name: str | None, value: Any) -> None:
        now = time.time_ns()
        if name not in self._seen:  # log one of each so the payload shape is easy to confirm
            self._seen.add(name)
            logger.info("relay info event first seen callSid=%s name=%s value=%r", self._call_sid, name, value)
        self._settle(now)
        match name:
            case "clientSpeaking":
                self.speaker_events = True
                if _is_on(value):
                    self._caller_started_ns = now
                    self._caller_started_during_agent = self.agent_speaking
                    self._flush_caller_speech(transcript=None)  # earlier speech never became a prompt
                    self._record_silence(now)
                    if self._caller_segment_ns is None:
                        self._caller_segment_ns = now
                        self._caller_segment_during_agent = self.agent_speaking
                    self.caller_speaking = True
                else:
                    self._caller_stopped_ns = now
                    self.caller_speaking = False
                    if self._caller_segment_ns is not None:
                        self._pending_caller = (self._caller_segment_ns, now, self._caller_segment_during_agent)
                        self._caller_segment_ns = None
                    if not self.agent_speaking:
                        self._quiet_since, self._quiet_after = now, "caller speech with no transcript"
            case "agentSpeaking":
                self.speaker_events = True
                on = _is_on(value)
                self.agent_speaking = on
                if on:
                    self._quiet_since = None
                else:
                    self._agent_audio_ended_ns = now
                    if not self.caller_speaking:  # a barge-in isn't silence
                        self._quiet_since, self._quiet_after = now, "agent finished speaking"
                self._greeting_audio(on, now)
                playing = self._playing_turn()
                if playing:
                    playing.on_agent_audio(on, now)
            case "tokensPlayed":
                self._route_tokens_played(value, now)

    def on_interrupt(self, utterance: str | None, duration_ms: int | None) -> None:
        self._settle(time.time_ns())  # turns whose audio stopped > grace ago are done; recent ones get this interrupt
        if self._greeting:
            self._greeting.add_event("caller interrupted", {"utterance_until_interrupt": utterance or ""})
            self._end_greeting("interrupted")
        # The interrupt cancels every in-flight LLM stream and flushes all queued audio, so it's recorded on
        # every open turn, but only the turn being played gets the "heard so far" text.
        queued = self._queued()
        playing = queued[0] if queued else None
        for t in self._open_turns():
            t.on_interrupt(utterance, duration_ms, playing=t is playing)

    def _record_silence(self, now: int) -> None:
        """Dead air: neither side speaking and no prompt pending, ended by the caller starting to talk."""
        quiet, self._quiet_since = self._quiet_since, None
        if quiet is None or self.agent_speaking or now < quiet:
            return
        ms = _ms(quiet, now)
        self._silence_before_caller_ms = ms
        self._silences_ms.append(ms)
        _stage_span("silence · waiting for caller", self._call_span, quiet, now, {
            "silence.ms": ms,
            "silence.long": ms >= _LONG_SILENCE_MS,
            "silence.after": self._quiet_after,
        })

    def _flush_caller_speech(self, transcript: str | None) -> None:
        """Emit the last finished caller segment, labelled by whether Twilio produced a prompt for it."""
        pending, self._pending_caller = self._pending_caller, None
        if pending is None:
            return
        start, end, during_agent = pending
        attributes: dict[str, Any] = {
            "caller.speech_ms": _ms(start, end),
            "caller.during_agent_speech": during_agent,
            "caller.transcribed": transcript is not None,
        }
        if transcript is not None:
            attributes[SpanAttributes.INPUT_VALUE] = transcript
        else:
            self._untranscribed_speech += 1
        # Twilio reports "stopped" when it finalises the transcript, so this includes its end-of-speech wait
        name = "caller speaking" if transcript is not None else "caller speaking (no transcript)"
        _stage_span(name, self._call_span, start, end, attributes)

    def end_call(self) -> None:
        self._settle(time.time_ns(), force=True)
        self._end_greeting("call ended")
        for t in self._open_turns():
            t.end("call ended")
        self._flush_caller_speech(transcript=None)
        if not self._call_span.is_recording():
            return
        self._call_span.set_attribute("call.untranscribed_speech_count", self._untranscribed_speech)
        if self._silences_ms:
            self._call_span.set_attributes({
                "call.silence_count": len(self._silences_ms),
                "call.long_silence_count": sum(ms >= _LONG_SILENCE_MS for ms in self._silences_ms),
                "call.longest_silence_ms": max(self._silences_ms),
                "call.total_silence_ms": round(sum(self._silences_ms), 1),
            })
