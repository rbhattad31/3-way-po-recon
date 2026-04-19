"""Inbound ingestion service for normalized message persistence and routing."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.core.decorators import observed_service
from apps.email_integration.enums import EmailDirection, EmailProcessingStatus
from apps.email_integration.models import EmailMessage
from apps.email_integration.services.attachment_service import AttachmentService
from apps.email_integration.services.policy_service import EmailPolicyService
from apps.email_integration.services.routing_service import RoutingService
from apps.email_integration.services.thread_linking_service import ThreadLinkingService
from apps.email_integration.services.triage_service import TriageService


class InboundIngestionService:
    """Normalizes inbound provider payloads to canonical email models."""

    @staticmethod
    def _body_preview(body_text: str) -> str:
        return (body_text or "").strip().replace("\n", " ")[:1000]

    @classmethod
    @observed_service("email.inbound.ingest")
    def ingest_message_payload(cls, mailbox, payload: dict, *, tenant=None, actor_user=None) -> EmailMessage:
        provider_message_id = (payload.get("provider_message_id") or payload.get("id") or "").strip()
        internet_message_id = (payload.get("internet_message_id") or "").strip()
        if not provider_message_id:
            raise ValueError("provider_message_id is required")

        with transaction.atomic():
            thread = ThreadLinkingService.get_or_create_thread(mailbox, payload, tenant=tenant)
            existing = None
            if internet_message_id:
                existing = EmailMessage.objects.filter(
                    tenant=tenant,
                    mailbox=mailbox,
                    internet_message_id=internet_message_id,
                ).first()
            if existing is not None:
                return existing
            message, created = EmailMessage.objects.get_or_create(
                tenant=tenant,
                mailbox=mailbox,
                provider_message_id=provider_message_id,
                defaults={
                    "thread": thread,
                    "direction": EmailDirection.INBOUND,
                    "internet_message_id": internet_message_id,
                    "subject": payload.get("subject") or "",
                    "from_email": payload.get("from_email") or "",
                    "from_name": payload.get("from_name") or "",
                    "to_json": payload.get("to") or [],
                    "cc_json": payload.get("cc") or [],
                    "bcc_json": payload.get("bcc") or [],
                    "reply_to_json": payload.get("reply_to") or [],
                    "sent_at": payload.get("sent_at"),
                    "received_at": payload.get("received_at") or timezone.now(),
                    "body_text": payload.get("body_text") or "",
                    "body_html": payload.get("body_html") or "",
                    "body_preview": cls._body_preview(payload.get("body_text") or ""),
                    "has_attachments": bool(payload.get("attachments")),
                    "provider_payload_json": payload,
                    "raw_headers_json": payload.get("headers") or {},
                    "trace_id": payload.get("trace_id") or "",
                    "processing_status": EmailProcessingStatus.NORMALIZED,
                },
            )
            if not created:
                return message

            if not EmailPolicyService.is_sender_allowed(mailbox, message.from_email):
                message.processing_status = EmailProcessingStatus.IGNORED
                message.save(update_fields=["processing_status", "updated_at"])
                return message

            attachments = payload.get("attachments") or []
            if attachments:
                stored = AttachmentService.store_attachments(
                    message,
                    attachments,
                    tenant=tenant,
                    uploaded_by=actor_user,
                    trigger_extraction=True,
                )
                if stored and not message.linked_document_upload_id:
                    linked = next((a.linked_document_upload for a in stored if a.linked_document_upload_id), None)
                    if linked is not None:
                        message.linked_document_upload = linked
                message.processing_status = EmailProcessingStatus.ATTACHMENTS_STORED
                message.save(update_fields=["linked_document_upload", "processing_status", "updated_at"])

            triage_result = TriageService.triage_message(message, mailbox)
            message.message_classification = triage_result["classification"]
            message.intent_type = triage_result["intent"]
            message.sender_trust_level = triage_result["trust_level"]
            message.matched_entity_type = triage_result.get("entity_type") or ""
            message.matched_entity_id = triage_result.get("entity_id")
            message.processing_status = EmailProcessingStatus.CLASSIFIED
            message.save(
                update_fields=[
                    "message_classification",
                    "intent_type",
                    "sender_trust_level",
                    "matched_entity_type",
                    "matched_entity_id",
                    "processing_status",
                    "updated_at",
                ]
            )

            RoutingService.apply_routing(message, triage_result)

            thread.last_message_at = message.received_at or timezone.now()
            thread.message_count = (thread.message_count or 0) + 1
            thread.save(update_fields=["last_message_at", "message_count", "updated_at"])

        return message
