"""Governed email tools for agent usage -- shared and domain-specific."""

# Shared tools
from apps.email_integration.tools.email_thread_lookup_tool import EmailThreadLookupTool
from apps.email_integration.tools.email_attachment_list_tool import EmailAttachmentListTool
from apps.email_integration.tools.email_body_summary_tool import EmailBodySummaryTool
from apps.email_integration.tools.match_email_to_entity_tool import MatchEmailToEntityTool
from apps.email_integration.tools.send_templated_email_tool import SendTemplatedEmailTool
from apps.email_integration.tools.extract_email_intent_tool import ExtractEmailIntentTool

# AP domain wrapper tools
from apps.email_integration.tools.attach_email_to_case_tool import AttachEmailToCaseTool
from apps.email_integration.tools.extract_case_approval_from_email_tool import ExtractCaseApprovalFromEmailTool
from apps.email_integration.tools.send_vendor_clarification_email_tool import SendVendorClarificationEmailTool

# Procurement domain wrapper tools
from apps.email_integration.tools.attach_email_to_procurement_request_tool import AttachEmailToProcurementRequestTool
from apps.email_integration.tools.attach_email_to_supplier_quotation_tool import AttachEmailToSupplierQuotationTool
from apps.email_integration.tools.extract_supplier_response_fields_tool import ExtractSupplierResponseFieldsTool
from apps.email_integration.tools.send_supplier_clarification_email_tool import SendSupplierClarificationEmailTool

__all__ = [
    # Shared
    "EmailThreadLookupTool",
    "EmailAttachmentListTool",
    "EmailBodySummaryTool",
    "MatchEmailToEntityTool",
    "SendTemplatedEmailTool",
    "ExtractEmailIntentTool",
    # AP wrappers
    "AttachEmailToCaseTool",
    "ExtractCaseApprovalFromEmailTool",
    "SendVendorClarificationEmailTool",
    # Procurement wrappers
    "AttachEmailToProcurementRequestTool",
    "AttachEmailToSupplierQuotationTool",
    "ExtractSupplierResponseFieldsTool",
    "SendSupplierClarificationEmailTool",
]
