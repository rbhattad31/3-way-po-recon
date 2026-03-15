"""
AP Copilot Service — read-only AI copilot for AP case investigation.

Synthesises data across cases, invoices, POs, GRNs, reconciliation results,
exceptions, recommendations, reviews, and governance metadata.  Every operation
respects RBAC and emits audit events.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db import models
from django.utils import timezone


def _json_safe(obj):
    """Recursively convert a structure so it is JSON-serializable."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(i) for i in obj]
    return str(obj)

from apps.auditlog.services import AuditService
from apps.copilot.models import CopilotMessage, CopilotSession, CopilotSessionArtifact
from apps.core.enums import (
    AuditEventType,
    CopilotMessageType,
    CopilotSessionStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role-aware field visibility
# ---------------------------------------------------------------------------
GOVERNANCE_ROLES = {"ADMIN", "AUDITOR"}
EXTENDED_ROLES = {"ADMIN", "AUDITOR", "FINANCE_MANAGER", "REVIEWER"}
OPERATIONAL_ROLES = {"AP_PROCESSOR"}

# Suggested prompts per role
ROLE_PROMPTS: Dict[str, List[str]] = {
    "AP_PROCESSOR": [
        "What is the status of this case?",
        "Why was this invoice flagged for review?",
        "Summarise the exceptions on this case.",
        "What does the extraction confidence look like?",
    ],
    "REVIEWER": [
        "What evidence supports this mismatch?",
        "What do the agents recommend for this case?",
        "Show me the reconciliation breakdown.",
        "Are there similar cases from this vendor?",
    ],
    "FINANCE_MANAGER": [
        "What is the financial impact of open exceptions?",
        "Summarise high-priority cases waiting for review.",
        "Which vendors have the most mismatches?",
        "Give me a risk summary for this case.",
    ],
    "AUDITOR": [
        "Show me the full audit trail for this case.",
        "Were there any permission denials on this case?",
        "Which agents participated and what did they decide?",
        "Trace the governance path for this invoice.",
    ],
    "ADMIN": [
        "Give me a system-wide reconciliation summary.",
        "Show agent performance metrics.",
        "List recent RBAC changes.",
        "What is the overall exception breakdown?",
    ],
}


class APCopilotService:
    """Read-only copilot service for AP case investigation and insight."""

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    @staticmethod
    def start_session(
        user,
        case_id: Optional[int] = None,
    ) -> CopilotSession:
        """Create or resume a copilot session.

        If *case_id* is given and the user already has an active session for
        that case, return the existing session.  Otherwise create a new one.
        """
        if case_id:
            existing = CopilotSession.objects.filter(
                user=user,
                linked_case_id=case_id,
                status=CopilotSessionStatus.ACTIVE,
            ).first()
            if existing:
                APCopilotService._audit(
                    AuditEventType.COPILOT_SESSION_RESUMED,
                    "CopilotSession", str(existing.id),
                    f"Resumed copilot session for case {case_id}",
                    user=user, case_id=case_id,
                    session_id=str(existing.id),
                )
                return existing

        # Resolve RBAC snapshot
        primary_role = getattr(user, "role", "")
        roles_snapshot = None
        if hasattr(user, "active_role_codes"):
            try:
                roles_snapshot = list(user.active_role_codes())
            except Exception:
                roles_snapshot = [primary_role] if primary_role else []

        # Derive linked_invoice from case
        linked_invoice = None
        linked_case_obj = None
        if case_id:
            from apps.cases.models import APCase
            linked_case_obj = APCase.objects.filter(pk=case_id).select_related("invoice").first()
            if linked_case_obj:
                linked_invoice = linked_case_obj.invoice

        session = CopilotSession.objects.create(
            user=user,
            actor_primary_role=primary_role,
            actor_roles_snapshot_json=roles_snapshot,
            linked_case_id=case_id,
            linked_invoice=linked_invoice,
            trace_id=uuid.uuid4().hex,
        )

        # Auto-generate title
        if linked_case_obj:
            session.title = f"Investigation: {linked_case_obj.case_number}"
            session.save(update_fields=["title"])

        APCopilotService._audit(
            AuditEventType.COPILOT_SESSION_CREATED,
            "CopilotSession", str(session.id),
            f"Copilot session created{' for case ' + str(case_id) if case_id else ''}",
            user=user, case_id=case_id,
            session_id=str(session.id),
        )
        return session

    @staticmethod
    def list_sessions(user, include_archived: bool = False) -> models.QuerySet:
        """Return sessions for this user, newest first."""
        qs = CopilotSession.objects.filter(user=user)
        if not include_archived:
            qs = qs.filter(is_archived=False)
        return qs.select_related("linked_case", "linked_invoice")

    @staticmethod
    def get_session_detail(user, session_id: str) -> Optional[CopilotSession]:
        """Fetch a single session belonging to the user."""
        session = CopilotSession.objects.filter(
            pk=session_id, user=user,
        ).select_related("linked_case", "linked_invoice").first()
        if session:
            APCopilotService._audit(
                AuditEventType.COPILOT_SESSION_VIEWED,
                "CopilotSession", str(session.id),
                "Viewed copilot session",
                user=user,
                session_id=str(session.id),
                case_id=session.linked_case_id,
            )
        return session

    @staticmethod
    def load_session_messages(user, session_id: str) -> models.QuerySet:
        """Load conversation messages for a session the user owns."""
        return CopilotMessage.objects.filter(
            session__pk=session_id,
            session__user=user,
        ).order_by("created_at")

    @staticmethod
    def archive_session(user, session_id: str) -> bool:
        updated = CopilotSession.objects.filter(
            pk=session_id, user=user,
        ).update(
            is_archived=True,
            status=CopilotSessionStatus.ARCHIVED,
        )
        if updated:
            APCopilotService._audit(
                AuditEventType.COPILOT_SESSION_ARCHIVED,
                "CopilotSession", session_id,
                "Copilot session archived",
                user=user, session_id=session_id,
            )
        return updated > 0

    @staticmethod
    def toggle_pin(user, session_id: str) -> Optional[bool]:
        session = CopilotSession.objects.filter(pk=session_id, user=user).first()
        if not session:
            return None
        session.is_pinned = not session.is_pinned
        session.save(update_fields=["is_pinned"])
        return session.is_pinned

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    @staticmethod
    def save_user_message(session: CopilotSession, content: str) -> CopilotMessage:
        msg = CopilotMessage.objects.create(
            session=session,
            message_type=CopilotMessageType.USER,
            content=content,
            linked_case_id=session.linked_case_id,
            trace_id=session.trace_id,
            span_id=uuid.uuid4().hex[:16],
        )
        session.last_message_at = timezone.now()
        session.save(update_fields=["last_message_at"])

        APCopilotService._audit(
            AuditEventType.COPILOT_MESSAGE_SENT,
            "CopilotMessage", msg.pk,
            f"User message in copilot session",
            user=session.user,
            session_id=str(session.id),
            case_id=session.linked_case_id,
        )
        return msg

    @staticmethod
    def save_assistant_message(
        session: CopilotSession,
        response_payload: Dict[str, Any],
    ) -> CopilotMessage:
        safe_payload = _json_safe(response_payload)
        msg = CopilotMessage.objects.create(
            session=session,
            message_type=CopilotMessageType.ASSISTANT,
            content=response_payload.get("summary", ""),
            structured_payload_json=safe_payload,
            consulted_agents_json=safe_payload.get("consulted_agents"),
            evidence_payload_json=safe_payload.get("evidence"),
            governance_payload_json=safe_payload.get("governance"),
            linked_case_id=session.linked_case_id,
            token_count=response_payload.get("token_count"),
            trace_id=session.trace_id,
            span_id=uuid.uuid4().hex[:16],
        )
        session.last_message_at = timezone.now()
        session.save(update_fields=["last_message_at"])

        # Auto-generate title from first user message if untitled
        if not session.title:
            first_user = CopilotMessage.objects.filter(
                session=session,
                message_type=CopilotMessageType.USER,
            ).first()
            if first_user:
                session.title = first_user.content[:80]
                session.save(update_fields=["title"])

        APCopilotService._audit(
            AuditEventType.COPILOT_RESPONSE_GENERATED,
            "CopilotMessage", msg.pk,
            "Assistant response generated",
            user=session.user,
            session_id=str(session.id),
            case_id=session.linked_case_id,
        )
        return msg

    # ------------------------------------------------------------------
    # Case context building (read-only)
    # ------------------------------------------------------------------

    @staticmethod
    def build_case_context(case_id: int, user) -> Dict[str, Any]:
        """Gather a comprehensive snapshot of a case for the context panel."""
        from apps.cases.models import APCase
        from apps.reconciliation.models import ReconciliationException

        case = (
            APCase.objects
            .filter(pk=case_id)
            .select_related(
                "invoice", "vendor", "purchase_order",
                "reconciliation_result", "review_assignment",
                "assigned_to",
            )
            .first()
        )
        if not case:
            return {"error": "Case not found"}

        inv = case.invoice
        ctx: Dict[str, Any] = {
            "case": {
                "id": case.pk,
                "case_number": case.case_number,
                "status": case.get_status_display(),
                "status_code": case.status,
                "priority": case.get_priority_display(),
                "processing_path": case.get_processing_path_display(),
                "invoice_type": case.get_invoice_type_display(),
                "current_stage": case.current_stage,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            },
            "invoice": None,
            "vendor": None,
            "purchase_order": None,
            "reconciliation": None,
            "exceptions": [],
            "recommendation": None,
            "review": None,
        }

        if inv:
            ctx["invoice"] = {
                "id": inv.pk,
                "invoice_number": inv.invoice_number,
                "amount": str(inv.total_amount) if inv.total_amount else None,
                "currency": inv.currency,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "status": inv.get_status_display() if hasattr(inv, "get_status_display") else inv.status,
                "extraction_confidence": inv.extraction_confidence,
            }

        if case.vendor:
            ctx["vendor"] = {
                "id": case.vendor.pk,
                "name": case.vendor.name,
                "vendor_code": case.vendor.code,
            }

        if case.purchase_order:
            po = case.purchase_order
            ctx["purchase_order"] = {
                "id": po.pk,
                "po_number": po.po_number,
                "total_amount": str(po.total_amount) if po.total_amount else None,
            }

        if case.reconciliation_result:
            rr = case.reconciliation_result
            ctx["reconciliation"] = {
                "id": rr.pk,
                "match_status": rr.get_match_status_display(),
                "match_status_code": rr.match_status,
                "reconciliation_mode": getattr(rr, "reconciliation_mode", ""),
                "overall_confidence": rr.deterministic_confidence,
            }
            # Exceptions
            exceptions = ReconciliationException.objects.filter(
                result=rr,
            ).values("exception_type", "severity", "message")
            ctx["exceptions"] = list(exceptions)

            # Latest recommendation
            from apps.agents.models import AgentRecommendation
            rec = AgentRecommendation.objects.filter(
                agent_run__reconciliation_result=rr,
            ).order_by("-created_at").first()
            if rec:
                ctx["recommendation"] = {
                    "type": rec.recommendation_type,
                    "text": rec.reasoning,
                    "confidence": rec.confidence,
                    "accepted": rec.accepted,
                }

        if case.review_assignment:
            ra = case.review_assignment
            ctx["review"] = {
                "id": ra.pk,
                "status": ra.get_status_display(),
                "assigned_to": ra.assigned_to.email if ra.assigned_to else None,
            }

        APCopilotService._audit(
            AuditEventType.COPILOT_CASE_CONTEXT_LOADED,
            "APCase", case_id,
            f"Case context loaded for copilot",
            user=user, case_id=case_id,
        )
        return ctx

    @staticmethod
    def build_case_evidence(case_id: int, user) -> Dict[str, Any]:
        """Build evidence cards for a case."""
        from apps.cases.models import APCase, APCaseArtifact, APCaseDecision

        case = APCase.objects.filter(pk=case_id).select_related(
            "invoice", "purchase_order", "reconciliation_result",
        ).first()
        if not case:
            return {"error": "Case not found"}

        evidence: List[Dict[str, Any]] = []

        # Invoice evidence
        if case.invoice:
            inv = case.invoice
            evidence.append({
                "type": "invoice",
                "label": f"Invoice {inv.invoice_number}",
                "data": {
                    "amount": str(inv.total_amount) if inv.total_amount else None,
                    "currency": inv.currency,
                    "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                    "confidence": inv.extraction_confidence,
                },
            })

        # PO evidence
        if case.purchase_order:
            po = case.purchase_order
            evidence.append({
                "type": "purchase_order",
                "label": f"PO {po.po_number}",
                "data": {
                    "amount": str(po.total_amount) if po.total_amount else None,
                    "status": po.status,
                },
            })

        # GRN evidence
        if case.purchase_order:
            from apps.documents.models import GoodsReceiptNote
            grns = GoodsReceiptNote.objects.filter(
                purchase_order=case.purchase_order,
            ).values("grn_number", "receipt_date", "total_amount")[:5]
            for g in grns:
                evidence.append({
                    "type": "grn",
                    "label": f"GRN {g['grn_number']}",
                    "data": {
                        "amount": str(g["total_amount"]) if g["total_amount"] else None,
                        "date": g["receipt_date"].isoformat() if g["receipt_date"] else None,
                    },
                })

        # Reconciliation exceptions as evidence
        if case.reconciliation_result:
            from apps.reconciliation.models import ReconciliationException
            exceptions = ReconciliationException.objects.filter(
                result=case.reconciliation_result,
            ).values("exception_type", "severity", "message")
            for exc in exceptions:
                evidence.append({
                    "type": "exception",
                    "label": exc["exception_type"],
                    "data": {
                        "severity": exc["severity"],
                        "message": exc["message"],
                    },
                })

        # Decisions
        decisions = APCaseDecision.objects.filter(case_id=case_id).order_by("-created_at")[:5]
        for d in decisions:
            evidence.append({
                "type": "decision",
                "label": d.get_decision_type_display(),
                "data": {
                    "value": d.decision_value,
                    "confidence": d.confidence,
                    "rationale": d.rationale,
                    "source": d.decision_source,
                },
            })

        return {"case_id": case_id, "evidence": evidence}

    @staticmethod
    def build_case_governance(case_id: int, user) -> Dict[str, Any]:
        """Build governance / traceability view for a case.

        Only ADMIN and AUDITOR see the full governance block;
        other roles receive a filtered subset.
        """
        primary_role = getattr(user, "role", "")
        has_governance = primary_role in GOVERNANCE_ROLES
        if hasattr(user, "has_permission"):
            has_governance = has_governance or user.has_permission("governance.view")

        if not has_governance:
            APCopilotService._audit(
                AuditEventType.COPILOT_UNAUTHORIZED_GOVERNANCE_REQUEST,
                "APCase", case_id,
                f"Governance context denied for role {primary_role}",
                user=user, case_id=case_id,
            )
            return {
                "case_id": case_id,
                "permitted": False,
                "message": "Governance details require elevated permissions.",
            }

        from apps.auditlog.models import AuditEvent as AuditEventModel

        events = AuditEventModel.objects.filter(
            case_id=case_id,
        ).order_by("-created_at").values(
            "event_type", "event_description", "actor_email",
            "actor_primary_role", "permission_checked", "permission_source",
            "access_granted", "trace_id", "span_id", "created_at",
            "performed_by_agent",
        )[:30]

        APCopilotService._audit(
            AuditEventType.COPILOT_GOVERNANCE_CONTEXT_VIEWED,
            "APCase", case_id,
            "Governance context viewed in copilot",
            user=user, case_id=case_id,
        )
        return {
            "case_id": case_id,
            "permitted": True,
            "events": list(events),
        }

    @staticmethod
    def build_case_timeline(case_id: int, user) -> Dict[str, Any]:
        """Delegate to existing CaseTimelineService."""
        try:
            from apps.auditlog.timeline_service import CaseTimelineService
            from apps.cases.models import APCase
            case = APCase.objects.filter(pk=case_id).select_related("invoice").first()
            if not case or not case.invoice:
                return {"case_id": case_id, "timeline": []}
            timeline = CaseTimelineService.get_case_timeline(case.invoice.pk)
            return {"case_id": case_id, "timeline": timeline}
        except Exception as exc:
            logger.warning("Timeline build failed for case %s: %s", case_id, exc)
            return {"case_id": case_id, "timeline": [], "error": str(exc)}

    # ------------------------------------------------------------------
    # Answer generation (read-only, no mutations)
    # ------------------------------------------------------------------

    @staticmethod
    def answer_question(
        user,
        message: str,
        session: CopilotSession,
    ) -> Dict[str, Any]:
        """Generate a structured copilot response.

        This is a **read-only** operation.  The copilot synthesises data from
        existing models and produces guidance — it never modifies records.

        In the current version the response is assembled deterministically
        from database queries.  A future version will route through an LLM
        with tool access for richer analysis.
        """
        case_id = session.linked_case_id
        primary_role = getattr(user, "role", "")

        # Gather context
        context_data = {}
        evidence_data: List[Dict[str, Any]] = []
        consulted_agents: List[str] = []
        governance_data: Dict[str, Any] = {}
        recommendation_data: Optional[Dict[str, Any]] = None

        if case_id:
            context_data = APCopilotService.build_case_context(case_id, user)
            ev = APCopilotService.build_case_evidence(case_id, user)
            evidence_data = ev.get("evidence", [])

            # Gather agent runs for this case
            from apps.agents.models import AgentRun
            from apps.cases.models import APCase
            case = APCase.objects.filter(pk=case_id).first()
            if case and case.reconciliation_result_id:
                runs = AgentRun.objects.filter(
                    reconciliation_result_id=case.reconciliation_result_id,
                ).values_list("agent_type", flat=True).distinct()
                consulted_agents = list(runs)

            # Governance for privileged roles
            if primary_role in GOVERNANCE_ROLES:
                governance_data = APCopilotService.build_case_governance(case_id, user)

            # Recommendation
            if context_data.get("recommendation"):
                recommendation_data = {
                    "text": context_data["recommendation"].get("text", ""),
                    "confidence": context_data["recommendation"].get("confidence"),
                    "read_only": True,
                }

            # Build case-specific summary
            summary = APCopilotService._build_summary(
                message, context_data, evidence_data, primary_role,
            )
            follow_ups = APCopilotService.build_follow_up_prompts(
                user, context_data,
            )
        else:
            # System-wide / general query (no case linked)
            system_data = APCopilotService._build_system_context(user)
            summary = APCopilotService._build_system_summary(
                message, system_data, primary_role,
            )
            evidence_data = system_data.get("evidence", [])
            follow_ups = system_data.get("follow_ups", [])

        return {
            "summary": summary,
            "evidence": evidence_data,
            "consulted_agents": consulted_agents,
            "recommendation": recommendation_data,
            "governance": governance_data if governance_data.get("permitted") else {},
            "follow_up_prompts": follow_ups,
        }

    @staticmethod
    def get_suggestions(user) -> List[str]:
        """Return role-based suggested prompts."""
        role = getattr(user, "role", "")
        prompts = ROLE_PROMPTS.get(role, ROLE_PROMPTS.get("AP_PROCESSOR", []))
        return prompts

    @staticmethod
    def build_follow_up_prompts(
        user,
        context_data: Dict[str, Any],
    ) -> List[str]:
        role = getattr(user, "role", "")
        prompts: List[str] = []

        case = context_data.get("case", {})
        if case:
            if context_data.get("exceptions"):
                prompts.append("Explain the exceptions on this case.")
            if context_data.get("recommendation"):
                prompts.append("Why was this recommendation made?")
            if context_data.get("reconciliation"):
                prompts.append("Break down the reconciliation result line by line.")
            if role in GOVERNANCE_ROLES:
                prompts.append("Show the governance audit trail.")
            prompts.append("What should happen next?")
        else:
            prompts = APCopilotService.get_suggestions(user)

        return prompts[:5]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(
        question: str,
        context: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        role: str,
    ) -> str:
        """Build a human-readable summary based on available data.

        This deterministic summariser will be replaced by LLM calls in a
        future release.
        """
        case = context.get("case", {})
        if not case:
            return (
                "No case is currently linked to this session. "
                "You can ask general questions or link a case from the sidebar."
            )

        parts: List[str] = []
        parts.append(
            f"**Case {case.get('case_number', 'N/A')}** is currently "
            f"**{case.get('status', 'unknown')}** "
            f"(priority: {case.get('priority', 'N/A')}, "
            f"path: {case.get('processing_path', 'N/A')})."
        )

        inv = context.get("invoice")
        if inv:
            parts.append(
                f"Invoice **{inv.get('invoice_number', 'N/A')}** "
                f"for {inv.get('currency', '')} {inv.get('amount', 'N/A')} "
                f"(confidence: {APCopilotService._pct(inv.get('extraction_confidence'))})."
            )

        recon = context.get("reconciliation")
        if recon:
            parts.append(
                f"Reconciliation result: **{recon.get('match_status', 'N/A')}** "
                f"(mode: {recon.get('reconciliation_mode', 'N/A')}, "
                f"confidence: {APCopilotService._pct(recon.get('overall_confidence'))})."
            )

        exceptions = context.get("exceptions", [])
        if exceptions:
            types = ", ".join(e["exception_type"] for e in exceptions[:4])
            parts.append(f"Exceptions: {types}.")

        rec = context.get("recommendation")
        if rec:
            parts.append(
                f"Agent recommendation: *{rec.get('text', 'N/A')}* "
                f"(confidence: {APCopilotService._pct(rec.get('confidence'))}).  "
                f"This is read-only guidance."
            )

        review = context.get("review")
        if review:
            parts.append(
                f"Review status: **{review.get('status', 'N/A')}** "
                f"(assigned to {review.get('assigned_to', 'unassigned')})."
            )

        return "\n\n".join(parts)

    @staticmethod
    def _build_system_context(user) -> Dict[str, Any]:
        """Gather system-wide aggregate data for general queries."""
        from django.db.models import Count, Q, Sum

        from apps.cases.models import APCase
        from apps.documents.models import Invoice
        from apps.reconciliation.models import ReconciliationResult
        from apps.reviews.models import ReviewAssignment

        # Reconciliation breakdown
        match_counts = dict(
            ReconciliationResult.objects.values_list("match_status")
            .annotate(c=Count("id"))
        )
        total_results = sum(match_counts.values())

        # Case status breakdown
        case_counts = dict(
            APCase.objects.values_list("status")
            .annotate(c=Count("id"))
        )
        total_cases = sum(case_counts.values())

        # Invoice stats
        total_invoices = Invoice.objects.count()
        pending_invoices = Invoice.objects.filter(
            status__in=["UPLOADED", "EXTRACTION_IN_PROGRESS", "EXTRACTED"],
        ).count()

        # Review stats
        pending_reviews = ReviewAssignment.objects.filter(
            status__in=["PENDING", "ASSIGNED", "IN_REVIEW"],
        ).count()

        # Cases needing attention
        action_statuses = [
            "READY_FOR_REVIEW", "ESCALATED", "EXCEPTION_ANALYSIS_IN_PROGRESS",
        ]
        cases_needing_action = APCase.objects.filter(
            status__in=action_statuses,
        ).count()

        # Recent high-priority cases
        recent_cases = list(
            APCase.objects.filter(status__in=action_statuses)
            .order_by("-created_at")[:5]
            .values("id", "case_number", "status", "priority")
        )

        # Build evidence cards
        evidence: List[Dict[str, Any]] = []
        evidence.append({
            "type": "reconciliation_overview",
            "title": "Reconciliation Overview",
            "data": {
                "total": total_results,
                "matched": match_counts.get("MATCHED", 0),
                "partial_match": match_counts.get("PARTIAL_MATCH", 0),
                "unmatched": match_counts.get("UNMATCHED", 0),
                "requires_review": match_counts.get("REQUIRES_REVIEW", 0),
            },
        })
        evidence.append({
            "type": "case_overview",
            "title": "Case Pipeline",
            "data": {
                "total": total_cases,
                "needing_action": cases_needing_action,
                "closed": case_counts.get("CLOSED", 0),
                "in_review": case_counts.get("IN_REVIEW", 0),
                "escalated": case_counts.get("ESCALATED", 0),
            },
        })
        evidence.append({
            "type": "workload_summary",
            "title": "Pending Workload",
            "data": {
                "pending_invoices": pending_invoices,
                "pending_reviews": pending_reviews,
                "cases_needing_action": cases_needing_action,
            },
        })
        if recent_cases:
            evidence.append({
                "type": "attention_cases",
                "title": "Cases Needing Attention",
                "data": {"cases": recent_cases},
            })

        follow_ups = [
            "Which cases are escalated?",
            "Show me unmatched reconciliation results.",
            "What cases are pending review?",
            "Give me a breakdown by vendor.",
            "What are the most common exception types?",
        ]

        return {
            "match_counts": match_counts,
            "case_counts": case_counts,
            "total_results": total_results,
            "total_cases": total_cases,
            "total_invoices": total_invoices,
            "pending_invoices": pending_invoices,
            "pending_reviews": pending_reviews,
            "cases_needing_action": cases_needing_action,
            "recent_cases": recent_cases,
            "evidence": evidence,
            "follow_ups": follow_ups,
        }

    @staticmethod
    def _build_system_summary(
        question: str,
        system_data: Dict[str, Any],
        role: str,
    ) -> str:
        """Build a system-wide summary from aggregate data."""
        mc = system_data.get("match_counts", {})
        total_r = system_data.get("total_results", 0)
        total_c = system_data.get("total_cases", 0)
        total_i = system_data.get("total_invoices", 0)
        pending_rev = system_data.get("pending_reviews", 0)
        needing_action = system_data.get("cases_needing_action", 0)
        pending_inv = system_data.get("pending_invoices", 0)

        parts: List[str] = []
        parts.append("**System-Wide Reconciliation Summary**")

        parts.append(
            f"Across **{total_i} invoices** and **{total_c} cases**, "
            f"there are **{total_r} reconciliation results**:"
        )

        matched = mc.get("MATCHED", 0)
        partial = mc.get("PARTIAL_MATCH", 0)
        unmatched = mc.get("UNMATCHED", 0)
        requires = mc.get("REQUIRES_REVIEW", 0)

        match_pct = f"{matched / total_r * 100:.0f}%" if total_r else "0%"
        lines = [
            f"- **Matched**: {matched} ({match_pct})",
            f"- **Partial Match**: {partial}",
            f"- **Unmatched**: {unmatched}",
            f"- **Requires Review**: {requires}",
        ]
        parts.append("\n".join(lines))

        # Workload
        workload_items = []
        if needing_action:
            workload_items.append(f"**{needing_action}** cases needing action")
        if pending_rev:
            workload_items.append(f"**{pending_rev}** reviews pending")
        if pending_inv:
            workload_items.append(f"**{pending_inv}** invoices in pipeline")
        if workload_items:
            parts.append("**Current Workload:** " + " · ".join(workload_items))

        # Highlight attention items
        recent = system_data.get("recent_cases", [])
        if recent:
            case_lines = []
            for c in recent[:3]:
                case_lines.append(
                    f"- {c['case_number']} — {c['status']} "
                    f"(priority: {c.get('priority', 'N/A')})"
                )
            parts.append("**Cases Needing Attention:**\n" + "\n".join(case_lines))

        return "\n\n".join(parts)

    @staticmethod
    def _pct(value) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value) * 100:.0f}%"
        except (ValueError, TypeError):
            return str(value)

    @staticmethod
    def _audit(
        event_type: str,
        entity_type: str,
        entity_id,
        description: str,
        user=None,
        case_id=None,
        session_id: str = "",
        **kwargs,
    ):
        """Convenience wrapper for audit logging."""
        try:
            metadata = {"session_id": session_id} if session_id else {}
            metadata.update(kwargs)
            # entity_id must be an int; for UUID-based entities use 0 and store id in metadata
            try:
                eid = int(entity_id)
            except (ValueError, TypeError):
                metadata["entity_uuid"] = str(entity_id)
                eid = 0
            AuditService.log_event(
                entity_type=entity_type,
                entity_id=eid,
                event_type=event_type,
                description=description,
                user=user,
                metadata=metadata,
                case_id=case_id,
            )
        except Exception:
            logger.exception("Copilot audit event failed: %s", event_type)
