"""Enumerations used across the PO Reconciliation system."""
from django.db import models


class InvoiceStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    EXTRACTION_IN_PROGRESS = "EXTRACTION_IN_PROGRESS", "Extraction In Progress"
    EXTRACTED = "EXTRACTED", "Extracted"
    VALIDATED = "VALIDATED", "Validated"
    INVALID = "INVALID", "Invalid"
    READY_FOR_RECON = "READY_FOR_RECON", "Ready for Reconciliation"
    RECONCILED = "RECONCILED", "Reconciled"
    FAILED = "FAILED", "Failed"


class ReconciliationRunStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    PARTIAL = "PARTIAL", "Partial"


class MatchStatus(models.TextChoices):
    MATCHED = "MATCHED", "Matched"
    PARTIAL_MATCH = "PARTIAL_MATCH", "Partial Match"
    UNMATCHED = "UNMATCHED", "Unmatched"
    ERROR = "ERROR", "Error"
    REQUIRES_REVIEW = "REQUIRES_REVIEW", "Requires Review"


class ReviewStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ASSIGNED = "ASSIGNED", "Assigned"
    IN_REVIEW = "IN_REVIEW", "In Review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    REPROCESSED = "REPROCESSED", "Reprocessed"


class ExceptionSeverity(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class AgentRunStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"


class AgentType(models.TextChoices):
    INVOICE_UNDERSTANDING = "INVOICE_UNDERSTANDING", "Invoice Understanding"
    PO_RETRIEVAL = "PO_RETRIEVAL", "PO Retrieval"
    GRN_RETRIEVAL = "GRN_RETRIEVAL", "GRN Retrieval"
    RECONCILIATION_ASSIST = "RECONCILIATION_ASSIST", "Reconciliation Assist"
    EXCEPTION_ANALYSIS = "EXCEPTION_ANALYSIS", "Exception Analysis"
    REVIEW_ROUTING = "REVIEW_ROUTING", "Review Routing"
    CASE_SUMMARY = "CASE_SUMMARY", "Case Summary"


class ToolCallStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"


class RecommendationType(models.TextChoices):
    AUTO_CLOSE = "AUTO_CLOSE", "Auto Close"
    SEND_TO_AP_REVIEW = "SEND_TO_AP_REVIEW", "Send to AP Review"
    SEND_TO_PROCUREMENT = "SEND_TO_PROCUREMENT", "Send to Procurement"
    SEND_TO_VENDOR_CLARIFICATION = "SEND_TO_VENDOR_CLARIFICATION", "Send to Vendor Clarification"
    REPROCESS_EXTRACTION = "REPROCESS_EXTRACTION", "Reprocess Extraction"
    ESCALATE_TO_MANAGER = "ESCALATE_TO_MANAGER", "Escalate to Manager"


class ExceptionType(models.TextChoices):
    PO_NOT_FOUND = "PO_NOT_FOUND", "PO Not Found"
    GRN_NOT_FOUND = "GRN_NOT_FOUND", "GRN Not Found"
    VENDOR_MISMATCH = "VENDOR_MISMATCH", "Vendor Mismatch"
    ITEM_MISMATCH = "ITEM_MISMATCH", "Item Mismatch"
    QTY_MISMATCH = "QTY_MISMATCH", "Quantity Mismatch"
    PRICE_MISMATCH = "PRICE_MISMATCH", "Price Mismatch"
    TAX_MISMATCH = "TAX_MISMATCH", "Tax Mismatch"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount Mismatch"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE", "Duplicate Invoice"
    EXTRACTION_LOW_CONFIDENCE = "EXTRACTION_LOW_CONFIDENCE", "Extraction Low Confidence"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH", "Currency Mismatch"


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    AP_PROCESSOR = "AP_PROCESSOR", "AP Processor"
    REVIEWER = "REVIEWER", "Reviewer"
    FINANCE_MANAGER = "FINANCE_MANAGER", "Finance Manager"
    AUDITOR = "AUDITOR", "Auditor"


class DocumentType(models.TextChoices):
    INVOICE = "INVOICE", "Invoice"
    PURCHASE_ORDER = "PO", "Purchase Order"
    GRN = "GRN", "Goods Receipt Note"


class FileProcessingState(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class ReviewActionType(models.TextChoices):
    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"
    REQUEST_INFO = "REQUEST_INFO", "Request Info"
    REPROCESS = "REPROCESS", "Reprocess"
    ESCALATE = "ESCALATE", "Escalate"
    CORRECT_FIELD = "CORRECT_FIELD", "Correct Field"
    ADD_COMMENT = "ADD_COMMENT", "Add Comment"


class AuditEventType(models.TextChoices):
    INVOICE_UPLOADED = "INVOICE_UPLOADED", "Invoice Uploaded"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED", "Extraction Completed"
    EXTRACTION_FAILED = "EXTRACTION_FAILED", "Extraction Failed"
    VALIDATION_FAILED = "VALIDATION_FAILED", "Validation Failed"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED", "Reconciliation Started"
    RECONCILIATION_COMPLETED = "RECONCILIATION_COMPLETED", "Reconciliation Completed"
    AGENT_RECOMMENDATION_CREATED = "AGENT_RECOMMENDATION_CREATED", "Agent Recommendation Created"
    REVIEW_ASSIGNED = "REVIEW_ASSIGNED", "Review Assigned"
    REVIEW_APPROVED = "REVIEW_APPROVED", "Review Approved"
    REVIEW_REJECTED = "REVIEW_REJECTED", "Review Rejected"
    FIELD_CORRECTED = "FIELD_CORRECTED", "Field Corrected"
    RECONCILIATION_RERUN = "RECONCILIATION_RERUN", "Reconciliation Rerun"
    AGENT_RUN_STARTED = "AGENT_RUN_STARTED", "Agent Run Started"
    AGENT_RUN_COMPLETED = "AGENT_RUN_COMPLETED", "Agent Run Completed"
    AGENT_RUN_FAILED = "AGENT_RUN_FAILED", "Agent Run Failed"
