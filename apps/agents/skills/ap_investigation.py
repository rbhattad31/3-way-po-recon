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
        "2. `invoke_po_retrieval_agent` -- Delegate PO search to the retrieval "
        "agent when the PO number on the invoice does not match any record.\n"
        "3. `invoke_grn_retrieval_agent` -- Delegate GRN search when GRN is "
        "missing for a 3-way match.\n"
        "4. `get_vendor_history` -- Check vendor's recent invoices and POs "
        "for patterns.\n"
        "5. `get_case_history` -- Check if similar invoices have been seen before.\n\n"
        "IMPORTANT: Always attempt re-extraction of the PO number before "
        "escalating a PO_NOT_FOUND failure. The PO may exist but the extracted "
        "number could be wrong.\n\n"
        "After investigation, return to MATCH phase if new data was recovered."
    ),
    tools=[
        "re_extract_field",
        "invoke_po_retrieval_agent",
        "invoke_grn_retrieval_agent",
        "get_vendor_history",
        "get_case_history",
        "invoice_details",
    ],
    decision_hints=[
        "If re-extraction yields a different PO number, re-run po_lookup with "
        "the new number before concluding.",
        "If PO retrieval agent finds a PO, re-run matching with the new PO.",
        "Do NOT escalate without attempting at least one recovery action.",
    ],
))
