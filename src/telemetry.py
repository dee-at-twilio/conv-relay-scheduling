import logging
import time
from contextvars import ContextVar

from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.config import config

logger = logging.getLogger(__name__)


# The instrumentor's span is only "current" while create() runs, not while the stream is consumed,
# so we capture it here to annotate it later. Set inside the calling task, so it's per-turn.
_llm_span: ContextVar[Span | None] = ContextVar("llm_span", default=None)


class _LLMSpanHook(SpanProcessor):
    """Rename the instrumentor's 'ChatCompletion' spans to 'llm' and remember the latest one."""

    def on_start(self, span: Span, parent_context=None) -> None:
        if span.name == "ChatCompletion":
            span.update_name("llm")
            _llm_span.set(span)


def last_llm_span() -> Span | None:
    """Span of the most recent OpenAI request made in this task; None when tracing is off."""
    return _llm_span.get()


def mark_latency(span, event: str, attribute: str) -> float | None:
    """Add a timestamped event to `span` and record ms elapsed since it started. No-op when not tracing."""
    start = getattr(span, "start_time", None)
    if not start:
        return None
    elapsed_ms = round((time.time_ns() - start) / 1e6, 1)
    span.add_event(event)
    span.set_attribute(attribute, elapsed_ms)
    return elapsed_ms


def setup_tracing() -> TracerProvider | None:
    """Export OpenTelemetry traces over OTLP if an endpoint is configured; no-op otherwise."""
    if not config.otel_exporter_endpoint:
        return None
    provider = TracerProvider(
        resource=Resource.create({"service.name": config.otel_service_name}),
        # The call span carries a raw relay message log as events; the default cap (128) would truncate it
        span_limits=SpanLimits(max_events=4096),
    )
    provider.add_span_processor(_LLMSpanHook())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otel_exporter_endpoint)))
    trace.set_tracer_provider(provider)
    # Patches the openai SDK classes, so module-level clients created before this are traced too
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    logger.info("tracing enabled endpoint=%s", config.otel_exporter_endpoint)
    return provider
