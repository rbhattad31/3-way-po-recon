"""
Seed script: Populate agent detail data for Case 192 (AP-260324-0002).
Run: python seed_case192.py
"""
import os
import sys
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from apps.agents.models import AgentRun, AgentStep, AgentMessage, DecisionLog
from apps.tools.models import ToolCall

trace_id = uuid.uuid4().hex[:32]

# ════════════════════════════════════════════════════════════════
# EXCEPTION_ANALYSIS (run 776)
# ════════════════════════════════════════════════════════════════

AgentStep.objects.create(
    agent_run_id=776, step_number=1, action="tool_call:exception_list",
    input_data={"invoice_id": 225},
    output_data={"exceptions": [{"type": "PO_NOT_FOUND", "severity": "HIGH",
                  "description": "Purchase order not found for PO number 'PO-BEL-2025-0112'"}]},
    success=True, duration_ms=34,
)
AgentStep.objects.create(
    agent_run_id=776, step_number=2, action="tool_call:invoice_details",
    input_data={"invoice_id": 225},
    output_data={"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP",
                 "amount": 227740.00, "po_number": "PO-BEL-2025-0112"},
    success=True, duration_ms=28,
)
AgentStep.objects.create(
    agent_run_id=776, step_number=3, action="tool_call:reconciliation_summary",
    input_data={"reconciliation_result_id": 139},
    output_data={"match_status": "UNMATCHED", "mode": "3-way", "exceptions_count": 1},
    success=True, duration_ms=41,
)
AgentStep.objects.create(
    agent_run_id=776, step_number=4, action="analysis_complete",
    input_data={"exception_types": ["PO_NOT_FOUND"]},
    output_data={"assessment": "PO reference on invoice is invalid or not in system. Cannot auto-resolve. AP review required.",
                 "severity": "HIGH"},
    success=True, duration_ms=1250,
)

AgentMessage.objects.create(
    agent_run_id=776, role="system", message_index=0,
    content="You are an exception analysis specialist. Analyze reconciliation exceptions for the given invoice and determine root cause, severity, and recommended resolution path.",
    token_count=42,
)
AgentMessage.objects.create(
    agent_run_id=776, role="user", message_index=1,
    content="Invoice ID: 225\nReconciliation Result ID: 139\nMatch Status: UNMATCHED\nExceptions: PO_NOT_FOUND",
    token_count=28,
)
AgentMessage.objects.create(
    agent_run_id=776, role="assistant", message_index=2,
    content="I will analyze the exceptions by fetching the exception details, invoice information, and reconciliation summary.",
    token_count=24,
)
AgentMessage.objects.create(
    agent_run_id=776, role="tool", message_index=3,
    content='{"exceptions": [{"type": "PO_NOT_FOUND", "severity": "HIGH", "description": "Purchase order not found for PO number PO-BEL-2025-0112"}]}',
    token_count=35,
)
AgentMessage.objects.create(
    agent_run_id=776, role="tool", message_index=4,
    content='{"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "amount": 227740.00, "currency": "INR", "po_number": "PO-BEL-2025-0112"}',
    token_count=40,
)
AgentMessage.objects.create(
    agent_run_id=776, role="tool", message_index=5,
    content='{"match_status": "UNMATCHED", "mode": "3-way", "exceptions_count": 1, "has_po": false, "has_grn": false}',
    token_count=30,
)
AgentMessage.objects.create(
    agent_run_id=776, role="assistant", message_index=6,
    content="Analysis complete. The PO number PO-BEL-2025-0112 referenced on invoice TSL/INV/2025/0892 was not found in the system. The vendor TechServ Solutions LLP is not recognized in the vendor master. This is a blocking exception that prevents automated reconciliation. The invoice requires AP review to validate the PO reference and vendor details.",
    token_count=68,
)

ToolCall.objects.create(
    agent_run_id=776, tool_name="exception_list", status="SUCCESS",
    input_payload={"invoice_id": 225},
    output_payload={"exceptions": [{"type": "PO_NOT_FOUND", "severity": "HIGH"}]},
    duration_ms=34,
)
ToolCall.objects.create(
    agent_run_id=776, tool_name="invoice_details", status="SUCCESS",
    input_payload={"invoice_id": 225},
    output_payload={"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "amount": 227740.00},
    duration_ms=28,
)
ToolCall.objects.create(
    agent_run_id=776, tool_name="reconciliation_summary", status="SUCCESS",
    input_payload={"reconciliation_result_id": 139},
    output_payload={"match_status": "UNMATCHED", "exceptions_count": 1},
    duration_ms=41,
)

DecisionLog.objects.create(
    agent_run_id=776,
    decision_type="EXCEPTION_CLASSIFICATION",
    decision="PO_NOT_FOUND classified as blocking - cannot auto-resolve",
    rationale="The PO number PO-BEL-2025-0112 does not exist in the system. Vendor TechServ Solutions LLP has no matching vendor record. Without a valid PO, 3-way matching cannot proceed.",
    confidence=0.95,
    deterministic_flag=False,
    evidence_refs={"exception_type": "PO_NOT_FOUND", "po_number": "PO-BEL-2025-0112"},
    invoice_id=225,
    case_id=192,
    reconciliation_result_id=139,
    trace_id=trace_id,
)
DecisionLog.objects.create(
    agent_run_id=776,
    decision_type="RESOLUTION_PATH",
    decision="Route to AP Review for manual PO validation",
    rationale="Standard PO_NOT_FOUND exception with no alternative PO candidates. AP team should verify the PO reference with the vendor or procurement team.",
    confidence=0.90,
    deterministic_flag=True,
    evidence_refs={"exception_types": ["PO_NOT_FOUND"], "auto_resolvable": False},
    invoice_id=225,
    case_id=192,
    reconciliation_result_id=139,
    trace_id=trace_id,
)

print("Exception Analysis (776): DONE")

# ════════════════════════════════════════════════════════════════
# REVIEW_ROUTING (run 777)
# ════════════════════════════════════════════════════════════════

AgentStep.objects.create(
    agent_run_id=777, step_number=1, action="evaluate_exception_severity",
    input_data={"exceptions": ["PO_NOT_FOUND"], "match_status": "UNMATCHED"},
    output_data={"severity_level": "HIGH", "auto_close_eligible": False},
    success=True, duration_ms=15,
)
AgentStep.objects.create(
    agent_run_id=777, step_number=2, action="determine_review_queue",
    input_data={"severity": "HIGH", "exception_types": ["PO_NOT_FOUND"], "confidence": 0.9},
    output_data={"queue": "AP_REVIEW", "priority": "HIGH",
                 "reason": "Blocking PO exception requires manual resolution"},
    success=True, duration_ms=820,
)

AgentMessage.objects.create(
    agent_run_id=777, role="system", message_index=0,
    content="You are a review routing specialist. Based on exception analysis results, determine the appropriate review queue, priority, and assignee.",
    token_count=35,
)
AgentMessage.objects.create(
    agent_run_id=777, role="user", message_index=1,
    content="Invoice: TSL/INV/2025/0892\nMatch Status: UNMATCHED\nExceptions: PO_NOT_FOUND (HIGH severity)\nAnalysis: PO not found, cannot auto-resolve",
    token_count=32,
)
AgentMessage.objects.create(
    agent_run_id=777, role="assistant", message_index=2,
    content="Based on the exception analysis, this invoice has a blocking PO_NOT_FOUND exception. The case should be routed to AP Review with HIGH priority. Auto-close is not eligible because the PO reference cannot be validated programmatically.",
    token_count=48,
)

DecisionLog.objects.create(
    agent_run_id=777,
    decision_type="REVIEW_ROUTING",
    decision="Route to AP_REVIEW queue with HIGH priority",
    rationale="PO_NOT_FOUND is a blocking exception that requires human intervention to validate PO reference PO-BEL-2025-0112 with procurement or vendor.",
    confidence=0.90,
    deterministic_flag=True,
    evidence_refs={"queue": "AP_REVIEW", "auto_close": False, "priority": "HIGH"},
    recommendation_type="SEND_TO_AP_REVIEW",
    invoice_id=225,
    case_id=192,
    reconciliation_result_id=139,
    trace_id=trace_id,
)

print("Review Routing (777): DONE")

# ════════════════════════════════════════════════════════════════
# CASE_SUMMARY (run 778)
# ════════════════════════════════════════════════════════════════

AgentStep.objects.create(
    agent_run_id=778, step_number=1, action="tool_call:invoice_details",
    input_data={"invoice_id": 225},
    output_data={"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP",
                 "amount": 227740.00, "currency": "INR"},
    success=True, duration_ms=22,
)
AgentStep.objects.create(
    agent_run_id=778, step_number=2, action="tool_call:reconciliation_summary",
    input_data={"reconciliation_result_id": 139},
    output_data={"match_status": "UNMATCHED", "mode": "3-way", "exceptions_count": 1},
    success=True, duration_ms=35,
)
AgentStep.objects.create(
    agent_run_id=778, step_number=3, action="generate_summary",
    input_data={"invoice_id": 225, "case_id": 192},
    output_data={"summary_generated": True, "recommended_action": "Send to AP Review"},
    success=True, duration_ms=1480,
)

AgentMessage.objects.create(
    agent_run_id=778, role="system", message_index=0,
    content="You are a case summary specialist. Generate a comprehensive narrative summary of the reconciliation case including match status, exceptions, analysis, and recommended action.",
    token_count=38,
)
AgentMessage.objects.create(
    agent_run_id=778, role="user", message_index=1,
    content="Generate a case summary for Case AP-260324-0002 (Invoice TSL/INV/2025/0892).\nReconciliation Result: UNMATCHED (3-way)\nExceptions: PO_NOT_FOUND\nAgent Analysis: PO not found, routing to AP review",
    token_count=45,
)
AgentMessage.objects.create(
    agent_run_id=778, role="assistant", message_index=2,
    content="I will gather the invoice details and reconciliation summary to generate a comprehensive case narrative.",
    token_count=22,
)
AgentMessage.objects.create(
    agent_run_id=778, role="tool", message_index=3,
    content='{"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "total_amount": 227740.00, "currency": "INR", "po_number": "PO-BEL-2025-0112"}',
    token_count=38,
)
AgentMessage.objects.create(
    agent_run_id=778, role="tool", message_index=4,
    content='{"match_status": "UNMATCHED", "reconciliation_mode": "THREE_WAY", "exceptions": [{"type": "PO_NOT_FOUND", "description": "Purchase order not found for PO number PO-BEL-2025-0112"}]}',
    token_count=42,
)
AgentMessage.objects.create(
    agent_run_id=778, role="assistant", message_index=5,
    content="Case Summary generated. Invoice TSL/INV/2025/0892 from TechServ Solutions LLP (INR 227,740.00) is UNMATCHED in 3-way reconciliation. The referenced PO PO-BEL-2025-0112 was not found in the system. No GRN is available. Recommended action: Send to AP Review for manual PO validation with procurement team.",
    token_count=72,
)

ToolCall.objects.create(
    agent_run_id=778, tool_name="invoice_details", status="SUCCESS",
    input_payload={"invoice_id": 225},
    output_payload={"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "amount": 227740.00},
    duration_ms=22,
)
ToolCall.objects.create(
    agent_run_id=778, tool_name="reconciliation_summary", status="SUCCESS",
    input_payload={"reconciliation_result_id": 139},
    output_payload={"match_status": "UNMATCHED", "mode": "3-way", "exceptions_count": 1},
    duration_ms=35,
)

DecisionLog.objects.create(
    agent_run_id=778,
    decision_type="CASE_SUMMARY",
    decision="Case requires AP Review - PO not found, vendor unrecognized",
    rationale="Invoice TSL/INV/2025/0892 references PO-BEL-2025-0112 which does not exist in the system. TechServ Solutions LLP is not in the vendor master. The case cannot be auto-resolved and must be reviewed by the AP team to clarify the PO reference.",
    confidence=0.90,
    deterministic_flag=False,
    evidence_refs={"match_status": "UNMATCHED", "exceptions": ["PO_NOT_FOUND"],
                   "recommended_action": "SEND_TO_AP_REVIEW"},
    recommendation_type="SEND_TO_AP_REVIEW",
    invoice_id=225,
    case_id=192,
    reconciliation_result_id=139,
    trace_id=trace_id,
)

# Update agent runs with token counts and cost
AgentRun.objects.filter(pk=776).update(
    total_tokens=1456, cost_estimate=0.0043,
)
AgentRun.objects.filter(pk=777).update(
    total_tokens=890, cost_estimate=0.0027,
)
AgentRun.objects.filter(pk=778).update(
    total_tokens=1680, cost_estimate=0.0050,
)

print("Case Summary (778): DONE")
print()
print("All seed data created successfully!")
