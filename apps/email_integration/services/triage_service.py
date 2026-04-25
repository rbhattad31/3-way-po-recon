"""Message triage service that composes classification and linking outcomes."""
from __future__ import annotations

from apps.email_integration.enums import EmailIntentType, EmailMessageClassification, TargetDomain
from apps.email_integration.services.classification_service import ClassificationService
from apps.email_integration.services.entity_linking_service import EntityLinkingService


class TriageService:
    """Builds classification and linking outputs for routing decisions."""

    @staticmethod
    def triage_message(email_message, mailbox) -> dict:
        classification_meta = ClassificationService.classify_with_metadata(
            email_message.subject,
            email_message.body_text,
            mailbox=mailbox,
        )
        classification = classification_meta["classification"]
        intent = ClassificationService.infer_intent(classification)
        classification_source = classification_meta.get("source") or "RULE"
        requires_human_decision = (
            classification_source == "RULE_FALLBACK"
            or classification == EmailMessageClassification.UNKNOWN
        )
        if requires_human_decision:
            intent = EmailIntentType.MANUAL_TRIAGE
        trust_level = ClassificationService.infer_sender_trust(
            email_message.from_email,
            tenant_domains=(mailbox.allowed_sender_domains_json or []),
        )
        entity = EntityLinkingService.infer_entity(email_message.subject, email_message.body_text)

        if requires_human_decision:
            domain = TargetDomain.TRIAGE
        elif classification in ["AP_INVOICE", "AP_SUPPORTING_DOCUMENT"]:
            domain = TargetDomain.AP
        elif classification in ["PROCUREMENT_QUOTATION", "PROCUREMENT_PROPOSAL", "PROCUREMENT_CLARIFICATION"]:
            domain = TargetDomain.PROCUREMENT
        else:
            domain = mailbox.default_domain_route or TargetDomain.TRIAGE

        return {
            "classification": classification,
            "intent": intent,
            "trust_level": trust_level,
            "target_domain": domain,
            "entity_type": entity.get("entity_type", ""),
            "entity_id": entity.get("entity_id"),
            "llm_used": bool(classification_meta.get("llm_used")),
            "classification_source": classification_source,
            "confidence": float(classification_meta.get("confidence") or 0.0),
            "reasoning_summary": classification_meta.get("reasoning_summary") or "",
            "model_name": classification_meta.get("model_name") or "",
            "fallback_classification": classification_meta.get("fallback_classification") or "",
            "requires_human_decision": requires_human_decision,
        }
