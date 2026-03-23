"""
ExtractionAuditService — Audit event logging for the extraction platform.

Wraps AuditService.log_event() with extraction-specific helper methods
that ensure every event includes the mandatory metadata:
- extraction_run_id
- schema_code/version
- prompt_code/version
- country/regime
- actor + roles snapshot
- permission_checked + access_granted
- duration_ms
- before/after where applicable
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from apps.auditlog.services import AuditService
from apps.core.enums import AuditEventType

logger = logging.getLogger(__name__)


class ExtractionAuditService:
    """
    Extraction-specific audit event logging.

    All methods are static/class — no state.
    """

    @staticmethod
    def _base_metadata(
        extraction_run_id: int | None = None,
        country_code: str = "",
        regime_code: str = "",
        schema_code: str = "",
        schema_version: str = "",
        prompt_code: str = "",
        prompt_version: str = "",
        **extra: Any,
    ) -> dict:
        """Build common metadata dict for extraction events."""
        meta = {
            "extraction_run_id": extraction_run_id,
            "country_code": country_code,
            "regime_code": regime_code,
            "schema_code": schema_code,
            "schema_version": schema_version,
            "prompt_code": prompt_code,
            "prompt_version": prompt_version,
        }
        meta.update(extra)
        return meta

    # ------------------------------------------------------------------
    # Pipeline events
    # ------------------------------------------------------------------

    @classmethod
    def log_extraction_started(
        cls,
        extraction_run_id: int,
        document_id: int | None = None,
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_STARTED,
            description="Extraction pipeline started",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                document_id=document_id,
                **kwargs,
            ),
            status_after="PENDING",
        )

    @classmethod
    def log_jurisdiction_resolved(
        cls,
        extraction_run_id: int,
        country_code: str,
        regime_code: str,
        jurisdiction_source: str,
        confidence: float = 0.0,
        user=None,
        duration_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.JURISDICTION_RESOLVED,
            description=f"Jurisdiction resolved: {country_code}/{regime_code} via {jurisdiction_source}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                country_code=country_code,
                regime_code=regime_code,
                jurisdiction_source=jurisdiction_source,
                confidence=confidence,
                **kwargs,
            ),
            duration_ms=duration_ms,
            status_after="JURISDICTION_RESOLVED",
        )

    @classmethod
    def log_schema_selected(
        cls,
        extraction_run_id: int,
        schema_code: str,
        schema_version: str,
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.SCHEMA_SELECTED,
            description=f"Schema selected: {schema_code} v{schema_version}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                schema_code=schema_code,
                schema_version=schema_version,
                **kwargs,
            ),
            status_after="SCHEMA_SELECTED",
        )

    @classmethod
    def log_prompt_selected(
        cls,
        extraction_run_id: int,
        prompt_code: str,
        prompt_version: str,
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.PROMPT_SELECTED,
            description=f"Prompt selected: {prompt_code} v{prompt_version}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                prompt_code=prompt_code,
                prompt_version=prompt_version,
                **kwargs,
            ),
            status_after="PROMPT_BUILT",
        )

    @classmethod
    def log_extraction_completed(
        cls,
        extraction_run_id: int,
        overall_confidence: float = 0.0,
        field_count: int = 0,
        extraction_method: str = "",
        user=None,
        duration_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_COMPLETED,
            description=f"Extraction completed: confidence={overall_confidence:.2%}, fields={field_count}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                overall_confidence=overall_confidence,
                field_count=field_count,
                extraction_method=extraction_method,
                **kwargs,
            ),
            duration_ms=duration_ms,
            status_after="COMPLETED",
        )

    @classmethod
    def log_extraction_failed(
        cls,
        extraction_run_id: int,
        error_message: str = "",
        user=None,
        duration_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_FAILED,
            description=f"Extraction failed: {error_message[:200]}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                error_message=error_message[:500],
                **kwargs,
            ),
            duration_ms=duration_ms,
            status_after="FAILED",
            error_code="EXTRACTION_FAILED",
        )

    @classmethod
    def log_normalization_completed(
        cls,
        extraction_run_id: int,
        user=None,
        duration_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.NORMALIZATION_COMPLETED,
            description="Normalization completed",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id, **kwargs,
            ),
            duration_ms=duration_ms,
        )

    @classmethod
    def log_validation_completed(
        cls,
        extraction_run_id: int,
        issue_count: int = 0,
        error_count: int = 0,
        user=None,
        duration_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.VALIDATION_COMPLETED,
            description=f"Validation completed: {issue_count} issues, {error_count} errors",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                issue_count=issue_count,
                error_count=error_count,
                **kwargs,
            ),
            duration_ms=duration_ms,
        )

    @classmethod
    def log_evidence_captured(
        cls,
        extraction_run_id: int,
        evidence_count: int = 0,
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EVIDENCE_CAPTURED,
            description=f"Evidence captured: {evidence_count} records",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                evidence_count=evidence_count,
                **kwargs,
            ),
        )

    @classmethod
    def log_review_route_assigned(
        cls,
        extraction_run_id: int,
        review_queue: str = "",
        reasons: list[str] | None = None,
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.REVIEW_ROUTE_ASSIGNED,
            description=f"Review route assigned: {review_queue}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                review_queue=review_queue,
                reasons=reasons or [],
                **kwargs,
            ),
        )

    # ------------------------------------------------------------------
    # Human action events
    # ------------------------------------------------------------------

    @classmethod
    def log_field_corrected(
        cls,
        extraction_run_id: int,
        field_code: str,
        old_value: str,
        new_value: str,
        user=None,
        permission_checked: str = "extraction.correct",
        access_granted: bool = True,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_FIELD_CORRECTED,
            description=f"Field '{field_code}' corrected",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                field_code=field_code,
                old_value=old_value[:200],
                new_value=new_value[:200],
                permission_checked=permission_checked,
                access_granted=access_granted,
                **kwargs,
            ),
            status_before=old_value[:100],
            status_after=new_value[:100],
        )

    # REMOVED: duplicate emission — approval/rejection audit is now emitted
    # by ExtractionApprovalService._log_audit() (legacy flow) and
    # GovernanceTrailService.record_approval_decision() (governed flow).
    # These methods are retained as no-ops for backward compatibility.

    @classmethod
    def log_extraction_approved(cls, **kwargs: Any) -> None:
        """DEPRECATED: No-op. Use GovernanceTrailService.record_approval_decision()."""
        pass

    @classmethod
    def log_extraction_rejected(cls, **kwargs: Any) -> None:
        """DEPRECATED: No-op. Use GovernanceTrailService.record_approval_decision()."""
        pass

    @classmethod
    def log_extraction_reprocessed(
        cls,
        extraction_run_id: int,
        user=None,
        reason: str = "",
        permission_checked: str = "extraction.reprocess",
        access_granted: bool = True,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_REPROCESSED,
            description=f"Extraction reprocessed: {reason[:100]}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                reason=reason[:500],
                permission_checked=permission_checked,
                access_granted=access_granted,
                **kwargs,
            ),
        )

    @classmethod
    def log_extraction_escalated(
        cls,
        extraction_run_id: int,
        user=None,
        reason: str = "",
        permission_checked: str = "extraction.escalate",
        access_granted: bool = True,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_ESCALATED,
            description=f"Extraction escalated: {reason[:100]}",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                reason=reason[:500],
                permission_checked=permission_checked,
                access_granted=access_granted,
                **kwargs,
            ),
        )

    @classmethod
    def log_comment_added(
        cls,
        extraction_run_id: int,
        comment: str = "",
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionRun",
            entity_id=extraction_run_id,
            event_type=AuditEventType.EXTRACTION_COMMENT_ADDED,
            description=f"Comment added on extraction run",
            user=user,
            metadata=cls._base_metadata(
                extraction_run_id=extraction_run_id,
                comment=comment[:500],
                **kwargs,
            ),
        )

    # ------------------------------------------------------------------
    # Config events
    # ------------------------------------------------------------------

    @classmethod
    def log_settings_updated(
        cls,
        entity_id: int,
        entity_type: str = "ExtractionRuntimeSettings",
        user=None,
        before: dict | None = None,
        after: dict | None = None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=AuditEventType.SETTINGS_UPDATED,
            description=f"{entity_type} updated",
            user=user,
            metadata=kwargs,
            input_snapshot=before,
            output_snapshot=after,
        )

    @classmethod
    def log_analytics_snapshot_created(
        cls,
        snapshot_id: int,
        snapshot_type: str = "",
        user=None,
        **kwargs: Any,
    ) -> None:
        AuditService.log_event(
            entity_type="ExtractionAnalyticsSnapshot",
            entity_id=snapshot_id,
            event_type=AuditEventType.ANALYTICS_SNAPSHOT_CREATED,
            description=f"Analytics snapshot created: {snapshot_type}",
            user=user,
            metadata={"snapshot_type": snapshot_type, **kwargs},
        )
