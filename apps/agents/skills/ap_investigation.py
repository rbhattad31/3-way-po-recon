"""AP investigation skill -- recovery actions when matching fails."""
from apps.agents.skills.base import Skill, register_skill

ap_investigation_skill = register_skill(Skill(
    name="ap_investigation",
    description="Investigate and recover from matching failures.",
    prompt_extension=(
        "## INVESTIGATE Phase\n"
        "When matching fails or produces partial results, investigate before "
        "escalating.\n\n"
        "Available recovery actions:\n"
        "1. `re_extract_field` -- Re-extract a specific field if you suspect "
        "OCR/extraction error (e.g. wrong PO number).\n"
        "2. `invoke_po_retrieval_agent` -- Delegate to the real PO Retrieval "
        "Agent (full LLM agent with reasoning and tools). It will search for "
        "POs using po_lookup, vendor_search, and invoice_details.\n"
        "3. `invoke_grn_retrieval_agent` -- Delegate to the real GRN Retrieval "
        "Agent (full LLM agent) when GRN is missing for a 3-way match.\n"
        "4. `invoke_exception_analysis_agent` -- Delegate to the Exception "
        "Analysis Agent to analyze reconciliation exceptions and determine "
        "root causes. Requires a reconciliation_result_id.\n"
        "5. `invoke_reconciliation_assist_agent` -- Delegate to the "
        "Reconciliation Assist Agent for general-purpose partial match "
        "investigation. Requires a reconciliation_result_id.\n"
        "6. `get_vendor_history` -- Check vendor's recent invoices and POs "
        "for patterns.\n"
        "7. `get_case_history` -- Check if similar invoices have been seen before.\n\n"
        "IMPORTANT: Always attempt re-extraction of the PO number before "
        "escalating a PO_NOT_FOUND failure. The PO may exist but the extracted "
        "number could be wrong.\n\n"
        "AGENT DELEGATION: The invoke_*_agent tools run real specialized LLM "
        "agents that have their own reasoning loops and tool access. They "
        "return structured recommendations with confidence scores. Prefer "
        "delegating to these agents over manual investigation when the problem "
        "is complex.\n\n"
        "After investigation, return to MATCH phase if new data was recovered."
    ),
    tools=[
        "re_extract_field",
        "invoke_po_retrieval_agent",
        "invoke_grn_retrieval_agent",
        "invoke_exception_analysis_agent",
        "invoke_reconciliation_assist_agent",
        "get_vendor_history",
        "get_case_history",
        "invoice_details",
    ],
    decision_hints=[
        "If re-extraction yields a different PO number, re-run po_lookup with "
        "the new number before concluding.",
        "If PO retrieval agent finds a PO, re-run matching with the new PO.",
        "Use invoke_exception_analysis_agent when there are multiple "
        "reconciliation exceptions that need root cause analysis.",
        "Use invoke_reconciliation_assist_agent for complex partial matches "
        "that need holistic investigation across PO, GRN, and invoice data.",
        "Do NOT escalate without attempting at least one recovery action.",
    ],
))
