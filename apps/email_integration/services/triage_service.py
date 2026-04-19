"""Message triage service that composes classification and linking outcomes."""
from __future__ import annotations

from apps.email_integration.enums import TargetDomain
from apps.email_integration.services.classification_service import ClassificationService
from apps.email_integration.services.entity_linking_service import EntityLinkingService


class TriageService:
    """Builds deterministic triage outputs for routing decisions."""

    @staticmethod
    def triage_message(email_message, mailbox) -> dict:
        classification = ClassificationService.classify(email_message.subject, email_message.body_text)
        intent = ClassificationService.infer_intent(classification)
        trust_level = ClassificationService.infer_sender_trust(
            email_message.from_email,
            tenant_domains=(mailbox.allowed_sender_domains_json or []),
        )
        entity = EntityLinkingService.infer_entity(email_message.subject, email_message.body_text)

        if classification in ["AP_INVOICE", "AP_SUPPORTING_DOCUMENT"]:
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
        }
