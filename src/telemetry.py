import logging

from openinference.instrumentation.openai import OpenAIInstrumentor
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.config import config

logger = logging.getLogger(__name__)


def setup_tracing() -> TracerProvider | None:
    """Export OpenTelemetry traces over OTLP if an endpoint is configured; no-op otherwise."""
    if not config.otel_exporter_endpoint:
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": config.otel_service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otel_exporter_endpoint)))
    trace.set_tracer_provider(provider)
    # Patches the openai SDK classes, so module-level clients created before this are traced too
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    logger.info("tracing enabled endpoint=%s", config.otel_exporter_endpoint)
    return provider
