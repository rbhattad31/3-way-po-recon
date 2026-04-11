"""Centralized Langfuse evaluation score name taxonomy.

All observation and trace-level score names used across the platform are
defined here as stable string constants.  Services MUST import names from
this module instead of using scattered raw strings.

Naming convention
-----------------
- ALL_CAPS with underscore separators
- Grouped by domain prefix:  EXTRACTION_, RECON_, AGENT_, CASE_, REVIEW_,
  POSTING_, ERP_, plus CROSS_CUTTING_ for shared concerns.
- Values are lowercase underscore strings matching the Langfuse Score name
  (which is also the evaluation metric key).

Adding a new score
------------------
1. Add the constant in the correct domain group.
2. Add a brief inline comment describing the score semantics.
3. Use the constant at the call-site via ``from apps.core.evaluation_constants import X``.
4. Update ``docs/LANGFUSE_INTEGRATION.md`` Score Taxonomy table.
"""
from __future__ import annotations

# =====================================================================
# EXTRACTION
# =====================================================================
EXTRACTION_SUCCESS = "extraction_success"                           # 1.0 / 0.0 -- pipeline completed without fatal error
EXTRACTION_CONFIDENCE = "extraction_confidence"                     # 0.0-1.0  -- overall extraction confidence
EXTRACTION_IS_VALID = "extraction_is_valid"                         # 1.0 / 0.0 -- passed field validation
EXTRACTION_IS_DUPLICATE = "extraction_is_duplicate"                 # 1.0 / 0.0 -- duplicate invoice detected
EXTRACTION_REQUIRES_REVIEW = "extraction_requires_review"           # 1.0 / 0.0 -- routed to human review gate
EXTRACTION_RESPONSE_REPAIRED = "response_was_repaired"              # 1.0 / 0.0 -- LLM response JSON repair applied
EXTRACTION_QR_DETECTED = "qr_detected"                              # 1.0 / 0.0 -- e-invoice QR payload found
EXTRACTION_DECISION_CODE_COUNT = "decision_code_count"              # int as float -- number of decision codes derived
EXTRACTION_WEAKEST_CRITICAL_FIELD_SCORE = "weakest_critical_field_score"  # 0.0-1.0
EXTRACTION_OCR_CHAR_COUNT = "ocr_char_count"                        # int as float -- OCR text length
EXTRACTION_DOC_TYPE_CONFIDENCE = "doc_type_confidence"              # 0.0-1.0 -- document type classification confidence
EXTRACTION_WEAKEST_CRITICAL_SCORE = "weakest_critical_score"        # 0.0-1.0 -- observation-level weakest field
EXTRACTION_VALIDATION_IS_VALID = "validation_is_valid"              # 1.0 / 0.0 -- observation: passed validation
EXTRACTION_RECOVERY_INVOKED = "recovery_invoked"                    # 1.0 / 0.0 -- recovery lane was triggered
EXTRACTION_IS_DUPLICATE_OBS = "is_duplicate"                        # 1.0 / 0.0 -- observation: duplicate found
EXTRACTION_REQUIRES_HUMAN_REVIEW = "requires_human_review"          # 1.0 / 0.0 -- observation: routed to human review
EXTRACTION_AUTO_APPROVE_CONFIDENCE = "extraction_auto_approve_confidence"  # 0.0-1.0
EXTRACTION_APPROVAL_DECISION = "extraction_approval_decision"       # 1.0=approved, 0.0=rejected
EXTRACTION_APPROVAL_CONFIDENCE = "extraction_approval_confidence"   # 0.0-1.0 -- pre-review confidence
EXTRACTION_CORRECTIONS_COUNT = "extraction_corrections_count"       # int as float
EXTRACTION_BULK_JOB_SUCCESS_RATE = "bulk_job_success_rate"          # 0.0-1.0

# =====================================================================
# RECONCILIATION
# =====================================================================
RECON_FINAL_SUCCESS = "recon_final_success"                         # 1.0 / 0.0 -- reconciliation task completed
RECON_FINAL_STATUS_MATCHED = "recon_final_status_matched"           # 1.0 / 0.0
RECON_FINAL_STATUS_PARTIAL_MATCH = "recon_final_status_partial_match"
RECON_FINAL_STATUS_REQUIRES_REVIEW = "recon_final_status_requires_review"
RECON_FINAL_STATUS_UNMATCHED = "recon_final_status_unmatched"
RECON_PO_FOUND = "recon_po_found"                                   # 1.0 / 0.0
RECON_GRN_FOUND = "recon_grn_found"                                 # 1.0 / 0.0
RECON_AUTO_CLOSE_ELIGIBLE = "recon_auto_close_eligible"             # 1.0 / 0.0
RECON_ROUTED_TO_AGENTS = "recon_routed_to_agents"                   # float count
RECON_ROUTED_TO_REVIEW = "recon_routed_to_review"                   # float count
RECON_EXCEPTION_COUNT_FINAL = "recon_exception_count_final"         # int as float
RECON_RECONCILIATION_MATCH = "reconciliation_match"                 # 1.0/0.5/0.3/0.0
RECON_PO_LOOKUP_SUCCESS = "recon_po_lookup_success"                 # 1.0 / 0.0
RECON_GRN_LOOKUP_SUCCESS = "recon_grn_lookup_success"               # 1.0 / 0.0 (observation-level)
RECON_TOLERANCE_PASSED = "recon_tolerance_passed"                   # 1.0 / 0.0
RECON_HEADER_MATCH_RATIO = "recon_header_match_ratio"               # 0.0-1.0
RECON_LINE_MATCH_RATIO = "recon_line_match_ratio"                   # 0.0-1.0
RECON_GRN_MATCH_RATIO = "recon_grn_match_ratio"                     # 0.0-1.0
RECON_PO_LOOKUP_FRESH = "recon_po_lookup_fresh"                     # 1.0 / 0.0 -- observation: PO data not stale
RECON_PO_LOOKUP_AUTHORITATIVE = "recon_po_lookup_authoritative"     # 1.0 / 0.0 -- observation: from API/MIRROR_DB
RECON_GRN_LOOKUP_FRESH = "recon_grn_lookup_fresh"                   # 1.0 / 0.0 -- observation: GRN data not stale
RECON_GRN_LOOKUP_AUTHORITATIVE = "recon_grn_lookup_authoritative"   # 1.0 / 0.0 -- observation: from API/MIRROR_DB
RECON_CLASSIFIED_REQUIRES_REVIEW = "recon_classified_requires_review"  # 1.0 / 0.0 -- observation
RECON_CLASSIFIED_AUTO_CLOSE = "recon_classified_auto_close_candidate"  # 1.0 / 0.0 -- observation
RECON_BLOCKING_EXCEPTION_COUNT = "recon_blocking_exception_count"   # int as float -- observation
RECON_WARNING_EXCEPTION_COUNT = "recon_warning_exception_count"     # int as float -- observation
RECON_INVOICE_ERROR = "recon_invoice_error"                         # 1.0 / 0.0 -- per-invoice pipeline error

# --- Reconciliation Eval: predicted-vs-actual business outcome metrics ---
RECON_PREDICTED_MATCH_STATUS = "recon_predicted_match_status"       # label: MATCHED/PARTIAL_MATCH/UNMATCHED/REQUIRES_REVIEW
RECON_ACTUAL_MATCH_STATUS = "recon_actual_match_status"             # label: MATCHED/UNMATCHED (post-review)
RECON_MATCH_STATUS_CORRECT = "recon_match_status_correct"           # 1.0 / 0.0 -- predicted == actual
RECON_PREDICTED_REQUIRES_REVIEW = "recon_predicted_requires_review" # 1.0 / 0.0
RECON_ACTUAL_REVIEW_CREATED = "recon_actual_review_created"         # 1.0 / 0.0
RECON_REVIEW_ROUTE_CORRECT = "recon_review_route_correct"           # 1.0 / 0.0
RECON_PREDICTED_AUTO_CLOSE = "recon_predicted_auto_close"           # 1.0 / 0.0
RECON_ACTUAL_AUTO_CLOSE = "recon_actual_auto_close"                 # 1.0 / 0.0
RECON_AUTO_CLOSE_CORRECT = "recon_auto_close_correct"               # 1.0 / 0.0
RECON_PREDICTED_PO_FOUND = "recon_predicted_po_found"               # 1.0 / 0.0
RECON_ACTUAL_PO_FOUND = "recon_actual_po_found"                     # 1.0 / 0.0
RECON_PO_FOUND_CORRECT = "recon_po_found_correct"                   # 1.0 / 0.0
RECON_PREDICTED_GRN_FOUND = "recon_predicted_grn_found"             # 1.0 / 0.0
RECON_ACTUAL_GRN_FOUND = "recon_actual_grn_found"                   # 1.0 / 0.0
RECON_GRN_FOUND_CORRECT = "recon_grn_found_correct"                 # 1.0 / 0.0
RECON_REPROCESSED = "recon_reprocessed"                             # 1.0 / 0.0
RECON_REVIEW_OUTCOME = "recon_review_outcome"                       # label: APPROVED/REJECTED/REPROCESSED
RECON_CORRECTED_BY_REVIEWER = "recon_corrected_by_reviewer"         # 1.0 / 0.0
RECON_ACTUAL_FINAL_ROUTE = "recon_actual_final_route"               # label: auto_close/review/agent/unresolved

# --- Reconciliation Eval: learning signal types ---
RECON_SIG_WRONG_MATCH_STATUS = "wrong_match_status_prediction"
RECON_SIG_WRONG_AUTO_CLOSE = "wrong_auto_close_prediction"
RECON_SIG_WRONG_REVIEW_ROUTE = "wrong_review_route_prediction"
RECON_SIG_MISSING_PO = "missing_po_prediction_issue"
RECON_SIG_MISSING_GRN = "missing_grn_prediction_issue"
RECON_SIG_REVIEW_OVERRIDE = "review_override"
RECON_SIG_REPROCESS = "reprocess_signal"
RECON_SIG_TOLERANCE_REVIEW = "tolerance_or_rule_review_candidate"

# =====================================================================
# AGENTS
# =====================================================================
AGENT_PIPELINE_FINAL_CONFIDENCE = "agent_pipeline_final_confidence"         # 0.0-1.0
AGENT_PIPELINE_RECOMMENDATION_PRESENT = "agent_pipeline_recommendation_present"  # 1.0 / 0.0
AGENT_PIPELINE_ESCALATION_TRIGGERED = "agent_pipeline_escalation_triggered"      # 1.0 / 0.0
AGENT_PIPELINE_AUTO_CLOSE_CANDIDATE = "agent_pipeline_auto_close_candidate"
AGENT_PIPELINE_AGENTS_EXECUTED_COUNT = "agent_pipeline_agents_executed_count"     # int as float
AGENT_CONFIDENCE = "agent_confidence"                               # 0.0-1.0 per agent
AGENT_RECOMMENDATION_PRESENT = "agent_recommendation_present"       # 1.0 / 0.0 per agent
AGENT_TOOL_SUCCESS_RATE = "agent_tool_success_rate"                 # 0.0-1.0 per agent
TOOL_CALL_SUCCESS = "tool_call_success"                             # 1.0 / 0.0 per tool call
AGENT_FEEDBACK_TRIGGERED_RERUN = "agent_feedback_triggered_rerun"   # 1.0 / 0.0
AGENT_FEEDBACK_IMPROVED_OUTCOME = "agent_feedback_improved_outcome" # 1.0 / 0.0

# =====================================================================
# CASE / REVIEW
# =====================================================================
CASE_PROCESSING_SUCCESS = "case_processing_success"                 # 1.0 / 0.0
CASE_CLOSED = "case_closed"                                         # 1.0 / 0.0
CASE_TERMINAL = "case_terminal"                                     # 1.0 / 0.0
CASE_REPROCESSED = "case_reprocessed"                               # 1.0 / 0.0
CASE_PATH_RESOLUTION_SUCCESS = "case_path_resolved"                 # 1.0 / 0.0
CASE_STAGES_EXECUTED = "case_stages_executed"                       # int as float
CASE_MATCH_STATUS = "case_match_status"                             # 1.0/0.5/0.3/0.0
CASE_AUTO_CLOSED = "case_auto_closed"                               # 1.0 / 0.0
CASE_ROUTED_TO_REVIEW = "case_routed_to_review"                     # 1.0 / 0.0
CASE_REVIEW_REQUIRED = "case_review_required"                       # 1.0 / 0.0
CASE_ROUTED_TO_ASSIGNMENT = "case_routed_to_assignment"             # 1.0 / 0.0
CASE_ASSIGNMENT_CREATED = "case_assignment_created"                 # 1.0 / 0.0
CASE_ASSIGNMENT_HAS_REVIEWER = "case_assignment_has_reviewer"       # 1.0 / 0.0
CASE_SUMMARY_GENERATED = "case_summary_generated"                   # 1.0 / 0.0
CASE_PO_FOUND = "case_po_found"                                     # 1.0 / 0.0
CASE_AGENT_CONFIDENCE = "case_agent_confidence"                     # 0.0-1.0
CASE_NON_PO_APPROVAL_READY = "case_non_po_approval_ready"           # 1.0 / 0.0
CASE_NON_PO_RISK_SCORE = "case_non_po_risk_score"                   # 0.0-1.0
CASE_MATCH_RESULT = "case_match_result"                             # observation: 1.0/0.5/0.3/0.0

REVIEW_ASSIGNMENT_CREATED = "review_assignment_created"             # 1.0
REVIEW_PRIORITY = "review_priority"                                 # priority / 10.0
REVIEW_APPROVED = "review_approved"                                 # 1.0 / 0.0
REVIEW_REJECTED = "review_rejected"                                 # 1.0 / 0.0
REVIEW_REPROCESS_REQUESTED = "review_reprocess_requested"           # 1.0 / 0.0
REVIEW_HAD_CORRECTIONS = "review_had_corrections"                   # 1.0 / 0.0
REVIEW_FIELDS_CORRECTED_COUNT = "review_fields_corrected_count"     # int as float
REVIEW_DECISION = "review_decision"                                 # APPROVED=1.0, REPROCESSED=0.5, REJECTED=0.0

# =====================================================================
# POSTING
# =====================================================================
POSTING_FINAL_CONFIDENCE = "posting_confidence"                     # 0.0-1.0 composite
POSTING_FINAL_REQUIRES_REVIEW = "posting_requires_review"           # 1.0 / 0.0
POSTING_FINAL_TOUCHLESS = "posting_touchless"                       # 1.0 / 0.0
POSTING_FINAL_READY_TO_SUBMIT = "posting_ready_to_submit"           # 1.0 / 0.0
POSTING_FINAL_FAILED = "posting_failed"                             # 1.0 / 0.0
POSTING_ELIGIBILITY_PASSED = "posting_eligibility_passed"           # 1.0 / 0.0
POSTING_VENDOR_MAPPING_SUCCESS = "posting_vendor_mapping_success"   # 1.0 / 0.0
POSTING_ITEM_MAPPING_SUCCESS_RATE = "posting_item_mapping_success_rate"         # 0.0-1.0
POSTING_TAX_MAPPING_SUCCESS_RATE = "posting_tax_mapping_success_rate"           # 0.0-1.0
POSTING_COST_CENTER_MAPPING_SUCCESS_RATE = "posting_cost_center_mapping_success_rate"  # 0.0-1.0
POSTING_REFERENCE_FRESHNESS_SCORE = "posting_reference_freshness_score"         # 0.0-1.0
POSTING_VALIDATION_ERROR_COUNT = "posting_validation_error_count"               # int as float
POSTING_VALIDATION_WARNING_COUNT = "posting_validation_warning_count"           # int as float
POSTING_REQUIRES_REVIEW = "posting_requires_review"                 # alias kept for backward compat
POSTING_TOUCHLESS_CANDIDATE = "posting_touchless_candidate"         # 1.0 / 0.0
POSTING_PAYLOAD_BUILD_SUCCESS = "posting_payload_build_success"     # 1.0 / 0.0

# =====================================================================
# ERP
# =====================================================================
ERP_RESOLUTION_SUCCESS = "erp_resolution_success"                   # 1.0 / 0.0
ERP_RESOLUTION_FRESH = "erp_resolution_fresh"                       # 1.0 / 0.0
ERP_RESOLUTION_AUTHORITATIVE = "erp_resolution_authoritative"       # 1.0 / 0.0
ERP_RESOLUTION_USED_FALLBACK = "erp_resolution_used_fallback"       # 1.0 / 0.0
ERP_RESOLUTION_LATENCY_OK = "erp_resolution_latency_ok"             # 1.0 / 0.0
ERP_RESOLUTION_RESULT_PRESENT = "erp_resolution_result_present"     # 1.0 / 0.0
ERP_CACHE_HIT = "erp_cache_hit"                                     # 1.0 / 0.0
ERP_CACHE_STALE = "erp_cache_stale"                                 # 1.0 / 0.0
ERP_LIVE_LOOKUP_SUCCESS = "erp_live_lookup_success"                 # 1.0 / 0.0
ERP_LIVE_LOOKUP_LATENCY_OK = "erp_live_lookup_latency_ok"           # 1.0 / 0.0
ERP_LIVE_LOOKUP_TIMEOUT = "erp_live_lookup_timeout"                 # 1.0 / 0.0
ERP_LIVE_LOOKUP_RATE_LIMITED = "erp_live_lookup_rate_limited"       # 1.0 / 0.0
ERP_DB_FALLBACK_USED = "erp_db_fallback_used"                       # 1.0
ERP_DB_FALLBACK_SUCCESS = "erp_db_fallback_success"                 # 1.0 / 0.0
ERP_NORMALIZATION_SUCCESS = "erp_normalization_success"             # 1.0 / 0.0
ERP_PARTIAL_RESULT = "erp_partial_result"                           # 1.0 / 0.0
ERP_DUPLICATE_FOUND = "erp_duplicate_found"                         # 1.0 / 0.0
ERP_SUBMISSION_ATTEMPTED = "erp_submission_attempted"               # 1.0
ERP_SUBMISSION_SUCCESS = "erp_submission_success"                   # 1.0 / 0.0
ERP_SUBMISSION_LATENCY_OK = "erp_submission_latency_ok"             # 1.0 / 0.0
ERP_SUBMISSION_RETRYABLE_FAILURE = "erp_submission_retryable_failure"
ERP_DOCUMENT_NUMBER_PRESENT = "erp_document_number_present"         # 1.0 / 0.0
ERP_RETRY_ATTEMPTED = "erp_retry_attempted"                         # 1.0 / 0.0
ERP_RETRY_SUCCESS = "erp_retry_success"                             # 1.0 / 0.0

# =====================================================================
# CROSS-CUTTING
# =====================================================================
LATENCY_OK = "latency_ok"                                           # 1.0 / 0.0 (generic)
FALLBACK_USED = "fallback_used"                                     # 1.0 / 0.0 (generic)
RBAC_GUARDRAIL = "rbac_guardrail"                                   # 1.0=GRANTED, 0.0=DENIED
RBAC_DATA_SCOPE = "rbac_data_scope"                                 # 0.0 only (deny path)
COPILOT_SESSION_LENGTH = "copilot_session_length"                   # int as float -- message count

# =====================================================================
# DECISION-QUALITY EVALUATION SIGNALS
# =====================================================================
DECISION_CONFIDENCE_ALIGNMENT = "decision_confidence_alignment"     # 1.0 if confidence-based routing matched actual outcome
ROUTING_DECISION_EXECUTED = "routing_decision_executed"              # 1.0 / 0.0
REVIEW_REQUIRED_CORRECTLY_TRIGGERED = "review_required_correctly_triggered"  # 1.0 / 0.0
TOUCHLESS_CANDIDATE_SELECTED = "touchless_candidate_selected"       # 1.0 / 0.0
STALE_DATA_ACCEPTED = "stale_data_accepted"                         # 1.0 / 0.0
FALLBACK_USED_BUT_SUCCESSFUL = "fallback_used_but_successful"       # 1.0 / 0.0


# =====================================================================
# LATENCY THRESHOLDS (ms) -- per operation type
# =====================================================================
LATENCY_THRESHOLD_OCR_MS = 30000        # 30s -- document intelligence OCR
LATENCY_THRESHOLD_LLM_MS = 20000        # 20s -- LLM generation call
LATENCY_THRESHOLD_ERP_MS = 5000         # 5s  -- ERP API call
LATENCY_THRESHOLD_DB_MS = 2000          # 2s  -- database query / fallback
LATENCY_THRESHOLD_RECON_STAGE_MS = 5000 # 5s  -- reconciliation sub-stage
LATENCY_THRESHOLD_POSTING_STAGE_MS = 5000
LATENCY_THRESHOLD_TOOL_CALL_MS = 10000  # 10s -- agent tool call


# =====================================================================
# ROOT TRACE NAMES (canonical names for start_trace 'name' parameter)
# =====================================================================
TRACE_EXTRACTION_PIPELINE = "extraction_pipeline"
TRACE_RECONCILIATION_PIPELINE = "reconciliation_pipeline"
TRACE_AGENT_PIPELINE = "agent_pipeline"
TRACE_CASE_PIPELINE = "case_pipeline"
TRACE_POSTING_PIPELINE = "posting_pipeline"
TRACE_ERP_SUBMISSION_PIPELINE = "erp_submission_pipeline"
TRACE_REVIEW_WORKFLOW = "review_workflow"
TRACE_COPILOT_SESSION = "copilot_session"


# =====================================================================
# SYSTEM AGENTS (deterministic system-agent scores)
# =====================================================================
SYSTEM_AGENT_SUCCESS = "system_agent_success"                       # 1.0 / 0.0 -- deterministic agent completed
SYSTEM_AGENT_DECISION_COUNT = "system_agent_decision_count"         # int as float -- decisions logged
SYSTEM_REVIEW_ROUTING_SUCCESS = "system_review_routing_success"     # 1.0 / 0.0
SYSTEM_CASE_SUMMARY_SUCCESS = "system_case_summary_success"         # 1.0 / 0.0
SYSTEM_BULK_INTAKE_SUCCESS = "system_bulk_intake_success"           # 1.0 / 0.0
SYSTEM_CASE_INTAKE_SUCCESS = "system_case_intake_success"           # 1.0 / 0.0
SYSTEM_POSTING_PREPARATION_SUCCESS = "system_posting_preparation_success"  # 1.0 / 0.0

# Root trace names for system agent pipelines
TRACE_SYSTEM_AGENT = "system_agent"
TRACE_SYSTEM_BULK_INTAKE = "system_bulk_intake"
TRACE_SYSTEM_CASE_INTAKE = "system_case_intake"
TRACE_SYSTEM_POSTING_PREPARATION = "system_posting_preparation"

# =====================================================================
# SUPERVISOR AGENT
# =====================================================================
TRACE_SUPERVISOR_PIPELINE = "supervisor_pipeline"
SUPERVISOR_CONFIDENCE = "supervisor_confidence"                       # 0.0-1.0
SUPERVISOR_RECOMMENDATION_PRESENT = "supervisor_recommendation_present"  # 1.0 / 0.0
SUPERVISOR_TOOLS_USED_COUNT = "supervisor_tools_used_count"           # int as float
SUPERVISOR_RECOVERY_USED = "supervisor_recovery_used"                 # 1.0 / 0.0
SUPERVISOR_AUTO_CLOSE_CANDIDATE = "supervisor_auto_close_candidate"   # 1.0 / 0.0
