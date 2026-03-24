"""Seed remaining data for case 192 (run 778 + update all runs)."""
import os, uuid
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

from apps.agents.models import AgentRun, AgentStep, AgentMessage, DecisionLog
from apps.tools.models import ToolCall

trace_id = uuid.uuid4().hex[:32]
RUN_778 = 778
RUN_776 = 776
RUN_777 = 777

# CASE_SUMMARY steps
AgentStep.objects.create(agent_run_id=RUN_778, step_number=1, action="tool_call:invoice_details",
    input_data={"invoice_id": 225},
    output_data={"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "amount": 227740.00, "currency": "INR"},
    success=True, duration_ms=22)
AgentStep.objects.create(agent_run_id=RUN_778, step_number=2, action="tool_call:reconciliation_summary",
    input_data={"reconciliation_result_id": 139},
    output_data={"match_status": "UNMATCHED", "mode": "3-way", "exceptions_count": 1},
    success=True, duration_ms=35)
AgentStep.objects.create(agent_run_id=RUN_778, step_number=3, action="generate_summary",
    input_data={"invoice_id": 225, "case_id": 192},
    output_data={"summary_generated": True, "recommended_action": "Send to AP Review"},
    success=True, duration_ms=1480)
print("Steps done")

# Messages
AgentMessage.objects.create(agent_run_id=RUN_778, role="system", message_index=0,
    content="You are a case summary specialist. Generate a comprehensive narrative summary of the reconciliation case including match status, exceptions, analysis, and recommended action.",
    token_count=38)
AgentMessage.objects.create(agent_run_id=RUN_778, role="user", message_index=1,
    content="Generate a case summary for Case AP-260324-0002 (Invoice TSL/INV/2025/0892). Reconciliation Result: UNMATCHED (3-way). Exceptions: PO_NOT_FOUND. Agent Analysis: PO not found, routing to AP review",
    token_count=45)
AgentMessage.objects.create(agent_run_id=RUN_778, role="assistant", message_index=2,
    content="I will gather the invoice details and reconciliation summary to generate a comprehensive case narrative.",
    token_count=22)
AgentMessage.objects.create(agent_run_id=RUN_778, role="tool", message_index=3,
    content='{"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "total_amount": 227740.00}',
    token_count=38)
AgentMessage.objects.create(agent_run_id=RUN_778, role="tool", message_index=4,
    content='{"match_status": "UNMATCHED", "reconciliation_mode": "THREE_WAY", "exceptions_count": 1}',
    token_count=42)
AgentMessage.objects.create(agent_run_id=RUN_778, role="assistant", message_index=5,
    content="Case Summary generated. Invoice TSL/INV/2025/0892 from TechServ Solutions LLP (INR 227,740.00) is UNMATCHED in 3-way reconciliation. The referenced PO PO-BEL-2025-0112 was not found in the system. No GRN is available. Recommended action: Send to AP Review for manual PO validation with procurement team.",
    token_count=72)
print("Messages done")

# Tool calls
ToolCall.objects.create(agent_run_id=RUN_778, tool_name="invoice_details", status="SUCCESS",
    input_payload={"invoice_id": 225},
    output_payload={"invoice_number": "TSL/INV/2025/0892", "vendor": "TechServ Solutions LLP", "amount": 227740.00},
    duration_ms=22)
ToolCall.objects.create(agent_run_id=RUN_778, tool_name="reconciliation_summary", status="SUCCESS",
    input_payload={"reconciliation_result_id": 139},
    output_payload={"match_status": "UNMATCHED", "mode": "3-way", "exceptions_count": 1},
    duration_ms=35)
print("Tool calls done")

# Decision
DecisionLog.objects.create(agent_run_id=RUN_778, decision_type="CASE_SUMMARY",
    decision="Case requires AP Review - PO not found, vendor unrecognized",
    rationale="Invoice TSL/INV/2025/0892 references PO-BEL-2025-0112 which does not exist. TechServ Solutions LLP is not in the vendor master. Must be reviewed by AP team.",
    confidence=0.90, deterministic_flag=False,
    evidence_refs={"match_status": "UNMATCHED", "exceptions": ["PO_NOT_FOUND"]},
    recommendation_type="SEND_TO_AP_REVIEW",
    invoice_id=225, case_id=192, reconciliation_result_id=139, trace_id=trace_id)
print("Decision done")

# Update runs with token counts
AgentRun.objects.filter(id=RUN_776).update(total_tokens=1456, cost_estimate=0.0043)
AgentRun.objects.filter(id=RUN_777).update(total_tokens=890, cost_estimate=0.0027)
AgentRun.objects.filter(id=RUN_778).update(total_tokens=1680, cost_estimate=0.0050)
print("Runs updated")
print("ALL DONE")
