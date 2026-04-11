# Procurement Intelligence Platform — Project Documentation

> **Version**: 2.0 · **Last Updated**: April 2026  
> **Stack**: Django 4.2 · MySQL · Celery + Redis · Azure OpenAI · Bootstrap 5  
> **App**: `apps.procurement`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)  
   2a. [Phase 1 Agentic Bridge](#2a-phase-1-agentic-bridge-architecture)
3. [Data Models](#3-data-models)
4. [Business Enumerations](#4-business-enumerations)
5. [Service Layer](#5-service-layer)
6. [Agent System](#6-agent-system)
7. [API Reference](#7-api-reference)
8. [Template Views & UI](#8-template-views--ui)
9. [Celery Tasks](#9-celery-tasks)
10. [Governance & Audit Integration](#10-governance--audit-integration)
11. [Observability Integration](#11-observability-integration)
12. [RBAC & Permissions](#12-rbac--permissions)
13. [File Organization](#13-file-organization)
14. [Status Transitions](#14-status-transitions)
15. [Flow Walkthroughs](#15-flow-walkthroughs)
16. [Configuration & Extension Points](#16-configuration--extension-points)
17. [Market Intelligence](#17-market-intelligence)

---

## 1. Executive Summary

The **Procurement Intelligence Platform** is a generic, domain-agnostic module built on top of the existing Django enterprise stack. All procurement models (`ProcurementRequest`, `ProcurementRecommendation`, quotation-related models) are tenant-scoped via the `CompanyProfile` FK (see [MULTI_TENANT.md](MULTI_TENANT.md)). It supports three primary analysis flows:

| Flow | Description |
|---|---|
| **Product / Solution Recommendation** | Given a set of requirements (attributes), apply deterministic rules and optionally invoke AI to recommend the best product or solution |
| **Should-Cost Benchmarking** | Given supplier quotations with line items, resolve market benchmark prices, compute variance, classify risk, and flag outliers |
| **Validation** | Given a procurement request with attributes/documents/quotations, run 6 deterministic validation dimensions (attribute completeness, document completeness, scope coverage, ambiguity detection, commercial completeness, compliance readiness) with optional AI augmentation for ambiguity resolution |

### Core Design Principles

- **Request-centric** — Uses `ProcurementRequest` + `AnalysisRun` hierarchy (NOT the existing AP case model)
- **Deterministic first** — Rule-based logic runs before any LLM invocation; AI is only called when rules are insufficient
- **Domain-agnostic** — `domain_code` and `schema_code` fields allow any business domain (HVAC, IT, Facilities, etc.) without hardcoded logic
- **Re-uses existing governance** — All audit logging, traceability, and observability use the existing platform services (no new governance modules)
- **Stateless services** — All business logic lives in service classes with static/class methods

### Business Flow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│  RECOMMENDATION FLOW                                            │
│                                                                 │
│  Create Request → Define Attributes → Mark Ready                │
│    → Create AnalysisRun(RECOMMENDATION)                         │
│    → Validate Attributes → Apply Rules → [Invoke AI if needed]  │
│    → Compliance Check → Save RecommendationResult               │
│    → Update Request Status                                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  BENCHMARK FLOW                                                 │
│                                                                 │
│  Create Request → Upload Quotation(s) → Add Line Items          │
│    → Normalize Line Items                                       │
│    → Create AnalysisRun(BENCHMARK)                              │
│    → Resolve Benchmark Prices → Compute Variance → Classify Risk│
│    → Save BenchmarkResult + BenchmarkResultLines                │
│    → Update Request Status                                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  VALIDATION FLOW                                                │
│                                                                 │
│  Create Request → Define Attributes → Upload Quotations         │
│    → Create AnalysisRun(VALIDATION)                             │
│    → Resolve Validation Rules (domain/schema-specific)          │
│    → Run 6 Deterministic Validators                             │
│    → [Optional AI Augmentation for ambiguity resolution]        │
│    → Score & Classify → Save ValidationResult + Items           │
│    → Update Request Status                                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Overview

### Hierarchy

```
ProcurementRequest (top-level business entity)
  ├─ ProcurementRequestAttribute  (dynamic key-value requirements)
  ├─ SupplierQuotation            (vendor quote with document link)
  │    └─ QuotationLineItem       (individual priced items)
  └─ AnalysisRun                  (execution instance -- can have many per request)
       ├─ RecommendationResult    (1:1 with RECOMMENDATION run)
       ├─ BenchmarkResult         (1:N with BENCHMARK run per quotation)
       │    └─ BenchmarkResultLine  (per-line comparison)
       ├─ ComplianceResult        (1:1 compliance check output)
       ├─ ValidationResult        (1:1 with VALIDATION run)
       │    └─ ValidationResultItem  (individual findings)
       └─ ProcurementAgentExecutionRecord  (Phase 1 -- one per AI agent invocation)

ValidationRuleSet (reusable rule definitions, domain/schema-scoped)
  └─ ValidationRule (individual rules within a set)
```

### Layered Architecture

```
+-----------------------------------------------+
|              UI Layer (Bootstrap 5)           |
|  request_list . request_create . workspace .  |
|  run_detail . validation_summary partial      |
+-----------------------------------------------+
|              API Layer (DRF)                  |
|  ProcurementRequestViewSet (CRUD + actions)   |
|  SupplierQuotationViewSet                     |
|  ValidationRuleSetViewSet (read-only)         |
|  AnalysisRunValidationView                    |
+-----------------------------------------------+
|            Celery Tasks                       |
|  run_analysis_task . run_validation_task      |
+-----------------------------------------------+
|            Service Layer                      |
|  ProcurementRequestService . AttributeService |
|  QuotationService . LineItemNormalizationSvc  |
|  RecommendationService . BenchmarkService     |
|  ComplianceService . AnalysisRunService       |
+-----------------------------------------------+
|            Agentic Bridge (Phase 1)           |
|  ProcurementAgentOrchestrator                 |
|  ProcurementAgentContext                      |
|  ProcurementAgentMemory                       |
+-----------------------------------------------+
|            Agent Layer                        |
|  RecommendationAgent . BenchmarkAgent         |
|  ComplianceAgent . ValidationAgentService     |
+-----------------------------------------------+
|       Existing Platform Services (REUSED)     |
|  AuditService . TraceContext . MetricsService |
|  LLMClient . @observed_service/task           |
|  RBAC . ProcessingLog . AuditEvent            |
+-----------------------------------------------+
```

---

## 2a. Phase 1 Agentic Bridge Architecture

> Added in v2.0 (April 2026). This section documents the thin compatibility bridge
> added to make `apps.procurement` consistent with the shared agentic platform
> patterns already used by reconciliation, extraction, posting, and ERP integration.

### Motivation

Prior to Phase 1, the three AI entry points in `apps.procurement` invoked their
underlying agents directly (bypassing shared governance):

| Entry Point | AI Call (before) |
|---|---|
| `RecommendationService.run_recommendation()` | `RecommendationGraphService.run()` inline |
| `BenchmarkService.run_benchmark()` | `BenchmarkAgent.resolve_benchmark_for_item()` inline |
| `ValidationOrchestratorService._run_agent_augmentation()` | `ValidationAgentService.augment_findings()` inline |

This meant: no `ProcurementAgentExecutionRecord` DB row, no Langfuse span, no
`PROCUREMENT_AGENT_RUN_*` audit events, and no standard context/memory bag.

### Phase 1 Bridge Components

All new files live under `apps/procurement/runtime/`.

#### `ProcurementAgentMemory`
Dataclass (`apps/procurement/runtime/procurement_agent_memory.py`). Analogous to
`apps.agents.services.agent_memory.AgentMemory` but scoped to procurement. Holds
cross-agent working memory during a single orchestration run:

```python
@dataclass
class ProcurementAgentMemory:
    recommended_solution: Optional[str]
    recommended_category: Optional[str]
    benchmark_findings: Dict[str, Any]    # keyed by line pk or item code
    compliance_findings: Dict[str, Any]
    validation_flags: Dict[str, str]
    market_signals: List[str]
    agent_summaries: Dict[str, str]       # agent_type -> summary text
    facts: Dict[str, Any]
    current_recommendation: Optional[str]
    current_confidence: float
```

#### `ProcurementAgentContext`
Dataclass (`apps/procurement/runtime/procurement_agent_context.py`). Analogous to
`apps.agents.services.base_agent.AgentContext` but procurement-domain specific.
Carries request data, RBAC fields, trace IDs, and a reference to `ProcurementAgentMemory`:

```python
@dataclass
class ProcurementAgentContext:
    procurement_request_id: int
    analysis_run_id: int
    analysis_type: str          # AnalysisRunType value
    domain_code: str
    schema_code: str
    attributes: Dict[str, Any]
    quotation_summaries: List[Dict[str, Any]]
    validation_context: Dict[str, Any]
    constraints: List[str]
    assumptions: List[str]
    rule_result: Dict[str, Any]
    actor_user_id: Optional[int]
    actor_primary_role: str
    actor_roles_snapshot: List[str]
    permission_checked: str
    permission_source: str
    access_granted: bool
    trace_id: str               # from TraceContext.get_current()
    span_id: str
    memory: Optional[ProcurementAgentMemory]
```

#### `ProcurementAgentOrchestrator`
The central bridge class (`apps/procurement/runtime/procurement_agent_orchestrator.py`).
Every AI invocation in procurement now flows through `.run()`:

```python
orchestrator = ProcurementAgentOrchestrator()
result = orchestrator.run(
    run=analysis_run,           # AnalysisRun instance
    agent_type="recommendation",  # string label
    agent_fn=lambda ctx: ...,   # callable receives ProcurementAgentContext
    memory=memory,              # ProcurementAgentMemory
    extra_context={...},        # arbitrary additional fields
    request_user=request.user,  # optional Django user for RBAC snapshot
)
```

`agent_fn` is a zero-knowledge lambda — it wraps the existing agent call without
requiring any agent code changes. The orchestrator constructs a `ProcurementAgentContext`,
passes it to `agent_fn`, then normalises the output.

**What `.run()` does:**
1. Builds `ProcurementAgentContext` (RBAC snapshot, trace IDs from `TraceContext`)
2. Creates a `ProcurementAgentExecutionRecord` DB row (status=RUNNING)
3. Fires `PROCUREMENT_AGENT_RUN_STARTED` audit event via `AuditService`
4. Opens a Langfuse span (fail-silent; sets `procurement_agent` tag)
5. Calls `agent_fn(ctx)` -- existing agent code runs here unchanged
6. Normalises raw output to `Dict[str, Any]`
7. Updates `ProcurementAgentExecutionRecord` to COMPLETED + sets confidence
8. Fires `PROCUREMENT_AGENT_RUN_COMPLETED` audit event
9. Emits `procurement_agent_confidence` score to Langfuse
10. Returns `ProcurementOrchestrationResult`

On any exception: status set to FAILED, `PROCUREMENT_AGENT_RUN_FAILED` audit event fires,
Langfuse span closed with level=ERROR. **Never re-raises** -- business flow continues.

#### `ProcurementOrchestrationResult`
Return type from `.run()`:

```python
@dataclass
class ProcurementOrchestrationResult:
    agent_type: str
    status: str           # "completed" | "failed" | "skipped"
    output: Dict[str, Any]
    confidence: float     # 0.0-1.0
    reasoning_summary: str
    error: str
    duration_ms: int
    execution_record_id: Optional[int]
```

#### `ProcurementAgentExecutionRecord` (Model)
New DB table (`procurement_agent_execution_record`) added to `apps/procurement/models.py`.
One row per agent invocation. Links back to `AnalysisRun` via FK.

Migration: `apps/procurement/migrations/0004_procurementagentexecutionrecord.py`

Key fields:

| Field | Type | Purpose |
|---|---|---|
| `run` | FK(AnalysisRun) | Parent execution run |
| `agent_type` | CharField | e.g. `"recommendation"`, `"benchmark_item_42"` |
| `status` | CharField | `AnalysisRunStatus` value |
| `confidence_score` | FloatField | 0.0-1.0 output confidence |
| `reasoning_summary` | TextField | Human-readable agent summary |
| `input_snapshot` | JSONField | `ctx.to_snapshot()` at call time |
| `output_snapshot` | JSONField | Normalised agent output dict |
| `error_message` | TextField | Error if `status=FAILED` |
| `trace_id` | CharField | Linked to platform TraceContext |
| `actor_user_id` | IntegerField | User who triggered the run |
| `actor_primary_role` | CharField | RBAC role at time of invocation |

### Updated AI Flow (Post Phase 1)

```
RecommendationService.run_recommendation()
  |-- deterministic: _apply_rules(request)
  |-- if rule confidence low:
  |     ProcurementAgentOrchestrator.run(
  |       agent_type="recommendation",
  |       agent_fn=lambda ctx: RecommendationGraphService.run(run)
  |     )
  |       -> ProcurementAgentExecutionRecord created
  |       -> AuditEvent: PROCUREMENT_AGENT_RUN_STARTED
  |       -> Langfuse span: "procurement_recommendation_agent"
  |       -> RecommendationGraphService.run(run)   <-- unchanged
  |       -> AuditEvent: PROCUREMENT_AGENT_RUN_COMPLETED
  |       -> score_trace: procurement_agent_confidence
  |-- _merge_recommendation_result(rule_result, ai_output)

BenchmarkService.run_benchmark()
  |-- for each QuotationLineItem:
  |     ProcurementAgentOrchestrator.run(
  |       agent_type="benchmark_item_{pk}",
  |       agent_fn=lambda ctx: BenchmarkAgent.resolve_benchmark_for_item(item)
  |     )
  |       -> ProcurementAgentExecutionRecord per line
  |       -> memory.benchmark_findings[item_code] = result
  |-- compute variance, classify risk

ValidationOrchestratorService.run_validation()
  |-- 6 deterministic validators run first
  |-- if agent_enabled and ambiguous_count >= 3:
  |     ProcurementAgentOrchestrator.run(
  |       agent_type="validation_augmentation",
  |       agent_fn=lambda ctx: ValidationAgentService.augment_findings(...)
  |     )
  |       -> ProcurementAgentExecutionRecord created (outer)
  |       -> ValidationAgentService creates its own AgentRun internally (unchanged)
  |-- score and classify updated findings
```

### Phase 1 Scope Boundaries

**What Phase 1 DID:**
- Added `ProcurementAgentContext`, `ProcurementAgentMemory`, `ProcurementAgentOrchestrator`
- All three AI entry points route through the orchestrator bridge
- `ProcurementAgentExecutionRecord` DB row per AI invocation
- Standard audit events (`PROCUREMENT_AGENT_RUN_STARTED/COMPLETED/FAILED`)
- Langfuse spans attached to existing trace context
- `request_user` propagated for RBAC snapshot recording
- Extension point stubs: `_ProcurementPlannerStub`, `_ProcurementToolRegistryStub`

**What Phase 1 deliberately did NOT do:**
- Did NOT register procurement agents in shared `AgentRun` / `AgentOrchestrator` tables
- Did NOT rewrite `RecommendationGraphService`, `BenchmarkAgent`, or `ValidationAgentService`
- Did NOT add new tool classes to `apps/tools/registry/`
- Did NOT add `AgentDefinition` catalog rows for procurement agents
- Did NOT change task signatures (callers of `run_analysis_task`, `run_validation_task`)

### Phase 2 Roadmap

| Item | Description |
|---|---|
| Planner integration | Replace `_ProcurementPlannerStub` with a `ReasoningPlanner` call to select which agents to run and in what order |
| Tool registration | Promote stub tool names (`market_benchmark_lookup`, `vendor_catalog_lookup`, `standards_compliance_lookup`, `erp_reference_lookup`) to real `BaseTool` subclasses in `apps/tools/registry/` |
| `AgentDefinition` catalog | Add `AgentDefinition` DB rows for `recommendation`, `benchmark`, `validation_augmentation` so they appear in governance dashboards |
| Shared `AgentRun` convergence | Consider writing a shared `AgentRun` record alongside `ProcurementAgentExecutionRecord` so procurement agents appear in the unified agent trace view |
| ReAct loop | Upgrade `RecommendationAgent` from LangGraph `StateGraph` to the shared `BaseAgent` ReAct loop |
| Memory persistence | Serialise `ProcurementAgentMemory.to_snapshot()` to `AnalysisRun.output_payload_json` for replay |

---

## 3. Data Models

All models are defined in `apps/procurement/models.py`.

### 3.1 ProcurementRequest

The top-level business entity representing a procurement need.

| Field | Type | Notes |
|---|---|---|
| `request_id` | UUID | Auto-generated, unique, indexed |
| `title` | CharField(300) | Human-readable title |
| `description` | TextField | Detailed description of the procurement need |
| `domain_code` | CharField(100) | Business domain (e.g. `HVAC`, `IT`, `FACILITIES`), indexed |
| `schema_code` | CharField(100) | Attribute schema identifier for dynamic forms |
| `request_type` | CharField(20) | `RECOMMENDATION` / `BENCHMARK` / `BOTH` |
| `status` | CharField(20) | `DRAFT` → `READY` → `PROCESSING` → `COMPLETED` / `REVIEW_REQUIRED` / `FAILED` |
| `priority` | CharField(10) | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `geography_country` | CharField(100) | Country context |
| `geography_city` | CharField(100) | City context |
| `currency` | CharField(3) | Default `USD` |
| `assigned_to` | FK → User | Optional assignee |
| `trace_id` | CharField(64) | Distributed tracing correlation |
| `created_by` | FK → User | Inherited from `BaseModel` (via `AuditMixin`) |
| `updated_by` | FK → User | Inherited from `BaseModel` (via `AuditMixin`) |
| `created_at` | DateTimeField | Inherited from `BaseModel` (via `TimestampMixin`) |
| `updated_at` | DateTimeField | Inherited from `BaseModel` (via `TimestampMixin`) |

**Indexes**: `(status, request_type)`, `(domain_code, status)`, `request_id` (unique), `domain_code`

**Inherits**: `BaseModel` → `TimestampMixin` + `AuditMixin`

### 3.2 ProcurementRequestAttribute

Dynamic key-value attributes allowing domain-specific requirements without schema changes.

| Field | Type | Notes |
|---|---|---|
| `request` | FK → ProcurementRequest | CASCADE delete |
| `attribute_code` | CharField(120) | Machine-readable key (e.g. `cooling_capacity`, `budget`) |
| `attribute_label` | CharField(200) | Human-readable label |
| `data_type` | CharField(20) | `TEXT` / `NUMBER` / `BOOLEAN` / `JSON` / `DATE` / `SELECT` |
| `value_text` | TextField | Text value storage |
| `value_number` | Decimal(18,4) | Numeric value storage |
| `value_json` | JSONField | Complex value storage |
| `is_required` | BooleanField | Whether this attribute must be filled before marking READY |
| `normalized_value` | TextField | Normalized/canonical form of the value |

**Unique constraint**: `(request, attribute_code)`

**Inherits**: `TimestampMixin` only (lightweight join table)

### 3.3 SupplierQuotation

Supplier quotation attached to a procurement request. Can link to an uploaded document for extraction.

| Field | Type | Notes |
|---|---|---|
| `request` | FK → ProcurementRequest | CASCADE delete |
| `vendor_name` | CharField(300) | Supplier name, indexed |
| `quotation_number` | CharField(100) | Vendor's quote reference |
| `quotation_date` | DateField | Date on the quotation |
| `total_amount` | Decimal(18,2) | Total quoted amount |
| `currency` | CharField(3) | Default `USD` |
| `uploaded_document` | FK → DocumentUpload | Links to existing document upload system |
| `extraction_status` | CharField(20) | `PENDING` / `IN_PROGRESS` / `COMPLETED` / `FAILED` |
| `extraction_confidence` | FloatField | 0.0–1.0 confidence from extraction |

**Inherits**: `BaseModel`

**Cross-reference**: Links to `apps.documents.DocumentUpload` from the existing document pipeline.

### 3.4 QuotationLineItem

Individual priced line item from a supplier quotation.

| Field | Type | Notes |
|---|---|---|
| `quotation` | FK → SupplierQuotation | CASCADE delete |
| `line_number` | PositiveIntegerField | Sequential line number |
| `description` | TextField | Raw description from quotation |
| `normalized_description` | TextField | Cleaned/normalized description |
| `category_code` | CharField(100) | Product/service category |
| `quantity` | Decimal(14,4) | Default 1 |
| `unit` | CharField(50) | Unit of measure (default `EA`) |
| `unit_rate` | Decimal(18,4) | Per-unit price |
| `total_amount` | Decimal(18,2) | `quantity × unit_rate` |
| `brand` | CharField(200) | Brand name if applicable |
| `model` | CharField(200) | Model number if applicable |
| `extraction_confidence` | FloatField | Per-line extraction confidence |

**Unique constraint**: `(quotation, line_number)`

**Inherits**: `TimestampMixin`

### 3.5 AnalysisRun

A single execution of an analysis. Each `ProcurementRequest` can have multiple runs (re-runs, different types).

| Field | Type | Notes |
|---|---|---|
| `run_id` | UUID | Auto-generated, unique, indexed |
| `request` | FK → ProcurementRequest | CASCADE delete |
| `run_type` | CharField(20) | `RECOMMENDATION` / `BENCHMARK` |
| `status` | CharField(20) | `QUEUED` → `RUNNING` → `COMPLETED` / `FAILED` |
| `started_at` | DateTimeField | When execution began |
| `completed_at` | DateTimeField | When execution finished |
| `triggered_by` | FK → User | Who initiated the run |
| `input_snapshot_json` | JSONField | Frozen copy of request attributes at run time |
| `output_summary` | TextField | Human-readable summary of results |
| `confidence_score` | FloatField | Overall confidence (0.0–1.0) |
| `trace_id` | CharField(64) | Distributed tracing correlation |
| `error_message` | TextField | Error details if FAILED |

**Computed property**: `duration_ms` — calculated from `started_at` / `completed_at`

**Index**: `(request, run_type, status)`

**Inherits**: `BaseModel`

### 3.6 RecommendationResult

Output of a recommendation analysis run (1:1 with AnalysisRun).

| Field | Type | Notes |
|---|---|---|
| `run` | OneToOne → AnalysisRun | CASCADE delete |
| `recommended_option` | CharField(500) | The recommended product/solution |
| `reasoning_summary` | TextField | Plain-text explanation |
| `reasoning_details_json` | JSONField | Structured reasoning (source, rules evaluated, etc.) |
| `confidence_score` | FloatField | Recommendation confidence |
| `constraints_json` | JSONField | Constraints considered |
| `compliance_status` | CharField(20) | `PASS` / `FAIL` / `PARTIAL` / `NOT_CHECKED` |
| `output_payload_json` | JSONField | Full structured output from rules + AI |

**Inherits**: `TimestampMixin`

### 3.7 BenchmarkResult

Header-level benchmark output per quotation. One record per `(run, quotation)` pair.

**DB table**: `procurement_benchmark_result`
**Inherits**: `TimestampMixin`
**Unique constraint**: `(run, quotation)`

| Field | Type | Notes |
|---|---|---------|
| `run` | FK -> AnalysisRun | CASCADE delete; `related_name="benchmark_results"` |
| `quotation` | FK -> SupplierQuotation | CASCADE delete; `related_name="benchmark_results"` |
| `total_quoted_amount` | Decimal(18,2) | Sum of all `QuotationLineItem.total_amount` values |
| `total_benchmark_amount` | Decimal(18,2) | Sum of `benchmark_avg * quantity` per line |
| `variance_pct` | Decimal(8,2) | `(total_quoted - total_benchmark) / total_benchmark * 100` |
| `risk_level` | CharField(20) | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`; default `LOW` |
| `summary_json` | JSONField | Snapshot: `{line_count, total_quoted, total_benchmark, variance_pct}` as strings |

**`summary_json` structure**:
```json
{
  "line_count": 4,
  "total_quoted": "124500.00",
  "total_benchmark": "112000.00",
  "variance_pct": "11.16"
}
```

### 3.8 BenchmarkResultLine

Per-line-item benchmark comparison. One record per `QuotationLineItem` inside a benchmark run.

**DB table**: `procurement_benchmark_result_line`
**Inherits**: `TimestampMixin`
**Ordering**: `[quotation_line__line_number]`

| Field | Type | Notes |
|---|---|---|
| `benchmark_result` | FK -> BenchmarkResult | CASCADE delete; `related_name="lines"` |
| `quotation_line` | FK -> QuotationLineItem | CASCADE delete; `related_name="benchmark_lines"` |
| `benchmark_min` | Decimal(18,4) | Market minimum price (null when source has no data) |
| `benchmark_avg` | Decimal(18,4) | Market average price used for variance calculation |
| `benchmark_max` | Decimal(18,4) | Market maximum price |
| `quoted_value` | Decimal(18,4) | `QuotationLineItem.unit_rate` (copy at time of run) |
| `variance_pct` | Decimal(8,2) | `(quoted_value - benchmark_avg) / benchmark_avg * 100`; null when no benchmark |
| `variance_status` | CharField(30) | `WITHIN_RANGE` / `ABOVE_BENCHMARK` / `BELOW_BENCHMARK` / `SIGNIFICANTLY_ABOVE` |
| `remarks` | TextField | Source-specific notes (e.g. `"No benchmark data available"`, `"DuckDuckGo IA results used."`) |

### 3.9 ComplianceResult

Compliance check output attached to an analysis run (1:1).

| Field | Type | Notes |
|---|---|---|
| `run` | OneToOne → AnalysisRun | CASCADE delete |
| `compliance_status` | CharField(20) | `PASS` / `FAIL` / `PARTIAL` / `NOT_CHECKED` |
| `rules_checked_json` | JSONField | List of `{rule, description}` dicts |
| `violations_json` | JSONField | List of `{rule, detail}` dicts |
| `recommendations_json` | JSONField | List of remediation suggestions |

**Inherits**: `TimestampMixin`

### 3.10 ValidationRuleSet

Reusable set of validation rules scoped to a domain and/or schema.

| Field | Type | Notes |
|---|---|---|
| `domain_code` | CharField(100) | Business domain (blank = generic / all domains), indexed |
| `schema_code` | CharField(100) | Attribute schema identifier (blank = all schemas) |
| `rule_set_code` | CharField(120) | Unique identifier, indexed |
| `rule_set_name` | CharField(300) | Human-readable name |
| `description` | TextField | Optional description |
| `validation_type` | CharField(40) | `ATTRIBUTE_COMPLETENESS` / `DOCUMENT_COMPLETENESS` / `SCOPE_COVERAGE` / `AMBIGUITY_CHECK` / `COMMERCIAL_COMPLETENESS` / `COMPLIANCE_READINESS` |
| `is_active` | BooleanField | Default `True`, indexed |
| `priority` | PositiveIntegerField | Ordering priority (lower = higher priority), default 100 |
| `config_json` | JSONField | Domain-specific config (expected docs, categories, commercial terms) |

**Indexes**: `(domain_code, validation_type, is_active)`

**Inherits**: `BaseModel`

### 3.11 ValidationRule

Individual validation rule within a rule set.

| Field | Type | Notes |
|---|---|---|
| `rule_set` | FK → ValidationRuleSet | CASCADE delete |
| `rule_code` | CharField(120) | Code within its set, indexed |
| `rule_name` | CharField(300) | Human-readable name |
| `rule_type` | CharField(30) | `REQUIRED_ATTRIBUTE` / `REQUIRED_DOCUMENT` / `REQUIRED_CATEGORY` / `AMBIGUITY_PATTERN` / `COMMERCIAL_CHECK` / `COMPLIANCE_CHECK` |
| `severity` | CharField(20) | `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `is_active` | BooleanField | Default `True` |
| `evaluation_mode` | CharField(20) | `DETERMINISTIC` / `AGENT_ASSISTED` |
| `condition_json` | JSONField | Evaluation conditions (attribute_code, pattern, etc.) |
| `expected_value_json` | JSONField | Expected value or pattern for comparison |
| `failure_message` | CharField(500) | Message shown on rule failure |
| `remediation_hint` | CharField(500) | Suggested fix |
| `display_order` | PositiveIntegerField | Display ordering |

**Unique constraint**: `(rule_set, rule_code)`

**Inherits**: `TimestampMixin`

### 3.12 ValidationResult

Top-level output of a validation run (1:1 with AnalysisRun).

| Field | Type | Notes |
|---|---|---|
| `run` | OneToOne → AnalysisRun | CASCADE delete |
| `validation_type` | CharField(40) | Primary validation type (default: `ATTRIBUTE_COMPLETENESS` for combined runs) |
| `overall_status` | CharField(30) | `PASS` / `PASS_WITH_WARNINGS` / `REVIEW_REQUIRED` / `FAIL` |
| `completeness_score` | FloatField | 0–100 percentage |
| `summary_text` | TextField | Human-readable summary |
| `readiness_for_recommendation` | BooleanField | Whether request is ready for recommendation analysis |
| `readiness_for_benchmarking` | BooleanField | Whether request is ready for benchmark analysis |
| `recommended_next_action` | CharField(40) | `READY_FOR_RECOMMENDATION` / `READY_FOR_BENCHMARKING` / `REQUEST_REFINEMENT` / `NEEDS_TECHNICAL_REVIEW` / `NEEDS_COMMERCIAL_REVIEW` |
| `missing_items_json` | JSONField | List of `{item_code, item_label, severity, remarks}` |
| `warnings_json` | JSONField | List of `{item_code, item_label, severity, remarks}` |
| `ambiguous_items_json` | JSONField | List of `{item_code, item_label, remarks}` |
| `output_payload_json` | JSONField | Full structured output for API consumers |

**Inherits**: `TimestampMixin`

### 3.13 ValidationResultItem

Individual finding within a validation result.

| Field | Type | Notes |
|---|---|---|
| `validation_result` | FK → ValidationResult | CASCADE delete |
| `item_code` | CharField(120) | Finding identifier |
| `item_label` | CharField(300) | Human-readable label |
| `category` | CharField(40) | Which validation dimension (uses `ValidationType` choices) |
| `status` | CharField(20) | `PRESENT` / `MISSING` / `WARNING` / `AMBIGUOUS` / `FAILED` |
| `severity` | CharField(20) | `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `source_type` | CharField(20) | `ATTRIBUTE` / `DOCUMENT` / `LINE_ITEM` / `RULE` / `AGENT` |
| `source_reference` | CharField(200) | Rule code, attribute code, or document reference |
| `remarks` | TextField | Human-readable notes |
| `details_json` | JSONField | Structured details |

**Inherits**: `TimestampMixin`

### Entity Relationship Diagram

```
User (accounts.User)
  ├── creates ──> ProcurementRequest (created_by)
  ├── assigned ──> ProcurementRequest (assigned_to)
  └── triggers ──> AnalysisRun (triggered_by)

ProcurementRequest
  ├── ──< ProcurementRequestAttribute (attributes)
  ├── ──< SupplierQuotation (quotations)
  │         └── ──< QuotationLineItem (line_items)
  └── ──< AnalysisRun (analysis_runs)
            ├── ── RecommendationResult (1:1, recommendation_result)
            ├── ──< BenchmarkResult (benchmark_results)
            │         └── ──< BenchmarkResultLine (lines)
            ├── ── ComplianceResult (1:1, compliance_result)
            └── ── ValidationResult (1:1, validation_result)
                      └── ──< ValidationResultItem (items)

ValidationRuleSet
  └── ──< ValidationRule (rules)

SupplierQuotation ── FK ──> DocumentUpload (existing documents app)

-- Phase 1 addition --
AnalysisRun ──< ProcurementAgentExecutionRecord (agent_execution_records)
```

### 3.14 ProcurementAgentExecutionRecord (Phase 1)

Added in v2.0. One row per AI agent invocation within an `AnalysisRun`.
Written by `ProcurementAgentOrchestrator` -- never written directly by service code.

| Field | Type | Notes |
|---|---|---|
| `run` | FK(AnalysisRun) | CASCADE delete |
| `agent_type` | CharField(100) | e.g. `"recommendation"`, `"benchmark_item_42"` |
| `status` | CharField(20) | `AnalysisRunStatus` value |
| `started_at` | DateTimeField | Set on create (`auto_now_add`) |
| `completed_at` | DateTimeField(null) | Set when orchestrator receives output |
| `confidence_score` | FloatField(null) | 0.0-1.0 agent-reported confidence |
| `reasoning_summary` | TextField | Short text summary from agent output |
| `input_snapshot` | JSONField(null) | `ctx.to_snapshot()` at call time |
| `output_snapshot` | JSONField(null) | Normalised agent output dict |
| `error_message` | TextField | Exception message if `status=FAILED` |
| `trace_id` | CharField(64) | From `TraceContext` at invocation time |
| `span_id` | CharField(64) | From `TraceContext` |
| `actor_user_id` | IntegerField(null) | User who triggered the run |
| `actor_primary_role` | CharField(100) | Primary RBAC role at invocation |

**Inherits**: `TimestampMixin`  
**DB table**: `procurement_agent_execution_record`  
**Indexes**: `[run, agent_type]`, `[status, started_at]`, `[trace_id]`

### 3.15 MarketIntelligenceSuggestion

Stores AI-generated market intelligence (product suggestions) for a procurement request. Generated via `MarketIntelligenceService` using Perplexity sonar or Azure OpenAI.

| Field | Type | Notes |
|---|---|---|
| `request` | FK(ProcurementRequest) | CASCADE delete |
| `query_text` | TextField | The query sent to the AI provider |
| `suggestions_json` | JSONField | List of suggestion dicts (see below) |
| `source_provider` | CharField(50) | `"perplexity"` or `"openai"` |
| `model_used` | CharField(100) | e.g. `"sonar"`, `"gpt-4o"` |
| `generation_time_seconds` | FloatField(null) | Wall-clock time for the AI call |
| `error_message` | TextField | Exception message if generation failed |
| `trace_id` | CharField(64) | From `TraceContext` at call time |
| `created_by` | FK(User) | Inherited via `AuditMixin` |
| `created_at` | DateTimeField | Inherited via `TimestampMixin` |

**`suggestions_json` item structure** (stored per suggestion after LLM post-processing):

| Key | Type | Description |
|---|---|---|
| `rank` | int | Rank by fit_score descending (1 = best) |
| `product_name` | string | Full product/series name (e.g. `"Daikin VRV-X Series RXYQ48T"`) |
| `manufacturer` | string | Brand name (e.g. `"Daikin"`) |
| `model_code` | string | Specific model or series code |
| `system_type` | string | HVAC system type locked to the request (e.g. `"VRF System"`) |
| `cooling_capacity` | string | Rated cooling range (e.g. `"8 TR - 12 TR"`) |
| `cop_eer` | string | Efficiency rating string (e.g. `"COP 3.8 / EER 13.0"`) |
| `price_range_aed` | string | Price retrieved from approved source, or `"Contact distributor for pricing"` |
| `market_availability` | string | Regional availability note |
| `key_benefits` | list[string] | Up to 3 benefit bullet points |
| `limitations` | list[string] | Up to 2 limitation bullet points |
| `fit_score` | int | 0-100 fitness score (clamped by post-processing) |
| `fit_rationale` | string | One sentence explaining fit |
| `standards_compliance` | list[string] | Applicable standards (e.g. `["ASHRAE 90.1", "ESMA UAE"]`) |
| `citation_index` | int | 0-based index into Perplexity `citations[]` array (LLM-generated; used during Perplexity path only) |
| `citation_source` | string | Human-readable source name (e.g. `"Daikin Middle East"`) |
| `price_citation_index` | int | 0-based index of price page in Perplexity `citations[]`; -1 if not found |
| `category` | string | `"MANUFACTURER"` or `"DISTRIBUTOR"` |
| `citation_url` | string | Resolved product page URL (from `perplexity_citations[citation_index]` on Perplexity path; LLM string on OpenAI path) |
| `brand_page_url` | string | Stable registry landing page URL (resolved from `ExternalSourceRegistry.source_url` by domain matching) |
| `price_source_url` | string | Price page URL resolved from `perplexity_citations[price_citation_index]`; empty string if index is -1 |
| `icon_class` | string | Bootstrap icon class added by post-processing (e.g. `"bi-building"`) |

**Additional model fields** (not in old docs — added in v2.0):

| Field | Type | Notes |
|---|---|---|
| `rephrased_query` | TextField | One-sentence query rephrased by the LLM |
| `ai_summary` | TextField | 2-3 sentence executive summary of market context |
| `market_context` | TextField | Brief note on availability, lead times, pricing trends |
| `system_code` | CharField(100) | Enum-style system code (e.g. `"VRF"`, `"CHILLER"`) |
| `system_name` | CharField(200) | Human-readable system name (e.g. `"VRF System"`) |
| `suggestions_json` | JSONField | List of normalised suggestion dicts (see schema above) |
| `suggestion_count` | IntegerField | Count of suggestions in `suggestions_json` |
| `perplexity_citations_json` | JSONField | Raw `citations[]` list returned by Perplexity API - real URLs Perplexity fetched during live search |
| `generated_by` | FK(User, null) | User who triggered generation; null for background/seed |

**Inherits**: `BaseModel` (TimestampMixin + AuditMixin)  
**DB table**: `procurement_market_intelligence_suggestion`  
**Indexes**: `[request, created_at]`

### 3.16 ExternalSourceRegistry

Allow-list of approved external sources for HVAC product discovery. Controls which web sources the AI discovery agent is permitted to query. Used to build the Perplexity `search_domain_filter`, to provide `brand_page_url` fallback URLs (via `source_url` per domain), and to sort sources by class priority in the prompt.

| Field | Type | Notes |
|---|---|---|
| `source_name` | CharField(200) | Display name, e.g. `"Daikin MEA Official"` |
| `domain` | CharField(300) | Root domain, e.g. `"daikinmea.com"` |
| `source_url` | URLField(500) | Direct product-page URL for this source |
| `source_type` | CharField(40) | `ExternalSourceClass` enum: `OEM_OFFICIAL`, `OEM_REGIONAL`, `AUTHORIZED_DISTRIBUTOR`, `RETAILER`, `STANDARDS_BODY`, `OTHER` |
| `country_scope` | JSONField | List of country codes this source covers, e.g. `["UAE", "KSA"]` |
| `priority` | PositiveIntegerField | Lower = higher priority; OEM_OFFICIAL sources come first in the prompt |
| `trust_score` | FloatField(0.0-1.0) | Trust score used in candidate ranking (default `0.8`) |
| `hvac_system_type` | CharField(40) | HVAC system type this source covers, e.g. `"VRF System"`, `"Split AC"`, `"Ducting & Accessories"`. Blank = all types. |
| `equipment` | CharField(200) | Equipment sub-type within the system, e.g. `"VRF Outdoor Unit"`, `"FCU / AHU Units"`. Auto-derived from `notes` field by `seed_configurations` if not explicitly set. |
| `allowed_for_discovery` | BooleanField | If `True`, AI discovery agent may search this source (default `True`) |
| `allowed_for_compliance` | BooleanField | If `True`, source may be cited as compliance/regulatory evidence (default `False`) |
| `fetch_mode` | CharField(10) | `PAGE` / `PDF` / `API` (default `PAGE`) |
| `notes` | TextField | Optional admin notes; first segment (before ` -- `) is auto-used as `equipment` value |
| `is_active` | BooleanField | Whether this source is included at all (default `True`) |
| `created_at` | DateTimeField | Inherited via `BaseModel` |
| `updated_at` | DateTimeField | Inherited via `BaseModel` |

**`_SYSTEM_CODE_TO_DB_NAME` mapping** (in `market_intelligence_service.py`): Because `ExternalSourceRegistry.hvac_system_type` stores free-text names (e.g. `"VRF System"`) while the recommendation engine uses enum-style codes (e.g. `"VRF"`), the service uses this dict to translate before querying:

```python
_SYSTEM_CODE_TO_DB_NAME: dict[str, str] = {
    "SPLIT_AC":    "Split AC",
    "VRF":         "VRF System",
    "PACKAGED_DX": "Packaged Unit (Rooftop)",
    "CHILLER":     "Chilled Water System",
    "FCU":         "Chilled Water System",
    "AHU":         "Chilled Water System",
    "DUCTING":     "Ducting & Accessories",
}
```

**Seeding**: Records are seeded by `python manage.py seed_configurations`. The command populates sources for Split AC, VRF, Chilled Water, Packaged Unit, Ducting, and related categories. As of the current seed, 39+ records are loaded including Amazon UAE (Diffusers / Grills), DesertCart entries for Split AC, Diffusers, and Dampers / Louvers.

**Inherits**: `BaseModel` (TimestampMixin + AuditMixin)  
**DB table**: `procurement_external_source_registry`  
**Ordering**: `[priority, source_name]`

---

## 4. Business Enumerations

All procurement enums are defined in `apps/core/enums.py` (following existing project convention).

### ProcurementRequestType
| Value | Label |
|---|---|
| `RECOMMENDATION` | Product / Solution Recommendation |
| `BENCHMARK` | Should-Cost Benchmarking |
| `BOTH` | Recommendation + Benchmarking |

### ProcurementRequestStatus
| Value | Label |
|---|---|
| `DRAFT` | Draft — initial creation, attributes being defined |
| `READY` | Ready — attributes validated, ready for analysis |
| `PROCESSING` | Processing — analysis run in progress |
| `COMPLETED` | Completed — analysis finished successfully |
| `REVIEW_REQUIRED` | Review Required — compliance failure or high-risk benchmark |
| `FAILED` | Failed — analysis run failed |

### AnalysisRunType
| Value | Label |
|---|---|
| `RECOMMENDATION` | Recommendation analysis |
| `BENCHMARK` | Benchmark analysis |
| `VALIDATION` | Validation analysis |

### AnalysisRunStatus
| Value | Label |
|---|---|
| `QUEUED` | Queued — waiting for execution |
| `RUNNING` | Running — currently executing |
| `COMPLETED` | Completed — finished successfully |
| `FAILED` | Failed — execution error |

### ExtractionStatus
| Value | Label |
|---|---|
| `PENDING` | Pending |
| `IN_PROGRESS` | In Progress |
| `COMPLETED` | Completed |
| `FAILED` | Failed |

### ValidationType
| Value | Label |
|---|---|
| `ATTRIBUTE_COMPLETENESS` | Attribute Completeness |
| `DOCUMENT_COMPLETENESS` | Document Completeness |
| `SCOPE_COVERAGE` | Scope Coverage |
| `AMBIGUITY_CHECK` | Ambiguity Check |
| `COMMERCIAL_COMPLETENESS` | Commercial Completeness |
| `COMPLIANCE_READINESS` | Compliance Readiness |

### ValidationOverallStatus
| Value | Label |
|---|---|
| `PASS` | Pass |
| `PASS_WITH_WARNINGS` | Pass with Warnings |
| `REVIEW_REQUIRED` | Review Required |
| `FAIL` | Fail |

### ValidationRuleType
| Value | Label |
|---|---|
| `REQUIRED_ATTRIBUTE` | Required Attribute |
| `REQUIRED_DOCUMENT` | Required Document |
| `REQUIRED_CATEGORY` | Required Category |
| `AMBIGUITY_PATTERN` | Ambiguity Pattern |
| `COMMERCIAL_CHECK` | Commercial Check |
| `COMPLIANCE_CHECK` | Compliance Check |

### ValidationSeverity
| Value | Label |
|---|---|
| `INFO` | Info |
| `WARNING` | Warning |
| `ERROR` | Error |
| `CRITICAL` | Critical |

### ValidationEvaluationMode
| Value | Label |
|---|---|
| `DETERMINISTIC` | Deterministic |
| `AGENT_ASSISTED` | Agent-Assisted |

### ValidationItemStatus
| Value | Label |
|---|---|
| `PRESENT` | Present |
| `MISSING` | Missing |
| `WARNING` | Warning |
| `AMBIGUOUS` | Ambiguous |
| `FAILED` | Failed |

### ValidationSourceType
| Value | Label |
|---|---|
| `ATTRIBUTE` | Attribute |
| `DOCUMENT` | Document |
| `LINE_ITEM` | Line Item |
| `RULE` | Rule |
| `AGENT` | Agent |

### ValidationNextAction
| Value | Label |
|---|---|
| `READY_FOR_RECOMMENDATION` | Ready for Recommendation |
| `READY_FOR_BENCHMARKING` | Ready for Benchmarking |
| `REQUEST_REFINEMENT` | Request Refinement |
| `NEEDS_TECHNICAL_REVIEW` | Needs Technical Review |
| `NEEDS_COMMERCIAL_REVIEW` | Needs Commercial Review |

### ComplianceStatus
| Value | Label |
|---|---|
| `PASS` | Pass — all rules satisfied |
| `FAIL` | Fail — critical violations |
| `PARTIAL` | Partial — some violations (non-critical) |
| `NOT_CHECKED` | Not Checked — compliance not evaluated |

### VarianceStatus
| Value | Label |
|---|---|
| `WITHIN_RANGE` | Within Range |
| `ABOVE_BENCHMARK` | Above Benchmark |
| `BELOW_BENCHMARK` | Below Benchmark |
| `SIGNIFICANTLY_ABOVE` | Significantly Above (>30%) |

### BenchmarkRiskLevel
| Value | Label | Variance threshold |
|---|---|---|
| `LOW` | Low | abs(variance) <= 5% |
| `MEDIUM` | Medium | 5% < abs(variance) <= 15% |
| `HIGH` | High | 15% < abs(variance) <= 30% |
| `CRITICAL` | Critical | abs(variance) > 30% |

### AttributeDataType
| Value | Label |
|---|---|
| `TEXT` | Text |
| `NUMBER` | Number |
| `BOOLEAN` | Boolean |
| `JSON` | JSON |
| `DATE` | Date |
| `SELECT` | Select (dropdown) |

---

## 5. Service Layer

All services are in `apps/procurement/services/`. They follow existing project conventions:
- Stateless classes with static/class methods
- Called by views/tasks (never directly from serializers)
- Each service method logs via existing `AuditService`
- Entry-point methods decorated with `@observed_service` for tracing

### 5.1 ProcurementRequestService

**File**: `apps/procurement/services/request_service.py`

| Method | Description |
|---|---|
| `create_request(...)` | Creates a `ProcurementRequest` with optional attributes. Logs `PROCUREMENT_REQUEST_CREATED` audit event. Decorated with `@observed_service`. |
| `update_status(request, new_status, user)` | Transitions request status. Logs `PROCUREMENT_REQUEST_STATUS_CHANGED` audit event with `status_before` / `status_after`. |
| `mark_ready(request, user)` | Validates all `is_required` attributes have values, then transitions status to `READY`. Raises `ValueError` if validation fails. |
| `get_request(request_id)` | Fetches by PK or UUID. |

### 5.2 AttributeService

**File**: `apps/procurement/services/request_service.py` (same file)

| Method | Description |
|---|---|
| `bulk_set_attributes(request, attributes)` | Upserts attributes (update-or-create by `attribute_code`). |
| `get_attributes_dict(request)` | Returns attributes as `{code: value}` dict with type-aware extraction (number/json/text). |

### 5.3 QuotationService

**File**: `apps/procurement/services/quotation_service.py`

| Method | Description |
|---|---|
| `create_quotation(...)` | Creates a `SupplierQuotation` linked to a request. Logs `QUOTATION_UPLOADED` audit event. |
| `add_line_items(quotation, items)` | Bulk-creates `QuotationLineItem` records from a list of dicts. |
| `update_extraction_status(quotation, status, confidence)` | Updates extraction pipeline status on quotation. |

### 5.4 LineItemNormalizationService

**File**: `apps/procurement/services/quotation_service.py` (same file)

| Method | Description |
|---|---|
| `normalize_line_items(quotation)` | Normalizes all line item descriptions (lowercase, strip, collapse whitespace). Returns count of items normalized. |
| `_normalize_description(description)` | Internal: basic text normalization. Extension point for domain-specific normalization. |

### 5.5 Quotation Document Prefill Pipeline

The prefill pipeline extracts structured data from uploaded supplier proposals/quotation PDFs using OCR + LLM.

#### Architecture

```
Quotation Upload (API: quotation_prefill)
    │
    ├─ Create DocumentUpload + SupplierQuotation (PENDING)
    ├─ Queue run_quotation_prefill_task
    │
    ▼
QuotationDocumentPrefillService.run_prefill(quotation)
    │
    ├─ Step 1: OCR (Azure Document Intelligence via InvoiceExtractionAdapter)
    ├─ Step 2: LLM Extraction (GPT-4o, up to 60K chars of OCR text)
    │          └─ System prompt requires JSON: header fields + line_items[] + commercial terms
    ├─ Step 3: Field Mapping (AttributeMappingService.map_quotation_fields)
    │          ├─ Header: vendor_name, quotation_number, quotation_date, total_amount, currency, subtotal
    │          ├─ Commercial: warranty_terms, payment_terms, delivery_terms, lead_time, etc.
    │          └─ Line Items: description, category_code, quantity, unit, unit_rate, total_amount, brand, model
    ├─ Step 4: Confidence Classification (high/low per field)
    ├─ Step 5: Store prefill_payload_json on quotation (status → REVIEW_PENDING)
    │
    ▼
User reviews extracted data in UI
    │
    ▼
PrefillReviewService.confirm_quotation_prefill(quotation, reviewed_data)
    │
    ├─ Persist header fields on SupplierQuotation
    ├─ Bulk-create QuotationLineItem records from confirmed line items
    └─ Set prefill_status → COMPLETED
```

#### Key Services

**File**: `apps/procurement/services/prefill/quotation_prefill_service.py`

| Method | Description |
|---|---|
| `run_prefill(quotation)` | Full pipeline: OCR → LLM → mapping → payload storage. Accepts up to 60K chars of OCR text to handle long proposals. |
| `_ocr_document(file_path)` | Delegates to `InvoiceExtractionAdapter._ocr_document()` (Azure Document Intelligence). |
| `_extract_quotation_data(ocr_text)` | LLM extraction with `max_tokens=8192`. Strips markdown code fences from response. |

**File**: `apps/procurement/services/prefill/attribute_mapping_service.py`

| Method | Description |
|---|---|
| `map_quotation_fields(extracted)` | Maps LLM output to canonical header fields, commercial terms, and line items via synonym dictionaries. |
| `classify_confidence(fields)` | Separates fields into high_confidence (≥0.7) and low_confidence (<0.7) groups. |

**File**: `apps/procurement/services/prefill/prefill_review_service.py`

| Method | Description |
|---|---|
| `confirm_quotation_prefill(quotation, reviewed_data)` | Atomic: updates header fields + bulk-creates `QuotationLineItem` records from user-confirmed data. |

**File**: `apps/procurement/services/prefill/prefill_status_service.py`

| Method | Description |
|---|---|
| `mark_quotation_in_progress(quotation)` | Sets `prefill_status` → `IN_PROGRESS`. |
| `mark_quotation_completed(quotation, confidence, payload)` | Sets `prefill_status` → `REVIEW_PENDING`, stores `prefill_payload_json`. |
| `mark_quotation_failed(quotation)` | Sets `prefill_status` → `FAILED`. |

**File**: `apps/procurement/agents/quotation_extraction_agent.py`

| Method | Description |
|---|---|
| `extract(ocr_text)` | Single-shot LLM call with structured JSON prompt. Extracts header + line items from OCR text (up to 60K chars). |

#### Important Notes

- **OCR text limit**: 60,000 characters (sufficient for 40+ page proposals). Long technical proposals often have pricing/licensing tables deep in the document.
- **Two-phase persistence**: Extraction stores data as JSON in `prefill_payload_json` (phase 1). Line items are NOT persisted to `QuotationLineItem` table until the user confirms (phase 2). This allows human review before commitment.
- **Line item sources**: The LLM is instructed to find line items in pricing tables, BOQ sections, licensing tables, cost breakdowns, and commercial schedules anywhere in the document.

### 5.6 AnalysisRunService

**File**: `apps/procurement/services/analysis_run_service.py`

Manages the full lifecycle of an `AnalysisRun`.

| Method | Description |
|---|---|
| `create_run(request, run_type, triggered_by)` | Creates run with `QUEUED` status, captures `input_snapshot_json` (request attributes frozen at creation time). Logs `ANALYSIS_RUN_CREATED`. |
| `start_run(run)` | Sets status to `RUNNING`, records `started_at`. Logs `ANALYSIS_RUN_STARTED`. |
| `complete_run(run, output_summary, confidence_score)` | Sets status to `COMPLETED`, records `completed_at`, summary, confidence. Logs `ANALYSIS_RUN_COMPLETED` with output snapshot. |
| `fail_run(run, error_message)` | Sets status to `FAILED`, records error. Logs `ANALYSIS_RUN_FAILED`. |

### 5.7 RecommendationService

**File**: `apps/procurement/services/recommendation_service.py`

Orchestrates the full recommendation flow. Decorated with `@observed_service`.

**`run_recommendation(request, run, use_ai=True)`** — steps:

1. **Start run** — calls `AnalysisRunService.start_run()`
2. **Gather attributes** — calls `AttributeService.get_attributes_dict()`
3. **Apply deterministic rules** — calls `_apply_rules()` (returns `{recommended_option, reasoning_summary, confident, constraints}`)
4. **Invoke AI** — if `use_ai=True` AND rules returned `confident=False`, calls `RecommendationAgent.execute()`
5. **Compliance check** — calls `ComplianceService.check_recommendation()` to validate the recommendation
6. **Persist result** — creates `RecommendationResult` + `ComplianceResult` in a transaction
7. **Finalize** — calls `AnalysisRunService.complete_run()`, updates request status to `COMPLETED` or `REVIEW_REQUIRED` (if compliance fails)

**Error path**: On exception, calls `AnalysisRunService.fail_run()` and sets request to `FAILED`.

**Extension point**: `_apply_rules()` is a static method that can be extended per domain with deterministic recommendation logic.

### 5.8 BenchmarkService

**File**: `apps/procurement/services/benchmark_service.py`

Orchestrates the complete should-cost benchmarking pipeline. Decorated with `@observed_service("procurement.benchmark.run", audit_event="BENCHMARK_RUN_STARTED")`.

#### `run_benchmark(request, run, quotation, *, use_ai=True, request_user=None) -> BenchmarkResult`

Full 5-step orchestration:

1. **Start run** -- `AnalysisRunService.start_run(run)`; initialises a `ProcurementAgentMemory()` instance shared across all per-line agent calls.
2. **Per-line resolution + variance** -- for each `QuotationLineItem` on the quotation:
   - calls `_resolve_benchmark(item, run, memory, use_ai, request_user)` to get a corridor dict
   - calls `_compute_variance(item, benchmark_data)` to compute `variance_pct` and `variance_status`
3. **Aggregate** -- sums `total_quoted` and `total_benchmark` across all lines; computes `overall_variance_pct`
4. **Persist (atomic transaction)**:
   - `BenchmarkResult.objects.create(...)` -- one header record
   - `BenchmarkResultLine.objects.bulk_create(lines)` -- one row per line item
5. **Finalise** -- `AnalysisRunService.complete_run(run, output_summary, confidence_score=0.8)`; updates request status: `COMPLETED` when `risk_level in (LOW, MEDIUM)`, `REVIEW_REQUIRED` otherwise

#### `_resolve_benchmark(item, *, run, memory, use_ai, request_user) -> dict`

Three-tier benchmark resolution with explicit priority order:

```
Priority 1: Internal catalogue DB  (Phase 2 extension point -- stub in Phase 1)
Priority 2: BenchmarkAgent via ProcurementAgentOrchestrator  (LLM, use_ai=True)
Priority 3: WebSearchService.search_benchmark()  (DuckDuckGo -> Bing scrape)
Priority 4: No-data fallback  ({min: None, avg: None, max: None, source: "none"})
```

**Priority 1 -- Internal catalogue** (Phase 2 extension point):
```python
# Phase 2 stub -- add deterministic catalogue lookup here:
# result = BenchmarkCatalogueService.lookup(item)
# if result: return result
```

**Priority 2 -- AI via orchestrator** (when `use_ai=True` and `run` is provided):
```python
orchestrator = ProcurementAgentOrchestrator()
def _agent_fn(ctx):
    return BenchmarkAgent.resolve_benchmark_for_item(item)
orch_result = orchestrator.run(
    run=run,
    agent_type=f"benchmark_item_{item.pk}",   # unique per line item
    agent_fn=_agent_fn,
    memory=memory,
    extra_context={"line_item_pk": item.pk, "description": item.description},
    request_user=request_user,
)
if orch_result.status == "completed" and orch_result.output:
    memory.benchmark_findings[item.description[:80]] = orch_result.output
    return orch_result.output
```
The orchestrator creates `AgentRun` + `AgentStep` records for every call, giving full audit trail. The shared `ProcurementAgentMemory` accumulates results across all line items in a single run so any cross-line reasoning is available.

**Priority 3 -- WebSearchService** (when AI fails or `use_ai=False`):
```python
from apps.procurement.services.web_search_service import WebSearchService
ws_result = WebSearchService.search_benchmark(
    description=item.description,
    geography=request.geography_country or "UAE",
    uom=item.uom,
    currency=item.currency or "AED",
)
if ws_result.get("avg") is not None:
    return ws_result  # source="WEB_SEARCH", confidence=0.35
```

Priority 3 is **non-blocking** -- it is wrapped in `try/except` and falls through to Priority 4 on failure.

#### `_compute_variance(item, benchmark) -> dict`

```python
pct = ((quoted - avg) / avg) * 100
```

| `variance_pct` | `variance_status` |
|---|---|
| `avg` is None or 0 | `WITHIN_RANGE` (no data) |
| pct > 30% | `SIGNIFICANTLY_ABOVE` |
| pct > 0% | `ABOVE_BENCHMARK` |
| pct < -30% | `BELOW_BENCHMARK` |
| otherwise | `WITHIN_RANGE` |

Note: `BELOW_BENCHMARK` is currently only triggered when `pct < -30%` (no separate "significantly below" variant).

#### `_classify_risk(variance_pct) -> str`

Operates on **absolute value** of aggregate variance:

```python
RISK_THRESHOLDS = {
    "low":    Decimal("5.0"),   # abs_var <= 5%  -> LOW
    "medium": Decimal("15.0"),  # abs_var <= 15% -> MEDIUM
    "high":   Decimal("30.0"),  # abs_var <= 30% -> HIGH
                                # abs_var >  30% -> CRITICAL
}
```

**Request status after benchmark**:
- `risk_level in (LOW, MEDIUM)` -> `COMPLETED`
- `risk_level in (HIGH, CRITICAL)` -> `REVIEW_REQUIRED`

#### WebSearchService (`apps/procurement/services/web_search_service.py`)

Standalone utility used as the last-resort fallback. No API key required.

**`search_benchmark(description, geography, uom, currency) -> dict`**:
1. Builds a price-discovery query: `"{description} price cost Dubai UAE AED 2024"`
2. **DuckDuckGo Instant Answer API** (`api.duckduckgo.com`, free, no key) -- extracts prices from `Abstract`, `Answer`, and `RelatedTopics`
3. **Bing fallback** (scrapes `www.bing.com/search`) -- used only when DDG returns nothing
4. Parses prices with regex (recognises `AED 2,500`, `$1200`, `1,500 AED`, `AED 500 - 3,000`)
5. Returns corridor: `{min, avg, max, source="WEB_SEARCH", query, confidence=0.35, notes}`
6. `confidence` is capped at 0.35 -- downstream variance logic should treat WEB_SEARCH data as indicative only

**`search_product_info(system_type, capacity_tr, geography, currency, extra_keywords) -> dict`** -- used by the recommendation pipeline (separate from benchmark path):
- Returns `{snippets: list[str], pricing: {min, avg, max}, query, source, confidence, notes}`

#### ProcurementAgentOrchestrator (`apps/procurement/runtime/`)

Thinner orchestrator bridge used by both `BenchmarkService` and `RecommendationService` to route LLM agent calls through the platform's standard `AgentRun`/`AgentStep` record infrastructure.

- `ProcurementAgentMemory` -- dataclass holding shared state across all agent calls within one `run_benchmark()` / `run_recommendation()` invocation: `benchmark_findings: Dict[str, Any]`, `recommendation_findings: Dict[str, Any]`, and other cross-agent context
- `ProcurementAgentOrchestrator.run(run, agent_type, agent_fn, memory, extra_context, request_user)` -- wraps any callable `agent_fn` in a standard `AgentRun` + `AgentStep` pair
- `agent_type` string is unique per line item (e.g. `"benchmark_item_42"`), making each LLM call independently traceable in the audit log

### 5.9 ComplianceService

**File**: `apps/procurement/services/compliance_service.py`

Stateless rule-based compliance checking.

| Method | Description |
|---|---|
| `check_recommendation(request, recommendation)` | Checks: (1) recommendation present, (2) confidence ≥ 0.5, (3) budget constraint if `budget` attribute exists. Returns `{status, rules_checked, violations, recommendations}`. |
| `check_benchmark(request, benchmark_summary)` | Checks: overall variance ≤ 30%. Returns same structure. |

**Compliance status logic**:
- 0 violations → `PASS`
- 1 violation → `PARTIAL`
- 2+ violations → `FAIL`

### 5.10 ValidationRuleResolverService

**File**: `apps/procurement/services/validation/rule_resolver_service.py`

Resolves applicable validation rules for a procurement request based on domain and schema.

| Method | Description |
|---|---|
| `resolve_rule_sets(domain_code, schema_code, validation_type)` | Fetches active `ValidationRuleSet` records matching domain/schema with specificity ordering (exact match → domain-only → generic). |
| `resolve_rules(domain_code, schema_code, validation_type)` | Returns flat list of `ValidationRule` records from resolved rule sets. |
| `resolve_rules_for_request(request)` | Resolves rules across all 6 validation types for a given request. |

### 5.11 AttributeCompletenessValidationService

**File**: `apps/procurement/services/validation/attribute_completeness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Checks `REQUIRED_ATTRIBUTE` rules against request attributes. Validates presence and type for each required attribute. Returns list of finding dicts. |

### 5.12 DocumentCompletenessValidationService

**File**: `apps/procurement/services/validation/document_completeness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Checks `REQUIRED_DOCUMENT` rules. Maps document types (`QUOTATION`, `BOQ`, `SPECIFICATION`, etc.) to presence checks via quotation data. Returns findings. |

### 5.13 ScopeCoverageValidationService

**File**: `apps/procurement/services/validation/scope_coverage_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Compares expected categories from `REQUIRED_CATEGORY` rules and `config_json` against detected `category_code` values from `QuotationLineItem` records. Returns findings. |

### 5.14 AmbiguityValidationService

**File**: `apps/procurement/services/validation/ambiguity_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Scans request description, line item descriptions, and attribute values against configurable regex patterns. 12 default patterns ("as required", "lumpsum", "complete system", etc.) plus rule-defined patterns from `AMBIGUITY_PATTERN` rules. Returns findings with `AMBIGUOUS` status. |

### 5.15 CommercialCompletenessValidationService

**File**: `apps/procurement/services/validation/commercial_completeness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Keyword-based search for 8 default commercial terms (`WARRANTY`, `DELIVERY`, `PAYMENT_TERMS`, `TAXES`, `INSTALLATION`, `SUPPORT`, `LEAD_TIME`, `TESTING`) plus rule-defined checks from `COMMERCIAL_CHECK` rules. Returns findings. |

### 5.16 ComplianceReadinessValidationService

**File**: `apps/procurement/services/validation/compliance_readiness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Evaluates `COMPLIANCE_CHECK` rules with check_types: `attribute`, `keyword`, `geography`. Returns findings. |

### 5.17 ValidationOrchestratorService

**File**: `apps/procurement/services/validation/orchestrator_service.py`

Central orchestrator for the full validation pipeline. Decorated with `@observed_service`.

**`run_validation(request, run, agent_enabled=False)`** — steps:

1. **Resolve rules** — calls `ValidationRuleResolverService.resolve_rules_for_request()`
2. **Run all deterministic validators** — calls all 6 validators (attribute, document, scope, ambiguity, commercial, compliance)
3. **Optional agent augmentation** — if `agent_enabled=True` AND ambiguity count ≥ 3, calls `ValidationAgentService.augment_findings()`
4. **Score and classify** — computes completeness score (severity-weighted: CRITICAL=3×, ERROR=2×, WARNING=0.5×, INFO=0×)
5. **Determine status** — `_determine_overall_status()` maps score + findings to `PASS`/`PASS_WITH_WARNINGS`/`REVIEW_REQUIRED`/`FAIL`
6. **Determine readiness** — `_determine_readiness()` checks if request is ready for recommendation and/or benchmarking
7. **Persist** — creates `ValidationResult` + bulk-creates `ValidationResultItem` records in a transaction
8. **Complete run** — calls `AnalysisRunService.complete_run()`, logs `VALIDATION_COMPLETED` audit event

**Status classification**:
- Any CRITICAL missing → `FAIL`
- Score < 70 → `FAIL`
- Score < 90 with warnings → `REVIEW_REQUIRED`
- Score < 95 with warnings → `PASS_WITH_WARNINGS`
- Score >= 95 → `PASS`

### 5.18 MarketIntelligenceService

**File**: `apps/procurement/services/market_intelligence_service.py`

Generates real-time AI market intelligence for a procurement request. Supports two LLM back-ends: **Perplexity sonar** (live web search with domain filtering) and **Azure OpenAI** (knowledge-base generation). Entry points are decorated with `@observed_service`.

| Method | Description |
|---|---|
| `generate(proc_request, generated_by)` | OpenAI fallback path: builds prompt with `_USER_PROMPT_TPL` (including `system_name=` kwarg), calls Azure OpenAI, normalises `citation_url`/`price_source_url` as plain strings if they look like URLs, persists `MarketIntelligenceSuggestion` |
| `generate_with_perplexity(proc_request, generated_by)` | Perplexity path: builds prompt, queries `ExternalSourceRegistry` by `hvac_system_type__iexact` using `_SYSTEM_CODE_TO_DB_NAME` lookup, calls Perplexity sonar API with `search_domain_filter`, resolves citation indices to real URLs, persists `MarketIntelligenceSuggestion` |
| `generate_auto(proc_request, generated_by)` | Provider auto-router: uses `generate_with_perplexity` if `PERPLEXITY_API_KEY` is set, otherwise falls back to `generate` |
| `has_existing(proc_request)` | Returns `True` if at least one `MarketIntelligenceSuggestion` exists for the request |
| `get_latest(proc_request)` | Returns the most recently created `MarketIntelligenceSuggestion` (or `None`) |

**Perplexity generation -- citation-index post-processing** (applied per suggestion after the API response is parsed):

1. **`citation_url` resolution** -- `citation_index` (0-based int from LLM JSON) is used to index into `perplexity_citations` (the real `citations[]` list returned by the Perplexity API). If the index is valid, `citation_url = perplexity_citations[citation_index]`. If out of range, falls back to `perplexity_citations[0]` or the first `ExternalSourceRegistry.source_url`.
2. **`brand_page_url` resolution** -- The domain of the resolved `citation_url` is extracted and matched against `domain_to_source_url` (built from active registry entries). The matching `source_url` becomes `brand_page_url`, guaranteeing this link is always a registry-verified live page.
3. **`price_source_url` resolution** -- `price_citation_index` (0-based int from LLM JSON) is used identically to `citation_index` but for pricing pages. If the index is -1 or out of range, `price_source_url` is set to empty string.

**No HTTP liveness checks are performed.** Perplexity performs a live web search and the `citations[]` array contains only URLs Perplexity actually visited and confirmed during the search session. Using integer indices into that array is therefore more reliable than any server-side GET check, and avoids the problem where major OEM JavaScript SPA sites (Daikin, Mitsubishi, LG) return 4xx to Python bots even when the page is live.

**Approved sources query**: The service resolves the DB system name from `system_code` via `_SYSTEM_CODE_TO_DB_NAME` (e.g. `"VRF"` -> `"VRF System"`), then queries:
```python
ExternalSourceRegistry.objects.filter(
    hvac_system_type__iexact=db_system_name,
    is_active=True,
    allowed_for_discovery=True,
)
```
If no system-specific sources are found, it falls back to any active `allowed_for_discovery` source.

**Why `sources_block` omits URL paths**: Early versions passed full paths (e.g. `/products/ac/commercial/vrv/`) into the Perplexity prompt. Perplexity echoed those paths back in `citation_url` unchanged — even when they returned 404. The fix: `sources_block` now sends only `brand name | domain` (no paths). Perplexity then discovers real product URLs through live web search.

**JSON schema sent to LLM** (per suggestion item in `_USER_PROMPT_TPL`):

```python
{
    "rank": 1,
    "product_name": "Daikin VRV-X Series RXYQ48T",
    "manufacturer": "Daikin",
    "model_code": "RXYQ48T7W1B",
    "system_type": "{system_name}",   # locked to the request system type
    "cooling_capacity": "8 TR - 12 TR",
    "cop_eer": "COP 3.8 / EER 13.0",
    "price_range_aed": "AED 95,000 - 125,000",
    "market_availability": "Available via authorised UAE distributors",
    "key_benefits": ["benefit 1", "benefit 2", "benefit 3"],
    "limitations": ["limitation 1", "limitation 2"],
    "fit_score": 88,
    "fit_rationale": "Best fit for large commercial floor plans above 1,000 sqm",
    "standards_compliance": ["ASHRAE 90.1", "ESMA UAE"],
    "citation_index": 0,              # 0-based index into Perplexity citations[]
    "citation_source": "Daikin Middle East",
    "price_citation_index": 0,        # 0-based index; -1 if no price page found
    "category": "MANUFACTURER"
}
```

Post-processing resolves `citation_index` and `price_citation_index` to real URLs from the Perplexity `citations[]` array and adds `citation_url`, `brand_page_url`, `price_source_url`, and `icon_class` to each suggestion dict before storing in `suggestions_json`.

**Configuration**:

| Setting | Default | Description |
|---|---|---|
| `PERPLEXITY_API_KEY` | (none) | API key for Perplexity sonar; if unset, `generate_auto` routes to OpenAI |
| `PERPLEXITY_MODEL` | `"sonar"` | Perplexity model identifier |

---

## 6. Agent System

Agent files live in `apps/procurement/agents/`. One service-layer agent lives in
`apps/procurement/services/validation/`. All LLM agents use `LLMClient` from
`apps.agents.services.llm_client` (Azure OpenAI or OpenAI, configured via
`LLM_PROVIDER` / `AZURE_OPENAI_*` env vars).

**Design principle -- deterministic first**: Agents are only invoked when rule-based
logic cannot produce a confident answer. Every agent catches all exceptions and returns
a safe fallback dict; none raises to the caller.

| File | Lines | Status | Purpose |
|---|---|---|---|
| `hvac_recommendation_agent.py` | 548 | Full | PRIMARY -- HVAC system selection (two entry points) |
| `reason_summary_agent.py` | 773 | Full | Transforms RecommendationResult into rich UI display |
| `RFQ_Generator_Agent.py` | 1235 | Full | Generates Excel + PDF RFQ from approved recommendation |
| `Azure_Document_Intelligence_Extractor_Agent.py` | 714 | Full | ReAct-style document OCR via Azure Document Intelligence |
| `Perplexity_Market_Research_Analyst_Agent.py` | 660 | Full | Live web product sourcing via Perplexity sonar API |
| `Fallback_Webscraper_Agent.py` | 640 | Full | Playwright + Azure OAI fallback when Perplexity fails |
| `request_extraction_agent.py` | 106 | Shallow | OCR text -> structured procurement request (12 k char limit) |
| `compliance_agent.py` | 63 | Stub | Generic LLM compliance check -- no domain rules yet |
| `services/validation/validation_agent.py` | -- | Full | Ambiguity resolution for validation findings |

> `recommendation_agent.py` (305 lines) exists in the directory but is superseded
> by `hvac_recommendation_agent.py` and is not called by any active service.
> `benchmark_agent.py` does not exist and is not required.

---

### 6.1 HVACRecommendationAgent (PRIMARY)

**File**: `apps/procurement/agents/hvac_recommendation_agent.py`

The primary recommendation agent. Called by `RecommendationService` only for HVAC
domain requests. Implements two distinct entry points depending on whether the
deterministic rules engine produced a match.

#### Entry point A -- `recommend(attrs, no_match_context, procurement_request_pk)`

Called when `HVACRulesEngine.evaluate()` returns `confident=False` (no rule matched).
The agent performs full AI-driven system selection.

- Loads DB context via `_load_db_context()`: available system types, similar historical
  stores, and cached `MarketIntelligenceSuggestion` records.
- Sends a single LLM chat call with a detailed system prompt listing all known HVAC
  system types and the full attribute payload.
- Parses the JSON response via `_extract_json()` (strips fences, repairs truncation).

**Returns** (same schema as `HVACRulesEngine.evaluate()`):

```json
{
  "recommended_system_type": "VRF",
  "recommended_option":      "Daikin VRV-X 135 kW",
  "reasoning_summary":       "VRF is optimal for...",
  "confidence":              0.82,
  "decision_drivers":        ["area_sqm > 5000", "multi_zone=true"],
  "tradeoffs":               "Higher upfront vs. lower OPEX...",
  "constraints":             ["Power: 3-phase 415V required"],
  "alternate_option":        "Carrier AquaForce chiller",
  "indicative_capacity_tr":  38.4,
  "human_validation_required": false,
  "market_notes":            "Daikin VRV pricing AED 28k-36k range",
  "compliance_notes":        "Meets UAE Estidama Pearl 2 equivalent",
  "source":                  "hvac_agent"
}
```

#### Entry point B -- `explain(attrs, rule_result)`

Called when `HVACRulesEngine.evaluate()` returns `confident=True` (a rule DID match).
The agent adds tradeoff commentary alongside the deterministic result without overriding
the recommendation itself.

- Sends a shorter `EXPLAIN_SYSTEM_PROMPT` with the matched rule details and attributes.
- Inserts the LLM tradeoff text into `rule_result["tradeoffs"]` and returns the
  enriched dict.
- Falls back to the original `rule_result` unchanged on any LLM error.

---

### 6.2 ReasonSummaryAgent

**File**: `apps/procurement/agents/reason_summary_agent.py`

Transforms a `RecommendationResult` DB record into a human-readable display payload
for the recommendation detail page. Combines one LLM call with deterministic
extraction from the stored JSON payload.

**Entry point**: `ReasonSummaryAgent.generate(recommendation_result) -> dict`

**LLM output** (natural language, generated):

| Key | Description |
|---|---|
| `headline` | One-sentence plain-English recommendation summary |
| `reasoning_summary` | 2-4 sentence narrative explaining the choice |
| `top_drivers` | List of 3-5 most influential decision factors |

**Deterministic output** (extracted from `recommendation_result.result_json`):

| Key | Description |
|---|---|
| `rules_table` | List of `{rule, result, detail}` dicts from matched rules |
| `conditions_table` | List of `{condition, value, met}` dicts for rule conditions |
| `alternatives_table` | List of `{option, reason_rejected}` dicts for alternate systems |
| `constraints` | List of installation / power / structural constraint strings |
| `assumptions` | List of assumed values (e.g., default occupancy) |
| `thought_steps` | Sequential reasoning steps from `decision_drivers` |
| `standards` | Applicable standards (e.g., ASHRAE 90.1, UAE Green Building) |

Falls back gracefully to deterministic text only if the LLM call fails.

---

### 6.3 RFQGeneratorAgent

**File**: `apps/procurement/agents/RFQ_Generator_Agent.py`

Generates a complete Request for Quotation document in both Excel (`.xlsx`) and
PDF formats from an approved procurement request.

**Entry point**:

```python
RFQGeneratorAgent.run(
    proc_request,                    # ProcurementRequest instance
    selection_mode="RECOMMENDED",    # "RECOMMENDED" or any system code string
    qty_overrides=None,              # optional {system_code: qty} dict
    generated_by=None,               # User instance or None
    save_record=True,                # persist RFQDocument record to DB
) -> RFQResult
```

**`selection_mode`**:
- `"RECOMMENDED"` -- reads the latest approved `RecommendationResult` for the request
  and uses its `recommended_system_type`.
- Any other string (e.g. `"VRF"`, `"CHILLER"`) -- generates for that explicit system
  code regardless of recommendation.

**`RFQResult` dataclass fields**:

| Field | Type | Description |
|---|---|---|
| `xlsx_bytes` | `bytes` | Raw Excel file content |
| `pdf_bytes` | `bytes` | Raw PDF file content |
| `rfq_ref` | `str` | Generated reference: `RFQ-{YYYYMMDD}-{request_pk}` |
| `filename_xlsx` | `str` | Suggested save name for the Excel file |
| `filename_pdf` | `str` | Suggested save name for the PDF file |
| `system_code` | `str` | System type code used for generation |
| `system_label` | `str` | Human-readable system name |
| `selection_basis` | `str` | `"recommendation"` or `"manual"` |
| `confidence_pct` | `int` | Recommendation confidence as 0-100 integer |
| `scope_rows` | `list` | Scope-of-work line items included in the RFQ |
| `rfq_record` | `RFQDocument` | Persisted DB record (if `save_record=True`) |
| `error` | `str or None` | Error message if generation failed |

---

### 6.4 AzureDocumentIntelligenceExtractorAgent

**File**: `apps/procurement/agents/Azure_Document_Intelligence_Extractor_Agent.py`

Universal document extractor using a ReAct-style tool-calling loop:

1. LLM is invoked with an `extract_document_text` tool spec.
2. When the model issues the tool call, the agent runs Azure Document Intelligence
   and returns raw OCR text + tables + key-value pairs as a `tool` role message.
3. LLM synthesises a final structured JSON response from the DI output.

**Entry point**:

```python
AzureDIExtractorAgent.extract(
    file_path=None,       # absolute path to the document file, OR
    file_bytes=None,      # raw bytes
    mime_type=None,       # required when using file_bytes
) -> dict
```

**Supported input formats**: PDF, JPEG, JPG, PNG, BMP, TIFF, HEIF, DOCX, XLSX, PPTX.

**Output dict keys**:

| Key | Description |
|---|---|
| `success` | `bool` |
| `doc_type` | `"invoice"`, `"quotation"`, `"purchase_order"`, `"delivery_note"`, `"contract"`, `"proforma_invoice"`, or `"unknown"` |
| `confidence` | `float` 0.0-1.0 overall extraction confidence |
| `header` | Dict of top-level fields (vendor, buyer, document number, date, totals, etc.) each with `{value, confidence}` |
| `line_items` | List of line item dicts (description, quantity, unit, unit_rate, etc.) |
| `commercial_terms` | Dict of warranty, installation, support, penalty, lead-time terms |
| `raw_ocr_text` | Concatenated plain text from DI |
| `tables` | List of table dicts extracted by DI |
| `key_value_pairs` | List of `{key, value, confidence}` from DI |
| `engine` | `"azure_di_gpt4o"` or `"error"` |
| `duration_ms` | Total wall-clock time in milliseconds |
| `error` | `None` or error string |

**Required env vars**: `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`, `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`.

---

### 6.5 PerplexityMarketResearchAnalystAgent

**File**: `apps/procurement/agents/Perplexity_Market_Research_Analyst_Agent.py`

Primary market intelligence agent. Uses the Perplexity sonar API (`sonar-pro` model)
to perform live web searches for commercially available products matching the request.

**Entry point**: `PerplexityMarketResearchAnalystAgent().run(proc_request, generated_by) -> dict`

**Behaviour**:

1. Builds a purchase-intent search query from the request attributes.
2. Sends the query to Perplexity with a system prompt instructing it to prioritise
   B2B marketplace pages (Alibaba, IndiaMART, Tradekey, etc.) and vendor product pages.
3. Parses the JSON response via a 4-step `_parse_json` recovery (handles open fences,
   missing closing fence, markdown wrappers, and partial truncation).
4. Persists a `MarketIntelligenceSuggestion` record and returns the structured dict.

**Output dict keys**: `system_code`, `system_name`, `rephrased_query`, `ai_summary`,
`market_context`, `suggestions` (list), `perplexity_citations` (list of source URLs).

**Required env var**: `PERPLEXITY_API_KEY`. If absent, `generate_auto()` in
`market_intelligence_service.py` routes directly to `FallbackWebscraperAgent`.

---

### 6.6 FallbackWebscraperAgent

**File**: `apps/procurement/agents/Fallback_Webscraper_Agent.py`

Playwright + Azure OpenAI fallback path for market intelligence. Triggered automatically
by `market_intelligence_service.generate_auto()` when:

- `PERPLEXITY_API_KEY` is not set, OR
- Perplexity raises any exception, OR
- Perplexity returns zero suggestions.

**Entry point**: `FallbackWebscraperAgent().run(proc_request, generated_by) -> dict`

**Three-step pipeline**:

1. **URL selection** -- Azure OpenAI selects up to 6 commercial vendor / marketplace
   URLs best suited to the product attributes.
2. **Playwright scraping** -- Headless Chromium visits each URL, captures up to
   6 000 characters of visible body text per page (30 s timeout per page).
3. **LLM parsing** -- Azure OpenAI processes the combined scraped text and extracts
   structured product suggestions in the same JSON schema as
   `PerplexityMarketResearchAnalystAgent`.

**Returns** the same dict shape as `PerplexityMarketResearchAnalystAgent.run()` so all
callers are agnostic to which agent provided the data.
`perplexity_citations` is populated with the scraped URLs.

**Dependency**: `playwright` Python package + `playwright install chromium`.

**Error handling**: If both Perplexity and the fallback fail, `generate_auto()` raises
`ValueError` naming both error messages.

---

### 6.7 RequestExtractionAgent

**File**: `apps/procurement/agents/request_extraction_agent.py`

Lightweight agent that converts raw OCR text (from an uploaded PDF or image) into a
structured `ProcurementRequest`-compatible dict.

**Entry point**: `RequestExtractionAgent().extract(ocr_text: str) -> dict`

- Input limited to 12 000 characters (trimmed before sending to LLM).
- Single `LLMClient.chat()` call with a concise system prompt.

**Output JSON**:

```json
{
  "title":       "VRF Air Conditioning System -- Office Tower Block A",
  "description": "Supply and installation of VRF system...",
  "domain_code": "HVAC",
  "attributes":  [
    {"attribute_code": "area_sqm",     "value": "4500",  "unit": "sqm"},
    {"attribute_code": "climate_zone", "value": "HOT_ARID"}
  ]
}
```

Falls back to `{"title": "", "description": ocr_text[:500], "domain_code": "", "attributes": []}`
on any LLM error.

---

### 6.8 ComplianceAgent

**File**: `apps/procurement/agents/compliance_agent.py`

Extension point for AI-augmented compliance checking. Currently a minimal stub --
no domain-specific rule tables are wired in yet.

**Entry point**: `ComplianceAgent.check(request, context) -> dict`

- `request`: `ProcurementRequest` instance (provides `domain_code`, `geography_country`)
- `context`: arbitrary dict (e.g., recommendation payload or benchmark results)

**Output JSON**:

```json
{
  "status":          "PASS | FAIL | PARTIAL | NOT_CHECKED",
  "rules_checked":   [{"rule": "...", "description": "..."}],
  "violations":      [{"rule": "...", "detail": "..."}],
  "recommendations": ["..."]
}
```

Returns `status: "NOT_CHECKED"` with the exception text in `recommendations` on failure.

---

### 6.9 ValidationAgentService

**File**: `apps/procurement/services/validation/validation_agent.py`

Lightweight LLM agent for ambiguity resolution within the validation pipeline. Only
invoked when the deterministic `ValidationService` produces 3 or more ambiguous items
in a single run.

**Entry point**: `ValidationAgentService.augment_findings(request, run, findings) -> list`

**Steps**:

1. Filters ambiguous items from the `findings` list.
2. Creates an `AgentRun` DB record for full auditability.
3. Sends the ambiguous items to the LLM with a system prompt requesting JSON
   classification (`VALID`, `INVALID`, or `NEEDS_REVIEW` per item).
4. Applies LLM resolutions back to the `findings` list (updates `status`, `remarks`,
   `source_type`).
5. Logs an `AgentStep` record with resolution details.
6. On any LLM error, returns the original unmodified `findings` list.

Does NOT replace deterministic checks -- it augments them on the ambiguous subset only.

---

## 7. API Reference

All APIs are mounted under `/api/v1/procurement/`.

### 7.1 ProcurementRequestViewSet

**Base URL**: `/api/v1/procurement/requests/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/requests/` | List all requests (paginated, filterable) |
| `POST` | `/requests/` | Create new request (with inline attributes) |
| `GET` | `/requests/{id}/` | Get request detail (with attributes, quotations, runs) |
| `PUT/PATCH` | `/requests/{id}/` | Update request |
| `DELETE` | `/requests/{id}/` | Delete request |
| `GET` | `/requests/{id}/attributes/` | List attributes for a request |
| `POST` | `/requests/{id}/attributes/` | Bulk set attributes |
| `GET` | `/requests/{id}/runs/` | List analysis runs |
| `POST` | `/requests/{id}/runs/` | Trigger new analysis run (`{"run_type": "RECOMMENDATION" or "BENCHMARK" or "VALIDATION"}`) |
| `GET` | `/requests/{id}/recommendation/` | Get latest recommendation result |
| `GET` | `/requests/{id}/benchmark/` | Get all benchmark results |
| `POST` | `/requests/{id}/validate/` | Trigger validation run (creates `AnalysisRun(VALIDATION)` and dispatches task) |
| `GET` | `/requests/{id}/validation/` | Get latest validation result with items |

**Filters** (via `DjangoFilterBackend`): `status`, `request_type`, `domain_code`, `priority`

**Search** (via `SearchFilter`): `title`, `description`, `domain_code`

**Ordering** (via `OrderingFilter`): `created_at`, `updated_at`, `priority`, `status`

**Serializers**:
- **List**: `ProcurementRequestListSerializer` — lightweight with counts (`attribute_count`, `quotation_count`, `run_count`)
- **Detail**: `ProcurementRequestDetailSerializer` — full with nested `attributes`, `quotations`, `analysis_runs`
- **Write**: `ProcurementRequestWriteSerializer` — accepts inline `attributes` array, calls `ProcurementRequestService.create_request()`

### 7.2 SupplierQuotationViewSet

**Base URL**: `/api/v1/procurement/quotations/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/quotations/` | List all quotations |
| `POST` | `/quotations/` | Create quotation |
| `GET` | `/quotations/{id}/` | Get quotation detail (with line items) |
| `PUT/PATCH` | `/quotations/{id}/` | Update quotation |
| `DELETE` | `/quotations/{id}/` | Delete quotation |

**Filters**: `extraction_status`, `currency`, `request`

**Serializers**:
- **List**: `SupplierQuotationListSerializer` — with `line_item_count`
- **Detail**: `SupplierQuotationDetailSerializer` — full with nested `line_items`

### 7.3 ValidationRuleSetViewSet

**Base URL**: `/api/v1/procurement/validation/rulesets/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/validation/rulesets/` | List all validation rule sets |
| `GET` | `/validation/rulesets/{id}/` | Get rule set detail with nested rules |

**Filters**: `domain_code`, `schema_code`, `validation_type`, `is_active`

**Search**: `rule_set_code`, `rule_set_name`

**Serializers**:
- **List**: `ValidationRuleSetListSerializer` — with `rule_count`
- **Detail**: `ValidationRuleSetSerializer` — full with nested `rules`

### 7.4 AnalysisRunValidationView

**URL**: `/api/v1/procurement/runs/{id}/validation/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/runs/{id}/validation/` | Get validation result for a specific analysis run |

**Serializers**: `ValidationResultSerializer` with nested `ValidationResultItemSerializer`

### 7.5 External Suggestions (Template API)

**URL**: `/procurement/{id}/external-suggestions/`  
**View**: `api_external_suggestions` (function-based view, `@login_required`)

| Method | URL | Description |
|---|---|---|
| `POST` | `/procurement/{id}/external-suggestions/` | Trigger market intelligence generation for the request |
| `GET` | `/procurement/{id}/external-suggestions/` | Return the latest cached `MarketIntelligenceSuggestion` as JSON |

**POST request body** (optional):

```json
{
    "provider": "perplexity",
    "query": "VRF system 135kW UAE commercial building"
}
```

If `provider` is omitted, `generate_auto()` is used (Perplexity if key is set, else OpenAI).

**GET response body** (abridged):

```json
{
    "request_id": 170,
    "provider": "perplexity",
    "suggestions": [
        {
            "model": "Daikin VRV-X Series RXYQ48T",
            "brand": "Daikin",
            "price_min": 28000,
            "price_max": 36000,
            "currency": "USD",
            "citation_url": "https://www.daikin.com/products/",
            "brand_page_url": "https://www.daikin.com/products/",
            "price_source_url": null,
            "rationale": "..."
        }
    ]
}
```

**Permissions**: `@login_required`. Users with `procurement.view` can GET; `procurement.run_analysis` required for POST trigger.

### Authentication

All endpoints require authentication (`permissions.IsAuthenticated`). RBAC permission classes (`HasPermissionCode`) are available for finer-grained control — see [Section 12](#12-rbac--permissions).

---

## 8. Template Views & UI

All template views are in `apps/procurement/template_views.py`. URLs are in `apps/procurement/urls.py` with `app_name = "procurement"`.

### 8.1 Request List (`/procurement/`)

**View**: `request_list`  
**Template**: `templates/procurement/request_list.html`

Features:
- Paginated table (25 per page)
- Filter by: status, request type, domain code, search text
- Status badges (color-coded per status)
- Priority indicators (colored dots)
- Attribute, quotation, and run counts per request
- "New Request" button

### 8.2 Create Request (`/procurement/create/`)

**View**: `request_create`  
**Template**: `templates/procurement/request_create.html`

Features:
- Core fields: title, description, domain code, schema code, request type, priority
- Geography: country, city, currency
- Dynamic attribute form: add/remove attribute rows with code, label, type, value
- JavaScript for adding dynamic attribute rows
- Redirects to workspace on success

### 8.3 Request Workspace (`/procurement/{id}/`)

**View**: `request_workspace`  
**Template**: `templates/procurement/request_workspace.html`

The primary workspace for a procurement request. Sections:

| Section | Description |
|---|---|
| **Request Summary** | Title, description, status badge, type, domain, priority, geography, currency, trace ID. Action buttons: "Mark Ready" (if DRAFT), "Run Analysis" with type selector (if READY/COMPLETED/REVIEW_REQUIRED). |
| **Attributes** | Table of all `ProcurementRequestAttribute` records (code, label, type, value). |
| **Validation Summary** | Latest `ValidationResult`: overall status badge, completeness progress bar, summary text, readiness indicators (recommendation/benchmarking), next action recommendation, missing items accordion, warnings accordion, ambiguous items accordion, detailed findings table accordion, last-validated footer with trace ID. Included via `{% include "procurement/partials/validation_summary.html" %}`. |
| **Recommendation** | Latest `RecommendationResult`: recommended option, reasoning, confidence percentage, compliance badge. |
| **Benchmark Results** | All `BenchmarkResult` records: vendor name, risk badge, quoted/benchmark/variance summary, line-level comparison table. |
| **Compliance** | Latest `ComplianceResult`: status badge, violations list. |
| **Quotations** (right column) | List of quotations with vendor name, amount, extraction status. Collapsible form to add new quotation. |
| **Analysis Runs** (right column) | Linked list to `run_detail` view. Shows type icon (including VALIDATION → `bi-check2-square`), status badge, date, confidence. |
| **Activity Timeline** (right column) | Uses existing `AuditService.fetch_entity_history("ProcurementRequest", pk)` to show all governance events. |

### 8.4 Analysis Run Detail (`/procurement/run/{id}/`)

**View**: `run_detail`  
**Template**: `templates/procurement/run_detail.html`

Features:
- Run metadata: run ID, status, confidence, start/end time, duration, triggered by, trace ID
- Output summary / error message
- Input snapshot (pretty-printed JSON)
- Recommendation result (if RECOMMENDATION type): option, reasoning, confidence, compliance, reasoning details (collapsible)
- Benchmark results (if BENCHMARK type): vendor, quoted/benchmark/variance/risk, line-level table
- Validation result (if VALIDATION type): overall status, completeness score, summary, findings
- Compliance result: status, violations list
- Audit trail: events from `AuditService.fetch_entity_history("AnalysisRun", pk)`

### 8.5 Action Views

| URL | Method | View | Description |
|---|---|---|---|
| `/procurement/{id}/trigger/` | POST | `trigger_analysis` | Creates `AnalysisRun` and fires `run_analysis_task` Celery task |
| `/procurement/{id}/ready/` | POST | `mark_ready` | Validates required attributes and sets status to `READY` |
| `/procurement/{id}/quotation/` | POST | `upload_quotation` | Creates `SupplierQuotation` from form data |
| `/procurement/{id}/validate/` | POST | `trigger_validation` | Creates `AnalysisRun(VALIDATION)` and fires `run_validation_task` Celery task |

### 8.6 Market Intelligence (`/procurement/{id}/market-intelligence/`)

**View**: `market_intelligence`  
**Template**: `templates/procurement/market_intelligence.html`

Displays AI-generated product suggestions for the procurement request. Suggestions are generated on demand via `MarketIntelligenceService.generate_auto()` and stored in `MarketIntelligenceSuggestion`.

#### Page Sections

| Section | Description |
|---|---|
| **Query Banner** | Shows the system type, target capacity, geography, currency inferred from request attributes |
| **Summary Cards** | Count of suggestions, price range, source provider badge |
| **Product Table** | One row per suggestion: model, brand, capacity, price range, efficiency, citation link (Product Page button), rationale |
| **Expandable Detail Row** | Click any row to expand the rationale, limitations, standards text, and the 3-link source block |

#### 3-Link Source Block (Dropdown Detail)

The expanded detail row shows up to three labelled link buttons:

| Button | Colour | Source | Condition |
|---|---|---|---|
| **Product Page** | Blue (`btn-outline-primary`) | `citation_url` from LLM | Only shown if `citation_url != brand_page_url` |
| **Price Source** | Green (`btn-outline-success`) | `price_source_url` from LLM | Only shown if non-null |
| **Brand Site** | Grey (`btn-outline-secondary`) | `brand_page_url` from registry | Always shown (guaranteed-live) |

**Deduplication rule**: "Product Page" is suppressed when `citation_url` equals `brand_page_url` (i.e. the citation-index resolved to the same URL as the registry landing page, or no valid citation index was available). This prevents showing the same URL twice.

#### Data Flow

```
request_workspace.html
  → "Market Intelligence" button
  → POST /procurement/{id}/external-suggestions/
  → MarketIntelligenceService.generate_auto(request_pk)
      → generate_with_perplexity()  (if PERPLEXITY_API_KEY set)
          → build prompt + domain filter from ExternalSourceRegistry
          → call Perplexity sonar API
          → parse JSON suggestions
          → 3-step post-processing (domain enforce, brand_page_url resolve, GET liveness)
      → persist MarketIntelligenceSuggestion
  → redirect to GET /procurement/{id}/market-intelligence/
  → market_intelligence view fetches MarketIntelligenceSuggestion.get_latest()
  → render market_intelligence.html with suggestions list
```

#### URL Configuration

```python
# apps/procurement/urls.py
path("<int:pk>/market-intelligence/", views.market_intelligence, name="market_intelligence"),
path("<int:pk>/external-suggestions/", views.api_external_suggestions, name="api_external_suggestions"),
```

### Sidebar Navigation

A new "Procurement" section is added to the global sidebar (`templates/partials/sidebar.html`) between the copilot/dashboard entries and the Documents section:

```html
{# ── Procurement ── #}
<li class="nav-item">
  <a class="nav-link" href="{% url 'procurement:request_list' %}">
    <i class="bi bi-cart4 me-2"></i>Requests
  </a>
</li>
```

---

## 9. Celery Tasks

**File**: `apps/procurement/tasks.py`

### `run_analysis_task(run_id: int) → dict`

**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`  
**Observability**: `@observed_task("procurement.run_analysis", audit_event="ANALYSIS_RUN_STARTED", entity_type="AnalysisRun")`

**Behavior**:
1. Loads the `AnalysisRun` with its related `ProcurementRequest`
2. Sets request status to `PROCESSING`
3. Dispatches based on `run_type`:
   - `RECOMMENDATION` → `RecommendationService.run_recommendation(request, run)`
   - `BENCHMARK` → `BenchmarkService.run_benchmark(request, run, quotation)` (uses first quotation)
   - `VALIDATION` → `ValidationOrchestratorService.run_validation(request, run)`
4. Returns structured result dict with status, run_id, type-specific data

**Error handling**: Catches exceptions and returns `{"status": "failed", "error": "..."}`.

**Execution mode**: In development on Windows, runs synchronously via `CELERY_TASK_ALWAYS_EAGER=True` (existing setting). In production, runs asynchronously with Redis broker.

### `run_validation_task(run_id: int) → dict`

**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`  
**Observability**: `@observed_task("procurement.run_validation", audit_event="VALIDATION_RUN_STARTED", entity_type="AnalysisRun")`

**Behavior**:
1. Loads the `AnalysisRun` with its related `ProcurementRequest`
2. Calls `ValidationOrchestratorService.run_validation(request, run)`
3. Updates request status based on validation outcome:
   - `PASS` → `READY`
   - `FAIL` → `FAILED`
   - `REVIEW_REQUIRED` → `REVIEW_REQUIRED`
4. Returns structured result dict with status, completeness_score, overall_status

### `run_quotation_prefill_task(quotation_id: int) → dict`

**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`  
**Observability**: `@observed_task("procurement.quotation_prefill", audit_event="PREFILL_STARTED", entity_type="SupplierQuotation")`

**Behavior**:
1. Loads the `SupplierQuotation` with its related `uploaded_document` and `request`
2. Calls `QuotationDocumentPrefillService.run_prefill(quotation)`
3. Returns structured result dict with status, quotation_id, prefill_status, line_item_count

**Error handling**: Catches exceptions and returns `{"status": "failed", "error": "..."}`. Quotation `extraction_status` set to `FAILED`.

---

## 10. Governance & Audit Integration

The procurement platform **reuses existing governance infrastructure** — no new audit modules were created.

### Existing Services Used

| Service | Module | Usage in Procurement |
|---|---|---|
| **AuditService** | `apps.auditlog.services` | All business events are logged via `AuditService.log_event()` |
| **AuditEvent** | `apps.auditlog.models` | Events are stored as `AuditEvent` records with full RBAC snapshot |
| **ProcessingLog** | `apps.auditlog.models` | Operational logs written by `@observed_service` decorator |
| **TraceContext** | `apps.core.trace` | Distributed tracing with `trace_id` / `span_id` propagation |
| **AuditService.fetch_entity_history()** | `apps.auditlog.services` | Used by workspace and run detail views for activity timeline |

### Audit Events Emitted

Every significant action in the procurement flow logs an `AuditEvent`:

| Event Type | Entity Type | Triggered By |
|---|---|---|
| `PROCUREMENT_REQUEST_CREATED` | `ProcurementRequest` | `ProcurementRequestService.create_request()` |
| `PROCUREMENT_REQUEST_STATUS_CHANGED` | `ProcurementRequest` | `ProcurementRequestService.update_status()` |
| `QUOTATION_UPLOADED` | `SupplierQuotation` | `QuotationService.create_quotation()` |
| `ANALYSIS_RUN_CREATED` | `AnalysisRun` | `AnalysisRunService.create_run()` |
| `ANALYSIS_RUN_STARTED` | `AnalysisRun` | `AnalysisRunService.start_run()` |
| `ANALYSIS_RUN_COMPLETED` | `AnalysisRun` | `AnalysisRunService.complete_run()` |
| `ANALYSIS_RUN_FAILED` | `AnalysisRun` | `AnalysisRunService.fail_run()` |
| `VALIDATION_RUN_STARTED` | `AnalysisRun` | `run_validation_task` (via `@observed_task`) |
| `VALIDATION_COMPLETED` | `ProcurementRequest` | `ValidationOrchestratorService.run_validation()` |

### Audit Event Fields Populated

Each audit event includes:

- `entity_type` + `entity_id` — which object was affected
- `event_type` — what happened
- `description` — human-readable summary
- `user` — who performed the action (Django User)
- `trace_ctx` — TraceContext for distributed tracing correlation
- `status_before` / `status_after` — for state transitions
- `output_snapshot` — redacted payload snapshot (for completed runs)
- `error_code` — for failure events

### Activity Timeline in UI

Both the **Request Workspace** and **Run Detail** views display an activity timeline powered by `AuditService.fetch_entity_history()`:

```python
# In request_workspace view:
audit_events = AuditService.fetch_entity_history("ProcurementRequest", proc_request.pk)

# In run_detail view:
audit_events = AuditService.fetch_entity_history("AnalysisRun", run.pk)
```

This provides a chronological log of all governance events for each entity without any new governance UI or modules.

---

## 11. Observability Integration

The procurement platform uses the existing observability infrastructure.

### Decorators Applied

| Decorator | Applied To | Effect |
|---|---|---|
| `@observed_service(...)` | `create_request`, `create_quotation`, `run_recommendation`, `run_benchmark`, `create_run`, `run_validation` | Creates child trace spans, measures duration, writes `ProcessingLog` |
| `@observed_task(...)` | `run_analysis_task`, `run_validation_task` | Trace propagation via Celery headers, writes `ProcessingLog`, emits audit event |

### Trace Propagation

1. **Request** → `RequestTraceMiddleware` creates root `TraceContext`
2. **Service** → `@observed_service` creates child span
3. **Task** → `@observed_task` propagates trace via Celery headers
4. **Nested services** — child spans preserve parent `trace_id`
5. **Models** — `ProcurementRequest.trace_id` and `AnalysisRun.trace_id` store the trace ID for cross-referencing

---

## 12. RBAC & Permissions

The procurement platform has its own RBAC roles, permissions, and role-permission matrix — fully integrated with the existing platform RBAC system.

### Roles

Three procurement-specific roles were added (seeded via `python manage.py seed_rbac`):

| Role Code | Name | Rank | Description |
|---|---|---|---|
| `PROCUREMENT_MANAGER` | Procurement Manager | 25 | Supervise procurement operations, review high-risk results, full control including delete |
| `CATEGORY_MANAGER` | Category Manager | 35 | Domain expert — manage category-specific rules, benchmarks, review results within their domain |
| `PROCUREMENT_BUYER` | Procurement Buyer | 55 | Operational buyer — create requests, upload quotations, trigger analysis |

These are separate from AP roles (AP_PROCESSOR, REVIEWER) because procurement teams are typically different from accounts payable teams.

### Permissions

Eight procurement permissions (module: `procurement`):

| Permission Code | Name | Description |
|---|---|---|
| `procurement.view` | View Procurement Requests | View requests, attributes, and quotations |
| `procurement.create` | Create Procurement Requests | Create new procurement requests |
| `procurement.edit` | Edit Procurement Requests | Edit requests and manage attributes |
| `procurement.delete` | Delete Procurement Requests | Delete procurement requests |
| `procurement.run_analysis` | Run Procurement Analysis | Trigger recommendation and benchmark analysis runs |
| `procurement.manage_quotations` | Manage Quotations | Upload and manage supplier quotations |
| `procurement.view_results` | View Analysis Results | View recommendation, benchmark, compliance, and validation results |
| `procurement.validate` | Run Validation | Trigger validation analysis runs |

### Role-Permission Matrix

| Permission | ADMIN | PROC_MGR | CAT_MGR | PROC_BUYER | FIN_MGR | AUDITOR | REVIEWER | AP_PROC | SYS_AGENT |
|---|---|---|---|---|---|---|---|---|---|
| `procurement.view` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | ✅ |
| `procurement.create` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | — |
| `procurement.edit` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | — |
| `procurement.delete` | ✅ | ✅ | — | — | — | — | — | — | — |
| `procurement.run_analysis` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | ✅ |
| `procurement.manage_quotations` | ✅ | ✅ | — | ✅ | — | — | — | — | — |
| `procurement.view_results` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | ✅ |
| `procurement.validate` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | ✅ |

**Rationale**:
- **ADMIN** — all permissions (auto-granted from PERMISSIONS list)
- **PROCUREMENT_MANAGER** — full control including delete (supervisory)
- **CATEGORY_MANAGER** — create/edit/run/view but no delete or quotation management
- **PROCUREMENT_BUYER** — operational: create, edit, run, manage quotations, but no delete
- **FINANCE_MANAGER** — cross-functional oversight: view + view results only
- **AUDITOR** — read-only: view + view results
- **SYSTEM_AGENT** — automated pipeline: view, run_analysis, view_results

### Enforcement

#### API Views (`apps/procurement/views.py`)

Both ViewSets use `get_permissions()` to route each action to the correct permission:

| ViewSet | Action | Permission |
|---|---|---|
| `ProcurementRequestViewSet` | `list`, `retrieve` | `procurement.view` |
| | `create` | `procurement.create` |
| | `update`, `partial_update` | `procurement.edit` |
| | `destroy` | `procurement.delete` |
| | `attributes` (GET) | `procurement.view` |
| | `attributes` (POST) | `procurement.edit` |
| | `runs` (GET) | `procurement.view` |
| | `runs` (POST) | `procurement.run_analysis` |
| | `recommendation` (GET) | `procurement.view_results` |
| | `benchmark` (GET) | `procurement.view_results` |
| | `validation` (GET) | `procurement.view_results` |
| | `validate` (POST) | `procurement.validate` |
| `SupplierQuotationViewSet` | `list`, `retrieve` | `procurement.view` |
| | `create`, `update`, `destroy` | `procurement.manage_quotations` |
| `ValidationRuleSetViewSet` | `list`, `retrieve` | `procurement.view` |
| `AnalysisRunValidationView` | `retrieve` | `procurement.view_results` |

#### Template Views (`apps/procurement/template_views.py`)

All views use `@login_required` + `@permission_required_code()`:

| View | Permission |
|---|---|
| `request_list` | `procurement.view` |
| `request_create` | `procurement.create` |
| `request_workspace` | `procurement.view` |
| `run_detail` | `procurement.view_results` |
| `trigger_analysis` | `procurement.run_analysis` |
| `mark_ready` | `procurement.edit` |
| `upload_quotation` | `procurement.manage_quotations` |
| `trigger_validation` | `procurement.validate` |

#### Sidebar Navigation (`templates/partials/sidebar.html`)

The Procurement sidebar section is gated with `{% has_permission "procurement.view" %}` — only visible to users with the `procurement.view` permission.

---

## 13. File Organization

```
apps/procurement/
├── __init__.py
├── apps.py                    # AppConfig: "Procurement Intelligence"
├── admin.py                   # Admin registration with inlines
├── models.py                  # 15 models (Request, Attribute, Quotation, LineItem,
│                              #   AnalysisRun, RecommendationResult, BenchmarkResult,
│                              #   BenchmarkResultLine, ComplianceResult,
│                              #   ValidationRuleSet, ValidationRule,
│                              #   ValidationResult, ValidationResultItem,
│                              #   MarketIntelligenceSuggestion, ExternalSourceRegistry)
├── serializers.py             # 17 DRF serializers (list/detail/write per model)
├── views.py                   # 4 DRF ViewSets + nested actions
├── api_urls.py                # DRF router → /api/v1/procurement/
├── template_views.py          # 8 template views (list, create, workspace, detail, actions)
├── urls.py                    # Template URLs → /procurement/
├── tasks.py                   # Celery tasks: run_analysis_task, run_validation_task
├── agents/
│   ├── __init__.py
│   ├── recommendation_agent.py      # AI recommendation agent
│   ├── benchmark_agent.py           # AI benchmark resolution agent
│   ├── compliance_agent.py          # AI compliance check agent
│   ├── quotation_extraction_agent.py # AI quotation data extraction (OCR text → structured JSON)
│   └── request_extraction_agent.py   # AI request/SOW data extraction
├── services/
│   ├── __init__.py
│   ├── request_service.py      # ProcurementRequestService + AttributeService
│   ├── quotation_service.py    # QuotationService + LineItemNormalizationService
│   ├── analysis_run_service.py # AnalysisRunService (lifecycle)
│   ├── recommendation_service.py # RecommendationService (full flow)
│   ├── benchmark_service.py    # BenchmarkService (full flow)
│   ├── compliance_service.py   # ComplianceService (rule-based)
│   ├── market_intelligence_service.py  # MarketIntelligenceService (Perplexity + OpenAI)
│   ├── prefill/                # Quotation Prefill Extraction Pipeline
│   │   ├── __init__.py
│   │   ├── quotation_prefill_service.py  # QuotationDocumentPrefillService (OCR → LLM → mapping → payload)
│   │   ├── attribute_mapping_service.py  # AttributeMappingService (field synonym resolution + line item mapping)
│   │   ├── prefill_status_service.py     # PrefillStatusService (status transitions + payload persistence)
│   │   ├── prefill_review_service.py     # PrefillReviewService (user confirmation → QuotationLineItem creation)
│   │   └── request_prefill_service.py    # RequestDocumentPrefillService (SOW/RFQ attribute extraction)
│   └── validation/             # Validation Framework services
│       ├── __init__.py
│       ├── rule_resolver_service.py        # Rule resolution by domain/schema
│       ├── attribute_completeness_service.py # REQUIRED_ATTRIBUTE checks
│       ├── document_completeness_service.py  # REQUIRED_DOCUMENT checks
│       ├── scope_coverage_service.py         # REQUIRED_CATEGORY scope checks
│       ├── ambiguity_service.py              # Ambiguity pattern detection
│       ├── commercial_completeness_service.py # Commercial term checks
│       ├── compliance_readiness_service.py   # Compliance readiness checks
│       ├── orchestrator_service.py           # ValidationOrchestratorService
│       └── validation_agent.py               # LLM augmentation for ambiguity
└── migrations/
    ├── __init__.py
    ├── 0001_initial.py                        # Initial migration (9 tables)
    ├── 0002_add_validation_framework.py        # Validation models (4 tables)
    └── 0003_add_market_intelligence.py          # MarketIntelligenceSuggestion + ExternalSourceRegistry

templates/procurement/
├── request_list.html          # Filterable list with status badges
├── request_create.html        # Dynamic attribute form
├── request_workspace.html     # Full workspace (summary, results, timeline)
├── run_detail.html            # Analysis run detail (input/output/audit)
├── market_intelligence.html   # AI market intelligence suggestions + 3-link dropdown
└── partials/
    └── validation_summary.html # Validation results partial (status, score, findings)
```

### Integration Points in Existing Files

| File | Change |
|---|---|
| `config/settings.py` | Added `"apps.procurement"` to `INSTALLED_APPS` |
| `config/urls.py` | Added `path("procurement/", ...)` and `path("api/v1/procurement/", ...)` |
| `apps/core/enums.py` | Added 8 base enum classes + 8 validation enum classes (17 total including `VALIDATION` in AnalysisRunType) |
| `templates/partials/sidebar.html` | Added "Procurement" nav section |

### Database Tables Created

| Table Name | Model |
|---|---|
| `procurement_request` | ProcurementRequest |
| `procurement_request_attribute` | ProcurementRequestAttribute |
| `procurement_supplier_quotation` | SupplierQuotation |
| `procurement_quotation_line_item` | QuotationLineItem |
| `procurement_analysis_run` | AnalysisRun |
| `procurement_recommendation_result` | RecommendationResult |
| `procurement_benchmark_result` | BenchmarkResult |
| `procurement_benchmark_result_line` | BenchmarkResultLine |
| `procurement_compliance_result` | ComplianceResult |
| `procurement_validation_rule_set` | ValidationRuleSet |
| `procurement_validation_rule` | ValidationRule |
| `procurement_validation_result` | ValidationResult |
| `procurement_validation_result_item` | ValidationResultItem |
| `procurement_market_intelligence_suggestion` | MarketIntelligenceSuggestion |
| `procurement_external_source_registry` | ExternalSourceRegistry |

---

## 14. Status Transitions

### ProcurementRequest Status Flow

```
DRAFT ──[mark_ready]──> READY ──[trigger_analysis]──> PROCESSING
                                                          │
                          ┌───────────────────────────────┤
                          │               │               │
                     COMPLETED    REVIEW_REQUIRED       FAILED
                          │               │
                          └───[re-run]────┘──> PROCESSING (re-analysis)
```

| Transition | Trigger | Condition |
|---|---|---|
| DRAFT → READY | `mark_ready()` | All required attributes have values |
| READY → PROCESSING | `run_analysis_task` | Task dispatched |
| PROCESSING → COMPLETED | Service completion | Risk ≤ MEDIUM, compliance not FAIL, or validation PASS |
| PROCESSING → REVIEW_REQUIRED | Service completion | Risk = HIGH/CRITICAL, or compliance = FAIL, or validation REVIEW_REQUIRED |
| PROCESSING → FAILED | Service failure | Exception during analysis, or validation FAIL |
| COMPLETED/REVIEW_REQUIRED → PROCESSING | Re-trigger analysis | User manually re-runs |

### AnalysisRun Status Flow

```
QUEUED ──[start_run]──> RUNNING ──[complete_run]──> COMPLETED
                                 ──[fail_run]────> FAILED
```

---

## 15. Flow Walkthroughs

### Flow 1: Product / Solution Recommendation

```
Step 1: User creates ProcurementRequest via UI or API
        → ProcurementRequestService.create_request()
        → Status: DRAFT
        → AuditEvent: PROCUREMENT_REQUEST_CREATED

Step 2: User defines attributes (requirements)
        → AttributeService.bulk_set_attributes()

Step 3: User clicks "Mark Ready"
        → ProcurementRequestService.mark_ready()
        → Validates required attributes
        → Status: READY
        → AuditEvent: PROCUREMENT_REQUEST_STATUS_CHANGED

Step 4: User clicks "Run Analysis" with type=RECOMMENDATION
        → AnalysisRunService.create_run(run_type="RECOMMENDATION")
        → run_analysis_task.delay(run.pk)
        → Status: PROCESSING

Step 5: Celery task executes:
        → AnalysisRunService.start_run()
        → AttributeService.get_attributes_dict()
        → RecommendationService._apply_rules()  ← Deterministic first
        │
        ├── If rules confident=True:
        │   → Use rule result directly
        │
        └── If rules confident=False and use_ai=True:
            → RecommendationAgent.execute()  ← LLM call
            → Returns structured recommendation JSON

Step 6: ComplianceService.check_recommendation()
        → Checks: recommendation present, confidence ≥ 0.5, budget
        → Returns compliance status

Step 7: Persist results in transaction:
        → RecommendationResult.objects.create()
        → ComplianceResult.objects.create()

Step 8: AnalysisRunService.complete_run()
        → AuditEvent: ANALYSIS_RUN_COMPLETED

Step 9: ProcurementRequestService.update_status()
        → If compliance PASS/PARTIAL → COMPLETED
        → If compliance FAIL → REVIEW_REQUIRED
        → AuditEvent: PROCUREMENT_REQUEST_STATUS_CHANGED
```

### Flow 2: Should-Cost Benchmarking

```
Step 1: User creates ProcurementRequest
        → Status: DRAFT

Step 2: User adds SupplierQuotation(s)
        → QuotationService.create_quotation()
        → AuditEvent: QUOTATION_UPLOADED

Step 3: Line items are added to quotation
        → QuotationService.add_line_items()
        → LineItemNormalizationService.normalize_line_items()

Step 4: User clicks "Run Analysis" with type=BENCHMARK
        → AnalysisRunService.create_run(run_type="BENCHMARK")
        → run_analysis_task.delay(run.pk)
        → Status: PROCESSING

Step 5: Celery task executes:
        BenchmarkService.run_benchmark(request, run, quotation)
        --
        AnalysisRunService.start_run()
        ProcurementAgentMemory()  <- shared across all per-line agent calls
        --
        For each QuotationLineItem:
        |
        +--> _resolve_benchmark(item)  [3-tier priority]
        |     |
        |     +--> Priority 1: Internal catalogue DB   (Phase 2 stub -- not yet implemented)
        |     |
        |     +--> Priority 2: BenchmarkAgent via ProcurementAgentOrchestrator
        |     |     agent_type = "benchmark_item_{item.pk}"
        |     |     -> LLMClient.chat([system, user])
        |     |     -> AgentRun + AgentStep records created in DB
        |     |     -> result stored in memory.benchmark_findings
        |     |     -> returns {min, avg, max, source="ai_estimate", reasoning}
        |     |
        |     +--> Priority 3: WebSearchService.search_benchmark()  (non-blocking)
        |     |     1. DuckDuckGo Instant Answer API (free, no key)
        |     |     2. Bing search scrape fallback
        |     |     -> returns {min, avg, max, source="WEB_SEARCH", confidence=0.35}
        |     |
        |     +--> Priority 4: no-data fallback
        |           -> returns {min: null, avg: null, max: null, source="none"}
        |
        +--> _compute_variance(item, benchmark)
              -> pct = (quoted_unit_rate - benchmark_avg) / benchmark_avg * 100
              -> status = WITHIN_RANGE | ABOVE_BENCHMARK | BELOW_BENCHMARK | SIGNIFICANTLY_ABOVE
              -> remarks = notes from the source (or "No benchmark data available")

Step 6: Aggregate results:
        total_quoted    = sum(line.total_amount for all lines)
        total_benchmark = sum(benchmark_avg * line.quantity for all lines)
        overall_variance_pct = (total_quoted - total_benchmark) / total_benchmark * 100

Step 7: BenchmarkService._classify_risk(overall_variance_pct)
        abs <= 5%  -> LOW
        abs <= 15% -> MEDIUM
        abs <= 30% -> HIGH
        abs >  30% -> CRITICAL

Step 8: Atomic DB transaction:
        BenchmarkResult.objects.create(
            run, quotation, total_quoted_amount, total_benchmark_amount,
            variance_pct, risk_level,
            summary_json={line_count, total_quoted, total_benchmark, variance_pct}
        )
        BenchmarkResultLine.objects.bulk_create([
            BenchmarkResultLine(benchmark_result, quotation_line,
                benchmark_min, benchmark_avg, benchmark_max,
                quoted_value, variance_pct, variance_status, remarks)
            for each line
        ])

Step 9: AnalysisRunService.complete_run(run,
            output_summary="Benchmark complete: {risk_level} risk, {variance_pct:.1f}% variance",
            confidence_score=0.8 if not use_ai else None,
        )
        AuditEvent: ANALYSIS_RUN_COMPLETED

Step 10: ProcurementRequestService.update_status()
         risk LOW or MEDIUM  -> COMPLETED
         risk HIGH or CRITICAL -> REVIEW_REQUIRED
```

### Flow 3: Validation

```
Step 1: User creates ProcurementRequest and defines attributes/quotations
        → Status: DRAFT or READY

Step 2: User clicks "Run Analysis" with type=VALIDATION
        → AnalysisRunService.create_run(run_type="VALIDATION")
        → run_validation_task.delay(run.pk) (or via validate action)
        → Status: PROCESSING

Step 3: Celery task executes:
        → ValidationOrchestratorService.run_validation(request, run)
        │
        ├── Step 3a: Resolve rules
        │   → ValidationRuleResolverService.resolve_rules_for_request()
        │   → Matches rules by domain_code + schema_code (specific → generic)
        │
        ├── Step 3b: Run 6 deterministic validators
        │   → AttributeCompletenessValidationService.validate()
        │   → DocumentCompletenessValidationService.validate()
        │   → ScopeCoverageValidationService.validate()
        │   → AmbiguityValidationService.validate()
        │   → CommercialCompletenessValidationService.validate()
        │   → ComplianceReadinessValidationService.validate()
        │
        ├── Step 3c: Optional agent augmentation
        │   → If agent_enabled AND ambiguous_count >= 3:
        │     → ValidationAgentService.augment_findings()  ← LLM call
        │
        └── Step 3d: Score and classify
            → _compute_completeness_score() (severity-weighted)
            → _determine_overall_status() (PASS/PASS_WITH_WARNINGS/REVIEW_REQUIRED/FAIL)
            → _determine_readiness() (recommendation/benchmarking readiness)
            → _determine_next_action()

Step 4: Persist in transaction:
        → ValidationResult.objects.create(overall_status, score, summary, ...)
        → ValidationResultItem.objects.bulk_create(all findings)

Step 5: AnalysisRunService.complete_run()
        → AuditEvent: ANALYSIS_RUN_COMPLETED

Step 6: AuditService.log_event(VALIDATION_COMPLETED)
        → AuditEvent with completeness_score, missing/warning/ambiguous counts

Step 7: run_validation_task updates request status:
        → If PASS → READY
        → If FAIL → FAILED
        → If REVIEW_REQUIRED → REVIEW_REQUIRED
```

---

## 16. Configuration & Extension Points

### Adding a New Domain

1. No code changes needed — create requests with a new `domain_code` (e.g. `"ELECTRICAL"`)
2. Optionally set `schema_code` to define domain-specific attribute templates

### Adding Deterministic Rules

Extend `RecommendationService._apply_rules()`:

```python
@staticmethod
def _apply_rules(request, attrs):
    if request.domain_code == "IT":
        if attrs.get("compute_type") == "GPU" and attrs.get("budget") > 50000:
            return {
                "recommended_option": "NVIDIA A100 Cluster",
                "reasoning_summary": "GPU compute with sufficient budget matches A100",
                "confident": True,
                "constraints": ["budget", "compute_type"],
            }
    # ... fallback to AI
    return {"confident": False, ...}
```

### Adding Benchmark Data Sources

The benchmark resolution chain is implemented in `BenchmarkService._resolve_benchmark()`. Priority 1 (internal catalogue) is a Phase 2 stub -- add deterministic lookup here to bypass the LLM entirely for known item categories:

```python
@staticmethod
def _resolve_benchmark(item, *, run, memory, use_ai, request_user):
    # Priority 1: add deterministic catalogue lookup here
    # from myapp.benchmarks import PriceCatalog
    # result = PriceCatalog.lookup(item.category_code, item.normalized_description)
    # if result:
    #     return {"min": result.p10, "avg": result.p50,
    #             "max": result.p90, "source": "catalog"}

    # Priority 2: AI via orchestrator (already implemented)
    if use_ai and run is not None:
        ...

    # Priority 3: WebSearchService (already implemented, non-blocking)
    ...

    # Priority 4: no-data fallback
    return {"min": None, "avg": None, "max": None, "source": "none"}
```

The `source` field value in the returned dict propagates into `BenchmarkResultLine.remarks` so the UI and audit logs can always show whether a line was benchmarked via catalogue, AI, web search, or had no data at all.

### Adding New Compliance Rules

Add rules in `ComplianceService.check_recommendation()` or `check_benchmark()`:

```python
# Example: geography-based compliance
rules_checked.append({"rule": "geo_restriction", "description": "Vendor must be in approved countries"})
if request.geography_country in RESTRICTED_COUNTRIES:
    violations.append({"rule": "geo_restriction", "detail": f"{request.geography_country} is restricted"})
```

### Adding RBAC Permissions

```bash
# Via seed_rbac or Django admin, add permissions:
procurement.view       → All roles
procurement.create     → AP_PROCESSOR, ADMIN, FINANCE_MANAGER
procurement.manage     → ADMIN, FINANCE_MANAGER
procurement.run_analysis → AP_PROCESSOR, ADMIN, FINANCE_MANAGER
```

### Adding New Analysis Run Types

1. Add enum value to `AnalysisRunType` in `apps/core/enums.py`
2. Create new service in `apps/procurement/services/`
3. Add dispatch branch in `run_analysis_task`
4. Create result model if needed
5. Add UI section in workspace template

**Example**: The `VALIDATION` run type was added following this pattern — see `ValidationOrchestratorService`, `run_validation_task`, and `validation_summary.html`.

### Adding Validation Rules

Validation rules are data-driven via the `ValidationRuleSet` + `ValidationRule` models:

1. Create a `ValidationRuleSet` via Django admin or API with `domain_code`, `schema_code`, and `validation_type`
2. Add `ValidationRule` records with `rule_type` matching the validation dimension:
   - `REQUIRED_ATTRIBUTE` → checked by `AttributeCompletenessValidationService`
   - `REQUIRED_DOCUMENT` → checked by `DocumentCompletenessValidationService`
   - `REQUIRED_CATEGORY` → checked by `ScopeCoverageValidationService`
   - `AMBIGUITY_PATTERN` → additional patterns for `AmbiguityValidationService`
   - `COMMERCIAL_CHECK` → additional terms for `CommercialCompletenessValidationService`
   - `COMPLIANCE_CHECK` → compliance checks for `ComplianceReadinessValidationService`
3. Set `severity` (INFO/WARNING/ERROR/CRITICAL) to control scoring impact
4. Set `condition_json` for rule-specific parameters (e.g. `{"attribute_code": "budget"}` for REQUIRED_ATTRIBUTE)
5. Rules are automatically resolved for matching requests via `ValidationRuleResolverService`

### Integration with Existing Document Extraction

`SupplierQuotation.uploaded_document` links to `apps.documents.DocumentUpload`. The quotation extraction pipeline operates independently from the invoice extraction pipeline:

1. Upload quotation/proposal PDF via `quotation_prefill` API → creates `DocumentUpload` + `SupplierQuotation`
2. Async `run_quotation_prefill_task` triggers `QuotationDocumentPrefillService.run_prefill()`
3. OCR via Azure Document Intelligence (reuses `InvoiceExtractionAdapter._ocr_document()`)
4. LLM extraction via `QuotationDocumentPrefillService._extract_quotation_data()` (GPT-4o, up to 60K chars)
5. Field mapping via `AttributeMappingService.map_quotation_fields()` → stores `prefill_payload_json`
6. User reviews and confirms → `PrefillReviewService.confirm_quotation_prefill()` persists `QuotationLineItem` records
7. `extraction_status` and `extraction_confidence` updated at each stage

---

## 17. Market Intelligence

### Overview

The Market Intelligence feature provides real-time AI-generated product suggestions for a procurement request. It answers the question: *"Given the specifications in this request, what commercial products are available, and what do they cost?"*

The primary source is the **Perplexity sonar-pro** live-web-search API. When Perplexity is unavailable, fails, or returns zero suggestions, the system automatically falls back to `FallbackWebscraperAgent` which uses **Azure OpenAI** (to select vendor/marketplace URLs) + **Playwright** (to scrape those pages) + **Azure OpenAI** (to parse the scraped content into structured suggestions). Both paths produce the same result dict and persist to `MarketIntelligenceSuggestion`.

### Architecture

```
Procurement Request
  |
  v
POST /procurement/{id}/external-suggestions/
  |
  v
MarketIntelligenceService.generate_auto(proc_request)
  |
  +--[PERPLEXITY_API_KEY set]--> generate_with_perplexity()
  |     |                           |
  |     |  PerplexityMarket-        +--> Resolve system_code via _SYSTEM_CODE_TO_DB_NAME
  |     |  ResearchAnalystAgent     +--> Query ExternalSourceRegistry (source hints)
  |     |                           +--> Build prompt (vendor/seller page priority)
  |     |                           +--> POST to Perplexity sonar-pro API
  |     |                           +--> Receive citations[] (real visited URLs)
  |     |                           +--> _parse_json() -- strips fences, handles
  |     |                           |     dangling ``` / leading prose / trailing text
  |     |                           +--> Parse 5-7 JSON suggestions
  |     |                           +--> Citation-index resolution per suggestion:
  |     |                                 citation_url    = citations[citation_index]
  |     |                                 price_source_url = citations[price_citation_index]
  |     |
  |     +--> returned suggestions > 0?  YES --> use result
  |     |                               NO  --> fall through to FallbackWebscraperAgent
  |     |
  |     +--> any exception? -----------YES --> fall through to FallbackWebscraperAgent
  |
  +--[no PERPLEXITY_API_KEY] ---------------------> FallbackWebscraperAgent
  |                                                   |
  |  FallbackWebscraperAgent.run()                    |
  |    Step 1: Azure OpenAI                           +--> selects 6 vendor/marketplace
  |             site-selection prompt                 |    URLs (Alibaba, IndiaMART,
  |                                                   |    UAE distributors, etc.)
  |    Step 2: Playwright (headless Chromium)         +--> visits each URL, strips
  |             page scraper                         |    nav/scripts, captures body text
  |                                                   |
  |    Step 3: Azure OpenAI                           +--> parses scraped text into
  |             product-parser prompt                      5-7 JSON suggestions
  |
  v
Persist MarketIntelligenceSuggestion
  |
  v
GET /procurement/{id}/market-intelligence/
  |
  v
market_intelligence.html (product table + 3-link dropdown)
```

### Fallback Decision Logic

`MarketIntelligenceService.generate_auto()` applies this decision tree:

```
PERPLEXITY_API_KEY present?
  YES -> call Perplexity
           returned suggestions > 0?  YES -> use result and return
           returned 0 suggestions?    -> log warning -> FALLBACK
           any exception?             -> log warning -> FALLBACK
  NO  -> skip Perplexity entirely     -> FALLBACK

FALLBACK (FallbackWebscraperAgent)
  success?     -> return result
  also fails?  -> raise ValueError listing both error messages
```

### ExternalSourceRegistry

`ExternalSourceRegistry` is the gatekeeper for which external domains the service is allowed to use. Each record represents one approved brand source for a given HVAC system type.

**Purpose**:
- Provides the `search_domain_filter` list sent to Perplexity (restricts results to approved manufacturer sites)
- Provides `domain_to_url` mapping used to resolve `brand_page_url` (guaranteed-live fallback URLs)
- Prevents the service from citing unknown or untrusted domains

**Fields**: `hvac_system_type`, `equipment`, `source_name`, `domain`, `source_url`, `source_type`, `country_scope`, `priority`, `trust_score`, `allowed_for_discovery`, `allowed_for_compliance`, `fetch_mode`, `is_active`, `notes`. See Section 3.16 for the full field table.

**`_SYSTEM_CODE_TO_DB_NAME` lookup**: Because `hvac_system_type` stores free-text names (e.g. `"VRF System"`), the service translates the recommendation `system_code` (e.g. `"VRF"`) via `_SYSTEM_CODE_TO_DB_NAME` before querying.

**Seeding**: Active sources are seeded by `python manage.py seed_configurations`. See Section 3.16 for the mapping dict and seed details.

**Extending for new HVAC types**: Add `ExternalSourceRegistry` records with the appropriate `hvac_system_type` name (e.g. `"Chilled Water System"`) via Django admin or updated `seed_configurations.py`. No code changes needed; `MarketIntelligenceService` dynamically filters by the mapped name.

### Citation-Index URL Resolution

The Perplexity API returns a top-level `citations` list alongside the LLM response. These are the real URLs Perplexity fetched and verified during the live search session. The service uses integer indices (returned by the LLM in `citation_index` and `price_citation_index`) to look up actual URLs from this list:

```python
# citation_url: LLM returns citation_index=2 -> use perplexity_citations[2]
cit_idx = s.get("citation_index")   # e.g. 2
s["citation_url"] = perplexity_citations[cit_idx]  # real Perplexity URL

# price_source_url: LLM returns price_citation_index=4 -> use perplexity_citations[4]
#   or -1 if no price page found
price_idx = s.get("price_citation_index")  # e.g. 4 or -1
s["price_source_url"] = (
    perplexity_citations[price_idx] if 0 <= price_idx < len(perplexity_citations) else ""
)
```

**No HTTP liveness checks are performed.** Because:
- Perplexity already verified all `citations[]` URLs during live search. Re-checking them from Python is redundant.
- Major OEM manufacturer sites (Daikin, Mitsubishi, LG, Samsung) are JavaScript SPAs that return `4xx` to Python bots even when the page is live. A server-side GET would wrongly discard valid product-page links.
- Using LLM-returned integer indices instead of LLM-generated URL strings eliminates hallucinated or echoed-back URL paths entirely.

**Fallback chain for `citation_url`**: `perplexity_citations[citation_index]` -> (if out of range) `perplexity_citations[0]` -> (if no citations) first `ExternalSourceRegistry.source_url` for the system type.

### Prompt Design

#### Source priority -- vendor/seller pages first

The prompt instructs Perplexity to search for and return ONLY pages from commercial selling sources, in this priority order:

1. **B2B marketplace seller listings** (highest priority) -- Alibaba product page with supplier contact, IndiaMART listing with "Send Enquiry", Tradeindia, made-in-china.com
2. **UAE/GCC HVAC distributor or dealer pages** -- local supplier sites with contact/WhatsApp/enquiry form
3. **Manufacturer product pages ONLY** if they include a live "Get Quote", "Find Dealer", or "Buy Now" form

Brand homepages, spec-sheet-only pages, news articles, and energy-rating databases are explicitly forbidden as `citation_url` values. Every suggested link must be a page where the buyer can contact a seller.

`price_source_url` must be the page where the price is actually printed -- the buyer will open it manually to verify. Assumed prices are not acceptable.

#### Why the `sources_block` omits URL paths

Early prompt versions included full registry paths (e.g. `daikin.com/products/ac/commercial/vrv/`) in the prompt sources block. Perplexity echoed those paths back as `citation_url` even when the pages returned 404. The fix:

```python
# WRONG (old): prompted with full URL -- Perplexity echoes it back unchanged
sources_block = f"| {src.source_name} | {src.source_url} |\n"

# CORRECT (now): prompt with brand + domain only -- Perplexity discovers real URLs
sources_block = f"  - {src['source_name']} | domain: {src['domain']}\n"
```

Perplexity then performs live web search against the approved domains and returns real, crawled product URLs in the `citations[]` array. The LLM returns integer `citation_index` values referencing those real URLs.

#### `_parse_json` resilience

Perplexity occasionally returns a response that opens with ` ```json ` but has no closing ` ``` ` (truncated /streaming response). The `_parse_json` static method in `PerplexityMarketResearchAnalystAgent` applies a 4-step recovery chain:

1. Look for a complete ` ```json ... ``` ` fence and extract its contents.
2. If no complete fence, strip a dangling opening ` ```json ` prefix with `re.sub`.
3. If there is still non-JSON leading prose, scan forward to the first `{` or `[`.
4. Trim trailing content beyond the last `}` or `]`.

This makes the parser tolerant of incomplete responses from Perplexity without masking real errors.

### 3-Link UI Pattern

The expanded detail row in `market_intelligence.html` shows up to 3 purposefully labelled link buttons:

```
[  Product Page (blue)  ]  [  Price Source (green)  ]  [  Brand Site (grey)  ]
```

| Button | Field | When shown |
|---|---|---|
| **Product Page** | `suggestion.citation_url` | When `citation_url` is set AND differs from `brand_page_url` |
| **Price Source** | `suggestion.price_source_url` | When `price_source_url` is non-null |
| **Brand Site** | `suggestion.brand_page_url` | Always (registry-verified, always live) |

**Deduplication**: `citation_url` may equal `brand_page_url` when the citation-index resolves to the same registry landing page (e.g. the only approved citation for that brand was the homepage). The deduplication condition (`citation_url != brand_page_url`) prevents showing the same URL twice under two different labels.

**Server-rendered template snippet** (expandable row):

```html
{% if s.citation_url and s.citation_url != s.brand_page_url %}
<a href="{{ s.citation_url }}" class="btn btn-sm btn-outline-primary">
  <i class="bi bi-file-earmark-text me-1"></i>Product Page
</a>
{% endif %}
{% if s.price_source_url %}
<a href="{{ s.price_source_url }}" class="btn btn-sm btn-outline-success">
  <i class="bi bi-tag me-1"></i>Price Source
</a>
{% endif %}
{% if s.brand_page_url %}
<a href="{{ s.brand_page_url }}" class="btn btn-sm btn-outline-secondary">
  <i class="bi bi-house me-1"></i>Brand Site
</a>
{% endif %}
```

The JavaScript `buildSuggestions()` function in the template uses the same deduplication logic for dynamically rendered rows.

### FallbackWebscraperAgent

**File**: `apps/procurement/agents/Fallback_Webscraper_Agent.py`

Used automatically by `MarketIntelligenceService.generate_auto()` when Perplexity is unavailable, fails, or returns zero suggestions. Produces the same dict shape as `PerplexityMarketResearchAnalystAgent.run()` so no view or template changes are needed.

**Three-step pipeline:**

| Step | What happens |
|---|---|
| **1 -- Site selection** | Azure OpenAI receives the procurement request details and returns exactly 6 concrete vendor/marketplace URLs to visit (Alibaba search pages, IndiaMART listings, UAE HVAC dealer pages, etc.). If Azure OAI returns unusable data, a hardcoded Alibaba/IndiaMART/Tradeindia/made-in-china fallback URL list is used. |
| **2 -- Playwright scraping** | Headless Chromium (Playwright `sync_api`) visits each URL. Scripts/nav/header/footer/iframe elements are removed from the DOM, then `innerText` is captured. Each page is limited to 6,000 chars. Pages that time out, fail to navigate, or return < 80 chars of text are silently skipped. |
| **3 -- Product parsing** | All scraped page texts are sent to Azure OpenAI with a structured extraction prompt. Azure OAI returns 5-7 product suggestions in the same JSON schema as the Perplexity agent (`citation_url` set to the scraped page URL, `price_source_url` to wherever price was found). |

**Requirements** (one-time setup):
```
pip install playwright
playwright install chromium
```

**`_extract_json()` helper** applies the same 4-step fence-stripping logic as `_parse_json` in the Perplexity agent, making it tolerant of incomplete or fence-wrapped Azure OAI responses.

**`perplexity_citations` key** in the returned dict contains the scraped page URLs (same key as the Perplexity path -- all callers treat both identically).

---

### Configuration

| Setting | Default | Description |
|---|---|---|
| `PERPLEXITY_API_KEY` | (none) | Primary path. If set, `generate_auto` tries Perplexity first. |
| `PERPLEXITY_MODEL` | `"sonar-pro"` | Perplexity model name. `sonar-pro` is the enhanced live-search model. |
| `AZURE_OPENAI_ENDPOINT` | (none) | Required for `FallbackWebscraperAgent` (site selection + product parsing). |
| `AZURE_OPENAI_API_KEY` | (none) | Required for `FallbackWebscraperAgent`. |
| `AZURE_OPENAI_DEPLOYMENT` | `"gpt-4o"` | Azure OAI deployment used by the fallback agent. |
| `AZURE_OPENAI_API_VERSION` | `"2024-02-01"` | Azure OAI API version used by the fallback agent. |

### Adding Sources for a New HVAC System Type

1. Add `ExternalSourceRegistry` records via Django admin or update `seed_configurations.py`:
   ```python
   {
       "hvac_system_type": "Chilled Water System",  # must match DB label
       "source_name": "Carrier Chillers",
       "domain": "carrier.com",
       "source_url": "https://www.carrier.com/commercial/en/us/products/chillers/",
       "source_type": "OEM_OFFICIAL",
       "is_active": True,
       "priority": 1,
       "allowed_for_discovery": True,
   }
   ```
2. Add the new system-code -> DB-label mapping to `_SYSTEM_CODE_TO_DB_NAME` in `market_intelligence_service.py`.
3. Ensure procurement requests use a `domain_code` that maps to the new system code.
4. No other code changes needed -- `MarketIntelligenceService` dynamically filters `ExternalSourceRegistry` by `hvac_system_type`.

### Database Tables

| Table | Model | Description |
|---|---|---|
| `procurement_market_intelligence_suggestion` | `MarketIntelligenceSuggestion` | Generated suggestions per request |
| `procurement_external_source_registry` | `ExternalSourceRegistry` | Approved brand sources by system type |

### Known Limitations

- **No `search_domain_filter`**: The Perplexity call does not restrict to approved domains. The hint block lists approved registry sources as suggestions, but Perplexity is free to search any site. This gives better real-world product coverage (B2B marketplaces, UAE distributors) than a locked domain list.
- **`price_source_url` may be blank on B2B sites**: Many marketplace pages require login to show price. The prompt instructs the model to use `~X AED est.` when price is behind a login wall, and to set `price_source_url` to the seller listing page so the buyer can contact for price.
- **Playwright requires a one-time install**: `pip install playwright && playwright install chromium`. If not installed, the fallback agent raises `ImportError` with installation instructions. The primary Perplexity path is not affected.
- **Playwright may be blocked**: Some sites (Alibaba, IndiaMART) serve bot-detection pages. The scraper uses a realistic Chrome user-agent and waits 2.5s for JS rendering, which is sufficient for most listing pages. Pages returning < 80 chars of body text are silently skipped.
- **No HTTP liveness check on Perplexity URLs**: `citation_url` and `price_source_url` are resolved by integer index from Perplexity's `citations[]` array -- pages Perplexity actually visited. Server-side GET checks are intentionally omitted: OEM JavaScript SPAs (Daikin, Mitsubishi, LG) return 4xx to Python bots but load correctly in a browser.

---

## Appendix: Existing Platform Dependencies

The procurement module depends on these existing platform services:

| Dependency | Module | Purpose |
|---|---|---|
| `BaseModel` | `apps.core.models` | Timestamp + audit field inheritance |
| `TimestampMixin` | `apps.core.models` | Lightweight timestamp inheritance |
| `AuditService` | `apps.auditlog.services` | Business event logging |
| `AuditEvent` | `apps.auditlog.models` | Audit record storage |
| `ProcessingLog` | `apps.auditlog.models` | Operational log storage |
| `TraceContext` | `apps.core.trace` | Distributed tracing |
| `@observed_service` | `apps.core.decorators` | Service method tracing |
| `@observed_task` | `apps.core.decorators` | Celery task tracing |
| `LLMClient` | `apps.agents.services.llm_client` | Azure OpenAI / OpenAI API client |
| `LLMMessage` | `apps.agents.services.llm_client` | Message format for LLM calls |
| `DocumentUpload` | `apps.documents.models` | Document storage (FK from SupplierQuotation) |
| `HasPermissionCode` | `apps.core.permissions` | DRF permission class |
| `@login_required` | `django.contrib.auth` | Template view authentication |
| `LoginRequiredMiddleware` | `apps.core.middleware` | Global authentication enforcement |
| `RBACMiddleware` | `apps.core.middleware` | Permission cache pre-loading |
| `RequestTraceMiddleware` | `apps.core.middleware` | Root TraceContext creation per request |
| `DjangoFilterBackend` | `django_filters` | API filtering |
| `Celery` | `config.celery` | Async task execution |
