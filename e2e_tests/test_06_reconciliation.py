"""
TEST 06 -- 3-Way Reconciliation Engine
========================================
Covers:
  - ReconciliationRunnerService
  - TwoWayMatchService + ThreeWayMatchService
  - LineMatchService v2 (11-signal scorer)
  - ToleranceEngine
  - ReconciliationModeResolver
  - ReconciliationPolicy
  - Reconciliation UI pages
  - Reconciliation API endpoints
"""

import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db(transaction=False)


class TestReconciliationModels:
    """Reconciliation model imports."""

    def test_recon_run_model(self):
        from apps.reconciliation.models import ReconciliationRun
        assert ReconciliationRun is not None

    def test_recon_result_model(self):
        from apps.reconciliation.models import ReconciliationResult
        assert ReconciliationResult is not None

    def test_recon_result_line_model(self):
        from apps.reconciliation.models import ReconciliationResultLine
        assert ReconciliationResultLine is not None

    def test_recon_exception_model(self):
        from apps.reconciliation.models import ReconciliationException
        assert ReconciliationException is not None

    def test_recon_config_model(self):
        from apps.reconciliation.models import ReconciliationConfig
        assert ReconciliationConfig is not None

    def test_recon_policy_model(self):
        from apps.reconciliation.models import ReconciliationPolicy
        assert ReconciliationPolicy is not None


class TestToleranceEngine:
    """Tolerance engine: tiered strict + auto-close bands."""

    def test_tolerance_engine_importable(self):
        from apps.reconciliation.services.tolerance_engine import ToleranceEngine
        assert ToleranceEngine is not None

    def test_strict_tolerance_passes_exact(self):
        from apps.reconciliation.services.tolerance_engine import ToleranceEngine
        engine = ToleranceEngine()
        result = engine.compare_amount(
            inv_amount=Decimal("10000.00"),
            po_amount=Decimal("10000.00"),
        )
        assert result.within_tolerance is True

    def test_strict_tolerance_fails_large_variance(self):
        from apps.reconciliation.services.tolerance_engine import ToleranceEngine
        engine = ToleranceEngine()
        result = engine.compare_amount(
            inv_amount=Decimal("12000.00"),
            po_amount=Decimal("10000.00"),
        )
        assert result.within_tolerance is False, \
            "20% variance should fail strict tolerance"


class TestLineMatchService:
    """LineMatchService v2 -- 11-signal scorer."""

    def test_line_match_service_importable(self):
        from apps.reconciliation.services.line_match_service import LineMatchService
        assert LineMatchService is not None

    def test_line_match_types_importable(self):
        from apps.reconciliation.services.line_match_types import (
            LineMatchDecision, LineCandidateScore, LLMFallbackResult
        )
        assert LineMatchDecision is not None
        assert LineCandidateScore is not None
        assert LLMFallbackResult is not None

    def test_line_match_helpers_importable(self):
        from apps.reconciliation.services.line_match_helpers import (
            normalize_line_text, token_similarity
        )
        assert normalize_line_text is not None
        assert token_similarity is not None

    def test_normalize_text_strips_whitespace(self):
        from apps.reconciliation.services.line_match_helpers import normalize_line_text
        result = normalize_line_text("  HVAC   UNIT  ")
        assert result == result.strip()
        assert "  " not in result, "normalize_text should collapse whitespace"

    def test_token_similarity_identical(self):
        from apps.reconciliation.services.line_match_helpers import token_similarity
        score = token_similarity("HVAC compressor unit", "HVAC compressor unit")
        assert score >= 0.99, f"Identical strings should score ~1.0, got {score}"

    def test_token_similarity_different(self):
        from apps.reconciliation.services.line_match_helpers import token_similarity
        score = token_similarity("refrigerator", "completely different item xyz")
        assert score < 0.5, f"Different strings should score < 0.5, got {score}"


class TestReconciliationModeResolver:
    """ReconciliationModeResolver -- 3-tier mode cascade."""

    def test_mode_resolver_importable(self):
        from apps.reconciliation.services.mode_resolver import ReconciliationModeResolver
        assert ReconciliationModeResolver is not None

    def test_two_way_match_service_importable(self):
        from apps.reconciliation.services.two_way_match_service import TwoWayMatchService
        assert TwoWayMatchService is not None

    def test_three_way_match_service_importable(self):
        from apps.reconciliation.services.three_way_match_service import ThreeWayMatchService
        assert ThreeWayMatchService is not None


class TestReconciliationRunnerService:
    """ReconciliationRunnerService core orchestration."""

    def test_runner_service_importable(self):
        from apps.reconciliation.services.runner_service import ReconciliationRunnerService
        assert ReconciliationRunnerService is not None

    def test_runner_has_run_method(self):
        from apps.reconciliation.services.runner_service import ReconciliationRunnerService
        assert hasattr(ReconciliationRunnerService, "run") or \
               hasattr(ReconciliationRunnerService, "execute"), \
            "ReconciliationRunnerService needs run() or execute()"

    def test_recon_task_importable(self):
        from apps.reconciliation.tasks import run_reconciliation_task
        assert run_reconciliation_task is not None


class TestReconciliationUI:
    """Reconciliation UI pages."""

    RECON_URLS = [
        "/reconciliation/",
        "/reconciliation/settings/",
        "/reconciliation/policies/",
    ]

    def test_reconciliation_pages_no_500(self, admin_client):
        failures = []
        for url in self.RECON_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These recon pages returned 500: {failures}"

    def test_reconciliation_api_list(self, admin_client):
        r = admin_client.get("/api/v1/")
        assert r.status_code in (200, 404)

    def test_reconciliation_eval_adapter_importable(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter
        assert ReconciliationEvalAdapter is not None
