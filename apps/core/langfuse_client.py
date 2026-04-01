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
                environment=os.getenv("LANGFUSE_ENVIRONMENT", "development"),
            )
            logger.info(
                "Langfuse client initialised (host=%s, environment=%s)",
                os.getenv("LANGFUSE_HOST", "cloud"),
                os.getenv("LANGFUSE_ENVIRONMENT", "development"),
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
        # Set trace-level attributes + user/session via OTel span attributes (Langfuse v4 API).
        # start_observation creates a child span; the trace-level record itself
        # only gets name/input/metadata when we set them as OTel attributes.
        if span is not None:
            try:
                from langfuse._client.attributes import LangfuseOtelSpanAttributes as _A
                otel_span = getattr(span, "_otel_span", None)
                if otel_span is not None:
                    # Trace-level name, input, metadata
                    otel_span.set_attribute(_A.TRACE_NAME, name)
                    _trace_input = {
                        "invoice_id": invoice_id,
                        "result_id": result_id,
                        "user_id": user_id,
                    }
                    try:
                        import json as _json
                        otel_span.set_attribute(_A.TRACE_INPUT, _json.dumps(_trace_input, default=str))
                    except Exception:
                        pass
                    _trace_meta = {
                        **(metadata or {}),
                        "invoice_id": invoice_id,
                        "result_id": result_id,
                        "django_trace_id": trace_id,
                    }
                    try:
                        import json as _json
                        otel_span.set_attribute(_A.TRACE_METADATA, _json.dumps(_trace_meta, default=str))
                    except Exception:
                        pass
                    # User and session attribution
                    if user_id:
                        otel_span.set_attribute(_A.TRACE_USER_ID, str(user_id))
                    if session_id:
                        otel_span.set_attribute(_A.TRACE_SESSION_ID, session_id)
                    _env = os.getenv("LANGFUSE_ENVIRONMENT", "development")
                    otel_span.set_attribute(_A.ENVIRONMENT, _env)
            except Exception:
                pass  # Non-fatal -- traces still work, just without trace-level fields
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
    is_root: bool = False,
) -> None:
    """Close a Langfuse span, optionally setting output. Fail-silent.

    When *is_root* is True, also sets the trace-level output via OTel attribute
    so the output appears on the trace row in the Langfuse UI (not just on the
    child span).
    """
    if not span:
        return
    try:
        if output is not None:
            span.update(output=output)
            # Propagate output to trace-level record
            if is_root:
                try:
                    import json as _json
                    from langfuse._client.attributes import LangfuseOtelSpanAttributes as _A
                    otel_span = getattr(span, "_otel_span", None)
                    if otel_span is not None:
                        otel_span.set_attribute(
                            _A.TRACE_OUTPUT,
                            _json.dumps(output, default=str),
                        )
                except Exception:
                    pass
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


def score_observation(
    observation: Any,
    name: str,
    value: float,
    *,
    comment: str = "",
) -> None:
    """Attach a numeric score to a specific observation/span. Fail-silent.

    Uses the observation's trace_id + observation_id so the score is linked
    to both the trace and the specific span in the Langfuse UI.
    """
    if not observation:
        return
    lf = get_client()
    if not lf:
        return
    try:
        obs_id = getattr(observation, "id", None)
        trace_id = None
        # Try to read trace_id from the observation's OTel span context
        otel_span = getattr(observation, "_otel_span", None)
        if otel_span is not None:
            sc = getattr(otel_span, "get_span_context", lambda: None)()
            if sc is not None:
                trace_id = format(getattr(sc, "trace_id", 0), "032x")
        lf.create_score(
            trace_id=trace_id or "",
            observation_id=obs_id,
            name=name,
            value=value,
            comment=comment or None,
        )
    except Exception as exc:
        logger.debug("Langfuse score_observation failed: %s", exc)


def update_trace(
    span: Any,
    *,
    output: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    is_root: bool = False,
) -> None:
    """Update an existing trace/span with additional output or metadata. Fail-silent.

    When *is_root* is True, also propagates to trace-level OTel attributes.
    """
    if not span:
        return
    try:
        kwargs: Dict[str, Any] = {}
        if output is not None:
            kwargs["output"] = output
        if metadata is not None:
            kwargs["metadata"] = metadata
        if kwargs:
            span.update(**kwargs)
        # Propagate to trace-level OTel attributes
        if is_root:
            try:
                import json as _json
                from langfuse._client.attributes import LangfuseOtelSpanAttributes as _A
                otel_span = getattr(span, "_otel_span", None)
                if otel_span is not None:
                    if output is not None:
                        otel_span.set_attribute(_A.TRACE_OUTPUT, _json.dumps(output, default=str))
                    if metadata is not None:
                        otel_span.set_attribute(_A.TRACE_METADATA, _json.dumps(metadata, default=str))
            except Exception:
                pass
            span.update(**kwargs)
    except Exception as exc:
        logger.debug("Langfuse update_trace failed: %s", exc)


def get_prompt(slug: str, label: str = "production") -> Optional[Any]:
    """Fetch a chat prompt from Langfuse by name (slug).

    Returns the ChatPromptClient if found, or None if not configured /
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
            type="chat",
            cache_ttl_seconds=60,
            max_retries=1,
            fetch_timeout_seconds=2,
        )
        return client
    except Exception as exc:
        logger.debug("Langfuse get_prompt failed for '%s': %s", slug, exc)
        return None


def prompt_text(slug: str, label: str = "production") -> Optional[str]:
    """Return just the prompt text string from Langfuse chat prompt, or None if not found.

    For chat prompts stored as a single system message, returns the content of that
    message.  For multi-message chat prompts, returns the content of the first system
    message found, or all messages joined by newlines as a fallback.
    """
    client = get_prompt(slug, label=label)
    if client is None:
        return None
    try:
        messages = client.prompt  # list of ChatMessage objects or dicts
        if not messages:
            return None
        # Prefer the first system message
        for msg in messages:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            if role == "system" and content:
                return content
        # Fallback: return content of the first message regardless of role
        first = messages[0]
        return (
            first.get("content") if isinstance(first, dict)
            else getattr(first, "content", None)
        )
    except Exception:
        return None


def push_prompt(slug: str, content: str, *, labels: Optional[List[str]] = None) -> bool:
    """Create or update a chat prompt in Langfuse.

    Returns True on success, False on failure.

    Args:
        slug:    Langfuse prompt name (use slug_to_langfuse_name() for conversion).
        content: Full prompt text (stored as a single system message in chat format).
        labels:  List of labels to attach. Defaults to ["production"].
    """
    lf = get_client()
    if not lf:
        return False
    try:
        lf.create_prompt(
            name=slug,
            prompt=[{"role": "system", "content": content}],
            labels=labels if labels is not None else ["production"],
            type="chat",
        )
        logger.info("Pushed chat prompt '%s' to Langfuse", slug)
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
