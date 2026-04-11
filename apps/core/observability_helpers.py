"""Cross-flow observability helpers -- correlation, metadata, sanitisation, latency.

Provides shared utilities used by ALL pipeline entry points (extraction,
reconciliation, agents, cases, posting, ERP, reviews) to ensure consistent
Langfuse trace metadata, session attribution, and evaluation-ready scoring.

This module is the ONLY place where:
- session_id derivation logic lives
- cross-linking metadata is assembled
- general Langfuse metadata sanitisation is performed
- latency scoring thresholds are applied

All functions are fail-silent and never raise.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Reuse sensitive-key detection from ERP helpers
_SENSITIVE_KEYS = frozenset({
    "api_key", "api_secret", "apikey", "apisecret",
    "token", "access_token", "refresh_token", "bearer",
    "password", "passwd", "secret", "secret_key",
    "authorization", "auth_header", "auth_token",
    "credentials", "client_secret", "private_key",
    "cookie", "session_token", "x-api-key",
})

# Keys whose values may contain raw OCR / large text -- truncate aggressively
_LARGE_TEXT_KEYS = frozenset({
    "ocr_text", "raw_text", "full_text", "content", "body",
    "payload", "raw_payload", "request_body", "response_body",
    "prompt_text", "completion_text",
})

# Max length for string values in Langfuse metadata
_MAX_VALUE_LEN = 2000
_MAX_LARGE_TEXT_LEN = 300


# =====================================================================
# Session ID derivation
# =====================================================================

def derive_session_id(
    *,
    case_number: Optional[str] = None,
    invoice_id: Optional[int] = None,
    document_upload_id: Optional[int] = None,
    case_id: Optional[int] = None,
) -> Optional[str]:
    """Derive a standardised Langfuse session_id.

    Convention (in priority order):
    1. ``case-{case_number}`` when case_number is available (earliest anchor)
    2. ``invoice-{invoice_id}`` when invoice_id is available
    3. ``upload-{document_upload_id}`` for extraction pre-invoice
    4. ``case-{case_id}`` for case-first flows (numeric fallback)
    5. None if nothing is available
    """
    if case_number:
        return f"case-{case_number}"
    if invoice_id:
        return f"invoice-{invoice_id}"
    if document_upload_id:
        return f"upload-{document_upload_id}"
    if case_id:
        return f"case-{case_id}"
    return None


# =====================================================================
# Cross-linking metadata builder
# =====================================================================

def build_observability_context(
    *,
    tenant_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    document_upload_id: Optional[int] = None,
    extraction_result_id: Optional[int] = None,
    extraction_run_id: Optional[int] = None,
    reconciliation_result_id: Optional[int] = None,
    reconciliation_run_id: Optional[int] = None,
    case_id: Optional[int] = None,
    case_number: Optional[str] = None,
    posting_run_id: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    trigger: Optional[str] = None,
    po_number: Optional[str] = None,
    vendor_code: Optional[str] = None,
    vendor_name: Optional[str] = None,
    reconciliation_mode: Optional[str] = None,
    match_status: Optional[str] = None,
    case_stage: Optional[str] = None,
    posting_stage: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a compact cross-linking metadata dict for Langfuse traces/spans.

    Filters out None/empty values so the dict stays minimal.
    """
    raw = {
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "document_upload_id": document_upload_id,
        "extraction_result_id": extraction_result_id,
        "extraction_run_id": extraction_run_id,
        "reconciliation_result_id": reconciliation_result_id,
        "reconciliation_run_id": reconciliation_run_id,
        "case_id": case_id,
        "case_number": case_number,
        "posting_run_id": posting_run_id,
        "actor_user_id": actor_user_id,
        "trigger": trigger,
        "po_number": po_number,
        "vendor_code": vendor_code,
        "vendor_name": vendor_name,
        "reconciliation_mode": reconciliation_mode,
        "match_status": match_status,
        "case_stage": case_stage,
        "posting_stage": posting_stage,
        "source": source,
    }
    return {k: v for k, v in raw.items() if v is not None and v != ""}


def merge_trace_metadata(
    base: Optional[Dict[str, Any]],
    *extras: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge multiple metadata dicts (left-to-right, later wins). Filters None."""
    merged: Dict[str, Any] = {}
    if base:
        merged.update(base)
    for extra in extras:
        if extra:
            merged.update(extra)
    return {k: v for k, v in merged.items() if v is not None and v != ""}


# =====================================================================
# Metadata sanitisation (general -- not ERP-specific)
# =====================================================================

def sanitize_langfuse_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of *meta* safe for Langfuse ingestion.

    - Strips sensitive keys (passwords, tokens, secrets)
    - Truncates large string values (OCR text, payloads)
    - Never raises
    """
    if not meta:
        return {}
    try:
        return _sanitize_dict(meta)
    except Exception:
        return {}


def _sanitize_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in d.items():
        lk = key.lower()
        if lk in _SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            sanitized[key] = _sanitize_dict(value)
        elif isinstance(value, str):
            max_len = _MAX_LARGE_TEXT_LEN if lk in _LARGE_TEXT_KEYS else _MAX_VALUE_LEN
            if len(value) > max_len:
                sanitized[key] = value[:max_len] + "... [truncated]"
            else:
                sanitized[key] = value
        elif isinstance(value, (list, tuple)):
            # Truncate large lists
            if len(value) > 50:
                sanitized[key] = list(value[:50]) + ["... [truncated]"]
            else:
                sanitized[key] = value
        else:
            sanitized[key] = value
    return sanitized


def sanitize_summary_text(text: Optional[str], max_length: int = 2000) -> str:
    """Sanitise LLM-generated or free-text content before sending to Langfuse.

    - Strips non-ASCII characters (Unicode arrows, fancy quotes, etc.)
    - Truncates to max_length
    - Never raises
    """
    if not text:
        return ""
    try:
        # Strip non-ASCII (same as _sanitise_text in AGENT_ARCHITECTURE.md)
        cleaned = re.sub(r"[^\x00-\x7F]", "", text)
        if len(cleaned) > max_length:
            cleaned = cleaned[:max_length] + "... [truncated]"
        return cleaned
    except Exception:
        return str(text)[:max_length] if text else ""


# =====================================================================
# Latency scoring
# =====================================================================

def latency_ok(latency_ms: Union[int, float], threshold_ms: Union[int, float]) -> float:
    """Return 1.0 if latency is within threshold, 0.0 otherwise.

    Convenience for emitting latency scores at observation level.
    """
    try:
        return 1.0 if float(latency_ms) <= float(threshold_ms) else 0.0
    except (TypeError, ValueError):
        return 0.0


def score_latency(
    observation: Any,
    latency_ms: Union[int, float],
    threshold_ms: Union[int, float],
    *,
    score_name: str = "",
    comment: str = "",
) -> None:
    """Emit a latency_ok observation score. Fail-silent.

    Args:
        observation: Langfuse span/observation object
        latency_ms:  Measured latency in milliseconds
        threshold_ms: Threshold for "OK" in milliseconds
        score_name:  Score name constant (defaults to LATENCY_OK)
        comment:     Optional comment
    """
    if observation is None:
        return
    try:
        from apps.core.langfuse_client import score_observation_safe
        from apps.core.evaluation_constants import LATENCY_OK as _DEFAULT_NAME
        _name = score_name or _DEFAULT_NAME
        _val = latency_ok(latency_ms, threshold_ms)
        _comment = comment or f"{int(latency_ms)}ms (threshold={int(threshold_ms)}ms)"
        score_observation_safe(observation, _name, _val, comment=_comment)
    except Exception:
        logger.debug("Langfuse latency score failed (non-fatal)", exc_info=True)


# =====================================================================
# Evaluation-ready metadata builders (per flow)
# =====================================================================

def build_extraction_eval_metadata(
    *,
    prompt_source: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    decision_codes: Optional[List[str]] = None,
    recovery_lane_invoked: bool = False,
    recovery_lane_succeeded: bool = False,
    extraction_success: bool = False,
    final_confidence: Optional[float] = None,
    requires_review_override: bool = False,
    duplicate_detected: bool = False,
    approval_status: Optional[str] = None,
    final_outcome: Optional[str] = None,
) -> Dict[str, Any]:
    """Build compact eval metadata for extraction traces."""
    return _strip_none({
        "prompt_source": prompt_source,
        "prompt_hash": prompt_hash,
        "decision_codes": decision_codes,
        "recovery_lane_invoked": recovery_lane_invoked,
        "recovery_lane_succeeded": recovery_lane_succeeded,
        "extraction_success": extraction_success,
        "final_confidence": final_confidence,
        "requires_review_override": requires_review_override,
        "duplicate_detected": duplicate_detected,
        "approval_status": approval_status,
        "final_outcome": final_outcome,
    })


def build_recon_eval_metadata(
    *,
    po_found: bool = False,
    grn_found: bool = False,
    reconciliation_mode: Optional[str] = None,
    final_match_status: Optional[str] = None,
    exception_count: int = 0,
    requires_review: bool = False,
    auto_close_eligible: bool = False,
    routed_to_agents: bool = False,
    routed_to_review: bool = False,
) -> Dict[str, Any]:
    """Build compact eval metadata for reconciliation traces."""
    return _strip_none({
        "po_found": po_found,
        "grn_found": grn_found,
        "reconciliation_mode": reconciliation_mode,
        "final_match_status": final_match_status,
        "exception_count": exception_count,
        "requires_review": requires_review,
        "auto_close_eligible": auto_close_eligible,
        "routed_to_agents": routed_to_agents,
        "routed_to_review": routed_to_review,
    })


def build_agent_eval_metadata(
    *,
    planner_source: Optional[str] = None,
    planned_agents: Optional[List[str]] = None,
    executed_agents: Optional[List[str]] = None,
    prior_match_status: Optional[str] = None,
    final_recommendation: Optional[str] = None,
    final_confidence: Optional[float] = None,
    escalation_triggered: bool = False,
    feedback_rerun_triggered: bool = False,
) -> Dict[str, Any]:
    """Build compact eval metadata for agent pipeline traces."""
    return _strip_none({
        "planner_source": planner_source,
        "planned_agents": planned_agents,
        "executed_agents": executed_agents,
        "prior_match_status": prior_match_status,
        "final_recommendation": final_recommendation,
        "final_confidence": final_confidence,
        "escalation_triggered": escalation_triggered,
        "feedback_rerun_triggered": feedback_rerun_triggered,
    })


def build_case_eval_metadata(
    *,
    case_id: Optional[int] = None,
    case_number: Optional[str] = None,
    current_stage: Optional[str] = None,
    final_stage: Optional[str] = None,
    current_status: Optional[str] = None,
    final_status: Optional[str] = None,
    resolved_path: Optional[str] = None,
    review_required: bool = False,
    assigned_queue: Optional[str] = None,
    assigned_reviewer_id: Optional[int] = None,
    reprocess_requested: bool = False,
) -> Dict[str, Any]:
    """Build compact eval metadata for case pipeline traces."""
    return _strip_none({
        "case_id": case_id,
        "case_number": case_number,
        "current_stage": current_stage,
        "final_stage": final_stage,
        "current_status": current_status,
        "final_status": final_status,
        "resolved_path": resolved_path,
        "review_required": review_required,
        "assigned_queue": assigned_queue,
        "assigned_reviewer_id": assigned_reviewer_id,
        "reprocess_requested": reprocess_requested,
    })


def build_posting_eval_metadata(
    *,
    posting_stage: Optional[str] = None,
    final_status: Optional[str] = None,
    review_queue: Optional[str] = None,
    is_touchless: bool = False,
    issue_count: int = 0,
    blocking_issue_count: int = 0,
    ready_to_submit: bool = False,
    erp_document_number_present: bool = False,
) -> Dict[str, Any]:
    """Build compact eval metadata for posting pipeline traces."""
    return _strip_none({
        "posting_stage": posting_stage,
        "final_status": final_status,
        "review_queue": review_queue,
        "is_touchless": is_touchless,
        "issue_count": issue_count,
        "blocking_issue_count": blocking_issue_count,
        "ready_to_submit": ready_to_submit,
        "erp_document_number_present": erp_document_number_present,
    })


def build_erp_span_metadata(
    *,
    source_used: Optional[str] = None,
    freshness_status: Optional[str] = None,
    connector_name: Optional[str] = None,
    connector_type: Optional[str] = None,
    operation_type: Optional[str] = None,
    result_present: bool = False,
    retryable_failure: bool = False,
    sanitized_error_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Build compact eval metadata for ERP resolution/submission spans."""
    return _strip_none({
        "source_used": source_used,
        "freshness_status": freshness_status,
        "connector_name": connector_name,
        "connector_type": connector_type,
        "operation_type": operation_type,
        "result_present": result_present,
        "retryable_failure": retryable_failure,
        "sanitized_error_type": sanitized_error_type,
    })


# =====================================================================
# ERP error type normalisation (shared with erp langfuse_helpers.py)
# =====================================================================

# Canonical ERP error categories
ERP_ERROR_TIMEOUT = "timeout"
ERP_ERROR_UNAUTHORIZED = "unauthorized"
ERP_ERROR_RATE_LIMITED = "rate_limited"
ERP_ERROR_VALIDATION = "validation_error"
ERP_ERROR_EMPTY_RESULT = "empty_result"
ERP_ERROR_CONNECTOR_UNAVAILABLE = "connector_unavailable"
ERP_ERROR_NORMALIZATION_FAILED = "normalization_failed"
ERP_ERROR_SUBMISSION_FAILED = "submission_failed"
ERP_ERROR_RETRYABLE = "retryable_failure"
ERP_ERROR_NON_RETRYABLE = "non_retryable_failure"
ERP_ERROR_UNKNOWN = "unknown_error"

# Source provenance constants (re-exported for non-ERP callers)
SOURCE_CACHE = "CACHE"
SOURCE_LIVE_API = "API"
SOURCE_MIRROR_DB = "MIRROR_DB"
SOURCE_DB_FALLBACK = "DB_FALLBACK"
SOURCE_MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
SOURCE_NONE = "NONE"

# Freshness labels
FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"


# =====================================================================
# Internal helpers
# =====================================================================

def _strip_none(d: Dict[str, Any]) -> Dict[str, Any]:
    """Remove keys with None values from a dict."""
    return {k: v for k, v in d.items() if v is not None}
