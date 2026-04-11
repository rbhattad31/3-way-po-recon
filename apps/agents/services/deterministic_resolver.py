"""Deterministic resolver — rule-based replacement for EXCEPTION_ANALYSIS,
REVIEW_ROUTING, and CASE_SUMMARY agents.

When all exceptions map to unambiguous routing rules, this resolver produces
recommendations and case summaries WITHOUT calling the LLM, saving cost and
latency while maintaining full auditability via synthetic AgentRun records.

Rule priority (highest first):
  1. EXTRACTION_LOW_CONFIDENCE   -> REPROCESS_EXTRACTION
  2. VENDOR_MISMATCH             -> SEND_TO_VENDOR_CLARIFICATION
  2b. VENDOR_NOT_VERIFIED        -> SEND_TO_AP_REVIEW  (Non-PO path)
  3. GRN / receipt issues        -> SEND_TO_PROCUREMENT
  4. Complex (3+ types + HIGH)   -> ESCALATE_TO_MANAGER
  5. All other exceptions        -> SEND_TO_AP_REVIEW

If a prior agent (e.g. RECONCILIATION_ASSIST) recommended AUTO_CLOSE with
high confidence, the resolver respects that recommendation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from apps.core.enums import (
    ExceptionSeverity,
    ExceptionType,
    RecommendationType,
)

logger = logging.getLogger(__name__)

# Exception types that route to procurement team
_PROCUREMENT_EXCEPTION_TYPES: Set[str] = {
    ExceptionType.GRN_NOT_FOUND,
    ExceptionType.RECEIPT_SHORTAGE,
    ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED,
    ExceptionType.OVER_RECEIPT,
    ExceptionType.MULTI_GRN_PARTIAL_RECEIPT,
}

# Numeric mismatch types that frequently cascade from the same root cause.
# For complexity assessment these count as ONE issue category.
_NUMERIC_MISMATCH_TYPES: Set[str] = {
    ExceptionType.QTY_MISMATCH,
    ExceptionType.PRICE_MISMATCH,
    ExceptionType.AMOUNT_MISMATCH,
    ExceptionType.TAX_MISMATCH,
}

# Confidence threshold for trusting a prior agent's AUTO_CLOSE recommendation
_PRIOR_AUTO_CLOSE_CONFIDENCE = 0.80


@dataclass
class DeterministicResolution:
    """Output of the deterministic resolver."""

    recommendation_type: str
    confidence: float
    reasoning: str
    evidence: Dict
    case_summary: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_evidence(exceptions: List[Dict]) -> Dict:
    """Build structured evidence dict from exception list."""
    return {
        "exception_count": len(exceptions),
        "exception_types": sorted({e["exception_type"] for e in exceptions}),
        "severities": sorted({e.get("severity", "MEDIUM") for e in exceptions}),
        "resolver": "deterministic",
    }


def _build_case_summary(
    result,
    exceptions: List[Dict],
    recommendation_type: str,
    reasoning: str,
) -> str:
    """Build a template-based case summary for reviewers."""
    invoice = result.invoice
    po = result.purchase_order
    mode = getattr(result, "reconciliation_mode", "") or ""
    mode_label = "2-way" if mode == "TWO_WAY" else "3-way"

    rec_label = RecommendationType(recommendation_type).label

    exc_lines = []
    for exc in exceptions:
        if not exc.get("resolved"):
            exc_lines.append(
                f"  - {exc['exception_type']}: {exc.get('message', 'N/A')}"
            )
    exc_block = "\n".join(exc_lines) if exc_lines else "  (none)"

    vendor_name = (
        invoice.vendor.name if invoice.vendor else invoice.raw_vendor_name or "N/A"
    )

    summary = (
        f"Case Summary — Invoice #{invoice.invoice_number or invoice.pk}\n"
        f"{'=' * 50}\n"
        f"Reconciliation Mode: {mode_label}\n"
        f"Match Status: {result.match_status}\n"
        f"Invoice: #{invoice.invoice_number or 'N/A'} | "
        f"Vendor: {vendor_name} | "
        f"Amount: {invoice.total_amount or 'N/A'} {invoice.currency or ''}\n"
        f"PO: {po.po_number if po else 'N/A'}\n"
    )

    if mode != "TWO_WAY":
        summary += (
            f"GRN Available: {'Yes' if result.grn_available else 'No'} | "
            f"Fully Received: {'Yes' if result.grn_fully_received else 'No'}\n"
        )

    summary += (
        f"\nExceptions ({len(exc_lines)}):\n{exc_block}\n\n"
        f"Analysis: {reasoning}\n"
        f"Recommended Action: {rec_label}\n"
    )

    return summary


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class DeterministicResolver:
    """Rule-based resolver replacing EXCEPTION_ANALYSIS + REVIEW_ROUTING + CASE_SUMMARY.

    Applies a priority-ordered rule matrix to map exception types/severities
    to recommendation types.  Always produces a resolution (never returns None)
    because the default rule covers all remaining cases.
    """

    # Agent types this resolver replaces (used by orchestrator to partition the plan)
    REPLACED_AGENTS: Set[str] = {"EXCEPTION_ANALYSIS", "REVIEW_ROUTING", "CASE_SUMMARY"}

    def resolve(
        self,
        result,
        exceptions: List[Dict],
        prior_recommendation: Optional[str] = None,
        prior_confidence: float = 0.0,
    ) -> DeterministicResolution:
        """Produce a deterministic resolution for the given reconciliation result.

        Args:
            result: ReconciliationResult instance.
            exceptions: List of exception dicts (from result.exceptions.values()).
            prior_recommendation: recommendation_type from a prior LLM agent
                (e.g. RECONCILIATION_ASSIST), if any.
            prior_confidence: The confidence of the prior recommendation.
        """
        # Rule 0 (priority): Respect a prior agent's AUTO_CLOSE even when there
        # are no exceptions. This must run BEFORE the empty-exceptions early return
        # so that a high-confidence AUTO_CLOSE recommendation is never silently
        # discarded just because the exception list happens to be empty.
        if (
            prior_recommendation == RecommendationType.AUTO_CLOSE
            and prior_confidence >= _PRIOR_AUTO_CLOSE_CONFIDENCE
        ):
            reasoning = (
                f"Prior agent recommended AUTO_CLOSE with confidence {prior_confidence:.2f} "
                f">= {_PRIOR_AUTO_CLOSE_CONFIDENCE} threshold. Respecting prior recommendation."
            )
            return DeterministicResolution(
                recommendation_type=RecommendationType.AUTO_CLOSE,
                confidence=prior_confidence,
                reasoning=reasoning,
                evidence=_build_evidence(exceptions),
                case_summary=_build_case_summary(
                    result, exceptions, RecommendationType.AUTO_CLOSE, reasoning,
                ),
            )

        if not exceptions:
            return DeterministicResolution(
                recommendation_type=RecommendationType.SEND_TO_AP_REVIEW,
                confidence=0.95,
                reasoning="No exceptions found but status requires review — routing to AP for manual check.",
                evidence=_build_evidence(exceptions),
                case_summary=_build_case_summary(
                    result, exceptions, RecommendationType.SEND_TO_AP_REVIEW,
                    "No exceptions but flagged for review.",
                ),
            )

        # Use only active (unresolved) exceptions for routing decisions
        active = [e for e in exceptions if not e.get("resolved")]
        if not active:
            active = exceptions  # Fallback: use all if everything is resolved

        exc_types: Set[str] = {e["exception_type"] for e in active}
        severities: Set[str] = {e.get("severity", "MEDIUM") for e in active}

        rec_type, confidence, reasoning = self._apply_rules(
            exc_types, severities, active, result,
            prior_recommendation, prior_confidence,
        )

        evidence = _build_evidence(active)
        case_summary = _build_case_summary(result, active, rec_type, reasoning)

        logger.info(
            "Deterministic resolution for result %s: %s (confidence=%.2f, exceptions=%s)",
            result.pk, rec_type, confidence, sorted(exc_types),
        )

        return DeterministicResolution(
            recommendation_type=rec_type,
            confidence=confidence,
            reasoning=reasoning,
            evidence=evidence,
            case_summary=case_summary,
        )

    # ------------------------------------------------------------------
    # Rule matrix
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_rules(
        exc_types: Set[str],
        severities: Set[str],
        exceptions: List[Dict],
        result,
        prior_recommendation: Optional[str],
        prior_confidence: float,
    ) -> tuple:
        """Apply priority-ordered rules.

        Returns (recommendation_type, confidence, reasoning).
        """
        # Rule 0: Respect a prior agent's AUTO_CLOSE recommendation
        if (
            prior_recommendation == RecommendationType.AUTO_CLOSE
            and prior_confidence >= _PRIOR_AUTO_CLOSE_CONFIDENCE
        ):
            return (
                RecommendationType.AUTO_CLOSE,
                prior_confidence,
                f"Prior agent analysis recommends auto-close with confidence "
                f"{prior_confidence:.0%} — discrepancies deemed within acceptable range.",
            )

        # Rule 1: Low extraction confidence → reprocess
        if ExceptionType.EXTRACTION_LOW_CONFIDENCE in exc_types:
            other_count = len(exc_types) - 1
            suffix = (
                f" {other_count} additional exception(s) may be artifacts of poor extraction."
                if other_count else ""
            )
            return (
                RecommendationType.REPROCESS_EXTRACTION,
                0.95,
                f"Extraction confidence below threshold — document should be reprocessed "
                f"for accurate data.{suffix}",
            )

        # Rule 2: Vendor mismatch -> vendor clarification
        if ExceptionType.VENDOR_MISMATCH in exc_types:
            other = exc_types - {ExceptionType.VENDOR_MISMATCH}
            suffix = f" Additional issues: {sorted(other)}." if other else ""
            return (
                RecommendationType.SEND_TO_VENDOR_CLARIFICATION,
                0.95,
                f"Vendor on invoice does not match PO vendor -- "
                f"requires vendor clarification.{suffix}",
            )

        # Rule 2b: Vendor not verified (Non-PO) -> AP review
        if ExceptionType.VENDOR_NOT_VERIFIED in exc_types:
            other = exc_types - {ExceptionType.VENDOR_NOT_VERIFIED}
            suffix = f" Additional issues: {sorted(other)}." if other else ""
            return (
                RecommendationType.SEND_TO_AP_REVIEW,
                0.90,
                f"Vendor on invoice could not be verified (not linked or inactive) "
                f"-- requires AP review.{suffix}",
            )

        # Rule 3: GRN / receipt issues → procurement
        procurement_issues = exc_types & _PROCUREMENT_EXCEPTION_TYPES
        if procurement_issues:
            return (
                RecommendationType.SEND_TO_PROCUREMENT,
                0.90,
                f"Receipt/GRN issues detected ({sorted(procurement_issues)}) — "
                f"routing to procurement team.",
            )

        # Rule 4: Complex case (3+ independent issue categories with HIGH severity)
        # Group correlated numeric mismatches as one category to avoid
        # false escalation for natural cascading (e.g. qty→amount→tax).
        categories: Set[str] = set()
        for t in exc_types:
            if t in _NUMERIC_MISMATCH_TYPES:
                categories.add("numeric")
            else:
                categories.add(t)

        if len(categories) >= 3 and ExceptionSeverity.HIGH in severities:
            return (
                RecommendationType.ESCALATE_TO_MANAGER,
                0.85,
                f"Complex case with {len(categories)} independent issue categories "
                f"including high-severity issues — escalating to manager for review.",
            )

        # Rule 5: Standard exceptions → AP review (default)
        readable = sorted(exc_types)
        return (
            RecommendationType.SEND_TO_AP_REVIEW,
            0.90,
            f"Standard reconciliation exceptions ({readable}) — routing to AP review.",
        )
