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
from apps.core.evaluation_constants import (
    POSTING_FINAL_CONFIDENCE,
    POSTING_FINAL_REQUIRES_REVIEW,
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
    def run(cls, invoice: Invoice, *, user=None, tenant=None) -> PostingRun:
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
            tenant=tenant,
        )

        # Link extraction records if available
        cls._link_extraction(posting_run, invoice)

        # ------------------------------------------------------------------
        # Langfuse: open root trace for this posting pipeline run.
        # Trace ID uses str(posting_run.pk) so the two quality scores
        # (posting_confidence, posting_requires_review) that are already
        # emitted with the same trace_id will link correctly in Langfuse.
        # ------------------------------------------------------------------
        _trace_id = str(posting_run.pk)
        _lf_trace = None
        # Persist trace_id to PostingRun for cross-referencing
        try:
            posting_run.langfuse_trace_id = _trace_id
            posting_run.save(update_fields=["langfuse_trace_id", "updated_at"])
        except Exception:
            pass
        try:
            from apps.core.langfuse_client import start_trace
            _lf_trace = start_trace(
                _trace_id,
                "posting_pipeline",
                invoice_id=invoice.pk,
                user_id=user.pk if user else None,
                session_id=f"invoice-{invoice.pk}",
                metadata={
                    "posting_run_pk": posting_run.pk,
                    "invoice_id": invoice.pk,
                    "invoice_number": invoice.invoice_number or "",
                },
            )
        except Exception:
            pass
        try:
            from apps.core.langfuse_client import set_current_span
            set_current_span(_lf_trace)
        except Exception:
            pass

        def _open_stage_span(name: str, extra_meta: dict = None):
            """Open a child span for a pipeline stage. Fail-silent."""
            try:
                if _lf_trace is not None:
                    from apps.core.langfuse_client import start_span
                    return start_span(
                        _lf_trace,
                        name,
                        metadata={"posting_run_pk": posting_run.pk, "invoice_id": invoice.pk, **(extra_meta or {})},
                    )
            except Exception:
                pass
            return None

        def _close_stage_span(span, output: dict = None, failed: bool = False):
            """Close a stage span with output. Fail-silent."""
            try:
                if span is not None:
                    from apps.core.langfuse_client import end_span
                    end_span(span, output=output or {}, level="ERROR" if failed else "DEFAULT")
            except Exception:
                pass

        try:
            # Stage 1: Eligibility check (delegated to caller via eligibility_service)
            posting_run.stage_code = PostingStage.ELIGIBILITY_CHECK
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s1 = _open_stage_span("eligibility_check")

            PostingAuditService.log_event(
                AuditEventType.POSTING_ELIGIBILITY_PASSED,
                f"Posting eligibility passed for invoice {invoice.invoice_number}",
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                user=user,
            )
            _close_stage_span(_lf_s1, output={"passed": True})

            # Stage 2: Snapshot build
            posting_run.stage_code = PostingStage.SNAPSHOT_BUILD
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s2 = _open_stage_span("snapshot_build")
            snapshot = PostingSnapshotBuilder.build_invoice_snapshot(invoice)
            posting_run.source_invoice_snapshot_json = snapshot
            _close_stage_span(_lf_s2, output={"built": True})

            # Stage 3: Reference resolution + Stage 4: Mapping
            posting_run.stage_code = PostingStage.MAPPING
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s3 = _open_stage_span("mapping")

            connector = cls._get_erp_connector()
            engine = PostingMappingEngine(connector=connector, lf_parent_span=_lf_s3)
            line_items = list(invoice.line_items.order_by("line_number"))
            proposal = engine.resolve(
                invoice,
                line_items,
                po_number=invoice.po_number or "",
            )

            # Store ERP source metadata on the run
            if engine.erp_source_metadata:
                posting_run.erp_source_metadata_json = engine.erp_source_metadata

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
            _close_stage_span(_lf_s3, output={
                "vendor_resolved": bool(proposal.header.vendor_code),
                "lines_count": len(proposal.lines),
                "mapping_issues": len(proposal.issues),
                "connector_used": bool(connector),
            })

            # Stage 5: Validation
            posting_run.stage_code = PostingStage.VALIDATION
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s4 = _open_stage_span("validation")
            validation_issues = PostingValidationService.validate(proposal, invoice)
            all_issues = proposal.issues + validation_issues

            PostingAuditService.log_event(
                AuditEventType.POSTING_VALIDATION_COMPLETED,
                f"Posting validation completed: {len(all_issues)} issue(s)",
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                user=user,
            )
            _close_stage_span(_lf_s4, output={"total_issues": len(all_issues)})

            # Stage 6: Confidence
            _lf_s5 = _open_stage_span("confidence_scoring")
            confidence = PostingConfidenceService.calculate(proposal, all_issues)
            posting_run.overall_confidence = confidence

            try:
                from apps.core.langfuse_client import score_trace
                score_trace(
                    str(posting_run.pk),
                    POSTING_FINAL_CONFIDENCE,
                    float(confidence),
                    comment=(
                        f"invoice={invoice.pk} "
                        f"requires_review='pending' "
                        f"issues={len(all_issues)}"
                    ),
                    span=_lf_trace,
                )
            except Exception:
                pass
            _close_stage_span(_lf_s5, output={"confidence": float(confidence), "issue_count": len(all_issues)})

            # Stage 7: Review routing
            posting_run.stage_code = PostingStage.REVIEW_ROUTING
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s6 = _open_stage_span("review_routing")
            requires_review, primary_queue, review_reasons = (
                PostingReviewRoutingService.route(proposal, all_issues, confidence)
            )

            try:
                from apps.core.langfuse_client import score_trace
                score_trace(
                    str(posting_run.pk),
                    POSTING_FINAL_REQUIRES_REVIEW,
                    1.0 if requires_review else 0.0,
                    comment=f"queue={primary_queue} reasons={len(review_reasons)}",
                    span=_lf_trace,
                )
            except Exception:
                pass
            _close_stage_span(_lf_s6, output={
                "requires_review": requires_review,
                "queue": primary_queue or "",
                "reason_count": len(review_reasons),
            })

            posting_run.requires_review = requires_review
            posting_run.review_queue = primary_queue
            posting_run.review_reasons_json = review_reasons

            # Stage 8: Payload build
            posting_run.stage_code = PostingStage.PAYLOAD_BUILD
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s7 = _open_stage_span("payload_build")
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
            _close_stage_span(_lf_s7, output={"lines_in_payload": len(proposal.lines)})

            # Stage 9: Persist run artifacts
            posting_run.stage_code = PostingStage.FINALIZATION
            posting_run.save(update_fields=["stage_code", "updated_at"])
            _lf_s8 = _open_stage_span("finalization")
            cls._persist_artifacts(posting_run, proposal, all_issues)
            _close_stage_span(_lf_s8, output={"artifacts_persisted": True})

            # Stage 9b: Duplicate invoice check (ERP integration)
            _lf_s9 = _open_stage_span("duplicate_check")
            cls._check_duplicate(posting_run, invoice, proposal, connector, lf_parent_span=_lf_s9)
            _dup_meta = (posting_run.erp_source_metadata_json or {}).get("duplicate_check", {})
            _close_stage_span(_lf_s9, output={
                "is_duplicate": _dup_meta.get("is_duplicate", False),
                "source_type": _dup_meta.get("source_type", ""),
            })

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

            # Close root Langfuse trace on success
            try:
                if _lf_trace is not None:
                    from apps.core.langfuse_client import end_span
                    end_span(
                        _lf_trace,
                        output={
                            "status": posting_run.status,
                            "confidence": float(posting_run.overall_confidence or 0.0),
                            "requires_review": posting_run.requires_review,
                            "review_queue": posting_run.review_queue or "",
                            "duration_ms": elapsed,
                        },
                    )
            except Exception:
                pass

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

            # Close root Langfuse trace on failure
            try:
                if _lf_trace is not None:
                    from apps.core.langfuse_client import end_span
                    end_span(
                        _lf_trace,
                        output={
                            "status": "FAILED",
                            "error_code": posting_run.error_code,
                            "duration_ms": int((time.time() - start) * 1000),
                        },
                        level="ERROR",
                    )
            except Exception:
                pass

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

    @staticmethod
    def _get_erp_connector():
        """Get the default ERP connector, or None if not configured."""
        try:
            from apps.erp_integration.services.connector_factory import ConnectorFactory
            return ConnectorFactory.get_default_connector()
        except ImportError:
            return None
        except Exception:
            logger.debug("Could not load ERP connector", exc_info=True)
            return None

    @staticmethod
    def _check_duplicate(posting_run, invoice, proposal, connector, lf_parent_span=None) -> None:
        """Run duplicate invoice check via ERP integration layer."""
        try:
            from apps.erp_integration.services.resolution.duplicate_invoice_resolver import (
                DuplicateInvoiceResolver,
            )
            resolver = DuplicateInvoiceResolver()
            result = resolver.resolve(
                connector,
                invoice_number=invoice.invoice_number or "",
                vendor_code=proposal.header.vendor_code,
                fiscal_year=str(invoice.invoice_date.year) if invoice.invoice_date else "",
                exclude_invoice_id=invoice.pk,
                invoice_id=invoice.pk,
                posting_run_id=posting_run.pk,
                lf_parent_span=lf_parent_span,
            )
            if result.resolved and result.value and result.value.get("is_duplicate"):
                posting_run.requires_review = True
                dup_count = result.value.get("duplicate_count", 0)
                reasons = posting_run.review_reasons_json or []
                reasons.append(f"Potential duplicate invoice detected ({dup_count} match(es))")
                posting_run.review_reasons_json = reasons
                erp_meta = posting_run.erp_source_metadata_json or {}
                erp_meta["duplicate_check"] = {
                    "is_duplicate": True,
                    "duplicate_count": dup_count,
                    "source_type": result.source_type,
                    "confidence": result.confidence,
                }
                posting_run.erp_source_metadata_json = erp_meta
                posting_run.save(update_fields=[
                    "requires_review", "review_reasons_json",
                    "erp_source_metadata_json", "updated_at",
                ])
                logger.info(
                    "Duplicate invoice check: %d potential duplicate(s) for invoice %s",
                    dup_count, invoice.invoice_number,
                )
        except ImportError:
            pass
        except Exception:
            logger.debug("Duplicate invoice check skipped", exc_info=True)
