"""Deterministic email classification and intent extraction service."""
from __future__ import annotations

from apps.email_integration.enums import EmailIntentType, EmailMessageClassification, SenderTrustLevel


class ClassificationService:
    """Rule-first message classification with no raw LLM mailbox access."""

    @staticmethod
    def classify(subject: str, body_text: str) -> str:
        content = f"{subject or ''} {body_text or ''}".lower()
        if "invoice" in content:
            return EmailMessageClassification.AP_INVOICE
        if "quotation" in content or "quote" in content:
            return EmailMessageClassification.PROCUREMENT_QUOTATION
        if "approval" in content or "approved" in content or "reject" in content:
            return EmailMessageClassification.APPROVAL_RESPONSE
        if "clarification" in content:
            return EmailMessageClassification.PROCUREMENT_CLARIFICATION
        if "proposal" in content:
            return EmailMessageClassification.PROCUREMENT_PROPOSAL
        if "review" in content:
            return EmailMessageClassification.INTERNAL_REVIEW_REPLY
        if content.strip():
            return EmailMessageClassification.GENERAL_QUERY
        return EmailMessageClassification.UNKNOWN

    @staticmethod
    def infer_intent(classification: str) -> str:
        mapping = {
            EmailMessageClassification.AP_INVOICE: EmailIntentType.DOCUMENT_INGEST,
            EmailMessageClassification.AP_SUPPORTING_DOCUMENT: EmailIntentType.DOCUMENT_INGEST,
            EmailMessageClassification.PROCUREMENT_QUOTATION: EmailIntentType.DOCUMENT_INGEST,
            EmailMessageClassification.APPROVAL_RESPONSE: EmailIntentType.APPROVAL_ACTION,
            EmailMessageClassification.PROCUREMENT_CLARIFICATION: EmailIntentType.CLARIFICATION_RESPONSE,
        }
        return mapping.get(classification, EmailIntentType.MANUAL_TRIAGE)

    @staticmethod
    def infer_sender_trust(from_email: str, tenant_domains=None) -> str:
        email_lower = (from_email or "").strip().lower()
        if not email_lower or "@" not in email_lower:
            return SenderTrustLevel.UNKNOWN
        domain = email_lower.split("@", 1)[1]
        if tenant_domains and domain in {d.lower() for d in tenant_domains}:
            return SenderTrustLevel.TRUSTED_INTERNAL
        if any(k in domain for k in ["spam", "phish", "fraud"]):
            return SenderTrustLevel.SUSPICIOUS
        return SenderTrustLevel.KNOWN_EXTERNAL
