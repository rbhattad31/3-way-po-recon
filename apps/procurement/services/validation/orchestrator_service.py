"""ValidationOrchestratorService — central service that runs all validations."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    ValidationItemStatus,
    ValidationNextAction,
    ValidationOverallStatus,
    ValidationSeverity,
    ValidationType,
)
from apps.core.trace import TraceContext
from apps.procurement.models import (
    AnalysisRun,
    ProcurementRequest,
    ValidationResult,
    ValidationResultItem,
)
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.validation.ambiguity_service import AmbiguityValidationService
from apps.procurement.services.validation.attribute_completeness_service import (
    AttributeCompletenessValidationService,
)
from apps.procurement.services.validation.commercial_completeness_service import (
    CommercialCompletenessValidationService,
)
from apps.procurement.services.validation.compliance_readiness_service import (
    ComplianceReadinessValidationService,
)
from apps.procurement.services.validation.document_completeness_service import (
    DocumentCompletenessValidationService,
)
from apps.procurement.services.validation.rule_resolver_service import (
    ValidationRuleResolverService,
)
from apps.procurement.services.validation.scope_coverage_service import (
    ScopeCoverageValidationService,
)

logger = logging.getLogger(__name__)


class ValidationOrchestratorService:
    """Run all applicable validation dimensions and build a unified result."""

    @staticmethod
    @observed_service("procurement.validation.orchestrate", audit_event="VALIDATION_RUN_STARTED")
    def run_validation(
        request: ProcurementRequest,
        run: AnalysisRun,
        *,
        agent_enabled: bool = False,
        tenant=None,
    ) -> ValidationResult:
        """Execute the full validation pipeline.

        Steps:
          1. Resolve applicable rules
          2. Run each deterministic validator
          3. Optionally run ValidationAgent for ambiguity resolution
          4. Score and classify results
          5. Persist ValidationResult + items
          6. Update run status
        """
        ctx = TraceContext.get_current()

        # Mark run as started
        AnalysisRunService.start_run(run)

        try:
            # 1. Resolve rules
            rules = ValidationRuleResolverService.resolve_rules_for_request(request)

            # 2. Run all deterministic validators
            all_findings: List[Dict[str, Any]] = []

            all_findings.extend(
                AttributeCompletenessValidationService.validate(request, rules)
            )
            all_findings.extend(
                DocumentCompletenessValidationService.validate(request, rules)
            )
            all_findings.extend(
                ScopeCoverageValidationService.validate(request, rules)
            )
            all_findings.extend(
                AmbiguityValidationService.validate(request, rules)
            )
            all_findings.extend(
                CommercialCompletenessValidationService.validate(request, rules)
            )
            all_findings.extend(
                ComplianceReadinessValidationService.validate(request, rules)
            )

            # 3. Optional agent augmentation for high-ambiguity cases
            if agent_enabled:
                ambiguous_count = sum(
                    1 for f in all_findings
                    if f.get("status") == ValidationItemStatus.AMBIGUOUS
                )
                if ambiguous_count >= 3:
                    all_findings = _run_agent_augmentation(
                        request, run, all_findings
                    )

            # 4. Score and classify
            score = _compute_completeness_score(all_findings)
            overall_status = _determine_overall_status(all_findings, score)
            missing = [f for f in all_findings if f["status"] == ValidationItemStatus.MISSING]
            warnings = [
                f for f in all_findings
                if f["status"] in (ValidationItemStatus.WARNING, ValidationItemStatus.FAILED)
            ]
            ambiguous = [f for f in all_findings if f["status"] == ValidationItemStatus.AMBIGUOUS]
            next_action = _determine_next_action(overall_status, missing, ambiguous)
            ready_for_rec, ready_for_bench = _determine_readiness(overall_status, missing)

            summary = _build_summary(overall_status, score, len(missing), len(warnings), len(ambiguous))

            # 5. Persist
            with transaction.atomic():
                validation_result = ValidationResult.objects.create(
                    run=run,
                    validation_type=ValidationType.ATTRIBUTE_COMPLETENESS,
                    overall_status=overall_status,
                    completeness_score=score,
                    summary_text=summary,
                    readiness_for_recommendation=ready_for_rec,
                    readiness_for_benchmarking=ready_for_bench,
                    recommended_next_action=next_action,
                    missing_items_json=[
                        {"item_code": f["item_code"], "item_label": f["item_label"],
                         "severity": f["severity"], "remarks": f.get("remarks", "")}
                        for f in missing
                    ],
                    warnings_json=[
                        {"item_code": f["item_code"], "item_label": f["item_label"],
                         "severity": f["severity"], "remarks": f.get("remarks", "")}
                        for f in warnings
                    ],
                    ambiguous_items_json=[
                        {"item_code": f["item_code"], "item_label": f["item_label"],
                         "remarks": f.get("remarks", "")}
                        for f in ambiguous
                    ],
                    output_payload_json={
                        "total_checks": len(all_findings),
                        "passed": sum(1 for f in all_findings if f["status"] == ValidationItemStatus.PRESENT),
                        "missing": len(missing),
                        "warnings": len(warnings),
                        "ambiguous": len(ambiguous),
                        "rules_applied": len(rules),
                    },
                    tenant=tenant,
                )

                # Create individual result items
                items_to_create = [
                    ValidationResultItem(
                        validation_result=validation_result,
                        item_code=f["item_code"],
                        item_label=f["item_label"],
                        category=f["category"],
                        status=f["status"],
                        severity=f["severity"],
                        source_type=f.get("source_type", "RULE"),
                        source_reference=f.get("source_reference", ""),
                        remarks=f.get("remarks", ""),
                        details_json=f.get("details_json"),
                    )
                    for f in all_findings
                ]
                ValidationResultItem.objects.bulk_create(items_to_create)

            # 6. Complete the run
            AnalysisRunService.complete_run(
                run,
                output_summary=summary,
                confidence_score=score / 100.0,
            )

            # Audit
            AuditService.log_event(
                entity_type="ProcurementRequest",
                entity_id=request.pk,
                event_type="VALIDATION_COMPLETED",
                description=f"Validation {overall_status}: score {score:.0f}%, "
                            f"{len(missing)} missing, {len(warnings)} warnings, {len(ambiguous)} ambiguous",
                trace_ctx=ctx,
                status_after=overall_status,
                output_snapshot={
                    "completeness_score": score,
                    "overall_status": overall_status,
                    "missing_count": len(missing),
                    "warning_count": len(warnings),
                    "ambiguous_count": len(ambiguous),
                    "next_action": next_action,
                },
            )

            logger.info(
                "Validation completed for request %s: %s (%.0f%%)",
                request.request_id, overall_status, score,
            )
            return validation_result

        except Exception:
            AnalysisRunService.fail_run(run, "Validation failed unexpectedly")
            raise


# ---------------------------------------------------------------------------
# Scoring and classification helpers
# ---------------------------------------------------------------------------

def _compute_completeness_score(findings: List[Dict[str, Any]]) -> float:
    """Compute a 0-100 completeness score based on findings."""
    if not findings:
        return 100.0

    total = len(findings)
    present = sum(1 for f in findings if f["status"] == ValidationItemStatus.PRESENT)

    # Weight by severity — CRITICAL missing items count double
    penalty = 0.0
    for f in findings:
        if f["status"] == ValidationItemStatus.PRESENT:
            continue
        if f["severity"] == ValidationSeverity.CRITICAL:
            penalty += 2.0
        elif f["severity"] == ValidationSeverity.ERROR:
            penalty += 1.0
        elif f["severity"] == ValidationSeverity.WARNING:
            penalty += 0.5
        else:
            penalty += 0.25

    weighted_total = total + penalty
    if weighted_total == 0:
        return 100.0

    score = (present / weighted_total) * 100.0
    return max(0.0, min(100.0, round(score, 1)))


def _determine_overall_status(
    findings: List[Dict[str, Any]],
    score: float,
) -> str:
    """Determine overall validation status."""
    critical_missing = any(
        f["status"] in (ValidationItemStatus.MISSING, ValidationItemStatus.FAILED)
        and f["severity"] == ValidationSeverity.CRITICAL
        for f in findings
    )
    error_missing = any(
        f["status"] in (ValidationItemStatus.MISSING, ValidationItemStatus.FAILED)
        and f["severity"] == ValidationSeverity.ERROR
        for f in findings
    )
    has_warnings = any(
        f["status"] in (ValidationItemStatus.WARNING, ValidationItemStatus.AMBIGUOUS)
        for f in findings
    )

    if critical_missing:
        return ValidationOverallStatus.FAIL
    if error_missing:
        return ValidationOverallStatus.REVIEW_REQUIRED
    if has_warnings:
        return ValidationOverallStatus.PASS_WITH_WARNINGS
    return ValidationOverallStatus.PASS


def _determine_next_action(
    overall_status: str,
    missing: List[Dict[str, Any]],
    ambiguous: List[Dict[str, Any]],
) -> str:
    """Determine recommended next action."""
    if overall_status == ValidationOverallStatus.FAIL:
        return ValidationNextAction.REQUEST_REFINEMENT

    # Check if missing items are commercial
    commercial_missing = any(
        f["category"] == ValidationType.COMMERCIAL_COMPLETENESS for f in missing
    )
    if commercial_missing:
        return ValidationNextAction.NEEDS_COMMERCIAL_REVIEW

    # Check for compliance issues
    compliance_missing = any(
        f["category"] == ValidationType.COMPLIANCE_READINESS for f in missing
    )
    if compliance_missing:
        return ValidationNextAction.NEEDS_TECHNICAL_REVIEW

    if overall_status == ValidationOverallStatus.REVIEW_REQUIRED:
        return ValidationNextAction.REQUEST_REFINEMENT

    if ambiguous:
        return ValidationNextAction.NEEDS_TECHNICAL_REVIEW

    return ValidationNextAction.READY_FOR_RECOMMENDATION


def _determine_readiness(
    overall_status: str,
    missing: List[Dict[str, Any]],
) -> tuple[bool, bool]:
    """Determine readiness flags."""
    if overall_status == ValidationOverallStatus.FAIL:
        return False, False

    # Readiness for recommendation is less strict
    ready_for_rec = overall_status in (
        ValidationOverallStatus.PASS,
        ValidationOverallStatus.PASS_WITH_WARNINGS,
    )

    # Readiness for benchmarking requires quotation data
    doc_missing = any(
        f["category"] == ValidationType.DOCUMENT_COMPLETENESS for f in missing
    )
    ready_for_bench = ready_for_rec and not doc_missing

    return ready_for_rec, ready_for_bench


def _build_summary(
    overall_status: str,
    score: float,
    missing_count: int,
    warning_count: int,
    ambiguous_count: int,
) -> str:
    """Build human-readable summary."""
    if overall_status == ValidationOverallStatus.PASS:
        return (
            f"The request is complete and ready for analysis. "
            f"Completeness score: {score:.0f}%."
        )
    if overall_status == ValidationOverallStatus.PASS_WITH_WARNINGS:
        return (
            f"The request is mostly complete ({score:.0f}%) but has "
            f"{warning_count} warning(s) and {ambiguous_count} ambiguous item(s) "
            f"that should be reviewed."
        )
    if overall_status == ValidationOverallStatus.REVIEW_REQUIRED:
        return (
            f"The request requires refinement. {missing_count} required item(s) missing, "
            f"{warning_count} warning(s). Completeness: {score:.0f}%."
        )
    return (
        f"The request has critical gaps and cannot proceed. "
        f"{missing_count} item(s) missing, {warning_count} warning(s). "
        f"Completeness: {score:.0f}%."
    )


def _run_agent_augmentation(
    request: ProcurementRequest,
    run: AnalysisRun,
    findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Optionally invoke ValidationAgent for ambiguity resolution.

    Returns updated findings list. Falls back to original findings on error.
    """
    try:
        from apps.procurement.services.validation.validation_agent import (
            ValidationAgentService,
        )

        return ValidationAgentService.augment_findings(request, run, findings)
    except Exception:
        logger.warning(
            "ValidationAgent augmentation failed for request %s, using deterministic results",
            request.request_id,
            exc_info=True,
        )
        return findings
