# ReasoningPlanner -- Architecture, Current State, and LLM-Only Upgrade Path

**Created**: 2026-04-10 | **Status**: Reference document  
**Audience**: Developers, architects, product owners

---

## Table of Contents

1. [Overview](#1-overview)
2. [How the Current Agent Planning Pipeline Works](#2-how-the-current-agent-planning-pipeline-works)
3. [PolicyEngine (Deterministic Planner)](#3-policyengine-deterministic-planner)
4. [ReasoningPlanner (LLM-Enhanced Planner)](#4-reasoningplanner-llm-enhanced-planner)
5. [Orchestrator Integration](#5-orchestrator-integration)
6. [Post-Plan Pipeline: Reflection + Deterministic Resolution](#6-post-plan-pipeline-reflection--deterministic-resolution)
7. [Eval Tracking: LLM vs Deterministic Plan Comparison](#7-eval-tracking-llm-vs-deterministic-plan-comparison)
8. [Approach: Fully LLM-Dependent Planning (No Deterministic Fallback)](#8-approach-fully-llm-dependent-planning-no-deterministic-fallback)
9. [Implementation Checklist for Full LLM Mode](#9-implementation-checklist-for-full-llm-mode)
10. [Risk Matrix and Mitigations](#10-risk-matrix-and-mitigations)
11. [Key Files](#11-key-files)

---

## 1. Overview

The agent pipeline decides **which AI agents** to run for a given reconciliation result and **in what order**. This decision is called a **plan**. Today, plan generation has two modes:

| Mode | Flag | Planner Class | LLM Calls | Production Default |
|------|------|--------------|-----------|-------------------|
| **Deterministic** | `AGENT_REASONING_ENGINE_ENABLED=false` | `PolicyEngine` | 0 | Yes (current default) |
| **LLM-Enhanced** | `AGENT_REASONING_ENGINE_ENABLED=true` | `ReasoningPlanner` | 1 (planning call) | No |

Both modes feed into the same orchestrator, which sequences agent execution, applies reflection rules, and runs deterministic resolution for tail agents.

---

## 2. How the Current Agent Planning Pipeline Works

```
ReconciliationResult (non-MATCHED)
        |
        v
[AgentOrchestrator.__init__()]
        |
        |-- AGENT_REASONING_ENGINE_ENABLED=false --> PolicyEngine.plan()
        |-- AGENT_REASONING_ENGINE_ENABLED=true  --> ReasoningPlanner.plan()
        |                                               |
        |                                               |-- PolicyEngine.plan() runs FIRST (baseline)
        |                                               |-- If skip_agents=True, return immediately
        |                                               |-- Else: call LLM for agent selection
        |                                               |-- On LLM error: fall back to PolicyEngine result
        v
   AgentPlan {agents: [...], plan_source: "deterministic"|"llm", plan_confidence: float}
        |
        v
[Orchestrator.execute()]
        |
        |-- 1. RBAC checks (actor, orchestration permission, data scope)
        |-- 2. Duplicate-run guard (reject if RUNNING orchestration exists)
        |-- 3. If plan.skip_agents: auto-close or skip, return early
        |-- 4. Partition agents: LLM-agents vs deterministic-tail
        |-- 5. Execute LLM agents in sequence (with reflection after each)
        |-- 6. Execute deterministic tail (DeterministicResolver)
        |-- 7. Resolve final recommendation (highest confidence)
        |-- 8. Apply post-policies (auto-close / escalate)
        v
   OrchestrationResult
```

---

## 3. PolicyEngine (Deterministic Planner)

**File**: `apps/agents/services/policy_engine.py`

The PolicyEngine uses hardcoded rules to select agents based on:

- `match_status` (MATCHED, PARTIAL_MATCH, UNMATCHED, REQUIRES_REVIEW, ERROR)
- `deterministic_confidence` (float 0.0-1.0)
- `extraction_confidence` (float 0.0-1.0)
- `reconciliation_mode` (TWO_WAY, THREE_WAY, NON_PO)
- Exception types present on the result

### Decision Rules

| # | Condition | Agent Sequence | Result |
|---|-----------|---------------|--------|
| 1 | MATCHED + high confidence | (none) | `skip_agents=True` |
| 1b | PARTIAL_MATCH within auto-close tolerance band | (none) | `skip_agents=True, auto_close=True` |
| 2 | PO_NOT_FOUND exception | PO_RETRIEVAL -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 3 | GRN_NOT_FOUND (3-way only) | GRN_RETRIEVAL -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 4 | PARTIAL_MATCH (outside auto-close) | RECONCILIATION_ASSIST -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 5 | Low extraction confidence | INVOICE_UNDERSTANDING -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 6 | REQUIRES_REVIEW / UNMATCHED / ERROR (fallback) | EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| NON_PO | Non-PO mode | [INVOICE_UNDERSTANDING if low conf] -> [EXCEPTION_ANALYSIS if exceptions] -> REVIEW_ROUTING -> CASE_SUMMARY | |

### Mode Awareness

- **TWO_WAY**: GRN_RETRIEVAL is never included; GRN_NOT_FOUND exceptions are ignored
- **NON_PO**: No PO/GRN retrieval or reconciliation assist
- **THREE_WAY**: Full agent set available

### Auto-Close Band Check (`_within_auto_close_band`)

Before queuing agents, the PolicyEngine checks if a PARTIAL_MATCH result has all line discrepancies within the wider auto-close tolerance (qty: 5%, price: 3%, amount: 3%). If yes, it skips agents entirely and auto-closes the result (upgrades PARTIAL_MATCH to MATCHED). Exceptions:
- GRN_NOT_FOUND in 3-way blocks auto-close
- First-partial invoices block auto-close
- HIGH severity exceptions block auto-close

### Post-Run Policy Checks

After all agents execute, PolicyEngine provides:
- `should_auto_close(recommendation_type, confidence)` -- AUTO_CLOSE recommendation + confidence >= threshold
- `should_escalate(recommendation_type, confidence)` -- ESCALATE_TO_MANAGER recommendation + confidence >= threshold

These are always deterministic regardless of the planner used.

---

## 4. ReasoningPlanner (LLM-Enhanced Planner)

**File**: `apps/agents/services/reasoning_planner.py`

### Architecture

```
ReasoningPlanner
    |
    +-- PolicyEngine (internal, used for baseline + fallback + post-run checks)
    +-- LLMClient (temperature=0.0, max_tokens=1024)
```

The ReasoningPlanner wraps PolicyEngine. It does NOT replace it. The relationship is:

1. **PolicyEngine runs first** as a baseline (`quick_plan`)
2. If `quick_plan.skip_agents=True` -- return immediately (no LLM call)
3. Otherwise, attempt LLM planning via `_llm_plan()`
4. On any LLM failure -- fall back to `quick_plan` (deterministic)
5. `should_auto_close()` and `should_escalate()` always delegate to PolicyEngine

### LLM Planning Call

The planner sends a single chat completion request with:

**System Prompt** -- describes all 7 available agents, their purposes, and rules:
- GRN_RETRIEVAL must not appear in TWO_WAY mode
- CASE_SUMMARY should be last
- Use minimum set of agents needed
- Assign integer priorities (lower = earlier)
- Respond with valid JSON only

**User Message** -- includes the reconciliation result context:
```
match_status: PARTIAL_MATCH
reconciliation_mode: THREE_WAY
deterministic_confidence: 0.4500
extraction_confidence: 0.8200
exception_types: ["QTY_MISMATCH", "PRICE_MISMATCH"]
```

**Expected Response Schema**:
```json
{
    "overall_reasoning": "Invoice has quantity and price mismatches...",
    "confidence": 0.85,
    "steps": [
        {"agent_type": "RECONCILIATION_ASSIST", "rationale": "...", "priority": 1},
        {"agent_type": "EXCEPTION_ANALYSIS", "rationale": "...", "priority": 2},
        {"agent_type": "REVIEW_ROUTING", "rationale": "...", "priority": 3},
        {"agent_type": "CASE_SUMMARY", "rationale": "...", "priority": 4}
    ]
}
```

### Validation Rules (Post-LLM)

After parsing the LLM response, the planner validates:

1. **Agent type validation**: Only `AgentType` enum values are accepted; unknown values are silently dropped
2. **Priority sorting**: Steps sorted by priority ascending
3. **Non-empty check**: At least one valid step must remain after filtering (raises ValueError otherwise)
4. **CASE_SUMMARY position**: If present, must be last (raises ValueError otherwise)
5. **GRN_RETRIEVAL in TWO_WAY**: Rejected outright (raises ValueError)

Any ValueError from validation triggers the fallback to the deterministic plan.

### AgentPlan Output

```python
AgentPlan(
    agents=["RECONCILIATION_ASSIST", "EXCEPTION_ANALYSIS", "REVIEW_ROUTING", "CASE_SUMMARY"],
    reason="Invoice has quantity and price mismatches...",
    skip_agents=False,
    auto_close=False,
    reconciliation_mode="THREE_WAY",
    plan_source="llm",          # vs "deterministic"
    plan_confidence=0.85,       # LLM's self-assessed confidence
)
```

---

## 5. Orchestrator Integration

**File**: `apps/agents/services/orchestrator.py`

### Planner Selection (Constructor)

```python
class AgentOrchestrator:
    def __init__(self):
        if getattr(settings, "AGENT_REASONING_ENGINE_ENABLED", False):
            self.policy = ReasoningPlanner()
        else:
            self.policy = PolicyEngine()
```

The orchestrator treats both planners identically after `plan()` returns. The `plan_source` and `plan_confidence` fields on `AgentPlan` are propagated to:

- `OrchestrationResult.plan_source` / `plan_confidence`
- `AgentOrchestrationRun.plan_source` / `plan_confidence` (DB record)
- First `AgentRun.input_payload` (includes `plan_source`, `plan_confidence`, `planned_agents`)
- Langfuse trace output metadata

### Agent Partitioning

The orchestrator splits the plan into two groups:

1. **LLM agents** -- executed in sequence via the ReAct loop (PO_RETRIEVAL, GRN_RETRIEVAL, INVOICE_UNDERSTANDING, RECONCILIATION_ASSIST)
2. **Deterministic tail** -- replaced by `DeterministicResolver` (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY become SYSTEM_REVIEW_ROUTING, SYSTEM_CASE_SUMMARY)

This means even when the LLM planner includes EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY in its plan, those are still executed deterministically via `DeterministicResolver` -- not as LLM agents. The planner controls which investigation agents run; the routing/summary step is always rule-based.

---

## 6. Post-Plan Pipeline: Reflection + Deterministic Resolution

### Reflection (Always Active)

After each LLM agent completes, the orchestrator's `_reflect()` method may insert additional agents:

- **After PO_RETRIEVAL**: If a PO was found in a 3-way case and GRN_RETRIEVAL is not already planned/executed, insert GRN_RETRIEVAL
- **After INVOICE_UNDERSTANDING**: If confidence < 0.5 and RECONCILIATION_ASSIST is not already planned/executed, insert RECONCILIATION_ASSIST

Reflection is independent of the planner -- it runs the same whether PolicyEngine or ReasoningPlanner generated the plan.

### DeterministicResolver (Always Active for Tail Agents)

The `DeterministicResolver` handles EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY with rule-based logic that maps exception types to recommendation types:

| Exception Pattern | Recommendation |
|------------------|---------------|
| EXTRACTION_LOW_CONFIDENCE | REPROCESS_EXTRACTION |
| VENDOR_MISMATCH | SEND_TO_VENDOR_CLARIFICATION |
| VENDOR_NOT_VERIFIED (Non-PO) | SEND_TO_AP_REVIEW |
| GRN / receipt issues | SEND_TO_PROCUREMENT |
| Complex (3+ types + HIGH severity) | ESCALATE_TO_MANAGER |
| All others | SEND_TO_AP_REVIEW |

---

## 7. Eval Tracking: LLM vs Deterministic Plan Comparison

**File**: `apps/agents/services/eval_adapter.py`

When the ReasoningPlanner is active, the eval adapter records comparison metrics on the pipeline-level EvalRun:

| Metric | Type | Description |
|--------|------|-------------|
| `plan_source` | string | `"llm"` or `"deterministic"` |
| `plan_source_is_llm` | float | `1.0` if LLM, `0.0` if deterministic |
| `plan_confidence` | float | LLM's self-reported confidence (0.0 for deterministic) |
| `planned_agents_count` | float | Number of agents in the original plan |
| `plan_adherence` | float | Fraction of planned agents that were actually executed |

These metrics enable A/B comparison in the eval UI at `/eval/` to determine whether LLM plans produce better outcomes.

---

## 8. Approach: Fully LLM-Dependent Planning (No Deterministic Fallback)

This section outlines how to make the entire agent pipeline fully LLM-driven, removing the deterministic safety net. This is a **significant architectural change** with real operational risk.

### 8.1 What "Fully LLM-Dependent" Means

| Layer | Current State | Fully LLM Target |
|-------|--------------|-------------------|
| **Plan generation** | PolicyEngine (deterministic) or ReasoningPlanner (LLM with deterministic fallback) | LLM planner only -- no PolicyEngine fallback |
| **Auto-close / skip decision** | PolicyEngine rules (tolerance band, confidence threshold) | LLM decides whether to skip or auto-close |
| **Agent selection** | Pre-defined rule table or LLM-selected (from fixed list) | LLM selects from agent catalog, potentially with dynamic agent composition |
| **Tail agents (routing/summary)** | DeterministicResolver (rule-based) | LLM agents (full ReAct loop for EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY) |
| **Post-run policy** | PolicyEngine.should_auto_close/should_escalate | LLM recommends action; optional deterministic guardrails |
| **Reflection** | Hardcoded rules (2 reflection rules) | LLM-based meta-reasoning after each agent |

### 8.2 Phased Approach

#### Phase 1: LLM Planning with Soft Fallback (Current + Improvements)

**Goal**: Keep deterministic safety net but make the LLM plan authoritative when it succeeds.

Changes needed:
1. Enable `AGENT_REASONING_ENGINE_ENABLED=true` in production
2. Enrich the LLM planner's system prompt with:
   - Historical plan outcomes (from eval data)
   - Auto-close eligibility signals (pass tolerance band results to the LLM)
   - Exception severity information
3. Log plan divergence -- when LLM plan differs from PolicyEngine plan, record both in eval metrics
4. Monitor plan quality via Langfuse scores and eval dashboard

#### Phase 2: LLM Planning without Fallback

**Goal**: Remove the PolicyEngine fallback from the planning step. The LLM planner must succeed or the pipeline errors out with a retry.

Changes to `ReasoningPlanner.plan()`:

```python
def plan(self, result) -> AgentPlan:
    # No quick_plan baseline -- LLM decides everything
    try:
        return self._llm_plan(result)
    except Exception as exc:
        # Instead of falling back to PolicyEngine, retry or error
        logger.error(
            "LLM planner failed for result %s (%s); retrying...",
            getattr(result, "pk", "?"), exc,
        )
        # Retry once with increased temperature
        try:
            return self._llm_plan(result, temperature=0.2)
        except Exception:
            raise PlanningError(
                f"LLM planner failed after retry for result {getattr(result, 'pk', '?')}"
            ) from exc
```

Requirements before this phase:
- [ ] LLM planner success rate > 99% (measured via eval metrics)
- [ ] Plan quality score (adherence + outcome) >= deterministic baseline
- [ ] Retry/circuit-breaker mechanism in LLMClient
- [ ] Alert on planning failures (ops threshold)

#### Phase 3: LLM Auto-Close Decision

**Goal**: Let the LLM decide whether a result should be auto-closed, replacing the tolerance band math.

Add to the LLM planner's prompt and response schema:

```json
{
    "overall_reasoning": "...",
    "confidence": 0.95,
    "auto_close": true,
    "auto_close_rationale": "All line discrepancies are within 2% and there are no high-severity exceptions",
    "steps": []
}
```

The LLM would receive:
- All line-level discrepancy data (qty, price, amount deviations)
- Exception list with severity
- Historical auto-close rate for this vendor/category
- Current tolerance thresholds (as reference context, not enforcement)

Guardrail: Even in full LLM mode, keep a **hard ceiling** -- never auto-close if any exception has severity=HIGH or total amount deviation > 10%. This is a safety guardrail, not a deterministic planner.

#### Phase 4: LLM Tail Agents (Replace DeterministicResolver)

**Goal**: Run EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY as full LLM agents instead of the rule-based DeterministicResolver.

Changes:
1. Remove `_SYSTEM_AGENT_REPLACEMENTS` mapping in orchestrator
2. Remove `DeterministicResolver` partitioning -- all agents run through the ReAct loop
3. These agents would use tools to:
   - Query exception details (existing `exception_list` tool)
   - Look up routing rules (new tool needed)
   - Read previous agent summaries from AgentMemory
   - Generate structured case summaries

Trade-offs:
- **Cost**: +2-3 LLM calls per pipeline run (exception analysis + routing + summary)
- **Latency**: +15-45 seconds per pipeline run
- **Quality**: Potentially better for complex multi-exception cases; worse for simple cases where rules are already perfect
- **Auditability**: LLM reasoning is captured in AgentRun.summarized_reasoning, but less predictable than rule tables

#### Phase 5: LLM-Based Reflection

**Goal**: Replace the two hardcoded reflection rules with LLM meta-reasoning.

After each agent completes, call the LLM with:
- The agent's output (reasoning, confidence, recommendation)
- Remaining planned agents
- Current AgentMemory state
- All exceptions (resolved and unresolved)

Ask the LLM: "Should any additional agents be inserted? Should any planned agents be removed? Should the pipeline stop early?"

This requires a lightweight "meta-agent" call with a focused system prompt and the current pipeline state.

### 8.3 Enhanced LLM Planner Prompt (for Phase 2+)

The current prompt only lists agent descriptions and basic rules. A fully LLM-dependent planner needs richer context:

```
You are an expert AP reconciliation pipeline planner. Decide which AI agents
should investigate this reconciliation result and in what order.

AVAILABLE AGENTS:
  PO_RETRIEVAL          - Searches for the correct Purchase Order [...]
  GRN_RETRIEVAL         - Investigates Goods Receipt Notes [...]
  INVOICE_UNDERSTANDING - Re-analyses extracted invoice fields [...]
  RECONCILIATION_ASSIST - Investigates partial-match discrepancies [...]
  EXCEPTION_ANALYSIS    - Performs root-cause analysis [...]
  REVIEW_ROUTING        - Determines the correct review queue [...]
  CASE_SUMMARY          - Produces a concise case summary [...]

RECONCILIATION RESULT:
  match_status: {match_status}
  reconciliation_mode: {recon_mode}
  deterministic_confidence: {det_confidence}
  extraction_confidence: {extraction_confidence}
  exception_types: {exc_types}
  exception_severities: {exc_severities}

LINE-LEVEL DISCREPANCIES:
  {line_discrepancy_summary}

TOLERANCE THRESHOLDS (reference):
  strict: qty={strict_qty}%, price={strict_price}%, amount={strict_amount}%
  auto_close: qty={ac_qty}%, price={ac_price}%, amount={ac_amount}%
  all_within_auto_close_band: {within_band}

HISTORICAL CONTEXT:
  vendor_auto_close_rate: {vendor_ac_rate}
  similar_case_resolution_pattern: {similar_pattern}

DECISION OPTIONS:
  1. skip_agents=true, auto_close=true  -- Auto-close (no agents needed)
  2. skip_agents=true, auto_close=false -- Skip agents (clean match)
  3. steps=[...]                        -- Run these agents in order

RULES:
  1. GRN_RETRIEVAL must never appear when reconciliation_mode is TWO_WAY
  2. CASE_SUMMARY should be last
  3. Use the minimum set of agents needed
  4. Be conservative with auto_close -- only when you are highly confident
  5. Assign each step a unique integer priority starting from 1

Respond ONLY with valid JSON:
{
    "overall_reasoning": "...",
    "confidence": 0.9,
    "auto_close": false,
    "skip_agents": false,
    "steps": [{"agent_type": "...", "rationale": "...", "priority": 1}]
}
```

### 8.4 Circuit Breaker for LLM Planning

In a fully LLM-dependent mode, you need protection against sustained LLM outages:

```python
class LLMPlannerCircuitBreaker:
    """Circuit breaker pattern for the LLM planner.
    
    States:
      CLOSED   -- Normal operation, LLM planner is called
      OPEN     -- LLM planner is bypassed (too many recent failures)
      HALF_OPEN -- Allow one probe request to test recovery
    
    Thresholds:
      failure_threshold: 5 failures in rolling window -> OPEN
      recovery_timeout: 60 seconds -> transition to HALF_OPEN
    """
```

When the circuit breaker is OPEN, the planner could:
- Fall back to PolicyEngine (safest, contradicts "fully LLM" goal)
- Queue the result for retry via Celery delayed task
- Return a minimal plan (EXCEPTION_ANALYSIS + REVIEW_ROUTING + CASE_SUMMARY) as a conservative default

---

## 9. Implementation Checklist for Full LLM Mode

### Pre-requisites (before enabling any phase)

- [ ] **Eval baseline established**: Run 100+ reconciliation results with `AGENT_REASONING_ENGINE_ENABLED=false` and record plan outcomes
- [ ] **LLM plan quality measured**: Run same results with `AGENT_REASONING_ENGINE_ENABLED=true` and compare:
  - Plan divergence rate (how often LLM disagrees with PolicyEngine)
  - Final recommendation accuracy (compared to human review decisions)
  - Pipeline completion rate (success vs error)
- [ ] **Langfuse dashboards built**: Filterable by `plan_source` to compare LLM vs deterministic
- [ ] **Cost model validated**: Average LLM token usage per planning call * expected volume

### Phase 1 (LLM with fallback -- low risk)

- [ ] Set `AGENT_REASONING_ENGINE_ENABLED=true` in production `.env`
- [ ] Monitor `plan_source` distribution in Langfuse (expect most plans to be "llm")
- [ ] Track fallback rate (plan_source="deterministic" when ReasoningPlanner is active means LLM failed)
- [ ] Run for 2+ weeks before proceeding

### Phase 2 (LLM without fallback -- medium risk)

- [ ] Implement retry logic in `_llm_plan()`
- [ ] Add circuit breaker with monitoring
- [ ] Create `PlanningError` exception class
- [ ] Update orchestrator to handle `PlanningError` (retry task, don't silently skip)
- [ ] Add ops alert for planning failure rate > 1%

### Phase 3 (LLM auto-close -- high risk)

- [ ] Expand LLM prompt with line-level discrepancy data
- [ ] Add `auto_close` and `skip_agents` to LLM response schema
- [ ] Implement hard-ceiling guardrails (HIGH severity, amount deviation)
- [ ] Shadow-mode first: run LLM auto-close decision alongside deterministic, compare results, do NOT act on LLM's decision
- [ ] Gradual rollout: start with specific vendors/categories where auto-close accuracy is already high

### Phase 4 (LLM tail agents -- medium risk, high cost)

- [ ] Create full LLM-based ExceptionAnalysisAgent (already exists: `AgentType.EXCEPTION_ANALYSIS`)
- [ ] Create full LLM-based ReviewRoutingAgent (already exists: `AgentType.REVIEW_ROUTING`)
- [ ] Keep CaseSummaryAgent as LLM (already exists: `AgentType.CASE_SUMMARY`)
- [ ] Remove `DeterministicResolver` from orchestrator
- [ ] Remove `_SYSTEM_AGENT_REPLACEMENTS` mapping
- [ ] Monitor cost increase and latency impact

### Phase 5 (LLM reflection -- medium risk)

- [ ] Create `ReflectionAgent` or lightweight meta-call
- [ ] Replace hardcoded `_reflect()` rules with LLM call
- [ ] Maintain the same reflection insertion mechanism (returns list of agent_type strings)

---

## 10. Risk Matrix and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| LLM outage stops all reconciliation | HIGH | LOW | Circuit breaker + Celery retry + optional PolicyEngine emergency fallback |
| LLM plans wrong agents (wastes time/cost) | MEDIUM | MEDIUM | Validation rules (current: GRN in TWO_WAY, CASE_SUMMARY position) + eval monitoring |
| LLM auto-closes a result that should go to review | HIGH | LOW | Hard-ceiling guardrails (HIGH severity, amount threshold) + shadow mode rollout |
| LLM cost increase (3-5x more tokens per pipeline) | MEDIUM | HIGH | Phase gradually; measure cost-per-invoice; set budget caps |
| LLM latency increase (+15-45s per pipeline) | LOW | HIGH | Acceptable for async Celery pipeline; consider parallel agent execution for independent agents |
| LLM hallucinates non-existent agent types | LOW | LOW | Already handled: validation drops unknown agent types |
| LLM ignores mode constraints (GRN in TWO_WAY) | MEDIUM | LOW | Already handled: post-parse validation raises ValueError |

---

## 11. Key Files

| File | Purpose |
|------|---------|
| `apps/agents/services/reasoning_planner.py` | LLM-enhanced planner (ReasoningPlanner class) |
| `apps/agents/services/policy_engine.py` | Deterministic planner (PolicyEngine class) + AgentPlan dataclass |
| `apps/agents/services/orchestrator.py` | Agent pipeline orchestration (planner selection, execution, reflection) |
| `apps/agents/services/llm_client.py` | LLM client (Azure OpenAI / OpenAI chat completion) |
| `apps/agents/services/deterministic_resolver.py` | Rule-based tail agent replacement |
| `apps/agents/services/agent_memory.py` | Cross-agent structured memory |
| `apps/agents/services/eval_adapter.py` | Eval tracking for plan source comparison |
| `apps/agents/tests/test_reasoning_planner.py` | 17 tests covering LLM plan, fallback, validation, orchestrator flag wiring |
| `config/settings.py` | `AGENT_REASONING_ENGINE_ENABLED` setting (line 274) |
| `apps/core/enums.py` | `AgentType` enum (valid agent type values) |
