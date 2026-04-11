"""Tests for the SupervisorAgent, SkillRegistry, PluginToolRouter, and supervisor tools."""
from __future__ import annotations

import json
import pytest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch, PropertyMock

from apps.core.enums import AgentType, AgentRunStatus, RecommendationType
from apps.agents.services.llm_client import LLMResponse, LLMToolCall
from apps.tools.registry.base import ToolResult, ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _llm_response(content=None, tool_calls=None):
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )


def _make_ctx(invoice_id=1, po_number="PO-001", mode="THREE_WAY", tenant=None):
    """Build a minimal AgentContext for supervisor tests."""
    from apps.agents.services.base_agent import AgentContext
    from apps.agents.services.agent_memory import AgentMemory

    memory = AgentMemory()
    memory.facts["invoice_number"] = "INV-001"
    memory.facts["vendor_name"] = "Test Vendor"
    memory.facts["extraction_confidence"] = 0.85

    return AgentContext(
        reconciliation_result=None,
        invoice_id=invoice_id,
        po_number=po_number,
        exceptions=[],
        reconciliation_mode=mode,
        actor_user_id=None,
        actor_primary_role="SYSTEM_AGENT",
        actor_roles_snapshot=["SYSTEM_AGENT"],
        memory=memory,
        tenant=tenant,
    )


# ============================================================================
# SkillRegistry tests
# ============================================================================
class TestSkillRegistry:
    """SR-01 to SR-06: Skill registration, resolution, and composition."""

    def setup_method(self):
        from apps.agents.skills.base import SkillRegistry
        SkillRegistry.clear()

    def teardown_method(self):
        from apps.agents.skills.base import SkillRegistry
        SkillRegistry.clear()

    def test_register_and_get(self):
        """SR-01: Register a skill and retrieve it by name."""
        from apps.agents.skills.base import Skill, SkillRegistry

        skill = Skill(
            name="test_skill",
            description="A test skill",
            prompt_extension="Test prompt extension",
            tools=["tool_a", "tool_b"],
            decision_hints=["Hint 1"],
        )
        SkillRegistry.register(skill)

        retrieved = SkillRegistry.get("test_skill")
        assert retrieved is not None
        assert retrieved.name == "test_skill"
        assert retrieved.tools == ["tool_a", "tool_b"]

    def test_get_unknown_returns_none(self):
        """SR-02: Getting an unregistered skill returns None."""
        from apps.agents.skills.base import SkillRegistry
        assert SkillRegistry.get("nonexistent") is None

    def test_all_tools_merges(self):
        """SR-03: all_tools merges tool lists, preserving order and deduplication."""
        from apps.agents.skills.base import Skill, SkillRegistry

        SkillRegistry.register(Skill(
            name="s1", description="", prompt_extension="",
            tools=["tool_a", "tool_b"],
        ))
        SkillRegistry.register(Skill(
            name="s2", description="", prompt_extension="",
            tools=["tool_b", "tool_c"],
        ))

        tools = SkillRegistry.all_tools(["s1", "s2"])
        assert tools == ["tool_a", "tool_b", "tool_c"]

    def test_compose_prompt(self):
        """SR-04: compose_prompt concatenates skill prompt extensions."""
        from apps.agents.skills.base import Skill, SkillRegistry

        SkillRegistry.register(Skill(
            name="s1", description="", prompt_extension="Phase 1 guidance",
            tools=[],
        ))
        SkillRegistry.register(Skill(
            name="s2", description="", prompt_extension="Phase 2 guidance",
            tools=[],
        ))

        prompt = SkillRegistry.compose_prompt(["s1", "s2"])
        assert "Phase 1 guidance" in prompt
        assert "Phase 2 guidance" in prompt

    def test_compose_hints(self):
        """SR-05: compose_hints collects hints from all requested skills."""
        from apps.agents.skills.base import Skill, SkillRegistry

        SkillRegistry.register(Skill(
            name="s1", description="", prompt_extension="",
            tools=[], decision_hints=["Hint A"],
        ))
        SkillRegistry.register(Skill(
            name="s2", description="", prompt_extension="",
            tools=[], decision_hints=["Hint B", "Hint C"],
        ))

        hints = SkillRegistry.compose_hints(["s1", "s2"])
        assert hints == ["Hint A", "Hint B", "Hint C"]

    def test_skill_loading(self):
        """SR-06: All 5 default skills load successfully."""
        import importlib
        from apps.agents.skills.base import SkillRegistry

        # After clear(), Python-cached modules won't re-register;
        # reload each skill module to trigger register_skill again.
        import apps.agents.skills.invoice_extraction as _ie
        import apps.agents.skills.ap_validation as _av
        import apps.agents.skills.ap_matching as _am
        import apps.agents.skills.ap_investigation as _ai
        import apps.agents.skills.ap_review_routing as _ar
        importlib.reload(_ie)
        importlib.reload(_av)
        importlib.reload(_am)
        importlib.reload(_ai)
        importlib.reload(_ar)

        skills = SkillRegistry.get_all()
        expected = {
            "invoice_extraction", "ap_validation", "ap_3way_matching",
            "ap_investigation", "ap_review_routing",
        }
        assert expected.issubset(set(skills.keys()))


# ============================================================================
# PluginToolRouter tests
# ============================================================================
class TestPluginToolRouter:
    """PT-01 to PT-04: Plugin routing and fallback behavior."""

    def test_fallback_to_registry(self):
        """PT-01: Non-ERP tools route to standard ToolRegistry."""
        from apps.agents.plugins.plugin_router import PluginToolRouter

        # Register a mock tool
        mock_tool = MagicMock()
        mock_tool.execute.return_value = ToolResult(success=True, data={"test": True})
        with patch.object(ToolRegistry, "get", return_value=mock_tool):
            result = PluginToolRouter.execute("some_tool")
            assert result.success is True

    def test_unknown_tool(self):
        """PT-02: Unknown tool returns error."""
        from apps.agents.plugins.plugin_router import PluginToolRouter

        with patch.object(ToolRegistry, "get", return_value=None):
            result = PluginToolRouter.execute("unknown_tool")
            assert result.success is False
            assert "not found" in result.error

    def test_erp_route_no_connector(self):
        """PT-03: ERP-routable tool without connector falls back to registry."""
        from apps.agents.plugins.plugin_router import PluginToolRouter

        mock_tool = MagicMock()
        mock_tool.execute.return_value = ToolResult(success=True, data={"found": True})

        with patch(
            "apps.agents.plugins.plugin_router.PluginToolRouter._try_erp_route",
            return_value=None,
        ):
            with patch.object(ToolRegistry, "get", return_value=mock_tool):
                tenant = MagicMock()
                result = PluginToolRouter.execute("po_lookup", tenant=tenant, po_number="PO-123")
                assert result.success is True

    def test_erp_routable_tools(self):
        """PT-04: Verify the ERP-routable tool list."""
        from apps.agents.plugins.plugin_router import PluginToolRouter

        expected = {"po_lookup", "grn_lookup", "vendor_search", "verify_vendor", "check_duplicate"}
        assert PluginToolRouter.ERP_ROUTABLE_TOOLS == expected


# ============================================================================
# SupervisorAgent tests
# ============================================================================
class TestSupervisorAgentRegistration:
    """SA-01 to SA-03: Agent registration and enum."""

    def test_supervisor_enum_exists(self):
        """SA-01: SUPERVISOR exists in AgentType enum."""
        assert hasattr(AgentType, "SUPERVISOR")
        assert AgentType.SUPERVISOR == "SUPERVISOR"

    def test_supervisor_in_registry(self):
        """SA-02: SupervisorAgent is registered in AGENT_CLASS_REGISTRY."""
        from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
        from apps.agents.services.supervisor_agent import SupervisorAgent
        assert AgentType.SUPERVISOR in AGENT_CLASS_REGISTRY
        assert AGENT_CLASS_REGISTRY[AgentType.SUPERVISOR] is SupervisorAgent

    def test_supervisor_agent_type(self):
        """SA-03: SupervisorAgent.agent_type matches enum."""
        from apps.agents.services.supervisor_agent import SupervisorAgent
        agent = SupervisorAgent()
        assert agent.agent_type == AgentType.SUPERVISOR


class TestSupervisorPrompt:
    """SP-01 to SP-03: Prompt assembly and skill integration."""

    def test_prompt_contains_phases(self):
        """SP-01: System prompt includes all 5 lifecycle phases."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        prompt = agent.system_prompt
        assert "UNDERSTAND" in prompt
        assert "VALIDATE" in prompt
        assert "MATCH" in prompt
        assert "INVESTIGATE" in prompt
        assert "DECIDE" in prompt

    def test_prompt_contains_guardrails(self):
        """SP-02: System prompt includes key guardrails."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        prompt = agent.system_prompt
        assert "submit_recommendation" in prompt
        assert "tax ID" in prompt
        assert "tolerance" in prompt.lower()

    def test_prompt_build_with_custom_skills(self):
        """SP-03: Custom skill list produces different prompt."""
        import importlib
        from apps.agents.services.supervisor_agent import SupervisorAgent
        from apps.agents.skills.base import SkillRegistry

        # Ensure skills are registered (reload since SkillRegistry may
        # have been cleared in a previous test within the same process).
        import apps.agents.skills.invoice_extraction as _ie
        import apps.agents.skills.ap_validation as _av
        import apps.agents.skills.ap_matching as _am
        import apps.agents.skills.ap_investigation as _ai
        import apps.agents.skills.ap_review_routing as _ar
        importlib.reload(_ie)
        importlib.reload(_av)
        importlib.reload(_am)
        importlib.reload(_ai)
        importlib.reload(_ar)

        agent_all = SupervisorAgent()
        agent_partial = SupervisorAgent(skill_names=["invoice_extraction"])

        # Both should have content but different lengths
        assert len(agent_all.system_prompt) > len(agent_partial.system_prompt)


class TestSupervisorTools:
    """ST-01 to ST-05: Tool registration and allowed tools."""

    def test_supervisor_has_all_skill_tools(self):
        """ST-01: SupervisorAgent.allowed_tools includes all skill tools."""
        import importlib
        from apps.agents.services.supervisor_agent import SupervisorAgent

        # Reload skill modules so register_skill runs even if registry was cleared
        import apps.agents.skills.invoice_extraction as _ie
        import apps.agents.skills.ap_validation as _av
        import apps.agents.skills.ap_matching as _am
        import apps.agents.skills.ap_investigation as _ai
        import apps.agents.skills.ap_review_routing as _ar
        importlib.reload(_ie)
        importlib.reload(_av)
        importlib.reload(_am)
        importlib.reload(_ai)
        importlib.reload(_ar)

        agent = SupervisorAgent()
        tools = agent.allowed_tools

        # Key tools from each phase
        assert "get_ocr_text" in tools
        assert "validate_extraction" in tools
        assert "po_lookup" in tools
        assert "run_header_match" in tools
        assert "re_extract_field" in tools
        assert "submit_recommendation" in tools

    def test_supervisor_includes_existing_tools(self):
        """ST-02: Existing tools (po_lookup, grn_lookup, etc.) are included."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        tools = agent.allowed_tools

        existing = ["po_lookup", "grn_lookup", "vendor_search",
                     "invoice_details", "exception_list", "reconciliation_summary"]
        for t in existing:
            assert t in tools, f"Missing existing tool: {t}"

    def test_tool_deduplication(self):
        """ST-03: Tools are not duplicated when skills overlap."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        tools = agent.allowed_tools
        assert len(tools) == len(set(tools)), "Duplicate tools found"

    def test_supervisor_tools_registered(self):
        """ST-04: All supervisor tools are registered in ToolRegistry."""
        # Force registration
        import apps.tools.registry.supervisor_tools  # noqa: F401

        supervisor_tool_names = [
            "get_ocr_text", "classify_document", "extract_invoice_fields",
            "validate_extraction", "repair_extraction", "check_duplicate",
            "verify_vendor", "verify_tax_computation",
            "run_header_match", "run_line_match", "run_grn_match",
            "get_tolerance_config",
            "re_extract_field", "invoke_po_retrieval_agent",
            "invoke_grn_retrieval_agent", "get_vendor_history", "get_case_history",
            "persist_invoice", "create_case", "submit_recommendation",
            "assign_reviewer", "generate_case_summary",
            "auto_close_case", "escalate_case",
        ]
        for name in supervisor_tool_names:
            tool = ToolRegistry.get(name)
            assert tool is not None, f"Tool '{name}' not registered"

    def test_submit_recommendation_validates(self):
        """ST-05: submit_recommendation rejects invalid types."""
        import apps.tools.registry.supervisor_tools  # noqa: F401
        tool = ToolRegistry.get("submit_recommendation")
        result = tool.execute(
            recommendation_type="INVALID_TYPE",
            confidence=0.5,
            reasoning="test",
        )
        assert result.success is False
        assert "Invalid" in result.error


class TestSupervisorUserMessage:
    """SU-01 to SU-03: User message construction."""

    def test_user_message_includes_mode(self):
        """SU-01: User message includes reconciliation mode."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        ctx = _make_ctx(mode="TWO_WAY")
        msg = agent.build_user_message(ctx)
        assert "2-WAY" in msg

    def test_user_message_includes_invoice_id(self):
        """SU-02: User message includes invoice ID."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        ctx = _make_ctx(invoice_id=42)
        msg = agent.build_user_message(ctx)
        assert "42" in msg

    def test_user_message_includes_memory_facts(self):
        """SU-03: User message includes facts from memory."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()
        agent = SupervisorAgent()
        ctx = _make_ctx()
        msg = agent.build_user_message(ctx)
        assert "INV-001" in msg
        assert "Test Vendor" in msg


class TestSupervisorOutputInterpreter:
    """SO-01 to SO-04: Output parsing and validation."""

    def test_parse_valid_json(self):
        """SO-01: Valid JSON is parsed correctly."""
        from apps.agents.services.supervisor_output_interpreter import (
            interpret_supervisor_output,
        )

        content = json.dumps({
            "recommendation_type": "AUTO_CLOSE",
            "confidence": 0.95,
            "reasoning": "All lines within tolerance",
            "evidence": {"match_status": "MATCHED"},
            "decisions": [{"decision": "close", "rationale": "good", "confidence": 0.95}],
            "tools_used": ["po_lookup", "run_line_match"],
        })
        output = interpret_supervisor_output(content)
        assert output.recommendation_type == "AUTO_CLOSE"
        assert output.confidence == 0.95
        assert "po_lookup" in output.tools_used

    def test_parse_markdown_wrapped_json(self):
        """SO-02: Markdown-wrapped JSON is handled."""
        from apps.agents.services.supervisor_output_interpreter import (
            interpret_supervisor_output,
        )

        content = '```json\n{"recommendation_type": "SEND_TO_AP_REVIEW", "confidence": 0.6, "reasoning": "test"}\n```'
        output = interpret_supervisor_output(content)
        assert output.recommendation_type == "SEND_TO_AP_REVIEW"

    def test_missing_recommendation_defaults(self):
        """SO-03: Missing recommendation defaults to SEND_TO_AP_REVIEW."""
        from apps.agents.services.supervisor_output_interpreter import (
            interpret_supervisor_output,
        )

        content = json.dumps({"reasoning": "analysis done", "confidence": 0.7})
        output = interpret_supervisor_output(content)
        assert output.recommendation_type == "SEND_TO_AP_REVIEW"

    def test_invalid_json_falls_back(self):
        """SO-04: Invalid JSON still produces a usable output."""
        from apps.agents.services.supervisor_output_interpreter import (
            interpret_supervisor_output,
        )

        output = interpret_supervisor_output("not valid json at all")
        assert output.recommendation_type == "SEND_TO_AP_REVIEW"
        assert output.confidence <= 0.3


class TestSupervisorGuardrails:
    """SG-01 to SG-02: RBAC integration."""

    def test_supervisor_permission_registered(self):
        """SG-01: SUPERVISOR has a permission entry in guardrails."""
        from apps.agents.services.guardrails_service import AGENT_PERMISSIONS
        assert "SUPERVISOR" in AGENT_PERMISSIONS
        assert AGENT_PERMISSIONS["SUPERVISOR"] == "agents.run_supervisor"

    def test_supervisor_tool_permissions_registered(self):
        """SG-02: All supervisor tools have permission entries."""
        from apps.agents.services.guardrails_service import TOOL_PERMISSIONS

        supervisor_tools = [
            "get_ocr_text", "classify_document", "extract_invoice_fields",
            "validate_extraction", "repair_extraction", "check_duplicate",
            "verify_vendor", "verify_tax_computation",
            "run_header_match", "run_line_match", "run_grn_match",
            "get_tolerance_config",
            "re_extract_field", "invoke_po_retrieval_agent",
            "invoke_grn_retrieval_agent", "get_vendor_history", "get_case_history",
            "persist_invoice", "create_case", "submit_recommendation",
            "assign_reviewer", "generate_case_summary",
            "auto_close_case", "escalate_case",
        ]
        for t in supervisor_tools:
            assert t in TOOL_PERMISSIONS, f"Missing tool permission: {t}"


class TestSupervisorPromptRegistry:
    """SPR-01: Prompt registry integration."""

    def test_supervisor_prompt_key_registered(self):
        """SPR-01: SUPERVISOR has a prompt key in _AGENT_TYPE_TO_PROMPT_KEY."""
        from apps.core.prompt_registry import _AGENT_TYPE_TO_PROMPT_KEY
        assert "SUPERVISOR" in _AGENT_TYPE_TO_PROMPT_KEY
        assert _AGENT_TYPE_TO_PROMPT_KEY["SUPERVISOR"] == "agent.supervisor_ap_lifecycle"


@pytest.mark.django_db
class TestSupervisorAgentRun:
    """SAR-01 to SAR-03: Full agent run with mocked LLM."""

    def _setup_agent_def(self, tenant=None):
        """Create an AgentDefinition for SUPERVISOR."""
        from apps.agents.models import AgentDefinition
        agent_def, _ = AgentDefinition.objects.get_or_create(
            agent_type=AgentType.SUPERVISOR,
            defaults={
                "name": "Supervisor Agent",
                "enabled": True,
                "description": "Full lifecycle supervisor",
                "config_json": {},
                "tenant": tenant,
            },
        )
        return agent_def

    @patch("apps.agents.services.base_agent.BaseAgent._call_llm_with_retry")
    def test_happy_path_auto_close(self, mock_llm):
        """SAR-01: Supervisor returns AUTO_CLOSE on successful analysis."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()

        self._setup_agent_def()

        mock_llm.return_value = _llm_response(
            content=json.dumps({
                "recommendation_type": "AUTO_CLOSE",
                "confidence": 0.95,
                "reasoning": "All lines within tolerance. Vendor verified by tax ID.",
                "evidence": {
                    "match_status": "MATCHED",
                    "vendor_verified": True,
                    "lines_checked": 3,
                },
                "decisions": [{
                    "decision": "auto_close",
                    "rationale": "All criteria met",
                    "confidence": 0.95,
                }],
                "tools_used": ["po_lookup", "run_header_match", "run_line_match"],
            }),
        )

        agent = SupervisorAgent()
        ctx = _make_ctx()
        agent_run = agent.run(ctx)

        assert agent_run.status == AgentRunStatus.COMPLETED
        assert agent_run.output_payload is not None
        assert agent_run.output_payload.get("recommendation_type") == "AUTO_CLOSE"

    @patch("apps.agents.services.base_agent.BaseAgent._call_llm_with_retry")
    def test_review_routing_path(self, mock_llm):
        """SAR-02: Supervisor routes to review on partial match."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()

        self._setup_agent_def()

        mock_llm.return_value = _llm_response(
            content=json.dumps({
                "recommendation_type": "SEND_TO_AP_REVIEW",
                "confidence": 0.65,
                "reasoning": "Line 2 price deviation exceeds tolerance",
                "evidence": {
                    "match_status": "PARTIAL_MATCH",
                    "deviations": ["Line 2: price 5.2% over tolerance"],
                },
                "decisions": [{
                    "decision": "route_to_review",
                    "rationale": "Price deviation on line 2",
                    "confidence": 0.65,
                }],
                "tools_used": ["po_lookup", "run_line_match", "get_tolerance_config"],
            }),
        )

        agent = SupervisorAgent()
        ctx = _make_ctx()
        agent_run = agent.run(ctx)

        assert agent_run.status == AgentRunStatus.COMPLETED
        assert agent_run.output_payload.get("recommendation_type") == "SEND_TO_AP_REVIEW"

    @patch("apps.agents.services.base_agent.BaseAgent._call_llm_with_retry")
    def test_recovery_with_tool_calls(self, mock_llm):
        """SAR-03: Supervisor uses tools, re-extracts, and retries match."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()

        self._setup_agent_def()

        # First call: tool calls for extraction + validation
        first_response = _llm_response(
            tool_calls=[
                LLMToolCall(
                    id="call_1",
                    name="extract_invoice_fields",
                    arguments={"invoice_id": 1},
                ),
            ],
        )
        # Second call: final response after tools
        second_response = _llm_response(
            content=json.dumps({
                "recommendation_type": "AUTO_CLOSE",
                "confidence": 0.88,
                "reasoning": "After re-extraction, PO matched successfully",
                "evidence": {"recovery_actions": ["re-extracted po_number"]},
                "decisions": [{
                    "decision": "auto_close",
                    "rationale": "Match successful after recovery",
                    "confidence": 0.88,
                }],
                "tools_used": [
                    "extract_invoice_fields", "po_lookup", "run_header_match",
                ],
            }),
        )

        mock_llm.side_effect = [first_response, second_response]

        agent = SupervisorAgent()
        ctx = _make_ctx()

        # Mock tool execution
        with patch.object(ToolRegistry, "get") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute.return_value = ToolResult(
                success=True,
                data={"invoice_id": 1, "extraction_confidence": 0.9},
            )
            mock_get_tool.return_value = mock_tool

            agent_run = agent.run(ctx)

        assert agent_run.status == AgentRunStatus.COMPLETED

    @patch("apps.agents.services.base_agent.BaseAgent._call_llm_with_retry")
    def test_guardrail_no_recommendation_default(self, mock_llm):
        """SAR-04: Missing recommendation defaults to SEND_TO_AP_REVIEW."""
        from apps.agents.services.supervisor_agent import SupervisorAgent, _ensure_skills_loaded
        _ensure_skills_loaded()

        self._setup_agent_def()

        mock_llm.return_value = _llm_response(
            content=json.dumps({
                "reasoning": "I analyzed the invoice but forgot to recommend",
                "confidence": 0.5,
                "evidence": {},
                "decisions": [],
                "tools_used": [],
            }),
        )

        agent = SupervisorAgent()
        ctx = _make_ctx()
        agent_run = agent.run(ctx)

        assert agent_run.status == AgentRunStatus.COMPLETED
        # Should default to SEND_TO_AP_REVIEW
        rec = agent_run.output_payload.get("recommendation_type", "")
        assert rec == "SEND_TO_AP_REVIEW"


@pytest.mark.django_db
class TestSupervisorToolExecution:
    """STE-01 to STE-04: Individual tool tests."""

    def test_get_tolerance_config_defaults(self):
        """STE-01: get_tolerance_config returns defaults when no config exists."""
        import apps.tools.registry.supervisor_tools  # noqa: F401
        tool = ToolRegistry.get("get_tolerance_config")
        result = tool.execute()
        assert result.success is True
        data = result.data
        assert "strict" in data
        assert "auto_close" in data

    def test_submit_recommendation_valid(self):
        """STE-02: submit_recommendation accepts valid types."""
        import apps.tools.registry.supervisor_tools  # noqa: F401
        tool = ToolRegistry.get("submit_recommendation")
        result = tool.execute(
            recommendation_type="AUTO_CLOSE",
            confidence=0.9,
            reasoning="All checks passed",
        )
        assert result.success is True
        assert result.data["submitted"] is True
        assert result.data["recommendation_type"] == "AUTO_CLOSE"

    def test_submit_recommendation_clamps_confidence(self):
        """STE-03: Confidence is clamped to [0.0, 1.0]."""
        import apps.tools.registry.supervisor_tools  # noqa: F401
        tool = ToolRegistry.get("submit_recommendation")
        result = tool.execute(
            recommendation_type="AUTO_CLOSE",
            confidence=1.5,
            reasoning="test",
        )
        assert result.success is True
        assert result.data["confidence"] == 1.0

    def test_generate_case_summary(self):
        """STE-04: generate_case_summary returns the summary."""
        import apps.tools.registry.supervisor_tools  # noqa: F401
        tool = ToolRegistry.get("generate_case_summary")
        result = tool.execute(invoice_id=1, summary="Test summary text")
        assert result.success is True
        assert result.data["summary"] == "Test summary text"


class TestSupervisorContextBuilder:
    """SCB-01 to SCB-02: Context builder tests."""

    def test_builds_context_with_memory(self):
        """SCB-01: Context builder populates memory facts."""
        from apps.agents.services.supervisor_context_builder import build_supervisor_context

        ctx = build_supervisor_context(
            invoice_id=1,
            reconciliation_mode="THREE_WAY",
            po_number="PO-001",
        )
        assert ctx.invoice_id == 1
        assert ctx.po_number == "PO-001"
        assert ctx.reconciliation_mode == "THREE_WAY"
        assert ctx.memory is not None
        assert ctx.memory.facts.get("reconciliation_mode") == "THREE_WAY"

    def test_two_way_mode_sets_fact(self):
        """SCB-02: TWO_WAY mode sets is_two_way fact."""
        from apps.agents.services.supervisor_context_builder import build_supervisor_context

        ctx = build_supervisor_context(
            invoice_id=1,
            reconciliation_mode="TWO_WAY",
        )
        assert ctx.memory.facts.get("is_two_way") is True


class TestSupervisorMaxToolRounds:
    """SMR-01: Supervisor uses expanded tool budget."""

    def test_supervisor_tool_budget(self):
        """SMR-01: SUPERVISOR_MAX_TOOL_ROUNDS is larger than standard."""
        from apps.agents.services.supervisor_agent import SUPERVISOR_MAX_TOOL_ROUNDS
        from apps.agents.services.base_agent import MAX_TOOL_ROUNDS

        assert SUPERVISOR_MAX_TOOL_ROUNDS > MAX_TOOL_ROUNDS
        assert SUPERVISOR_MAX_TOOL_ROUNDS == 15
