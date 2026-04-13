# 18 - Procurement Agent Upgrade Path (Phase 1 and Phase 2)

**Generated**: 2026-04-12  
**Method**: Code-first inspection + architecture comparison  
**Scope**: Procurement agent stack alignment to shared platform agentic architecture (Phase 1 and Phase 2 only)

---

## 1. Objective

This document defines the execution plan to upgrade procurement agents from the current Phase 1 bridge model to the shared enterprise agentic architecture standards in two controlled phases.

Targets for these phases:
1. Standardize output contracts and safety behavior.
2. Enforce centralized guardrails and permissions.
3. Preserve current procurement business behavior while reducing architectural drift.

Out of scope for this document:
1. Full migration of procurement agents to BaseAgent subclasses.
2. Full ToolRegistry conversion for all procurement external calls.
3. Planner-level multi-agent procurement orchestration.

---

## 2. Current State Snapshot

Procurement currently runs through a compatibility bridge:
1. Runtime bridge: `apps/procurement/runtime/procurement_agent_orchestrator.py`
2. Procurement context and memory: `apps/procurement/runtime/procurement_agent_context.py`, `apps/procurement/runtime/procurement_agent_memory.py`
3. Additive execution model: `ProcurementAgentExecutionRecord` in `apps/procurement/models.py`

Shared platform architecture uses:
1. Base agent runtime: `apps/agents/services/base_agent.py`
2. Orchestration: `apps/agents/services/orchestrator.py`
3. Planner: `apps/agents/services/reasoning_planner.py`
4. Guardrails: `apps/agents/services/guardrails_service.py`
5. Output schema: `apps/agents/services/agent_output_schema.py`
6. Unified trace service: `apps/agents/services/agent_trace_service.py`

Result: procurement is partially aligned on observability intent but not yet on contract, guardrail, and governance depth.

---

## 3. Required Fixes Register (Before and During Phases)

### 3.1 Blocking Fixes (Must Complete)

1. **Broken benchmark agent reference**  
   File: `apps/procurement/services/benchmark_service.py`  
   Issue: imports `apps.procurement.agents.benchmark_agent.BenchmarkAgent`, but no such agent module exists in current procurement agents package.  
   Required action: replace with an existing benchmark-capable component or add a concrete benchmark agent module and tests.

2. **Inconsistent AI-path coverage through orchestrator**  
   Files:
   - `apps/procurement/services/recommendation_service.py`
   - `apps/procurement/services/market_intelligence_service.py`
   - `apps/procurement/services/validation/validation_agent.py`
   Issue: some LLM invocations still execute outside centralized guardrail-compatible wrapper semantics.  
   Required action: ensure all procurement AI entry paths are consistently wrapped by the procurement orchestration boundary for auditable control points.

### 3.2 High-Priority Safety Fixes

1. **Schema normalization for all LLM outputs**  
   Required action: enforce shared output schema-compatible normalization and confidence clamping for procurement outputs before persistence.

2. **ASCII-safe persistence for LLM-generated text**  
   Required action: sanitize persisted LLM-generated text fields in procurement write paths to avoid non-ASCII drift and governance inconsistency.

3. **Permission-denied fail-closed behavior**  
   Required action: when authorization fails, stop execution before agent call and record deny audit.

### 3.3 Medium-Priority Governance Fixes

1. **Procurement-specific permission matrix extension**  
   Required action: define and seed granular procurement agent permissions.

2. **Scope-aware access checks**  
   Required action: introduce procurement data-scope checks aligned to tenant and user role scope.

---

## 4. Phase 1 - Contract and Safety Standardization

## 4.1 Phase Goal

Align procurement outputs and persistence behavior to shared agentic contracts without changing orchestration topology.

## 4.2 Deliverables

1. Procurement agent types and definitions aligned with shared catalog conventions.
2. LLM output normalization at procurement service boundaries.
3. ASCII-safe sanitization on persisted agent-generated text.
4. Benchmark path import/runtime drift fixed.

## 4.3 Workstreams

### Workstream A - Contract Catalog Alignment

Files:
1. `apps/core/enums.py`
2. `apps/agents/models.py` (through AgentDefinition usage)
3. RBAC/seed command files already used for agent definitions

Tasks:
1. Add procurement agent type values to `AgentType` only where shared architecture use is intended.
2. Seed corresponding `AgentDefinition` records with contract metadata.
3. Keep runtime behavior unchanged in this phase.

Acceptance criteria:
1. Procurement agent definitions are queryable in the same catalog model as other agents.
2. Seed command remains idempotent.

### Workstream B - Output Schema Normalization

Files:
1. `apps/procurement/services/recommendation_service.py`
2. `apps/procurement/services/benchmark_service.py`
3. `apps/procurement/services/validation/orchestrator_service.py`
4. `apps/procurement/services/validation/validation_agent.py`
5. `apps/procurement/services/market_intelligence_service.py`

Tasks:
1. Validate/normalize LLM outputs using shared schema semantics from `AgentOutputSchema`.
2. Clamp confidence to [0.0, 1.0].
3. Coerce invalid recommendation type values to safe defaults.
4. Ensure failures degrade gracefully with deterministic-safe defaults.

Acceptance criteria:
1. Malformed LLM JSON no longer breaks runs.
2. All persisted confidence values are bounded and valid.

### Workstream C - ASCII Sanitization

Files:
1. `apps/procurement/agents/reason_summary_agent.py`
2. `apps/procurement/agents/compliance_agent.py`
3. `apps/procurement/services/recommendation_service.py`
4. `apps/procurement/services/validation/validation_agent.py`

Tasks:
1. Apply sanitization helper before writing LLM-generated text to DB fields.
2. Keep deterministic text untouched unless sourced from model output.

Acceptance criteria:
1. Persisted agent-generated text is ASCII-only.
2. No change to business decisions caused by sanitization.

### Workstream D - Reference Integrity Repair

Files:
1. `apps/procurement/services/benchmark_service.py`

Tasks:
1. Replace missing benchmark agent import with a real implementation path.
2. Add import-path safety test.

Acceptance criteria:
1. Benchmark flow executes without module import error.
2. AI benchmark fallback remains optional and non-blocking.

## 4.4 Phase 1 PR Breakdown

1. **PR-1A**: Agent catalog and enum alignment.
2. **PR-1B**: Output normalization and confidence guards.
3. **PR-1C**: ASCII sanitization changes.
4. **PR-1D**: Benchmark reference fix and regression tests.

## 4.5 Phase 1 Test Plan

Unit tests:
1. Output schema coercion and fallback behavior.
2. Confidence clamping edge cases.
3. Sanitization of LLM-generated text.
4. Benchmark service import/path safety.

Integration tests:
1. Recommendation run with valid and malformed AI output.
2. Validation augmentation with malformed agent response.
3. Market intelligence service fallback path robustness.

Smoke tests:
1. `run_analysis_task` for recommendation, benchmark, validation in `apps/procurement/tasks.py`.

## 4.6 Phase 1 Exit Criteria

1. No runtime import drift in benchmark path.
2. Contract-safe output handling on all procurement AI paths.
3. ASCII-safe DB persistence on procurement LLM output fields.
4. No behavior regression in deterministic procurement flows.

---

## 5. Phase 2 - Guardrails and Permission Convergence

## 5.1 Phase Goal

Bring procurement orchestration under centralized authorization and audit guardrails while preserving existing bridge runtime.

## 5.2 Deliverables

1. Procurement orchestration and per-agent authorization using `AgentGuardrailsService`.
2. Procurement-specific RBAC permission set seeded and role-mapped.
3. Procurement data-scope authorization checks.
4. Guardrail grant/deny audit and trace parity with shared architecture.

## 5.3 Workstreams

### Workstream A - Permission Model Extension

Files:
1. RBAC seed command(s) under accounts/core management commands.
2. `apps/agents/services/guardrails_service.py` (mapping extensions).

Tasks:
1. Define procurement orchestration and per-agent permissions.
2. Map permissions by role including system-agent automation scenarios.
3. Keep fail-closed semantics for unknown agent/action types.

Acceptance criteria:
1. Permissions are seeded and visible in RBAC matrix.
2. Procurement orchestration cannot run without explicit grant.

### Workstream B - Orchestration Guardrail Integration

Files:
1. `apps/procurement/runtime/procurement_agent_orchestrator.py`

Tasks:
1. Resolve actor via guardrails service.
2. Authorize orchestration before any agent invocation.
3. Authorize per-agent execution before `agent_fn` call.
4. Record grant/deny audit events using centralized guardrail event patterns.

Acceptance criteria:
1. Unauthorized users are blocked before model call.
2. Denials are auditable with permission code and context.

### Workstream C - Procurement Data Scope Authorization

Files:
1. `apps/procurement/runtime/procurement_agent_orchestrator.py`
2. Guardrail service support code if procurement-specific scope helper is added.

Tasks:
1. Enforce data-scope checks based on tenant and scoped role constraints.
2. Deny cross-scope access with explicit guardrail audit logs.

Acceptance criteria:
1. Scoped users cannot run procurement agents for out-of-scope requests.
2. System-agent behavior remains controlled and auditable.

### Workstream D - Tool Authorization Groundwork

Files:
1. `apps/agents/services/guardrails_service.py`
2. Procurement agent files calling external AI/search logic directly.

Tasks:
1. Add permission gates before high-risk external AI/web-calling paths.
2. Record tool-level authorization decisions for later ToolRegistry migration.

Acceptance criteria:
1. External procurement AI paths are authorization-gated.
2. Denied calls produce guardrail audit events.

## 5.4 Phase 2 PR Breakdown

1. **PR-2A**: Procurement permissions and role matrix seed updates.
2. **PR-2B**: Guardrail checks in procurement orchestrator.
3. **PR-2C**: Data-scope enforcement for procurement requests.
4. **PR-2D**: External-call permission gates and audit parity.

## 5.5 Phase 2 Test Plan

Unit tests:
1. authorize_orchestration allow/deny cases.
2. authorize_agent allow/deny cases for procurement agent types.
3. data-scope allow/deny cases.

Integration tests:
1. Authorized procurement run proceeds and writes execution records.
2. Unauthorized run fails closed and emits deny audit event.
3. Scoped user cannot run out-of-scope request.

Audit tests:
1. Guardrail granted events emitted with expected metadata.
2. Guardrail denied events emitted with expected metadata.

## 5.6 Phase 2 Exit Criteria

1. Procurement orchestration is centrally permission-checked.
2. Per-agent procurement execution is centrally permission-checked.
3. Data-scope checks are enforced and audited.
4. No unguarded procurement AI execution path remains.

---

## 6. Recommended Implementation Order

1. Fix benchmark reference drift first.
2. Apply output schema normalization and sanitization.
3. Add procurement permission set and seed updates.
4. Integrate orchestration and agent-level guardrail checks.
5. Add data-scope enforcement and tool authorization groundwork.

Rationale: this sequence removes runtime risk first, then layers governance controls without destabilizing business logic.

---

## 7. Operational Checklist Before Execution

1. Create feature branch for Phase 1 and separate feature branch for Phase 2.
2. Confirm baseline tests are green for procurement and agents apps.
3. Apply migrations and seed updates in dev environment.
4. Run targeted Celery task smoke tests for procurement flows.
5. Review audit logs for new guardrail events.

Suggested commands:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py seed_agent_contracts
python manage.py seed_rbac --sync-users
pytest apps/procurement -q
pytest apps/agents -q
```

---

## 8. Risks and Mitigations

1. **Risk**: permission rollout blocks expected system-triggered jobs.  
   **Mitigation**: explicitly map required procurement permissions to SYSTEM_AGENT role before enabling enforcement.

2. **Risk**: strict schema normalization drops useful model output fields.  
   **Mitigation**: keep raw payload snapshots and only normalize fields used for decisions/persistence.

3. **Risk**: sanitization changes displayed narrative quality.  
   **Mitigation**: sanitize only persisted fields, not internal runtime objects used for prompt chaining.

4. **Risk**: data-scope rules under-specified for procurement dimensions.  
   **Mitigation**: start with tenant + existing scope keys; add procurement-specific dimensions in controlled follow-up.

---

## 9. Definition of Done for This Planning Stage

This planning stage is complete when:
1. Team agrees on the required-fixes register.
2. Team approves PR breakdown for Phase 1 and Phase 2.
3. Owners are assigned per workstream and test gates are accepted.

After approval, execution can begin with Phase 1 PR-1A.
