"""Policy engine — determines which agents to run and in what order.

The policy engine is the "brain" that decides the agentic workflow based on
the deterministic reconciliation outcome.  It enforces:
  - Which agents fire for each match status / exception combination
  - Agent ordering (pipeline)
  - Mode awareness: skips GRN-related agents in 2-way mode
  - Confidence thresholds for auto-close vs. escalation
  - Token budget guardrails
  - Auto-close tolerance band for PARTIAL_MATCH (skip AI when discrepancies
    are within the wider auto-close thresholds)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from apps.core.constants import AGENT_CONFIDENCE_THRESHOLD, REVIEW_AUTO_CLOSE_THRESHOLD
from apps.core.enums import AgentType, ExceptionType, MatchStatus, ReconciliationMode, RecommendationType
from apps.core.utils import within_tolerance
from apps.reconciliation.models import ReconciliationConfig, ReconciliationResult

logger = logging.getLogger(__name__)


@dataclass
class AgentPlan:
    """The sequence of agents the orchestrator should execute."""
    agents: List[str] = field(default_factory=list)  # AgentType values
    reason: str = ""
    skip_agents: bool = False
    auto_close: bool = False  # True when auto-closed by tolerance band
    reconciliation_mode: str = ""  # Propagated to orchestrator/agents
    plan_source: str = "deterministic"   # "deterministic" or "llm"
    plan_confidence: float = 0.0


class PolicyEngine:
    """Decide which agents to run for a given reconciliation result.

    Rules (deterministic, no LLM):
      1. MATCHED + high confidence → no agents needed (auto-close)
      1b. PARTIAL_MATCH + all line discrepancies within auto-close tolerance → auto-close, skip agents
      2. UNMATCHED with PO_NOT_FOUND → PO_RETRIEVAL → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      3. UNMATCHED with GRN_NOT_FOUND (3-way only) → GRN_RETRIEVAL → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      4. PARTIAL_MATCH (outside auto-close band) → RECONCILIATION_ASSIST → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      5. REQUIRES_REVIEW with low extraction confidence → INVOICE_UNDERSTANDING → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      6. REQUIRES_REVIEW (general) → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY

    Mode awareness: In TWO_WAY mode, GRN_RETRIEVAL is never included and
    GRN_NOT_FOUND exceptions are ignored because receipt verification is
    irrelevant for service/non-stock invoices.
    """

    def plan(self, result: ReconciliationResult) -> AgentPlan:
        status = result.match_status
        confidence = result.deterministic_confidence or 0.0
        extraction_conf = result.extraction_confidence or 0.0
        recon_mode = getattr(result, "reconciliation_mode", "") or ""
        is_two_way = recon_mode == ReconciliationMode.TWO_WAY
        is_non_po = recon_mode == ReconciliationMode.NON_PO
        auto_close_enabled = self._is_auto_close_enabled(result)

        # Gather exception types
        exc_types = set(
            result.exceptions.values_list("exception_type", flat=True)
        )

        # Rule 1: Full match, high confidence -> skip agents
        if auto_close_enabled and status == MatchStatus.MATCHED and confidence >= REVIEW_AUTO_CLOSE_THRESHOLD:
            return AgentPlan(
                skip_agents=True,
                reason=f"Full match with confidence {confidence:.2f} >= {REVIEW_AUTO_CLOSE_THRESHOLD}",
                reconciliation_mode=recon_mode,
            )

        # Rule 1b: PARTIAL_MATCH within auto-close tolerance band -> auto-close, skip agents
        #   Exception: GRN_NOT_FOUND in 3-way mode blocks auto-close (goods not confirmed received)
        #   Exception: First-partial invoices (no prior invoices on PO) use
        #   self-comparison so tolerances always pass; they must go to review.
        grn_blocks_close = (
            not is_two_way
            and not is_non_po
            and ExceptionType.GRN_NOT_FOUND in exc_types
        )
        first_partial_blocks_close = any(
            exc.get("is_first_partial") is True
            for exc in result.exceptions.filter(
                exception_type=ExceptionType.PARTIAL_INVOICE,
            ).values_list("details", flat=True)
            if isinstance(exc, dict)
        )
        if (
            auto_close_enabled
            and
            status == MatchStatus.PARTIAL_MATCH
            and not grn_blocks_close
            and not first_partial_blocks_close
            and not is_non_po
            and self._within_auto_close_band(result)
        ):
            return AgentPlan(
                skip_agents=True,
                auto_close=True,
                reason=(
                    f"PARTIAL_MATCH but all line discrepancies within auto-close tolerance band -- "
                    f"auto-closing without AI agents"
                ),
                reconciliation_mode=recon_mode,
            )

        agents: List[str] = []

        # Exception types that trigger a dedicated compliance review.
        _COMPLIANCE_TYPES = {
            ExceptionType.DUPLICATE_INVOICE,
            ExceptionType.TAX_MISMATCH,
            ExceptionType.VENDOR_MISMATCH,
            ExceptionType.MISSING_MANDATORY_FIELDS,
        }
        has_compliance_exceptions = bool(_COMPLIANCE_TYPES & exc_types)

        # ------------------------------------------------------------------
        # NON_PO mode: no PO/GRN retrieval or reconciliation assist.
        # Focus on exception analysis, vendor verification, and routing.
        # ------------------------------------------------------------------
        if is_non_po:
            # Low extraction confidence -> understand the invoice better
            if extraction_conf < AGENT_CONFIDENCE_THRESHOLD:
                agents.append(AgentType.INVOICE_UNDERSTANDING)

            # Always analyse exceptions for NON_PO (validation failures
            # are persisted as exceptions by the case pipeline).
            if exc_types:
                agents.append(AgentType.EXCEPTION_ANALYSIS)

            # Compliance review for NON_PO invoices that carry compliance risk
            # (these have no PO anchor so policy violations are higher risk).
            if has_compliance_exceptions and AgentType.COMPLIANCE_AGENT not in agents:
                agents.append(AgentType.COMPLIANCE_AGENT)

            # Route and summarise
            agents.append(AgentType.REVIEW_ROUTING)
            agents.append(AgentType.CASE_SUMMARY)

            reason = (
                f"Mode=non-po, status={status}, confidence={confidence:.2f}, "
                f"extraction_conf={extraction_conf:.2f}, "
                f"exceptions={sorted(exc_types)}"
            )
            logger.info("Policy plan (NON_PO) for result %s: %s (%s)", result.pk, agents, reason)
            return AgentPlan(agents=agents, reason=reason, reconciliation_mode=recon_mode)

        # ------------------------------------------------------------------
        # PO-backed modes (TWO_WAY / THREE_WAY)
        # ------------------------------------------------------------------

        # Rule 2: PO not found
        if ExceptionType.PO_NOT_FOUND in exc_types:
            agents.append(AgentType.PO_RETRIEVAL)

        # Rule 3: GRN not found (3-way only — irrelevant in 2-way mode)
        if not is_two_way and ExceptionType.GRN_NOT_FOUND in exc_types:
            agents.append(AgentType.GRN_RETRIEVAL)

        # Rule 4: Low extraction confidence
        if extraction_conf < AGENT_CONFIDENCE_THRESHOLD:
            agents.append(AgentType.INVOICE_UNDERSTANDING)

        # Rule 5: Partial match → reconciliation assist
        if status == MatchStatus.PARTIAL_MATCH:
            if AgentType.RECONCILIATION_ASSIST not in agents:
                agents.append(AgentType.RECONCILIATION_ASSIST)

        # Always include exception analysis if we have exceptions
        if exc_types and AgentType.EXCEPTION_ANALYSIS not in agents:
            agents.append(AgentType.EXCEPTION_ANALYSIS)

        # Rule 7: Compliance review after exception analysis for compliance-sensitive exceptions.
        if has_compliance_exceptions and AgentType.COMPLIANCE_AGENT not in agents:
            agents.append(AgentType.COMPLIANCE_AGENT)

        # Always route and summarise
        if agents:  # Only if we're running any agents
            agents.append(AgentType.REVIEW_ROUTING)
            agents.append(AgentType.CASE_SUMMARY)

        # Fallback: if REQUIRES_REVIEW but no specific agents queued
        if not agents and status in (MatchStatus.REQUIRES_REVIEW, MatchStatus.UNMATCHED, MatchStatus.ERROR):
            agents = [
                AgentType.EXCEPTION_ANALYSIS,
                AgentType.REVIEW_ROUTING,
                AgentType.CASE_SUMMARY,
            ]

        mode_label = "2-way" if is_two_way else "3-way"
        reason = (
            f"Mode={mode_label}, status={status}, confidence={confidence:.2f}, "
            f"extraction_conf={extraction_conf:.2f}, "
            f"exceptions={sorted(exc_types)}"
        )

        logger.info("Policy plan for result %s: %s (%s)", result.pk, agents, reason)
        return AgentPlan(agents=agents, reason=reason, reconciliation_mode=recon_mode)

    @staticmethod
    def _is_auto_close_enabled(result: ReconciliationResult) -> bool:
        """Return whether auto-close is enabled for this result's tenant config."""
        config = getattr(getattr(result, "run", None), "config", None)
        if config is None:
            try:
                config = ReconciliationConfig.get_or_create_default(
                    tenant=getattr(result, "tenant", None),
                )
            except Exception:
                config = None
        if config is None:
            return True
        return bool(getattr(config, "auto_close_on_match", True))

    # ------------------------------------------------------------------
    # Auto-close tolerance check
    # ------------------------------------------------------------------
    @staticmethod
    def _within_auto_close_band(result: ReconciliationResult) -> bool:
        """Check if all line discrepancies fall within the wider auto-close tolerance.

        Also verifies no HIGH severity exceptions exist (vendor mismatch,
        PO not found, etc.) — those always need review regardless of numbers.
        """
        # Check for HIGH-severity exceptions that can't be auto-closed
        high_severity_exceptions = result.exceptions.filter(severity="HIGH").exists()
        if high_severity_exceptions:
            return False

        # Load auto-close thresholds from the run's config (or defaults)
        config = getattr(result.run, "config", None) if result.run else None
        if config:
            ac_qty = config.auto_close_qty_tolerance_pct
            ac_price = config.auto_close_price_tolerance_pct
            ac_amount = config.auto_close_amount_tolerance_pct
        else:
            from apps.core.constants import (
                AUTO_CLOSE_QTY_TOLERANCE_PCT,
                AUTO_CLOSE_PRICE_TOLERANCE_PCT,
                AUTO_CLOSE_AMOUNT_TOLERANCE_PCT,
            )
            ac_qty = AUTO_CLOSE_QTY_TOLERANCE_PCT
            ac_price = AUTO_CLOSE_PRICE_TOLERANCE_PCT
            ac_amount = AUTO_CLOSE_AMOUNT_TOLERANCE_PCT

        lines = result.line_results.all()
        if not lines.exists():
            return False

        for ln in lines:
            # Only check matched/partial lines that have comparison data
            if ln.qty_invoice is not None and ln.qty_po is not None:
                if not within_tolerance(ln.qty_invoice, ln.qty_po, ac_qty):
                    return False

            if ln.price_invoice is not None and ln.price_po is not None:
                if not within_tolerance(ln.price_invoice, ln.price_po, ac_price):
                    return False

            if ln.amount_invoice is not None and ln.amount_po is not None:
                if not within_tolerance(ln.amount_invoice, ln.amount_po, ac_amount):
                    return False

        logger.info(
            "Result %s: all lines within auto-close band (qty<=%.1f%%, price<=%.1f%%, amt<=%.1f%%)",
            result.pk, ac_qty, ac_price, ac_amount,
        )
        return True

    # ------------------------------------------------------------------
    # Post-run policy checks
    # ------------------------------------------------------------------
    @staticmethod
    def should_auto_close(recommendation_type: Optional[str], confidence: float) -> bool:
        """Return True if the recommendation + confidence warrants auto-close."""
        return (
            recommendation_type == RecommendationType.AUTO_CLOSE
            and confidence >= REVIEW_AUTO_CLOSE_THRESHOLD
        )

    @staticmethod
    def should_escalate(recommendation_type: Optional[str], confidence: float) -> bool:
        """Return True if the case should be escalated (low-confidence non-trivial issue)."""
        return (
            recommendation_type == RecommendationType.ESCALATE_TO_MANAGER
            or confidence < AGENT_CONFIDENCE_THRESHOLD
        )
