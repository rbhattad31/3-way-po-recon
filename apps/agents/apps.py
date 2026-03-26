import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)

_phoenix_started = threading.Event()


def start_phoenix_once():
    """Instrument the openai SDK with Phoenix/OpenInference if PHOENIX_ENABLED=true.

    Uses a threading.Event to guarantee the instrumentor runs at most once,
    even when Django's auto-reloader forks a new process.
    """
    if _phoenix_started.is_set():
        return
    _phoenix_started.set()

    if os.environ.get("PHOENIX_ENABLED", "").lower() != "true":
        logger.debug("Phoenix tracing disabled (PHOENIX_ENABLED != true)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from openinference.instrumentation.openai import OpenAIInstrumentor

        endpoint = os.environ.get(
            "PHOENIX_COLLECTOR_ENDPOINT",
            "http://localhost:6006/v1/traces",
        )
        provider = TracerProvider()
        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        trace.set_tracer_provider(provider)

        OpenAIInstrumentor().instrument(tracer_provider=provider)
        logger.info(
            "Phoenix OpenAI instrumentation active (endpoint=%s)", endpoint
        )
    except Exception:
        logger.exception("Failed to initialise Phoenix instrumentation")


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents"
    verbose_name = "Agents"

    def ready(self):
        start_phoenix_once()
