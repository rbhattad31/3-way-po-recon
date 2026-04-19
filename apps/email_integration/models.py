"""Models for tenant-scoped email integration across AP and Procurement."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel
from apps.email_integration.enums import (
    EmailActionStatus,
    EmailActionType,
    EmailAttachmentProcessingStatus,
    EmailDirection,
    EmailDomainContext,
    EmailIntentType,
    EmailLinkStatus,
    EmailMessageClassification,
    EmailParticipantRoleType,
    EmailProcessingStatus,
    EmailProvider,
    EmailRoutingDecisionStatus,
    EmailRoutingDecisionType,
    EmailRoutingStatus,
    EmailScanStatus,
    EmailTemplateDomainScope,
    EmailThreadStatus,
    MailboxAuthMode,
    MailboxType,
    SenderTrustLevel,
    TargetDomain,
)


class MailboxConfig(BaseModel):
    """A configured mailbox/channel used for inbound and outbound email."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_mailboxes")
    name = models.CharField(max_length=200)
    provider = models.CharField(max_length=30, choices=EmailProvider.choices, db_index=True)
    mailbox_address = models.EmailField(max_length=320)
    mailbox_type = models.CharField(max_length=20, choices=MailboxType.choices, default=MailboxType.SHARED)
    auth_mode = models.CharField(max_length=30, choices=MailboxAuthMode.choices, default=MailboxAuthMode.OAUTH)
    is_inbound_enabled = models.BooleanField(default=True)
    is_outbound_enabled = models.BooleanField(default=False)
    webhook_enabled = models.BooleanField(default=True)
    polling_enabled = models.BooleanField(default=False)
    poll_interval_minutes = models.PositiveIntegerField(default=5)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_message = models.TextField(blank=True, default="")
    default_domain_route = models.CharField(max_length=20, choices=TargetDomain.choices, default=TargetDomain.TRIAGE, db_index=True)
    allowed_sender_domains_json = models.JSONField(default=list, blank=True)
    config_json = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "email_integration_mailbox"
        verbose_name = "Email Mailbox Config"
        verbose_name_plural = "Email Mailbox Configs"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "mailbox_address"], name="uniq_email_mailbox_tenant_address")]
        indexes = [
            models.Index(fields=["tenant", "is_active"], name="idx_eml_mbox_tenant_act"),
            models.Index(fields=["provider", "is_active"], name="idx_eml_mbox_prov_act"),
        ]

    def __str__(self) -> str:
        return f"{self.name} <{self.mailbox_address}>"


class EmailThread(BaseModel):
    """Canonical email thread/conversation record."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_threads")
    mailbox = models.ForeignKey(MailboxConfig, on_delete=models.CASCADE, related_name="threads")
    provider_thread_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    internet_conversation_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    normalized_subject = models.CharField(max_length=500, blank=True, default="", db_index=True)
    first_message_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    message_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=EmailThreadStatus.choices, default=EmailThreadStatus.OPEN, db_index=True)
    domain_context = models.CharField(max_length=20, choices=EmailDomainContext.choices, default=EmailDomainContext.UNKNOWN)
    link_status = models.CharField(max_length=20, choices=EmailLinkStatus.choices, default=EmailLinkStatus.UNLINKED)
    primary_case_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    primary_procurement_request_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    primary_supplier_quotation_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "email_integration_thread"
        verbose_name = "Email Thread"
        verbose_name_plural = "Email Threads"
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"], name="idx_email_thread_tenant_status"),
            models.Index(fields=["mailbox", "provider_thread_id"], name="idx_eml_thr_mbox_provider"),
        ]

    def __str__(self) -> str:
        return f"Thread #{self.pk} ({self.normalized_subject or 'no subject'})"


class EmailMessage(BaseModel):
    """Canonical inbound or outbound email message."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_messages")
    mailbox = models.ForeignKey(MailboxConfig, on_delete=models.CASCADE, related_name="messages")
    thread = models.ForeignKey(EmailThread, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    direction = models.CharField(max_length=20, choices=EmailDirection.choices, db_index=True)
    provider_message_id = models.CharField(max_length=255, db_index=True)
    internet_message_id = models.CharField(max_length=500, blank=True, default="", db_index=True)
    subject = models.CharField(max_length=500, blank=True, default="")
    from_email = models.EmailField(max_length=320, blank=True, default="", db_index=True)
    from_name = models.CharField(max_length=255, blank=True, default="")
    to_json = models.JSONField(default=list, blank=True)
    cc_json = models.JSONField(default=list, blank=True)
    bcc_json = models.JSONField(default=list, blank=True)
    reply_to_json = models.JSONField(default=list, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")
    body_preview = models.CharField(max_length=1000, blank=True, default="")
    has_attachments = models.BooleanField(default=False)
    message_classification = models.CharField(max_length=40, choices=EmailMessageClassification.choices, default=EmailMessageClassification.UNKNOWN, db_index=True)
    processing_status = models.CharField(max_length=30, choices=EmailProcessingStatus.choices, default=EmailProcessingStatus.RECEIVED, db_index=True)
    routing_status = models.CharField(max_length=20, choices=EmailRoutingStatus.choices, default=EmailRoutingStatus.PENDING, db_index=True)
    sender_trust_level = models.CharField(max_length=30, choices=SenderTrustLevel.choices, default=SenderTrustLevel.UNKNOWN)
    intent_type = models.CharField(max_length=30, choices=EmailIntentType.choices, blank=True, default="")
    matched_entity_type = models.CharField(max_length=100, blank=True, default="")
    matched_entity_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    raw_headers_json = models.JSONField(default=dict, blank=True)
    provider_payload_json = models.JSONField(default=dict, blank=True)
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    linked_document_upload = models.ForeignKey("documents.DocumentUpload", on_delete=models.SET_NULL, null=True, blank=True, related_name="email_messages")

    class Meta:
        db_table = "email_integration_message"
        verbose_name = "Email Message"
        verbose_name_plural = "Email Messages"
        ordering = ["-received_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "mailbox", "provider_message_id"], name="uniq_email_message_tenant_mailbox_provider")
        ]
        indexes = [
            models.Index(fields=["tenant", "received_at"], name="idx_email_msg_tenant_received"),
            models.Index(fields=["thread", "received_at"], name="idx_email_msg_thread_received"),
            models.Index(fields=["internet_message_id"], name="idx_email_msg_internet_id"),
        ]

    def __str__(self) -> str:
        return f"Message #{self.pk} ({self.direction}) {self.subject[:60]}"


class EmailAttachment(BaseModel):
    """Attachment metadata and document linking for an email message."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_attachments")
    email_message = models.ForeignKey(EmailMessage, on_delete=models.CASCADE, related_name="attachments")
    provider_attachment_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    filename = models.CharField(max_length=500)
    content_type = models.CharField(max_length=150, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    blob_path = models.CharField(max_length=1200, blank=True, default="")
    blob_url = models.CharField(max_length=2048, blank=True, default="")
    safe_to_process = models.BooleanField(default=True)
    scan_status = models.CharField(max_length=20, choices=EmailScanStatus.choices, default=EmailScanStatus.PENDING)
    classification = models.CharField(max_length=100, blank=True, default="")
    linked_document_upload = models.ForeignKey("documents.DocumentUpload", on_delete=models.SET_NULL, null=True, blank=True, related_name="email_attachments")
    extracted_text_preview = models.CharField(max_length=1000, blank=True, default="")
    processing_status = models.CharField(max_length=20, choices=EmailAttachmentProcessingStatus.choices, default=EmailAttachmentProcessingStatus.PENDING)

    class Meta:
        db_table = "email_integration_attachment"
        verbose_name = "Email Attachment"
        verbose_name_plural = "Email Attachments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "scan_status"], name="idx_email_att_tenant_scan"),
            models.Index(fields=["email_message", "processing_status"], name="idx_email_att_msg_status"),
        ]

    def __str__(self) -> str:
        return f"Attachment #{self.pk} {self.filename}"


class EmailParticipant(BaseModel):
    """Distinct participant identities in an email thread."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_participants")
    thread = models.ForeignKey(EmailThread, on_delete=models.CASCADE, related_name="participants")
    email = models.EmailField(max_length=320, db_index=True)
    display_name = models.CharField(max_length=255, blank=True, default="")
    role_type = models.CharField(max_length=20, choices=EmailParticipantRoleType.choices, default=EmailParticipantRoleType.UNKNOWN)
    trust_level = models.CharField(max_length=30, choices=SenderTrustLevel.choices, default=SenderTrustLevel.UNKNOWN)
    linked_vendor_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    linked_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="email_participants")

    class Meta:
        db_table = "email_integration_participant"
        verbose_name = "Email Participant"
        verbose_name_plural = "Email Participants"
        ordering = ["email"]
        constraints = [models.UniqueConstraint(fields=["thread", "email"], name="uniq_email_participant_thread_email")]

    def __str__(self) -> str:
        return self.email


class EmailRoutingDecision(BaseModel):
    """Stores immutable routing decision records for each email message."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_routing_decisions")
    email_message = models.ForeignKey(EmailMessage, on_delete=models.CASCADE, related_name="routing_decisions")
    decision_type = models.CharField(max_length=20, choices=EmailRoutingDecisionType.choices, default=EmailRoutingDecisionType.RULE_BASED)
    target_domain = models.CharField(max_length=30, choices=TargetDomain.choices, db_index=True)
    target_handler = models.CharField(max_length=100, blank=True, default="")
    target_entity_type = models.CharField(max_length=100, blank=True, default="")
    target_entity_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    confidence_score = models.FloatField(default=0.0)
    deterministic_flag = models.BooleanField(default=True)
    rule_name = models.CharField(max_length=200, blank=True, default="")
    rule_version = models.CharField(max_length=50, blank=True, default="v1")
    llm_used = models.BooleanField(default=False)
    reasoning_summary = models.TextField(blank=True, default="")
    evidence_json = models.JSONField(default=dict, blank=True)
    final_status = models.CharField(max_length=20, choices=EmailRoutingDecisionStatus.choices, default=EmailRoutingDecisionStatus.PROPOSED)

    class Meta:
        db_table = "email_integration_routing_decision"
        verbose_name = "Email Routing Decision"
        verbose_name_plural = "Email Routing Decisions"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "target_domain"], name="idx_email_route_tenant_domain")]

    def __str__(self) -> str:
        return f"Decision #{self.pk} -> {self.target_domain}"


class EmailAction(BaseModel):
    """Action audit ledger for governed inbound and outbound email operations."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_actions")
    email_message = models.ForeignKey(EmailMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name="actions")
    thread = models.ForeignKey(EmailThread, on_delete=models.SET_NULL, null=True, blank=True, related_name="actions")
    action_type = models.CharField(max_length=40, choices=EmailActionType.choices, db_index=True)
    action_status = models.CharField(max_length=20, choices=EmailActionStatus.choices, default=EmailActionStatus.PENDING, db_index=True)
    performed_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="email_actions_performed")
    performed_by_agent = models.ForeignKey("agents.AgentRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="email_actions")
    actor_primary_role = models.CharField(max_length=50, blank=True, default="")
    target_entity_type = models.CharField(max_length=100, blank=True, default="")
    target_entity_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    payload_json = models.JSONField(default=dict, blank=True)
    result_json = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        db_table = "email_integration_action"
        verbose_name = "Email Action"
        verbose_name_plural = "Email Actions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "action_status"], name="idx_email_action_tenant_status"),
            models.Index(fields=["action_type", "created_at"], name="idx_email_action_type_created"),
        ]

    def __str__(self) -> str:
        return f"Action #{self.pk} {self.action_type} ({self.action_status})"


class EmailTemplate(BaseModel):
    """Tenant-aware template definitions for governed outbound email."""

    tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, null=True, blank=True, db_index=True, related_name="email_templates")
    template_code = models.CharField(max_length=120)
    template_name = models.CharField(max_length=200)
    domain_scope = models.CharField(max_length=20, choices=EmailTemplateDomainScope.choices, default=EmailTemplateDomainScope.GLOBAL)
    subject_template = models.TextField()
    body_text_template = models.TextField(blank=True, default="")
    body_html_template = models.TextField(blank=True, default="")
    required_variables_json = models.JSONField(default=list, blank=True)
    approval_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "email_integration_template"
        verbose_name = "Email Template"
        verbose_name_plural = "Email Templates"
        ordering = ["template_code"]
        constraints = [models.UniqueConstraint(fields=["tenant", "template_code"], name="uniq_email_template_tenant_code")]
        indexes = [models.Index(fields=["tenant", "domain_scope", "is_active"], name="idx_eml_tpl_scope_act")]

    def __str__(self) -> str:
        return f"{self.template_code} ({self.domain_scope})"
