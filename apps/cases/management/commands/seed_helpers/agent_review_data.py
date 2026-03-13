"""
Agent runs, recommendations, review assignments, comments, summaries, audit events.

Creates rich agent trace data and review workflow records for each AP case.
"""
from __future__ import annotations

import logging
import random
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import AgentDefinition, AgentRecommendation, AgentRun
from apps.auditlog.models import AuditEvent
from apps.cases.models import (
    APCase,
    APCaseActivity,
    APCaseAssignment,
    APCaseComment,
    APCaseSummary,
)
from apps.core.enums import (
    AgentRunStatus,
    AgentType,
    AssignmentStatus,
    AssignmentType,
    AuditEventType,
    CasePriority,
    CaseStatus,
    ExceptionSeverity,
    PerformedByType,
    RecommendationType,
    ReviewActionType,
    ReviewStatus,
    UserRole,
)
from apps.reconciliation.models import ReconciliationResult
from apps.reviews.models import ReviewAssignment, ReviewComment, ReviewDecision

logger = logging.getLogger(__name__)

_rng = random.Random(42)


# ============================================================
# Agent run templates per stage
# ============================================================

_AGENT_STAGE_MAP = {
    "INTAKE": {
        "agent_name": "Invoice Intake Agent",
        "agent_type": AgentType.INVOICE_UNDERSTANDING,
        "rationale_template": "Validated invoice {inv} from {vendor}. Document format: PDF. Source channel: Web Upload. Content language: {lang}.",
    },
    "EXTRACTION": {
        "agent_name": "Invoice Extraction Agent",
        "agent_type": AgentType.INVOICE_UNDERSTANDING,
        "rationale_template": "Extracted {n_lines} line items from invoice {inv}. Confidence: {conf:.0%}. Vendor: {vendor}. Total: SAR {total}.",
    },
    "PO_RETRIEVAL": {
        "agent_name": "PO Retrieval Agent",
        "agent_type": AgentType.PO_RETRIEVAL,
        "rationale_template": "PO retrieval for invoice {inv}. {po_msg} Confidence: {conf:.0%}.",
    },
    "TWO_WAY_MATCHING": {
        "agent_name": "2-Way Matching Agent",
        "agent_type": AgentType.RECONCILIATION_ASSIST,
        "rationale_template": "2-Way match for invoice {inv} vs PO {po}. Result: {match}. {detail}",
    },
    "THREE_WAY_MATCHING": {
        "agent_name": "3-Way Reconciliation Agent",
        "agent_type": AgentType.RECONCILIATION_ASSIST,
        "rationale_template": "3-Way reconciliation for invoice {inv} vs PO {po} vs GRN(s). Result: {match}. {detail}",
    },
    "GRN_ANALYSIS": {
        "agent_name": "GRN Specialist Agent",
        "agent_type": AgentType.GRN_RETRIEVAL,
        "rationale_template": "GRN analysis for PO {po}. {grn_msg}",
    },
    "NON_PO_VALIDATION": {
        "agent_name": "Non-PO Validation Agent",
        "agent_type": AgentType.EXCEPTION_ANALYSIS,
        "rationale_template": "Non-PO validation for invoice {inv}. Vendor: {vendor}. {validation_msg}",
    },
    "EXCEPTION_ANALYSIS": {
        "agent_name": "Exception Analysis Agent",
        "agent_type": AgentType.EXCEPTION_ANALYSIS,
        "rationale_template": "Analyzed {n_exc} exception(s) for case {case}. {exc_summary}",
    },
    "REVIEW_ROUTING": {
        "agent_name": "Review Routing Agent",
        "agent_type": AgentType.REVIEW_ROUTING,
        "rationale_template": "Routing decision for case {case}. {routing_msg}",
    },
    "CASE_SUMMARY": {
        "agent_name": "Case Summary Agent",
        "agent_type": AgentType.CASE_SUMMARY,
        "rationale_template": "Generated summary for case {case}. Path: {path}. Status: {status}.",
    },
}


# ============================================================
# Review comments templates
# ============================================================

_REVIEW_COMMENTS = {
    "AMOUNT_MISMATCH": "PO total differs from invoice total. Please verify service completion at the relevant branch.",
    "TAX_MISMATCH": "VAT rate on invoice does not match PO terms. Confirm correct VAT treatment with Finance.",
    "PRICE_MISMATCH": "Unit price on invoice exceeds PO contract rate. Escalate to Procurement if unresolvable.",
    "RECEIPT_SHORTAGE": "GRN shows short receipt. Confirm with warehouse manager whether remaining stock is in transit.",
    "OVER_RECEIPT": "GRN indicates over-delivery. Verify if overage was accepted operationally.",
    "GRN_NOT_FOUND": "No GRN posted for this PO. Check with warehouse if goods were received but GRN entry is pending.",
    "DELAYED_RECEIPT": "GRN posted after invoice date. Likely timing issue — confirm with receiving team.",
    "PO_NOT_FOUND": "Referenced PO number not found in system. Check if PO was created under a different number.",
    "DUPLICATE_INVOICE": "Duplicate invoice suspected — same vendor, amount, and invoice number detected against a paid invoice.",
    "EXTRACTION_LOW_CONFIDENCE": "Poor scan quality — extraction confidence below threshold. Manual verification recommended.",
    "VENDOR_MISMATCH": "Vendor name extracted does not match any active vendor. Check for alias or new vendor setup.",
    "QTY_MISMATCH": "Invoice quantity differs from PO/GRN quantity. Verify if partial delivery or quantity amendment applies.",
}


# ============================================================
# Summary templates
# ============================================================

def _generate_summary(scenario: dict, case: APCase) -> dict:
    """Generate role-specific summaries for a case."""
    tag = scenario["tag"]
    desc = scenario["description"]
    path = scenario["path"]
    exceptions = scenario.get("exceptions", [])

    exc_str = ", ".join(exceptions) if exceptions else ""
    exc_note = f"Exceptions: {exc_str}." if exceptions else "No exceptions detected."

    latest = (
        f"Case {case.case_number}: {desc}. "
        f"Processing path: {path}. Priority: {scenario.get('priority', 'MEDIUM')}. "
        f"{exc_note}"
    )

    exc_issues = f"Key issues: {exc_str}. " if exceptions else "No issues — auto-closable. "
    v_code = scenario.get('vendor_code', 'N/A')
    branch = scenario.get('branch', 'HQ')
    reviewer = (
        f"Review needed: {desc}. "
        + exc_issues
        + f"Vendor: {v_code}. Branch: {branch}."
    )

    inv_amount = case.invoice.total_amount if case.invoice else "N/A"
    exc_fin = f"Exceptions requiring attention: {exc_str}." if exceptions else "Clean — ready for posting."
    finance = (
        f"Invoice amount: SAR {inv_amount}. "
        f"Path: {path}. "
        + exc_fin
    )

    recommendation = "Auto-close" if not exceptions else "Send to AP Review for manual resolution"
    if scenario.get("priority") == "CRITICAL":
        recommendation = "Escalate to Finance Manager immediately"
    elif scenario.get("priority") == "HIGH" and len(exceptions) > 1:
        recommendation = "Assign to Senior AP Reviewer for multi-exception investigation"

    return {
        "latest_summary": latest,
        "reviewer_summary": reviewer,
        "finance_summary": finance,
        "recommendation": recommendation,
    }


# ============================================================
# Main seeder
# ============================================================

def _get_reviewer_for_scenario(scenario: dict, users: dict[str, User]) -> User | None:
    """Pick an appropriate reviewer based on category/path."""
    cat = scenario.get("category", "")
    if "Facility" in cat or "HVAC" in cat or "Kitchen" in cat:
        return users.get("reviewer_fac")
    if "Frozen" in cat or "Bakery" in cat or "Fries" in cat or "Packaging" in cat or "Dairy" in cat or "Beverage" in cat or "Condiment" in cat or "Cleaning" in cat:
        return users.get("reviewer_sc")
    if "Telecom" in cat or "Security" in cat or "Logistics" in cat:
        return users.get("reviewer")
    if "Marketing" in cat or "Consulting" in cat or "Training" in cat or "Staffing" in cat:
        return users.get("reviewer_senior")
    return users.get("reviewer")


def seed_agent_review_data(
    scenario_data: dict,
    case_data: dict,
    users: dict[str, User],
    admin: User,
) -> dict:
    """
    Create agent runs, recommendations, review assignments, comments,
    summaries, audit events, and activities for all scenarios.
    """
    stats = {"agent_runs": 0, "recommendations": 0, "assignments": 0, "comments": 0, "summaries": 0, "audit_events": 0}

    # Load agent definitions
    agent_defs = {ad.agent_type: ad for ad in AgentDefinition.objects.all()}

    for sc_num, sd in scenario_data.items():
        sc = sd["scenario"]
        cd = case_data.get(sc_num)
        if not cd:
            continue

        case = cd["case"]
        recon_result = cd.get("recon_result")
        exceptions = cd.get("exceptions", [])
        invoice = sd["invoice"]
        po = sd.get("po")
        grns = sd.get("grns", [])

        base_time = timezone.now() - timedelta(hours=48, minutes=sc_num * 30)

        # ---- Agent Runs per completed stage (requires recon_result) ----
        stages_completed = [
            s for s in cd.get("stages", [])
            if s.stage_status in ("COMPLETED", "IN_PROGRESS")
        ] if recon_result else []

        agent_run = None
        for idx, stage in enumerate(stages_completed):
            stage_key = stage.stage_name
            template = _AGENT_STAGE_MAP.get(stage_key)
            if not template:
                continue

            agent_type = template["agent_type"]
            agent_def = agent_defs.get(agent_type)

            conf = invoice.extraction_confidence or 0.95
            if stage_key in ("TWO_WAY_MATCHING", "THREE_WAY_MATCHING"):
                conf = 0.95 if sc.get("match") == "MATCHED" else 0.72

            started = base_time + timedelta(minutes=idx * 12)
            duration = _rng.randint(800, 4500)
            completed = started + timedelta(milliseconds=duration)

            run_status = AgentRunStatus.COMPLETED
            if stage.stage_status == "IN_PROGRESS":
                run_status = AgentRunStatus.RUNNING
            elif stage.stage_status == "FAILED":
                run_status = AgentRunStatus.FAILED

            # Build rationale from template
            rationale_vars = {
                "inv": invoice.invoice_number,
                "vendor": invoice.raw_vendor_name or "Unknown",
                "lang": "Arabic/English" if _rng.random() > 0.6 else "English",
                "n_lines": len(sd.get("inv_lines", [])),
                "conf": conf,
                "total": str(invoice.total_amount),
                "po": po.po_number if po else "N/A",
                "po_msg": f"Linked PO {po.po_number}." if po else "No PO found.",
                "match": sc.get("match", "N/A"),
                "detail": sc["description"],
                "grn_msg": f"{len(grns)} GRN(s) found." if grns else "No GRN available.",
                "n_exc": len(exceptions),
                "exc_summary": ", ".join(sc.get("exceptions", [])) or "No exceptions.",
                "case": case.case_number,
                "routing_msg": "Routed to AP Review" if sc.get("review_required") else "Auto-closed, no review needed.",
                "path": sc["path"],
                "status": sc["status"],
                "validation_msg": "Passed duplicate check and policy validation." if not sc.get("exceptions") else f"Issues: {', '.join(sc.get('exceptions', []))}",
            }
            try:
                rationale = template["rationale_template"].format(**rationale_vars)
            except (KeyError, IndexError):
                rationale = sc["description"]

            agent_run, created = AgentRun.objects.get_or_create(
                agent_type=agent_type,
                reconciliation_result=recon_result,
                defaults={
                    "agent_definition": agent_def,
                    "status": run_status,
                    "summarized_reasoning": rationale[:500],
                    "input_payload": {"invoice_id": invoice.pk, "case_number": case.case_number},
                    "output_payload": {"result": sc.get("match", "N/A"), "exceptions": sc.get("exceptions", [])},
                    "confidence": conf,
                    "started_at": started,
                    "completed_at": completed if run_status == AgentRunStatus.COMPLETED else None,
                    "duration_ms": duration if run_status == AgentRunStatus.COMPLETED else None,
                    "created_by": admin,
                },
            )
            if created:
                stats["agent_runs"] += 1

                # Link stage to agent run
                stage.performed_by_agent = agent_run
                stage.performed_by_type = PerformedByType.AGENT
                stage.save(update_fields=["performed_by_agent", "performed_by_type"])

        # ---- Agent Recommendation ----
        if recon_result and sc.get("review_required") and agent_run:
            rec_type = RecommendationType.SEND_TO_AP_REVIEW
            if sc.get("priority") == "CRITICAL":
                rec_type = RecommendationType.ESCALATE_TO_MANAGER
            elif "EXTRACTION_LOW_CONFIDENCE" in sc.get("exceptions", []):
                rec_type = RecommendationType.REPROCESS_EXTRACTION
            elif sc["status"] == "CLOSED":
                rec_type = RecommendationType.AUTO_CLOSE

            _, created = AgentRecommendation.objects.get_or_create(
                reconciliation_result=recon_result,
                recommendation_type=rec_type,
                defaults={
                    "agent_run": agent_run,
                    "invoice": invoice,
                    "confidence": 0.85,
                    "reasoning": f"Recommendation for {case.case_number}: {sc['description']}",
                    "recommended_action": rec_type.label if hasattr(rec_type, 'label') else str(rec_type),
                    "evidence": {"exceptions": sc.get("exceptions", []), "scenario": sc["tag"]},
                    "accepted": True if sc["status"] in ("CLOSED", "REVIEW_COMPLETED") else None,
                },
            )
            if created:
                stats["recommendations"] += 1

        # ---- Review Assignment ----
        if sc.get("review_required"):
            reviewer = _get_reviewer_for_scenario(sc, users)
            review_status = ReviewStatus.PENDING
            if sc["status"] in ("IN_REVIEW",):
                review_status = ReviewStatus.IN_REVIEW
            elif sc["status"] in ("REVIEW_COMPLETED", "CLOSED", "READY_FOR_APPROVAL",
                                   "READY_FOR_GL_CODING", "READY_FOR_POSTING"):
                review_status = ReviewStatus.APPROVED
            elif sc["status"] == "REJECTED":
                review_status = ReviewStatus.REJECTED
            elif reviewer:
                review_status = ReviewStatus.ASSIGNED

            if recon_result:
                ra, created = ReviewAssignment.objects.get_or_create(
                    reconciliation_result=recon_result,
                    defaults={
                        "assigned_to": reviewer,
                        "status": review_status,
                        "priority": {"LOW": 3, "MEDIUM": 5, "HIGH": 7, "CRITICAL": 9}.get(sc.get("priority"), 5),
                        "due_date": timezone.now() + timedelta(days=3),
                        "notes": sc["description"],
                        "created_by": admin,
                    },
                )
                if created:
                    stats["assignments"] += 1
                    # Link to case
                    case.review_assignment = ra
                    case.assigned_to = reviewer
                    case.assigned_role = reviewer.role if reviewer else ""
                    case.save(update_fields=["review_assignment", "assigned_to", "assigned_role"])

                # ---- Review Comments ----
                for exc in sc.get("exceptions", [])[:2]:
                    comment_text = _REVIEW_COMMENTS.get(exc, f"Exception detected: {exc}. Review required.")
                    _, cc = ReviewComment.objects.get_or_create(
                        assignment=ra,
                        body=comment_text,
                        defaults={
                            "author": reviewer or admin,
                            "is_internal": True,
                        },
                    )
                    if cc:
                        stats["comments"] += 1

                # ---- Review Decision for completed reviews ----
                if review_status in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
                    ReviewDecision.objects.get_or_create(
                        assignment=ra,
                        defaults={
                            "decided_by": reviewer or admin,
                            "decision": review_status,
                            "reason": f"Review {'approved' if review_status == ReviewStatus.APPROVED else 'rejected'}. {sc['description']}",
                            "decided_at": timezone.now() - timedelta(hours=_rng.randint(1, 24)),
                        },
                    )

            # ---- AP Case Assignment ----
            assignment_status = AssignmentStatus.PENDING
            if sc["status"] in ("IN_REVIEW",):
                assignment_status = AssignmentStatus.IN_PROGRESS
            elif sc["status"] in ("REVIEW_COMPLETED", "CLOSED"):
                assignment_status = AssignmentStatus.COMPLETED
            elif sc["status"] == "ESCALATED":
                assignment_status = AssignmentStatus.ESCALATED

            APCaseAssignment.objects.get_or_create(
                case=case,
                assignment_type=AssignmentType.REVIEW,
                defaults={
                    "assigned_user": reviewer,
                    "assigned_role": reviewer.role if reviewer else "",
                    "queue_name": f"{reviewer.department if reviewer else 'AP'} Queue",
                    "due_at": timezone.now() + timedelta(days=3),
                    "status": assignment_status,
                },
            )

        # ---- AP Case Summary ----
        summaries = _generate_summary(sc, case)
        _, created = APCaseSummary.objects.get_or_create(
            case=case,
            defaults=summaries,
        )
        if created:
            stats["summaries"] += 1

        # ---- AP Case Comments ----
        if sc.get("review_required") and sc.get("exceptions"):
            exc_key = sc["exceptions"][0]
            comment = _REVIEW_COMMENTS.get(exc_key, sc["description"])
            APCaseComment.objects.get_or_create(
                case=case,
                body=comment,
                defaults={
                    "author": _get_reviewer_for_scenario(sc, users) or admin,
                    "is_internal": True,
                },
            )

        # Request-info scenario — add extra comment
        if "REQUEST-INFO" in sc["tag"].upper():
            APCaseComment.objects.get_or_create(
                case=case,
                body="Awaiting warehouse manager confirmation of receipt at Riyadh DC. Please provide GRN reference.",
                defaults={
                    "author": users.get("reviewer_sc") or admin,
                    "is_internal": True,
                },
            )

        # ---- Audit Events ----
        _create_audit_events(case, sc, invoice, admin)
        stats["audit_events"] += 1

        # ---- Activity log ----
        APCaseActivity.objects.get_or_create(
            case=case,
            activity_type="case_created",
            defaults={
                "description": f"AP Case {case.case_number} created for invoice {invoice.invoice_number}.",
                "actor": admin,
                "metadata": {"scenario": sc["tag"]},
            },
        )

    logger.info(
        "Agent/Review data: %d agent_runs, %d recommendations, %d assignments, "
        "%d comments, %d summaries",
        stats["agent_runs"], stats["recommendations"], stats["assignments"],
        stats["comments"], stats["summaries"],
    )
    return stats


def _create_audit_events(case: APCase, scenario: dict, invoice, admin: User):
    """Create realistic audit events for a case."""
    events = [
        (AuditEventType.INVOICE_UPLOADED, f"Invoice {invoice.invoice_number} uploaded."),
        (AuditEventType.EXTRACTION_COMPLETED, f"Extraction completed with confidence {invoice.extraction_confidence:.0%}."),
    ]

    if scenario["path"] in ("TWO_WAY", "THREE_WAY"):
        events.append((AuditEventType.RECONCILIATION_STARTED, f"Reconciliation started for {case.case_number}."))
        events.append((AuditEventType.RECONCILIATION_COMPLETED, f"Reconciliation completed. Match: {scenario.get('match', 'N/A')}."))

    if scenario.get("review_required"):
        events.append((AuditEventType.REVIEW_ASSIGNED, f"Case assigned to reviewer."))

    if scenario["status"] in ("CLOSED", "REVIEW_COMPLETED") and scenario.get("review_required"):
        events.append((AuditEventType.REVIEW_APPROVED, f"Review approved for case {case.case_number}."))

    if scenario["status"] == "REJECTED":
        events.append((AuditEventType.REVIEW_REJECTED, f"Invoice rejected for case {case.case_number}."))

    for event_type, description in events:
        AuditEvent.objects.get_or_create(
            entity_type="APCase",
            entity_id=str(case.pk),
            event_type=event_type,
            event_description=description,
            defaults={
                "action": event_type.value,
                "performed_by": admin,
                "metadata_json": {"case_number": case.case_number, "scenario": scenario["tag"]},
            },
        )
