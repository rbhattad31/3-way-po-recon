"""Thread normalization and canonical linking service."""
from __future__ import annotations

from django.utils import timezone

from apps.email_integration.models import EmailThread


class ThreadLinkingService:
    """Builds/updates canonical email threads per mailbox."""

    @staticmethod
    def normalize_subject(subject: str) -> str:
        normalized = (subject or "").strip().lower()
        for prefix in ["re:", "fw:", "fwd:"]:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip()
        return normalized[:500]

    @classmethod
    def get_or_create_thread(cls, mailbox, payload: dict, tenant=None) -> EmailThread:
        provider_thread_id = (payload.get("provider_thread_id") or "").strip()
        internet_conversation_id = (payload.get("internet_conversation_id") or "").strip()
        normalized_subject = cls.normalize_subject(payload.get("subject") or "")

        qs = EmailThread.objects.filter(mailbox=mailbox)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)

        thread = None
        if provider_thread_id:
            thread = qs.filter(provider_thread_id=provider_thread_id).first()
        if thread is None and internet_conversation_id:
            thread = qs.filter(internet_conversation_id=internet_conversation_id).first()
        if thread is None and normalized_subject:
            thread = qs.filter(normalized_subject=normalized_subject).order_by("-last_message_at").first()

        now = timezone.now()
        if thread is None:
            thread = EmailThread.objects.create(
                tenant=tenant,
                mailbox=mailbox,
                provider_thread_id=provider_thread_id,
                internet_conversation_id=internet_conversation_id,
                normalized_subject=normalized_subject,
                first_message_at=payload.get("received_at") or payload.get("sent_at") or now,
                last_message_at=payload.get("received_at") or payload.get("sent_at") or now,
                message_count=0,
            )
        return thread
