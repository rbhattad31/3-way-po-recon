"""Routes classified messages to governed domain handlers."""
from __future__ import annotations

from apps.core.decorators import observed_service
from apps.email_integration.domain_handlers.ap_handler import APEmailHandler
from apps.email_integration.domain_handlers.notification_handler import NotificationEmailHandler
from apps.email_integration.domain_handlers.procurement_handler import ProcurementEmailHandler
from apps.email_integration.enums import (
    EmailProcessingStatus,
    EmailRoutingDecisionStatus,
    EmailRoutingDecisionType,
    EmailRoutingStatus,
    TargetDomain,
)
from apps.email_integration.models import EmailRoutingDecision


class RoutingService:
    """Applies routing decisions and invokes domain handlers."""

    HANDLER_BY_DOMAIN = {
        TargetDomain.AP: APEmailHandler,
        TargetDomain.PROCUREMENT: ProcurementEmailHandler,
        TargetDomain.TRIAGE: NotificationEmailHandler,
        TargetDomain.NOTIFICATION_ONLY: NotificationEmailHandler,
    }

    @classmethod
    def _resolve_handler(cls, target_domain):
        return cls.HANDLER_BY_DOMAIN.get(target_domain, NotificationEmailHandler)

    @classmethod
    def _decision_type(cls, *, manual: bool):
        if manual:
            return EmailRoutingDecisionType.MANUAL
        return EmailRoutingDecisionType.RULE_BASED

    @classmethod
    @observed_service("email.routing")
    def apply_routing(cls, email_message, triage_result: dict, *, actor_user=None, manual: bool = False) -> EmailRoutingDecision:
        target_domain = triage_result.get("target_domain") or TargetDomain.TRIAGE
        handler_cls = cls._resolve_handler(target_domain)
        handler = handler_cls()

        decision = EmailRoutingDecision.objects.create(
            tenant=email_message.tenant,
            email_message=email_message,
            decision_type=cls._decision_type(manual=manual),
            target_domain=target_domain,
            target_handler=handler_cls.handler_name,
            target_entity_type=triage_result.get("entity_type", ""),
            target_entity_id=triage_result.get("entity_id"),
            confidence_score=1.0 if manual else (0.9 if target_domain != TargetDomain.TRIAGE else 0.6),
            deterministic_flag=True,
            rule_name="manual_override" if manual else "email_default_rule_set",
            rule_version="v1",
            llm_used=False,
            reasoning_summary="Manual routing override." if manual else "Deterministic classification and entity-linking routing.",
            evidence_json={
                "classification": triage_result.get("classification"),
                "intent": triage_result.get("intent"),
                "trust": triage_result.get("trust_level"),
            },
            final_status=EmailRoutingDecisionStatus.PROPOSED,
        )

        if not handler.can_handle(email_message, decision):
            decision.final_status = EmailRoutingDecisionStatus.REJECTED
            decision.save(update_fields=["final_status", "updated_at"])
            email_message.routing_status = EmailRoutingStatus.TRIAGED
            email_message.processing_status = EmailProcessingStatus.FAILED
            email_message.save(update_fields=["routing_status", "processing_status", "updated_at"])
            return decision

        try:
            handler.process(email_message, decision, actor_user=actor_user)
            decision.final_status = EmailRoutingDecisionStatus.APPLIED
            decision.save(update_fields=["final_status", "updated_at"])

            email_message.routing_status = EmailRoutingStatus.ROUTED
            email_message.processing_status = EmailProcessingStatus.PROCESSED
            email_message.save(update_fields=["routing_status", "processing_status", "updated_at"])
        except Exception:
            decision.final_status = EmailRoutingDecisionStatus.FAILED
            decision.save(update_fields=["final_status", "updated_at"])
            email_message.routing_status = EmailRoutingStatus.FAILED
            email_message.processing_status = EmailProcessingStatus.FAILED
            email_message.save(update_fields=["routing_status", "processing_status", "updated_at"])
            raise
        return decision
