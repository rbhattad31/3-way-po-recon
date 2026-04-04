"""Posting Orchestrator — coordinates posting lifecycle from business perspective."""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from apps.core.enums import (
    AuditEventType,
    InvoicePostingStatus,
    PostingRunStatus,
)
from apps.core.decorators import observed_service
from apps.documents.models import Invoice
from apps.posting.models import InvoicePosting
from apps.posting.services.eligibility_service import PostingEligibilityService
from apps.posting_core.services.posting_audit import PostingAuditService
from apps.posting_core.services.posting_pipeline import PostingPipeline

logger = logging.getLogger(__name__)


class PostingOrchestrator:
    """Coordinates the posting workflow from business layer."""

    @classmethod
    @observed_service(
        "posting.orchestrate",
        entity_type="InvoicePosting",
        audit_event="POSTING_STARTED",
    )
    def prepare_posting(
        cls,
        invoice_id: int,
        *,
        user=None,
        trigger: str = "system",
    ) -> InvoicePosting:
        """Prepare a posting proposal for an invoice.

        Creates or updates InvoicePosting, runs eligibility check,
        then delegates to PostingPipeline.
        """
        # Eligibility check
        eligibility = PostingEligibilityService.check(invoice_id)
        if not eligibility.eligible:
            logger.warning(
                "Invoice %s not eligible for posting: %s",
                invoice_id, eligibility.reasons,
            )
            PostingAuditService.log_event(
                AuditEventType.POSTING_ELIGIBILITY_FAILED,
                f"Posting eligibility failed: {'; '.join(eligibility.reasons)}",
                invoice_id=invoice_id,
                user=user,
                metadata={"reasons": eligibility.reasons},
            )
            raise ValueError(
                f"Invoice not eligible for posting: {'; '.join(eligibility.reasons)}"
            )

        invoice = Invoice.objects.get(pk=invoice_id)

        # Create or get InvoicePosting record
        posting, created = InvoicePosting.objects.get_or_create(
            invoice=invoice,
            defaults={
                "status": InvoicePostingStatus.MAPPING_IN_PROGRESS,
                "created_by": user,
            },
        )

        if not created:
            # Update status if re-preparing
            posting.status = InvoicePostingStatus.MAPPING_IN_PROGRESS
            posting.last_error_code = ""
            posting.last_error_message = ""
            posting.save(update_fields=[
                "status", "last_error_code", "last_error_message", "updated_at",
            ])

        try:
            # Run the pipeline
            posting_run = PostingPipeline.run(invoice, user=user)

            # Update InvoicePosting from run results
            posting.posting_confidence = posting_run.overall_confidence
            posting.payload_snapshot_json = posting_run.posting_payload_json
            posting.posting_snapshot_batch_refs_json = dict(
                posting_run.normalized_posting_data_json.get("header", {})
            )
            posting.mapping_summary_json = {
                "run_id": posting_run.pk,
                "confidence": posting_run.overall_confidence,
                "requires_review": posting_run.requires_review,
                "review_queue": posting_run.review_queue,
                "review_reasons": posting_run.review_reasons_json,
            }

            # Link extraction
            if posting_run.extraction_result:
                posting.extraction_result = posting_run.extraction_result
            if posting_run.extraction_run:
                posting.extraction_run = posting_run.extraction_run

            # Determine final status
            if posting_run.requires_review:
                posting.status = InvoicePostingStatus.MAPPING_REVIEW_REQUIRED
                posting.review_queue = posting_run.review_queue
                posting.stage = posting_run.stage_code

                PostingAuditService.log_event(
                    AuditEventType.POSTING_MAPPING_REVIEW_REQUIRED,
                    f"Posting requires review: {posting_run.review_queue}",
                    invoice_id=invoice.pk,
                    posting_run_id=posting_run.pk,
                    user=user,
                )
            else:
                posting.status = InvoicePostingStatus.READY_TO_SUBMIT
                posting.is_touchless = True

                PostingAuditService.log_event(
                    AuditEventType.POSTING_READY_TO_SUBMIT,
                    f"Posting ready to submit (confidence={posting_run.overall_confidence:.0%})",
                    invoice_id=invoice.pk,
                    posting_run_id=posting_run.pk,
                    user=user,
                )

            posting.save()

            # --- System agent: governance-visible posting preparation record ---
            try:
                from apps.agents.services.system_agent_classes import (
                    SystemPostingPreparationAgent,
                )
                from apps.agents.services.base_agent import AgentContext

                _normalized = posting_run.normalized_posting_data_json or {}
                _lines = _normalized.get("lines", [])
                _header = _normalized.get("header", {})

                _posting_ctx = AgentContext(
                    reconciliation_result=None,
                    invoice_id=invoice.pk,
                    extra={
                        "posting_run_id": posting_run.pk,
                        "posting_status": str(posting.status),
                        "confidence": posting.posting_confidence or 0.0,
                        "is_touchless": posting.is_touchless,
                        "review_queues": (
                            [posting.review_queue]
                            if posting.review_queue else []
                        ),
                        "vendor_mapped": bool(_header.get("vendor_id")),
                        "item_mapping_rate": (
                            len([ln for ln in _lines if ln.get("item_id")])
                            / max(len(_lines), 1)
                        ),
                        "validation_errors": posting_run.issues.filter(
                            severity="ERROR",
                        ).count(),
                        "validation_warnings": posting_run.issues.filter(
                            severity="WARNING",
                        ).count(),
                    },
                    actor_primary_role="SYSTEM_AGENT",
                    actor_roles_snapshot=["SYSTEM_AGENT"],
                    permission_source="system",
                    access_granted=True,
                )
                SystemPostingPreparationAgent().run(_posting_ctx)
            except Exception:
                logger.debug(
                    "SystemPostingPreparationAgent skipped for invoice %s",
                    invoice_id, exc_info=True,
                )

            return posting

        except Exception as exc:
            posting.status = InvoicePostingStatus.POST_FAILED
            posting.last_error_code = type(exc).__name__
            posting.last_error_message = str(exc)[:1000]
            posting.save(update_fields=[
                "status", "last_error_code", "last_error_message", "updated_at",
            ])
            raise
