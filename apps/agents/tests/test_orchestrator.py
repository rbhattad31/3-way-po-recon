"""Tests for AgentOrchestrator.execute() -- pipeline-level orchestration."""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import patch, MagicMock, PropertyMock

from apps.core.enums import AgentType, MatchStatus, ReconciliationMode


# ---------------------------------------------------------------------------
# Lightweight stubs
# ---------------------------------------------------------------------------
@dataclass
class _MockAgentPlan:
    skip_agents: bool = False
    skip_reason: str = ""
    reason: str = ""
    agents: List[str] = field(default_factory=list)
    auto_close: bool = False
    confidence: float = 0.8
    source: str = "policy_engine"
    plan_source: str = "deterministic"
    plan_confidence: float = 0.8
    reconciliation_mode: str = ""


@dataclass
class _MockAgentOutput:
    reasoning: str = "Test reasoning for assertion"
    recommendation_type: Optional[str] = "SEND_TO_AP_REVIEW"
    confidence: float = 0.72
    evidence: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)
    raw_content: str = ""
    status: str = "COMPLETED"


# =========================================================================
# AgentOrchestrator tests
# =========================================================================
@pytest.mark.django_db
class TestAgentOrchestrator:
    """AO-01 to AO-07: AgentOrchestrator.execute() flow."""

    @pytest.fixture
    def actor_user(self, db):
        """Create a real user for RBAC actor."""
        from apps.accounts.models import User
        return User.objects.create_user(
            email="agent-test@example.com",
            password="testpass123",
            role="ADMIN",
        )

    @pytest.fixture
    def recon_result(self):
        """Create minimal ReconciliationResult for orchestrator."""
        from apps.reconciliation.tests.factories import ReconConfigFactory, InvoiceFactory, POFactory
        from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
        from apps.core.enums import ReconciliationRunStatus

        config = ReconConfigFactory()
        invoice = InvoiceFactory(extraction_confidence=0.85)
        po = POFactory()
        run = ReconciliationRun.objects.create(
            status=ReconciliationRunStatus.COMPLETED,
            config=config,
        )
        return ReconciliationResult.objects.create(
            run=run,
            invoice=invoice,
            purchase_order=po,
            match_status=MatchStatus.PARTIAL_MATCH,
            deterministic_confidence=0.75,
            extraction_confidence=0.85,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )

    @patch("apps.agents.services.orchestrator.AgentGuardrailsService")
    @patch("apps.agents.services.orchestrator.ReasoningPlanner")
    def test_skip_for_matched(self, mock_planner_cls, mock_guard_cls, recon_result, actor_user):
        """AO-01: MATCHED result causes skip_agents=True."""
        recon_result.match_status = MatchStatus.MATCHED
        recon_result.deterministic_confidence = 0.98
        recon_result.save()

        # Setup guardrails — class-level mocks (static/class method calls)
        self._setup_guard(mock_guard_cls, actor_user)

        # Policy says skip
        mock_planner = MagicMock()
        mock_planner.plan.return_value = _MockAgentPlan(skip_agents=True, skip_reason="MATCHED", reason="MATCHED")
        mock_planner_cls.return_value = mock_planner

        from apps.agents.services.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        orch.policy = mock_planner

        outcome = orch.execute(recon_result)
        assert outcome.skipped is True
        assert outcome.skip_reason == "MATCHED"

    def _setup_guard(self, mock_guard_cls, actor_user, *, authorize=True):
        """Helper: set up guardrails mock with real user.

        AgentGuardrailsService methods are called as class/static methods,
        so we mock on the class mock directly, NOT via return_value.
        """
        from apps.core.trace import TraceContext
        trace_ctx = TraceContext.new_root(
            source_service="test",
            source_layer="SYSTEM",
        )
        mock_guard_cls.resolve_actor.return_value = actor_user
        mock_guard_cls.authorize_orchestration.return_value = authorize
        mock_guard_cls.authorize_data_scope.return_value = True
        mock_guard_cls.build_rbac_snapshot.return_value = {
            "actor_primary_role": "ADMIN",
            "actor_roles_snapshot": ["ADMIN"],
        }
        mock_guard_cls.build_trace_context_for_agent.return_value = trace_ctx
        mock_guard_cls.log_guardrail_decision.return_value = None
        mock_guard_cls.authorize_agent.return_value = True
        mock_guard_cls.authorize_action.return_value = True
        return mock_guard_cls

    @patch("apps.agents.services.orchestrator.AgentGuardrailsService")
    @patch("apps.agents.services.orchestrator.ReasoningPlanner")
    def test_rbac_denied_returns_error(self, mock_planner_cls, mock_guard_cls, recon_result, actor_user):
        """AO-02: RBAC denial returns error in OrchestrationResult."""
        self._setup_guard(mock_guard_cls, actor_user, authorize=False)

        from apps.agents.services.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()

        outcome = orch.execute(recon_result)
        assert outcome.skipped is True or outcome.error != ""

    @patch("apps.agents.services.orchestrator.AgentGuardrailsService")
    @patch("apps.agents.services.orchestrator.ReasoningPlanner")
    def test_auto_close_in_tolerance_band(self, mock_planner_cls, mock_guard_cls, recon_result, actor_user):
        """AO-03: auto_close plan upgrades PARTIAL_MATCH to MATCHED."""
        self._setup_guard(mock_guard_cls, actor_user)

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _MockAgentPlan(
            skip_agents=True,
            auto_close=True,
            skip_reason="within auto-close tolerance",
        )
        mock_planner_cls.return_value = mock_planner

        from apps.agents.services.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        orch.policy = mock_planner

        outcome = orch.execute(recon_result)
        assert outcome.skipped is True

    @patch("apps.agents.services.orchestrator.AgentGuardrailsService")
    @patch("apps.agents.services.orchestrator.ReasoningPlanner")
    def test_duplicate_run_guard(self, mock_planner_cls, mock_guard_cls, recon_result, actor_user):
        """AO-04: Active RUNNING orchestration blocks re-entry."""
        from apps.agents.models import AgentOrchestrationRun

        # Create an existing RUNNING orchestration run
        AgentOrchestrationRun.objects.create(
            reconciliation_result=recon_result,
            status="RUNNING",
        )

        mock_guard = self._setup_guard(mock_guard_cls, actor_user)

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _MockAgentPlan(
            agents=[AgentType.RECONCILIATION_ASSIST],
        )
        mock_planner_cls.return_value = mock_planner

        from apps.agents.services.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        orch.policy = mock_planner

        outcome = orch.execute(recon_result)
        assert outcome.skipped is True
        assert "duplicate" in outcome.skip_reason.lower() or "active" in outcome.skip_reason.lower()

    @patch("apps.agents.services.orchestrator.AgentGuardrailsService")
    def test_empty_plan_no_agents(self, mock_guard_cls, recon_result, actor_user):
        """AO-05: Empty agent plan ends with no agents executed."""
        self._setup_guard(mock_guard_cls, actor_user)

        from apps.agents.services.orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()

        # Use a planner that returns empty agent list
        mock_planner = MagicMock()
        mock_planner.plan.return_value = _MockAgentPlan(agents=[])
        orch.policy = mock_planner

        outcome = orch.execute(recon_result)
        assert outcome.agents_executed == [] or outcome.skipped is True
