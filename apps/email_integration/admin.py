"""Admin registration for email integration models."""
from django.contrib import admin

from apps.email_integration.models import (
    EmailAction,
    EmailAttachment,
    EmailMessage,
    EmailParticipant,
    EmailRoutingDecision,
    EmailTemplate,
    EmailThread,
    MailboxConfig,
)


@admin.register(MailboxConfig)
class MailboxConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "mailbox_address", "provider", "mailbox_type", "is_active", "is_inbound_enabled", "is_outbound_enabled", "last_success_at")
    list_filter = ("provider", "mailbox_type", "is_active", "is_inbound_enabled", "is_outbound_enabled")
    search_fields = ("name", "mailbox_address")
    readonly_fields = ("created_at", "updated_at", "last_sync_at", "last_success_at")


@admin.register(EmailThread)
class EmailThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "mailbox", "normalized_subject", "status", "domain_context", "message_count", "last_message_at")
    list_filter = ("status", "domain_context", "link_status")
    search_fields = ("normalized_subject", "provider_thread_id", "internet_conversation_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "mailbox", "direction", "from_email", "subject", "message_classification", "processing_status", "routing_status", "received_at")
    list_filter = ("direction", "message_classification", "processing_status", "routing_status", "sender_trust_level")
    search_fields = ("subject", "from_email", "provider_message_id", "internet_message_id", "trace_id")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("thread", "linked_document_upload")


@admin.register(EmailAttachment)
class EmailAttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "email_message", "filename", "content_type", "size_bytes", "scan_status", "processing_status")
    list_filter = ("scan_status", "processing_status", "safe_to_process")
    search_fields = ("filename", "provider_attachment_id", "sha256_hash")
    raw_id_fields = ("email_message", "linked_document_upload")


@admin.register(EmailParticipant)
class EmailParticipantAdmin(admin.ModelAdmin):
    list_display = ("id", "thread", "email", "role_type", "trust_level", "linked_vendor_id", "linked_user")
    list_filter = ("role_type", "trust_level")
    search_fields = ("email", "display_name")


@admin.register(EmailRoutingDecision)
class EmailRoutingDecisionAdmin(admin.ModelAdmin):
    list_display = ("id", "email_message", "target_domain", "target_handler", "confidence_score", "deterministic_flag", "final_status")
    list_filter = ("target_domain", "decision_type", "deterministic_flag", "llm_used", "final_status")
    search_fields = ("target_handler", "rule_name", "target_entity_type")


@admin.register(EmailAction)
class EmailActionAdmin(admin.ModelAdmin):
    list_display = ("id", "action_type", "action_status", "target_entity_type", "target_entity_id", "performed_by_user", "created_at")
    list_filter = ("action_type", "action_status")
    search_fields = ("trace_id", "target_entity_type", "target_entity_id")
    raw_id_fields = ("email_message", "thread", "performed_by_user", "performed_by_agent")


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("template_code", "template_name", "domain_scope", "approval_required", "is_active", "updated_at")
    list_filter = ("domain_scope", "approval_required", "is_active")
    search_fields = ("template_code", "template_name")
