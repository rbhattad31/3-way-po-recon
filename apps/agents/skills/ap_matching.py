"""AP 3-way matching skill -- PO lookup, header/line match, GRN match."""
from apps.agents.skills.base import Skill, register_skill

ap_matching_skill = register_skill(Skill(
    name="ap_3way_matching",
    description="Execute deterministic header, line, and GRN matching.",
    prompt_extension=(
        "## MATCH Phase\n"
        "Execute the deterministic matching pipeline against PO and GRN data.\n\n"
        "Steps:\n"
        "1. Call `get_tolerance_config` to retrieve current tolerance thresholds. "
        "NEVER hardcode tolerance values -- always use the tool output.\n"
        "2. Call `po_lookup` to find the referenced PO.\n"
        "3. If PO is found, call `run_header_match` to compare invoice header vs PO header.\n"
        "4. Call `run_line_match` to compare invoice lines vs PO lines.\n"
        "5. If reconciliation mode is 3-WAY:\n"
        "   a. Call `grn_lookup` to retrieve GRN(s) for the PO.\n"
        "   b. Call `run_grn_match` to compare received quantities.\n"
        "6. Analyze the match results. If partial match, check if deviations "
        "fall within the auto-close tolerance band.\n\n"
        "CRITICAL: Never auto-close without checking ALL lines against tolerance.\n"
        "If PO lookup fails, move to INVESTIGATE phase to try alternate lookups."
    ),
    tools=[
        "po_lookup",
        "run_header_match",
        "run_line_match",
        "grn_lookup",
        "run_grn_match",
        "get_tolerance_config",
    ],
    decision_hints=[
        "If all lines are within strict tolerance, recommend AUTO_CLOSE.",
        "If all lines are within auto-close tolerance but outside strict, "
        "recommend AUTO_CLOSE with lower confidence.",
        "If PO not found, investigate before escalating.",
        "In TWO_WAY mode, skip grn_lookup and run_grn_match entirely.",
    ],
))
