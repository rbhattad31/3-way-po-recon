"""AP review routing / decision skill -- persist results, route, and close."""
from apps.agents.skills.base import Skill, register_skill

ap_review_routing_skill = register_skill(Skill(
    name="ap_review_routing",
    description="Persist invoice, create case, route to review or auto-close.",
    prompt_extension=(
        "## DECIDE Phase\n"
        "After analysis is complete, take final actions.\n\n"
        "Steps:\n"
        "1. Call `persist_invoice` to save/update the invoice record.\n"
        "2. Call `create_case` to create or update the AP case.\n"
        "3. Call `submit_recommendation` with your final recommendation. "
        "You MUST call this tool before finishing.\n"
        "4. Based on the recommendation:\n"
        "   - AUTO_CLOSE: Call `auto_close_case` if all checks pass.\n"
        "   - SEND_TO_AP_REVIEW / SEND_TO_PROCUREMENT / SEND_TO_VENDOR_CLARIFICATION: "
        "     Call `assign_reviewer` to route to the appropriate queue.\n"
        "   - ESCALATE_TO_MANAGER: Call `escalate_case`.\n"
        "5. Call `generate_case_summary` to produce a human-readable summary.\n\n"
        "GUARDRAILS:\n"
        "- You MUST call submit_recommendation before finishing.\n"
        "- Do NOT auto-close if any line exceeds the auto-close tolerance.\n"
        "- Do NOT auto-close if vendor verification failed.\n"
        "- If uncertain about the correct action, route to SEND_TO_AP_REVIEW."
    ),
    tools=[
        "persist_invoice",
        "create_case",
        "submit_recommendation",
        "assign_reviewer",
        "generate_case_summary",
        "auto_close_case",
        "escalate_case",
        "exception_list",
        "reconciliation_summary",
    ],
    decision_hints=[
        "If confidence >= 0.9 and all lines match within tolerance, AUTO_CLOSE.",
        "If confidence >= 0.6 but some deviations exist, SEND_TO_AP_REVIEW.",
        "If confidence < 0.6 or critical exceptions found, ESCALATE_TO_MANAGER.",
        "Always generate a case summary regardless of the decision.",
    ],
))
