"""
Structured logging utilities for the 3-Way PO Reconciliation platform.

Provides:
- JSON formatter for production
- Readable formatter for development
- Trace-aware logging helper
- Redaction of sensitive financial data
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ============================================================================
# Sensitive data redaction
# ============================================================================

# Patterns for financial PII
_BANK_ACCOUNT_RE = re.compile(r"\b\d{8,18}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b")
_TAX_ID_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")  # SSN-style
_VAT_RE = re.compile(r"\b\d{15}\b")  # Saudi VAT-style

_SENSITIVE_FIELD_NAMES = frozenset({
    "bank_account", "account_number", "iban", "swift", "tax_id",
    "ssn", "national_id", "bank_name", "routing_number", "sort_code",
    "credit_card", "card_number",
})


def redact_value(key: str, value: Any) -> Any:
    """Redact sensitive values based on field name or content patterns."""
    if not isinstance(value, str):
        return value
    key_lower = key.lower()
    if key_lower in _SENSITIVE_FIELD_NAMES:
        return "***REDACTED***"
    if key_lower in ("raw_text", "ocr_text", "raw_json", "raw_response"):
        if len(value) > 500:
            return f"[{len(value)} chars — truncated for logging]"
    return value


def redact_dict(data: Dict[str, Any], max_depth: int = 3) -> Dict[str, Any]:
    """Recursively redact sensitive fields from a dictionary."""
    if max_depth <= 0 or not isinstance(data, dict):
        return data
    result = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = redact_dict(v, max_depth - 1)
        elif isinstance(v, str):
            result[k] = redact_value(k, v)
        else:
            result[k] = v
    return result


def summarize_payload(data: Any, max_keys: int = 20, max_str_len: int = 200) -> Any:
    """Create a summarized version of a payload for audit storage."""
    if isinstance(data, dict):
        out = {}
        for i, (k, v) in enumerate(data.items()):
            if i >= max_keys:
                out["__truncated__"] = f"{len(data) - max_keys} more keys"
                break
            out[k] = summarize_payload(v, max_keys, max_str_len)
        return out
    if isinstance(data, list):
        if len(data) > 10:
            return data[:10] + [f"... {len(data) - 10} more items"]
        return [summarize_payload(item, max_keys, max_str_len) for item in data]
    if isinstance(data, str) and len(data) > max_str_len:
        return data[:max_str_len] + f"... [{len(data)} total chars]"
    return data


# ============================================================================
# JSON log formatter (production)
# ============================================================================

class JSONLogFormatter(logging.Formatter):
    """Structured JSON log formatter with trace context injection."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject trace context if available
        from apps.core.trace import TraceContext
        ctx = TraceContext.get_current()
        if ctx:
            trace_dict = ctx.as_log_dict()
            log_entry.update(trace_dict)

        # Additional structured fields from extra
        for key in (
            "event_name", "trace_id", "span_id", "parent_span_id",
            "invoice_id", "case_id", "reconciliation_result_id",
            "review_assignment_id", "agent_run_id",
            "actor_user_id", "actor_email", "actor_primary_role",
            "permission_checked", "permission_source", "access_granted",
            "service_name", "endpoint_name", "duration_ms",
            "success", "error_code",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        if record.exc_info and record.exc_info[1]:
            log_entry["exception_class"] = type(record.exc_info[1]).__name__
            log_entry["exception_message"] = str(record.exc_info[1])[:500]

        return json.dumps(log_entry, default=str)


# ============================================================================
# Readable dev formatter
# ============================================================================

class DevLogFormatter(logging.Formatter):
    """Human-readable dev formatter with optional trace context."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        parts = [base]

        from apps.core.trace import TraceContext
        ctx = TraceContext.get_current()
        if ctx and ctx.trace_id:
            parts.append(f"[trace={ctx.trace_id[:12]}]")
        if getattr(record, "invoice_id", None):
            parts.append(f"[inv={record.invoice_id}]")
        if getattr(record, "duration_ms", None) is not None:
            parts.append(f"[{record.duration_ms}ms]")
        return " ".join(parts)


# ============================================================================
# Trace-aware logging helper
# ============================================================================

class TraceLogger:
    """Convenience wrapper that injects trace context into log records."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def _extra(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from apps.core.trace import TraceContext
        ctx = TraceContext.get_current()
        d: Dict[str, Any] = {}
        if ctx:
            d.update(ctx.as_log_dict())
        if extra:
            d.update(extra)
        return d

    def info(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.info(msg, *args, extra=self._extra(extra), **kwargs)

    def warning(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.warning(msg, *args, extra=self._extra(extra), **kwargs)

    def error(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.error(msg, *args, extra=self._extra(extra), **kwargs)

    def debug(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.debug(msg, *args, extra=self._extra(extra), **kwargs)

    def exception(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs):
        self.logger.exception(msg, *args, extra=self._extra(extra), **kwargs)


def get_trace_logger(name: str) -> TraceLogger:
    """Factory for trace-aware loggers."""
    return TraceLogger(name)


# ============================================================================
# Duration timer helper
# ============================================================================

class DurationTimer:
    """Context manager for measuring operation duration in milliseconds."""

    def __init__(self):
        self.start_time: float = 0
        self.duration_ms: int = 0

    def __enter__(self):
        self.start_time = time.monotonic()
        return self

    def __exit__(self, *args):
        self.duration_ms = int((time.monotonic() - self.start_time) * 1000)
