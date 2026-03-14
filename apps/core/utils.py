"""Shared utility functions."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import dateparser


def normalize_string(value: Optional[str]) -> str:
    """Lowercase, strip, collapse whitespace, remove special chars."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def normalize_po_number(po_number: Optional[str]) -> str:
    """Normalise PO number: uppercase, strip leading zeros/prefixes."""
    if not po_number:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", po_number).upper()
    cleaned = re.sub(r"^PO0*", "", cleaned) or cleaned
    return cleaned


def normalize_invoice_number(invoice_number: Optional[str]) -> str:
    """Normalise invoice number: strip spaces and special chars, uppercase."""
    if not invoice_number:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", invoice_number).upper()


def parse_date(value) -> Optional[date]:
    """Best-effort date parse from various formats."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    parsed = dateparser.parse(str(value))
    return parsed.date() if parsed else None


def to_decimal(value, default: Decimal = Decimal("0.00")) -> Decimal:
    """Safely convert to Decimal."""
    if isinstance(value, Decimal):
        return value
    try:
        cleaned = str(value).strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        cleaned = cleaned.replace(",", "")
        cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return default


def pct_difference(a: Decimal, b: Decimal) -> Decimal:
    """Return absolute percentage difference: |a-b|/b * 100. Returns 100 if b is 0."""
    if b == 0:
        return Decimal("100.00") if a != 0 else Decimal("0.00")
    return abs((a - b) / b * 100).quantize(Decimal("0.01"))


def within_tolerance(a: Decimal, b: Decimal, tolerance_pct: float) -> bool:
    """Check whether the percentage difference between a and b is within tolerance."""
    return pct_difference(a, b) <= Decimal(str(tolerance_pct))


# ---------------------------------------------------------------------------
# Celery helpers — Windows/no-Redis fallback
# ---------------------------------------------------------------------------
import logging as _logging

_celery_logger = _logging.getLogger("apps.core.celery_utils")


def dispatch_task(task, *args, **kwargs):
    """Dispatch a Celery task: try async, fall back to synchronous .run().

    Use this instead of ``task.delay(...)`` anywhere a task is dispatched to
    ensure it works on Windows without Redis.
    """
    try:
        return task.delay(*args, **kwargs)
    except Exception:
        _celery_logger.info(
            "Celery broker unavailable — running %s synchronously", task.name,
        )
        return task.run(*args, **kwargs)


def safe_retry(task_self, exc):
    """Attempt Celery retry; if the broker is down, re-raise the original error.

    Use this instead of ``raise self.retry(exc=exc)`` in ``@shared_task``
    functions so tasks don't crash with a Redis ``ConnectionError`` on Windows.
    """
    try:
        raise task_self.retry(exc=exc)
    except (AttributeError, TypeError):
        # Running outside Celery context (sync fallback)
        raise exc
    except Exception as retry_exc:
        # Broker unavailable (e.g. Redis not running on Windows)
        if "Connection" in type(retry_exc).__name__ or "OperationalError" in type(retry_exc).__name__:
            _celery_logger.warning(
                "Celery retry failed (broker unavailable) — propagating original error"
            )
            raise exc
        raise
