"""Enumerations for the email integration app."""
from django.db import models


class EmailProvider(models.TextChoices):
    MICROSOFT_365 = "MICROSOFT_365", "Microsoft 365"
    GMAIL = "GMAIL", "Gmail"


class MailboxType(models.TextChoices):
    SHARED = "SHARED", "Shared"
    USER = "USER", "User"
    SYSTEM = "SYSTEM", "System"


class MailboxAuthMode(models.TextChoices):
    OAUTH = "OAUTH", "OAuth"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT", "Service Account"
    APP_REGISTRATION = "APP_REGISTRATION", "App Registration"


class TargetDomain(models.TextChoices):
    AP = "AP", "AP"
    PROCUREMENT = "PROCUREMENT", "Procurement"
    TRIAGE = "TRIAGE", "Triage"
    NOTIFICATION_ONLY = "NOTIFICATION_ONLY", "Notification Only"


class EmailThreadStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    CLOSED = "CLOSED", "Closed"
    PENDING_REVIEW = "PENDING_REVIEW", "Pending Review"
    IGNORED = "IGNORED", "Ignored"


class EmailDomainContext(models.TextChoices):
    AP = "AP", "AP"
    PROCUREMENT = "PROCUREMENT", "Procurement"
    UNKNOWN = "UNKNOWN", "Unknown"


class EmailLinkStatus(models.TextChoices):
    UNLINKED = "UNLINKED", "Unlinked"
    LINKED = "LINKED", "Linked"
    AMBIGUOUS = "AMBIGUOUS", "Ambiguous"


class EmailDirection(models.TextChoices):
    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class EmailMessageClassification(models.TextChoices):
    AP_INVOICE = "AP_INVOICE", "AP Invoice"
    AP_SUPPORTING_DOCUMENT = "AP_SUPPORTING_DOCUMENT", "AP Supporting Document"
    PROCUREMENT_QUOTATION = "PROCUREMENT_QUOTATION", "Procurement Quotation"
    PROCUREMENT_PROPOSAL = "PROCUREMENT_PROPOSAL", "Procurement Proposal"
    PROCUREMENT_CLARIFICATION = "PROCUREMENT_CLARIFICATION", "Procurement Clarification"
    APPROVAL_RESPONSE = "APPROVAL_RESPONSE", "Approval Response"
    INTERNAL_REVIEW_REPLY = "INTERNAL_REVIEW_REPLY", "Internal Review Reply"
    GENERAL_QUERY = "GENERAL_QUERY", "General Query"
    UNKNOWN = "UNKNOWN", "Unknown"


class EmailProcessingStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    NORMALIZED = "NORMALIZED", "Normalized"
    ATTACHMENTS_STORED = "ATTACHMENTS_STORED", "Attachments Stored"
    CLASSIFIED = "CLASSIFIED", "Classified"
    LINKED = "LINKED", "Linked"
    ROUTED = "ROUTED", "Routed"
    PROCESSED = "PROCESSED", "Processed"
    FAILED = "FAILED", "Failed"
    IGNORED = "IGNORED", "Ignored"


class EmailRoutingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ROUTED = "ROUTED", "Routed"
    TRIAGED = "TRIAGED", "Triaged"
    FAILED = "FAILED", "Failed"


class SenderTrustLevel(models.TextChoices):
    TRUSTED_INTERNAL = "TRUSTED_INTERNAL", "Trusted Internal"
    TRUSTED_VENDOR = "TRUSTED_VENDOR", "Trusted Vendor"
    KNOWN_EXTERNAL = "KNOWN_EXTERNAL", "Known External"
    UNKNOWN = "UNKNOWN", "Unknown"
    SUSPICIOUS = "SUSPICIOUS", "Suspicious"


class EmailIntentType(models.TextChoices):
    DOCUMENT_INGEST = "DOCUMENT_INGEST", "Document Ingest"
    THREAD_REPLY = "THREAD_REPLY", "Thread Reply"
    APPROVAL_ACTION = "APPROVAL_ACTION", "Approval Action"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE", "Clarification Response"
    NOTIFICATION = "NOTIFICATION", "Notification"
    MANUAL_TRIAGE = "MANUAL_TRIAGE", "Manual Triage"


class EmailParticipantRoleType(models.TextChoices):
    INTERNAL_USER = "INTERNAL_USER", "Internal User"
    VENDOR = "VENDOR", "Vendor"
    SUPPLIER = "SUPPLIER", "Supplier"
    APPROVER = "APPROVER", "Approver"
    UNKNOWN = "UNKNOWN", "Unknown"


class EmailScanStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SAFE = "SAFE", "Safe"
    SUSPICIOUS = "SUSPICIOUS", "Suspicious"
    BLOCKED = "BLOCKED", "Blocked"


class EmailAttachmentProcessingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    STORED = "STORED", "Stored"
    LINKED = "LINKED", "Linked"
    FAILED = "FAILED", "Failed"
    IGNORED = "IGNORED", "Ignored"


class EmailRoutingDecisionType(models.TextChoices):
    RULE_BASED = "RULE_BASED", "Rule Based"
    HYBRID = "HYBRID", "Hybrid"
    MANUAL = "MANUAL", "Manual"


class EmailRoutingDecisionStatus(models.TextChoices):
    PROPOSED = "PROPOSED", "Proposed"
    APPLIED = "APPLIED", "Applied"
    REJECTED = "REJECTED", "Rejected"
    FAILED = "FAILED", "Failed"


class EmailActionType(models.TextChoices):
    CREATE_DOCUMENT_UPLOAD = "CREATE_DOCUMENT_UPLOAD", "Create Document Upload"
    CREATE_AP_CASE = "CREATE_AP_CASE", "Create AP Case"
    LINK_TO_AP_CASE = "LINK_TO_AP_CASE", "Link to AP Case"
    LINK_TO_PROCUREMENT_REQUEST = "LINK_TO_PROCUREMENT_REQUEST", "Link to Procurement Request"
    LINK_TO_SUPPLIER_QUOTATION = "LINK_TO_SUPPLIER_QUOTATION", "Link to Supplier Quotation"
    CREATE_SUPPLIER_QUOTATION = "CREATE_SUPPLIER_QUOTATION", "Create Supplier Quotation"
    TRIGGER_EXTRACTION = "TRIGGER_EXTRACTION", "Trigger Extraction"
    TRIGGER_QUOTATION_PREFILL = "TRIGGER_QUOTATION_PREFILL", "Trigger Quotation Prefill"
    TRIGGER_RECONCILIATION = "TRIGGER_RECONCILIATION", "Trigger Reconciliation"
    TRIGGER_PROCUREMENT_ANALYSIS = "TRIGGER_PROCUREMENT_ANALYSIS", "Trigger Procurement Analysis"
    SEND_OUTBOUND_EMAIL = "SEND_OUTBOUND_EMAIL", "Send Outbound Email"
    SEND_CLARIFICATION_EMAIL = "SEND_CLARIFICATION_EMAIL", "Send Clarification Email"
    SYNC_MAILBOX = "SYNC_MAILBOX", "Sync Mailbox"
    TEST_MAILBOX_CONNECTION = "TEST_MAILBOX_CONNECTION", "Test Mailbox Connection"
    RELINK_THREAD = "RELINK_THREAD", "Relink Thread"
    QUEUE_FOR_TRIAGE = "QUEUE_FOR_TRIAGE", "Queue For Triage"
    REOPEN_ENTITY = "REOPEN_ENTITY", "Reopen Entity"
    IGNORE_EMAIL = "IGNORE_EMAIL", "Ignore Email"


class EmailActionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    BLOCKED = "BLOCKED", "Blocked"


class EmailTemplateDomainScope(models.TextChoices):
    GLOBAL = "GLOBAL", "Global"
    AP = "AP", "AP"
    PROCUREMENT = "PROCUREMENT", "Procurement"
