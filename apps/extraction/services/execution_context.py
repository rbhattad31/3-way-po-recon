"""ExecutionContext — centralizes governed vs legacy data resolution.

Views should use get_execution_context() instead of directly accessing
ExtractionRun or ExtractionResult fields for execution metadata.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    review_queue: str | None
    schema_code: str | None
    schema_version: str | None
    extraction_method: str | None
    requires_review: bool
    governed_status: str | None  # ExtractionRun.status if available
    source: str  # "governed" | "legacy"


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
        return ExecutionContext(
            review_queue=run.review_queue,
            schema_code=run.schema_code,
            schema_version=run.schema_version,
            extraction_method=run.extraction_method,
            requires_review=run.requires_review,
            governed_status=run.status,
            source="governed",
        )

    # Legacy fallback: attempt lookup via document_upload_id
    try:
        from apps.extraction_core.models import ExtractionRun

        doc_upload_id = getattr(extraction_result, "document_upload_id", None)
        if doc_upload_id:
            run = (
                ExtractionRun.objects
                .filter(document__document_upload_id=doc_upload_id)
                .order_by("-created_at")
                .first()
            )
            if run:
                return ExecutionContext(
                    review_queue=run.review_queue,
                    schema_code=run.schema_code,
                    schema_version=run.schema_version,
                    extraction_method=run.extraction_method,
                    requires_review=run.requires_review,
                    governed_status=run.status,
                    source="governed",
                )
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
