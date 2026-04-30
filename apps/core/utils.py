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
    """Best-effort date parse from various formats.

    Fix: datetime must be checked BEFORE date because datetime is a subclass
    of date — isinstance(datetime_obj, date) returns True, making the
    datetime branch previously unreachable dead code.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
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


def parse_percentage(value) -> Optional[Decimal]:
    """Parse a percentage-like value such as ``15`` or ``15%``."""
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        cleaned = cleaned.replace(",", "").replace("%", "")
        cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
        if cleaned in {"", "-", ".", "-."}:
            return None
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def calculate_tax_percentage(tax_amount, base_amount) -> Optional[Decimal]:
    """Return ``tax_amount / base_amount * 100`` when both values are valid."""
    if tax_amount is None or base_amount is None:
        return None
    try:
        tax = Decimal(str(tax_amount))
        base = Decimal(str(base_amount))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if base == 0:
        return None
    return ((tax / base) * Decimal("100")).quantize(Decimal("0.01"))


def resolve_tax_percentage(raw_percentage=None, tax_amount=None, base_amount=None) -> Optional[Decimal]:
    """Prefer an extracted tax percentage, else derive it from amounts."""
    parsed = parse_percentage(raw_percentage)
    if parsed is not None:
        return parsed
    return calculate_tax_percentage(tax_amount, base_amount)


def resolve_line_tax_percentage(
    *,
    raw_percentage=None,
    tax_amount=None,
    quantity=None,
    unit_price=None,
    line_amount=None,
) -> Optional[Decimal]:
    """Resolve a line tax percentage from extracted or derived values."""
    base_amount = None
    try:
        if quantity is not None and unit_price is not None:
            base_amount = Decimal(str(quantity)) * Decimal(str(unit_price))
        elif line_amount is not None and tax_amount is not None:
            gross = Decimal(str(line_amount))
            tax = Decimal(str(tax_amount))
            if gross > tax:
                base_amount = gross - tax
    except (InvalidOperation, TypeError, ValueError):
        base_amount = None
    return resolve_tax_percentage(
        raw_percentage=raw_percentage,
        tax_amount=tax_amount,
        base_amount=base_amount,
    )


def normalize_category(value: Optional[str], fallback: str = "") -> str:
    """Normalize a business category label for storage/display."""
    if not value:
        return fallback
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    if not cleaned:
        return fallback
    return cleaned.title()


def pct_difference(a: Decimal, b: Decimal) -> Decimal:
    """Return absolute percentage difference: |a-b|/b * 100. Returns 100 if b is 0."""
    if b == 0:
        return Decimal("100.00") if a != 0 else Decimal("0.00")
    return abs((a - b) / b * 100).quantize(Decimal("0.01"))


def within_tolerance(a: Decimal, b: Decimal, tolerance_pct: float) -> bool:
    """Check whether the percentage difference between a and b is within tolerance."""
    return pct_difference(a, b) <= Decimal(str(tolerance_pct))


def build_case_remarks(
    *,
    invoice_status: str = "",
    case_status: str = "",
    match_status: str = "",
    unresolved_exceptions: int = 0,
    has_case: bool = False,
    policy_applied: str = "",
    review_decision: str = "",
) -> str:
    """Return a concise, user-facing remark for case/extraction list views."""
    invoice_status = (invoice_status or "").upper()
    case_status = (case_status or "").upper()
    match_status = (match_status or "").upper()
    policy_applied = (policy_applied or "").strip()
    review_decision = (review_decision or "").upper()
    unresolved_exceptions = max(0, int(unresolved_exceptions or 0))

    if not has_case:
        if invoice_status == "PENDING_APPROVAL":
            return "Pending extraction approval before reconciliation."
        if invoice_status in ("UPLOADED", "EXTRACTION_IN_PROGRESS", "EXTRACTED", "VALIDATED"):
            return "Extraction completed; waiting for approval/reconciliation trigger."
        if invoice_status == "READY_FOR_RECON":
            return "Ready for reconciliation; case orchestration pending."
        if invoice_status == "RECONCILED":
            return "Reconciled; case linkage/closure record pending sync."
        return "Awaiting next pipeline step."

    if case_status == "CLOSED":
        if unresolved_exceptions > 0:
            suffix = "s" if unresolved_exceptions != 1 else ""
            detail_parts = []
            if policy_applied:
                detail_parts.append(f"policy={policy_applied}")
            if review_decision:
                detail_parts.append(f"review={review_decision}")
            detail = ""
            if detail_parts:
                detail = " (" + "; ".join(detail_parts) + ")"

            return (
                f"Closed with {unresolved_exceptions} unresolved exception{suffix}; "
                f"closed by policy/review outcome{detail}."
            )
        if match_status == "MATCHED":
            return "Closed after matched reconciliation."
        if match_status in ("PARTIAL_MATCH", "UNMATCHED", "REQUIRES_REVIEW"):
            return f"Closed after review decision ({match_status})."
        return "Closed after workflow completion."

    if case_status == "PENDING_EXTRACTION_APPROVAL":
        return "Open: waiting for extraction approval to continue."
    if case_status in ("READY_FOR_REVIEW", "IN_REVIEW", "READY_FOR_APPROVAL", "APPROVAL_IN_PROGRESS"):
        return "Open: waiting for human review decision."
    if case_status == "ESCALATED":
        return "Open: escalated and awaiting senior reviewer action."
    if case_status in ("FAILED", "REJECTED"):
        return "Open: failed/rejected stage requires reprocess or manual action."

    if unresolved_exceptions > 0:
        suffix = "s" if unresolved_exceptions != 1 else ""
        return f"Open: {unresolved_exceptions} unresolved exception{suffix} to resolve before closure."

    if case_status:
        return f"Open: pending {case_status.replace('_', ' ').lower()} stage completion."

    return "Open: pending remaining workflow stages."


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


def safe_retry(task_self, exc, base_delay: int = 60):
    """Attempt Celery retry with exponential back-off; re-raise when retries
    exhausted or when running outside a Celery worker context.

    Back-off formula: ``base_delay * 2 ** retries`` (capped at 10 minutes).

    Use this instead of ``raise self.retry(exc=exc)`` in ``@shared_task``
    functions so tasks don't crash with a Redis ``ConnectionError`` on Windows.
    """
    try:
        retries = getattr(task_self.request, "retries", 0)
        countdown = min(base_delay * (2 ** retries), 600)  # max 10 minutes
        raise task_self.retry(exc=exc, countdown=countdown)
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
