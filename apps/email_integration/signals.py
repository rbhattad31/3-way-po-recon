"""Signals for asynchronous post-receive email processing hooks."""
from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.email_integration.enums import EmailDirection, EmailProcessingStatus
from apps.email_integration.models import EmailMessage


@receiver(post_save, sender=EmailMessage)
def mark_inbound_received(sender, instance: EmailMessage, created: bool, **kwargs):
    """Normalize initial state transitions for newly-created inbound emails."""
    if not created:
        return
    if instance.direction != EmailDirection.INBOUND:
        return
    if instance.processing_status == EmailProcessingStatus.RECEIVED:
        instance.processing_status = EmailProcessingStatus.NORMALIZED
        instance.save(update_fields=["processing_status", "updated_at"])
