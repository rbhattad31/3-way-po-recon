"""
Case & reconciliation builder — APCase, ReconciliationResult, Exceptions.

Creates AP Cases, Reconciliation Runs/Results/Exceptions for each scenario.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import (
    APCase,
    APCaseActivity,
    APCaseArtifact,
    APCaseDecision,
    APCaseStage,
)
from apps.core.enums import (
    ArtifactType,
    BudgetCheckStatus,
    CasePriority,
    CaseStageType,
    CaseStatus,
    CodingStatus,
    DecisionSource,
    DecisionType,
    ExceptionSeverity,
    ExceptionType,
    InvoiceType,
    MatchStatus,
    PerformedByType,
    ProcessingPath,
    ReconciliationMode,
    ReconciliationModeApplicability,
    ReconciliationRunStatus,
    SourceChannel,
    StageStatus,
)
from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.models import (
    ReconciliationException,
    ReconciliationResult,
    ReconciliationResultLine,
    ReconciliationRun,
)

logger = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================

_EXCEPTION_SEVERITY_MAP = {
    "PO_NOT_FOUND": ExceptionSeverity.HIGH,
    "VENDOR_MISMATCH": ExceptionSeverity.HIGH,
    "ITEM_MISMATCH": ExceptionSeverity.MEDIUM,
    "QTY_MISMATCH": ExceptionSeverity.MEDIUM,
    "PRICE_MISMATCH": ExceptionSeverity.HIGH,
    "TAX_MISMATCH": ExceptionSeverity.MEDIUM,
    "AMOUNT_MISMATCH": ExceptionSeverity.MEDIUM,
    "DUPLICATE_INVOICE": ExceptionSeverity.CRITICAL,
    "EXTRACTION_LOW_CONFIDENCE": ExceptionSeverity.HIGH,
    "CURRENCY_MISMATCH": ExceptionSeverity.MEDIUM,
    "LOCATION_MISMATCH": ExceptionSeverity.LOW,
    "GRN_NOT_FOUND": ExceptionSeverity.HIGH,
    "RECEIPT_SHORTAGE": ExceptionSeverity.MEDIUM,
    "INVOICE_QTY_EXCEEDS_RECEIVED": ExceptionSeverity.MEDIUM,
    "OVER_RECEIPT": ExceptionSeverity.LOW,
    "MULTI_GRN_PARTIAL_RECEIPT": ExceptionSeverity.LOW,
    "RECEIPT_LOCATION_MISMATCH": ExceptionSeverity.MEDIUM,
    "DELAYED_RECEIPT": ExceptionSeverity.LOW,
}

_EXCEPTION_MESSAGES = {
    "PO_NOT_FOUND": "No matching Purchase Order found for the referenced PO number.",
    "VENDOR_MISMATCH": "Invoice vendor does not match the PO vendor.",
    "ITEM_MISMATCH": "One or more invoice line items do not match PO line items.",
    "QTY_MISMATCH": "Invoice quantity differs from PO/GRN quantity beyond tolerance.",
    "PRICE_MISMATCH": "Invoice unit price differs from PO contract price.",
    "TAX_MISMATCH": "Invoice VAT/tax amount does not match expected calculation.",
    "AMOUNT_MISMATCH": "Invoice total amount differs from PO total beyond tolerance.",
    "DUPLICATE_INVOICE": "Potential duplicate — same vendor, amount, and invoice period detected.",
    "EXTRACTION_LOW_CONFIDENCE": "OCR extraction confidence below threshold (< 0.75).",
    "GRN_NOT_FOUND": "No Goods Receipt Note found for this PO.",
    "RECEIPT_SHORTAGE": "GRN received quantity is less than invoice/PO quantity.",
    "OVER_RECEIPT": "GRN received quantity exceeds PO ordered quantity.",
    "DELAYED_RECEIPT": "GRN receipt date is after the invoice date.",
}


def _mode_applicability(exc_type: str) -> str:
    three_way_only = {
        "GRN_NOT_FOUND", "RECEIPT_SHORTAGE", "INVOICE_QTY_EXCEEDS_RECEIVED",
        "OVER_RECEIPT", "MULTI_GRN_PARTIAL_RECEIPT", "RECEIPT_LOCATION_MISMATCH",
        "DELAYED_RECEIPT",
    }
    if exc_type in three_way_only:
        return ReconciliationModeApplicability.THREE_WAY
    return ReconciliationModeApplicability.BOTH


def _case_status(s: str) -> str:
    """Map scenario status string to CaseStatus value."""
    return getattr(CaseStatus, s, CaseStatus.NEW)


def _processing_path(p: str) -> str:
    return getattr(ProcessingPath, p, ProcessingPath.UNRESOLVED)


def _match_status(m: str | None) -> str | None:
    if m is None:
        return None
    return getattr(MatchStatus, m, None)


# ============================================================
# Reconciliation Run + Result
# ============================================================

def _create_recon_run(admin: User) -> ReconciliationRun:
    """Create or get a shared reconciliation run for seeded data."""
    run, _ = ReconciliationRun.objects.get_or_create(
        celery_task_id="seed-run-mcdksa",
        defaults={
            "status": ReconciliationRunStatus.COMPLETED,
            "started_at": timezone.now() - timedelta(hours=2),
            "completed_at": timezone.now() - timedelta(hours=1),
            "total_invoices": 30,
            "matched_count": 6,
            "partial_count": 10,
            "unmatched_count": 4,
            "error_count": 0,
            "review_count": 10,
            "triggered_by": admin,
            "created_by": admin,
        },
    )
    return run


def _create_recon_result(
    run: ReconciliationRun,
    scenario: dict,
    invoice: Invoice,
    po: PurchaseOrder | None,
    admin: User,
) -> ReconciliationResult | None:
    """Create a ReconciliationResult for PO-backed scenarios."""
    match = _match_status(scenario.get("match"))
    if match is None and scenario["path"] == "NON_PO":
        # Non-PO — no recon result
        return None

    path = scenario["path"]
    is_two_way = path == "TWO_WAY"
    mode = ReconciliationMode.TWO_WAY if is_two_way else ReconciliationMode.THREE_WAY

    vendor_match = po is not None and invoice.vendor_id == (po.vendor_id if po else None)
    po_total = po.total_amount if po else Decimal("0")
    diff = invoice.total_amount - po_total if po else Decimal("0")
    diff_pct = (abs(diff) / po_total * 100) if po_total else Decimal("0")

    result, _ = ReconciliationResult.objects.get_or_create(
        run=run,
        invoice=invoice,
        defaults={
            "purchase_order": po,
            "match_status": match or MatchStatus.UNMATCHED,
            "requires_review": scenario.get("review_required", False),
            "vendor_match": vendor_match,
            "currency_match": True,
            "po_total_match": abs(diff) < Decimal("1"),
            "invoice_total_vs_po": diff,
            "total_amount_difference": diff,
            "total_amount_difference_pct": diff_pct,
            "grn_available": path == "THREE_WAY" and "GRN_NOT_FOUND" not in scenario.get("exceptions", []),
            "grn_fully_received": match == "MATCHED" and path == "THREE_WAY",
            "extraction_confidence": invoice.extraction_confidence or 0.95,
            "deterministic_confidence": 0.90 if match == "MATCHED" else 0.65,
            "summary": scenario["description"],
            "reconciliation_mode": mode,
            "is_two_way_result": is_two_way,
            "is_three_way_result": not is_two_way,
            "mode_resolution_reason": "policy" if is_two_way else "default",
            "created_by": admin,
        },
    )
    return result


def _create_recon_exceptions(
    result: ReconciliationResult,
    scenario: dict,
) -> list[ReconciliationException]:
    """Create ReconciliationException records for each exception type."""
    created = []
    for exc_key in scenario.get("exceptions", []):
        exc_type = getattr(ExceptionType, exc_key, None)
        if not exc_type:
            continue
        severity = _EXCEPTION_SEVERITY_MAP.get(exc_key, ExceptionSeverity.MEDIUM)
        message = _EXCEPTION_MESSAGES.get(exc_key, f"{exc_key} detected.")
        applicability = _mode_applicability(exc_key)

        is_resolved = scenario["status"] in ("CLOSED", "REVIEW_COMPLETED", "REJECTED")

        exc, _ = ReconciliationException.objects.get_or_create(
            result=result,
            exception_type=exc_type,
            defaults={
                "severity": severity,
                "message": message,
                "details": {"scenario": scenario["tag"], "auto_generated": True},
                "resolved": is_resolved,
                "applies_to_mode": applicability,
            },
        )
        created.append(exc)
    return created


# ============================================================
# AP Case + Stages + Decisions + Artifacts
# ============================================================

def _stages_for_path(path: str, status: str) -> list[tuple[str, str]]:
    """Return list of (stage_name, stage_status) based on path and case status."""
    common_start = [
        (CaseStageType.INTAKE, StageStatus.COMPLETED),
        (CaseStageType.EXTRACTION, StageStatus.COMPLETED),
        (CaseStageType.PATH_RESOLUTION, StageStatus.COMPLETED),
    ]

    status_obj = _case_status(status)

    if path == "TWO_WAY":
        stages = common_start + [
            (CaseStageType.PO_RETRIEVAL, StageStatus.COMPLETED),
            (CaseStageType.TWO_WAY_MATCHING, StageStatus.COMPLETED),
        ]
    elif path == "THREE_WAY":
        stages = common_start + [
            (CaseStageType.PO_RETRIEVAL, StageStatus.COMPLETED),
            (CaseStageType.THREE_WAY_MATCHING, StageStatus.COMPLETED),
            (CaseStageType.GRN_ANALYSIS, StageStatus.COMPLETED),
        ]
    else:  # NON_PO
        stages = common_start + [
            (CaseStageType.NON_PO_VALIDATION, StageStatus.COMPLETED),
        ]

    # Trim stages based on status — if still early, not all completed
    early_statuses = {
        CaseStatus.NEW, CaseStatus.INTAKE_IN_PROGRESS,
        CaseStatus.EXTRACTION_IN_PROGRESS, CaseStatus.EXTRACTION_COMPLETED,
    }
    if status_obj in early_statuses:
        # Only first 1-2 stages completed
        for i in range(min(2, len(stages)), len(stages)):
            stages[i] = (stages[i][0], StageStatus.PENDING)
        return stages

    in_progress_statuses = {
        CaseStatus.TWO_WAY_IN_PROGRESS, CaseStatus.THREE_WAY_IN_PROGRESS,
        CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, CaseStatus.GRN_ANALYSIS_IN_PROGRESS,
        CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
    }
    if status_obj in in_progress_statuses:
        # Last path-specific stage is in_progress
        if len(stages) > 3:
            stages[-1] = (stages[-1][0], StageStatus.IN_PROGRESS)
        return stages

    # Add exception analysis + review routing + summary for review/closed cases
    stages.append((CaseStageType.EXCEPTION_ANALYSIS, StageStatus.COMPLETED))
    stages.append((CaseStageType.REVIEW_ROUTING, StageStatus.COMPLETED))

    review_in_progress = {
        CaseStatus.READY_FOR_REVIEW, CaseStatus.IN_REVIEW,
    }
    if status_obj in review_in_progress:
        stages.append((CaseStageType.CASE_SUMMARY, StageStatus.COMPLETED))
        stages.append((CaseStageType.REVIEWER_COPILOT, StageStatus.WAITING_HUMAN))
        return stages

    # Closed / completed
    stages.append((CaseStageType.CASE_SUMMARY, StageStatus.COMPLETED))

    if status_obj in (CaseStatus.READY_FOR_APPROVAL, CaseStatus.APPROVAL_IN_PROGRESS):
        stages.append((CaseStageType.APPROVAL, StageStatus.IN_PROGRESS))
    elif status_obj == CaseStatus.READY_FOR_GL_CODING:
        stages.append((CaseStageType.APPROVAL, StageStatus.COMPLETED))
        stages.append((CaseStageType.GL_CODING, StageStatus.PENDING))
    elif status_obj in (CaseStatus.CLOSED, CaseStatus.REVIEW_COMPLETED, CaseStatus.REJECTED):
        stages.append((CaseStageType.APPROVAL, StageStatus.COMPLETED))
    elif status_obj == CaseStatus.ESCALATED:
        stages.append((CaseStageType.REVIEWER_COPILOT, StageStatus.WAITING_HUMAN))

    return stages


def create_cases_and_recon(
    scenario_data: dict,
    admin: User,
) -> dict:
    """
    Create APCase, ReconciliationResult/Exceptions, Stages, Decisions, Artifacts.
    Returns {scenario_num: {case, recon_result, exceptions, stages}}.
    """
    run = _create_recon_run(admin)
    case_results = {}

    for sc_num, sd in scenario_data.items():
        sc = sd["scenario"]
        invoice = sd["invoice"]
        po = sd.get("po")
        vendor = invoice.vendor

        # --- Reconciliation Result (PO-backed only) ---
        recon_result = None
        exceptions = []
        if sc["path"] in ("TWO_WAY", "THREE_WAY"):
            recon_result = _create_recon_result(run, sc, invoice, po, admin)
            if recon_result:
                exceptions = _create_recon_exceptions(recon_result, sc)

        # --- AP Case ---
        case_num = f"AP-{sc_num:06d}"
        path = _processing_path(sc["path"])
        status = _case_status(sc["status"])
        priority = getattr(CasePriority, sc.get("priority", "MEDIUM"), CasePriority.MEDIUM)

        inv_type = InvoiceType.PO_BACKED if sc["path"] != "NON_PO" else InvoiceType.NON_PO
        recon_mode = ""
        if sc["path"] == "TWO_WAY":
            recon_mode = ReconciliationMode.TWO_WAY
        elif sc["path"] == "THREE_WAY":
            recon_mode = ReconciliationMode.THREE_WAY

        budget_status = ""
        if sc["path"] == "NON_PO":
            budget_status = BudgetCheckStatus.NOT_CHECKED
            if "BUDGET" in sc["tag"].upper():
                budget_status = BudgetCheckStatus.NO_BUDGET_DATA
            elif sc["status"] in ("CLOSED", "READY_FOR_GL_CODING", "READY_FOR_POSTING"):
                budget_status = BudgetCheckStatus.WITHIN_BUDGET

        coding_status = ""
        if sc["status"] == "READY_FOR_GL_CODING":
            coding_status = CodingStatus.NOT_STARTED
        elif sc["status"] in ("CLOSED",) and sc["path"] == "NON_PO":
            coding_status = CodingStatus.ACCEPTED

        risk_score = 0.2
        if priority == CasePriority.HIGH:
            risk_score = 0.65
        elif priority == CasePriority.CRITICAL:
            risk_score = 0.85
        elif priority == CasePriority.MEDIUM:
            risk_score = 0.40

        case, _ = APCase.objects.get_or_create(
            case_number=case_num,
            defaults={
                "invoice": invoice,
                "vendor": vendor,
                "purchase_order": po,
                "reconciliation_result": recon_result,
                "source_channel": SourceChannel.WEB_UPLOAD,
                "invoice_type": inv_type,
                "processing_path": path,
                "status": status,
                "current_stage": "",
                "priority": priority,
                "risk_score": risk_score,
                "extraction_confidence": invoice.extraction_confidence,
                "requires_human_review": sc.get("review_required", False),
                "requires_approval": sc["status"] in ("READY_FOR_APPROVAL",),
                "eligible_for_posting": sc["status"] in ("READY_FOR_POSTING", "CLOSED"),
                "duplicate_risk_flag": "DUPLICATE" in sc.get("tag", ""),
                "reconciliation_mode": recon_mode,
                "budget_check_status": budget_status,
                "coding_status": coding_status,
                "created_by": admin,
            },
        )

        # --- Stages ---
        stage_defs = _stages_for_path(sc["path"], sc["status"])
        created_stages = []
        base_time = timezone.now() - timedelta(hours=48, minutes=sc_num * 30)
        for idx, (stage_name, stage_status) in enumerate(stage_defs):
            started = base_time + timedelta(minutes=idx * 15)
            completed = started + timedelta(minutes=10) if stage_status == StageStatus.COMPLETED else None
            st, _ = APCaseStage.objects.get_or_create(
                case=case,
                stage_name=stage_name,
                retry_count=0,
                defaults={
                    "stage_status": stage_status,
                    "performed_by_type": PerformedByType.AGENT if "AGENT" not in stage_name else PerformedByType.SYSTEM,
                    "started_at": started,
                    "completed_at": completed,
                },
            )
            created_stages.append(st)

        # Update current_stage to last stage
        if created_stages:
            case.current_stage = created_stages[-1].stage_name
            case.save(update_fields=["current_stage"])

        # --- Key Decisions ---
        decisions = []
        # Path selected decision
        APCaseDecision.objects.get_or_create(
            case=case,
            decision_type=DecisionType.PATH_SELECTED,
            decision_value=sc["path"],
            defaults={
                "decision_source": DecisionSource.DETERMINISTIC,
                "confidence": 0.95,
                "rationale": f"Processing path resolved to {sc['path']} based on invoice type and vendor category.",
            },
        )

        if po:
            APCaseDecision.objects.get_or_create(
                case=case,
                decision_type=DecisionType.PO_LINKED,
                decision_value=po.po_number,
                defaults={
                    "decision_source": DecisionSource.AGENT,
                    "confidence": 0.92 if "OCR" not in sc["tag"] else 0.55,
                    "rationale": f"PO {po.po_number} linked to invoice {invoice.invoice_number}.",
                },
            )

        if recon_result:
            APCaseDecision.objects.get_or_create(
                case=case,
                decision_type=DecisionType.MATCH_DETERMINED,
                decision_value=str(recon_result.match_status),
                defaults={
                    "decision_source": DecisionSource.DETERMINISTIC,
                    "confidence": 0.90,
                    "rationale": f"Match status: {recon_result.match_status}. {sc['description']}",
                },
            )

        if sc.get("review_required"):
            APCaseDecision.objects.get_or_create(
                case=case,
                decision_type=DecisionType.SENT_TO_REVIEW,
                decision_value="AP_REVIEW",
                defaults={
                    "decision_source": DecisionSource.AGENT,
                    "confidence": 0.88,
                    "rationale": f"Case routed to review queue. Exceptions: {', '.join(sc.get('exceptions', ['policy']))}.",
                },
            )

        if sc["status"] == "CLOSED" and not sc.get("review_required"):
            APCaseDecision.objects.get_or_create(
                case=case,
                decision_type=DecisionType.AUTO_CLOSED,
                decision_value="AUTO_CLOSED",
                defaults={
                    "decision_source": DecisionSource.DETERMINISTIC,
                    "confidence": 0.98,
                    "rationale": "Case auto-closed — perfect match with no exceptions.",
                },
            )

        if sc["status"] == "REJECTED":
            APCaseDecision.objects.get_or_create(
                case=case,
                decision_type=DecisionType.REJECTED,
                decision_value="REJECTED",
                defaults={
                    "decision_source": DecisionSource.HUMAN,
                    "confidence": 1.0,
                    "rationale": "Invoice rejected by reviewer due to contract discrepancy.",
                },
            )

        if sc["status"] == "ESCALATED":
            APCaseDecision.objects.get_or_create(
                case=case,
                decision_type=DecisionType.ESCALATED,
                decision_value="FINANCE_MANAGER",
                defaults={
                    "decision_source": DecisionSource.AGENT,
                    "confidence": 0.82,
                    "rationale": "Case escalated to Finance Manager due to severity and unresolved exceptions.",
                },
            )

        # --- Artifacts ---
        APCaseArtifact.objects.get_or_create(
            case=case,
            artifact_type=ArtifactType.EXTRACTION_RESULT,
            version=1,
            defaults={
                "linked_object_type": "documents.Invoice",
                "linked_object_id": invoice.pk,
                "payload": {
                    "confidence": invoice.extraction_confidence,
                    "vendor_name": invoice.raw_vendor_name,
                    "total_amount": str(invoice.total_amount),
                },
            },
        )

        if recon_result:
            APCaseArtifact.objects.get_or_create(
                case=case,
                artifact_type=ArtifactType.RECONCILIATION_RESULT,
                version=1,
                defaults={
                    "linked_object_type": "reconciliation.ReconciliationResult",
                    "linked_object_id": recon_result.pk,
                    "payload": {
                        "match_status": str(recon_result.match_status),
                        "exceptions_count": len(exceptions),
                    },
                },
            )

        case_results[sc_num] = {
            "case": case,
            "recon_result": recon_result,
            "exceptions": exceptions,
            "stages": created_stages,
        }

    logger.info("Cases & recon: %d cases created", len(case_results))
    return case_results
