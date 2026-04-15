"""Supervisor query router -- classifies incoming queries by intent.

Determines whether a query is:
  - CASE_ANALYSIS: Case-specific deep analysis (existing supervisor path)
  - AP_INSIGHTS: System-wide AP analytics / dashboard questions
  - HYBRID: Needs both case-level and system-level context

The router uses keyword-based heuristics first (fast, no LLM cost), with
an optional LLM classification fallback for ambiguous queries.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class QueryMode(str, Enum):
    """Routing mode for supervisor queries."""
    CASE_ANALYSIS = "CASE_ANALYSIS"
    AP_INSIGHTS = "AP_INSIGHTS"
    HYBRID = "HYBRID"


@dataclass
class RoutingDecision:
    """Result of query classification."""
    mode: QueryMode
    confidence: float  # 0.0-1.0
    reason: str
    has_case_context: bool  # Whether a specific invoice/case was referenced


# -- Keyword patterns for classification ----------------------------------

# Patterns indicating system-wide / analytics questions
_INSIGHTS_PATTERNS = [
    re.compile(r"\b(dashboard|kpi|summary|overview|overall|system[\s-]?wide)\b", re.I),
    re.compile(r"\b(how many|total|count|percentage|rate|trend|volume)\b", re.I),
    re.compile(r"\b(performance|success rate|failure rate|reliability)\b", re.I),
    re.compile(r"\b(exception breakdown|exception trend|most common)\b", re.I),
    re.compile(r"\b(match rate|matched percentage|reconciliation rate)\b", re.I),
    re.compile(r"\b(agent performance|agent health|agent reliability)\b", re.I),
    re.compile(r"\b(token usage|cost|spending|budget|token)\b", re.I),
    re.compile(r"\b(daily volume|throughput|processing volume)\b", re.I),
    re.compile(r"\b(review queue|pending review|backlog|workload)\b", re.I),
    re.compile(r"\b(touchless|auto[\s-]?approve|extraction quality)\b", re.I),
    re.compile(r"\b(recommendation|acceptance rate|auto[\s-]?close rate)\b", re.I),
    re.compile(r"\b(2[\s-]?way vs 3[\s-]?way|mode breakdown|mode comparison)\b", re.I),
    re.compile(r"\b(recent activity|latest|what happened)\b", re.I),
    re.compile(r"\b(all invoices|all vendors|all cases|across)\b", re.I),
    re.compile(r"\b(compare|comparison|breakdown|distribution)\b", re.I),
    re.compile(r"\b(this week|this month|today|yesterday|last \d+ days)\b", re.I),
    re.compile(r"\b(average|median|worst|best|top|bottom)\b", re.I),
]

# Patterns indicating case-specific / invoice-specific questions
_CASE_PATTERNS = [
    re.compile(r"\b(invoice|inv)[\s#-]*\d+", re.I),
    re.compile(r"\b(po|purchase order)[\s#-]*\w+", re.I),
    re.compile(r"\b(case)[\s#-]*\w+", re.I),
    re.compile(r"\b(this invoice|this case|this po|current invoice)\b", re.I),
    re.compile(r"\b(investigate|analyze|check|validate|verify|match)\b", re.I),
    re.compile(r"\b(extract|re[\s-]?extract|extraction)\b", re.I),
    re.compile(r"\b(vendor verify|vendor check|tax id)\b", re.I),
    re.compile(r"\b(line match|header match|grn match)\b", re.I),
    re.compile(r"\b(duplicate|self[\s-]?company)\b", re.I),
    re.compile(r"\b(approve|reject|close|escalate|route)\b", re.I),
    re.compile(r"\b(what is wrong|what happened to|status of)\b", re.I),
]


def classify_query(
    query: str,
    *,
    has_invoice_id: bool = False,
    has_reconciliation_result: bool = False,
    has_case_id: bool = False,
) -> RoutingDecision:
    """Classify a query into a routing mode.

    Uses keyword heuristics. The presence of case context (invoice_id,
    reconciliation_result, case_id) heavily influences the decision.

    Args:
        query: The user's question or instruction.
        has_invoice_id: Whether the supervisor context has an invoice_id.
        has_reconciliation_result: Whether a reconciliation result exists.
        has_case_id: Whether a case ID is available.

    Returns:
        RoutingDecision with mode, confidence, and reason.
    """
    query_lower = query.lower().strip()
    has_case_context = has_invoice_id or has_reconciliation_result or has_case_id

    # Score both directions
    insights_score = 0
    case_score = 0

    for pattern in _INSIGHTS_PATTERNS:
        if pattern.search(query):
            insights_score += 1

    for pattern in _CASE_PATTERNS:
        if pattern.search(query):
            case_score += 1

    # Boost case score if we have explicit case context
    if has_case_context:
        case_score += 3

    total = insights_score + case_score
    if total == 0:
        # No strong signals -- default based on context
        if has_case_context:
            return RoutingDecision(
                mode=QueryMode.CASE_ANALYSIS,
                confidence=0.5,
                reason="No strong keyword signals; defaulting to case analysis (case context present)",
                has_case_context=has_case_context,
            )
        return RoutingDecision(
            mode=QueryMode.AP_INSIGHTS,
            confidence=0.5,
            reason="No strong keyword signals; defaulting to AP insights (no case context)",
            has_case_context=has_case_context,
        )

    # Both signals present -> hybrid
    if insights_score >= 2 and case_score >= 2:
        return RoutingDecision(
            mode=QueryMode.HYBRID,
            confidence=min(0.85, (insights_score + case_score) / 10),
            reason=(
                f"Mixed signals: {insights_score} insight keywords, "
                f"{case_score} case keywords"
            ),
            has_case_context=has_case_context,
        )

    # Strong insights signal
    if insights_score > case_score and not has_case_context:
        return RoutingDecision(
            mode=QueryMode.AP_INSIGHTS,
            confidence=min(0.95, insights_score / 5),
            reason=f"Strong insights signal ({insights_score} keywords, no case context)",
            has_case_context=has_case_context,
        )

    # Insights signal even with case context -> hybrid
    if insights_score > case_score and has_case_context:
        return RoutingDecision(
            mode=QueryMode.HYBRID,
            confidence=min(0.85, insights_score / 5),
            reason=f"Insights signal ({insights_score} keywords) with case context present",
            has_case_context=has_case_context,
        )

    # Strong case signal
    return RoutingDecision(
        mode=QueryMode.CASE_ANALYSIS,
        confidence=min(0.95, case_score / 5),
        reason=f"Strong case analysis signal ({case_score} keywords)",
        has_case_context=has_case_context,
    )
