"""
AP Copilot Service — read-only AI copilot for AP case investigation.

Synthesises data across cases, invoices, POs, GRNs, reconciliation results,
exceptions, recommendations, reviews, and governance metadata.  Every operation
respects RBAC and emits audit events.
"""
from __future__ import annotations

import logging
import re
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
    UserRole,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Small-talk / greeting detection
# ---------------------------------------------------------------------------
import re as _re

_SMALL_TALK_PATTERNS: List[_re.Pattern] = [
    _re.compile(r"^(hi|hey|hello|howdy|yo|hola|greetings|good\s*(morning|afternoon|evening|day))\b", _re.I),
    _re.compile(r"^(how are you|how's it going|what's up|whats up|sup|how do you do)\b", _re.I),
    _re.compile(r"^(thanks|thank you|thx|cheers|ty|much appreciated)\b", _re.I),
    _re.compile(r"^(bye|goodbye|see you|later|cya|take care|good night)\b", _re.I),
    _re.compile(r"^(who are you|what are you|what can you do|what is your name|your name)\b", _re.I),
    _re.compile(r"^(help|help me)$", _re.I),
    _re.compile(r"^(ok|okay|cool|nice|great|awesome|got it|understood|sure)$", _re.I),
    _re.compile(r"^(lol|haha|hehe)$", _re.I),
    _re.compile(r"^(please|yes|no|yep|nope|yeah|nah)$", _re.I),
]

_SMALL_TALK_RESPONSES = {
    "greeting": (
        "Hello! I'm the AP Copilot \u2014 your assistant for investigating "
        "invoices, purchase orders, reconciliation results, and cases.\n\n"
        "You can ask me things like:\n"
        "- *What's the status of case X?*\n"
        "- *Show me pending reviews*\n"
        "- *Summarise exceptions across all cases*\n\n"
        "How can I help you today?"
    ),
    "how_are_you": (
        "I'm running well, thanks for asking! "
        "I'm here to help you with AP case investigation. "
        "What would you like to know?"
    ),
    "thanks": (
        "You're welcome! Let me know if there's anything else "
        "I can help you with."
    ),
    "bye": (
        "Goodbye! Feel free to come back any time you need help "
        "with AP investigations."
    ),
    "identity": (
        "I'm the **AP Copilot** \u2014 a read-only assistant for investigating "
        "AP cases, invoices, purchase orders, reconciliation results, "
        "exceptions, and agent activity.\n\n"
        "I can help you understand case statuses, review evidence, "
        "explore vendor data, and trace governance decisions. "
        "Just ask me a question!"
    ),
    "help": (
        "Here are some things I can help with:\n\n"
        "**Case Investigation**\n"
        "- *What is the status of this case?*\n"
        "- *Summarise the exceptions on this case*\n\n"
        "**System Overview**\n"
        "- *Show me pending reviews*\n"
        "- *How are agents performing?*\n"
        "- *Which vendors have the most mismatches?*\n\n"
        "**Reconciliation**\n"
        "- *Give me a reconciliation summary*\n"
        "- *What's the overall match rate?*\n\n"
        "Just type your question and I'll do my best to help!"
    ),
    "acknowledgement": (
        "Got it! Let me know if you have any questions about "
        "invoices, cases, or reconciliation."
    ),
}


def _detect_small_talk(message: str) -> Optional[str]:
    """Return a small-talk category key if *message* is conversational, else None."""
    text = message.strip()
    if not text:
        return "greeting"

    # Only treat short messages as potential small talk (avoid false positives)
    if len(text.split()) > 5:
        return None

    # If the message contains business-related keywords, it's not small talk
    _biz_keywords = (
        "case", "invoice", "vendor", "review", "recon", "match", "exception",
        "agent", "po", "grn", "show", "status", "summary", "list", "report",
        "pending", "approved", "rejected", "escalat",
    )
    low = text.lower()
    if any(kw in low for kw in _biz_keywords):
        return None

    # Check against compiled patterns
    for pattern in _SMALL_TALK_PATTERNS:
        if pattern.search(text):
            break
    else:
        return None  # not small talk

    # Determine sub-category
    if any(low.startswith(w) for w in ("how are", "how's it", "what's up", "whats up", "sup", "how do you do")):
        return "how_are_you"
    if any(low.startswith(w) for w in ("thank", "thx", "cheers", "ty", "much appreciated")):
        return "thanks"
    if any(low.startswith(w) for w in ("bye", "goodbye", "see you", "later", "cya", "take care", "good night")):
        return "bye"
    if any(low.startswith(w) for w in ("who are you", "what are you", "what can you do", "what is your name", "your name")):
        return "identity"
    if low in ("help", "help me"):
        return "help"
    if low in ("ok", "okay", "cool", "nice", "great", "awesome", "got it", "understood", "sure",
               "yes", "no", "yep", "nope", "yeah", "nah", "please",
               "lol", "haha", "hehe"):
        return "acknowledgement"
    return "greeting"


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
    # RBAC data-scoping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_scoped_role(user) -> bool:
        """Return True if user requires data scoping (AP_PROCESSOR with restricted view)."""
        if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
            return False
        from apps.reconciliation.models import ReconciliationConfig
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config and config.ap_processor_sees_all_cases:
            return False
        return True

    @staticmethod
    def _is_reviewer(user) -> bool:
        return getattr(user, "role", None) == UserRole.REVIEWER

    @staticmethod
    def _scoped_cases(user):
        """Return APCase queryset scoped to what *user* may see."""
        from apps.cases.selectors.case_selectors import CaseSelectors
        return CaseSelectors.scope_for_user(
            __import__("apps.cases.models", fromlist=["APCase"]).APCase.objects.filter(is_active=True),
            user,
        )

    @staticmethod
    def _scoped_invoices(user):
        """Return Invoice queryset scoped to what *user* may see."""
        from apps.documents.models import Invoice
        qs = Invoice.objects.all()
        if APCopilotService._is_scoped_role(user):
            qs = qs.filter(document_upload__uploaded_by=user)
        return qs

    @staticmethod
    def _scoped_recon_results(user):
        """Return ReconciliationResult queryset scoped to user's invoices."""
        from apps.reconciliation.models import ReconciliationResult
        qs = ReconciliationResult.objects.all()
        if APCopilotService._is_scoped_role(user):
            qs = qs.filter(invoice__document_upload__uploaded_by=user)
        elif APCopilotService._is_reviewer(user):
            qs = qs.filter(invoice__ap_case__assigned_to=user)
        return qs

    @staticmethod
    def _scoped_exceptions(user):
        """Return ReconciliationException queryset scoped to user's results."""
        from apps.reconciliation.models import ReconciliationException
        qs = ReconciliationException.objects.all()
        if APCopilotService._is_scoped_role(user):
            qs = qs.filter(result__invoice__document_upload__uploaded_by=user)
        elif APCopilotService._is_reviewer(user):
            qs = qs.filter(result__invoice__ap_case__assigned_to=user)
        return qs

    @staticmethod
    def _scoped_reviews(user):
        """Return ReviewAssignment queryset scoped to user."""
        from apps.reviews.models import ReviewAssignment
        qs = ReviewAssignment.objects.all()
        if APCopilotService._is_scoped_role(user):
            qs = qs.filter(reconciliation_result__invoice__document_upload__uploaded_by=user)
        elif APCopilotService._is_reviewer(user):
            qs = qs.filter(assigned_to=user)
        return qs

    @staticmethod
    def _scoped_vendors(user):
        """Return Vendor queryset scoped to user's invoices."""
        from apps.vendors.models import Vendor
        qs = Vendor.objects.filter(is_active=True)
        if APCopilotService._is_scoped_role(user):
            from apps.documents.models import Invoice
            vendor_ids = (
                Invoice.objects.filter(document_upload__uploaded_by=user)
                .exclude(vendor__isnull=True)
                .values_list("vendor_id", flat=True)
                .distinct()
            )
            qs = qs.filter(pk__in=vendor_ids)
        return qs

    @staticmethod
    def _scoped_agent_runs(user):
        """Return AgentRun queryset scoped to user's recon results."""
        from apps.agents.models import AgentRun
        qs = AgentRun.objects.all()
        if APCopilotService._is_scoped_role(user):
            qs = qs.filter(reconciliation_result__invoice__document_upload__uploaded_by=user)
        elif APCopilotService._is_reviewer(user):
            qs = qs.filter(reconciliation_result__invoice__ap_case__assigned_to=user)
        return qs

    @staticmethod
    def _user_can_access_case(user, case_id: int) -> bool:
        """Return True if user is allowed to access the given case."""
        return APCopilotService._scoped_cases(user).filter(pk=case_id).exists()

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
    def search_cases(user, query: str) -> List[Dict[str, Any]]:
        """Search AP cases for linking to a copilot session.

        Searches by case_number, invoice number, vendor name, or PO number.
        Returns at most 15 lightweight results, scoped to user access.
        """
        qs = APCopilotService._scoped_cases(user).select_related(
            "invoice", "vendor",
        )
        if query:
            qs = qs.filter(
                models.Q(case_number__icontains=query)
                | models.Q(invoice__invoice_number__icontains=query)
                | models.Q(vendor__name__icontains=query)
                | models.Q(invoice__po_number__icontains=query)
            )
        qs = qs.order_by("-created_at")[:15]

        results = []
        for c in qs:
            results.append({
                "id": c.pk,
                "case_number": c.case_number,
                "status": c.status,
                "priority": c.priority,
                "invoice_number": getattr(c.invoice, "invoice_number", None),
                "vendor_name": getattr(c.vendor, "name", None),
            })
        return results

    @staticmethod
    def link_case_to_session(
        user, session_id: str, case_id: int,
    ) -> Dict[str, Any]:
        """Link an AP case to an existing copilot session."""
        session = CopilotSession.objects.filter(pk=session_id, user=user).first()
        if not session:
            return {"error": "Session not found"}

        if not APCopilotService._user_can_access_case(user, case_id):
            return {"error": "Case not found"}

        from apps.cases.models import APCase
        case = APCase.objects.filter(pk=case_id, is_active=True).select_related("invoice").first()
        if not case:
            return {"error": "Case not found"}

        session.linked_case = case
        session.linked_invoice = case.invoice
        if not session.title or session.title == "Untitled":
            session.title = f"Investigation: {case.case_number}"
        session.save(update_fields=["linked_case", "linked_invoice", "title", "updated_at"])

        APCopilotService._audit(
            AuditEventType.COPILOT_SESSION_RESUMED,
            "CopilotSession", str(session.id),
            f"Linked case {case.case_number} to copilot session",
            user=user, case_id=case_id,
            session_id=str(session.id),
        )
        return {
            "linked": True,
            "case_id": case.pk,
            "case_number": case.case_number,
            "title": session.title,
        }

    @staticmethod
    def unlink_case_from_session(user, session_id: str) -> Dict[str, Any]:
        """Remove the case link from a copilot session."""
        session = CopilotSession.objects.filter(pk=session_id, user=user).first()
        if not session:
            return {"error": "Session not found"}

        old_case_id = session.linked_case_id
        session.linked_case = None
        session.linked_invoice = None
        session.save(update_fields=["linked_case", "linked_invoice", "updated_at"])

        APCopilotService._audit(
            AuditEventType.COPILOT_SESSION_RESUMED,
            "CopilotSession", str(session.id),
            f"Unlinked case {old_case_id} from copilot session",
            user=user, session_id=str(session.id),
        )
        return {"unlinked": True}

    @staticmethod
    def list_sessions(user, include_archived: bool = False) -> models.QuerySet:
        """Return sessions for this user, newest first."""
        from django.db.models.functions import Coalesce

        qs = CopilotSession.objects.filter(user=user)
        if not include_archived:
            qs = qs.filter(is_archived=False)
        return qs.select_related("linked_case", "linked_invoice").order_by(
            "-is_pinned",
            Coalesce("last_message_at", "created_at").desc(),
        )

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
            try:
                from apps.core.langfuse_client import score_trace
                msg_count = CopilotMessage.objects.filter(
                    session__pk=session_id, session__user=user,
                ).count()
                score_trace(
                    f"copilot-{session_id}",
                    "copilot_session_length",
                    float(msg_count),
                    comment=f"session={session_id} messages={msg_count}",
                )
            except Exception:
                pass
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

        # Ownership check — user must have access to this case
        if not APCopilotService._user_can_access_case(user, case_id):
            return {"error": "Case not found"}

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
            v = case.vendor
            ctx["vendor"] = {
                "id": v.pk,
                "name": v.name,
                "vendor_code": v.code,
                "tax_id": v.tax_id or None,
                "address": v.address or None,
                "country": v.country or None,
                "currency": v.currency or None,
                "payment_terms": v.payment_terms or None,
                "contact_email": v.contact_email or None,
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

        # Validation issues (from VALIDATION_RESULT artifact, e.g. NON_PO cases)
        from apps.cases.models import APCaseArtifact
        val_artifact = (
            APCaseArtifact.objects
            .filter(case=case, artifact_type="VALIDATION_RESULT")
            .order_by("-version", "-created_at")
            .first()
        )
        if val_artifact and isinstance(val_artifact.payload, dict):
            checks = val_artifact.payload.get("checks", {})
            validation_issues = []
            for check_name, check_data in checks.items():
                status = check_data.get("status", "")
                if status in ("FAIL", "WARNING"):
                    validation_issues.append({
                        "check_name": check_name.replace("_", " ").title(),
                        "status": status,
                        "message": check_data.get("message", ""),
                    })
            ctx["validation_issues"] = validation_issues

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

        # Ownership check
        if not APCopilotService._user_can_access_case(user, case_id):
            return {"error": "Case not found"}

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
            ).values("grn_number", "receipt_date", "status")[:5]
            for g in grns:
                evidence.append({
                    "type": "grn",
                    "label": f"GRN {g['grn_number']}",
                    "data": {
                        "status": g["status"] or None,
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
        if not APCopilotService._user_can_access_case(user, case_id):
            return {"case_id": case_id, "permitted": False, "message": "Case not found."}

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
        if not APCopilotService._user_can_access_case(user, case_id):
            return {"case_id": case_id, "timeline": []}
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
        existing models and produces guidance -- it never modifies records.

        In the current version the response is assembled deterministically
        from database queries.  A future version will route through an LLM
        with tool access for richer analysis.
        """
        _lf_span = None
        _topic = "unknown"
        _session_trace_id = getattr(session, "trace_id", None) or f"copilot-{session.pk}"
        try:
            from apps.core.langfuse_client import start_trace
            _lf_span = start_trace(
                _session_trace_id,
                "copilot_answer",
                session_id=f"copilot-{session.pk}",
                metadata={
                    "session_id": str(session.pk),
                    "case_id": session.linked_case_id,
                },
            )
        except Exception:
            pass

        case_id = session.linked_case_id
        primary_role = getattr(user, "role", "")

        # ── Small-talk short-circuit ─────────────────────────────
        small_talk_key = _detect_small_talk(message)
        if small_talk_key is not None:
            _topic = "small_talk"
            response_text = _SMALL_TALK_RESPONSES.get(small_talk_key, _SMALL_TALK_RESPONSES["greeting"])
            follow_ups = APCopilotService.get_suggestions(user)
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span, output={"topic": _topic, "case_id": session.linked_case_id})
            except Exception:
                pass
            return {
                "summary": response_text,
                "evidence": [],
                "consulted_agents": [],
                "recommendation": None,
                "governance": {},
                "follow_up_prompts": follow_ups,
            }

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

            # Build case-specific summary (question-aware)
            summary = APCopilotService._build_summary(
                message, context_data, evidence_data, primary_role,
            )
            topic = APCopilotService._classify_case_question(message)
            follow_ups = APCopilotService.build_follow_up_prompts(
                user, context_data, topic=topic,
            )
        else:
            # System-wide / general query (no case linked)
            # Classify the question and route to the appropriate handler
            topic = APCopilotService._classify_question(message)
            topic_result = APCopilotService._handle_system_topic(
                topic, message, user, primary_role,
            )
            summary = topic_result["summary"]
            evidence_data = topic_result["evidence"]
            follow_ups = topic_result["follow_ups"]

        _topic = topic
        try:
            from apps.core.langfuse_client import end_span
            if _lf_span:
                end_span(_lf_span, output={"topic": _topic, "case_id": session.linked_case_id})
        except Exception:
            pass
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
        topic: str = "overview",
    ) -> List[str]:
        role = getattr(user, "role", "")
        prompts: List[str] = []

        case = context_data.get("case", {})
        if not case:
            return APCopilotService.get_suggestions(user)

        # Topic-aware follow-ups: suggest areas the user hasn't asked about
        topic_follow_ups = {
            "overview": [
                "Tell me about the invoice details.",
                "What exceptions were found?",
                "Show the reconciliation result.",
                "What should happen next?",
            ],
            "invoice": [
                "Show the reconciliation result.",
                "What exceptions were found?",
                "What do the agents recommend?",
            ],
            "reconciliation": [
                "Explain the exceptions on this case.",
                "What do the agents recommend?",
                "What should happen next?",
            ],
            "exceptions": [
                "Break down the reconciliation result line by line.",
                "What do the agents recommend?",
                "What should happen next?",
            ],
            "recommendation": [
                "Explain the exceptions on this case.",
                "What's the current review status?",
                "What should happen next?",
            ],
            "review": [
                "What do the agents recommend?",
                "Explain the exceptions on this case.",
                "Give me a case overview.",
            ],
            "agents": [
                "What do the agents recommend?",
                "Show the reconciliation result.",
                "Explain the exceptions on this case.",
            ],
            "governance": [
                "Give me a case overview.",
                "What should happen next?",
                "What's the current review status?",
            ],
            "next_steps": [
                "Give me a case overview.",
                "Explain the exceptions on this case.",
                "What do the agents recommend?",
            ],
        }
        prompts = list(topic_follow_ups.get(topic, topic_follow_ups["overview"]))

        if role in GOVERNANCE_ROLES and topic != "governance":
            prompts.append("Show the governance audit trail.")

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
        """Build a question-aware summary based on available case data."""
        case = context.get("case", {})
        if not case:
            return (
                "No case is currently linked to this session. "
                "You can ask general questions or link a case from the sidebar."
            )

        case_ref = f"**Case {case.get('case_number', 'N/A')}**"
        topic = APCopilotService._classify_case_question(question)

        if topic == "overview":
            return APCopilotService._case_summary_overview(case_ref, context)
        elif topic == "vendor":
            return APCopilotService._case_summary_vendor(case_ref, context)
        elif topic == "invoice":
            return APCopilotService._case_summary_invoice(case_ref, context)
        elif topic == "reconciliation":
            return APCopilotService._case_summary_reconciliation(case_ref, context)
        elif topic == "exceptions":
            return APCopilotService._case_summary_exceptions(case_ref, context)
        elif topic == "recommendation":
            return APCopilotService._case_summary_recommendation(case_ref, context)
        elif topic == "review":
            return APCopilotService._case_summary_review(case_ref, context)
        elif topic == "agents":
            return APCopilotService._case_summary_agents(case_ref, context)
        elif topic == "governance":
            return APCopilotService._case_summary_governance(case_ref, context, role)
        elif topic == "next_steps":
            return APCopilotService._case_summary_next_steps(case_ref, context, role)
        else:
            return APCopilotService._case_summary_overview(case_ref, context)

    # Case-question classifier keywords
    _CASE_TOPIC_PATTERNS = [
        ("vendor", [
            "vendor", "supplier", "payment terms", "tax id",
            "contact email", "vendor code", "vendor detail",
        ]),
        ("invoice", [
            "invoice", "amount", "extraction",
            "confidence", "currency",
        ]),
        ("reconciliation", [
            "reconciliation", "recon", "match", "mismatch", "tolerance",
            "two-way", "three-way", "2-way", "3-way", "match status",
            "line by line", "line item",
        ]),
        ("exceptions", [
            "exception", "error", "discrepancy", "difference",
            "price mismatch", "quantity mismatch", "why",
            "validation", "issue", "fail", "warning",
        ]),
        ("recommendation", [
            "recommendation", "suggest", "what should",
            "agent recommend", "advice", "guidance",
        ]),
        ("review", [
            "review", "reviewer", "assigned", "approval",
            "approved", "rejected", "decision",
        ]),
        ("agents", [
            "agent", "agent run", "which agent", "agent performance",
            "tool call", "agent type",
        ]),
        ("governance", [
            "governance", "audit", "trace", "rbac", "permission",
            "compliance", "who accessed",
        ]),
        ("next_steps", [
            "next step", "what next", "what should happen",
            "action needed", "todo", "to do",
        ]),
    ]

    @staticmethod
    def _classify_case_question(question: str) -> str:
        """Classify a case-specific question into a sub-topic."""
        q = question.lower()
        for topic, keywords in APCopilotService._CASE_TOPIC_PATTERNS:
            if any(kw in q for kw in keywords):
                return topic
        return "overview"

    @staticmethod
    def _case_summary_overview(case_ref: str, ctx: Dict) -> str:
        case = ctx.get("case", {})
        parts = [
            f"{case_ref} is currently **{case.get('status', 'unknown')}** "
            f"(priority: {case.get('priority', 'N/A')}, "
            f"path: {case.get('processing_path', 'N/A')})."
        ]
        inv = ctx.get("invoice")
        if inv:
            parts.append(
                f"Invoice **{inv.get('invoice_number', 'N/A')}** "
                f"for {inv.get('currency', '')} {inv.get('amount', 'N/A')}."
            )
        recon = ctx.get("reconciliation")
        if recon:
            parts.append(
                f"Reconciliation: **{recon.get('match_status', 'N/A')}** "
                f"({recon.get('reconciliation_mode', 'N/A')})."
            )
        exc = ctx.get("exceptions", [])
        if exc:
            parts.append(f"Exceptions: {len(exc)} found.")
        val = ctx.get("validation_issues", [])
        if val:
            parts.append(f"Validation issues: {len(val)} found.")
        rec = ctx.get("recommendation")
        if rec:
            parts.append(f"Recommendation: *{rec.get('text', 'N/A')}*")
        review = ctx.get("review")
        if review:
            parts.append(f"Review: **{review.get('status', 'N/A')}**.")
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_vendor(case_ref: str, ctx: Dict) -> str:
        vendor = ctx.get("vendor")
        if not vendor:
            return f"{case_ref} has no vendor linked yet."
        parts = [f"**Vendor Details** for {case_ref}"]
        parts.append(
            f"- **Name**: {vendor.get('name', 'N/A')}\n"
            f"- **Code**: {vendor.get('vendor_code', 'N/A')}\n"
            f"- **Country**: {vendor.get('country', 'N/A')}\n"
            f"- **Currency**: {vendor.get('currency', 'N/A')}\n"
            f"- **Payment Terms**: {vendor.get('payment_terms', 'N/A')}\n"
            f"- **Tax ID**: {vendor.get('tax_id', 'N/A')}\n"
            f"- **Contact**: {vendor.get('contact_email', 'N/A')}\n"
            f"- **Address**: {vendor.get('address', 'N/A')}"
        )
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_invoice(case_ref: str, ctx: Dict) -> str:
        inv = ctx.get("invoice")
        if not inv:
            return f"{case_ref} has no invoice data available."
        vendor = ctx.get("vendor", {})
        vendor_name = vendor.get("name") or inv.get("vendor_name") or "Unknown Vendor"
        parts = [f"**Invoice Details** for {case_ref}"]
        parts.append(
            f"Invoice **{inv.get('invoice_number', 'N/A')}** was submitted by "
            f"**{vendor_name}**."
        )
        parts.append(
            f"- **Amount**: {inv.get('currency', '')} {inv.get('amount', 'N/A')}\n"
            f"- **Date**: {inv.get('invoice_date', 'N/A')}\n"
            f"- **PO Reference**: {inv.get('po_number', 'N/A')}\n"
            f"- **Status**: {inv.get('status', 'N/A')}\n"
            f"- **Extraction Confidence**: {APCopilotService._pct(inv.get('extraction_confidence'))}"
        )
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_reconciliation(case_ref: str, ctx: Dict) -> str:
        recon = ctx.get("reconciliation")
        if not recon:
            return f"{case_ref} has no reconciliation results yet."
        parts = [f"**Reconciliation Details** for {case_ref}"]
        parts.append(
            f"- **Match Status**: {recon.get('match_status', 'N/A')}\n"
            f"- **Mode**: {recon.get('reconciliation_mode', 'N/A')}\n"
            f"- **Overall Confidence**: {APCopilotService._pct(recon.get('overall_confidence'))}\n"
            f"- **Mode Resolved By**: {recon.get('mode_resolved_by', 'N/A')}"
        )
        # Line-level details if available
        lines = recon.get("line_results", [])
        if lines:
            line_parts = []
            for ln in lines[:10]:
                status = ln.get("match_status", "N/A")
                desc = ln.get("description", ln.get("item_description", ""))
                line_parts.append(f"- Line: {desc[:40]} — **{status}**")
            parts.append("**Line Results:**\n" + "\n".join(line_parts))
        exc = ctx.get("exceptions", [])
        if exc:
            parts.append(f"This reconciliation raised **{len(exc)}** exception(s).")
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_exceptions(case_ref: str, ctx: Dict) -> str:
        exceptions = ctx.get("exceptions", [])
        validation_issues = ctx.get("validation_issues", [])
        if not exceptions and not validation_issues:
            return f"{case_ref} has no exceptions recorded."

        parts = [f"**Exception Analysis** for {case_ref}"]

        if exceptions:
            parts.append(f"There are **{len(exceptions)}** reconciliation exception(s):")
            for e in exceptions:
                severity = e.get("severity", "N/A")
                etype = e.get("exception_type", "N/A")
                msg = e.get("message", "") or e.get("description", "")
                field = e.get("field_name", "")
                expected = e.get("expected_value", "")
                actual = e.get("actual_value", "")
                line = f"- **{etype}** ({severity})"
                if field:
                    line += f": field `{field}`"
                if expected and actual:
                    line += f" — expected **{expected}**, got **{actual}**"
                if msg:
                    line += f"\n  {msg}"
                parts.append(line)

        if validation_issues:
            parts.append(f"There are **{len(validation_issues)}** validation issue(s):")
            for v in validation_issues:
                status = v.get("status", "N/A")
                name = v.get("check_name", "N/A")
                msg = v.get("message", "")
                line = f"- **{name}** ({status})"
                if msg:
                    line += f": {msg}"
                parts.append(line)

        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_recommendation(case_ref: str, ctx: Dict) -> str:
        rec = ctx.get("recommendation")
        if not rec:
            return f"{case_ref} has no agent recommendation yet."
        parts = [f"**Agent Recommendation** for {case_ref}"]
        parts.append(
            f"*{rec.get('text', 'N/A')}*\n\n"
            f"- **Confidence**: {APCopilotService._pct(rec.get('confidence'))}\n"
            f"- **Type**: {rec.get('recommendation_type', 'N/A')}\n"
            f"- **Status**: {rec.get('status', 'N/A')}"
        )
        parts.append("This is read-only guidance — the copilot does not take action.")
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_review(case_ref: str, ctx: Dict) -> str:
        review = ctx.get("review")
        if not review:
            return f"{case_ref} has no review assignment."
        parts = [f"**Review Status** for {case_ref}"]
        parts.append(
            f"- **Status**: {review.get('status', 'N/A')}\n"
            f"- **Assigned To**: {review.get('assigned_to', 'Unassigned')}\n"
            f"- **Priority**: {review.get('priority', 'N/A')}"
        )
        decision = review.get("decision")
        if decision:
            parts.append(
                f"Decision: **{decision.get('outcome', 'N/A')}** "
                f"by {decision.get('decided_by', 'N/A')}."
            )
        comments = review.get("comments", [])
        if comments:
            comment_lines = [f"- {c.get('user', '?')}: {c.get('text', '')}" for c in comments[:5]]
            parts.append("**Review Comments:**\n" + "\n".join(comment_lines))
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_agents(case_ref: str, ctx: Dict) -> str:
        from apps.agents.models import AgentRun
        from apps.cases.models import APCase

        case_data = ctx.get("case", {})
        case_id = case_data.get("id")
        if not case_id:
            return f"{case_ref} — no agent data available."

        case_obj = APCase.objects.filter(pk=case_id).first()
        if not case_obj or not case_obj.reconciliation_result_id:
            return f"{case_ref} has no agent runs linked."

        runs = list(
            AgentRun.objects.filter(
                reconciliation_result_id=case_obj.reconciliation_result_id,
            ).values("agent_type", "status", "duration_ms", "total_tokens")
            .order_by("created_at")
        )
        if not runs:
            return f"{case_ref} has no agent runs."

        parts = [f"**Agent Activity** for {case_ref}"]
        parts.append(f"**{len(runs)}** agent run(s) were executed for this case:")
        for r in runs:
            dur = f"{r['duration_ms']}ms" if r.get("duration_ms") else "N/A"
            tokens = r.get("total_tokens") or "N/A"
            parts.append(
                f"- **{r['agent_type']}**: {r['status']} "
                f"(duration: {dur}, tokens: {tokens})"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_governance(case_ref: str, ctx: Dict, role: str) -> str:
        if role not in GOVERNANCE_ROLES:
            return f"Governance details for {case_ref} require ADMIN or AUDITOR role."
        # Pull from timeline if available
        case_data = ctx.get("case", {})
        parts = [f"**Governance & Audit Trail** for {case_ref}"]
        parts.append(
            f"Case status: **{case_data.get('status', 'N/A')}**, "
            f"processing path: **{case_data.get('processing_path', 'N/A')}**."
        )
        recon = ctx.get("reconciliation")
        if recon:
            parts.append(
                f"Reconciliation mode **{recon.get('reconciliation_mode', 'N/A')}** "
                f"resolved by: {recon.get('mode_resolved_by', 'N/A')}."
            )
        rec = ctx.get("recommendation")
        if rec:
            parts.append(
                f"Recommendation status: **{rec.get('status', 'N/A')}** "
                f"(type: {rec.get('recommendation_type', 'N/A')})."
            )
        review = ctx.get("review")
        if review:
            parts.append(f"Review: **{review.get('status', 'N/A')}** — assigned to {review.get('assigned_to', 'N/A')}.")
        parts.append("Use the governance dashboard for full audit event details and RBAC trace.")
        return "\n\n".join(parts)

    @staticmethod
    def _case_summary_next_steps(case_ref: str, ctx: Dict, role: str) -> str:
        case = ctx.get("case", {})
        status = case.get("status", "")
        parts = [f"**Recommended Next Steps** for {case_ref}"]

        recon = ctx.get("reconciliation", {})
        match_status = recon.get("match_status", "")
        review = ctx.get("review")
        exceptions = ctx.get("exceptions", [])
        rec = ctx.get("recommendation")

        steps = []
        if match_status == "MATCHED":
            steps.append("This case is fully matched. No further action needed — it can be closed.")
        elif match_status == "PARTIAL_MATCH":
            steps.append("Review the partial match details and exceptions to determine if differences are within tolerance.")
            if rec:
                steps.append(f"Consider the agent recommendation: *{rec.get('text', '')}*")
        elif match_status == "UNMATCHED":
            steps.append("Investigate why the invoice could not be matched to a PO/GRN.")
            if exceptions:
                steps.append(f"Start by reviewing the {len(exceptions)} exception(s).")
        elif match_status == "REQUIRES_REVIEW":
            steps.append("This case requires manual review before it can proceed.")

        if review:
            rev_status = review.get("status", "")
            if rev_status in ("PENDING", "ASSIGNED"):
                steps.append("A reviewer needs to pick up and complete the review.")
            elif rev_status == "IN_REVIEW":
                steps.append("Review is in progress — await the reviewer's decision.")
            elif rev_status == "APPROVED":
                steps.append("Review was approved. Case can proceed to closure.")
            elif rev_status == "REJECTED":
                steps.append("Review was rejected. The invoice may need reprocessing or escalation.")

        if status == "ESCALATED":
            steps.append("This case has been escalated and needs attention from a senior reviewer or finance manager.")

        if not steps:
            steps.append("Review the case details and determine the appropriate action.")

        for i, step in enumerate(steps, 1):
            parts.append(f"{i}. {step}")

        return "\n\n".join(parts)

    @staticmethod
    def _build_system_context(user) -> Dict[str, Any]:
        """Gather system-wide aggregate data for general queries (scoped to user access)."""
        from django.db.models import Count, Q, Sum

        # Use scoped querysets
        recon_qs = APCopilotService._scoped_recon_results(user)
        case_qs = APCopilotService._scoped_cases(user)
        invoice_qs = APCopilotService._scoped_invoices(user)
        review_qs = APCopilotService._scoped_reviews(user)

        # Reconciliation breakdown
        match_counts = dict(
            recon_qs.values_list("match_status")
            .annotate(c=Count("id"))
        )
        total_results = sum(match_counts.values())

        # Case status breakdown
        case_counts = dict(
            case_qs.values_list("status")
            .annotate(c=Count("id"))
        )
        total_cases = sum(case_counts.values())

        # Invoice stats
        total_invoices = invoice_qs.count()
        pending_invoices = invoice_qs.filter(
            status__in=["UPLOADED", "EXTRACTION_IN_PROGRESS", "EXTRACTED"],
        ).count()

        # Review stats
        pending_reviews = review_qs.filter(
            status__in=["PENDING", "ASSIGNED", "IN_REVIEW"],
        ).count()

        # Cases needing attention
        action_statuses = [
            "READY_FOR_REVIEW", "ESCALATED", "EXCEPTION_ANALYSIS_IN_PROGRESS",
        ]
        cases_needing_action = case_qs.filter(
            status__in=action_statuses,
        ).count()

        # Recent high-priority cases
        recent_cases = list(
            case_qs.filter(status__in=action_statuses)
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

    # ------------------------------------------------------------------
    # Question classification & topic-specific handlers
    # ------------------------------------------------------------------

    # Keyword → topic mapping. Order matters: first match wins.
    _TOPIC_PATTERNS = [
        ("greeting", [
            "hi", "hello", "hey", "good morning", "good afternoon",
            "good evening", "howdy", "greetings", "yo", "sup",
            "what's up", "whats up",
        ]),
        ("thanks", [
            "thank", "thanks", "thank you", "thx", "cheers",
            "appreciate", "great job", "well done", "nice",
        ]),
        ("help", [
            "help", "what can you do", "how do i", "how to",
            "what do you", "capabilities", "features",
            "guide me", "assist", "support",
        ]),
        ("agent_performance", [
            "agent performance", "agent metric", "agent stats",
            "agent run", "agent success", "agent fail",
            "how are agents", "agent overview",
        ]),
        ("exceptions", [
            "exception", "mismatch type", "common exception",
            "exception breakdown", "exception types",
        ]),
        ("reviews", [
            "review", "pending review", "review status",
            "awaiting review", "review assignment", "reviewer",
        ]),
        ("vendors", [
            "vendor", "supplier", "vendor breakdown",
            "vendor performance", "top vendor",
        ]),
        ("invoices", [
            "invoice", "uploaded invoice", "invoice pipeline",
            "invoice status", "extraction",
        ]),
        ("cases", [
            "case", "escalated", "cases needing", "case status",
            "case pipeline", "open case", "closed case",
        ]),
        ("reconciliation", [
            "reconciliation", "recon", "match", "unmatched",
            "partial match", "match rate", "match status",
        ]),
    ]

    # Topics where short keywords need word-boundary matching to avoid
    # false positives (e.g. "hi" matching inside "think").
    _WORD_BOUNDARY_TOPICS = {"greeting", "thanks", "help"}

    @staticmethod
    def _classify_question(question: str) -> str:
        """Classify a free-text question into a topic."""
        q = question.lower().strip()
        for topic, keywords in APCopilotService._TOPIC_PATTERNS:
            use_boundary = topic in APCopilotService._WORD_BOUNDARY_TOPICS
            for kw in keywords:
                if use_boundary:
                    if re.search(r'\b' + re.escape(kw) + r'\b', q):
                        return topic
                else:
                    if kw in q:
                        return topic
        return "reconciliation"  # default topic

    @staticmethod
    def _handle_system_topic(
        topic: str,
        question: str,
        user,
        role: str,
    ) -> Dict[str, Any]:
        """Route to the correct topic handler and return structured result."""
        handlers = {
            "greeting": APCopilotService._topic_greeting,
            "thanks": APCopilotService._topic_thanks,
            "help": APCopilotService._topic_help,
            "agent_performance": APCopilotService._topic_agent_performance,
            "exceptions": APCopilotService._topic_exceptions,
            "reviews": APCopilotService._topic_reviews,
            "vendors": APCopilotService._topic_vendors,
            "invoices": APCopilotService._topic_invoices,
            "cases": APCopilotService._topic_cases,
            "reconciliation": APCopilotService._topic_reconciliation,
        }
        handler = handlers.get(topic, APCopilotService._topic_reconciliation)
        return handler(user, role)

    # ── Topic: Greeting / Thanks / Help ──

    @staticmethod
    def _topic_greeting(user, role: str) -> Dict[str, Any]:
        """Respond to greetings with a friendly welcome."""
        first_name = getattr(user, "first_name", "") or "there"
        role_label = (role or "user").replace("_", " ").title()
        prompts = ROLE_PROMPTS.get(role, ROLE_PROMPTS.get("AP_PROCESSOR", []))
        summary = (
            f"**Hello, {first_name}!** 👋\n\n"
            f"I'm your AP Copilot — here to help you investigate invoices, "
            f"cases, reconciliation results, and more.\n\n"
            f"You can ask me about:\n"
            f"- 📄 **Invoices** — status, pipeline, extraction details\n"
            f"- 🔄 **Reconciliation** — match rates, exceptions, results\n"
            f"- 📋 **Cases** — open cases, escalations, priorities\n"
            f"- 👥 **Reviews** — pending reviews, assignments\n"
            f"- 🏢 **Vendors** — vendor performance, breakdowns\n"
            f"- 🤖 **Agents** — agent performance, tool usage\n\n"
            f"Try one of the suggested prompts below, or just ask a question!"
        )
        return {
            "summary": summary,
            "evidence": [],
            "follow_ups": prompts,
        }

    @staticmethod
    def _topic_thanks(user, role: str) -> Dict[str, Any]:
        """Respond to thank-you messages."""
        first_name = getattr(user, "first_name", "") or "there"
        prompts = ROLE_PROMPTS.get(role, ROLE_PROMPTS.get("AP_PROCESSOR", []))
        summary = (
            f"You're welcome, {first_name}! 😊\n\n"
            f"Let me know if there's anything else I can help with. "
            f"Here are some things you can ask about:"
        )
        return {
            "summary": summary,
            "evidence": [],
            "follow_ups": prompts,
        }

    @staticmethod
    def _topic_help(user, role: str) -> Dict[str, Any]:
        """Respond to help/capability questions."""
        prompts = ROLE_PROMPTS.get(role, ROLE_PROMPTS.get("AP_PROCESSOR", []))
        summary = (
            "**What I can help you with:**\n\n"
            "🔍 **Case Investigation** — Link a case to get deep analysis including "
            "invoice details, reconciliation results, exceptions, agent recommendations, "
            "and governance audit trails.\n\n"
            "📊 **System Insights** (no case needed) — Ask me about:\n"
            "- **Reconciliation overview** — match rates, status breakdown\n"
            "- **Exception analysis** — common mismatches, trends\n"
            "- **Invoice pipeline** — upload status, extraction progress\n"
            "- **Case workload** — open/escalated cases, priorities\n"
            "- **Review queue** — pending reviews, assignments\n"
            "- **Vendor analytics** — vendor-level performance\n"
            "- **Agent performance** — AI agent metrics, success rates\n\n"
            "💡 **Tip:** Link a case using the search button above for the most "
            "detailed analysis. Without a case, I'll give you system-wide summaries."
        )
        return {
            "summary": summary,
            "evidence": [],
            "follow_ups": prompts,
        }

    # ── Topic: Agent Performance ──

    @staticmethod
    def _topic_agent_performance(user, role: str) -> Dict[str, Any]:
        from django.db.models import Avg, Count

        from apps.tools.models import ToolCall

        runs = APCopilotService._scoped_agent_runs(user)
        total_runs = runs.count()
        status_counts = dict(
            runs.values_list("status").annotate(c=Count("id"))
        )
        type_counts = dict(
            runs.values_list("agent_type").annotate(c=Count("id"))
        )
        avg_duration = runs.aggregate(avg=Avg("duration_ms"))["avg"]
        total_tool_calls = ToolCall.objects.filter(
            agent_run__in=runs,
        ).count() if APCopilotService._is_scoped_role(user) or APCopilotService._is_reviewer(user) else ToolCall.objects.count()

        completed = status_counts.get("COMPLETED", 0)
        failed = status_counts.get("FAILED", 0)
        success_rate = f"{completed / total_runs * 100:.0f}%" if total_runs else "N/A"

        parts = ["**Agent Performance Metrics**"]
        parts.append(
            f"Across **{total_runs} agent runs**: "
            f"**{completed}** completed, **{failed}** failed "
            f"(success rate: {success_rate})."
        )
        if avg_duration:
            parts.append(f"Average run duration: **{avg_duration:.0f}ms**.")
        parts.append(f"Total tool calls executed: **{total_tool_calls}**.")

        if type_counts:
            lines = [f"- **{t}**: {c} runs" for t, c in sorted(type_counts.items(), key=lambda x: -x[1])]
            parts.append("**Runs by Agent Type:**\n" + "\n".join(lines))

        evidence = [
            {
                "type": "agent_performance",
                "label": "Agent Runs",
                "data": {
                    "total_runs": total_runs,
                    "completed": completed,
                    "failed": failed,
                    "success_rate": success_rate,
                },
            },
            {
                "type": "agent_performance",
                "label": "Tool Calls",
                "data": {
                    "total_tool_calls": total_tool_calls,
                    "avg_duration_ms": round(avg_duration) if avg_duration else "N/A",
                },
            },
        ]
        for agent_type, count in sorted(type_counts.items(), key=lambda x: -x[1])[:5]:
            evidence.append({
                "type": "agent_performance",
                "label": agent_type.replace("_", " ").title(),
                "data": {"runs": count},
            })

        return {
            "summary": "\n\n".join(parts),
            "evidence": evidence,
            "follow_ups": [
                "Which agents failed and why?",
                "Show reconciliation summary.",
                "What cases are escalated?",
                "Show exception breakdown.",
            ],
        }

    # ── Topic: Exceptions ──

    @staticmethod
    def _topic_exceptions(user, role: str) -> Dict[str, Any]:
        from django.db.models import Count

        exc_qs = APCopilotService._scoped_exceptions(user)
        exc_counts = dict(
            exc_qs.values_list("exception_type")
            .annotate(c=Count("id"))
        )
        total_exc = sum(exc_counts.values())
        sev_counts = dict(
            exc_qs.values_list("severity")
            .annotate(c=Count("id"))
        )

        parts = ["**Exception Analysis**"]
        parts.append(f"There are **{total_exc}** total exceptions across all reconciliations.")

        if sev_counts:
            sev_lines = [f"- **{s}**: {c}" for s, c in sorted(sev_counts.items(), key=lambda x: -x[1])]
            parts.append("**By Severity:**\n" + "\n".join(sev_lines))

        if exc_counts:
            type_lines = [f"- **{t}**: {c}" for t, c in sorted(exc_counts.items(), key=lambda x: -x[1])]
            parts.append("**By Type:**\n" + "\n".join(type_lines))

        evidence = []
        for exc_type, count in sorted(exc_counts.items(), key=lambda x: -x[1])[:6]:
            evidence.append({
                "type": "exception",
                "label": exc_type.replace("_", " ").title(),
                "data": {"count": count},
            })

        return {
            "summary": "\n\n".join(parts),
            "evidence": evidence,
            "follow_ups": [
                "Which cases have the most exceptions?",
                "Show reconciliation summary.",
                "Show agent performance metrics.",
                "What cases are pending review?",
            ],
        }

    # ── Topic: Reviews ──

    @staticmethod
    def _topic_reviews(user, role: str) -> Dict[str, Any]:
        from django.db.models import Count

        review_qs = APCopilotService._scoped_reviews(user)
        status_counts = dict(
            review_qs.values_list("status")
            .annotate(c=Count("id"))
        )
        total_reviews = sum(status_counts.values())
        pending = status_counts.get("PENDING", 0) + status_counts.get("ASSIGNED", 0)
        in_review = status_counts.get("IN_REVIEW", 0)
        approved = status_counts.get("APPROVED", 0)
        rejected = status_counts.get("REJECTED", 0)

        parts = ["**Review Status Overview**"]
        parts.append(
            f"There are **{total_reviews}** review assignments total: "
            f"**{pending}** pending, **{in_review}** in review, "
            f"**{approved}** approved, **{rejected}** rejected."
        )

        if status_counts:
            lines = [f"- **{s}**: {c}" for s, c in sorted(status_counts.items(), key=lambda x: -x[1])]
            parts.append("**Status Breakdown:**\n" + "\n".join(lines))

        evidence = [{
            "type": "review",
            "label": "Review Pipeline",
            "data": {
                "total": total_reviews,
                "pending": pending,
                "in_review": in_review,
                "approved": approved,
                "rejected": rejected,
            },
        }]

        return {
            "summary": "\n\n".join(parts),
            "evidence": evidence,
            "follow_ups": [
                "Which cases are escalated?",
                "Show reconciliation summary.",
                "Show exception breakdown.",
                "Show agent performance metrics.",
            ],
        }

    # ── Topic: Vendors ──

    @staticmethod
    def _topic_vendors(user, role: str) -> Dict[str, Any]:
        from django.db.models import Count

        from apps.documents.models import Invoice, PurchaseOrder

        vendor_qs = APCopilotService._scoped_vendors(user)
        invoice_qs = APCopilotService._scoped_invoices(user)
        total_vendors = vendor_qs.count()
        top_by_invoices = list(
            invoice_qs.exclude(vendor__isnull=True)
            .values("vendor__name")
            .annotate(inv_count=Count("id"))
            .order_by("-inv_count")[:5]
        )
        # POs scoped via user's invoices
        po_qs = PurchaseOrder.objects.all()
        if APCopilotService._is_scoped_role(user):
            user_po_numbers = (
                invoice_qs.exclude(po_number="")
                .values_list("po_number", flat=True)
            )
            po_qs = po_qs.filter(po_number__in=user_po_numbers)
        top_by_pos = list(
            po_qs.exclude(vendor__isnull=True)
            .values("vendor__name")
            .annotate(po_count=Count("id"))
            .order_by("-po_count")[:5]
        )

        parts = ["**Vendor Overview**"]
        parts.append(f"There are **{total_vendors}** active vendors in the system.")

        if top_by_invoices:
            lines = [f"- **{v['vendor__name']}**: {v['inv_count']} invoices" for v in top_by_invoices]
            parts.append("**Top Vendors by Invoice Count:**\n" + "\n".join(lines))

        if top_by_pos:
            lines = [f"- **{v['vendor__name']}**: {v['po_count']} POs" for v in top_by_pos]
            parts.append("**Top Vendors by PO Count:**\n" + "\n".join(lines))

        evidence = []
        for v in top_by_invoices[:4]:
            evidence.append({
                "type": "vendor",
                "label": v["vendor__name"] or "Unknown",
                "data": {"invoices": v["inv_count"]},
            })

        return {
            "summary": "\n\n".join(parts),
            "evidence": evidence,
            "follow_ups": [
                "Show reconciliation summary.",
                "Which vendor has the most exceptions?",
                "Show invoice pipeline status.",
                "Show agent performance metrics.",
            ],
        }

    # ── Topic: Invoices ──

    @staticmethod
    def _topic_invoices(user, role: str) -> Dict[str, Any]:
        from django.db.models import Count

        invoice_qs = APCopilotService._scoped_invoices(user)
        status_counts = dict(
            invoice_qs.values_list("status")
            .annotate(c=Count("id"))
        )
        total = sum(status_counts.values())

        parts = ["**Invoice Pipeline Status**"]
        parts.append(f"There are **{total}** invoices in the system.")

        if status_counts:
            lines = [f"- **{s}**: {c}" for s, c in sorted(status_counts.items(), key=lambda x: -x[1])]
            parts.append("**By Status:**\n" + "\n".join(lines))

        evidence = [{
            "type": "invoice",
            "label": "Invoice Pipeline",
            "data": status_counts,
        }]

        return {
            "summary": "\n\n".join(parts),
            "evidence": evidence,
            "follow_ups": [
                "Show reconciliation summary.",
                "Which invoices failed extraction?",
                "Show vendor breakdown.",
                "Show agent performance metrics.",
            ],
        }

    # ── Topic: Cases ──

    @staticmethod
    def _topic_cases(user, role: str) -> Dict[str, Any]:
        from django.db.models import Count

        case_qs = APCopilotService._scoped_cases(user)
        status_counts = dict(
            case_qs.values_list("status")
            .annotate(c=Count("id"))
        )
        total = sum(status_counts.values())
        priority_counts = dict(
            case_qs.values_list("priority")
            .annotate(c=Count("id"))
        )

        attention_cases = list(
            case_qs.filter(
                status__in=["ESCALATED", "READY_FOR_REVIEW", "EXCEPTION_ANALYSIS_IN_PROGRESS"],
            ).order_by("-created_at")[:5]
            .values("id", "case_number", "status", "priority")
        )

        parts = ["**Case Pipeline Overview**"]
        parts.append(f"There are **{total}** cases total.")

        if status_counts:
            lines = [f"- **{s}**: {c}" for s, c in sorted(status_counts.items(), key=lambda x: -x[1])]
            parts.append("**By Status:**\n" + "\n".join(lines))

        if priority_counts:
            lines = [f"- **{p}**: {c}" for p, c in sorted(priority_counts.items(), key=lambda x: -x[1])]
            parts.append("**By Priority:**\n" + "\n".join(lines))

        if attention_cases:
            lines = [f"- {c['case_number']} — {c['status']} (priority: {c['priority']})" for c in attention_cases]
            parts.append("**Cases Needing Attention:**\n" + "\n".join(lines))

        evidence = [{
            "type": "case_overview",
            "label": "Case Pipeline",
            "data": {"total": total, **{k: v for k, v in status_counts.items()}},
        }]
        for c in attention_cases[:3]:
            evidence.append({
                "type": "case",
                "label": c["case_number"],
                "data": {"status": c["status"], "priority": c["priority"]},
            })

        return {
            "summary": "\n\n".join(parts),
            "evidence": evidence,
            "follow_ups": [
                "Show reconciliation summary.",
                "Which cases have the most exceptions?",
                "Show pending reviews.",
                "Show agent performance metrics.",
            ],
        }

    # ── Topic: Reconciliation (default) ──

    @staticmethod
    def _topic_reconciliation(user, role: str) -> Dict[str, Any]:
        """Default handler — system-wide reconciliation summary."""
        system_data = APCopilotService._build_system_context(user)
        summary = APCopilotService._build_system_summary(
            "", system_data, role,
        )
        return {
            "summary": summary,
            "evidence": system_data.get("evidence", []),
            "follow_ups": system_data.get("follow_ups", []),
        }

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
