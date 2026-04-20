"""
TEST 04 -- Deterministic System Agents (5 agents)
==================================================
Tests all 5 system agents that extend DeterministicSystemAgent and
bypass the LLM ReAct loop.

Agents tested:
  1. SystemReviewRoutingAgent
  2. SystemCaseSummaryAgent
  3. SystemBulkExtractionIntakeAgent
  4. SystemCaseIntakeAgent
  5. SystemPostingPreparationAgent
"""

import pytest
from apps.core.enums import AgentType

pytestmark = pytest.mark.django_db(transaction=False)

SYSTEM_AGENT_TYPES = [
    AgentType.SYSTEM_REVIEW_ROUTING,
    AgentType.SYSTEM_CASE_SUMMARY,
    AgentType.SYSTEM_BULK_EXTRACTION_INTAKE,
    AgentType.SYSTEM_CASE_INTAKE,
    AgentType.SYSTEM_POSTING_PREPARATION,
]


class TestSystemAgentImports:
    """All 5 system agent classes must be importable."""

    def test_import_system_review_routing_agent(self):
        from apps.agents.services.system_agent_classes import SystemReviewRoutingAgent
        assert SystemReviewRoutingAgent is not None

    def test_import_system_case_summary_agent(self):
        from apps.agents.services.system_agent_classes import SystemCaseSummaryAgent
        assert SystemCaseSummaryAgent is not None

    def test_import_system_bulk_extraction_intake_agent(self):
        from apps.agents.services.system_agent_classes import SystemBulkExtractionIntakeAgent
        assert SystemBulkExtractionIntakeAgent is not None

    def test_import_system_case_intake_agent(self):
        from apps.agents.services.system_agent_classes import SystemCaseIntakeAgent
        assert SystemCaseIntakeAgent is not None

    def test_import_system_posting_preparation_agent(self):
        from apps.agents.services.system_agent_classes import SystemPostingPreparationAgent
        assert SystemPostingPreparationAgent is not None


class TestDeterministicSystemAgentBase:
    """DeterministicSystemAgent base class contract."""

    def test_base_class_importable(self):
        from apps.agents.services.deterministic_system_agent import DeterministicSystemAgent
        assert DeterministicSystemAgent is not None

    def test_base_class_has_execute_deterministic(self):
        from apps.agents.services.deterministic_system_agent import DeterministicSystemAgent
        assert hasattr(DeterministicSystemAgent, "execute_deterministic"), \
            "DeterministicSystemAgent must declare execute_deterministic"

    def test_system_agents_in_registry(self):
        from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
        for agent_type in SYSTEM_AGENT_TYPES:
            assert agent_type in AGENT_CLASS_REGISTRY, \
                f"SystemAgent {agent_type} not in agent class registry"


class TestAgentTraceService:
    """AgentTraceService records persistence."""

    def test_agent_trace_service_importable(self):
        from apps.agents.services.agent_trace_service import AgentTraceService
        assert AgentTraceService is not None

    def test_agent_run_model_importable(self):
        from apps.agents.models import AgentRun
        assert AgentRun is not None

    def test_agent_orchestration_run_model_importable(self):
        from apps.agents.models import AgentOrchestrationRun
        assert AgentOrchestrationRun is not None

    def test_decision_log_model_importable(self):
        from apps.agents.models import DecisionLog
        assert DecisionLog is not None


class TestAgentOutputSchema:
    """AgentOutputSchema Pydantic v2 validation."""

    def test_output_schema_importable(self):
        from apps.agents.services.agent_output_schema import AgentOutputSchema
        assert AgentOutputSchema is not None

    def test_valid_schema_passes(self):
        from apps.agents.services.agent_output_schema import AgentOutputSchema
        schema = AgentOutputSchema(
            confidence=0.85,
            reasoning="Test reasoning output",
            recommendation_type="SEND_TO_AP_REVIEW",
            summary="Test summary",
        )
        assert schema.confidence == 0.85

    def test_confidence_clamped_to_range(self):
        from apps.agents.services.agent_output_schema import AgentOutputSchema
        # Confidence > 1.0 should raise ValidationError (Pydantic v2 enforces bounds)
        from pydantic import ValidationError
        try:
            schema = AgentOutputSchema(
                confidence=1.5,  # above 1.0 -- Pydantic should reject
                reasoning="Test",
                recommendation_type="SEND_TO_AP_REVIEW",
                summary="Test",
            )
            assert False, "Expected ValidationError for confidence > 1.0"
        except ValidationError:
            pass  # Expected behavior

    def test_invalid_recommendation_coerced(self):
        from apps.agents.services.agent_output_schema import AgentOutputSchema
        schema = AgentOutputSchema(
            confidence=0.5,
            reasoning="Test",
            recommendation_type="INVALID_RECO_TYPE_XYZ",
            summary="Test",
        )
        assert schema.recommendation_type == "SEND_TO_AP_REVIEW", \
            "Invalid recommendation_type should coerce to SEND_TO_AP_REVIEW"
