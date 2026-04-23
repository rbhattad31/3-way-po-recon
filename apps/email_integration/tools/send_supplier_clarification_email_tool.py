"""Procurement wrapper tool: send a governed supplier clarification email."""
from __future__ import annotations

from apps.tools.registry.base import BaseTool, ToolResult, register_tool

from apps.email_integration.models import MailboxConfig
from apps.email_integration.services.outbound_email_service import OutboundEmailService


@register_tool
class SendSupplierClarificationEmailTool(BaseTool):
    name = "send_supplier_clarification_email"
    description = (
        "Send a governed clarification email to a supplier on behalf of the procurement team. "
        "Uses an approved template and records a full outbound EmailAction audit trail."
    )
    required_permission = "email.send"
    parameters_schema = {
        "type": "object",
        "properties": {
            "mailbox_id": {"type": "integer", "description": "Outbound mailbox to send from"},
            "supplier_email": {"type": "string", "description": "Supplier recipient email address"},
            "procurement_request_id": {"type": "integer", "description": "PK of the linked ProcurementRequest"},
            "supplier_quotation_id": {
                "type": "integer",
                "description": "Optional PK of the specific SupplierQuotation being queried",
            },
            "clarification_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of specific points requiring clarification from the supplier",
            },
            "rfq_reference": {
                "type": "string",
                "description": "RFQ or procurement request reference for email subject",
            },
        },
        "required": ["mailbox_id", "supplier_email", "procurement_request_id", "clarification_points"],
    }

    def run(
        self,
        *,
        mailbox_id: int,
        supplier_email: str,
        procurement_request_id: int,
        supplier_quotation_id: int | None = None,
        clarification_points: list,
        rfq_reference: str = "",
        **kwargs,
    ) -> ToolResult:
        mailbox = self._scoped(
            MailboxConfig.objects.filter(pk=mailbox_id, is_active=True, is_outbound_enabled=True)
        ).first()
        if mailbox is None:
            return ToolResult(success=False, error="Active outbound mailbox not found")

        variables = {
            "procurement_request_id": procurement_request_id,
            "supplier_quotation_id": supplier_quotation_id,
            "rfq_reference": rfq_reference,
            "clarification_points": clarification_points,
            "clarification_list": "\n".join(f"- {p}" for p in clarification_points),
        }

        result = OutboundEmailService.send_templated_email(
            tenant=getattr(self, "_tenant", None),
            mailbox=mailbox,
            template_code="PROCUREMENT_SUPPLIER_CLARIFICATION",
            variables=variables,
            to_recipients=[supplier_email],
            actor_user=None,
            trace_id=kwargs.get("trace_id", ""),
        )

        return ToolResult(
            success=True,
            data={
                "sent": True,
                "supplier_email": supplier_email,
                "procurement_request_id": procurement_request_id,
                "supplier_quotation_id": supplier_quotation_id,
                "clarification_points_count": len(clarification_points),
                **result,
            },
        )
