"""
Tests for PolicyEngine — DB-backed (needs ReconciliationResult + exceptions).

Decision matrix (from source):
  Rule 1:  MATCHED + confidence >= 0.95 → skip_agents=True
  Rule 1b: PARTIAL_MATCH + all within auto-close band + no HIGH exceptions → auto_close=True
  Rule 2:  PO_NOT_FOUND exception → PO_RETRIEVAL queued
  Rule 3:  GRN_NOT_FOUND (3-way only, NOT in 2-way) → GRN_RETRIEVAL queued
  Rule 4:  extraction_confidence < 0.70 → INVOICE_UNDERSTANDING queued
  Rule 5:  PARTIAL_MATCH (outside band) → RECONCILIATION_ASSIST queued
  Always:  if any agents queued → REVIEW_ROUTING + CASE_SUMMARY appended last
  Fallback: REQUIRES_REVIEW/UNMATCHED/ERROR with no specific agents → EXCEPTION_ANALYSIS + REVIEW_ROUTING + CASE_SUMMARY
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from apps.agents.services.policy_engine import PolicyEngine, AgentPlan
from apps.core.enums import AgentType, ExceptionType, ExceptionSeverity, MatchStatus, ReconciliationMode
from apps.core.constants import REVIEW_AUTO_CLOSE_THRESHOLD, AGENT_CONFIDENCE_THRESHOLD


# ─── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def recon_result(db):
    from apps.reconciliation.tests.factories import ReconConfigFactory, InvoiceFactory, POFactory
    from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
    from apps.core.enums import ReconciliationRunStatus

    config = ReconConfigFactory()
    invoice = InvoiceFactory(extraction_confidence=0.95)
    po = POFactory()
    run = ReconciliationRun.objects.create(
        status=ReconciliationRunStatus.RUNNING,
        config=config,
    )
    result = ReconciliationResult.objects.create(
        run=run,
        invoice=invoice,
        purchase_order=po,
        match_status=MatchStatus.MATCHED,
        deterministic_confidence=0.95,
        extraction_confidence=0.95,
        reconciliation_mode=ReconciliationMode.THREE_WAY,
    )
    return result


def add_exception(result, exc_type, severity=ExceptionSeverity.MEDIUM):
    from apps.reconciliation.models import ReconciliationException
    return ReconciliationException.objects.create(
        result=result,
        exception_type=exc_type,
        severity=severity,
        message=f"Test exception: {exc_type}",
    )


engine = PolicyEngine()


# ─── Rule 1: MATCHED + high confidence → skip ────────────────────────────────

@pytest.mark.django_db
class TestRule1MatchedSkip:
    def test_matched_high_confidence_skips_agents(self, recon_result):
        """MATCHED + confidence >= threshold → skip_agents=True."""
        recon_result.match_status = MatchStatus.MATCHED
        recon_result.deterministic_confidence = REVIEW_AUTO_CLOSE_THRESHOLD
        recon_result.save()

        plan = engine.plan(recon_result)
        assert plan.skip_agents is True
        assert plan.auto_close is False

    def test_matched_low_confidence_does_not_skip(self, recon_result):
        """MATCHED but low confidence → agents still run."""
        recon_result.match_status = MatchStatus.MATCHED
        recon_result.deterministic_confidence = 0.50
        recon_result.save()

        plan = engine.plan(recon_result)
        assert plan.skip_agents is False


# ─── Rule 1b: PARTIAL_MATCH auto-close band ───────────────────────────────────

@pytest.mark.django_db
class TestRule1bAutoClose:
    def test_partial_match_within_band_no_high_exc_auto_closes(self, recon_result):
        """PARTIAL_MATCH + all lines within auto-close band + no HIGH → auto_close=True."""
        from apps.reconciliation.models import ReconciliationResultLine
        recon_result.match_status = MatchStatus.PARTIAL_MATCH
        recon_result.deterministic_confidence = 0.60
        recon_result.save()

        # Create line within auto-close band (3% amount tolerance)
        ReconciliationResultLine.objects.create(
            result=recon_result,
            match_status=MatchStatus.PARTIAL_MATCH,
            qty_invoice=Decimal("10"),
            qty_po=Decimal("10"),
            price_invoice=Decimal("100"),
            price_po=Decimal("100"),
            amount_invoice=Decimal("1020"),   # 2% diff — within 3% auto-close
            amount_po=Decimal("1000"),
        )

        plan = engine.plan(recon_result)
        assert plan.skip_agents is True
        assert plan.auto_close is True

    def test_partial_match_with_high_exception_not_auto_closed(self, recon_result):
        """PARTIAL_MATCH + HIGH severity exception → auto-close blocked."""
        from apps.reconciliation.models import ReconciliationResultLine
        recon_result.match_status = MatchStatus.PARTIAL_MATCH
        recon_result.save()

        add_exception(recon_result, ExceptionType.VENDOR_MISMATCH, ExceptionSeverity.HIGH)
        ReconciliationResultLine.objects.create(
            result=recon_result,
            match_status=MatchStatus.PARTIAL_MATCH,
            amount_invoice=Decimal("1000"),
            amount_po=Decimal("1000"),
        )

        plan = engine.plan(recon_result)
        assert plan.auto_close is False

    def test_partial_match_with_grn_not_found_in_3way_blocks_auto_close(self, recon_result):
        """GRN_NOT_FOUND in 3-way mode blocks auto-close."""
        recon_result.match_status = MatchStatus.PARTIAL_MATCH
        recon_result.reconciliation_mode = ReconciliationMode.THREE_WAY
        recon_result.save()

        add_exception(recon_result, ExceptionType.GRN_NOT_FOUND)

        plan = engine.plan(recon_result)
        assert plan.auto_close is False


# ─── Rule 2: PO_NOT_FOUND → PO_RETRIEVAL ─────────────────────────────────────

@pytest.mark.django_db
class TestRule2PORetrieval:
    def test_po_not_found_queues_po_retrieval(self, recon_result):
        """PO_NOT_FOUND exception → PO_RETRIEVAL in agents."""
        recon_result.match_status = MatchStatus.UNMATCHED
        recon_result.save()
        add_exception(recon_result, ExceptionType.PO_NOT_FOUND)

        plan = engine.plan(recon_result)

        assert AgentType.PO_RETRIEVAL in plan.agents
        assert AgentType.REVIEW_ROUTING in plan.agents
        assert AgentType.CASE_SUMMARY in plan.agents

    def test_review_routing_and_case_summary_always_last(self, recon_result):
        """REVIEW_ROUTING and CASE_SUMMARY are always the last two agents."""
        recon_result.match_status = MatchStatus.UNMATCHED
        recon_result.save()
        add_exception(recon_result, ExceptionType.PO_NOT_FOUND)

        plan = engine.plan(recon_result)

        assert plan.agents[-1] == AgentType.CASE_SUMMARY
        assert plan.agents[-2] == AgentType.REVIEW_ROUTING


# ─── Rule 3: GRN_NOT_FOUND — mode-aware ──────────────────────────────────────

@pytest.mark.django_db
class TestRule3GRNRetrieval:
    def test_grn_not_found_in_3way_queues_grn_retrieval(self, recon_result):
        """GRN_NOT_FOUND in THREE_WAY mode → GRN_RETRIEVAL queued."""
        recon_result.match_status = MatchStatus.REQUIRES_REVIEW
        recon_result.reconciliation_mode = ReconciliationMode.THREE_WAY
        recon_result.save()
        add_exception(recon_result, ExceptionType.GRN_NOT_FOUND)

        plan = engine.plan(recon_result)

        assert AgentType.GRN_RETRIEVAL in plan.agents

    def test_grn_not_found_in_2way_not_queued(self, recon_result):
        """GRN_NOT_FOUND in TWO_WAY mode → GRN_RETRIEVAL NOT queued (irrelevant)."""
        recon_result.match_status = MatchStatus.REQUIRES_REVIEW
        recon_result.reconciliation_mode = ReconciliationMode.TWO_WAY
        recon_result.save()
        add_exception(recon_result, ExceptionType.GRN_NOT_FOUND)

        plan = engine.plan(recon_result)

        assert AgentType.GRN_RETRIEVAL not in plan.agents


# ─── Rule 4: Low extraction confidence → INVOICE_UNDERSTANDING ───────────────

@pytest.mark.django_db
class TestRule4InvoiceUnderstanding:
    def test_low_extraction_confidence_queues_understanding(self, recon_result):
        """extraction_confidence < AGENT_CONFIDENCE_THRESHOLD → INVOICE_UNDERSTANDING."""
        recon_result.match_status = MatchStatus.REQUIRES_REVIEW
        recon_result.extraction_confidence = AGENT_CONFIDENCE_THRESHOLD - 0.01
        recon_result.save()
        add_exception(recon_result, ExceptionType.EXTRACTION_LOW_CONFIDENCE)

        plan = engine.plan(recon_result)

        assert AgentType.INVOICE_UNDERSTANDING in plan.agents

    def test_high_extraction_confidence_not_queued(self, recon_result):
        """High extraction confidence → INVOICE_UNDERSTANDING NOT queued."""
        recon_result.match_status = MatchStatus.REQUIRES_REVIEW
        recon_result.extraction_confidence = 0.95
        recon_result.save()
        add_exception(recon_result, ExceptionType.AMOUNT_MISMATCH)

        plan = engine.plan(recon_result)

        assert AgentType.INVOICE_UNDERSTANDING not in plan.agents


# ─── Rule 5: PARTIAL_MATCH → RECONCILIATION_ASSIST ───────────────────────────

@pytest.mark.django_db
class TestRule5ReconciliationAssist:
    def test_partial_match_outside_band_queues_assist(self, recon_result):
        """PARTIAL_MATCH (outside auto-close band) → RECONCILIATION_ASSIST."""
        from apps.reconciliation.models import ReconciliationResultLine
        recon_result.match_status = MatchStatus.PARTIAL_MATCH
        recon_result.save()
        add_exception(recon_result, ExceptionType.AMOUNT_MISMATCH)

        # Add line outside auto-close band (10% difference)
        ReconciliationResultLine.objects.create(
            result=recon_result,
            match_status=MatchStatus.PARTIAL_MATCH,
            amount_invoice=Decimal("1100"),  # 10% — outside 3% band
            amount_po=Decimal("1000"),
        )

        plan = engine.plan(recon_result)

        assert AgentType.RECONCILIATION_ASSIST in plan.agents


# ─── Fallback: REQUIRES_REVIEW with no specific trigger ──────────────────────

@pytest.mark.django_db
class TestFallbackRequiresReview:
    def test_requires_review_no_specific_agents_uses_fallback(self, recon_result):
        """REQUIRES_REVIEW with no specific exceptions → EXCEPTION_ANALYSIS + tail."""
        recon_result.match_status = MatchStatus.REQUIRES_REVIEW
        recon_result.extraction_confidence = 0.95
        recon_result.save()

        plan = engine.plan(recon_result)

        assert AgentType.EXCEPTION_ANALYSIS in plan.agents
        assert AgentType.REVIEW_ROUTING in plan.agents
        assert AgentType.CASE_SUMMARY in plan.agents

    def test_unmatched_uses_fallback(self, recon_result):
        """UNMATCHED with no PO_NOT_FOUND exception uses fallback."""
        recon_result.match_status = MatchStatus.UNMATCHED
        recon_result.extraction_confidence = 0.95
        recon_result.save()

        plan = engine.plan(recon_result)

        assert len(plan.agents) >= 3


# ─── should_auto_close / should_escalate ─────────────────────────────────────

class TestPostRunChecks:
    def test_should_auto_close_with_auto_close_recommendation_and_high_confidence(self):
        from apps.core.enums import RecommendationType
        assert engine.should_auto_close(RecommendationType.AUTO_CLOSE, 0.96) is True

    def test_should_auto_close_with_low_confidence_false(self):
        from apps.core.enums import RecommendationType
        assert engine.should_auto_close(RecommendationType.AUTO_CLOSE, 0.50) is False

    def test_should_auto_close_wrong_recommendation_false(self):
        from apps.core.enums import RecommendationType
        assert engine.should_auto_close(RecommendationType.SEND_TO_AP_REVIEW, 0.99) is False

    def test_should_escalate_on_escalate_recommendation(self):
        from apps.core.enums import RecommendationType
        assert engine.should_escalate(RecommendationType.ESCALATE_TO_MANAGER, 0.99) is True

    def test_should_escalate_on_low_confidence(self):
        from apps.core.enums import RecommendationType
        assert engine.should_escalate(RecommendationType.SEND_TO_AP_REVIEW, 0.50) is True
