"""ValidationOrchestratorService — central service that runs all validations.

Phase 1 agentic bridge: AI agent augmentation now routes through
ProcurementAgentOrchestrator for standard tracing, audit, and execution records.
Deterministic validators are unchanged.
"""
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
from apps.procurement.runtime import ProcurementAgentMemory, ProcurementAgentOrchestrator
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
        request_user: Any = None,
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
        # Tenant propagation is explicit for multi-tenant safety.
        tenant = request.tenant

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
            # Phase 1: routes through ProcurementAgentOrchestrator bridge
            if agent_enabled:
                ambiguous_count = sum(
                    1 for f in all_findings
                    if f.get("status") == ValidationItemStatus.AMBIGUOUS
                )
                if ambiguous_count >= 3:
                    all_findings = _run_agent_augmentation(
                        request, run, all_findings, request_user=request_user
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
            failure_digest = _build_failure_digest(all_findings, overall_status, score)

            # Build thought-process log for AnalysisRun
            thought_process = [
                {"step": 1, "stage": "rule_resolution",
                 "decision": f"{len(rules)} validation rule(s) resolved",
                 "reasoning": "Rules matched to domain/schema of this request."},
                {"step": 2, "stage": "attribute_completeness",
                 "decision": f"{len([f for f in all_findings if f.get('category') == 'ATTRIBUTE_COMPLETENESS'])} checks run",
                 "reasoning": "Verified all required store parameters are present."},
                {"step": 3, "stage": "document_completeness",
                 "decision": f"{len([f for f in all_findings if f.get('category') == 'DOCUMENT_COMPLETENESS'])} checks run",
                 "reasoning": "Verified quotation document presence and completeness."},
                {"step": 4, "stage": "scope_coverage",
                 "decision": f"{len([f for f in all_findings if f.get('category') == 'SCOPE_COVERAGE'])} checks run",
                 "reasoning": "Validated HVAC scope categories match store type expectation."},
                {"step": 5, "stage": "commercial_completeness",
                 "decision": f"{len([f for f in all_findings if f.get('category') == 'COMMERCIAL_COMPLETENESS'])} checks run",
                 "reasoning": "Checked for warranty, payment terms, and lead time."},
                {"step": 6, "stage": "compliance_readiness",
                 "decision": f"{len([f for f in all_findings if f.get('category') == 'COMPLIANCE_READINESS'])} checks run",
                 "reasoning": "Ensured geography and compliance pre-conditions are set."},
                {"step": 7, "stage": "scoring",
                 "decision": f"Score={score:.1f}%, Status={overall_status}, NextAction={next_action}",
                 "reasoning": (
                     f"Missing={len(missing)}, Warnings={len(warnings)}, "
                     f"Ambiguous={len(ambiguous)}. "
                     "Score weighted by severity and category importance."
                 )},
            ]

            with transaction.atomic():
                validation_result = ValidationResult.objects.create(
                    run=run,
                    validation_type=ValidationType.ATTRIBUTE_COMPLETENESS,
                    overall_status=overall_status,
                    completeness_score=score,
                    summary_text=summary,
                    failure_digest_text=failure_digest,
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

            # 6. Complete the run and persist thought-process log
            AnalysisRunService.complete_run(
                run,
                output_summary=summary,
                confidence_score=score / 100.0,
            )
            # Write the step-by-step thought process log to AnalysisRun
            try:
                run.thought_process_log = thought_process
                run.save(update_fields=["thought_process_log"])
            except Exception:
                logger.debug("Could not persist thought_process_log -- skipping", exc_info=True)

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


_SEVERITY_LABEL = {
    "ERROR": "[CRITICAL]",
    "WARNING": "[WARNING]",
    "INFO": "[INFO]",
}

_CATEGORY_WHY = {
    "ATTRIBUTE_COMPLETENESS": (
        "Required store/site parameters missing. Without these the rules engine "
        "and AI cannot produce a valid HVAC system recommendation."
    ),
    "DOCUMENT_COMPLETENESS": (
        "Supplier quotation document is absent or incomplete. Benchmarking and "
        "commercial review cannot run without a valid quotation PDF."
    ),
    "SCOPE_COVERAGE": (
        "The quotation scope does not cover all expected HVAC categories for "
        "this store type. Missing categories may indicate scope gaps or wrong "
        "work classification."
    ),
    "COMMERCIAL_COMPLETENESS": (
        "Commercial terms (warranty, payment, lead time, taxes) are absent. "
        "Finance and procurement leadership require these before approval."
    ),
    "COMPLIANCE_READINESS": (
        "Compliance pre-conditions not met (e.g. geography not set, no ESMA/ASHRAE "
        "standards linked). The compliance check module will not run until these "
        "are resolved."
    ),
    "AMBIGUITY": (
        "One or more field values are ambiguous or cannot be normalised. "
        "The AI could not determine the intended value from the text provided."
    ),
}


def _build_failure_digest(
    all_findings: List[Dict[str, Any]],
    overall_status: str,
    score: float,
) -> str:
    """Build a plain-English root-cause digest of every validation failure.

    The digest explains:
    - Which items failed / are missing
    - Why each item is required (category-level rationale)
    - The exact remediation step to fix it
    - A priority order (CRITICAL first, then WARNING)

    This is written to ValidationResult.failure_digest_text so developers
    and analysts can understand failures at a glance without reading raw JSON.
    """
    if overall_status == ValidationOverallStatus.PASS:
        return ""

    lines: List[str] = [
        f"=== VALIDATION FAILURE DIGEST ===",
        f"Overall Status : {overall_status}",
        f"Completeness   : {score:.0f}%",
        "",
    ]

    # Group failures by category for clarity
    failed_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for f in all_findings:
        if f["status"] not in (
            ValidationItemStatus.MISSING,
            ValidationItemStatus.FAILED,
            ValidationItemStatus.WARNING,
            ValidationItemStatus.AMBIGUOUS,
        ):
            continue
        cat = f.get("category", "UNKNOWN")
        failed_by_cat.setdefault(cat, []).append(f)

    if not failed_by_cat:
        lines.append("No failures found -- status may be PASS_WITH_WARNINGS only.")
        return "\n".join(lines)

    for cat, findings in failed_by_cat.items():
        why = _CATEGORY_WHY.get(cat, "")
        lines.append(f"--- {cat} ---")
        if why:
            lines.append(f"Why this category matters: {why}")
        lines.append("")

        for idx, f in enumerate(findings, 1):
            severity_tag = _SEVERITY_LABEL.get(f.get("severity", ""), "[OTHER]")
            item_label = f.get("item_label", f.get("item_code", "Unknown"))
            item_code = f.get("item_code", "")
            status = f.get("status", "")
            remarks = f.get("remarks", "").strip()
            details = f.get("details_json") or {}
            remediation = details.get("remediation_hint", "")

            lines.append(f"  {idx}. {severity_tag} {item_label} ({item_code})")
            lines.append(f"     Status     : {status}")
            if remarks:
                lines.append(f"     Root Cause : {remarks}")
            if remediation:
                lines.append(f"     Fix Action : {remediation}")
            elif status == ValidationItemStatus.MISSING:
                lines.append(
                    f"     Fix Action : Provide a value for '{item_label}' before re-running validation."
                )
            elif status == ValidationItemStatus.AMBIGUOUS:
                lines.append(
                    f"     Fix Action : Clarify or standardise the value for '{item_label}'."
                )
            elif status in (ValidationItemStatus.FAILED, ValidationItemStatus.WARNING):
                lines.append(
                    f"     Fix Action : Review '{item_label}' and correct per the rule requirements."
                )
            lines.append("")

        lines.append("")

    lines.append(
        "=== END OF DIGEST ===\n"
        "Re-run Validation after addressing each Fix Action above to progress "
        "to Recommendation or Benchmarking stage."
    )
    return "\n".join(lines)


def _run_agent_augmentation(
    request: ProcurementRequest,
    run: AnalysisRun,
    findings: List[Dict[str, Any]],
    *,
    request_user: Any = None,
) -> List[Dict[str, Any]]:
    """Invoke ValidationAgent for ambiguity resolution via ProcurementAgentOrchestrator.

    Phase 1 bridge: wraps ValidationAgentService call so that a
    ProcurementAgentExecutionRecord is created for governance, Langfuse tracing
    fires, and audit events are emitted -- without modifying the inner
    ValidationAgentService logic that creates its own AgentRun records.

    Falls back to original findings on any error.
    """
    try:
        from apps.procurement.services.validation.validation_agent import (
            ValidationAgentService,
        )

        orchestrator = ProcurementAgentOrchestrator()
        memory = ProcurementAgentMemory()
        ambiguous_count = sum(1 for f in findings if f.get("status") == ValidationItemStatus.AMBIGUOUS)

        def _agent_fn(ctx):  # noqa: ANN001
            updated = ValidationAgentService.augment_findings(request, run, findings)
            resolved = sum(
                1 for f in updated
                if f.get("status") != ValidationItemStatus.AMBIGUOUS
                and next(
                    (o for o in findings if o["item_code"] == f["item_code"]
                     and o["status"] == ValidationItemStatus.AMBIGUOUS),
                    None,
                ) is not None
            )
            return {
                "updated_findings": updated,
                "resolved_count": resolved,
                "ambiguous_input_count": ambiguous_count,
                "confidence": 0.7,
                "reasoning_summary": (
                    f"Resolved {resolved} of {ambiguous_count} ambiguous validation items "
                    f"for procurement request {request.request_id}"
                ),
            }

        orch_result = orchestrator.run(
            run=run,
            agent_type="validation_augmentation",
            agent_fn=_agent_fn,
            memory=memory,
            extra_context={
                "ambiguous_count": ambiguous_count,
                "request_id": request.request_id,
            },
            request_user=request_user,
        )

        if orch_result.status == "completed":
            updated_findings = orch_result.output.get("updated_findings")
            if isinstance(updated_findings, list) and updated_findings:
                return updated_findings

        # Orchestrator failed/skipped -- return original deterministic results
        return findings

    except Exception:
        logger.warning(
            "ValidationAgent augmentation failed for request %s, using deterministic results",
            request.request_id,
            exc_info=True,
        )
        return findings
