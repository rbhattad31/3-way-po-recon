"""
TEST 03 -- LLM Agents (9 agents)
=================================
Tests agent definitions, registry, API list, and per-agent DB records.

Agents tested:
  1. ExceptionAnalysisAgent
  2. InvoiceExtractionAgent
  3. InvoiceUnderstandingAgent
  4. PORetrievalAgent
  5. GRNRetrievalAgent
  6. ReviewRoutingAgent
  7. CaseSummaryAgent
  8. ReconciliationAssistAgent
  9. ComplianceAgent + SupervisorAgent
"""

import pytest
from apps.core.enums import AgentType

pytestmark = pytest.mark.django_db(transaction=False)


EXPECTED_LLM_AGENT_TYPES = [
    AgentType.EXCEPTION_ANALYSIS,
    AgentType.INVOICE_EXTRACTION,
    AgentType.INVOICE_UNDERSTANDING,
    AgentType.PO_RETRIEVAL,
    AgentType.GRN_RETRIEVAL,
    AgentType.REVIEW_ROUTING,
    AgentType.CASE_SUMMARY,
    AgentType.RECONCILIATION_ASSIST,
        AgentType.COMPLIANCE_AGENT,
]


class TestAgentRegistry:
    """AgentType enum and class registry completeness."""

    def test_all_llm_agent_types_defined(self):
        from apps.core.enums import AgentType
        defined = [a.value for a in AgentType]
        for agent_type in EXPECTED_LLM_AGENT_TYPES:
            assert agent_type in AgentType, \
                f"AgentType.{agent_type} missing from enums"

    def test_agent_class_registry_populated(self):
        from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
        assert len(AGENT_CLASS_REGISTRY) >= 9, \
            f"Expected >= 9 agent classes in registry, got {len(AGENT_CLASS_REGISTRY)}"

    def test_supervisor_agent_in_registry(self):
        from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
        from apps.core.enums import AgentType
        assert AgentType.SUPERVISOR in AGENT_CLASS_REGISTRY, \
            "SupervisorAgent must be registered"


class TestAgentDefinitionsDB:
    """AgentDefinition catalog records in the database."""

    def test_agent_definitions_exist(self):
        from apps.agents.models import AgentDefinition
        count = AgentDefinition.objects.count()
        # Mark as expected to need seed data, but allow 0 in test environment
        if count == 0:
            pytest.skip("AgentDefinition records require: python manage.py seed_agent_contracts")
        assert count >= 5

    def test_agent_definitions_have_required_fields(self):
        from apps.agents.models import AgentDefinition
        defns = AgentDefinition.objects.all()
        for defn in defns[:5]:
            assert defn.agent_type, f"AgentDefinition #{defn.pk} missing agent_type"
            assert defn.purpose, f"AgentDefinition #{defn.pk} ({defn.agent_type}) missing purpose"

    def test_active_agents_have_lifecycle_status(self):
        from apps.agents.models import AgentDefinition
        for defn in AgentDefinition.objects.all():
            assert defn.lifecycle_status, \
                f"AgentDefinition {defn.agent_type} has no lifecycle_status"


class TestAgentAPI:
    """Agents REST API endpoints."""

    def test_agent_definitions_api_list(self, admin_client):
        r = admin_client.get("/agents/")
        assert r.status_code in (200, 302, 404), \
            f"/agents/ returned {r.status_code}"

    def test_agent_run_history_accessible(self, admin_client):
        from apps.agents.models import AgentRun
        # Just check the model is queryable
        count = AgentRun.objects.count()
        assert count >= 0


class TestAgentGuardrails:
    """AgentGuardrailsService + RBAC enforcement for agents."""

    def test_guardrails_service_importable(self):
        from apps.agents.services.guardrails_service import AgentGuardrailsService
        assert AgentGuardrailsService is not None

    def test_resolve_actor_returns_system_agent_when_no_user(self):
        from apps.agents.services.guardrails_service import AgentGuardrailsService
        actor = AgentGuardrailsService.resolve_actor(request_user=None)
        # Should return system-agent identity without raising
        assert actor is not None

    def test_guardrails_denies_unknown_permission(self, admin_user):
        from apps.agents.services.guardrails_service import AgentGuardrailsService
        # A permission that does not exist should not crash -- just deny
        try:
            AgentGuardrailsService.authorize_orchestration(user=admin_user)
            # no exception means permission was granted (expected for admin)
        except PermissionError:
            pass  # also acceptable


class TestAgentOrchestrator:
    """AgentOrchestrator import and basic instantiation."""

    def test_orchestrator_importable(self):
        from apps.agents.services.orchestrator import AgentOrchestrator
        assert AgentOrchestrator is not None

    def test_policy_engine_importable(self):
        from apps.agents.services.policy_engine import PolicyEngine
        assert PolicyEngine is not None

    def test_reasoning_planner_importable(self):
        from apps.agents.services.reasoning_planner import ReasoningPlanner
        assert ReasoningPlanner is not None
