"""AP Insights skill -- system-wide analytics and dashboard access."""
from apps.agents.skills.base import Skill, register_skill

ap_insights_skill = register_skill(Skill(
    name="ap_insights",
    description="Answer system-wide AP analytics, KPI, and performance questions.",
    prompt_extension=(
        "## AP INSIGHTS Phase\n"
        "When the user asks about system-wide AP metrics, performance, trends, "
        "or analytics (rather than a specific invoice), use the AP Insights tools.\n\n"
        "Available analytics tools:\n"
        "1. `get_ap_dashboard_summary` -- Overall KPIs: total invoices, match rate, "
        "   pending reviews, open exceptions, average confidence.\n"
        "2. `get_match_status_breakdown` -- Distribution of match outcomes "
        "   (MATCHED, PARTIAL_MATCH, UNMATCHED, etc.) with percentages.\n"
        "3. `get_exception_breakdown` -- Most common exception types and counts.\n"
        "4. `get_mode_breakdown` -- 2-way vs 3-way reconciliation comparison.\n"
        "5. `get_daily_volume_trend` -- Daily processing volumes (invoices, "
        "   reconciliations, exceptions) over time.\n"
        "6. `get_recent_activity` -- Latest activity feed across the system.\n"
        "7. `get_agent_performance_summary` -- Agent KPIs: success rate, "
        "   escalation rate, runtime, token cost.\n"
        "8. `get_agent_reliability_matrix` -- Per-agent health: which agents "
        "   succeed/fail/escalate most.\n"
        "9. `get_agent_token_cost` -- Token usage and cost per agent type.\n"
        "10. `get_recommendation_intelligence` -- Recommendation type distribution "
        "    and acceptance rates.\n"
        "11. `get_extraction_approval_analytics` -- Touchless rate, most-corrected "
        "    fields, approval breakdown.\n"
        "12. `get_review_queue_status` -- Open review counts by status, backlog.\n\n"
        "RESPONSE FORMAT for insights queries:\n"
        "When answering analytics questions, provide:\n"
        "- A clear summary with specific numbers\n"
        "- Key highlights or concerns\n"
        "- Trend direction if relevant (improving/worsening/stable)\n"
        "- Actionable observations when possible\n\n"
        "IMPORTANT: For insights queries, you do NOT need to call "
        "submit_recommendation. Simply provide the analytical answer."
    ),
    tools=[
        "get_ap_dashboard_summary",
        "get_match_status_breakdown",
        "get_exception_breakdown",
        "get_mode_breakdown",
        "get_daily_volume_trend",
        "get_recent_activity",
        "get_agent_performance_summary",
        "get_agent_reliability_matrix",
        "get_agent_token_cost",
        "get_recommendation_intelligence",
        "get_extraction_approval_analytics",
        "get_review_queue_status",
    ],
    decision_hints=[
        "For pure insights queries (no specific invoice), skip UNDERSTAND/VALIDATE/MATCH "
        "phases and go directly to answering with analytics tools.",
        "When a question mixes case-specific and system-wide context (e.g. 'how does "
        "this invoice compare to others?'), be HYBRID: analyze the invoice AND pull "
        "system metrics for comparison.",
        "If asked about agent performance, always include success rate and average "
        "confidence -- these are the primary health indicators.",
        "If touchless rate is below 60%, highlight it as a concern and suggest "
        "checking the most-corrected fields for extraction tuning.",
    ],
))
