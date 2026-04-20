"""AP wrapper tool: send a governed clarification email to a vendor."""
from __future__ import annotations

from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import MailboxConfig
from apps.email_integration.services.outbound_email_service import OutboundEmailService


class SendVendorClarificationEmailTool(BaseTool):
    name = "send_vendor_clarification_email"
    description = (
        "Send a governed clarification email to a vendor on behalf of the AP team. "
        "Uses an approved email template and records an outbound EmailAction with full audit trail."
    )
    required_permission = "email.send"
    parameters_schema = {
        "type": "object",
        "properties": {
            "mailbox_id": {"type": "integer", "description": "Outbound mailbox to send from"},
            "vendor_email": {"type": "string", "description": "Vendor recipient email address"},
            "ap_case_id": {"type": "integer", "description": "AP case the clarification relates to"},
            "clarification_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of specific items requiring clarification from the vendor",
            },
            "invoice_reference": {"type": "string", "description": "Invoice number or reference for the email subject"},
        },
        "required": ["mailbox_id", "vendor_email", "ap_case_id", "clarification_points"],
    }

    def run(
        self,
        *,
        mailbox_id: int,
        vendor_email: str,
        ap_case_id: int,
        clarification_points: list,
        invoice_reference: str = "",
        **kwargs,
    ) -> ToolResult:
        mailbox = self._scoped(
            MailboxConfig.objects.filter(pk=mailbox_id, is_active=True, is_outbound_enabled=True)
        ).first()
        if mailbox is None:
            return ToolResult(success=False, error="Active outbound mailbox not found")

        variables = {
            "ap_case_id": ap_case_id,
            "invoice_reference": invoice_reference,
            "clarification_points": clarification_points,
            "clarification_list": "\n".join(f"- {p}" for p in clarification_points),
        }

        result = OutboundEmailService.send_templated_email(
            tenant=getattr(self, "_tenant", None),
            mailbox=mailbox,
            template_code="AP_VENDOR_CLARIFICATION",
            variables=variables,
            to_recipients=[vendor_email],
            actor_user=None,
            trace_id=kwargs.get("trace_id", ""),
        )

        return ToolResult(
            success=True,
            data={
                "sent": True,
                "vendor_email": vendor_email,
                "ap_case_id": ap_case_id,
                "clarification_points_count": len(clarification_points),
                **result,
            },
        )
