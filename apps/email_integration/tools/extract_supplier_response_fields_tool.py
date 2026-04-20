"""Procurement wrapper tool: extract structured supplier response fields from email body."""
from __future__ import annotations

import re

from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import EmailMessage
from apps.email_integration.services.classification_service import ClassificationService


class ExtractSupplierResponseFieldsTool(BaseTool):
    name = "extract_supplier_response_fields"
    description = (
        "Extract key procurement fields from a supplier email response: "
        "quoted price, delivery lead time, validity period, payment terms, and any revision indicators. "
        "Deterministic extraction only -- no LLM call."
    )
    required_permission = "email.read_thread"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer", "description": "PK of the EmailMessage"},
        },
        "required": ["email_message_id"],
    }

    # Simple regex patterns for common supplier email fields
    _PRICE_PATTERN = re.compile(r"(?:total\s+)?(?:price|amount|value|cost)[:\s]+([A-Z]{0,3}\s?[\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
    _LEAD_TIME_PATTERN = re.compile(r"(?:lead\s+time|delivery)[:\s]+(\d+\s+(?:days?|weeks?|months?))", re.IGNORECASE)
    _VALIDITY_PATTERN = re.compile(r"(?:valid(?:ity)?|quote\s+valid)[:\s]+(\d+\s+(?:days?|weeks?|months?))", re.IGNORECASE)
    _PAYMENT_PATTERN = re.compile(r"(?:payment\s+terms?)[:\s]+([^\n\.]{3,60})", re.IGNORECASE)
    _REVISED_PATTERN = re.compile(r"\b(?:revised|updated|amended|resubmit|new\s+quote)\b", re.IGNORECASE)

    def run(self, *, email_message_id: int, **kwargs) -> ToolResult:
        message = self._scoped(EmailMessage.objects.filter(pk=email_message_id)).first()
        if not message:
            return ToolResult(success=True, data={"found": False, "email_message_id": email_message_id})

        body = message.body_text or ""

        price_match = self._PRICE_PATTERN.search(body)
        lead_time_match = self._LEAD_TIME_PATTERN.search(body)
        validity_match = self._VALIDITY_PATTERN.search(body)
        payment_match = self._PAYMENT_PATTERN.search(body)
        is_revised = bool(self._REVISED_PATTERN.search(body))

        extracted = {
            "quoted_price_raw": price_match.group(1).strip() if price_match else None,
            "delivery_lead_time_raw": lead_time_match.group(1).strip() if lead_time_match else None,
            "validity_period_raw": validity_match.group(1).strip() if validity_match else None,
            "payment_terms_raw": payment_match.group(1).strip() if payment_match else None,
            "is_revised_quote": is_revised,
        }

        classification = ClassificationService.classify(message.subject, body)
        intent = ClassificationService.infer_intent(classification)

        return ToolResult(
            success=True,
            data={
                "found": True,
                "email_message_id": email_message_id,
                "extracted_fields": extracted,
                "fields_extracted_count": sum(1 for v in extracted.values() if v not in (None, False)),
                "base_classification": classification,
                "inferred_intent": intent,
                "sender": message.sender_address,
            },
        )
