# 18 - Procurement Agent Upgrade Path (Phase 0 to Phase 6)

**Generated**: 2026-04-13  
**Method**: Code-first inspection + architecture comparison  
**Scope**: Full procurement agent upgrade execution now (no deferred phases)

---

## 1. Objective

This document is the execution plan to align procurement agents with the shared enterprise agentic architecture across all phases.

Execution objectives:
1. Stabilize current runtime and remove blocking defects.
2. Standardize contracts, safety, and output governance.
3. Enforce centralized guardrails, permissions, and data-scope controls.
4. Migrate procurement runtime to shared agent base classes.
5. Migrate procurement external calls to ToolRegistry contracts.
6. Enable planner-driven multi-agent procurement orchestration.
7. Achieve governance and observability parity with reconciliation/posting stacks.

No deferred backlog in this version. All previously identified changes are in scope now.

---

## 1.1 Coverage Matrix Against Prior Change List

| Prior Change Item | Covered Here | Where Covered | Status |
|---|---|---|---|
| Phase 0 baseline stabilization | Yes | Section 4 | In scope now |
| Runtime model gap (ProcurementAgentExecutionRecord vs AgentRun lifecycle) | Yes | Sections 2, 7 | In scope now |
| Planning/orchestration gap | Yes | Section 9 | In scope now |
| Guardrails/authorization gap | Yes | Section 6 | In scope now |
| Tooling gap (ToolRegistry contract) | Yes | Section 8 | In scope now |
| Output contract and safety gap | Yes | Section 5 | In scope now |
| Trace/governance depth gap | Yes | Section 10 | In scope now |
| Benchmark agent reference inconsistency | Yes | Sections 3.1, 4.3 | In scope now |
| Practical migration order | Yes | Section 11 | In scope now |
| PR-by-PR rollout and exit criteria | Yes | Sections 4 through 10 | In scope now |

Interpretation:
1. Every previously identified gap is explicitly covered.
2. This is an execution document, not a phased deferral memo.

---

## 2. Current State Snapshot

Procurement currently runs through a compatibility bridge:
1. Runtime bridge: `apps/procurement/runtime/procurement_agent_orchestrator.py`
2. Procurement context and memory: `apps/procurement/runtime/procurement_agent_context.py`, `apps/procurement/runtime/procurement_agent_memory.py`
3. Additive execution model: `ProcurementAgentExecutionRecord` in `apps/procurement/models.py`

Shared platform architecture uses:
1. Base runtime: `apps/agents/services/base_agent.py`
2. Orchestrator: `apps/agents/services/orchestrator.py`
3. Planner: `apps/agents/services/reasoning_planner.py`
4. Guardrails: `apps/agents/services/guardrails_service.py`
5. Output schema: `apps/agents/services/agent_output_schema.py`
6. Trace service: `apps/agents/services/agent_trace_service.py`

Result: procurement is partially aligned and requires full convergence.

---

## 3. Required Fixes Register (Must Be Addressed During Execution)

## 3.1 Blocking Fixes

1. **Benchmark runtime downgraded to compatibility bridge**  
   Files: `apps/procurement/tasks.py`, `apps/benchmarking/services/procurement_cost_service.py`, `apps/benchmarking/services/procurement_benchmark_service.py`  
   Issue: current BENCHMARK execution no longer calls a procurement-local benchmark service. It dispatches to a compatibility bridge that creates a `BenchmarkResult` with `total_benchmark_amount = total_quoted_amount`, `variance_pct = 0`, `risk_level = LOW`, and a `summary_json` note indicating compatibility mode.  
   Required fix: restore or replace the full should-cost benchmark engine with corridor resolution, variance classification, and per-line evidence.

2. **Inconsistent AI-path orchestration coverage**  
   Files:
   - `apps/procurement/services/recommendation_service.py`
   - `apps/procurement/services/market_intelligence_service.py`
   - `apps/procurement/services/validation/validation_agent.py`  
   Required fix: enforce centralized runtime wrappers and governance entrypoints for all LLM paths.

## 3.2 High-Priority Safety Fixes

1. Schema normalization for all LLM outputs before persistence.
2. ASCII-safe persistence for all LLM-generated text stored in DB.
3. Fail-closed behavior on authorization denial.

## 3.3 Governance Fixes

1. Procurement-specific permission matrix expansion.
2. Procurement data-scope authorization checks.
3. Tool-level permission enforcement for external call paths.

---

## 4. Phase 0 - Stabilize and Baseline

## 4.1 Goal

Eliminate immediate runtime hazards and establish baseline confidence before structural migration.

## 4.2 Deliverables

1. Benchmark reference/runtime integrity fixed.
2. Baseline integration tests for procurement bridge flows.
3. Baseline smoke checks for procurement tasks.

## 4.3 Workstreams

### Workstream A - Runtime Integrity

Files:
1. `apps/procurement/tasks.py`
2. `apps/benchmarking/services/procurement_cost_service.py`
3. Any selected full benchmark implementation module.

Tasks:
1. Replace compatibility-bridge benchmark execution with the intended full runtime.
2. Preserve deterministic-first benchmark fallback behavior.

Acceptance criteria:
1. Benchmark flow executes full corridor comparison instead of zero-variance placeholder output.
2. Functional behavior remains stable.

### Workstream B - Baseline Test Coverage

Files:
1. `apps/procurement/tests/`

Tasks:
1. Add integration tests for recommendation, benchmark, validation augmentation, market intelligence.
2. Add task-level smoke tests for procurement analysis tasks.

Acceptance criteria:
1. Baseline tests pass and become pre-merge gate.

## 4.4 PR Breakdown

1. **PR-0A**: Runtime integrity fixes.
2. **PR-0B**: Baseline test suite additions.

## 4.5 Exit Criteria

1. No baseline runtime blockers remain.
2. Baseline tests are green.

---

## 5. Phase 1 - Contract and Safety Standardization

## 5.1 Goal

Align procurement output and persistence behavior to shared architecture contracts without changing external procurement APIs.

## 5.2 Deliverables

1. Procurement agent catalog aligned with shared definitions.
2. Shared-schema output normalization and confidence clamping.
3. ASCII-safe sanitization for persisted model-generated text.

## 5.3 Workstreams

### Workstream A - Agent Catalog Alignment

Files:
1. `apps/core/enums.py`
2. Agent definition seed command and related seed assets.

Tasks:
1. Add procurement agent types intended for shared runtime convergence.
2. Seed/update corresponding AgentDefinition records.

Acceptance criteria:
1. Procurement agents are represented in shared catalog model.
2. Seed process remains idempotent.

### Workstream B - Schema-Normalized Output Handling

Files:
1. `apps/procurement/services/recommendation_service.py`
2. `apps/procurement/services/benchmark_service.py`
3. `apps/procurement/services/validation/orchestrator_service.py`
4. `apps/procurement/services/validation/validation_agent.py`
5. `apps/procurement/services/market_intelligence_service.py`

Tasks:
1. Normalize LLM outputs to shared output schema semantics.
2. Clamp confidence to [0.0, 1.0].
3. Map invalid recommendation values to safe defaults.

Acceptance criteria:
1. Malformed LLM payloads degrade gracefully.
2. Persisted confidence/recommendation fields are valid.

### Workstream C - ASCII Sanitization

Files:
1. `apps/procurement/agents/reason_summary_agent.py`
2. `apps/procurement/agents/compliance_agent.py`
3. `apps/procurement/services/recommendation_service.py`
4. `apps/procurement/services/validation/validation_agent.py`

Tasks:
1. Sanitize persisted LLM-generated text prior to save.
2. Preserve non-persisted runtime text for internal reasoning continuity.

Acceptance criteria:
1. Persisted LLM text fields are ASCII-safe.

## 5.4 PR Breakdown

1. **PR-1A**: Catalog alignment.
2. **PR-1B**: Output normalization and confidence guards.
3. **PR-1C**: ASCII sanitization.

## 5.5 Test Plan

1. Output schema coercion tests.
2. Confidence clamp edge-case tests.
3. Sanitization behavior tests.
4. Regression tests on recommendation/validation/market intelligence paths.

## 5.6 Exit Criteria

1. Procurement output handling follows shared contract semantics.
2. Persisted LLM text passes ASCII safety requirement.

---

## 6. Phase 2 - Guardrails and Permission Convergence

## 6.1 Goal

Bring procurement runtime under centralized guardrails with strict authorization and auditability.

## 6.2 Deliverables

1. Procurement orchestration authorization through `AgentGuardrailsService`.
2. Per-agent procurement authorization.
3. Procurement permissions seeded and role-mapped.
4. Procurement data-scope enforcement.

## 6.3 Workstreams

### Workstream A - Permission Model Expansion

Files:
1. RBAC seed command files in accounts/core management commands.
2. `apps/agents/services/guardrails_service.py` (permission map extensions).

Tasks:
1. Add procurement orchestration and per-agent permissions.
2. Map permissions to business roles and system agent.

Acceptance criteria:
1. New permissions are seeded and visible in role matrix.

### Workstream B - Orchestrator Guardrail Integration

Files:
1. `apps/procurement/runtime/procurement_agent_orchestrator.py`

Tasks:
1. Resolve actor via centralized guardrails.
2. Authorize orchestration and per-agent execution before invocation.
3. Log grant/deny audit events.

Acceptance criteria:
1. Unauthorized runs are blocked before model/tool execution.
2. Guardrail decisions are auditable.

### Workstream C - Data Scope Enforcement

Files:
1. `apps/procurement/runtime/procurement_agent_orchestrator.py`
2. Scope helper additions if needed in guardrails/service utilities.

Tasks:
1. Enforce tenant and role-scope checks for procurement request context.
2. Deny out-of-scope runs with audit logs.

Acceptance criteria:
1. Scoped users cannot execute out-of-scope procurement agent runs.

## 6.4 PR Breakdown

1. **PR-2A**: Permission and role matrix updates.
2. **PR-2B**: Orchestrator guardrail integration.
3. **PR-2C**: Data-scope enforcement.

## 6.5 Test Plan

1. Authorization allow/deny tests.
2. Out-of-scope deny tests.
3. Guardrail audit event assertion tests.

## 6.6 Exit Criteria

1. Procurement runs are centrally permission-checked and scope-checked.
2. No unguarded orchestration path remains.

---

## 7. Phase 3 - Runtime Convergence to Shared Agent Base

## 7.1 Goal

Migrate procurement execution onto shared `BaseAgent` and deterministic system-agent patterns.

## 7.2 Deliverables

1. LLM procurement agents implemented on shared agent runtime.
2. Deterministic procurement flows wrapped for standardized governance where required.
3. Shared `AgentRun` lifecycle as primary execution record.

## 7.3 Workstreams

### Workstream A - LLM Agent Migration

Candidate targets:
1. Request extraction reasoning path.
2. HVAC recommendation reasoning path.
3. Compliance augmentation path.
4. Market intelligence summarization path.

Tasks:
1. Implement concrete shared-runtime agent classes.
2. Use shared context/output contracts.
3. Persist full shared run/message/step/decision artifacts.

Acceptance criteria:
1. Procurement LLM runs generate shared agent artifacts end-to-end.

### Workstream B - Deterministic Wrapper Migration

Candidate targets:
1. RFQ routing/governance wrapper.
2. Deterministic validation wrapper.
3. Rules recommendation wrapper.

Tasks:
1. Wrap deterministic operations using deterministic system-agent pattern where governance parity is needed.

Acceptance criteria:
1. Deterministic procurement operations emit standard run and decision records.

### Workstream C - Execution Record Transition

Files:
1. `apps/procurement/models.py`
2. `apps/procurement/runtime/procurement_agent_orchestrator.py`

Tasks:
1. Transition from procurement execution record dependence to shared AgentRun system-of-record.
2. Retire or de-emphasize bridge-specific record as compatibility step completes.

Acceptance criteria:
1. Shared AgentRun lifecycle is primary source for procurement agent execution history.

## 7.4 PR Breakdown

1. **PR-3A**: LLM agent class migration.
2. **PR-3B**: Deterministic wrapper migration.
3. **PR-3C**: Execution record transition.

## 7.5 Test Plan

1. Agent contract tests for new classes.
2. Regression tests for procurement APIs/tasks.
3. Governance persistence integrity tests.

## 7.6 Exit Criteria

1. Procurement runtime aligns with shared base agent model.

---

## 8. Phase 4 - Full ToolRegistry Migration

## 8.1 Goal

Move procurement external intelligence/lookup behavior to registered tools with centralized permission and evidence controls.

## 8.2 Deliverables

1. Procurement tool suite registered with `@register_tool` and required permissions.
2. Direct external calls replaced by tool invocations.
3. Tool-call provenance persisted uniformly.

## 8.3 Workstreams

### Workstream A - Tool Authoring

Target tool set:
1. `market_benchmark_lookup`
2. `vendor_catalog_lookup`
3. `standards_compliance_lookup`
4. `quotation_evidence_lookup`
5. `regional_regulation_lookup`

Tasks:
1. Implement tool classes and schemas.
2. Define authoritative fields and evidence keys.

Acceptance criteria:
1. Tools are available in registry specs with permission enforcement.

### Workstream B - Agent Call-Site Migration

Tasks:
1. Replace direct external call blocks in procurement agents/services with ToolRegistry calls.
2. Keep fallback and failure semantics behaviorally equivalent.

Acceptance criteria:
1. No ungoverned direct external call path remains.

## 8.4 PR Breakdown

1. **PR-4A**: Procurement tool definitions.
2. **PR-4B**: Call-site migration to ToolRegistry.

## 8.5 Test Plan

1. Tool unit tests.
2. Permission-deny tool tests.
3. Integration tests confirming tool-routed execution.

## 8.6 Exit Criteria

1. Procurement external intelligence paths are fully ToolRegistry-governed.

---

## 9. Phase 5 - Planner-Enabled Procurement Multi-Agent Orchestration

## 9.1 Goal

Enable planner-driven, confidence-aware sequencing of procurement agents by run type.

## 9.2 Deliverables

1. Procurement planning strategy integrated into shared planning approach.
2. Top-level procurement orchestration run state persistence.
3. Duplicate-run guard and partial-failure semantics.

## 9.3 Workstreams

### Workstream A - Planner Strategy

Tasks:
1. Implement procurement planner or shared planner extension.
2. Define allowed execution chains by analysis run type.

Acceptance criteria:
1. Planner returns valid sequences with deterministic fallback.

### Workstream B - Orchestration Run State

Tasks:
1. Add procurement orchestration run state model.
2. Add duplicate-run prevention and partial completion states.

Acceptance criteria:
1. Procurement orchestration has top-level run visibility and lifecycle states.

## 9.4 PR Breakdown

1. **PR-5A**: Planner strategy.
2. **PR-5B**: Orchestration state model and wiring.

## 9.5 Test Plan

1. Planner output validation tests.
2. Duplicate-run guard tests.
3. Partial-failure behavior tests.

## 9.6 Exit Criteria

1. Procurement orchestration is planner-driven with deterministic fallback and run-state governance.

---

## 10. Phase 6 - Governance and Observability Parity

## 10.1 Goal

Match governance and observability depth of shared enterprise agent flows.

## 10.2 Deliverables

1. Full `AgentTraceService` depth for procurement runs.
2. Langfuse conventions aligned with platform standards.
3. Governance API/view visibility for procurement traces.

## 10.3 Workstreams

### Workstream A - Trace Depth Integration

Tasks:
1. Ensure steps/messages/tool calls/decisions are fully captured for procurement agent runs.

Acceptance criteria:
1. Procurement traces are reconstructable with full run detail.

### Workstream B - Langfuse Alignment

Tasks:
1. Align naming/session/score conventions and linkage behavior.

Acceptance criteria:
1. Procurement traces and scores are linked and queryable with standard dashboards.

### Workstream C - Governance Surface Integration

Tasks:
1. Add procurement trace support in governance APIs/views where needed.
2. Enforce governance access permissions.

Acceptance criteria:
1. Authorized users can inspect procurement traces through governance surfaces.

## 10.4 PR Breakdown

1. **PR-6A**: Trace depth integration.
2. **PR-6B**: Langfuse alignment.
3. **PR-6C**: Governance API/view integration.

## 10.5 Test Plan

1. End-to-end trace completeness tests.
2. Langfuse linkage tests.
3. Governance authorization tests.

## 10.6 Exit Criteria

1. Procurement governance and observability parity achieved.

---

## 11. Recommended Full Execution Order

1. Phase 0: Stabilize and baseline.
2. Phase 1: Contract and safety standardization.
3. Phase 2: Guardrails and permissions convergence.
4. Phase 3: Runtime convergence to shared agent base.
5. Phase 4: Full ToolRegistry migration.
6. Phase 5: Planner-enabled procurement orchestration.
7. Phase 6: Governance and observability parity.

---

## 12. Operational Checklist Before Execution

1. Create dedicated branch per phase.
2. Confirm baseline tests are green before each phase start.
3. Run migrations and seed updates as required per phase.
4. Run targeted smoke tests after each phase.
5. Review audit and trace output after each phase.
6. Track latency and failure-rate deltas across phases.

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

## 13. Risks and Mitigations

1. **Risk**: permission rollout blocks system-triggered jobs.  
   **Mitigation**: explicit permission grants for SYSTEM_AGENT before enforcement.

2. **Risk**: strict schema normalization drops useful optional model output.  
   **Mitigation**: preserve raw payload snapshots while normalizing decision/persisted fields.

3. **Risk**: sanitization affects user-visible narrative quality.  
   **Mitigation**: sanitize persisted fields only, preserve runtime internal reasoning context.

4. **Risk**: data-scope policies are initially incomplete for procurement dimensions.  
   **Mitigation**: start tenant + current scope keys, then extend with procurement-specific dimensions.

5. **Risk**: runtime migration introduces hidden dependency mismatch.  
   **Mitigation**: facade-preserving migrations and phase gates.

6. **Risk**: tool migration adds latency.  
   **Mitigation**: baseline and monitor latency as rollout gate.

7. **Risk**: planner introduces non-deterministic execution drift.  
   **Mitigation**: strict planner output validation and deterministic fallback.

---

## 14. Definition of Done for Planning and Execution Readiness

Execution is ready to start when:
1. Team approves all phase workstreams and PR breakdowns.
2. Owners are assigned per phase and per workstream.
3. Test gates are accepted as mandatory promotion criteria.

After approval, execution starts with **Phase 0, PR-0A**.

---

## 15. Scope Completeness Confirmation

For the question "have we covered all changes mentioned in the gap analysis":
1. Yes, all previously identified changes are mapped in this document.
2. Yes, all phases now have executable workstreams, PRs, tests, and exit criteria.
3. Yes, no deferred backlog remains in this version.
