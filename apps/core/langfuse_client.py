"""Langfuse observability client -- fail-silent LLM tracing wrapper.

Compatible with Langfuse SDK v3 (uses start_observation / TraceContext API).
Disabled automatically if LANGFUSE_PUBLIC_KEY is not set.
Never raises -- all errors are logged at DEBUG level and suppressed.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client = None
_client_initialised = False


def get_client():
    """Return the Langfuse client singleton, or None if not configured."""
    global _client, _client_initialised
    if _client_initialised:
        return _client
    with _client_lock:
        if _client_initialised:
            return _client
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        if not pk or not sk:
            logger.debug("Langfuse disabled: LANGFUSE_PUBLIC_KEY not set")
            _client_initialised = True
            return None
        try:
            from langfuse import Langfuse
            _client = Langfuse(
                public_key=pk,
                secret_key=sk,
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
            logger.info(
                "Langfuse client initialised (host=%s)",
                os.getenv("LANGFUSE_HOST", "cloud"),
            )
        except Exception as exc:
            logger.debug("Langfuse init failed (disabled): %s", exc)
            _client = None
        _client_initialised = True
        return _client


def start_trace(
    trace_id: str,
    name: str,
    *,
    invoice_id: Optional[int] = None,
    result_id: Optional[int] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Open a root Langfuse trace span. Returns the span object or None.

    Uses TraceContext to preserve the Django trace_id so logs and Langfuse
    traces can be correlated by trace_id.
    """
    lf = get_client()
    if not lf:
        return None
    try:
        from langfuse.types import TraceContext
        tc: TraceContext = {"trace_id": trace_id}
        span = lf.start_observation(
            trace_context=tc,
            name=name,
            as_type="span",
            input={
                "invoice_id": invoice_id,
                "result_id": result_id,
                "user_id": user_id,
            },
            metadata={
                **(metadata or {}),
                "invoice_id": invoice_id,
                "result_id": result_id,
                "django_trace_id": trace_id,
            },
        )
        # Set user_id and session_id as OTel span attributes (Langfuse v4 API).
        # These populate the Users and Sessions tabs in the Langfuse UI.
        if span is not None:
            try:
                from langfuse._client.attributes import TRACE_USER_ID, TRACE_SESSION_ID
                otel_span = getattr(span, "_otel_span", None)
                if otel_span is not None:
                    if user_id:
                        otel_span.set_attribute(TRACE_USER_ID, str(user_id))
                    if session_id:
                        otel_span.set_attribute(TRACE_SESSION_ID, session_id)
            except Exception:
                pass  # Non-fatal — traces still work, just without user/session
        return span
    except Exception as exc:
        logger.debug("Langfuse start_trace failed: %s", exc)
        return None


def start_span(
    parent: Any,
    name: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Open a child span under a parent span or trace. Returns span or None."""
    if not parent:
        return None
    try:
        return parent.start_observation(
            name=name,
            as_type="agent",
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.debug("Langfuse start_span failed: %s", exc)
        return None


def log_generation(
    span: Any,
    name: str,
    *,
    model: str,
    prompt_messages: List[Dict[str, Any]],
    completion: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Log an LLM generation as a child of the given span. Fail-silent."""
    if not span:
        return
    try:
        gen = span.start_observation(
            name=name,
            as_type="generation",
            model=model,
            input=prompt_messages,
            output=completion,
            usage_details={
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": total_tokens,
            },
            metadata=metadata or {},
        )
        gen.end()
    except Exception as exc:
        logger.debug("Langfuse log_generation failed: %s", exc)


def end_span(
    span: Any,
    *,
    output: Optional[Any] = None,
    level: str = "DEFAULT",
) -> None:
    """Close a Langfuse span, optionally setting output. Fail-silent."""
    if not span:
        return
    try:
        if output is not None:
            span.update(output=output)
        if level and level != "DEFAULT":
            span.update(level=level)
        span.end()
    except Exception as exc:
        logger.debug("Langfuse end_span failed: %s", exc)


def score_trace(
    trace_id: str,
    name: str,
    value: float,
    *,
    comment: str = "",
) -> None:
    """Attach a numeric score to a trace (e.g. confidence). Fail-silent."""
    lf = get_client()
    if not lf:
        return
    try:
        lf.create_score(
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment or None,
        )
    except Exception as exc:
        logger.debug("Langfuse score_trace failed: %s", exc)


def get_prompt(slug: str, label: str = "production") -> Optional[Any]:
    """Fetch a text prompt from Langfuse by name (slug).

    Returns the TextPromptClient if found, or None if not configured /
    prompt does not exist in Langfuse.  Uses the SDK's built-in 60s cache
    so hot paths are not affected.

    Args:
        slug:  The prompt name as stored in Langfuse (same as PromptRegistry slug,
               dots replaced with dashes, e.g. "agent-exception-analysis").
        label: Langfuse prompt label to fetch. "production" is the default
               active version. Use "latest" to get the most recently created.
    """
    lf = get_client()
    if not lf:
        return None
    try:
        client = lf.get_prompt(
            name=slug,
            label=label,
            type="text",
            cache_ttl_seconds=60,
            max_retries=1,
            fetch_timeout_seconds=2,
        )
        return client
    except Exception as exc:
        logger.debug("Langfuse get_prompt failed for '%s': %s", slug, exc)
        return None


def prompt_text(slug: str, label: str = "production") -> Optional[str]:
    """Return just the prompt text string from Langfuse, or None if not found."""
    client = get_prompt(slug, label=label)
    if client is None:
        return None
    try:
        return client.prompt
    except Exception:
        return None


def push_prompt(slug: str, content: str, *, labels: Optional[List[str]] = None) -> bool:
    """Create or update a text prompt in Langfuse.

    Returns True on success, False on failure.

    Args:
        slug:    Langfuse prompt name (use slug_to_langfuse_name() for conversion).
        content: Full prompt text.
        labels:  List of labels to attach. Defaults to ["production"].
    """
    lf = get_client()
    if not lf:
        return False
    try:
        lf.create_prompt(
            name=slug,
            prompt=content,
            labels=labels if labels is not None else ["production"],
            type="text",
        )
        logger.info("Pushed prompt '%s' to Langfuse", slug)
        return True
    except Exception as exc:
        logger.warning("Langfuse push_prompt failed for '%s': %s", slug, exc)
        return False


def slug_to_langfuse_name(slug: str) -> str:
    """Convert a PromptRegistry slug to a Langfuse-compatible prompt name.

    Langfuse prompt names cannot contain dots so we replace them with dashes.
    Example: "agent.exception_analysis" -> "agent-exception_analysis"
    """
    return slug.replace(".", "-")


def langfuse_name_to_slug(name: str) -> str:
    """Reverse of slug_to_langfuse_name."""
    return name.replace("-", ".", 1)


def flush() -> None:
    """Flush all pending Langfuse events. Call before process exit. Fail-silent."""
    lf = get_client()
    if not lf:
        return
    try:
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse flush failed: %s", exc)
