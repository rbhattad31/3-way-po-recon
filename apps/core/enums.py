"""Enumerations used across the PO Reconciliation system."""
from django.db import models


class InvoiceStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    EXTRACTION_IN_PROGRESS = "EXTRACTION_IN_PROGRESS", "Extraction In Progress"
    EXTRACTED = "EXTRACTED", "Extracted"
    VALIDATED = "VALIDATED", "Validated"
    INVALID = "INVALID", "Invalid"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
    READY_FOR_RECON = "READY_FOR_RECON", "Ready for Reconciliation"
    RECONCILED = "RECONCILED", "Reconciled"
    SUPERSEDED = "SUPERSEDED", "Superseded"
    FAILED = "FAILED", "Failed"


class ExtractionApprovalStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    AUTO_APPROVED = "AUTO_APPROVED", "Auto-Approved"


class ReconciliationMode(models.TextChoices):
    TWO_WAY = "TWO_WAY", "2-Way (Invoice vs PO)"
    THREE_WAY = "THREE_WAY", "3-Way (Invoice vs PO vs GRN)"


class ReconciliationModeApplicability(models.TextChoices):
    TWO_WAY = "TWO_WAY", "2-Way Only"
    THREE_WAY = "THREE_WAY", "3-Way Only"
    BOTH = "BOTH", "Both Modes"


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
    INVOICE_EXTRACTION = "INVOICE_EXTRACTION", "Invoice Extraction"
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
    # Common (TWO_WAY + THREE_WAY)
    PO_NOT_FOUND = "PO_NOT_FOUND", "PO Not Found"
    VENDOR_MISMATCH = "VENDOR_MISMATCH", "Vendor Mismatch"
    ITEM_MISMATCH = "ITEM_MISMATCH", "Item Mismatch"
    QTY_MISMATCH = "QTY_MISMATCH", "Quantity Mismatch"
    PRICE_MISMATCH = "PRICE_MISMATCH", "Price Mismatch"
    TAX_MISMATCH = "TAX_MISMATCH", "Tax Mismatch"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount Mismatch"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE", "Duplicate Invoice"
    EXTRACTION_LOW_CONFIDENCE = "EXTRACTION_LOW_CONFIDENCE", "Extraction Low Confidence"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH", "Currency Mismatch"
    LOCATION_MISMATCH = "LOCATION_MISMATCH", "Location Mismatch"
    # THREE_WAY-specific (receipt/GRN related)
    GRN_NOT_FOUND = "GRN_NOT_FOUND", "GRN Not Found"
    RECEIPT_SHORTAGE = "RECEIPT_SHORTAGE", "Receipt Shortage"
    INVOICE_QTY_EXCEEDS_RECEIVED = "INVOICE_QTY_EXCEEDS_RECEIVED", "Invoice Qty Exceeds Received Qty"
    OVER_RECEIPT = "OVER_RECEIPT", "Over Receipt"
    MULTI_GRN_PARTIAL_RECEIPT = "MULTI_GRN_PARTIAL_RECEIPT", "Multi-GRN Partial Receipt"
    RECEIPT_LOCATION_MISMATCH = "RECEIPT_LOCATION_MISMATCH", "Receipt Location Mismatch"
    DELAYED_RECEIPT = "DELAYED_RECEIPT", "Delayed Receipt"


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    AP_PROCESSOR = "AP_PROCESSOR", "AP Processor"
    REVIEWER = "REVIEWER", "Reviewer"
    FINANCE_MANAGER = "FINANCE_MANAGER", "Finance Manager"
    AUDITOR = "AUDITOR", "Auditor"


class DocumentType(models.TextChoices):
    INVOICE = "INVOICE", "Invoice"
    CREDIT_NOTE = "CREDIT_NOTE", "Credit Note"
    DEBIT_NOTE = "DEBIT_NOTE", "Debit Note"
    DELIVERY_NOTE = "DELIVERY_NOTE", "Delivery Note"
    STATEMENT = "STATEMENT", "Statement"
    PURCHASE_ORDER = "PO", "Purchase Order"
    GRN = "GRN", "Goods Receipt Note"
    PROCUREMENT_RFQ = "PROCUREMENT_RFQ", "Procurement RFQ"
    PROCUREMENT_QUOTATION = "PROCUREMENT_QUOTATION", "Procurement Quotation"


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
    RECONCILIATION_MODE_RESOLVED = "RECONCILIATION_MODE_RESOLVED", "Reconciliation Mode Resolved"
    POLICY_APPLIED = "POLICY_APPLIED", "Policy Applied"
    MANUAL_MODE_OVERRIDE = "MANUAL_MODE_OVERRIDE", "Manual Mode Override"
    # RBAC audit events
    USER_CREATED = "USER_CREATED", "User Created"
    USER_UPDATED = "USER_UPDATED", "User Updated"
    ROLE_ASSIGNED = "ROLE_ASSIGNED", "Role Assigned"
    ROLE_REMOVED = "ROLE_REMOVED", "Role Removed"
    ROLE_PERMISSION_CHANGED = "ROLE_PERMISSION_CHANGED", "Role Permission Changed"
    USER_PERMISSION_OVERRIDE = "USER_PERMISSION_OVERRIDE", "User Permission Override"
    OVERRIDE_REMOVED = "OVERRIDE_REMOVED", "Permission Override Removed"
    USER_ACTIVATED = "USER_ACTIVATED", "User Activated"
    USER_DEACTIVATED = "USER_DEACTIVATED", "User Deactivated"
    ROLE_CREATED = "ROLE_CREATED", "Role Created"
    ROLE_UPDATED = "ROLE_UPDATED", "Role Updated"
    ROLE_DEACTIVATED = "ROLE_DEACTIVATED", "Role Deactivated"
    PRIMARY_ROLE_CHANGED = "PRIMARY_ROLE_CHANGED", "Primary Role Changed"
    # Case management events
    CASE_ASSIGNED = "CASE_ASSIGNED", "Case Assigned"
    CASE_CLOSED = "CASE_CLOSED", "Case Closed"
    CASE_REJECTED = "CASE_REJECTED", "Case Rejected"
    CASE_REPROCESSED = "CASE_REPROCESSED", "Case Reprocessed"
    CASE_ESCALATED = "CASE_ESCALATED", "Case Escalated"
    CASE_FAILED = "CASE_FAILED", "Case Failed"
    CASE_STATUS_CHANGED = "CASE_STATUS_CHANGED", "Case Status Changed"
    COMMENT_ADDED = "COMMENT_ADDED", "Comment Added"
    # Review lifecycle events
    REVIEWER_ASSIGNED = "REVIEWER_ASSIGNED", "Reviewer Assigned"
    REVIEW_STARTED = "REVIEW_STARTED", "Review Started"
    # Agent guardrail events
    GUARDRAIL_GRANTED = "GUARDRAIL_GRANTED", "Guardrail Granted"
    GUARDRAIL_DENIED = "GUARDRAIL_DENIED", "Guardrail Denied"
    TOOL_CALL_AUTHORIZED = "TOOL_CALL_AUTHORIZED", "Tool Call Authorized"
    TOOL_CALL_DENIED = "TOOL_CALL_DENIED", "Tool Call Denied"
    RECOMMENDATION_ACCEPTED = "RECOMMENDATION_ACCEPTED", "Recommendation Accepted"
    RECOMMENDATION_DENIED = "RECOMMENDATION_DENIED", "Recommendation Denied"
    AUTO_CLOSE_AUTHORIZED = "AUTO_CLOSE_AUTHORIZED", "Auto-Close Authorized"
    AUTO_CLOSE_DENIED = "AUTO_CLOSE_DENIED", "Auto-Close Denied"
    SYSTEM_AGENT_USED = "SYSTEM_AGENT_USED", "System Agent Used"
    # Copilot audit events
    COPILOT_SESSION_CREATED = "COPILOT_SESSION_CREATED", "Copilot Session Created"
    COPILOT_SESSION_VIEWED = "COPILOT_SESSION_VIEWED", "Copilot Session Viewed"
    COPILOT_SESSION_RESUMED = "COPILOT_SESSION_RESUMED", "Copilot Session Resumed"
    COPILOT_SESSION_ARCHIVED = "COPILOT_SESSION_ARCHIVED", "Copilot Session Archived"
    COPILOT_MESSAGE_SENT = "COPILOT_MESSAGE_SENT", "Copilot Message Sent"
    COPILOT_RESPONSE_GENERATED = "COPILOT_RESPONSE_GENERATED", "Copilot Response Generated"
    COPILOT_CASE_CONTEXT_LOADED = "COPILOT_CASE_CONTEXT_LOADED", "Copilot Case Context Loaded"
    COPILOT_GOVERNANCE_CONTEXT_VIEWED = "COPILOT_GOVERNANCE_CONTEXT_VIEWED", "Copilot Governance Context Viewed"
    COPILOT_UNAUTHORIZED_GOVERNANCE_REQUEST = "COPILOT_UNAUTHORIZED_GOVERNANCE_REQUEST", "Copilot Unauthorized Governance Request"
    COPILOT_SENSITIVE_FIELD_REDACTED = "COPILOT_SENSITIVE_FIELD_REDACTED", "Copilot Sensitive Field Redacted"
    # Extraction pipeline events
    EXTRACTION_STARTED = "EXTRACTION_STARTED", "Extraction Started"
    INVOICE_PERSISTED = "INVOICE_PERSISTED", "Invoice Persisted"
    EXTRACTION_RESULT_PERSISTED = "EXTRACTION_RESULT_PERSISTED", "Extraction Result Persisted"
    DUPLICATE_DETECTED = "DUPLICATE_DETECTED", "Duplicate Detected"
    VENDOR_RESOLVED = "VENDOR_RESOLVED", "Vendor Resolved"
    # Extraction approval events
    EXTRACTION_APPROVAL_PENDING = "EXTRACTION_APPROVAL_PENDING", "Extraction Approval Pending"
    EXTRACTION_APPROVED = "EXTRACTION_APPROVED", "Extraction Approved"
    EXTRACTION_REJECTED = "EXTRACTION_REJECTED", "Extraction Rejected"
    EXTRACTION_AUTO_APPROVED = "EXTRACTION_AUTO_APPROVED", "Extraction Auto-Approved"
    EXTRACTION_FIELD_CORRECTED = "EXTRACTION_FIELD_CORRECTED", "Extraction Field Corrected"
    # Extraction platform governance events
    JURISDICTION_RESOLVED = "JURISDICTION_RESOLVED", "Jurisdiction Resolved"
    SCHEMA_SELECTED = "SCHEMA_SELECTED", "Schema Selected"
    PROMPT_SELECTED = "PROMPT_SELECTED", "Prompt Selected"
    NORMALIZATION_COMPLETED = "NORMALIZATION_COMPLETED", "Normalization Completed"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED", "Validation Completed"
    EVIDENCE_CAPTURED = "EVIDENCE_CAPTURED", "Evidence Captured"
    REVIEW_ROUTE_ASSIGNED = "REVIEW_ROUTE_ASSIGNED", "Review Route Assigned"
    EXTRACTION_REPROCESSED = "EXTRACTION_REPROCESSED", "Extraction Reprocessed"
    EXTRACTION_ESCALATED = "EXTRACTION_ESCALATED", "Extraction Escalated"
    EXTRACTION_COMMENT_ADDED = "EXTRACTION_COMMENT_ADDED", "Extraction Comment Added"
    SETTINGS_UPDATED = "SETTINGS_UPDATED", "Settings Updated"
    SCHEMA_UPDATED = "SCHEMA_UPDATED", "Schema Updated"
    PROMPT_UPDATED = "PROMPT_UPDATED", "Prompt Updated"
    ROUTING_RULE_UPDATED = "ROUTING_RULE_UPDATED", "Routing Rule Updated"
    ANALYTICS_SNAPSHOT_CREATED = "ANALYTICS_SNAPSHOT_CREATED", "Analytics Snapshot Created"
    # Credit management events
    CREDIT_CHECKED = "CREDIT_CHECKED", "Credit Checked"
    CREDIT_RESERVED = "CREDIT_RESERVED", "Credit Reserved"
    CREDIT_CONSUMED = "CREDIT_CONSUMED", "Credit Consumed"
    CREDIT_REFUNDED = "CREDIT_REFUNDED", "Credit Refunded"
    CREDIT_ALLOCATION_UPDATED = "CREDIT_ALLOCATION_UPDATED", "Credit Allocation Updated"
    CREDIT_LIMIT_EXCEEDED = "CREDIT_LIMIT_EXCEEDED", "Credit Limit Exceeded"
    CREDIT_MONTHLY_RESET = "CREDIT_MONTHLY_RESET", "Credit Monthly Reset"


class PermissionOverrideType(models.TextChoices):
    ALLOW = "ALLOW", "Allow"
    DENY = "DENY", "Deny"


# ---------------------------------------------------------------------------
# Case-centric enums (APCase platform)
# ---------------------------------------------------------------------------


class InvoiceType(models.TextChoices):
    PO_BACKED = "PO_BACKED", "PO-Backed"
    NON_PO = "NON_PO", "Non-PO"
    UNKNOWN = "UNKNOWN", "Unknown"


class ProcessingPath(models.TextChoices):
    TWO_WAY = "TWO_WAY", "2-Way Matching"
    THREE_WAY = "THREE_WAY", "3-Way Reconciliation"
    NON_PO = "NON_PO", "Non-PO Validation"
    UNRESOLVED = "UNRESOLVED", "Unresolved"


class CaseStatus(models.TextChoices):
    NEW = "NEW", "New"
    INTAKE_IN_PROGRESS = "INTAKE_IN_PROGRESS", "Intake In Progress"
    EXTRACTION_IN_PROGRESS = "EXTRACTION_IN_PROGRESS", "Extraction In Progress"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED", "Extraction Completed"
    PATH_RESOLUTION_IN_PROGRESS = "PATH_RESOLUTION_IN_PROGRESS", "Path Resolution In Progress"
    TWO_WAY_IN_PROGRESS = "TWO_WAY_IN_PROGRESS", "2-Way Matching In Progress"
    THREE_WAY_IN_PROGRESS = "THREE_WAY_IN_PROGRESS", "3-Way Reconciliation In Progress"
    NON_PO_VALIDATION_IN_PROGRESS = "NON_PO_VALIDATION_IN_PROGRESS", "Non-PO Validation In Progress"
    GRN_ANALYSIS_IN_PROGRESS = "GRN_ANALYSIS_IN_PROGRESS", "GRN Analysis In Progress"
    EXCEPTION_ANALYSIS_IN_PROGRESS = "EXCEPTION_ANALYSIS_IN_PROGRESS", "Exception Analysis In Progress"
    READY_FOR_REVIEW = "READY_FOR_REVIEW", "Ready for Review"
    IN_REVIEW = "IN_REVIEW", "In Review"
    REVIEW_COMPLETED = "REVIEW_COMPLETED", "Review Completed"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL", "Ready for Approval"
    APPROVAL_IN_PROGRESS = "APPROVAL_IN_PROGRESS", "Approval In Progress"
    READY_FOR_GL_CODING = "READY_FOR_GL_CODING", "Ready for GL Coding"
    READY_FOR_POSTING = "READY_FOR_POSTING", "Ready for Posting"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"
    ESCALATED = "ESCALATED", "Escalated"
    FAILED = "FAILED", "Failed"


class CaseStageType(models.TextChoices):
    INTAKE = "INTAKE", "Intake"
    EXTRACTION = "EXTRACTION", "Extraction"
    PATH_RESOLUTION = "PATH_RESOLUTION", "Path Resolution"
    PO_RETRIEVAL = "PO_RETRIEVAL", "PO Retrieval"
    TWO_WAY_MATCHING = "TWO_WAY_MATCHING", "2-Way Matching"
    THREE_WAY_MATCHING = "THREE_WAY_MATCHING", "3-Way Matching"
    GRN_ANALYSIS = "GRN_ANALYSIS", "GRN Analysis"
    NON_PO_VALIDATION = "NON_PO_VALIDATION", "Non-PO Validation"
    EXCEPTION_ANALYSIS = "EXCEPTION_ANALYSIS", "Exception Analysis"
    REVIEW_ROUTING = "REVIEW_ROUTING", "Review Routing"
    CASE_SUMMARY = "CASE_SUMMARY", "Case Summary"
    REVIEWER_COPILOT = "REVIEWER_COPILOT", "Reviewer Copilot"
    APPROVAL = "APPROVAL", "Approval"
    GL_CODING = "GL_CODING", "GL Coding"
    POSTING = "POSTING", "Posting"


class StageStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"
    WAITING_HUMAN = "WAITING_HUMAN", "Waiting for Human"


class PerformedByType(models.TextChoices):
    SYSTEM = "SYSTEM", "System"
    DETERMINISTIC = "DETERMINISTIC", "Deterministic Engine"
    AGENT = "AGENT", "Agent"
    HUMAN = "HUMAN", "Human"


class ArtifactType(models.TextChoices):
    EXTRACTION_RESULT = "EXTRACTION_RESULT", "Extraction Result"
    PO_LINK = "PO_LINK", "PO Link"
    GRN_LINK = "GRN_LINK", "GRN Link"
    RECONCILIATION_RESULT = "RECONCILIATION_RESULT", "Reconciliation Result"
    VALIDATION_RESULT = "VALIDATION_RESULT", "Validation Result"
    AGENT_OUTPUT = "AGENT_OUTPUT", "Agent Output"
    REVIEW_DECISION = "REVIEW_DECISION", "Review Decision"
    SUPPORTING_DOCUMENT = "SUPPORTING_DOCUMENT", "Supporting Document"
    APPROVAL_PACKET = "APPROVAL_PACKET", "Approval Packet"
    GL_CODING_PROPOSAL = "GL_CODING_PROPOSAL", "GL Coding Proposal"


class DecisionType(models.TextChoices):
    PATH_SELECTED = "PATH_SELECTED", "Processing Path Selected"
    PATH_REROUTED = "PATH_REROUTED", "Processing Path Rerouted"
    PO_LINKED = "PO_LINKED", "PO Linked"
    GRN_LINKED = "GRN_LINKED", "GRN Linked"
    MATCH_DETERMINED = "MATCH_DETERMINED", "Match Status Determined"
    EXCEPTION_CLASSIFIED = "EXCEPTION_CLASSIFIED", "Exception Classified"
    AUTO_CLOSED = "AUTO_CLOSED", "Auto-Closed"
    SENT_TO_REVIEW = "SENT_TO_REVIEW", "Sent to Review"
    REVIEW_COMPLETED = "REVIEW_COMPLETED", "Review Completed"
    ESCALATED = "ESCALATED", "Escalated"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    GL_CODE_PROPOSED = "GL_CODE_PROPOSED", "GL Code Proposed"


class DecisionSource(models.TextChoices):
    DETERMINISTIC = "DETERMINISTIC", "Deterministic"
    POLICY = "POLICY", "Policy Rule"
    AGENT = "AGENT", "Agent"
    HUMAN = "HUMAN", "Human"


class AssignmentType(models.TextChoices):
    REVIEW = "REVIEW", "Review"
    APPROVAL = "APPROVAL", "Approval"
    INVESTIGATION = "INVESTIGATION", "Investigation"
    CORRECTION = "CORRECTION", "Correction"


class AssignmentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ASSIGNED = "ASSIGNED", "Assigned"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    ESCALATED = "ESCALATED", "Escalated"
    CANCELLED = "CANCELLED", "Cancelled"


class CasePriority(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class BudgetCheckStatus(models.TextChoices):
    NOT_CHECKED = "NOT_CHECKED", "Not Checked"
    WITHIN_BUDGET = "WITHIN_BUDGET", "Within Budget"
    OVER_BUDGET = "OVER_BUDGET", "Over Budget"
    NO_BUDGET_DATA = "NO_BUDGET_DATA", "No Budget Data"


class CodingStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not Started"
    PROPOSED = "PROPOSED", "Proposed"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"


class SourceChannel(models.TextChoices):
    WEB_UPLOAD = "WEB_UPLOAD", "Web Upload"
    EMAIL = "EMAIL", "Email"
    API = "API", "API"
    ERP_IMPORT = "ERP_IMPORT", "ERP Import"
    SCAN = "SCAN", "Scan"


# ---------------------------------------------------------------------------
# Copilot enums
# ---------------------------------------------------------------------------


class CopilotSessionStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    ARCHIVED = "ARCHIVED", "Archived"


class CopilotMessageType(models.TextChoices):
    USER = "USER", "User"
    ASSISTANT = "ASSISTANT", "Assistant"
    SYSTEM = "SYSTEM", "System"


class CopilotArtifactType(models.TextChoices):
    CASE_SNAPSHOT = "CASE_SNAPSHOT", "Case Snapshot"
    EVIDENCE_CARD = "EVIDENCE_CARD", "Evidence Card"
    RECOMMENDATION = "RECOMMENDATION", "Recommendation"
    GOVERNANCE_TRACE = "GOVERNANCE_TRACE", "Governance Trace"


# ---------------------------------------------------------------------------
# Procurement Intelligence enums
# ---------------------------------------------------------------------------


class ProcurementRequestType(models.TextChoices):
    RECOMMENDATION = "RECOMMENDATION", "Product / Solution Recommendation"
    BENCHMARK = "BENCHMARK", "Should-Cost Benchmarking"
    BOTH = "BOTH", "Recommendation + Benchmarking"


class ProcurementRequestStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    READY = "READY", "Ready"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review Required"
    FAILED = "FAILED", "Failed"


class AnalysisRunType(models.TextChoices):
    RECOMMENDATION = "RECOMMENDATION", "Recommendation"
    BENCHMARK = "BENCHMARK", "Benchmark"
    VALIDATION = "VALIDATION", "Validation"


class AnalysisRunStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class ExtractionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class ComplianceStatus(models.TextChoices):
    PASS = "PASS", "Pass"
    FAIL = "FAIL", "Fail"
    PARTIAL = "PARTIAL", "Partial"
    NOT_CHECKED = "NOT_CHECKED", "Not Checked"


class VarianceStatus(models.TextChoices):
    WITHIN_RANGE = "WITHIN_RANGE", "Within Range"
    ABOVE_BENCHMARK = "ABOVE_BENCHMARK", "Above Benchmark"
    BELOW_BENCHMARK = "BELOW_BENCHMARK", "Below Benchmark"
    SIGNIFICANTLY_ABOVE = "SIGNIFICANTLY_ABOVE", "Significantly Above"


class BenchmarkRiskLevel(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


# ---------------------------------------------------------------------------
# Validation Framework enums
# ---------------------------------------------------------------------------


class ValidationType(models.TextChoices):
    ATTRIBUTE_COMPLETENESS = "ATTRIBUTE_COMPLETENESS", "Attribute Completeness"
    DOCUMENT_COMPLETENESS = "DOCUMENT_COMPLETENESS", "Document Completeness"
    SCOPE_COVERAGE = "SCOPE_COVERAGE", "Scope Coverage"
    AMBIGUITY_CHECK = "AMBIGUITY_CHECK", "Ambiguity Check"
    COMMERCIAL_COMPLETENESS = "COMMERCIAL_COMPLETENESS", "Commercial Completeness"
    COMPLIANCE_READINESS = "COMPLIANCE_READINESS", "Compliance Readiness"


class ValidationOverallStatus(models.TextChoices):
    PASS = "PASS", "Pass"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS", "Pass with Warnings"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review Required"
    FAIL = "FAIL", "Fail"


class ValidationRuleType(models.TextChoices):
    REQUIRED_ATTRIBUTE = "REQUIRED_ATTRIBUTE", "Required Attribute"
    REQUIRED_DOCUMENT = "REQUIRED_DOCUMENT", "Required Document"
    REQUIRED_CATEGORY = "REQUIRED_CATEGORY", "Required Category"
    AMBIGUITY_PATTERN = "AMBIGUITY_PATTERN", "Ambiguity Pattern"
    COMMERCIAL_CHECK = "COMMERCIAL_CHECK", "Commercial Check"
    COMPLIANCE_CHECK = "COMPLIANCE_CHECK", "Compliance Check"


class ValidationSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"
    CRITICAL = "CRITICAL", "Critical"


class ValidationEvaluationMode(models.TextChoices):
    DETERMINISTIC = "DETERMINISTIC", "Deterministic"
    AGENT_ASSISTED = "AGENT_ASSISTED", "Agent-Assisted"


class ValidationItemStatus(models.TextChoices):
    PRESENT = "PRESENT", "Present"
    MISSING = "MISSING", "Missing"
    WARNING = "WARNING", "Warning"
    AMBIGUOUS = "AMBIGUOUS", "Ambiguous"
    FAILED = "FAILED", "Failed"


class ValidationSourceType(models.TextChoices):
    ATTRIBUTE = "ATTRIBUTE", "Attribute"
    DOCUMENT = "DOCUMENT", "Document"
    LINE_ITEM = "LINE_ITEM", "Line Item"
    RULE = "RULE", "Rule"
    AGENT = "AGENT", "Agent"


class ValidationNextAction(models.TextChoices):
    READY_FOR_RECOMMENDATION = "READY_FOR_RECOMMENDATION", "Ready for Recommendation"
    READY_FOR_BENCHMARKING = "READY_FOR_BENCHMARKING", "Ready for Benchmarking"
    REQUEST_REFINEMENT = "REQUEST_REFINEMENT", "Request Refinement"


class CreditTransactionType(models.TextChoices):
    ALLOCATE = "ALLOCATE", "Allocate"
    RESERVE = "RESERVE", "Reserve"
    CONSUME = "CONSUME", "Consume"
    REFUND = "REFUND", "Refund"
    ADJUST = "ADJUST", "Adjust"
    MONTHLY_RESET = "MONTHLY_RESET", "Monthly Reset"
    NEEDS_TECHNICAL_REVIEW = "NEEDS_TECHNICAL_REVIEW", "Needs Technical Review"
    NEEDS_COMMERCIAL_REVIEW = "NEEDS_COMMERCIAL_REVIEW", "Needs Commercial Review"


class AttributeDataType(models.TextChoices):
    TEXT = "TEXT", "Text"
    NUMBER = "NUMBER", "Number"
    BOOLEAN = "BOOLEAN", "Boolean"
    JSON = "JSON", "JSON"
    DATE = "DATE", "Date"
    SELECT = "SELECT", "Select"


# ---------------------------------------------------------------------------
# Prefill / PDF-led extraction enums
# ---------------------------------------------------------------------------


class PrefillStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not Started"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    REVIEW_PENDING = "REVIEW_PENDING", "Review Pending"


class SourceDocumentType(models.TextChoices):
    RFQ = "RFQ", "RFQ"
    REQUIREMENT_NOTE = "REQUIREMENT_NOTE", "Requirement Note"
    SPECIFICATION = "SPECIFICATION", "Specification"
    BOQ = "BOQ", "BOQ"
    PROPOSAL = "PROPOSAL", "Proposal"
    QUOTATION = "QUOTATION", "Quotation"
    OTHER = "OTHER", "Other"


class ExtractionSourceType(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    PREFILL = "PREFILL", "Prefill"
    SYSTEM = "SYSTEM", "System"


# ---------------------------------------------------------------------------
# Multi-Country Extraction Platform enums
# ---------------------------------------------------------------------------


class TaxRegime(models.TextChoices):
    GST = "GST", "Goods and Services Tax (India)"
    VAT_UAE = "VAT_UAE", "Value Added Tax (UAE)"
    VAT_SA = "VAT_SA", "Value Added Tax (Saudi Arabia)"
    VAT_EU = "VAT_EU", "Value Added Tax (EU)"
    NONE = "NONE", "No Tax Regime"


class JurisdictionResolutionMethod(models.TextChoices):
    TAX_ID_REGEX = "TAX_ID_REGEX", "Tax ID Regex Match"
    CURRENCY_DETECTION = "CURRENCY_DETECTION", "Currency Detection"
    ADDRESS_KEYWORDS = "ADDRESS_KEYWORDS", "Address Keywords"
    EXPLICIT = "EXPLICIT", "Explicitly Provided"
    LLM_FALLBACK = "LLM_FALLBACK", "LLM Fallback"
    MANUAL = "MANUAL", "Manual Override"


class ExtractionDocumentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CLASSIFYING = "CLASSIFYING", "Classifying"
    EXTRACTING = "EXTRACTING", "Extracting"
    NORMALIZING = "NORMALIZING", "Normalizing"
    VALIDATING = "VALIDATING", "Validating"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class FieldExtractionMethod(models.TextChoices):
    DETERMINISTIC = "DETERMINISTIC", "Deterministic (regex/rule)"
    LLM = "LLM", "LLM-based"
    HYBRID = "HYBRID", "Hybrid (rule + LLM)"
    MANUAL = "MANUAL", "Manual Override"


class JurisdictionMode(models.TextChoices):
    """How the system resolves jurisdiction for extraction."""
    AUTO = "AUTO", "Auto-Detect"
    FIXED = "FIXED", "Fixed (configured)"
    HYBRID = "HYBRID", "Hybrid (configured + detection fallback)"


class JurisdictionSource(models.TextChoices):
    """Where the resolved jurisdiction came from."""
    DOCUMENT_OVERRIDE = "DOCUMENT_OVERRIDE", "Document-Level Override"
    ENTITY_PROFILE = "ENTITY_PROFILE", "Entity Extraction Profile"
    SYSTEM_SETTINGS = "SYSTEM_SETTINGS", "System Runtime Settings"
    AUTO_DETECTED = "AUTO_DETECTED", "Auto-Detected"
    HYBRID_CONFIGURED = "HYBRID_CONFIGURED", "Hybrid — Configured Primary"
    HYBRID_FALLBACK = "HYBRID_FALLBACK", "Hybrid — Detection Fallback"
    FIXED = "FIXED", "Fixed (System Settings)"
    ENTITY = "ENTITY", "Entity Profile"


# ---------------------------------------------------------------------------
# Extraction Platform Upgrade enums
# ---------------------------------------------------------------------------


class ExtractionRunStatus(models.TextChoices):
    """Lifecycle status of an ExtractionRun."""
    PENDING = "PENDING", "Pending"
    JURISDICTION_RESOLVED = "JURISDICTION_RESOLVED", "Jurisdiction Resolved"
    SCHEMA_SELECTED = "SCHEMA_SELECTED", "Schema Selected"
    PROMPT_BUILT = "PROMPT_BUILT", "Prompt Built"
    EXTRACTING = "EXTRACTING", "Extracting"
    NORMALIZING = "NORMALIZING", "Normalizing"
    VALIDATING = "VALIDATING", "Validating"
    EVIDENCE_CAPTURING = "EVIDENCE_CAPTURING", "Evidence Capturing"
    ROUTING = "ROUTING", "Routing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"


class ExtractionIssueSeverity(models.TextChoices):
    """Severity of an extraction issue."""
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"
    CRITICAL = "CRITICAL", "Critical"


class ReviewQueue(models.TextChoices):
    """Review queue classification for extraction review routing."""
    AP_REVIEW = "AP_REVIEW", "AP Review"
    TAX_REVIEW = "TAX_REVIEW", "Tax Review"
    MASTER_DATA_REVIEW = "MASTER_DATA_REVIEW", "Master Data Review"
    EXCEPTION_OPS = "EXCEPTION_OPS", "Exception Ops"
    COMPLIANCE = "COMPLIANCE", "Compliance"


class ExtractionApprovalAction(models.TextChoices):
    """Approval actions for extractions."""
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    ESCALATED = "ESCALATED", "Escalated"
    REPROCESSED = "REPROCESSED", "Reprocessed"


class CountryPackStatus(models.TextChoices):
    """Activation status for country packs."""
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    DEPRECATED = "DEPRECATED", "Deprecated"
