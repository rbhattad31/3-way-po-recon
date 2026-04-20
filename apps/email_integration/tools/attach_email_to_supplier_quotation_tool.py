"""Procurement wrapper tool: link email message to a supplier quotation."""
from __future__ import annotations

from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import EmailMessage
from apps.email_integration.enums import EmailActionStatus, EmailActionType, EmailDomainContext, EmailLinkStatus


class AttachEmailToSupplierQuotationTool(BaseTool):
    name = "attach_email_to_supplier_quotation"
    description = (
        "Link an email message or thread to an existing supplier quotation. "
        "Used when a quotation or proposal arrives via email and needs to be associated with "
        "a SupplierQuotation record for the procurement analysis pipeline."
    )
    required_permission = "email.route"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer", "description": "PK of the EmailMessage"},
            "supplier_quotation_id": {"type": "integer", "description": "PK of the SupplierQuotation"},
        },
        "required": ["email_message_id", "supplier_quotation_id"],
    }

    def run(self, *, email_message_id: int, supplier_quotation_id: int, **kwargs) -> ToolResult:
        from apps.email_integration.models import EmailAction

        message = self._scoped(EmailMessage.objects.filter(pk=email_message_id)).first()
        if not message:
            return ToolResult(success=False, error=f"EmailMessage {email_message_id} not found")

        try:
            from apps.procurement.models import SupplierQuotation
            quotation = self._scoped(SupplierQuotation.objects.filter(pk=supplier_quotation_id)).first()
        except ImportError:
            quotation = None

        if quotation is None:
            return ToolResult(success=False, error=f"SupplierQuotation {supplier_quotation_id} not found")

        message.matched_entity_type = "SUPPLIER_QUOTATION"
        message.matched_entity_id = supplier_quotation_id
        message.save(update_fields=["matched_entity_type", "matched_entity_id"])

        if message.thread:
            thread = message.thread
            thread.domain_context = EmailDomainContext.PROCUREMENT
            thread.link_status = EmailLinkStatus.LINKED
            thread.primary_supplier_quotation_id = supplier_quotation_id
            thread.save(update_fields=["domain_context", "link_status", "primary_supplier_quotation_id"])

        EmailAction.objects.create(
            tenant=message.tenant,
            email_message=message,
            thread=message.thread,
            action_type=EmailActionType.LINK_TO_SUPPLIER_QUOTATION,
            action_status=EmailActionStatus.COMPLETED,
            target_entity_type="SUPPLIER_QUOTATION",
            target_entity_id=str(supplier_quotation_id),
            trace_id=message.trace_id or kwargs.get("trace_id", ""),
            payload_json={"email_message_id": email_message_id, "supplier_quotation_id": supplier_quotation_id},
            result_json={"linked": True},
        )

        return ToolResult(
            success=True,
            data={
                "linked": True,
                "email_message_id": email_message_id,
                "supplier_quotation_id": supplier_quotation_id,
            },
        )
