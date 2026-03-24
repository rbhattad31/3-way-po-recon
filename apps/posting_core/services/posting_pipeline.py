"""Posting Pipeline — orchestrates the full posting proposal preparation.

Stages:
1. Eligibility check
2. Invoice snapshot build
3. Fetch latest active reference batches
4. Resolve mappings
5. Validate
6. Calculate confidence
7. Assign review queue
8. Build canonical payload
9. Persist run artifacts
10. Finalize status
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    InvoicePostingStatus,
    PostingIssueSeverity,
    PostingRunStatus,
    PostingStage,
)
from apps.core.decorators import observed_service
from apps.documents.models import Invoice
from apps.posting_core.models import (
    PostingEvidence,
    PostingFieldValue,
    PostingIssue,
    PostingLineItem,
    PostingRun,
)
from apps.posting_core.services.payload_builder import PostingPayloadBuilder
from apps.posting_core.services.posting_audit import PostingAuditService
from apps.posting_core.services.posting_confidence import PostingConfidenceService
from apps.posting_core.services.posting_mapping_engine import PostingMappingEngine
from apps.posting_core.services.posting_review_routing import PostingReviewRoutingService
from apps.posting_core.services.posting_snapshot_builder import PostingSnapshotBuilder
from apps.posting_core.services.posting_validation import PostingValidationService

logger = logging.getLogger(__name__)


class PostingPipeline:
    """Orchestrates the posting proposal pipeline."""

    @classmethod
    @observed_service(
        "posting.pipeline",
        entity_type="PostingRun",
        audit_event="POSTING_STARTED",
    )
    def run(cls, invoice: Invoice, *, user=None) -> PostingRun:
        """Execute the full posting pipeline for an invoice.

        Returns the PostingRun with all artifacts persisted.
        """
        start = time.time()

        # Create the PostingRun
        posting_run = PostingRun.objects.create(
            invoice=invoice,
            status=PostingRunStatus.RUNNING,
            stage_code=PostingStage.ELIGIBILITY_CHECK,
            started_at=timezone.now(),
            created_by=user,
        )

        # Link extraction records if available
        cls._link_extraction(posting_run, invoice)

        try:
            # Stage 1: Eligibility check (delegated to caller via eligibility_service)
            posting_run.stage_code = PostingStage.ELIGIBILITY_CHECK
            posting_run.save(update_fields=["stage_code", "updated_at"])

            PostingAuditService.log_event(
                AuditEventType.POSTING_ELIGIBILITY_PASSED,
                f"Posting eligibility passed for invoice {invoice.invoice_number}",
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                user=user,
            )

            # Stage 2: Snapshot build
            posting_run.stage_code = PostingStage.SNAPSHOT_BUILD
            posting_run.save(update_fields=["stage_code", "updated_at"])
            snapshot = PostingSnapshotBuilder.build_invoice_snapshot(invoice)
            posting_run.source_invoice_snapshot_json = snapshot

            # Stage 3: Reference resolution + Stage 4: Mapping
            posting_run.stage_code = PostingStage.MAPPING
            posting_run.save(update_fields=["stage_code", "updated_at"])

            engine = PostingMappingEngine()
            line_items = list(invoice.line_items.order_by("line_number"))
            proposal = engine.resolve(
                invoice,
                line_items,
                po_number=invoice.po_number or "",
            )

            PostingAuditService.log_event(
                AuditEventType.POSTING_MAPPING_COMPLETED,
                f"Posting mapping completed for invoice {invoice.invoice_number}",
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                user=user,
                metadata={
                    "vendor_resolved": bool(proposal.header.vendor_code),
                    "lines_count": len(proposal.lines),
                    "issues_count": len(proposal.issues),
                },
            )

            # Stage 5: Validation
            posting_run.stage_code = PostingStage.VALIDATION
            posting_run.save(update_fields=["stage_code", "updated_at"])
            validation_issues = PostingValidationService.validate(proposal, invoice)
            all_issues = proposal.issues + validation_issues

            PostingAuditService.log_event(
                AuditEventType.POSTING_VALIDATION_COMPLETED,
                f"Posting validation completed: {len(all_issues)} issue(s)",
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                user=user,
            )

            # Stage 6: Confidence
            confidence = PostingConfidenceService.calculate(proposal, all_issues)
            posting_run.overall_confidence = confidence

            # Stage 7: Review routing
            posting_run.stage_code = PostingStage.REVIEW_ROUTING
            posting_run.save(update_fields=["stage_code", "updated_at"])
            requires_review, primary_queue, review_reasons = (
                PostingReviewRoutingService.route(proposal, all_issues, confidence)
            )
            posting_run.requires_review = requires_review
            posting_run.review_queue = primary_queue
            posting_run.review_reasons_json = review_reasons

            # Stage 8: Payload build
            posting_run.stage_code = PostingStage.PAYLOAD_BUILD
            posting_run.save(update_fields=["stage_code", "updated_at"])
            payload = PostingPayloadBuilder.build(proposal)
            posting_run.posting_payload_json = payload
            posting_run.normalized_posting_data_json = {
                "header": {
                    "vendor_code": proposal.header.vendor_code,
                    "vendor_name": proposal.header.vendor_name,
                    "vendor_confidence": proposal.header.vendor_confidence,
                },
                "lines_summary": [
                    {
                        "index": lp.line_index,
                        "item_code": lp.erp_item_code,
                        "confidence": lp.confidence,
                        "tax_code": lp.tax_code,
                        "cost_center": lp.cost_center,
                    }
                    for lp in proposal.lines
                ],
            }

            # Stage 9: Persist run artifacts
            posting_run.stage_code = PostingStage.FINALIZATION
            posting_run.save(update_fields=["stage_code", "updated_at"])
            cls._persist_artifacts(posting_run, proposal, all_issues)

            # Stage 10: Finalize status
            has_blocking = any(
                i.get("severity") == PostingIssueSeverity.ERROR for i in all_issues
            )

            if has_blocking or requires_review:
                posting_run.status = PostingRunStatus.COMPLETED
            else:
                posting_run.status = PostingRunStatus.COMPLETED

            elapsed = int((time.time() - start) * 1000)
            posting_run.completed_at = timezone.now()
            posting_run.duration_ms = elapsed
            posting_run.save()

            logger.info(
                "PostingPipeline: run %s completed in %dms — confidence=%.2f reviews=%s",
                posting_run.pk, elapsed, confidence, requires_review,
            )

            return posting_run

        except Exception as exc:
            posting_run.status = PostingRunStatus.FAILED
            posting_run.error_code = type(exc).__name__
            posting_run.error_message = str(exc)[:1000]
            posting_run.completed_at = timezone.now()
            elapsed = int((time.time() - start) * 1000)
            posting_run.duration_ms = elapsed
            posting_run.save()

            PostingAuditService.log_event(
                AuditEventType.POSTING_FAILED,
                f"Posting pipeline failed: {exc}",
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                user=user,
            )
            logger.exception("PostingPipeline: run %s failed", posting_run.pk)
            raise

    @staticmethod
    def _link_extraction(posting_run: PostingRun, invoice: Invoice) -> None:
        """Link extraction records if available."""
        try:
            from apps.extraction.models import ExtractionResult
            result = (
                ExtractionResult.objects
                .filter(invoice=invoice, success=True)
                .order_by("-created_at")
                .first()
            )
            if result:
                posting_run.extraction_result = result
                posting_run.extraction_run = result.extraction_run
                posting_run.save(update_fields=[
                    "extraction_result", "extraction_run", "updated_at",
                ])
        except Exception:
            logger.warning("Could not link extraction records for posting run %s", posting_run.pk)

    @staticmethod
    def _persist_artifacts(posting_run, proposal, issues) -> None:
        """Persist field values, line items, issues, and evidence."""
        # Field values for header
        header_fields = []
        h = proposal.header
        if h.vendor_code:
            header_fields.append(PostingFieldValue(
                posting_run=posting_run,
                field_code="vendor_code",
                category="HEADER",
                source_type=h.vendor_source or "INVOICE",
                value=h.vendor_code,
                confidence=h.vendor_confidence,
            ))

        if header_fields:
            PostingFieldValue.objects.bulk_create(header_fields)

        # Line items
        line_records = []
        for lp in proposal.lines:
            line_records.append(PostingLineItem(
                posting_run=posting_run,
                line_index=lp.line_index,
                invoice_line_item_id=lp.invoice_line_item_id,
                source_description=lp.source_description,
                mapped_description=lp.mapped_description,
                source_category=lp.source_category,
                mapped_category=lp.mapped_category,
                erp_item_code=lp.erp_item_code,
                erp_line_type=lp.erp_line_type,
                quantity=lp.quantity,
                unit_price=lp.unit_price,
                line_amount=lp.line_amount,
                tax_code=lp.tax_code,
                cost_center=lp.cost_center,
                gl_account=lp.gl_account,
                uom=lp.uom,
                confidence=lp.confidence,
                source_json={
                    "source_description": lp.source_description,
                    "source_category": lp.source_category,
                },
                resolved_json={
                    "item_source": lp.item_source,
                    "tax_source": lp.tax_source,
                    "cost_center_source": lp.cost_center_source,
                },
            ))
        if line_records:
            PostingLineItem.objects.bulk_create(line_records)

        # Issues
        issue_records = []
        for i in issues:
            issue_records.append(PostingIssue(
                posting_run=posting_run,
                severity=i.get("severity", "INFO"),
                field_code=i.get("field_code", ""),
                check_type=i.get("check_type", ""),
                message=i.get("message", ""),
                details_json=i.get("details_json", {}),
                line_item_index=i.get("line_item_index"),
            ))
        if issue_records:
            PostingIssue.objects.bulk_create(issue_records)

        # Evidence
        evidence_records = []
        for e in proposal.evidence:
            evidence_records.append(PostingEvidence(
                posting_run=posting_run,
                field_code=e.get("field_code", ""),
                source_type=e.get("source_type", "INVOICE"),
                snippet=e.get("snippet", ""),
                confidence=e.get("confidence"),
                line_item_index=e.get("line_item_index"),
            ))
        if evidence_records:
            PostingEvidence.objects.bulk_create(evidence_records)
