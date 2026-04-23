"""AP wrapper tool: extract APPROVE / REJECT intent from an email reply."""
from __future__ import annotations

from apps.tools.registry.base import BaseTool, ToolResult, register_tool

from apps.email_integration.models import EmailMessage
from apps.email_integration.services.classification_service import ClassificationService


@register_tool
class ExtractCaseApprovalFromEmailTool(BaseTool):
    name = "extract_case_approval_from_email"
    description = (
        "Analyse an email message body for AP case approval or rejection signals. "
        "Returns intent (APPROVE / REJECT / UNCLEAR) with confidence and key phrases."
    )
    required_permission = "email.read_thread"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer", "description": "PK of the EmailMessage to analyse"},
        },
        "required": ["email_message_id"],
    }

    # Keywords used for simple deterministic matching (augmented by ClassificationService)
    _APPROVE_SIGNALS = [
        "approved", "approve", "proceed", "confirmed", "go ahead",
        "authorize", "authorised", "authorised", "accept", "accepted",
    ]
    _REJECT_SIGNALS = [
        "reject", "rejected", "decline", "declined", "denied", "do not proceed",
        "do not approve", "not approved", "cancel", "cancelled", "hold",
        "put on hold", "not authorised", "not authorized",
    ]

    def run(self, *, email_message_id: int, **kwargs) -> ToolResult:
        message = self._scoped(EmailMessage.objects.filter(pk=email_message_id)).first()
        if not message:
            return ToolResult(success=True, data={"found": False, "email_message_id": email_message_id})

        body_lower = (message.body_text or "").lower()
        subject_lower = (message.subject or "").lower()
        combined = f"{subject_lower} {body_lower}"

        approve_hits = [s for s in self._APPROVE_SIGNALS if s in combined]
        reject_hits = [s for s in self._REJECT_SIGNALS if s in combined]

        if approve_hits and not reject_hits:
            intent = "APPROVE"
            confidence = min(0.5 + 0.1 * len(approve_hits), 0.95)
        elif reject_hits and not approve_hits:
            intent = "REJECT"
            confidence = min(0.5 + 0.1 * len(reject_hits), 0.95)
        elif approve_hits and reject_hits:
            intent = "UNCLEAR"
            confidence = 0.3
        else:
            intent = "UNCLEAR"
            confidence = 0.1

        # Pull base classification for context
        classification = ClassificationService.classify(message.subject, message.body_text)

        return ToolResult(
            success=True,
            data={
                "found": True,
                "email_message_id": email_message_id,
                "approval_intent": intent,
                "confidence": confidence,
                "approve_signals_found": approve_hits,
                "reject_signals_found": reject_hits,
                "base_classification": classification,
                "sender": message.sender_address,
            },
        )
