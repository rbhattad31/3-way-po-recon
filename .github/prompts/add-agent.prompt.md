---
description: "Add a new LLM agent or deterministic system agent to the platform. Handles AgentType enum, BaseAgent/DeterministicSystemAgent subclass, AGENT_CLASS_REGISTRY, PolicyEngine wiring, RBAC permission, guardrails entry, and AgentDefinition seed data."
agent: agent
argument-hint: "Agent type and purpose (e.g. 'LLM agent: DuplicateInvoiceDetectionAgent that uses invoice_details and exception_list tools to detect duplicate invoices')"
tools: [read, edit, search]
---

Add a new agent to the 3-Way PO Reconciliation Platform agent pipeline.

Use the `new-agent` agent to complete the full integration checklist:

**Step 1 — Enum**
- Read `apps/core/enums.py` and add the new `AgentType` value

**Step 2 — Agent Class**
- Read `apps/agents/services/agent_classes.py` for existing patterns
- For LLM agents: extend `BaseAgent`, implement `_build_system_prompt()`, configure `allowed_tools`
- For deterministic agents: extend `DeterministicSystemAgent`, implement `execute_deterministic(ctx)`
- Add the class to `apps/agents/services/agent_classes.py` or `system_agent_classes.py`

**Step 3 — Registry**
- Add to `AGENT_CLASS_REGISTRY` dict

**Step 4 — Policy Engine**
- Read `apps/agents/services/policy_engine.py`
- Add the new agent to the planning decision logic

**Step 5 — RBAC**
- Add `agents.run_<type>` permission to `PERMISSIONS` list in `apps/accounts/management/commands/seed_rbac.py`
- Map to appropriate roles in `ROLE_MATRIX`
- Add to `AGENT_PERMISSIONS` dict in `apps/agents/services/guardrails_service.py`

**Step 6 — AgentDefinition**
- Provide the complete `AgentDefinition` field values for the seed/admin record
- Include: purpose, entry_conditions, prohibited_actions, allowed_recommendation_types, tool_grounding contract

**Target agent**: $input
