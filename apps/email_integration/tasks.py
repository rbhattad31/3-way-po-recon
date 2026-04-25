"""Celery tasks for mailbox polling, enterprise routing, and recovery workflows."""
from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from apps.core.decorators import observed_task
from apps.email_integration.enums import EmailActionStatus, EmailActionType
from apps.email_integration.models import EmailAction, EmailMessage, MailboxConfig
from apps.email_integration.services.attachment_service import AttachmentService
from apps.email_integration.services.inbound_ingestion_service import InboundIngestionService
from apps.email_integration.services.mailbox_service import MailboxService
from apps.email_integration.services.outbound_email_service import OutboundEmailService
from apps.email_integration.services.processing_service import EmailProcessingService
from apps.email_integration.services.routing_service import RoutingService


def _mailbox_poll_due(mailbox, now=None) -> bool:
    now = now or timezone.now()
    if not getattr(mailbox, "polling_enabled", False):
        return False
    interval_minutes = int(getattr(mailbox, "poll_interval_minutes", 5) or 5)
    last_sync_at = getattr(mailbox, "last_sync_at", None)
    if last_sync_at is None:
        return True
    elapsed_seconds = (now - last_sync_at).total_seconds()
    return elapsed_seconds >= max(60, interval_minutes * 60)


@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
@observed_task("email.poll_mailboxes")
def poll_mailboxes_task(self, tenant_id=None):
    mailboxes = MailboxConfig.objects.filter(is_active=True, polling_enabled=True, is_inbound_enabled=True)
    if tenant_id:
        mailboxes = mailboxes.filter(tenant_id=tenant_id)

    now = timezone.now()
    processed = 0
    polled_mailboxes = 0
    skipped_mailboxes = 0
    for mailbox in mailboxes.iterator():
        if not _mailbox_poll_due(mailbox, now=now):
            skipped_mailboxes += 1
            continue
        result = EmailProcessingService.sync_mailbox_messages(mailbox)
        processed += result.get("processed_messages", 0)
        polled_mailboxes += 1

    return {
        "processed_messages": processed,
        "polled_mailboxes": polled_mailboxes,
        "skipped_mailboxes": skipped_mailboxes,
    }


@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
@observed_task("email.sync_mailbox")
def sync_mailbox_task(self, mailbox_id: int, tenant_id=None):
    mailbox_qs = MailboxConfig.objects.filter(pk=mailbox_id, is_active=True)
    if tenant_id:
        mailbox_qs = mailbox_qs.filter(tenant_id=tenant_id)
    mailbox = mailbox_qs.first()
    if mailbox is None:
        return {"synced": False, "reason": "mailbox_not_found"}
    result = EmailProcessingService.sync_mailbox_messages(mailbox)
    EmailAction.objects.create(
        tenant=mailbox.tenant,
        action_type=EmailActionType.SYNC_MAILBOX,
        action_status=EmailActionStatus.COMPLETED,
        payload_json={"mailbox_id": mailbox.pk},
        result_json=result,
    )
    return {"synced": True, **result}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.ingest_webhook")
def ingest_webhook_payload_task(self, mailbox_id: int, payload: dict):
    mailbox = MailboxService.get_mailbox(mailbox_id)
    if mailbox is None:
        return {"ingested": False, "reason": "mailbox_not_found"}
    message = InboundIngestionService.ingest_message_payload(mailbox, payload, tenant=mailbox.tenant)
    return {"ingested": True, "message_id": message.pk}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.ingest_provider_message")
def ingest_provider_message_task(self, mailbox_id: int, provider_message_id: str, payload: dict | None = None):
    mailbox = MailboxService.get_mailbox(mailbox_id)
    if mailbox is None:
        return {"ingested": False, "reason": "mailbox_not_found"}
    adapter = MailboxService.get_adapter(mailbox)
    normalized_payload = payload or adapter.get_message(mailbox, provider_message_id)
    if normalized_payload is None:
        return {"ingested": False, "reason": "provider_message_not_found"}
    normalized_payload.setdefault("provider_message_id", provider_message_id)
    attachments = normalized_payload.get("attachments") or adapter.get_attachments(mailbox, provider_message_id)
    if attachments:
        normalized_payload["attachments"] = attachments
    message = InboundIngestionService.ingest_message_payload(mailbox, normalized_payload, tenant=mailbox.tenant)
    return {"ingested": True, "message_id": message.pk}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.process_message")
def process_email_message_task(self, message_id: int, tenant_id=None, force_target_domain: str | None = None):
    message_qs = EmailMessage.objects.select_related("mailbox", "thread", "tenant").prefetch_related("attachments").filter(pk=message_id)
    if tenant_id:
        message_qs = message_qs.filter(tenant_id=tenant_id)
    message = message_qs.first()
    if message is None:
        return {"processed": False, "reason": "message_not_found"}
    result = EmailProcessingService.process_message(message, target_domain=force_target_domain)
    return {"processed": True, **result}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.download_attachment")
def download_email_attachment_task(self, message_id: int):
    message = EmailMessage.objects.select_related("mailbox", "tenant").filter(pk=message_id).first()
    if message is None:
        return {"downloaded": False, "reason": "message_not_found"}
    adapter = MailboxService.get_adapter(message.mailbox)
    attachments = adapter.get_attachments(message.mailbox, message.provider_message_id)
    if not attachments:
        return {"downloaded": False, "reason": "no_provider_attachments"}
    saved = AttachmentService.store_attachments(message, attachments, tenant=message.tenant, trigger_extraction=True)
    return {"downloaded": True, "attachment_ids": [item.pk for item in saved]}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.route_message")
def route_email_message_task(self, message_id: int, target_domain: str | None = None):
    message = EmailProcessingService.get_message(message_id)
    if message is None:
        return {"routed": False, "reason": "message_not_found"}
    result = EmailProcessingService.process_message(message, target_domain=target_domain)
    return {"routed": True, **result}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.handle_ap")
def handle_ap_email_task(self, message_id: int, decision_id: int | None = None):
    message = EmailProcessingService.get_message(message_id)
    if message is None:
        return {"handled": False, "reason": "message_not_found"}
    decision = message.routing_decisions.order_by("-created_at").first() if decision_id is None else message.routing_decisions.filter(pk=decision_id).first()
    if decision is None:
        return {"handled": False, "reason": "routing_decision_not_found"}
    handler_cls = RoutingService.HANDLER_BY_DOMAIN.get(decision.target_domain)
    if handler_cls is None:
        return {"handled": False, "reason": "handler_not_found"}
    result = handler_cls().process(message, decision)
    return {"handled": True, "result": result}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.handle_procurement")
def handle_procurement_email_task(self, message_id: int, decision_id: int | None = None):
    message = EmailProcessingService.get_message(message_id)
    if message is None:
        return {"handled": False, "reason": "message_not_found"}
    decision = message.routing_decisions.order_by("-created_at").first() if decision_id is None else message.routing_decisions.filter(pk=decision_id).first()
    if decision is None:
        return {"handled": False, "reason": "routing_decision_not_found"}
    handler_cls = RoutingService.HANDLER_BY_DOMAIN.get(decision.target_domain)
    if handler_cls is None:
        return {"handled": False, "reason": "handler_not_found"}
    result = handler_cls().process(message, decision)
    return {"handled": True, "result": result}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.send_templated")
def send_templated_email_task(self, mailbox_id: int, template_code: str, variables: dict, to_recipients: list, tenant_id=None):
    mailbox = MailboxService.get_mailbox(mailbox_id)
    if mailbox is None:
        return {"sent": False, "reason": "mailbox_not_found"}
    result = OutboundEmailService.send_templated_email(
        tenant=mailbox.tenant,
        mailbox=mailbox,
        template_code=template_code,
        variables=variables or {},
        to_recipients=to_recipients or [],
    )
    return {"sent": True, "result": result}


@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("email.send_clarification")
def send_clarification_email_task(self, mailbox_id: int, subject: str, body_text: str, to_recipients: list):
    mailbox = MailboxService.get_mailbox(mailbox_id)
    if mailbox is None:
        return {"sent": False, "reason": "mailbox_not_found"}
    payload = {"subject": subject, "body_text": body_text, "body_html": "", "to": to_recipients or []}
    result = MailboxService.get_adapter(mailbox).send_message(mailbox, payload)
    EmailAction.objects.create(
        tenant=mailbox.tenant,
        action_type=EmailActionType.SEND_CLARIFICATION_EMAIL,
        action_status=EmailActionStatus.COMPLETED,
        payload_json=payload,
        result_json=result,
    )
    return {"sent": True, "result": result}


@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
@observed_task("email.relink_threads")
def relink_email_threads_task(self, tenant_id=None):
    tenant = None
    if tenant_id:
        tenant = MailboxConfig.objects.filter(tenant_id=tenant_id).values_list("tenant", flat=True).first()
    return EmailProcessingService.relink_threads(tenant=tenant)


@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
@observed_task("email.retry_failed_actions")
def retry_failed_email_actions_task(self, tenant_id=None):
    tenant = None
    if tenant_id:
        tenant = MailboxConfig.objects.filter(tenant_id=tenant_id).values_list("tenant", flat=True).first()
    return EmailProcessingService.retry_failed_actions(tenant=tenant)


@shared_task(bind=True, max_retries=2, default_retry_delay=120, acks_late=True)
@observed_task("email.mailbox_health_check")
def mailbox_health_check_task(self, tenant_id=None):
    mailboxes = MailboxConfig.objects.filter(is_active=True)
    if tenant_id:
        mailboxes = mailboxes.filter(tenant_id=tenant_id)
    report = []
    now = timezone.now()
    for mailbox in mailboxes:
        minutes_since_success = None
        if mailbox.last_success_at:
            minutes_since_success = int((now - mailbox.last_success_at).total_seconds() // 60)
        report.append(
            {
                "mailbox_id": mailbox.pk,
                "mailbox_address": mailbox.mailbox_address,
                "last_success_at": mailbox.last_success_at.isoformat() if mailbox.last_success_at else None,
                "last_error_message": mailbox.last_error_message,
                "minutes_since_success": minutes_since_success,
            }
        )
    return {"mailboxes": report}
