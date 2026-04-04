"""ERP Integration Langfuse Helpers -- fail-silent tracing and scoring.

Provides a thin, ERP-specific layer over the core Langfuse client
(``apps.core.langfuse_client``) with:

- metadata sanitisation (strips auth tokens, passwords, large payloads)
- error categorisation (maps raw exceptions to safe categories)
- deterministic observation-level and trace-level ERP scores
- source provenance / freshness helpers

All functions are fail-silent: they never raise and never block ERP
operations regardless of Langfuse availability.
"""
from __future__ import annotations

import time
import logging
from typing import Any, Dict, List, Optional

from apps.core.evaluation_constants import (
    ERP_CACHE_HIT,
    ERP_CACHE_STALE,
    ERP_DB_FALLBACK_SUCCESS,
    ERP_DB_FALLBACK_USED,
    ERP_DOCUMENT_NUMBER_PRESENT,
    ERP_DUPLICATE_FOUND,
    ERP_LIVE_LOOKUP_LATENCY_OK,
    ERP_LIVE_LOOKUP_RATE_LIMITED,
    ERP_LIVE_LOOKUP_SUCCESS,
    ERP_LIVE_LOOKUP_TIMEOUT,
    ERP_RESOLUTION_AUTHORITATIVE,
    ERP_RESOLUTION_FRESH,
    ERP_RESOLUTION_LATENCY_OK,
    ERP_RESOLUTION_RESULT_PRESENT,
    ERP_RESOLUTION_SUCCESS,
    ERP_RESOLUTION_USED_FALLBACK,
    ERP_SUBMISSION_ATTEMPTED,
    ERP_SUBMISSION_LATENCY_OK,
    ERP_SUBMISSION_RETRYABLE_FAILURE,
    ERP_SUBMISSION_SUCCESS,
    LATENCY_THRESHOLD_ERP_MS,
    FALLBACK_USED_BUT_SUCCESSFUL,
    STALE_DATA_ACCEPTED,
)

logger = logging.getLogger(__name__)

# =====================================================================
# Constants
# =====================================================================

# Latency threshold (ms) -- operations faster than this are "OK"
ERP_LATENCY_THRESHOLD_MS = LATENCY_THRESHOLD_ERP_MS

# Keys that must never appear in Langfuse metadata
_SENSITIVE_KEYS = frozenset({
    "api_key", "api_secret", "apikey", "apisecret",
    "token", "access_token", "refresh_token", "bearer",
    "password", "passwd", "secret", "secret_key",
    "authorization", "auth_header", "auth_token",
    "credentials", "client_secret", "private_key",
    "cookie", "session_token", "x-api-key",
})

# Safe error categories for Langfuse metadata
_ERROR_CATEGORY_MAP = {
    "timeout": ["timeout", "timed out", "read timed out", "connect timed out"],
    "unauthorized": ["401", "unauthorized", "authentication failed", "forbidden", "403"],
    "rate_limited": ["429", "rate limit", "too many requests", "throttl"],
    "validation_error": ["400", "bad request", "validation", "invalid"],
    "connector_unavailable": [
        "connection refused", "connection error", "connect error",
        "unreachable", "name resolution", "dns", "502", "503", "504",
    ],
    "empty_result": ["not found", "no results", "empty", "none returned"],
    "normalization_failed": ["normalization", "parse error", "decode"],
    "submission_rejected": ["rejected", "duplicate", "already exists"],
}

# Source type labels for provenance tracking
SOURCE_CACHE = "CACHE"
SOURCE_LIVE_API = "API"
SOURCE_MIRROR_DB = "MIRROR_DB"
SOURCE_DB_FALLBACK = "DB_FALLBACK"
SOURCE_MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
SOURCE_NONE = "NONE"


# =====================================================================
# Metadata sanitisation
# =====================================================================

def sanitize_erp_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *meta* with sensitive keys removed.

    Recursively strips keys whose lowercase name matches ``_SENSITIVE_KEYS``.
    Values longer than 2000 chars are truncated with a ``[truncated]``
    marker so large ERP payloads never leak into Langfuse.
    """
    if not meta:
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in meta.items():
        if key.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            sanitized[key] = sanitize_erp_metadata(value)
        elif isinstance(value, str) and len(value) > 2000:
            sanitized[key] = value[:200] + "... [truncated]"
        else:
            sanitized[key] = value
    return sanitized


def sanitize_erp_error(error: Optional[str]) -> str:
    """Map a raw error message to a safe category string.

    Never exposes raw stack traces or credential-containing error text
    to Langfuse.  Returns the first matching category or ``"unknown_error"``.
    """
    if not error:
        return ""
    lower = error.lower()
    for category, keywords in _ERROR_CATEGORY_MAP.items():
        for kw in keywords:
            if kw in lower:
                return category
    return "unknown_error"


# =====================================================================
# Source provenance helpers
# =====================================================================

def build_source_chain(
    cache_attempted: bool = False,
    cache_hit: bool = False,
    live_attempted: bool = False,
    live_success: bool = False,
    mirror_attempted: bool = False,
    mirror_hit: bool = False,
    db_fallback_attempted: bool = False,
    db_fallback_hit: bool = False,
) -> List[str]:
    """Build a compact list of sources attempted in resolution order."""
    chain: List[str] = []
    if cache_attempted:
        chain.append(f"cache:{'hit' if cache_hit else 'miss'}")
    if mirror_attempted:
        chain.append(f"mirror_db:{'hit' if mirror_hit else 'miss'}")
    if live_attempted:
        chain.append(f"live_api:{'ok' if live_success else 'fail'}")
    if db_fallback_attempted:
        chain.append(f"db_fallback:{'hit' if db_fallback_hit else 'miss'}")
    return chain


def freshness_status_label(is_stale: bool, source_type: str) -> str:
    """Return a compact freshness status label."""
    if source_type in (SOURCE_CACHE, SOURCE_LIVE_API):
        return "fresh"
    if is_stale:
        return "stale"
    return "fresh"


def is_authoritative_source(source_type: str) -> bool:
    """Return True if the source is considered authoritative (live or cache)."""
    return source_type in (SOURCE_LIVE_API, SOURCE_CACHE)


# =====================================================================
# Span helpers -- thin wrappers with ERP-specific defaults
# =====================================================================

def start_erp_span(
    parent: Any,
    name: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Open an ERP child span under *parent*, sanitising metadata.

    Returns the span object or None if Langfuse is unavailable.
    """
    try:
        from apps.core.langfuse_client import start_span_safe
        return start_span_safe(parent, name, metadata=sanitize_erp_metadata(metadata or {}))
    except Exception:
        return None


def end_erp_span(
    span: Any,
    *,
    output: Optional[Dict[str, Any]] = None,
    level: str = "DEFAULT",
) -> None:
    """Close an ERP span with sanitised output."""
    if span is None:
        return
    try:
        from apps.core.langfuse_client import end_span_safe
        end_span_safe(span, output=sanitize_erp_metadata(output or {}), level=level)
    except Exception:
        pass


# =====================================================================
# Score helpers
# =====================================================================

def score_erp_observation(
    observation: Any,
    name: str,
    value: float,
    *,
    comment: str = "",
) -> None:
    """Attach a score to an ERP observation span. Fail-silent."""
    if observation is None:
        return
    try:
        from apps.core.langfuse_client import score_observation_safe
        score_observation_safe(observation, name, value, comment=comment)
    except Exception:
        pass


def score_erp_trace(
    trace_id: str,
    name: str,
    value: float,
    *,
    comment: str = "",
    span: Any = None,
) -> None:
    """Attach a trace-level ERP score. Fail-silent."""
    if not trace_id:
        return
    try:
        from apps.core.langfuse_client import score_trace_safe
        score_trace_safe(trace_id, name, value, comment=comment, span=span)
    except Exception:
        pass


# =====================================================================
# Resolution tracing (replaces the old _trace_resolve)
# =====================================================================

def trace_erp_resolution(
    lf_parent_span: Any,
    resolution_name: str,
    resolve_fn,
    *,
    operation_type: str = "",
    entity_type: str = "",
    entity_key: str = "",
    connector_name: str = "",
    connector_type: str = "",
    invoice_id: Optional[int] = None,
    posting_run_id: Optional[int] = None,
    reconciliation_result_id: Optional[int] = None,
    case_id: Optional[int] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
):
    """Wrap a resolution call with a full Langfuse span tree + scores.

    Creates an ``erp_resolution`` parent span and, inside it, records
    the result as sanitised metadata. Emits observation-level scores
    for evaluation.

    Returns the result from ``resolve_fn()`` -- never raises.
    """
    _lf_span = None
    start = time.monotonic()

    # Build safe metadata
    meta = {
        "operation_type": operation_type or resolution_name,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "connector_name": connector_name,
        "connector_type": connector_type,
        "invoice_id": invoice_id,
        "posting_run_id": posting_run_id,
        "reconciliation_result_id": reconciliation_result_id,
        "case_id": case_id,
    }
    if extra_metadata:
        meta.update(extra_metadata)

    try:
        _lf_span = start_erp_span(lf_parent_span, "erp_resolution", metadata=meta)
    except Exception:
        pass

    result = None
    error_msg = None
    try:
        result = resolve_fn()
        return result
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        _emit_resolution_span_output(
            _lf_span, result, start, error_msg,
            resolution_name=resolution_name,
            entity_key=entity_key,
            connector_name=connector_name,
        )


def _emit_resolution_span_output(
    span: Any,
    result: Any,
    start_time: float,
    error_msg: Optional[str],
    *,
    resolution_name: str = "",
    entity_key: str = "",
    connector_name: str = "",
) -> None:
    """Close the erp_resolution span and emit observation scores."""
    if span is None:
        return
    try:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        resolved = False
        source_type = SOURCE_NONE
        cache_hit = False
        fallback_used = False
        confidence = 0.0
        is_stale = False
        stale_reason = ""
        warnings_count = 0
        source_chain: List[str] = []

        if result is not None:
            resolved = bool(getattr(result, "resolved", False))
            source_type = str(getattr(result, "source_type", SOURCE_NONE))
            cache_hit = source_type == SOURCE_CACHE
            fallback_used = bool(getattr(result, "fallback_used", False))
            confidence = float(getattr(result, "confidence", 0.0))
            is_stale = bool(getattr(result, "is_stale", False))
            stale_reason = str(getattr(result, "stale_reason", ""))
            warnings_count = len(getattr(result, "warnings", []))

            # Build source chain from what we know
            # Cache hit means cache was the source
            if cache_hit:
                source_chain = ["cache:hit"]
            elif source_type == SOURCE_LIVE_API:
                source_chain = ["cache:miss", "live_api:ok"]
            elif source_type == SOURCE_MIRROR_DB:
                source_chain = ["cache:miss", "mirror_db:hit"]
            elif source_type == SOURCE_DB_FALLBACK:
                source_chain = ["cache:miss", "mirror_db:miss", "db_fallback:hit"]
            elif not resolved:
                source_chain = ["cache:miss", "all:miss"]

        _fresh = freshness_status_label(is_stale, source_type)
        _authoritative = is_authoritative_source(source_type)

        output = {
            "resolved": resolved,
            "source_type": source_type,
            "source_used": source_type,
            "source_chain_attempted": source_chain,
            "cache_hit": cache_hit,
            "live_lookup_attempted": source_type == SOURCE_LIVE_API or (
                not cache_hit and not resolved
            ),
            "db_fallback_used": fallback_used,
            "confidence": confidence,
            "freshness_status": _fresh,
            "is_stale": is_stale,
            "latency_ms": elapsed_ms,
            "normalized_result_present": resolved,
            "warnings_count": warnings_count,
            "success": resolved,
        }
        if error_msg:
            output["error_type"] = sanitize_erp_error(error_msg)
        if stale_reason:
            output["stale_reason"] = stale_reason[:200]

        level = "DEFAULT"
        if error_msg or not resolved:
            level = "WARNING"
        if error_msg and "unauthorized" in error_msg.lower():
            level = "ERROR"

        end_erp_span(span, output=output, level=level)

        # -- Observation-level scores --
        score_erp_observation(span, ERP_RESOLUTION_SUCCESS, 1.0 if resolved else 0.0,
                             comment=f"resolution={resolution_name} key={entity_key}")
        score_erp_observation(span, ERP_RESOLUTION_LATENCY_OK,
                             1.0 if elapsed_ms <= ERP_LATENCY_THRESHOLD_MS else 0.0,
                             comment=f"{elapsed_ms}ms")
        score_erp_observation(span, ERP_RESOLUTION_RESULT_PRESENT,
                             1.0 if resolved else 0.0)
        score_erp_observation(span, ERP_RESOLUTION_FRESH,
                             0.0 if is_stale else 1.0,
                             comment=_fresh)
        score_erp_observation(span, ERP_RESOLUTION_AUTHORITATIVE,
                             1.0 if _authoritative else 0.0,
                             comment=source_type)
        score_erp_observation(span, ERP_RESOLUTION_USED_FALLBACK,
                             1.0 if fallback_used else 0.0)

        # Cache score
        if cache_hit:
            score_erp_observation(span, ERP_CACHE_HIT, 1.0)
            score_erp_observation(span, ERP_CACHE_STALE, 0.0)
        elif source_type != SOURCE_LIVE_API:
            score_erp_observation(span, ERP_CACHE_HIT, 0.0)

        # DB fallback score
        if fallback_used:
            score_erp_observation(span, ERP_DB_FALLBACK_USED, 1.0)
            score_erp_observation(span, ERP_DB_FALLBACK_SUCCESS, 1.0 if resolved else 0.0)

        # Decision-quality evaluation signals
        if fallback_used and resolved:
            score_erp_observation(span, FALLBACK_USED_BUT_SUCCESSFUL, 1.0)
        if is_stale and resolved:
            score_erp_observation(span, STALE_DATA_ACCEPTED, 1.0)

    except Exception:
        logger.debug("Failed to emit ERP resolution span output", exc_info=True)


# =====================================================================
# Submission tracing
# =====================================================================

def trace_erp_submission(
    lf_parent: Any,
    submission_fn,
    *,
    submission_type: str = "",
    connector_name: str = "",
    invoice_id: Optional[int] = None,
    posting_run_id: Optional[int] = None,
    submission_mode: str = "sync",
    extra_metadata: Optional[Dict[str, Any]] = None,
):
    """Wrap an ERP submission call with a Langfuse span + scores.

    If *lf_parent* is None, creates a standalone root trace.
    Returns the result from ``submission_fn()`` -- never raises.
    """
    _lf_span = None
    _lf_root = None
    start = time.monotonic()

    meta = {
        "submission_type": submission_type,
        "connector_name": connector_name,
        "invoice_id": invoice_id,
        "posting_run_id": posting_run_id,
        "submission_mode": submission_mode,
    }
    if extra_metadata:
        meta.update(extra_metadata)

    try:
        if lf_parent is not None:
            _lf_span = start_erp_span(lf_parent, "erp_submission", metadata=meta)
        else:
            # Standalone root trace for submission outside a pipeline
            from apps.core.langfuse_client import start_trace_safe
            import uuid
            _trace_id = (
                f"erp-sub-{posting_run_id}" if posting_run_id
                else f"erp-inv-{invoice_id}" if invoice_id
                else uuid.uuid4().hex
            )
            _lf_root = start_trace_safe(
                _trace_id,
                "erp_submission_pipeline",
                invoice_id=invoice_id,
                metadata=meta,
            )
            _lf_span = start_erp_span(_lf_root, "erp_submission", metadata=meta)
    except Exception:
        pass

    result = None
    error_msg = None
    try:
        result = submission_fn()
        return result
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        _emit_submission_span_output(
            _lf_span, _lf_root, result, start, error_msg,
            submission_type=submission_type,
            connector_name=connector_name,
        )


def _emit_submission_span_output(
    span: Any,
    root_span: Any,
    result: Any,
    start_time: float,
    error_msg: Optional[str],
    *,
    submission_type: str = "",
    connector_name: str = "",
) -> None:
    """Close the erp_submission span and emit scores."""
    try:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        success = False
        status = ""
        doc_number_present = False
        retryable = False

        if result is not None:
            success = bool(getattr(result, "success", False))
            status = str(getattr(result, "status", ""))
            doc_number_present = bool(getattr(result, "erp_document_number", ""))
            retryable = status in ("FAILED", "TIMEOUT")

        output = {
            "submission_attempted": True,
            "submission_success": success,
            "response_status": status,
            "erp_document_number_present": doc_number_present,
            "latency_ms": elapsed_ms,
            "retryable_failure": retryable and not success,
            "connector_name": connector_name,
            "payload_built": True,
        }
        if error_msg:
            output["error_type"] = sanitize_erp_error(error_msg)

        level = "DEFAULT" if success else "ERROR"
        end_erp_span(span, output=output, level=level)

        # -- Observation scores --
        score_erp_observation(span, ERP_SUBMISSION_ATTEMPTED, 1.0)
        score_erp_observation(span, ERP_SUBMISSION_SUCCESS, 1.0 if success else 0.0,
                             comment=f"type={submission_type} status={status}")
        score_erp_observation(span, ERP_SUBMISSION_LATENCY_OK,
                             1.0 if elapsed_ms <= ERP_LATENCY_THRESHOLD_MS else 0.0,
                             comment=f"{elapsed_ms}ms")
        score_erp_observation(span, ERP_SUBMISSION_RETRYABLE_FAILURE,
                             1.0 if (retryable and not success) else 0.0)
        score_erp_observation(span, ERP_DOCUMENT_NUMBER_PRESENT,
                             1.0 if doc_number_present else 0.0)

        # Close standalone root trace if created
        if root_span is not None:
            from apps.core.langfuse_client import end_span_safe
            root_output = {
                "erp_final_success": success,
                "submission_type": submission_type,
                "latency_ms": elapsed_ms,
            }
            end_span_safe(root_span, output=root_output, level=level, is_root=True)

    except Exception:
        logger.debug("Failed to emit ERP submission span output", exc_info=True)


# =====================================================================
# Duplicate check tracing
# =====================================================================

def trace_erp_duplicate_check(
    lf_parent: Any,
    check_fn,
    *,
    invoice_id: Optional[int] = None,
    invoice_number: str = "",
    vendor_code: str = "",
    posting_run_id: Optional[int] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
):
    """Wrap a duplicate invoice check with a Langfuse span + scores."""
    _lf_span = None
    start = time.monotonic()

    meta = {
        "operation_type": "duplicate_invoice_check",
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "vendor_code": vendor_code,
        "posting_run_id": posting_run_id,
    }
    if extra_metadata:
        meta.update(extra_metadata)

    try:
        _lf_span = start_erp_span(lf_parent, "erp_duplicate_check", metadata=meta)
    except Exception:
        pass

    result = None
    error_msg = None
    try:
        result = check_fn()
        return result
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        _emit_duplicate_check_output(_lf_span, result, start, error_msg)


def _emit_duplicate_check_output(
    span: Any,
    result: Any,
    start_time: float,
    error_msg: Optional[str],
) -> None:
    """Close the erp_duplicate_check span and emit scores."""
    if span is None:
        return
    try:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        duplicate_found = False
        match_type = ""

        if result is not None:
            resolved = bool(getattr(result, "resolved", False))
            value = getattr(result, "value", None) or {}
            duplicate_found = resolved and value.get("is_duplicate", False)
            match_type = value.get("duplicate_match_type", "exact" if duplicate_found else "")

        output = {
            "duplicate_found": duplicate_found,
            "duplicate_match_type": match_type,
            "latency_ms": elapsed_ms,
            "success": error_msg is None,
        }
        if error_msg:
            output["error_type"] = sanitize_erp_error(error_msg)

        level = "WARNING" if duplicate_found else "DEFAULT"
        end_erp_span(span, output=output, level=level)

        score_erp_observation(span, ERP_DUPLICATE_FOUND, 1.0 if duplicate_found else 0.0,
                             comment=match_type)

    except Exception:
        logger.debug("Failed to emit duplicate check span output", exc_info=True)


# =====================================================================
# Cache tracing  (for use inside BaseResolver.resolve)
# =====================================================================

def trace_erp_cache_lookup(
    lf_parent: Any,
    cache_fn,
    *,
    cache_key: str = "",
    resolution_type: str = "",
) -> Any:
    """Wrap a cache lookup with a Langfuse span + score."""
    _lf_span = None
    start = time.monotonic()
    try:
        _lf_span = start_erp_span(lf_parent, "erp_cache_lookup", metadata={
            "cache_key": cache_key,
            "resolution_type": resolution_type,
        })
    except Exception:
        pass

    cached = None
    try:
        cached = cache_fn()
        return cached
    finally:
        if _lf_span is not None:
            try:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                hit = cached is not None
                end_erp_span(_lf_span, output={
                    "cache_hit": hit,
                    "latency_ms": elapsed_ms,
                    "cache_key": cache_key,
                })
                score_erp_observation(_lf_span, ERP_CACHE_HIT, 1.0 if hit else 0.0,
                                     comment=f"key={cache_key[:50]}")
            except Exception:
                pass


def trace_erp_live_lookup(
    lf_parent: Any,
    lookup_fn,
    *,
    connector_name: str = "",
    capability: str = "",
    resolution_type: str = "",
) -> Any:
    """Wrap a live ERP API lookup with a Langfuse span + scores."""
    _lf_span = None
    start = time.monotonic()
    try:
        _lf_span = start_erp_span(lf_parent, "erp_live_lookup", metadata={
            "connector_name": connector_name,
            "capability": capability,
            "resolution_type": resolution_type,
        })
    except Exception:
        pass

    result = None
    error_msg = None
    try:
        result = lookup_fn()
        return result
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        if _lf_span is not None:
            try:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                success = result is not None and getattr(result, "resolved", False)
                _cat = sanitize_erp_error(error_msg) if error_msg else ""
                timeout = _cat == "timeout"
                rate_limited = _cat == "rate_limited"

                end_erp_span(_lf_span, output={
                    "success": success,
                    "connector_name": connector_name,
                    "latency_ms": elapsed_ms,
                    "timeout": timeout,
                    "rate_limited": rate_limited,
                    "error_type": _cat if error_msg else "",
                }, level="ERROR" if error_msg else "DEFAULT")

                score_erp_observation(_lf_span, ERP_LIVE_LOOKUP_SUCCESS,
                                     1.0 if success else 0.0)
                score_erp_observation(_lf_span, ERP_LIVE_LOOKUP_LATENCY_OK,
                                     1.0 if elapsed_ms <= ERP_LATENCY_THRESHOLD_MS else 0.0,
                                     comment=f"{elapsed_ms}ms")
                score_erp_observation(_lf_span, ERP_LIVE_LOOKUP_RATE_LIMITED,
                                     1.0 if rate_limited else 0.0)
                score_erp_observation(_lf_span, ERP_LIVE_LOOKUP_TIMEOUT,
                                     1.0 if timeout else 0.0)
            except Exception:
                pass


def trace_erp_db_fallback(
    lf_parent: Any,
    fallback_fn,
    *,
    fallback_source_name: str = "",
    resolution_type: str = "",
) -> Any:
    """Wrap a DB fallback lookup with a Langfuse span + scores."""
    _lf_span = None
    start = time.monotonic()
    try:
        _lf_span = start_erp_span(lf_parent, "erp_db_fallback", metadata={
            "fallback_source_name": fallback_source_name,
            "resolution_type": resolution_type,
        })
    except Exception:
        pass

    result = None
    error_msg = None
    try:
        result = fallback_fn()
        return result
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        if _lf_span is not None:
            try:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                success = result is not None and getattr(result, "resolved", False)
                end_erp_span(_lf_span, output={
                    "success": success,
                    "fallback_source_name": fallback_source_name,
                    "latency_ms": elapsed_ms,
                }, level="DEFAULT" if success else "WARNING")

                score_erp_observation(_lf_span, ERP_DB_FALLBACK_USED, 1.0)
                score_erp_observation(_lf_span, ERP_DB_FALLBACK_SUCCESS,
                                     1.0 if success else 0.0,
                                     comment=fallback_source_name)
            except Exception:
                pass
