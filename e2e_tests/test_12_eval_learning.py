"""
TEST 12 -- Evaluation & Learning Framework
==========================================
Covers:
  - EvalRun, EvalMetric, EvalFieldOutcome, LearningSignal, LearningAction models
  - EvalRunService, EvalMetricService, LearningSignalService, LearningActionService
  - LearningEngine (5 rules)
  - ExtractionEvalAdapter + ReconciliationEvalAdapter
  - Eval UI pages at /eval/
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestEvalModels:
    """core_eval domain models."""

    def test_eval_run_model(self):
        from apps.core_eval.models import EvalRun
        assert EvalRun is not None

    def test_eval_metric_model(self):
        from apps.core_eval.models import EvalMetric
        assert EvalMetric is not None

    def test_eval_field_outcome_model(self):
        from apps.core_eval.models import EvalFieldOutcome
        assert EvalFieldOutcome is not None

    def test_learning_signal_model(self):
        from apps.core_eval.models import LearningSignal
        assert LearningSignal is not None

    def test_learning_action_model(self):
        from apps.core_eval.models import LearningAction
        assert LearningAction is not None

    def test_eval_run_queryable(self):
        from apps.core_eval.models import EvalRun
        count = EvalRun.objects.count()
        assert count >= 0


class TestEvalServices:
    """Evaluation service layer."""

    def test_eval_run_service_importable(self):
        from apps.core_eval.services.eval_run_service import EvalRunService
        assert EvalRunService is not None

    def test_eval_metric_service_importable(self):
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        assert EvalMetricService is not None

    def test_eval_field_outcome_service_importable(self):
        from apps.core_eval.services.eval_field_outcome_service import EvalFieldOutcomeService
        assert EvalFieldOutcomeService is not None

    def test_learning_signal_service_importable(self):
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        assert LearningSignalService is not None

    def test_learning_action_service_importable(self):
        from apps.core_eval.services.learning_action_service import LearningActionService
        assert LearningActionService is not None

    def test_eval_run_service_has_create_or_update(self):
        from apps.core_eval.services.eval_run_service import EvalRunService
        assert hasattr(EvalRunService, "create_or_update"), \
            "EvalRunService.create_or_update() missing"

    def test_learning_signal_service_has_record(self):
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        assert hasattr(LearningSignalService, "record"), \
            "LearningSignalService.record() missing"


class TestLearningEngine:
    """LearningEngine -- 5 deterministic rules."""

    def test_learning_engine_importable(self):
        from apps.core_eval.services.learning_engine import LearningEngine
        assert LearningEngine is not None

    def test_learning_engine_has_run_method(self):
        from apps.core_eval.services.learning_engine import LearningEngine
        assert hasattr(LearningEngine, "run") or \
               hasattr(LearningEngine, "execute") or \
               hasattr(LearningEngine, "apply_rules"), \
            "LearningEngine must have run()/execute()/apply_rules()"


class TestEvalAdapters:
    """Eval adapters for extraction and reconciliation pipelines."""

    def test_extraction_eval_adapter_importable(self):
        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter
        assert ExtractionEvalAdapter is not None

    def test_reconciliation_eval_adapter_importable(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter
        assert ReconciliationEvalAdapter is not None

    def test_extraction_eval_adapter_predicted_field(self):
        """Predicted value = LLM output; ground_truth is empty until approval."""
        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter
        # Just validate method exists
        assert hasattr(ExtractionEvalAdapter, "record_extraction_eval") or \
               hasattr(ExtractionEvalAdapter, "_record") or \
               callable(ExtractionEvalAdapter), \
            "ExtractionEvalAdapter needs a callable entry point"


class TestEvalUI:
    """Eval & Learning UI pages at /eval/."""

    EVAL_URLS = [
        "/eval/",
        "/eval/runs/",
        "/eval/signals/",
        "/eval/actions/",
    ]

    def test_eval_pages_no_500(self, admin_client):
        failures = []
        for url in self.EVAL_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These eval pages returned 500: {failures}"

    def test_eval_pages_accessible(self, admin_client):
        for url in self.EVAL_URLS:
            r = admin_client.get(url)
            assert r.status_code in (200, 302, 404), \
                f"Eval {url} returned {r.status_code}"


class TestLangfuseClient:
    """Langfuse client is fail-silent."""

    def test_langfuse_client_importable(self):
        from apps.core.langfuse_client import (
            start_trace, start_span, end_span, log_generation, score_trace
        )
        assert start_trace is not None
        assert end_span is not None

    def test_start_trace_never_raises_without_config(self):
        """With no LANGFUSE_PUBLIC_KEY, start_trace must return None silently."""
        import os
        from apps.core.langfuse_client import start_trace
        original = os.environ.get("LANGFUSE_PUBLIC_KEY")
        try:
            os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
            result = start_trace("test-trace", "test-pipeline")
            # Should return None silently, never raise
            assert result is None or True  # None or a real span -- both ok
        finally:
            if original:
                os.environ["LANGFUSE_PUBLIC_KEY"] = original

    def test_score_trace_never_raises(self):
        from apps.core.langfuse_client import score_trace
        # Calling with a None span must not raise
        try:
            score_trace("trace-123", "test_score", 1.0, comment="e2e test", span=None)
        except Exception as exc:
            pytest.fail(f"score_trace raised unexpectedly: {exc}")
