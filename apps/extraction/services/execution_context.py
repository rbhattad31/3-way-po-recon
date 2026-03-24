"""ExecutionContext — centralizes governed vs legacy data resolution.

Views should use get_execution_context() instead of directly accessing
ExtractionRun or ExtractionResult fields for execution metadata.

Resolution order:
  1. Direct FK: extraction_result.extraction_run
  2. Governed lookup: document_upload_id → ExtractionDocument → ExtractionRun
  3. Legacy fallback: no governed data available
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """Normalized execution metadata for UI rendering.

    source="governed"  — data comes from ExtractionRun (authoritative pipeline)
    source="legacy"    — no ExtractionRun linked; older extraction record
    """
    review_queue: str | None
    schema_code: str | None
    schema_version: str | None
    extraction_method: str | None
    requires_review: bool
    governed_status: str | None  # ExtractionRun.status if available
    source: str  # "governed" | "legacy"
    # Extended fields (populated when governed data is available)
    extraction_run_id: int | None = None
    country_code: str | None = None
    regime_code: str | None = None
    jurisdiction_source: str | None = None
    overall_confidence: float | None = None
    review_reasons: list = field(default_factory=list)
    approval_action: str | None = None  # ExtractionApprovalRecord.action, if any
    approval_decided_at: object = None  # datetime or None
    duration_ms: int | None = None


def get_execution_context(extraction_result) -> ExecutionContext:
    """
    Returns governed ExtractionRun data where available.
    Falls back to ExtractionResult fields for legacy records.
    Always use this function in views instead of directly accessing
    ExtractionRun or ExtractionResult fields for execution metadata.
    """
    # Primary path: FK to ExtractionRun exists
    run = getattr(extraction_result, "extraction_run", None)
    if run is not None:
        return _build_from_run(run)

    # Legacy fallback: attempt lookup via document_upload_id
    try:
        from apps.extraction_core.models import ExtractionRun

        doc_upload_id = getattr(extraction_result, "document_upload_id", None)
        if doc_upload_id:
            run = (
                ExtractionRun.objects
                .select_related("jurisdiction", "schema")
                .filter(document__document_upload_id=doc_upload_id)
                .order_by("-created_at")
                .first()
            )
            if run:
                return _build_from_run(run)
    except Exception:
        logger.debug(
            "ExtractionRun lookup failed for result %s — returning legacy context",
            getattr(extraction_result, "pk", "?"),
        )

    # No governed data available
    return ExecutionContext(
        review_queue=None,
        schema_code=None,
        schema_version=None,
        extraction_method=None,
        requires_review=False,
        governed_status=None,
        source="legacy",
    )


def _build_from_run(run) -> ExecutionContext:
    """Build ExecutionContext from an ExtractionRun instance."""
    # Fetch approval record if it exists (single query, optional)
    approval_action = None
    approval_decided_at = None
    try:
        from apps.extraction_core.models import ExtractionApprovalRecord
        record = ExtractionApprovalRecord.objects.filter(
            extraction_run=run,
        ).values("action", "decided_at").first()
        if record:
            approval_action = record["action"]
            approval_decided_at = record["decided_at"]
    except Exception:
        pass

    return ExecutionContext(
        review_queue=run.review_queue,
        schema_code=run.schema_code,
        schema_version=run.schema_version,
        extraction_method=run.extraction_method,
        requires_review=run.requires_review,
        governed_status=run.status,
        source="governed",
        extraction_run_id=run.pk,
        country_code=run.country_code,
        regime_code=run.regime_code,
        jurisdiction_source=run.jurisdiction_source,
        overall_confidence=run.overall_confidence,
        review_reasons=run.review_reasons_json or [],
        approval_action=approval_action,
        approval_decided_at=approval_decided_at,
        duration_ms=run.duration_ms,
    )
