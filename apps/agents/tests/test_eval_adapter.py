"""Tests for AgentEvalAdapter -- per-agent and pipeline-level eval records."""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from django.utils import timezone

from apps.agents.models import AgentOrchestrationRun, AgentRun
from apps.agents.services.eval_adapter import (
    APP_MODULE,
    ENTITY_TYPE_AGENT_RUN,
    ENTITY_TYPE_ORCH_RUN,
    AgentEvalAdapter,
)
from apps.core.enums import AgentRunStatus, AgentType
from apps.core_eval.models import EvalFieldOutcome, EvalMetric, EvalRun


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recon_result(db):
    """Create minimal ReconciliationResult with required FK chain."""
    from apps.reconciliation.tests.factories import (
        InvoiceFactory,
        POFactory,
        ReconConfigFactory,
    )
    from apps.reconciliation.models import ReconciliationRun, ReconciliationResult

    config = ReconConfigFactory()
    invoice = InvoiceFactory()
    po = POFactory()
    run = ReconciliationRun.objects.create(
        config=config,
        status="COMPLETED",
    )
    result = ReconciliationResult.objects.create(
        run=run,
        invoice=invoice,
        purchase_order=po,
        match_status="PARTIAL_MATCH",
    )
    return result


def _make_agent_run(result, agent_type="PO_RETRIEVAL", **kwargs):
    """Create an AgentRun with sensible defaults."""
    now = timezone.now()
    defaults = dict(
        agent_type=agent_type,
        reconciliation_result=result,
        status=AgentRunStatus.COMPLETED,
        confidence=0.85,
        started_at=now,
        completed_at=now,
        duration_ms=1200,
        prompt_tokens=500,
        completion_tokens=200,
        total_tokens=700,
        trace_id="test-trace-001",
        input_payload={"test": True},
        output_payload={
            "recommendation_type": "SEND_TO_AP_REVIEW",
            "evidence": {
                "found_po": "PO-12345",
                "po_number": "PO-12345",
            },
            "tools_used": ["po_lookup"],
            "decisions": [{"code": "D1"}],
        },
        summarized_reasoning="Found PO-12345 matching the invoice.",
    )
    defaults.update(kwargs)
    return AgentRun.objects.create(**defaults)


def _make_orch_run(result):
    """Create a minimal AgentOrchestrationRun."""
    now = timezone.now()
    return AgentOrchestrationRun.objects.create(
        reconciliation_result=result,
        status=AgentOrchestrationRun.Status.COMPLETED,
        plan_source="deterministic",
        planned_agents=["PO_RETRIEVAL", "EXCEPTION_ANALYSIS"],
        executed_agents=["PO_RETRIEVAL", "EXCEPTION_ANALYSIS"],
        final_recommendation="SEND_TO_AP_REVIEW",
        final_confidence=0.82,
        trace_id="test-orch-trace-001",
        started_at=now,
        completed_at=now,
        duration_ms=3500,
    )


@dataclass
class _MockOrchResult:
    """Lightweight stand-in for OrchestrationResult dataclass."""
    agents_executed: list = field(default_factory=lambda: ["PO_RETRIEVAL", "EXCEPTION_ANALYSIS"])
    agent_runs: list = field(default_factory=list)
    final_recommendation: str = "SEND_TO_AP_REVIEW"
    final_confidence: float = 0.82
    plan_source: str = "deterministic"
    planned_agents: list = field(default_factory=lambda: ["PO_RETRIEVAL", "EXCEPTION_ANALYSIS"])
    error: str = ""


# ===========================================================================
# Per-agent eval tests
# ===========================================================================

@pytest.mark.django_db
class TestSyncForAgentRun:
    """EA-01 to EA-08: Per-agent eval creation."""

    def test_creates_eval_run_for_po_retrieval(self, db):
        """EA-01: PO_RETRIEVAL agent gets an EvalRun + metrics + field outcomes."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(result, agent_type="PO_RETRIEVAL")

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        assert eval_run.status == EvalRun.Status.COMPLETED
        assert eval_run.trace_id == "test-trace-001"

    def test_metrics_created(self, db):
        """EA-02: Standard metrics are upserted."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(result)

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        metric_names = set(
            EvalMetric.objects.filter(eval_run=eval_run).values_list("metric_name", flat=True)
        )
        assert "agent_confidence" in metric_names
        assert "agent_status_completed" in metric_names
        assert "agent_duration_ms" in metric_names
        assert "tools_used_count" in metric_names
        assert "prompt_tokens" in metric_names
        assert "total_tokens" in metric_names
        assert "recommendation_type" in metric_names
        assert "decisions_count" in metric_names

    def test_field_outcomes_for_po_retrieval(self, db):
        """EA-03: PO_RETRIEVAL produces found_po and recommendation outcomes."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(result, agent_type="PO_RETRIEVAL")

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        outcomes = list(
            EvalFieldOutcome.objects.filter(eval_run=eval_run).order_by("field_name")
        )
        field_names = [o.field_name for o in outcomes]
        assert "found_po" in field_names
        assert "recommendation" in field_names

        po_outcome = next(o for o in outcomes if o.field_name == "found_po")
        assert po_outcome.predicted_value == "PO-12345"
        assert po_outcome.confidence == 0.85
        assert po_outcome.status == "CORRECT"
        assert po_outcome.ground_truth_value == ""

    def test_field_outcomes_for_grn_retrieval(self, db):
        """EA-04: GRN_RETRIEVAL produces found_grn and recommendation outcomes."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(
            result,
            agent_type="GRN_RETRIEVAL",
            output_payload={
                "recommendation_type": "CLOSE_CASE",
                "evidence": {"found_grn": "GRN-999"},
                "tools_used": ["grn_lookup"],
                "decisions": [],
            },
        )

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        outcomes = {
            o.field_name: o
            for o in EvalFieldOutcome.objects.filter(eval_run=eval_run)
        }
        assert "found_grn" in outcomes
        assert outcomes["found_grn"].predicted_value == "GRN-999"

    def test_failed_agent_sets_eval_status_failed(self, db):
        """EA-05: Failed agent run -> EvalRun.Status.FAILED."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(
            result,
            status=AgentRunStatus.FAILED,
            confidence=0.0,
            output_payload={},
        )

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        assert eval_run.status == EvalRun.Status.FAILED

    def test_missing_field_gets_zero_confidence(self, db):
        """EA-06: Empty predicted value -> status MISSING, confidence 0.0."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(
            result,
            agent_type="PO_RETRIEVAL",
            output_payload={
                "recommendation_type": "",
                "evidence": {},
                "tools_used": [],
                "decisions": [],
            },
            confidence=0.3,
        )

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        outcomes = {
            o.field_name: o
            for o in EvalFieldOutcome.objects.filter(eval_run=eval_run)
        }
        assert outcomes["found_po"].status == "MISSING"
        assert outcomes["found_po"].confidence == 0.0

    def test_idempotent_upsert(self, db):
        """EA-07: Calling twice does not create duplicate EvalRun."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(result)

        AgentEvalAdapter.sync_for_agent_run(agent_run)
        AgentEvalAdapter.sync_for_agent_run(agent_run)

        assert EvalRun.objects.filter(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        ).count() == 1

    def test_exception_analysis_fields(self, db):
        """EA-08: EXCEPTION_ANALYSIS gets recommendation + risk_level."""
        result = _make_recon_result(db)
        agent_run = _make_agent_run(
            result,
            agent_type="EXCEPTION_ANALYSIS",
            output_payload={
                "recommendation_type": "ESCALATE_TO_MANAGER",
                "evidence": {"risk_level": "HIGH"},
                "tools_used": [],
                "decisions": [],
            },
        )

        AgentEvalAdapter.sync_for_agent_run(agent_run)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=str(agent_run.pk),
        )
        outcomes = {
            o.field_name: o
            for o in EvalFieldOutcome.objects.filter(eval_run=eval_run)
        }
        assert outcomes["recommendation"].predicted_value == "ESCALATE_TO_MANAGER"
        assert outcomes["risk_level"].predicted_value == "HIGH"


# ===========================================================================
# Pipeline-level orchestration eval tests
# ===========================================================================

@pytest.mark.django_db
class TestSyncForOrchestration:
    """EA-10 to EA-14: Pipeline-level orchestration eval."""

    def test_creates_orch_eval_run(self, db):
        """EA-10: Orchestration eval run created with correct metadata."""
        result = _make_recon_result(db)
        orch_run = _make_orch_run(result)
        orch_result = _MockOrchResult()

        AgentEvalAdapter.sync_for_orchestration(orch_run, orch_result, result)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_ORCH_RUN,
            entity_id=str(orch_run.pk),
        )
        assert eval_run.status == EvalRun.Status.COMPLETED
        assert eval_run.trace_id == "test-orch-trace-001"

    def test_orch_metrics_created(self, db):
        """EA-11: Pipeline-level metrics are upserted."""
        result = _make_recon_result(db)
        orch_run = _make_orch_run(result)
        orch_result = _MockOrchResult()

        AgentEvalAdapter.sync_for_orchestration(orch_run, orch_result, result)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_ORCH_RUN,
            entity_id=str(orch_run.pk),
        )
        metric_names = set(
            EvalMetric.objects.filter(eval_run=eval_run).values_list("metric_name", flat=True)
        )
        assert "agents_executed_count" in metric_names
        assert "final_confidence" in metric_names
        assert "pipeline_status_completed" in metric_names
        assert "has_recommendation" in metric_names
        assert "final_recommendation" in metric_names
        assert "pipeline_duration_ms" in metric_names

    def test_orch_metrics_values(self, db):
        """EA-12: Metric values reflect orchestration result."""
        result = _make_recon_result(db)
        orch_run = _make_orch_run(result)
        orch_result = _MockOrchResult()

        AgentEvalAdapter.sync_for_orchestration(orch_run, orch_result, result)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_ORCH_RUN,
            entity_id=str(orch_run.pk),
        )
        metrics = {
            m.metric_name: m
            for m in EvalMetric.objects.filter(eval_run=eval_run)
        }
        assert float(metrics["agents_executed_count"].raw_value) == 2.0
        assert float(metrics["final_confidence"].raw_value) == 0.82
        assert float(metrics["pipeline_status_completed"].raw_value) == 1.0
        assert float(metrics["has_recommendation"].raw_value) == 1.0

    def test_orch_failed_status(self, db):
        """EA-13: Failed orch run -> FAILED eval status."""
        result = _make_recon_result(db)
        orch_run = _make_orch_run(result)
        orch_run.status = AgentOrchestrationRun.Status.FAILED
        orch_run.save(update_fields=["status"])

        orch_result = _MockOrchResult(
            final_recommendation="",
            final_confidence=0.0,
        )

        AgentEvalAdapter.sync_for_orchestration(orch_run, orch_result, result)

        eval_run = EvalRun.objects.get(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_ORCH_RUN,
            entity_id=str(orch_run.pk),
        )
        assert eval_run.status == EvalRun.Status.FAILED

    def test_orch_idempotent(self, db):
        """EA-14: Calling twice produces a single EvalRun."""
        result = _make_recon_result(db)
        orch_run = _make_orch_run(result)
        orch_result = _MockOrchResult()

        AgentEvalAdapter.sync_for_orchestration(orch_run, orch_result, result)
        AgentEvalAdapter.sync_for_orchestration(orch_run, orch_result, result)

        assert EvalRun.objects.filter(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_ORCH_RUN,
            entity_id=str(orch_run.pk),
        ).count() == 1


# ===========================================================================
# Fail-silent tests
# ===========================================================================

@pytest.mark.django_db
class TestFailSilent:
    """EA-20 to EA-21: Adapter must never raise."""

    def test_sync_for_agent_run_no_raise_on_bad_input(self, db):
        """EA-20: Passing None does not raise."""
        AgentEvalAdapter.sync_for_agent_run(None)  # should not raise

    def test_sync_for_orchestration_no_raise_on_bad_input(self, db):
        """EA-21: Passing None does not raise."""
        AgentEvalAdapter.sync_for_orchestration(None, None, None)  # should not raise
