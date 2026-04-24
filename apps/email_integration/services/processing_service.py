"""Service-layer orchestration for enterprise email processing workflows."""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.decorators import observed_service
from apps.email_integration.enums import (
    EmailActionStatus,
    EmailActionType,
    EmailDirection,
    EmailDomainContext,
    EmailLinkStatus,
    EmailProcessingStatus,
    EmailRoutingStatus,
)
from apps.email_integration.models import EmailAction, EmailMessage, EmailThread, MailboxConfig
from apps.email_integration.services.inbound_ingestion_service import InboundIngestionService
from apps.email_integration.services.mailbox_service import MailboxService
from apps.email_integration.services.routing_service import RoutingService
from apps.email_integration.services.triage_service import TriageService


class EmailProcessingService:
    """Shared orchestration for sync, reprocess, route, and recovery workflows."""

    ENTITY_THREAD_FIELD_MAP = {
        "AP_CASE": ("primary_case_id", EmailDomainContext.AP),
        "PROCUREMENT_REQUEST": ("primary_procurement_request_id", EmailDomainContext.PROCUREMENT),
        "SUPPLIER_QUOTATION": ("primary_supplier_quotation_id", EmailDomainContext.PROCUREMENT),
    }

    @staticmethod
    def _resolve_action_type(entity_type: str) -> str:
        mapping = {
            "AP_CASE": EmailActionType.LINK_TO_AP_CASE,
            "PROCUREMENT_REQUEST": EmailActionType.LINK_TO_PROCUREMENT_REQUEST,
            "SUPPLIER_QUOTATION": EmailActionType.LINK_TO_SUPPLIER_QUOTATION,
        }
        return mapping.get(entity_type or "", EmailActionType.QUEUE_FOR_TRIAGE)

    @staticmethod
    def get_message(message_id: int, *, tenant=None):
        qs = EmailMessage.objects.select_related("mailbox", "thread", "tenant").prefetch_related("attachments")
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return qs.filter(pk=message_id).first()

    @classmethod
    def _record_action(
        cls,
        *,
        action_type: str,
        email_message,
        actor_user=None,
        action_status: str = EmailActionStatus.COMPLETED,
        payload=None,
        result=None,
        error_message: str = "",
    ):
        return EmailAction.objects.create(
            tenant=email_message.tenant,
            email_message=email_message,
            thread=email_message.thread,
            action_type=action_type,
            action_status=action_status,
            performed_by_user=actor_user,
            actor_primary_role=(getattr(actor_user, "role", "") or "") if actor_user else "",
            target_entity_type=email_message.matched_entity_type or "",
            target_entity_id=email_message.matched_entity_id,
            payload_json=payload or {},
            result_json=result or {},
            error_message=error_message or "",
            trace_id=email_message.trace_id,
        )

    @classmethod
    def _apply_triage_result(cls, email_message, triage_result: dict):
        email_message.message_classification = triage_result["classification"]
        email_message.intent_type = triage_result["intent"]
        email_message.sender_trust_level = triage_result["trust_level"]
        email_message.matched_entity_type = triage_result.get("entity_type") or ""
        email_message.matched_entity_id = triage_result.get("entity_id")
        email_message.processing_status = EmailProcessingStatus.CLASSIFIED
        email_message.routing_status = EmailRoutingStatus.PENDING
        email_message.save(
            update_fields=[
                "message_classification",
                "intent_type",
                "sender_trust_level",
                "matched_entity_type",
                "matched_entity_id",
                "processing_status",
                "routing_status",
                "updated_at",
            ]
        )

    @classmethod
    @observed_service("email.mailbox.sync_messages")
    def sync_mailbox_messages(cls, mailbox: MailboxConfig) -> dict:
        adapter = MailboxService.get_adapter(mailbox)
        now = timezone.now()
        poll_interval_minutes = max(1, int(getattr(mailbox, "poll_interval_minutes", 5) or 5))
        last_sync_at = getattr(mailbox, "last_sync_at", None)
        if last_sync_at is not None:
            since_dt = last_sync_at - timedelta(minutes=1)
        else:
            since_dt = now - timedelta(minutes=poll_interval_minutes)

        since_cursor = since_dt.isoformat()
        messages = adapter.poll_messages(mailbox, since_cursor=since_cursor)
        processed = 0
        ingested_ids = []
        for payload in messages:
            message = InboundIngestionService.ingest_message_payload(mailbox, payload, tenant=mailbox.tenant)
            processed += 1
            ingested_ids.append(message.pk)

        mailbox.last_sync_at = now
        mailbox.last_success_at = now
        mailbox.last_error_message = ""
        mailbox.save(update_fields=["last_sync_at", "last_success_at", "last_error_message", "updated_at"])
        return {
            "processed_messages": processed,
            "message_ids": ingested_ids,
            "since_cursor": since_cursor,
        }

    @classmethod
    @observed_service("email.message.process")
    def process_message(cls, email_message, *, actor_user=None, target_domain: str | None = None) -> dict:
        triage_result = TriageService.triage_message(email_message, email_message.mailbox)
        if target_domain:
            triage_result["target_domain"] = target_domain

        cls._apply_triage_result(email_message, triage_result)
        decision = RoutingService.apply_routing(
            email_message,
            triage_result,
            actor_user=actor_user,
            manual=bool(target_domain),
        )
        return {
            "message_id": email_message.pk,
            "routing_decision_id": decision.pk,
            "target_domain": decision.target_domain,
            "processing_status": email_message.processing_status,
        }

    @classmethod
    @observed_service("email.message.link")
    def link_message_to_entity(cls, email_message, *, entity_type: str, entity_id: int, actor_user=None) -> dict:
        with transaction.atomic():
            email_message.matched_entity_type = entity_type
            email_message.matched_entity_id = entity_id
            email_message.processing_status = EmailProcessingStatus.LINKED
            email_message.save(
                update_fields=[
                    "matched_entity_type",
                    "matched_entity_id",
                    "processing_status",
                    "updated_at",
                ]
            )

            if email_message.thread_id:
                field_name, domain_context = cls.ENTITY_THREAD_FIELD_MAP.get(
                    entity_type,
                    (None, EmailDomainContext.UNKNOWN),
                )
                updates = ["domain_context", "link_status", "updated_at"]
                email_message.thread.domain_context = domain_context
                email_message.thread.link_status = EmailLinkStatus.LINKED if field_name else EmailLinkStatus.AMBIGUOUS
                if field_name:
                    setattr(email_message.thread, field_name, entity_id)
                    updates.append(field_name)
                email_message.thread.save(update_fields=updates)

            action = cls._record_action(
                action_type=cls._resolve_action_type(entity_type),
                email_message=email_message,
                actor_user=actor_user,
                payload={"operation": "link_message_to_entity", "entity_type": entity_type, "entity_id": entity_id},
                result={"linked": True},
            )

        return {"message_id": email_message.pk, "entity_type": entity_type, "entity_id": entity_id, "action_id": action.pk}

    @classmethod
    @observed_service("email.thread.relink")
    def relink_threads(cls, *, tenant=None) -> dict:
        threads = EmailThread.objects.all()
        if tenant is not None:
            threads = threads.filter(tenant=tenant)

        relinked = 0
        for thread in threads.select_related("tenant"):
            latest_message = thread.messages.exclude(matched_entity_type="").order_by("-received_at", "-created_at").first()
            if latest_message is None or not latest_message.matched_entity_id:
                continue
            cls.link_message_to_entity(
                latest_message,
                entity_type=latest_message.matched_entity_type,
                entity_id=latest_message.matched_entity_id,
            )
            relinked += 1
        return {"relinked_threads": relinked}

    @classmethod
    @observed_service("email.action.retry_failed")
    def retry_failed_actions(cls, *, tenant=None, actor_user=None) -> dict:
        actions = EmailAction.objects.filter(action_status=EmailActionStatus.FAILED).select_related("email_message", "email_message__mailbox")
        if tenant is not None:
            actions = actions.filter(tenant=tenant)

        recovered = 0
        failed = 0
        skipped = 0
        for action in actions:
            message = action.email_message
            if message is None or message.direction != EmailDirection.INBOUND:
                skipped += 1
                continue
            try:
                result = cls.process_message(message, actor_user=actor_user)
                action.action_status = EmailActionStatus.COMPLETED
                action.result_json = {**(action.result_json or {}), "retry_result": result}
                action.error_message = ""
                action.save(update_fields=["action_status", "result_json", "error_message", "updated_at"])
                recovered += 1
            except Exception as exc:
                action.error_message = str(exc)
                action.save(update_fields=["error_message", "updated_at"])
                failed += 1

        return {"recovered": recovered, "failed": failed, "skipped": skipped}
