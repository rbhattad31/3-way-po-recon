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

    vendor_name = (
        invoice.vendor.name if invoice.vendor else invoice.raw_vendor_name or "N/A"
    )

    summary = (
        f"Case Summary -- Invoice #{invoice.invoice_number or invoice.pk}\n"
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

    # Build human-readable issue descriptions from exception details
    active = [e for e in exceptions if not e.get("resolved")]
    if active:
        summary += f"\nIssues Found ({len(active)}):\n"
        for exc in active:
            summary += f"\n  [{exc.get('severity', 'MEDIUM')}] "
            summary += _describe_exception(exc)
    else:
        summary += "\nIssues Found: (none)\n"

    # Action required section
    actions = _build_action_items(active)
    if actions:
        summary += "\nAction Required:\n"
        for i, action in enumerate(actions, 1):
            summary += f"  {i}. {action}\n"

    summary += f"\nRecommended Action: {rec_label}\n"

    return summary


def _describe_exception(exc: Dict) -> str:
    """Produce a human-readable description from an exception dict."""
    exc_type = exc.get("exception_type", "")
    message = exc.get("message", "")
    details = exc.get("details") or {}

    if exc_type == ExceptionType.MISSING_MANDATORY_FIELDS:
        missing = details.get("missing_fields", [])
        if missing:
            return f"Missing mandatory fields: {', '.join(missing)}"
        return message or "Missing mandatory fields (unspecified)"

    if exc_type == ExceptionType.VENDOR_NOT_VERIFIED:
        return (
            "Vendor is not linked to a verified vendor in master data. "
            "If the vendor is already known, link it via the vendor page."
        )

    if exc_type == ExceptionType.VENDOR_MISMATCH:
        inv_v = details.get("invoice_vendor", "?")
        po_v = details.get("po_vendor", "?")
        return f"Vendor mismatch: invoice vendor='{inv_v}' vs PO vendor='{po_v}'"

    if exc_type == ExceptionType.AMOUNT_MISMATCH:
        inv_amt = details.get("invoice_total") or details.get("invoice_amount", "?")
        po_amt = details.get("po_total") or details.get("po_amount", "?")
        diff_pct = details.get("difference_pct", "")
        diff = details.get("difference", "")
        parts = f"Amount mismatch: invoice={inv_amt} vs PO={po_amt}"
        if diff:
            parts += f" (diff={diff}"
            if diff_pct:
                parts += f", {diff_pct}%"
            parts += ")"
        # Check if this is really a supporting-docs issue misclassified
        if details.get("source") == "non_po_validation":
            req_docs = details.get("required_documents", [])
            if req_docs:
                return (
                    f"Supporting documents required for amount {details.get('amount', '?')}: "
                    f"{', '.join(d.replace('_', ' ') for d in req_docs)}"
                )
            check_name = details.get("check_name", "")
            if check_name and check_name != "amount":
                return message or parts
        return parts

    if exc_type == ExceptionType.QTY_MISMATCH:
        ln = details.get("line_number", "?")
        inv_q = details.get("invoice_qty", "?")
        po_q = details.get("po_qty", "?")
        pct = details.get("difference_pct", "")
        desc = f"Line {ln}: quantity mismatch -- invoice={inv_q} vs PO={po_q}"
        if pct:
            desc += f" ({pct}% off)"
        return desc

    if exc_type == ExceptionType.PRICE_MISMATCH:
        ln = details.get("line_number", "?")
        inv_p = details.get("invoice_price", "?")
        po_p = details.get("po_price", "?")
        pct = details.get("difference_pct", "")
        desc = f"Line {ln}: unit price mismatch -- invoice={inv_p} vs PO={po_p}"
        if pct:
            desc += f" ({pct}% off)"
        return desc

    if exc_type == ExceptionType.TAX_MISMATCH:
        inv_t = details.get("invoice_tax", "?")
        po_t = details.get("po_tax", "?")
        pct = details.get("difference_pct", "")
        if details.get("source") == "non_po_validation":
            return message or "Tax calculation needs review"
        desc = f"Tax mismatch: invoice={inv_t} vs PO={po_t}"
        if pct:
            desc += f" ({pct}% off)"
        return desc

    if exc_type == ExceptionType.TAX_RATE_MISMATCH:
        ln = details.get("line_number", "?")
        inv_r = details.get("invoice_tax_rate", "?")
        po_r = details.get("po_effective_tax_rate", "?")
        return f"Line {ln}: tax rate mismatch -- invoice={inv_r}% vs PO={po_r}%"

    if exc_type == ExceptionType.DUPLICATE_INVOICE:
        dup_of = details.get("duplicate_of_invoice", details.get("duplicate_of_id", "?"))
        return f"Possible duplicate of Invoice #{dup_of}"

    if exc_type == ExceptionType.EXTRACTION_LOW_CONFIDENCE:
        conf = details.get("confidence", "?")
        thresh = details.get("threshold", "?")
        return f"Extraction confidence {conf} below threshold {thresh} -- data may be unreliable"

    if exc_type == ExceptionType.ITEM_MISMATCH:
        ln = details.get("line_number", "?")
        desc = details.get("description", "")
        text = f"Line {ln}: no matching PO line found"
        if desc:
            text += f" (description: '{desc[:60]}')"
        return text

    if exc_type == ExceptionType.PARTIAL_INVOICE:
        covers = details.get("covers_pct", "?")
        po_total = details.get("po_total", "?")
        prior = details.get("prior_invoice_count", 0)
        return (
            f"Partial invoice: covers {covers}% of PO total {po_total}"
            + (f" ({prior} prior invoice(s) on this PO)" if prior else "")
        )

    if exc_type in (ExceptionType.GRN_NOT_FOUND, ExceptionType.RECEIPT_SHORTAGE,
                    ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED):
        return message or exc_type.replace("_", " ").title()

    if exc_type == ExceptionType.CURRENCY_MISMATCH:
        return message or "Invoice and PO currencies do not match"

    if exc_type == ExceptionType.GSTIN_MISMATCH:
        inv_g = details.get("invoice_vendor_tax_id", "?")
        po_g = details.get("po_vendor_gstin", "?")
        return f"Tax ID mismatch: invoice={inv_g} vs PO={po_g}"

    # Fallback: use the raw message
    return message or exc_type.replace("_", " ").title()


def _build_action_items(exceptions: List[Dict]) -> List[str]:
    """Derive specific action items from exception details."""
    actions: List[str] = []
    seen_types: set = set()

    for exc in exceptions:
        exc_type = exc.get("exception_type", "")
        details = exc.get("details") or {}
        if exc_type in seen_types:
            continue
        seen_types.add(exc_type)

        if exc_type == ExceptionType.MISSING_MANDATORY_FIELDS:
            missing = details.get("missing_fields", [])
            if missing:
                actions.append(
                    f"Obtain missing fields ({', '.join(missing)}) from the vendor or source document"
                )
            else:
                actions.append("Verify all mandatory fields are populated")

        elif exc_type == ExceptionType.VENDOR_NOT_VERIFIED:
            actions.append(
                "Link invoice vendor to a verified vendor record, or verify the vendor in vendor master"
            )

        elif exc_type == ExceptionType.VENDOR_MISMATCH:
            actions.append(
                "Confirm with procurement whether the vendor change is authorized"
            )

        elif exc_type in (ExceptionType.AMOUNT_MISMATCH, ExceptionType.QTY_MISMATCH,
                          ExceptionType.PRICE_MISMATCH):
            if details.get("source") == "non_po_validation":
                req_docs = details.get("required_documents", [])
                if req_docs:
                    actions.append(
                        f"Attach required supporting documents: {', '.join(d.replace('_', ' ') for d in req_docs)}"
                    )
                    continue
            actions.append(
                "Review the amount/quantity/price difference and confirm with the vendor or PO owner"
            )

        elif exc_type == ExceptionType.DUPLICATE_INVOICE:
            actions.append("Verify this is not a duplicate payment -- compare with the flagged invoice")

        elif exc_type == ExceptionType.EXTRACTION_LOW_CONFIDENCE:
            actions.append("Consider re-uploading or manually correcting extracted data")

        elif exc_type in (ExceptionType.GRN_NOT_FOUND, ExceptionType.RECEIPT_SHORTAGE,
                          ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED):
            actions.append("Confirm receipt status with warehouse/procurement team")

        elif exc_type in (ExceptionType.GSTIN_MISMATCH, ExceptionType.COUNTRY_MISMATCH,
                          ExceptionType.SUPPLY_TYPE_MISMATCH, ExceptionType.TAX_RATE_MISMATCH):
            actions.append("Verify tax compliance details with the vendor and tax team")

    return actions


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
            # Check if the vendor is actually linked (the exception may be stale)
            vendor_linked = bool(result.invoice.vendor_id) if result.invoice else False
            if vendor_linked:
                vendor_note = (
                    f"Vendor '{result.invoice.vendor.name}' is linked but was flagged "
                    f"during validation -- confirm vendor status in vendor master."
                )
            else:
                vendor_note = (
                    "Invoice vendor is not linked to a verified vendor record "
                    "-- link or verify the vendor before approving."
                )
            other_detail = ""
            if other:
                other_descs = []
                for exc in exceptions:
                    if exc.get("resolved") or exc.get("exception_type") == ExceptionType.VENDOR_NOT_VERIFIED:
                        continue
                    d = _describe_exception(exc)
                    if d and len(other_descs) < 3:
                        other_descs.append(d)
                if other_descs:
                    other_detail = " Additional issues: " + "; ".join(other_descs) + "."
            return (
                RecommendationType.SEND_TO_AP_REVIEW,
                0.90,
                f"{vendor_note}{other_detail}",
            )

        # Rule 3: GRN / receipt issues -> procurement
        procurement_issues = exc_types & _PROCUREMENT_EXCEPTION_TYPES
        if procurement_issues:
            procurement_descs = []
            for exc in exceptions:
                if exc.get("resolved"):
                    continue
                if exc.get("exception_type") in _PROCUREMENT_EXCEPTION_TYPES:
                    d = _describe_exception(exc)
                    if d and len(procurement_descs) < 3:
                        procurement_descs.append(d)
            detail = "; ".join(procurement_descs) if procurement_descs else str(sorted(procurement_issues))
            return (
                RecommendationType.SEND_TO_PROCUREMENT,
                0.90,
                f"Receipt/GRN issues require procurement review: {detail}.",
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

        # Rule 5: Standard exceptions -> AP review (default)
        # Build a specific reasoning that describes the actual issues
        reasoning_parts = []
        for exc in exceptions:
            if exc.get("resolved"):
                continue
            desc = _describe_exception(exc)
            if desc and len(reasoning_parts) < 4:  # cap at 4 descriptions
                reasoning_parts.append(desc)
        if reasoning_parts:
            reasoning = (
                f"{len(reasoning_parts)} issue(s) require AP review: "
                + "; ".join(reasoning_parts)
                + "."
            )
        else:
            reasoning = (
                f"Reconciliation exceptions ({sorted(exc_types)}) require AP review."
            )
        return (
            RecommendationType.SEND_TO_AP_REVIEW,
            0.90,
            reasoning,
        )
