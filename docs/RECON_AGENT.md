# Reconciliation + Agent Pipeline — Comprehensive Reference

**App paths:** `apps/reconciliation/` + `apps/agents/` + `apps/tools/`
**Dependencies:** `apps/documents/`, `apps/erp_integration/`, `apps/reviews/`, `apps/auditlog/`, `apps/core/`
**Status:** Production-ready -- deterministic engine (2-way + 3-way), LLM agent pipeline (8 agent types), ERP-backed source resolution, full RBAC enforcement, Langfuse tracing.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Two-Layer Architecture](#2-two-layer-architecture)
3. [End-to-End Data Flow](#3-end-to-end-data-flow)
4. [Reconciliation Runner Service](#4-reconciliation-runner-service)
5. [Mode Resolution (3-Tier Cascade)](#5-mode-resolution-3-tier-cascade)
6. [PO Lookup Service (ERP-Backed)](#6-po-lookup-service-erp-backed)
7. [Execution Router](#7-execution-router)
8. [Two-Way Match Service](#8-two-way-match-service)
9. [Three-Way Match Service](#9-three-way-match-service)
10. [Tolerance Engine](#10-tolerance-engine)
11. [Classification Service](#11-classification-service)
12. [Exception Builder Service](#12-exception-builder-service)
13. [Result Service and ERP Provenance](#13-result-service-and-erp-provenance)
14. [Agent Orchestrator](#14-agent-orchestrator)
15. [Planning Layer: ReasoningPlanner and PolicyEngine](#15-planning-layer-reasoningplanner-and-policyengine)
16. [Deterministic Resolver](#16-deterministic-resolver)
17. [BaseAgent and the ReAct Loop](#17-baseagent-and-the-react-loop)
18. [Agent Registry: All 8 Agent Types](#18-agent-registry-all-8-agent-types)
19. [Agent Memory](#19-agent-memory)
20. [RBAC and Guardrails Service](#20-rbac-and-guardrails-service)
21. [Tool Registry: All 6 Tools](#21-tool-registry-all-6-tools)
22. [Agent Feedback Loop](#22-agent-feedback-loop)
23. [Data Models](#23-data-models)
24. [Prompt Registry Integration](#24-prompt-registry-integration)
25. [LLM Client Configuration](#25-llm-client-configuration)
26. [Langfuse Observability](#26-langfuse-observability)
27. [Audit Trail and Governance](#27-audit-trail-and-governance)
28. [Configuration Reference](#28-configuration-reference)
29. [File Reference](#29-file-reference)

---

## 1. Overview

The reconciliation and agent system handles the full lifecycle from a raw approved invoice to a final resolution recommendation ready for AP review or auto-closure.

The system is split into two cooperating layers:

- **Deterministic reconciliation engine** (`apps/reconciliation/`): Pure logic, no LLM. Looks up the PO, resolves the reconciliation mode (TWO_WAY vs THREE_WAY), runs line and header matching against configurable tolerance thresholds, classifies the result, and writes structured exceptions to the database.

- **AI agent pipeline** (`apps/agents/`): Eight LLM-backed agents with tool-calling capabilities. The agents investigate exceptions, attempt to recover missing documents, analyse quality issues, determine review routing, and produce case summaries. The pipeline is triggered automatically for every non-MATCHED result.

Key design principles:

- **Mode awareness**: Both layers track whether a reconciliation is `TWO_WAY` or `THREE_WAY`. GRN checks are suppressed in TWO_WAY mode at every level -- exception builder, classification, agent planner, tool calls.
- **ERP-backed source resolution**: PO and GRN data flow through `ERPResolutionService`, which implements a cache -> ERP API -> DB resolution chain. Every resolution carries provenance metadata (source type, confidence, freshness).
- **Fail-closed RBAC**: All agent operations go through `AgentGuardrailsService`. Unknown actors resolve to the SYSTEM_AGENT identity. Deny decisions are always logged as `AuditEvent` records.
- **Full auditability**: Every LLM call, tool invocation, decision, and recommendation is persisted to the database and optionally to Langfuse.

---

## 2. Two-Layer Architecture

```
+---------------------------------------------------------------------------+
|  LAYER 1: DETERMINISTIC RECONCILIATION ENGINE (apps/reconciliation/)      |
|                                                                           |
|  ReconciliationRunnerService                                              |
|    |                                                                      |
|    +--> POLookupService (ERP-backed) ---> PurchaseOrder (ORM)            |
|    |                                                                      |
|    +--> ReconciliationModeResolver                                        |
|    |     policy table -> heuristic keywords -> config default             |
|    |                                                                      |
|    +--> ReconciliationExecutionRouter                                     |
|    |     TwoWayMatchService  |  ThreeWayMatchService                      |
|    |       HeaderMatchService  +  LineMatchService                        |
|    |       (+ GRNLookupService + GRNMatchService in 3-way)               |
|    |                                                                      |
|    +--> ClassificationService --> MatchStatus                             |
|    |                                                                      |
|    +--> ExceptionBuilderService --> ReconciliationException (bulk_create) |
|    |                                                                      |
|    +--> ReconciliationResultService --> ReconciliationResult (DB)         |
|           (+ ReviewAssignment auto-creation for REQUIRES_REVIEW)         |
|                                                                           |
+---------------------------------------------------------------------------+
                                  |
                                  | (for non-MATCHED results)
                                  v
+---------------------------------------------------------------------------+
|  LAYER 2: LLM AGENT PIPELINE (apps/agents/)                              |
|                                                                           |
|  AgentOrchestrator                                                        |
|    |                                                                      |
|    +--> AgentGuardrailsService (RBAC: orchestrate + data-scope)          |
|    |                                                                      |
|    +--> ReasoningPlanner                                                  |
|    |     LLM plan (always) -> PolicyEngine fallback on error              |
|    |                                                                      |
|    +--> Partition: LLM agents | DeterministicResolver tail                |
|    |                                                                      |
|    +--> AgentContext + AgentMemory (shared state)                        |
|    |                                                                      |
|    +--> Execute agents in order                                           |
|    |     BaseAgent.run() -- ReAct loop (max 6 rounds)                    |
|    |       tool call -> ToolRegistry.execute() -> ToolResult             |
|    |                                                                      |
|    +--> AgentFeedbackService (if PO/GRN recovered -> re-reconcile)       |
|    |                                                                      |
|    +--> RecommendationService.create() / AgentEscalation                 |
|                                                                           |
+---------------------------------------------------------------------------+
```

---

## 3. End-to-End Data Flow

```
Invoice (status=READY_FOR_RECON)
  |
  v
ReconciliationRunnerService.run()
  |
  +--> ReconciliationRun created (status=RUNNING)
  |
  +--> For each invoice:
  |     _reconcile_single(run, invoice, lf_trace)
  |
  |     Step 1: POLookupService.lookup(invoice)
  |             ERPResolutionService.resolve_po()
  |             -> POLookupResult (found, po_id, po, erp_source_type, is_stale)
  |
  |     Step 1b: vendor+amount discovery fallback (if no po_number on invoice)
  |              -> backfills invoice.po_number, logs AuditEvent
  |
  |     Step 2: ReconciliationModeResolver.resolve(invoice, po)
  |             -> ModeResolutionResult (mode=TWO_WAY|THREE_WAY, policy_code, reason)
  |             Langfuse span: "mode_resolution"
  |
  |     Step 3: ReconciliationExecutionRouter.execute(invoice, po_result, mode)
  |             -> RoutedMatchOutput (header_result, line_result, grn_result)
  |             Langfuse span: "match_execution"
  |             For THREE_WAY: GRNLookupService.lookup(po)
  |                            GRNMatchService.match(invoice, po, grn_summaries)
  |
  |     Step 4: ClassificationService.classify(...) -> MatchStatus
  |             Decision tree (7 gates, deterministic)
  |
  |     Step 5: ExceptionBuilderService.build(...) -> [ReconciliationException]
  |             Applies mode tag (BOTH | THREE_WAY) to each exception
  |
  |     Step 6: ReconciliationResultService.save(...)
  |             -> ReconciliationResult (DB + ERP provenance fields)
  |             -> ReconciliationResultLine per matched pair
  |             -> bulk_create exceptions
  |             Langfuse span: "result_save"
  |
  |     Step 7: Invoice status -> RECONCILED
  |             ReviewAssignment auto-create (REQUIRES_REVIEW results)
  |             AuditEvent: RECONCILIATION_COMPLETED_SINGLE
  |
  v
ReconciliationRun finalised (COMPLETED, counts)
AuditEvent: RECONCILIATION_COMPLETED
Langfuse trace root closed with match counts

  |
  | (non-MATCHED results -- synchronous or async via Celery)
  v
AgentOrchestrator.execute(result, request_user)
  |
  +--> Guardrails: resolve_actor, authorize_orchestration, authorize_data_scope
  +--> ReasoningPlanner.plan(result) -> AgentPlan
  +--> Duplicate-run guard (reject if RUNNING AgentOrchestrationRun exists)
  +--> AgentOrchestrationRun created (RUNNING)
  +--> AgentContext + AgentMemory built
  |
  +--> For each LLM agent in plan (in priority order):
  |     BaseAgent.run(ctx) -> AgentRun (DB)
  |       GuardrailsService.authorize_agent()
  |       GuardrailsService.authorize_tool() per tool call
  |       ReAct loop: LLM -> tool_calls -> execute -> loop (max 6 rounds)
  |       AgentMemory.record_agent_output()
  |       AgentFeedbackService check (PO_RETRIEVAL, GRN_RETRIEVAL)
  |
  +--> DeterministicResolver tail (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
  |     -- or LLM agents for these, depending on plan
  |
  +--> RecommendationService.create() (dedup guard)
  +--> AgentOrchestrationRun finalised (COMPLETED | PARTIAL | FAILED)
```

---

## 4. Reconciliation Runner Service

**File:** `apps/reconciliation/services/runner_service.py`
**Class:** `ReconciliationRunnerService`

The runner is the entry point for the deterministic reconciliation layer. It is called directly from the `start_reconciliation` template view (synchronously) or from `run_reconciliation_task` Celery task (asynchronously).

### Constructor

```python
ReconciliationRunnerService()
```

On init, all sub-services are instantiated:

| Attribute | Type | Purpose |
|---|---|---|
| `self.config` | `ReconciliationConfig` | Active config (via `get_or_create()`) |
| `self.tolerance` | `ToleranceEngine` | Numeric threshold comparisons |
| `self.po_lookup` | `POLookupService` | PO resolution via ERP layer |
| `self.mode_resolver` | `ReconciliationModeResolver` | TWO_WAY vs THREE_WAY resolution |
| `self.router` | `ReconciliationExecutionRouter` | Dispatches to 2-way or 3-way |
| `self.classifier` | `ClassificationService` | Final match status decision |
| `self.exception_builder` | `ExceptionBuilderService` | Structured exception creation |
| `self.result_service` | `ReconciliationResultService` | DB persistence |

### `run(invoices, triggered_by, lf_trace)` 

```
invoices: Optional[QuerySet]  -- None means "fetch all READY_FOR_RECON"
triggered_by: Optional[User]  -- actor for audit trail
lf_trace: Optional[object]    -- parent Langfuse trace
```

**Sequence:**

1. Fetch `READY_FOR_RECON` invoices (if `invoices=None`).
2. Create `ReconciliationRun(status=RUNNING)`.
3. Open Langfuse root trace (`"reconciliation_run"`).
4. Log `RECONCILIATION_STARTED` `AuditEvent` per invoice.
5. Call `_reconcile_single()` for each invoice; count `matched / partial / unmatched / review / errors`.
6. Finalise `ReconciliationRun(status=COMPLETED, counts, completed_at)`.
7. Close Langfuse trace with counts.
8. Log `RECONCILIATION_COMPLETED` `AuditEvent`.
9. Emit `score_trace("reconciliation_match", ...)` per result.

### `_reconcile_single(run, invoice, lf_trace)`

Runs the 7-step per-invoice pipeline (see Section 3). Each step is wrapped in a `try/except` — a failure in any step records an `ERROR` result and does not abort processing for remaining invoices.

The Langfuse spans opened within `_reconcile_single` are children of the runner's root trace:

| Span name | Step |
|---|---|
| `mode_resolution` | Step 2 |
| `match_execution` | Step 3 |
| `result_save` | Step 6 |

---

## 5. Mode Resolution (3-Tier Cascade)

**File:** `apps/reconciliation/services/mode_resolver.py`
**Class:** `ReconciliationModeResolver`

Determines whether an invoice should be reconciled as `TWO_WAY` (Invoice vs PO) or `THREE_WAY` (Invoice vs PO vs GRN).

### Resolution cascade

```
Tier 1: ReconciliationPolicy lookup (explicit policy rules)
  |
  +--> Policies ordered by (priority ASC, policy_code ASC)
  +--> First matching policy wins
  +--> Matching criteria (all must be met if set):
       vendor_id, item_category, location_code, business_unit,
       is_service_invoice (bool), is_stock_invoice (bool)
       effective_from / effective_to (date-range)
  |
  v
Tier 2: Config-driven heuristics (if no policy matched)
  |
  +--> Check PO line items: is_service_item -> TWO_WAY
  +--> Check PO line items: is_stock_item -> THREE_WAY
  +--> Check item descriptions against _SERVICE_KEYWORDS -> TWO_WAY
  +--> Check item descriptions against _STOCK_KEYWORDS -> THREE_WAY
  |
  v
Tier 3: Config default mode
  +--> ReconciliationConfig.default_reconciliation_mode
       (typically THREE_WAY unless overridden)
```

### `ModeResolutionResult` dataclass

| Field | Type | Description |
|---|---|---|
| `mode` | str | `"TWO_WAY"` or `"THREE_WAY"` |
| `policy_code` | str | Matched policy code (blank if heuristic/default) |
| `policy_name` | str | Human-readable policy name |
| `reason` | str | Explanation text for audit trail |
| `grn_required` | bool | Whether GRN check is required |
| `resolution_method` | str | `"policy"` / `"heuristic"` / `"default"` |

### Service keywords (heuristic)

The heuristic compares line item descriptions against keyword sets:

| Keyword set | Signal | Mode result |
|---|---|---|
| `_SERVICE_KEYWORDS` (30 words) | services, maintenance, utilities, etc. | `TWO_WAY` |
| `_STOCK_KEYWORDS` (30 words) | frozen, fresh, packaging, inventory, etc. | `THREE_WAY` |

---

## 6. PO Lookup Service (ERP-Backed)

**File:** `apps/reconciliation/services/po_lookup_service.py`
**Class:** `POLookupService`

Resolves a Purchase Order for a given invoice using the ERP-backed resolution chain.

### `lookup(invoice) -> POLookupResult`

**Primary path (invoice has `po_number`):**

1. Call `ERPResolutionService.resolve_po(po_number=invoice.po_number)`.
2. If resolved: extract `po_id` from result value, fetch `PurchaseOrder` ORM object via `po_id` (or `po_number` normalised fallback).
3. Populate `POLookupResult` with ERP provenance fields.

**Discovery path (invoice has no `po_number`):**

```
ERPResolutionService.resolve_vendor(vendor_name=invoice.raw_vendor_name)
  -> vendor_id
    -> PurchaseOrder.objects.filter(vendor_id=vendor_id, status="OPEN")
       .filter(total_amount near invoice.total_amount within 5%)
```

If one matching PO is found, `invoice.po_number` is backfilled and saved. An `AuditEvent(RECONCILIATION_PO_DISCOVERED)` is logged.

### `POLookupResult` dataclass

| Field | Type | Description |
|---|---|---|
| `found` | bool | Whether a PO was resolved |
| `po` | Optional[PurchaseOrder] | ORM instance |
| `po_number` | str | Resolved PO number |
| `erp_source_type` | str | `ERPSourceType` value |
| `erp_confidence` | float | Resolution confidence (0.0-1.0) |
| `is_stale` | bool | Data freshness flag |
| `warnings` | List[str] | Freshness/provenance warnings |
| `erp_provenance` | dict | Full `to_provenance_dict()` output |

---

## 7. Execution Router

**File:** `apps/reconciliation/services/execution_router.py`
**Class:** `ReconciliationExecutionRouter`

Dispatches a reconciliation to the correct match pipeline based on the resolved mode.

```python
router = ReconciliationExecutionRouter(tolerance_engine)
output = router.execute(invoice, po_result, mode_resolution)
```

- If `po_result.found is False`: returns an early `RoutedMatchOutput` without running any match service.
- If `mode_resolution.mode == TWO_WAY`: delegates to `TwoWayMatchService`.
- If `mode_resolution.mode == THREE_WAY`: delegates to `ThreeWayMatchService`.

### `RoutedMatchOutput` dataclass

| Field | Type | Description |
|---|---|---|
| `mode` | str | The mode actually executed |
| `po_result` | POLookupResult | PO resolution output |
| `header_result` | Optional[HeaderMatchResult] | Header match (vendor, currency, total, tax) |
| `line_result` | Optional[LineMatchResult] | Line-level match summary |
| `grn_result` | Optional[GRNMatchResult] | GRN match (3-way only; None in 2-way) |
| `grn_required` | bool | From mode resolution |
| `grn_checked` | bool | Whether GRN lookup was attempted |
| `mode_resolution` | ModeResolutionResult | Full mode resolution metadata |

---

## 8. Two-Way Match Service

**File:** `apps/reconciliation/services/two_way_match_service.py`
**Class:** `TwoWayMatchService`

Performs Invoice vs PO matching without GRN verification.

### Sub-services

| Service | File | Responsibility |
|---|---|---|
| `HeaderMatchService` | `header_match_service.py` | Vendor, currency, totals, tax comparisons |
| `LineMatchService` | `line_match_service.py` | Line-by-line quantity, price, amount matching |

### `HeaderMatchResult` fields

| Field | Type | Meaning |
|---|---|---|
| `all_ok` | bool | True if all header checks passed |
| `vendor_match` | Optional[bool] | Vendor identity check |
| `currency_match` | Optional[bool] | Currency code check |
| `po_total_match` | Optional[bool] | Total amount within tolerance |
| `tax_match` | Optional[bool] | Tax amount within tolerance |
| `total_comparison` | Optional[FieldComparison] | Numeric diff for total |
| `tax_comparison` | Optional[FieldComparison] | Numeric diff for tax |

### `LineMatchResult` fields

| Field | Type | Meaning |
|---|---|---|
| `all_lines_matched` | bool | Every invoice line found a PO line match |
| `all_within_tolerance` | bool | All matched lines pass tolerance checks |
| `pairs` | List[LineMatchPair] | One entry per matched or unmatched invoice line |
| `unmatched_invoice_lines` | List | Invoice lines with no PO match |
| `unmatched_po_lines` | List | PO lines not referenced by any invoice line |

### `LineMatchPair` fields

| Field | Type | Meaning |
|---|---|---|
| `invoice_line` | InvoiceLineItem | Invoice side |
| `po_line` | Optional[PurchaseOrderLineItem] | PO side (None if unmatched) |
| `matched` | bool | Whether a pairing was found |
| `qty_comparison` | Optional[FieldComparison] | Quantity diff + tolerance flag |
| `price_comparison` | Optional[FieldComparison] | Unit price diff + tolerance flag |
| `amount_comparison` | Optional[FieldComparison] | Line amount diff + tolerance flag |

---

## 9. Three-Way Match Service

**File:** `apps/reconciliation/services/three_way_match_service.py`
**Class:** `ThreeWayMatchService`

Extends two-way matching with GRN verification. After running the two-way match, calls `GRNLookupService` and `GRNMatchService`.

### GRN resolution

```python
GRNLookupService.lookup(purchase_order) -> List[GRNSummary]
```

`GRNSummary` fields:

| Field | Type | Description |
|---|---|---|
| `grn` | GoodsReceiptNote | ORM instance |
| `grn_number` | str | GRN identifier |
| `receipt_date` | date | Date goods received |
| `status` | str | GRN status |
| `line_items` | List[GRNLineItem] | Received quantities per line |
| `erp_source_type` | str | ERP source used (MIRROR_DB, DB_FALLBACK, etc.) |
| `erp_provenance` | dict | Full ERP provenance dict |
| `is_stale` | bool | Freshness flag |
| `warnings` | List[str] | Provenance warnings |

### GRN matching

```python
GRNMatchService.match(invoice, purchase_order, grn_summaries) -> GRNMatchResult
```

`GRNMatchResult` fields:

| Field | Type | Description |
|---|---|---|
| `grn_available` | bool | At least one GRN exists for this PO |
| `grn_fully_received` | bool | All PO lines have matching GRN receipts |
| `has_receipt_issues` | bool | Shortage, over-receipt, or partial receipt |
| `grn_summaries` | List[GRNSummary] | All matched GRNs |
| `erp_source_type` | str | Copied from the primary GRN summary |
| `erp_provenance` | dict | Copied from the primary GRN summary |
| `is_stale` | bool | Propagated from GRN summary |

### ERP provenance propagation

After `grn_match.match()`, the three-way service copies GRN provenance fields from `grn_summaries[0]` into the `GRNMatchResult`. These are then persisted to `ReconciliationResult.grn_erp_source_type` and `ReconciliationResult.erp_source_metadata_json`.

---

## 10. Tolerance Engine

**File:** `apps/reconciliation/services/tolerance_engine.py`
**Class:** `ToleranceEngine`

Encapsulates configurable percentage-based tolerance comparisons for quantity, unit price, and line amount fields.

### Strict thresholds (default)

| Field | Default tolerance |
|---|---|
| Quantity | 2.0% |
| Price (unit price) | 1.0% |
| Amount (line total) | 1.0% |

These values come from `ReconciliationConfig.quantity_tolerance_pct`, `price_tolerance_pct`, `amount_tolerance_pct`.

### Auto-close thresholds

The `PolicyEngine._within_auto_close_band()` method uses a second set of wider thresholds for PARTIAL_MATCH auto-close decisions. These come from `ReconciliationConfig`:

| Field | Default |
|---|---|
| `review_auto_close_qty_tolerance` | 5.0% |
| `review_auto_close_price_tolerance` | 3.0% |
| `review_auto_close_amount_tolerance` | 3.0% |

### `FieldComparison` dataclass

| Field | Type | Description |
|---|---|---|
| `invoice_value` | Optional[Decimal] | Value from invoice |
| `po_value` | Optional[Decimal] | Value from PO |
| `difference` | Optional[Decimal] | `invoice_value - po_value` |
| `difference_pct` | Optional[Decimal] | Percentage difference |
| `within_tolerance` | Optional[bool] | Whether diff is within threshold; `None` if either value is missing |

---

## 11. Classification Service

**File:** `apps/reconciliation/services/classification_service.py`
**Class:** `ClassificationService`

Pure deterministic decision tree. Receives all match results and outputs a `MatchStatus` value.

### Decision tree (7 gates, evaluated in order)

| Gate | Condition | Result |
|---|---|---|
| 1 | `po_result.found is False` | `UNMATCHED` |
| 2 | `invoice.is_duplicate` | `REQUIRES_REVIEW` |
| 3 | `extraction_confidence < confidence_threshold` (default 0.75) | `REQUIRES_REVIEW` |
| 4 | `header.all_ok and line.all_lines_matched and line.all_within_tolerance and grn_ok` | `MATCHED` |
| 4a | *(2-way mode)*: GRN check is skipped; only header + line conditions apply | `MATCHED` |
| 5 | `header.all_ok and line.all_lines_matched and not line.all_within_tolerance` | `PARTIAL_MATCH` |
| 6 | `header.all_ok and line.all_lines_matched` (header issue) | `PARTIAL_MATCH` |
| 7 | GRN receipt issues (3-way only) | `REQUIRES_REVIEW` |
| Default | Unmatched invoice/PO lines, or fallback | `REQUIRES_REVIEW` |

Note: `ERROR` status is set by the runner service if `_reconcile_single()` raises an unhandled exception.

---

## 12. Exception Builder Service

**File:** `apps/reconciliation/services/exception_builder_service.py`
**Class:** `ExceptionBuilderService`

Converts comparison evidence into structured `ReconciliationException` instances. Returns unsaved instances for bulk creation within a transaction.

### Exception types generated

| Category | Exception Type | Severity | Mode |
|---|---|---|---|
| PO | `PO_NOT_FOUND` | HIGH | BOTH |
| Invoice | `DUPLICATE_INVOICE` | HIGH | BOTH |
| Extraction | `EXTRACTION_LOW_CONFIDENCE` | MEDIUM | BOTH |
| Header | `VENDOR_MISMATCH` | HIGH | BOTH |
| Header | `CURRENCY_MISMATCH` | MEDIUM | BOTH |
| Header | `AMOUNT_MISMATCH` | HIGH | BOTH |
| Header | `TAX_MISMATCH` | MEDIUM | BOTH |
| Line | `QTY_MISMATCH` | MEDIUM | BOTH |
| Line | `PRICE_MISMATCH` | MEDIUM | BOTH |
| Line | `AMOUNT_MISMATCH` | HIGH | BOTH |
| Line | `UNMATCHED_INVOICE_LINE` | HIGH | BOTH |
| Line | `UNMATCHED_PO_LINE` | MEDIUM | BOTH |
| GRN | `GRN_NOT_FOUND` | HIGH | THREE_WAY |
| GRN | `RECEIPT_SHORTAGE` | HIGH | THREE_WAY |
| GRN | `INVOICE_QTY_EXCEEDS_RECEIVED` | HIGH | THREE_WAY |
| GRN | `OVER_RECEIPT` | MEDIUM | THREE_WAY |
| GRN | `MULTI_GRN_PARTIAL_RECEIPT` | MEDIUM | THREE_WAY |

All exceptions are tagged with `applies_to_mode` (`BOTH` or `THREE_WAY`) using constant lookup in `apps/core/constants.py:THREE_WAY_ONLY_EXCEPTION_TYPES`.

---

## 13. Result Service and ERP Provenance

**File:** `apps/reconciliation/services/result_service.py`
**Class:** `ReconciliationResultService`

Persists all output of the deterministic pipeline to the database within a single atomic transaction.

### Operations in `save()`

1. Create or update `ReconciliationResult` with:
   - `match_status`, `reconciliation_mode`, `mode_resolved_by`, `mode_policy_code`
   - `deterministic_confidence` (calculated from match evidence)
   - ERP provenance fields (from `po_result` and `grn_result`)
2. Create `ReconciliationResultLine` per `LineMatchPair`.
3. Bulk-create `ReconciliationException` instances returned by the builder.
4. Langfuse span: `"result_save"`.

### ERP provenance fields on `ReconciliationResult`

| Field | Source |
|---|---|
| `po_erp_source_type` | `po_result.erp_source_type` |
| `grn_erp_source_type` | `grn_result.erp_source_type` (None in 2-way) |
| `data_is_stale` | `po_result.is_stale or grn_result.is_stale` |
| `erp_source_metadata_json` | Merged dict: `{"po": po_result.erp_provenance, "grn": grn_result.erp_provenance}` |

---

## 14. Agent Orchestrator

**File:** `apps/agents/services/orchestrator.py`
**Class:** `AgentOrchestrator`

The single entry point for the AI agent layer. Called synchronously from the `start_reconciliation` view (after the deterministic run) or asynchronously from `run_agent_pipeline_task`.

### Constructor

```python
AgentOrchestrator()
  self.policy = ReasoningPlanner()
  self.decision_service = DecisionLogService()
  self.resolver = DeterministicResolver()
```

### `execute(result, request_user) -> OrchestrationResult`

**Step-by-step:**

1. **Resolve actor**: `AgentGuardrailsService.resolve_actor(request_user)` -- returns authenticated user or SYSTEM_AGENT.
2. **Authorise**: `authorize_orchestration(actor)` -- checks `agents.orchestrate` permission. Raises `PermissionDenied` on failure.
3. **Data-scope check**: `authorize_data_scope(actor, result)` -- checks `UserRole.scope_json` for `allowed_vendor_ids` and `allowed_business_units`. ADMIN and SYSTEM_AGENT bypass.
4. **TraceContext**: Build child trace context enriched with RBAC snapshot.
5. **Langfuse trace**: Open `"agent_pipeline"` trace.
6. **Plan**: `self.policy.plan(result)` -> `AgentPlan`.
7. **Skip check**: If `plan.skip_agents`: create COMPLETED orchestration run, optionally auto-close, return.
8. **Duplicate-run guard**: Query for any `RUNNING` `AgentOrchestrationRun` for this result. If found, return `OrchestrationResult(skipped=True)`.
9. **Create `AgentOrchestrationRun`** (status=RUNNING).
10. **Partition plan**: Split `plan.agents` into `llm_agents` (non-deterministic) and `deterministic_tail` (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY -- may be replaced by `DeterministicResolver`).
11. **Build `AgentContext`**: Attach `result`, `invoice_id`, `po_number`, `exceptions`, `reconciliation_mode`, RBAC fields.
12. **Build `AgentMemory`**: Pre-seed facts: `grn_available`, `grn_fully_received`, `is_two_way`, `vendor_name`, `match_status`, `extraction_confidence`.
13. **Execute LLM agents**: For each `agent_type` in `llm_agents`:
    - Instantiate from `AGENT_CLASS_REGISTRY`.
    - Check `AgentDefinition.enabled`.
    - Call `agent.run(ctx)` -> `AgentRun`.
    - Update `AgentMemory.record_agent_output()`.
    - Check feedback: `AgentFeedbackService.maybe_re_reconcile(agent_type, agent_run, ctx)`.
    - Emit `score_trace("agent_confidence", ...)`.
14. **Execute deterministic tail**: Call `DeterministicResolver.resolve()` to produce `DeterministicResolution`. Create synthetic `AgentRun` records for auditability.
15. **Recommendations**: If plan includes `_RECOMMENDING_AGENTS`, call `RecommendationService.create()` with dedup guard.
16. **Finalize**: Update `AgentOrchestrationRun` to COMPLETED (or PARTIAL if any LLM agent failed).

### `_RECOMMENDING_AGENTS` and `_FEEDBACK_AGENTS`

```python
_RECOMMENDING_AGENTS = {"REVIEW_ROUTING", "CASE_SUMMARY"}
_FEEDBACK_AGENTS = {"PO_RETRIEVAL"}
```

Only agents in `_RECOMMENDING_AGENTS` emit formal `AgentRecommendation` DB records. The `PO_RETRIEVAL` agent triggers a feedback re-reconciliation if `found_po` appears in its output evidence.

---

## 15. Planning Layer: ReasoningPlanner and PolicyEngine

### ReasoningPlanner (primary)

**File:** `apps/agents/services/reasoning_planner.py`
**Class:** `ReasoningPlanner`

Always active -- no feature flag. Uses an LLM call to plan the agent sequence, falling back deterministically on any error.

**Contract:**
- LLM plan is always attempted for non-clean results.
- `PolicyEngine` runs first as a baseline (fast, no LLM).
- If `quick_plan.skip_agents`, the planner returns early without LLM.
- If the LLM call or JSON parsing fails, `quick_plan` (PolicyEngine result) is returned with `plan_source="deterministic"`.

**LLM planner system prompt** summarises 7 available agents and 5 hard constraints (no GRN_RETRIEVAL in TWO_WAY, CASE_SUMMARY must be last, etc.).

**LLM response schema:**

```json
{
  "overall_reasoning": "...",
  "confidence": 0.9,
  "steps": [
    {"agent_type": "EXCEPTION_ANALYSIS", "rationale": "...", "priority": 1}
  ]
}
```

Validation: unknown `agent_type` values are dropped; `GRN_RETRIEVAL` in a TWO_WAY plan raises `ValueError` (triggers fallback); `CASE_SUMMARY` out of last position raises `ValueError`.

**LLM config:** `temperature=0.0`, `max_tokens=1024`, `response_format=json_object`.

### PolicyEngine (deterministic fallback)

**File:** `apps/agents/services/policy_engine.py`
**Class:** `PolicyEngine`

Rule-based agent selector. Also used for `should_auto_close()` and `should_escalate()` checks (delegated from `ReasoningPlanner`).

### `AgentPlan` dataclass

| Field | Type | Description |
|---|---|---|
| `agents` | List[str] | Ordered list of agent type strings |
| `reason` | str | Explanation of why this plan was chosen |
| `skip_agents` | bool | True if no agents should run at all |
| `auto_close` | bool | True if result can be auto-closed |
| `reconciliation_mode` | str | Mode from result |
| `plan_source` | str | `"llm"` or `"deterministic"` |
| `plan_confidence` | float | LLM plan confidence (0.0 if deterministic) |

### PolicyEngine rules (7 rules, evaluated in order)

| Rule | Condition | Action |
|---|---|---|
| R1 | `MATCHED` and `confidence >= REVIEW_AUTO_CLOSE_THRESHOLD` | `skip_agents=True` |
| R1b | `PARTIAL_MATCH` and `_within_auto_close_band()` | `auto_close=True`, `skip_agents=True` |
| R2 | `PO_NOT_FOUND` exception exists | Add `PO_RETRIEVAL` to plan |
| R3 | `GRN_NOT_FOUND` or receipt issues (THREE_WAY only) | Add `GRN_RETRIEVAL` to plan |
| R4 | `extraction_confidence < AGENT_CONFIDENCE_THRESHOLD` | Add `INVOICE_UNDERSTANDING` to plan |
| R5 | `PARTIAL_MATCH` | Add `RECONCILIATION_ASSIST` to plan |
| Always | Any non-empty plan | Append `EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY` |
| Fallback | `REQUIRES_REVIEW` / `UNMATCHED` / `ERROR` with no prior agents | Full tail: `EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY` |

### `_within_auto_close_band(result)` (auto-close for PARTIAL_MATCH)

Returns `True` only if **all** of the following hold:

1. No `HIGH` severity exceptions.
2. All `ReconciliationResultLine` records have quantity diff within `review_auto_close_qty_tolerance` (5%).
3. All `ReconciliationResultLine` records have price diff within `review_auto_close_price_tolerance` (3%).
4. All `ReconciliationResultLine` records have amount diff within `review_auto_close_amount_tolerance` (3%).

---

## 16. Deterministic Resolver

**File:** `apps/agents/services/deterministic_resolver.py`
**Class:** `DeterministicResolver`

Replaces the `EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, and `CASE_SUMMARY` agents with deterministic rule-based logic when the exception set is unambiguous. Saves cost and latency while maintaining full auditability.

```python
DeterministicResolver.REPLACED_AGENTS = {"EXCEPTION_ANALYSIS", "REVIEW_ROUTING", "CASE_SUMMARY"}
```

### Resolution rules (priority order)

| Priority | Condition | Recommendation |
|---|---|---|
| 0 (highest) | Prior agent recommended `AUTO_CLOSE` with confidence >= 0.80 | `AUTO_CLOSE` |
| 1 | No active exceptions | `SEND_TO_AP_REVIEW` (confidence 0.95) |
| 2 | `EXTRACTION_LOW_CONFIDENCE` exception present | `REPROCESS_EXTRACTION` |
| 3 | `VENDOR_MISMATCH` exception present | `SEND_TO_VENDOR_CLARIFICATION` |
| 4 | Any of `GRN_NOT_FOUND`, `RECEIPT_SHORTAGE`, `INVOICE_QTY_EXCEEDS_RECEIVED`, `OVER_RECEIPT`, `MULTI_GRN_PARTIAL_RECEIPT` | `SEND_TO_PROCUREMENT` |
| 5 | 3+ distinct exception categories AND at least one HIGH severity | `ESCALATE_TO_MANAGER` |
| Default | Everything else | `SEND_TO_AP_REVIEW` |

Note: Numeric mismatch types (`QTY_MISMATCH`, `PRICE_MISMATCH`, `AMOUNT_MISMATCH`, `TAX_MISMATCH`) are counted as **one** category for complexity assessment (they typically cascade from the same root cause).

### `DeterministicResolution` dataclass

| Field | Type | Description |
|---|---|---|
| `recommendation_type` | str | `RecommendationType` value |
| `confidence` | float | Deterministic confidence score |
| `reasoning` | str | Human-readable explanation |
| `evidence` | dict | Structured evidence dict |
| `case_summary` | str | Template-rendered reviewer summary |

---

## 17. BaseAgent and the ReAct Loop

**File:** `apps/agents/services/base_agent.py`
**Class:** `BaseAgent`

Abstract base class for all agents. Implements the ReAct (Reason + Act) loop with tool calling, timeout enforcement, retry logic, composite confidence scoring, and Langfuse integration.

### `AgentContext` dataclass

| Field | Type | Description |
|---|---|---|
| `reconciliation_result` | ReconciliationResult | Current result being investigated |
| `invoice_id` | int | Invoice PK |
| `po_number` | str | PO number from invoice (may be missing) |
| `exceptions` | List[dict] | Serialised exception list |
| `extra` | dict | Agent-specific extra data |
| `reconciliation_mode` | str | `"TWO_WAY"` or `"THREE_WAY"` |
| `actor_user_id` | Optional[int] | Actor for RBAC audit trail |
| `actor_primary_role` | str | Actor's primary role code |
| `actor_roles_snapshot` | List[str] | All active role codes at execution time |
| `permission_checked` | str | Permission code that was verified |
| `permission_source` | str | `"ROLE"` / `"SYSTEM_AGENT"` / `"USER"` |
| `access_granted` | bool | Whether guardrail granted access |
| `trace_id` | str | Distributed trace ID |
| `span_id` | str | Parent span ID |
| `memory` | AgentMemory | Shared in-process memory across agents |
| `_langfuse_trace` | Optional[object] | Langfuse trace object (internal) |

### `AgentOutput` dataclass

| Field | Type | Description |
|---|---|---|
| `reasoning` | str | Agent's reasoning text |
| `recommendation_type` | Optional[str] | `RecommendationType` value |
| `confidence` | float | Confidence score (0.0-1.0) |
| `evidence` | dict | Structured evidence from tools |
| `decisions` | List[dict] | Decision log entries |
| `tools_used` | List[str] | Tool names actually called |
| `raw_content` | str | Raw LLM response string |

### `run(ctx) -> AgentRun` sequence

```
1. Authorise: AgentGuardrailsService.authorize_agent(actor, agent_type)
2. Create AgentRun(status=RUNNING, RBAC fields, trace_id, span_id)
3. Stamp prompt_version from PromptRegistry.version_for(agent_type)
4. Open Langfuse span (child of ctx._langfuse_trace)
5. Load AgentDefinition: timeout_seconds, max_retries, requires_tool_grounding,
   min_tool_calls, tool_failure_confidence_cap
6. Build initial messages: [system_prompt, user_message]
7. Load tool specs from ToolRegistry for allowed_tools
8. ReAct loop (max MAX_TOOL_ROUNDS=6 rounds):
   a. Deadline check (TimeoutError if elapsed > timeout_s)
   b. LLM call with retry (_call_llm_with_retry, max_retries from AgentDefinition)
   c. Record token usage on AgentRun
   d. Save assistant message to AgentMessage
   e. If no tool_calls: finish (steps below)
   f. If tool_calls: add assistant message with tool_calls array
      For each tool call:
        - _execute_tool(name, arguments, agent_run, step)
        - Open Langfuse span per tool call
        - count failed_tool_count / total_tool_calls
        - Append tool response message (with tool_call_id + name)
        - Save to AgentMessage
9. Finish: interpret_response(content, ctx) -> AgentOutput
10. Override tools_used from runtime tracking (authoritative)
11. Compute composite confidence:
    llm_confidence - (failed_tool_penalties) - (evidence quality adjustment)
12. Apply catalog guards:
    - requires_tool_grounding: cap at 0.4 if no tools called
    - min_tool_calls: cap at 0.5 if below minimum
    - tool_failure_confidence_cap: apply catalog cap if any tool failed
13. Evidence check: if empty evidence, cap at 0.5
14. _finalise_run(agent_run, output, start)
15. Close Langfuse span with output dict
16. score_trace("agent_confidence", output.confidence)
```

### Timeout and retry configuration

| Setting | Source | Default |
|---|---|---|
| `timeout_seconds` | `AgentDefinition.timeout_seconds` | 120s (env: `AGENT_TIMEOUT_SECONDS`) |
| `max_retries` | `AgentDefinition.max_retries` | 2 (env: `AGENT_MAX_RETRIES`) |

### Composite confidence formula

```
composite = llm_confidence
  - (failed_tool_count / total_tool_calls * 0.2)  # tool failure penalty
  - (0.1 if evidence is empty or missing)           # evidence penalty
  (clamped to [0.0, 1.0])
```

Catalog-level caps are then applied on top (requires_tool_grounding, min_tool_calls, tool_failure_confidence_cap).

---

## 18. Agent Registry: All 8 Agent Types

### Agent type overview

| Agent Type | Class | ReAct? | Tools | Key Output |
|---|---|---|---|---|
| `EXCEPTION_ANALYSIS` | `ExceptionAnalysisAgent` | Yes (+ 2nd LLM call) | po_lookup, grn_lookup, invoice_details, exception_list, reconciliation_summary | Recommendation + reviewer summary |
| `INVOICE_EXTRACTION` | `InvoiceExtractionAgent` | No (single-shot) | None | Extracted invoice fields |
| `INVOICE_UNDERSTANDING` | `InvoiceUnderstandingAgent` | Yes | invoice_details, po_lookup, vendor_search | Quality assessment |
| `PO_RETRIEVAL` | `PORetrievalAgent` | Yes | po_lookup, vendor_search, invoice_details | `found_po` in evidence |
| `GRN_RETRIEVAL` | `GRNRetrievalAgent` | Yes | grn_lookup, po_lookup, invoice_details | GRN availability confirmation |
| `REVIEW_ROUTING` | `ReviewRoutingAgent` | Yes | reconciliation_summary, exception_list | Routing recommendation |
| `CASE_SUMMARY` | `CaseSummaryAgent` | Yes | invoice_details, po_lookup, grn_lookup, reconciliation_summary, exception_list | Human-readable summary |
| `RECONCILIATION_ASSIST` | `ReconciliationAssistAgent` | Yes | invoice_details, po_lookup, grn_lookup, reconciliation_summary, exception_list | Discrepancy analysis |

---

### `ExceptionAnalysisAgent`

**Agent type:** `EXCEPTION_ANALYSIS`
**System prompt key:** `agent.exception_analysis`

**Special behaviour:** After the ReAct loop completes, makes a second LLM call (`_generate_reviewer_summary()`) to produce a structured reviewer-facing summary.

Reviewer summary fields persisted on `ReviewAssignment`:

| Field | Description |
|---|---|
| `reviewer_summary` | Narrative case summary |
| `reviewer_risk_level` | Risk classification |
| `reviewer_confidence` | Summary confidence |
| `reviewer_recommendation` | Recommended action |
| `reviewer_suggested_actions` | Numbered action list |
| `reviewer_summary_generated_at` | Timestamp |

Also logged via `AgentTraceService.log_agent_decision(decision_type="REVIEWER_SUMMARY")`.

**User message fields:** `mode_context`, `result.pk`, `invoice_id`, `po_number`, `match_status`, `extraction_confidence`, `exceptions` (JSON serialised).

---

### `InvoiceExtractionAgent`

**Agent type:** `INVOICE_EXTRACTION`
**System prompt key:** `extraction.invoice_system` (or composed from `ctx.extra["composed_prompt"]`)
**Mode:** Single-shot (no tools), `temperature=0.0`, `response_format=json_object`

Used during the extraction pipeline (not typically included in the reconciliation agent plan). When invoked standalone, opens its own Langfuse trace.

Captures prompt composition metadata (`prompt_source_type`, `prompt_hash`, `base_prompt_key`, `category_prompt_key`, `country_prompt_key`) and persists it to `AgentRun.input_payload["_prompt_meta"]`.

---

### `InvoiceUnderstandingAgent`

**Agent type:** `INVOICE_UNDERSTANDING`
**System prompt key:** `agent.invoice_understanding`
**Tools:** `invoice_details`, `po_lookup`, `vendor_search`

Invoked when `extraction_confidence < AGENT_CONFIDENCE_THRESHOLD`. Analyses extraction quality and flags issues via `AgentMemory.extraction_issues`.

User message includes: `invoice_id`, `po_number`, `extraction_confidence`, `match_status`, `validation_warnings` (from memory or extra).

---

### `PORetrievalAgent`

**Agent type:** `PO_RETRIEVAL`
**System prompt key:** `agent.po_retrieval`
**Tools:** `po_lookup`, `vendor_search`, `invoice_details`

Invoked when the deterministic PO lookup failed (exception type `PO_NOT_FOUND`). Normalises evidence to always place the recovered PO number under `evidence["found_po"]` so the orchestrator feedback loop can read it.

Evidence fallback keys checked (in order): `found_po`, `po_number`, `matched_po`, `result`, `found`, `po`.

Triggers `AgentFeedbackService.maybe_re_reconcile()` if `found_po` is non-empty.

---

### `GRNRetrievalAgent`

**Agent type:** `GRN_RETRIEVAL`
**System prompt key:** `agent.grn_retrieval`
**Tools:** `grn_lookup`, `po_lookup`, `invoice_details`

**Only invoked in THREE_WAY mode.** The PolicyEngine suppresses this agent for TWO_WAY reconciliations, and the LLM planner has a hard rule that rejects any plan including GRN_RETRIEVAL for a TWO_WAY result.

User message pre-checks `ctx.reconciliation_mode == "TWO_WAY"` and returns an early no-op JSON if somehow invoked in the wrong mode.

Pre-seeds memory context: `grn_available`, `grn_fully_received`.

---

### `ReviewRoutingAgent`

**Agent type:** `REVIEW_ROUTING`
**System prompt key:** `agent.review_routing`
**Tools:** `reconciliation_summary`, `exception_list`

In `_RECOMMENDING_AGENTS` -- produces a formal `AgentRecommendation` record. Receives full prior agent findings from `AgentMemory.agent_summaries` and `AgentMemory.current_recommendation`.

User message: `reconciliation_result.pk`, `match_status`, `exceptions` JSON, prior agent findings block.

---

### `CaseSummaryAgent`

**Agent type:** `CASE_SUMMARY`
**System prompt key:** `agent.case_summary`
**Tools:** `invoice_details`, `po_lookup`, `grn_lookup`, `reconciliation_summary`, `exception_list`

Always the **last** agent in any plan. In `_RECOMMENDING_AGENTS`. Produces a human-readable case summary using all prior agent memory and the full tool set.

User message: `result.pk`, `invoice_id`, `po_number`, `match_status`, condensed prior analysis (first 100 chars per agent), current recommendation + confidence.

---

### `ReconciliationAssistAgent`

**Agent type:** `RECONCILIATION_ASSIST`
**System prompt key:** `agent.reconciliation_assist`
**Tools:** `invoice_details`, `po_lookup`, `grn_lookup`, `reconciliation_summary`, `exception_list`

Invoked for `PARTIAL_MATCH` results to investigate the source of discrepancies. Aware of any PO number recovered by a prior `PORetrievalAgent` via `AgentMemory.resolved_po_number`.

---

## 19. Agent Memory

**File:** `apps/agents/services/agent_memory.py`
**Class:** `AgentMemory`

Plain Python dataclass (no DB). Created once per orchestration run, passed through `AgentContext.memory`. Every agent can read and write to it.

### Fields

| Field | Type | Description |
|---|---|---|
| `resolved_po_number` | Optional[str] | PO number recovered by `PORetrievalAgent` |
| `resolved_grn_numbers` | List[str] | GRN numbers confirmed by `GRNRetrievalAgent` |
| `extraction_issues` | List[str] | Quality issues from `InvoiceUnderstandingAgent` |
| `agent_summaries` | Dict[str, str] | Reasoning summaries keyed by `agent_type` (max 500 chars each) |
| `current_recommendation` | Optional[str] | Highest-confidence recommendation seen so far |
| `current_confidence` | float | Confidence of `current_recommendation` |
| `facts` | Dict[str, Any] | Free-form key-value store |

### Pre-seeded facts (by orchestrator)

| Key | Source |
|---|---|
| `grn_available` | `result.grn_available` |
| `grn_fully_received` | `result.grn_fully_received` |
| `is_two_way` | `result.reconciliation_mode == "TWO_WAY"` |
| `vendor_name` | `invoice.vendor.name` or `invoice.raw_vendor_name` |
| `match_status` | `result.match_status` |
| `extraction_confidence` | `result.extraction_confidence` |

### `record_agent_output(agent_type, output)` mutation

After each agent run, the orchestrator calls `memory.record_agent_output()`:

1. Saves `output.reasoning[:500]` to `agent_summaries[agent_type]`.
2. If `output.confidence > current_confidence`: promotes recommendation.
3. If `output.evidence["found_po"]` exists and is a non-empty string: sets `resolved_po_number`.

---

## 20. RBAC and Guardrails Service

**File:** `apps/agents/services/guardrails_service.py`
**Class:** `AgentGuardrailsService`

Single responsibility: RBAC checks and audit. No business logic. Fail-closed design -- unknown actors resolve to SYSTEM_AGENT (not admin bypass).

### Permission maps

**Orchestration:**

| Permission | Required for |
|---|---|
| `agents.orchestrate` | Any `AgentOrchestrator.execute()` call |

**Per-agent permissions:**

| Agent Type | Permission |
|---|---|
| `INVOICE_EXTRACTION` | `agents.run_extraction` |
| `INVOICE_UNDERSTANDING` | `agents.run_extraction` |
| `PO_RETRIEVAL` | `agents.run_po_retrieval` |
| `GRN_RETRIEVAL` | `agents.run_grn_retrieval` |
| `RECONCILIATION_ASSIST` | `agents.run_reconciliation_assist` |
| `EXCEPTION_ANALYSIS` | `agents.run_exception_analysis` |
| `REVIEW_ROUTING` | `agents.run_review_routing` |
| `CASE_SUMMARY` | `agents.run_case_summary` |

**Per-tool permissions:**

| Tool | Permission |
|---|---|
| `po_lookup` | `purchase_orders.view` |
| `grn_lookup` | `grns.view` |
| `vendor_search` | `vendors.view` |
| `invoice_details` | `invoices.view` |
| `exception_list` | `reconciliation.view` |
| `reconciliation_summary` | `reconciliation.view` |

**Recommendation permissions:**

| Recommendation Type | Permission |
|---|---|
| `AUTO_CLOSE` | `recommendations.auto_close` |
| `SEND_TO_AP_REVIEW` | `recommendations.route_review` |
| `ESCALATE_TO_MANAGER` | `recommendations.escalate` |
| `REPROCESS_EXTRACTION` | `recommendations.reprocess` |
| `SEND_TO_PROCUREMENT` | `recommendations.route_procurement` |
| `SEND_TO_VENDOR_CLARIFICATION` | `recommendations.vendor_clarification` |

**Action permissions:**

| Action | Permission |
|---|---|
| `auto_close_result` | `recommendations.auto_close` |
| `assign_review` | `reviews.assign` |
| `escalate_case` | `cases.escalate` |
| `reprocess_extraction` | `extraction.reprocess` |
| `rerun_reconciliation` | `reconciliation.run` |

### SYSTEM_AGENT identity

When `request_user` is unauthenticated or absent (Celery task): `resolve_actor()` returns (or creates) `system-agent@internal`. This user is assigned the `SYSTEM_AGENT` role via `_assign_system_agent_role()`. `SYSTEM_AGENT` and `ADMIN` always bypass data-scope checks.

### Data-scope enforcement

`authorize_data_scope(actor, result)` reads `UserRole.scope_json` for all active role assignments. Scope keys:

- `allowed_business_units: List[str]` -- result's `invoice.business_unit` must be in list (if set).
- `allowed_vendor_ids: List[int]` -- result's `invoice.vendor_id` must be in list (if set).

Multiple roles grant **additive** scope (union).

### Guardrail audit logging

Every guardrail decision (grant or deny) is logged as:
- `AuditEvent` with type `GUARDRAIL_GRANTED` or `GUARDRAIL_DENIED`.
- `score_trace("rbac_guardrail", 1.0 | 0.0)` to Langfuse.

---

## 21. Tool Registry: All 6 Tools

**File:** `apps/tools/registry/tools.py` (concrete tools)
**File:** `apps/tools/registry/base.py` (BaseTool, ToolRegistry, `@register_tool`)

All tools extend `BaseTool` and are registered via the `@register_tool` decorator. The registry is a module-level singleton; tools are loaded at import time.

### `BaseTool` interface

Each tool declares:

| Attribute | Description |
|---|---|
| `name` | Short unique identifier (the function name in tool specs) |
| `description` | What the tool does (shown to LLM in tool spec) |
| `when_to_use` | Guidance on appropriate use cases |
| `when_not_to_use` | Anti-patterns to avoid |
| `no_result_meaning` | What an empty result means |
| `failure_handling_instruction` | How to handle tool failures |
| `authoritative_fields` | Fields the LLM can trust as ground truth |
| `evidence_keys_produced` | Keys that appear in the returned data dict |
| `parameters_schema` | JSON Schema for tool arguments |
| `required_permission` | RBAC permission code checked before execution |

### Tool: `po_lookup`

**Class:** `POLookupTool`
**Permission:** `purchase_orders.view`

**Parameters:**

| Param | Type | Optional | Description |
|---|---|---|---|
| `po_number` | string | Yes | PO number (supports partial/contains match) |
| `vendor_id` | integer | Yes | Vendor PK -- list open POs for this vendor |

**Resolution order:**
1. ERP resolution via `ERPResolutionService.with_default_connector().resolve_po()`.
2. If import fails: direct `PurchaseOrder` ORM lookup (exact, normalized, contains).

**ERP metadata in response:**

| Key | Description |
|---|---|
| `_erp_source` | `ERPSourceType` value |
| `_erp_confidence` | Resolution confidence |
| `_erp_fallback_used` | Whether DB fallback was used |
| `_erp_is_stale` | Data freshness flag |

**Evidence keys produced:** `found`, `po_number`, `vendor`, `vendor_id`, `total_amount`, `currency`, `status`, `line_items`.

---

### Tool: `grn_lookup`

**Class:** `GRNLookupTool`
**Permission:** `grns.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `po_number` | string | Yes | Find GRNs for this PO |

**Resolution order:**
1. ERP resolution via `ERPResolutionService.with_default_connector().resolve_grn()`.
2. If import fails: direct `GoodsReceiptNote` ORM query.

**Evidence keys produced:** `found`, `po_number`, `grn_count`, `grns` (list).

Each GRN entry: `grn_number`, `receipt_date`, `status`, `warehouse`, `line_items` (with `quantity_received`, `quantity_accepted`, `quantity_rejected`).

---

### Tool: `vendor_search`

**Class:** `VendorSearchTool`
**Permission:** `vendors.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Vendor name, code, or alias |

**Lookup strategy:**
1. Name/code: `Q(code__iexact=raw) | Q(normalized_name=normalized) | Q(name__icontains=raw)` -- active vendors only.
2. Alias: `VendorAlias.objects.filter(normalized_alias=normalized)`.

**Evidence keys produced:** `query`, `count`, `vendors`.

Each vendor entry: `vendor_id`, `code`, `name`, `match_type` (`"direct"` or `"alias"`), optionally `alias`.

---

### Tool: `invoice_details`

**Class:** `InvoiceDetailsTool`
**Permission:** `invoices.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `invoice_id` | integer | Yes | Invoice PK |

Returns the full extracted invoice including normalised line items. Tax percentage is inferred from raw JSON or calculated from `tax_amount / subtotal` if not directly available.

**Evidence keys produced:** `invoice_id`, `invoice_number`, `vendor`, `vendor_id`, `po_number`, `invoice_date`, `currency`, `subtotal`, `tax_percentage`, `tax_amount`, `total_amount`, `status`, `extraction_confidence`, `is_duplicate`, `line_items`.

---

### Tool: `exception_list`

**Class:** `ExceptionListTool`
**Permission:** `reconciliation.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `reconciliation_result_id` | integer | Yes | ReconciliationResult PK |

Returns all exceptions for the result from `ReconciliationException`.

**Evidence keys produced:** `reconciliation_result_id`, `exceptions` (list with `id`, `exception_type`, `severity`, `message`, `resolved`).

---

### Tool: `reconciliation_summary`

**Class:** `ReconciliationSummaryTool`
**Permission:** `reconciliation.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `reconciliation_result_id` | integer | Yes | ReconciliationResult PK |

Returns the reconciliation summary: match status, mode, confidence, exception counts, key line-level discrepancies.

**Evidence keys produced:** `reconciliation_result_id`, `match_status`, `reconciliation_mode`, `deterministic_confidence`, `exception_count`, `exception_types`, `line_count`, `matched_lines`, `within_tolerance_lines`.

---

## 22. Agent Feedback Loop

**File:** `apps/reconciliation/services/agent_feedback_service.py`
**Class:** `AgentFeedbackService`

When `PORetrievalAgent` (or `GRNRetrievalAgent`) recovers a missing document, the orchestrator calls `AgentFeedbackService.maybe_re_reconcile()`. This atomically:

1. Re-links the invoice to the recovered PO number (`invoice.po_number = found_po`).
2. Triggers `ReconciliationRunnerService._reconcile_single()` again for the same invoice.
3. Updates `AgentMemory.resolved_po_number`.
4. Only runs if `last_output.status == COMPLETED`.

This allows a successfully recovered PO to produce a `MATCHED` result in the same orchestration run without requiring a full re-reconciliation job.

---

## 23. Data Models

### `ReconciliationRun`

**Table:** `reconciliation_run`

| Field | Type | Description |
|---|---|---|
| `status` | CharField | `PENDING / RUNNING / COMPLETED / FAILED` |
| `triggered_by` | FK(User) | Actor who started the run |
| `started_at` | DateTimeField | |
| `completed_at` | DateTimeField | |
| `total_invoices` | PositiveIntegerField | Total processed |
| `matched_count` | PositiveIntegerField | |
| `partial_match_count` | PositiveIntegerField | |
| `unmatched_count` | PositiveIntegerField | |
| `requires_review_count` | PositiveIntegerField | |
| `error_count` | PositiveIntegerField | |
| `trace_id` | CharField | Distributed trace ID |

### `ReconciliationResult`

**Table:** `reconciliation_result`

| Field | Type | Description |
|---|---|---|
| `invoice` | FK(Invoice) | |
| `purchase_order` | FK(PurchaseOrder) | |
| `reconciliation_run` | FK(ReconciliationRun) | |
| `match_status` | CharField | `MATCHED / PARTIAL_MATCH / UNMATCHED / REQUIRES_REVIEW / ERROR` |
| `reconciliation_mode` | CharField | `TWO_WAY / THREE_WAY` |
| `mode_resolved_by` | CharField | `"policy"` / `"heuristic"` / `"default"` |
| `mode_policy_code` | CharField | Policy code if mode was policy-resolved |
| `deterministic_confidence` | FloatField | Confidence from deterministic engine |
| `extraction_confidence` | FloatField | Copied from invoice |
| `grn_available` | BooleanField | At least one GRN exists |
| `grn_fully_received` | BooleanField | All PO lines received |
| `po_erp_source_type` | CharField | ERP source used for PO data |
| `grn_erp_source_type` | CharField | ERP source used for GRN data |
| `data_is_stale` | BooleanField | Whether any source data is stale |
| `erp_source_metadata_json` | JSONField | Full ERP provenance dict (`{"po": {...}, "grn": {...}}`) |
| `summary` | TextField | Agent-generated summary (ASCII-safe) |
| `trace_id` | CharField | Distributed trace ID |
| `agent_trace_id` | CharField | Agent pipeline trace ID |

### `ReconciliationResultLine`

**Table:** `reconciliation_result_line`

| Field | Type | Description |
|---|---|---|
| `result` | FK(ReconciliationResult) | Parent result |
| `invoice_line` | FK(InvoiceLineItem) | |
| `po_line` | FK(PurchaseOrderLineItem) | |
| `qty_difference_pct` | DecimalField | Quantity percentage difference |
| `price_difference_pct` | DecimalField | Unit price percentage difference |
| `amount_difference_pct` | DecimalField | Line amount percentage difference |
| `qty_within_tolerance` | BooleanField | |
| `price_within_tolerance` | BooleanField | |
| `amount_within_tolerance` | BooleanField | |

### `ReconciliationException`

**Table:** `reconciliation_exception`

| Field | Type | Description |
|---|---|---|
| `result` | FK(ReconciliationResult) | |
| `result_line` | FK(ReconciliationResultLine) | Nullable |
| `exception_type` | CharField | `ExceptionType` enum value |
| `severity` | CharField | `HIGH / MEDIUM / LOW` |
| `message` | TextField | Human-readable description |
| `details` | JSONField | Structured data (amounts, pct diff, etc.) |
| `resolved` | BooleanField | Whether exception has been manually resolved |
| `applies_to_mode` | CharField | `BOTH / THREE_WAY` |

### `AgentDefinition`

**Table:** `agents_definition`

| Field | Type | Description |
|---|---|---|
| `agent_type` | CharField | Unique `AgentType` value |
| `enabled` | BooleanField | Whether this agent can be invoked |
| `max_retries` | PositiveIntegerField | LLM retry count |
| `timeout_seconds` | PositiveIntegerField | Per-run timeout |
| `config_json` | JSONField | `{"allowed_tools": [...]}` per agent |
| `purpose` | TextField | Contract field |
| `entry_conditions` | TextField | Contract field |
| `success_criteria` | TextField | Contract field |
| `prohibited_actions` | JSONField | List of forbidden recommendation types |
| `requires_tool_grounding` | BooleanField | Must call at least one tool |
| `min_tool_calls` | PositiveIntegerField | Minimum successful tool calls |
| `tool_failure_confidence_cap` | FloatField | Max confidence when a tool fails |
| `allowed_recommendation_types` | JSONField | Null = all allowed |
| `default_fallback_recommendation` | CharField | Used when output is invalid |
| `output_schema_name` | CharField | e.g., `"AgentOutputSchema"` |
| `output_schema_version` | CharField | e.g., `"v1"` |
| `lifecycle_status` | CharField | `draft / active / deprecated` |
| `owner_team` | CharField | |
| `capability_tags` | JSONField | e.g., `["retrieval", "routing"]` |
| `domain_tags` | JSONField | e.g., `["po", "grn", "vendor"]` |

### `AgentRun`

**Table:** `agents_run`

| Field | Type | Description |
|---|---|---|
| `agent_type` | CharField | `AgentType` value |
| `agent_definition` | FK(AgentDefinition) | |
| `reconciliation_result` | FK(ReconciliationResult) | |
| `document_upload` | FK(DocumentUpload) | For extraction runs |
| `status` | CharField | `PENDING / RUNNING / COMPLETED / FAILED / SKIPPED` |
| `input_payload` | JSONField | Initial context |
| `output_payload` | JSONField | `AgentOutput` serialised |
| `summarized_reasoning` | TextField | ASCII-safe reasoning summary |
| `confidence` | FloatField | Final composite confidence |
| `started_at` / `completed_at` | DateTimeField | |
| `duration_ms` | PositiveIntegerField | |
| `error_message` | TextField | |
| `trace_id` / `span_id` | CharField | Distributed trace IDs |
| `prompt_version` | CharField | Prompt hash (max 50 chars) |
| `invocation_reason` | CharField | e.g., `"auto:PARTIAL_MATCH"` |
| `actor_user_id` | PositiveIntegerField | |
| `actor_primary_role` | CharField | e.g., `"SYSTEM_AGENT"` |
| `actor_roles_snapshot_json` | JSONField | Role codes at execution time |
| `permission_source` | CharField | `ROLE / SYSTEM_AGENT / USER` |
| `access_granted` | BooleanField | Guardrail decision |
| `llm_model_used` | CharField | |
| `prompt_tokens` | PositiveIntegerField | Cumulative across all rounds |
| `completion_tokens` | PositiveIntegerField | |
| `total_tokens` | PositiveIntegerField | |
| `cost_estimate` | DecimalField | |

### `AgentOrchestrationRun`

**Table:** `agents_orchestration_run`

| Field | Type | Description |
|---|---|---|
| `reconciliation_result` | FK(ReconciliationResult) | |
| `status` | CharField | `PLANNED / RUNNING / COMPLETED / PARTIAL / FAILED` |
| `plan_source` | CharField | `"llm"` or `"deterministic"` |
| `plan_agents` | JSONField | List of agent types in the plan |
| `agents_completed` | JSONField | List of completed agent types |
| `agents_failed` | JSONField | List of failed agent types |
| `started_at` / `completed_at` | DateTimeField | |
| `actor_user_id` | PositiveIntegerField | |

Acts as the **duplicate-run guard**: the orchestrator rejects entry if a `RUNNING` record exists for the same `reconciliation_result`.

### `AgentRecommendation`

**Table:** `agents_recommendation`

| Field | Type | Description |
|---|---|---|
| `agent_run` | FK(AgentRun) | |
| `reconciliation_result` | FK(ReconciliationResult) | |
| `recommendation_type` | CharField | `RecommendationType` value |
| `confidence` | FloatField | |
| `reasoning` | TextField | |
| `accepted` | BooleanField | Whether AP team accepted |
| `accepted_by` | FK(User) | |
| `accepted_at` | DateTimeField | |

**Uniqueness constraint:** `(reconciliation_result, recommendation_type, agent_run)` -- prevents duplicate recommendations. `RecommendationService.log_recommendation()` also checks for any PENDING recommendation of the same `(result, type)` before creating.

---

## 24. Prompt Registry Integration

**Module:** `apps/core/prompt_registry.py`

Agents retrieve their system prompts by key at runtime. The registry supports prompt versioning and caching.

### Prompt keys used by reconciliation agents

| Agent | Prompt Key |
|---|---|
| `ExceptionAnalysisAgent` | `agent.exception_analysis` |
| `InvoiceExtractionAgent` | `extraction.invoice_system` (or composed) |
| `InvoiceUnderstandingAgent` | `agent.invoice_understanding` |
| `PORetrievalAgent` | `agent.po_retrieval` |
| `GRNRetrievalAgent` | `agent.grn_retrieval` |
| `ReviewRoutingAgent` | `agent.review_routing` |
| `CaseSummaryAgent` | `agent.case_summary` |
| `ReconciliationAssistAgent` | `agent.reconciliation_assist` |

Prompt versions are stamped on `AgentRun.prompt_version` (prompt hash, max 50 chars) for auditability.

Seed prompts via: `python manage.py seed_prompts`

---

## 25. LLM Client Configuration

**File:** `apps/agents/services/llm_client.py`
**Class:** `LLMClient`

Supports both Azure OpenAI and standard OpenAI. Configured via environment variables:

| Setting | Env Var | Default |
|---|---|---|
| API type | `AZURE_OPENAI_API_KEY` (presence selects Azure) | OpenAI |
| Azure endpoint | `AZURE_OPENAI_ENDPOINT` | |
| Azure deployment | `AZURE_OPENAI_DEPLOYMENT` | |
| OpenAI API key | `OPENAI_API_KEY` | |
| Default model | `OPENAI_MODEL` | `gpt-4o` |

### Per-agent temperature settings

| Agent | Temperature | Notes |
|---|---|---|
| `InvoiceExtractionAgent` | 0.0 | Deterministic extraction |
| `ReasoningPlanner` | 0.0 | Deterministic planning |
| All ReAct agents | 0.2 (default) | Allows some creativity for investigation |

### Token limits

| Context | `max_tokens` |
|---|---|
| ReAct loop LLM calls | 4096 (default) |
| Reviewer summary call (ExceptionAnalysis) | 2048 |
| Planner call | 1024 |
| Extraction agent | 8192 |

---

## 26. Langfuse Observability

The reconciliation and agent pipeline emits rich Langfuse traces. All Langfuse calls are fail-silent (errors are caught and ignored).

### Trace structure

```
Root trace: "reconciliation_run" (trace_id = run.trace_id or str(run.pk))
  |
  +--> child span: "po_lookup"      (per invoice, step 1 -- PO lookup with ERP provenance)
  |     |
  |     +--> child span: "erp_resolution"  (when ERP connector available via lf_parent_span)
  |           +--> "erp_cache_lookup"      (BaseResolver cache check)
  |           +--> "erp_live_lookup"       (BaseResolver live API call)
  |           +--> "erp_db_fallback"       (BaseResolver DB fallback)
  |
  +--> child span: "mode_resolution"  (per invoice, step 2)
  +--> child span: "grn_lookup"       (per invoice, THREE_WAY only, step 3)
  +--> child span: "match_execution"  (per invoice, step 4)
  +--> child span: "classification"   (per invoice, step 5)
  +--> child span: "result_persist"   (per invoice, step 6)
  +--> child span: "exception_build"  (per invoice, step 7)
  +--> child span: "review_workflow_trigger" (per invoice, step 8)
  |
  +--> score: "reconciliation_match" (MATCHED=1.0 | PARTIAL=0.5 | REQUIRES_REVIEW=0.3 | UNMATCHED=0.0)

Root trace: "agent_pipeline" (trace_id = reconciliation_result.agent_trace_id)
  |
  +--> child span per agent: "agent_{AGENT_TYPE}"
  |     |
  |     +--> child span per tool call: "tool_{tool_name}"
  |           |
  |           +--> child span: "erp_resolution"  (for po_lookup / grn_lookup tools)
  |                 +--> "erp_cache_lookup" / "erp_live_lookup" / "erp_db_fallback"
  |
  +--> score: "agent_confidence" per agent run
  +--> score: "rbac_guardrail" per guardrail decision (1.0=granted | 0.0=denied)
  +--> score: "rbac_data_scope" (0.0 on deny path only)
```

### ERP resolution span threading

`POLookupService.lookup()` and `GRNLookupService.lookup()` accept `lf_parent_span=`
and pass it to `ERPResolutionService.resolve_po/grn()`. In the reconciliation runner,
`_lf_po` (the po_lookup span) is passed as the parent so ERP spans nest properly.

For agent tools, `BaseAgent._execute_tool()` injects `lf_parent_span=_tool_span`
into tool kwargs. `POLookupTool` and `GRNLookupTool` forward it to the ERP resolver.

ERP resolution spans emit 6 observation scores (success, latency_ok, result_present,
fresh, authoritative, used_fallback). Per-stage spans (cache, live, fallback) emit
additional scores. All metadata is sanitised via `langfuse_helpers.sanitize_erp_metadata()`.

**Full ERP tracing reference**: [LANGFUSE_INTEGRATION.md](../docs/LANGFUSE_INTEGRATION.md) Section 11.

### Score value conventions

| Score Name | Values | Emitted by |
|---|---|---|
| `reconciliation_match` | MATCHED=1.0, PARTIAL=0.5, REQUIRES_REVIEW=0.3, UNMATCHED=0.0 | `ReconciliationRunnerService.run()` |
| `agent_confidence` | 0.0-1.0 composite | `BaseAgent.run()` |
| `rbac_guardrail` | 1.0=granted, 0.0=denied | `AgentGuardrailsService.authorize_*()` |
| `rbac_data_scope` | 0.0 (deny path only) | `authorize_data_scope()` |

---

## 27. Audit Trail and Governance

### AuditEvent types emitted by the reconciliation layer

| Event Type | Trigger |
|---|---|
| `RECONCILIATION_STARTED` | Per invoice at start of `run()` |
| `RECONCILIATION_COMPLETED_SINGLE` | After each invoice in `_reconcile_single()` |
| `RECONCILIATION_COMPLETED` | After all invoices in `run()` |
| `RECONCILIATION_PO_DISCOVERED` | Vendor+amount discovery backfills `invoice.po_number` |
| `RECONCILIATION_ERROR` | Unhandled exception in `_reconcile_single()` |

### AuditEvent types emitted by the agent layer

| Event Type | Trigger |
|---|---|
| `GUARDRAIL_GRANTED` | Successful RBAC check in `AgentGuardrailsService` |
| `GUARDRAIL_DENIED` | Failed RBAC check |
| `TOOL_CALL_AUTHORIZED` | Tool permission check granted |
| `TOOL_CALL_DENIED` | Tool permission denied |
| `RECOMMENDATION_ACCEPTED` | Recommendation accepted by AP team |
| `RECOMMENDATION_DENIED` | Recommendation rejected |
| `AUTO_CLOSE_AUTHORIZED` | Auto-close authorized |
| `AUTO_CLOSE_DENIED` | Auto-close denied |
| `SYSTEM_AGENT_USED` | SYSTEM_AGENT identity was resolved |

### `AgentTraceService` (persistence)

**File:** `apps/agents/services/agent_trace_service.py`

Single entry point for recording all agent activity:

- `log_agent_run(agent_run)` -- persists step-level detail
- `log_tool_call(agent_run, tool_name, args, result, duration_ms)` -- `ToolCall` model
- `log_agent_decision(agent_run, decision_type, content)` -- `DecisionLog` model

### `DecisionLog` model

| Field | Type | Description |
|---|---|---|
| `agent_run` | FK(AgentRun) | |
| `decision_type` | CharField | e.g., `"RECOMMENDATION"`, `"REVIEWER_SUMMARY"` |
| `rationale` | TextField | ASCII-safe decision text |
| `recommendation_type` | CharField | If a recommendation decision |
| `confidence` | FloatField | |
| `created_at` | DateTimeField | |

---

## 28. Configuration Reference

### `ReconciliationConfig` fields

| Field | Default | Description |
|---|---|---|
| `quantity_tolerance_pct` | 2.0 | Strict qty tolerance (%) |
| `price_tolerance_pct` | 1.0 | Strict price tolerance (%) |
| `amount_tolerance_pct` | 1.0 | Strict amount tolerance (%) |
| `review_auto_close_qty_tolerance` | 5.0 | Auto-close band qty (%) |
| `review_auto_close_price_tolerance` | 3.0 | Auto-close band price (%) |
| `review_auto_close_amount_tolerance` | 3.0 | Auto-close band amount (%) |
| `review_auto_close_confidence_threshold` | 0.85 | Min deterministic confidence for auto-close |
| `extraction_confidence_threshold` | 0.75 | Below this -> REQUIRES_REVIEW |
| `enable_mode_resolver` | True | Enable 2-way/3-way mode resolution |
| `default_reconciliation_mode` | THREE_WAY | Fallback mode |

### Environment variables (agent pipeline)

| Variable | Default | Description |
|---|---|---|
| `AGENT_TIMEOUT_SECONDS` | 120 | Per-agent run timeout (overridden by `AgentDefinition.timeout_seconds`) |
| `AGENT_MAX_RETRIES` | 2 | LLM retry count (overridden by `AgentDefinition.max_retries`) |
| `AGENT_CONFIDENCE_THRESHOLD` | 0.75 | Below this -> invoke `INVOICE_UNDERSTANDING` agent |
| `REVIEW_AUTO_CLOSE_THRESHOLD` | 0.85 | MATCHED + confidence >= this -> skip agents |
| `CELERY_TASK_ALWAYS_EAGER` | True (dev) | Run tasks synchronously (Windows dev) |

### Environment variables (ERP source resolution)

| Variable | Default | Description |
|---|---|---|
| `ERP_CACHE_TTL_SECONDS` | 3600 | TTL for `ERPReferenceCacheRecord` |
| `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` | 0.8 | Min confidence for duplicate invoice match |
| `ERP_LIVE_REFRESH_ON_STALE` | False | Trigger live ERP API call if data is stale |
| `ERP_MIRROR_AS_PRIMARY` | False | Treat mirror DB as the primary source |

---

## 29. File Reference

### Reconciliation app (`apps/reconciliation/`)

| File | Responsibility |
|---|---|
| `services/runner_service.py` | Top-level orchestrator for deterministic pipeline |
| `services/mode_resolver.py` | 3-tier mode resolution (policy -> heuristic -> default) |
| `services/po_lookup_service.py` | ERP-backed PO resolution with discovery fallback |
| `services/grn_lookup_service.py` | ERP-backed GRN lookup and hydration |
| `services/execution_router.py` | Dispatches to 2-way or 3-way match service |
| `services/two_way_match_service.py` | Invoice vs PO matching (2-way) |
| `services/three_way_match_service.py` | Invoice vs PO vs GRN matching with ERP provenance propagation |
| `services/header_match_service.py` | Vendor, currency, total, tax comparisons |
| `services/line_match_service.py` | Line-by-line quantity, price, amount matching |
| `services/grn_match_service.py` | GRN receipt verification (`GRNMatchResult`) |
| `services/tolerance_engine.py` | Configurable percentage tolerance comparisons |
| `services/classification_service.py` | 7-gate deterministic decision tree |
| `services/exception_builder_service.py` | Structured exception creation (17 types) |
| `services/result_service.py` | DB persistence + ERP provenance fields |
| `services/agent_feedback_service.py` | Re-reconciliation on PO/GRN recovery |
| `models.py` | ReconciliationRun, ReconciliationResult, ReconciliationResultLine, ReconciliationException, ReconciliationConfig, ReconciliationPolicy |
| `tasks.py` | `run_reconciliation_task` (Celery) |
| `template_views.py` | `start_reconciliation` + case console views |
| `views.py` | DRF ViewSets |
| `api_urls.py` | API routes under `/api/v1/reconciliation/` |

### Agents app (`apps/agents/`)

| File | Responsibility |
|---|---|
| `services/orchestrator.py` | Agent pipeline entry point; RBAC + plan + execution |
| `services/reasoning_planner.py` | LLM-backed planner (always active); PolicyEngine fallback |
| `services/policy_engine.py` | Deterministic 7-rule agent selector; auto-close checks |
| `services/base_agent.py` | BaseAgent with ReAct loop; AgentContext; AgentOutput |
| `services/agent_classes.py` | All 8 agent implementations + AGENT_CLASS_REGISTRY |
| `services/agent_memory.py` | AgentMemory shared in-process state |
| `services/guardrails_service.py` | RBAC checks; SYSTEM_AGENT identity; guardrail audit logging |
| `services/deterministic_resolver.py` | Rule-based replacement for 3 LLM agents |
| `services/agent_trace_service.py` | Unified governance tracing |
| `services/recommendation_service.py` | AgentRecommendation create/accept with dedup |
| `services/decision_log_service.py` | DecisionLog persistence |
| `services/llm_client.py` | LLM client (Azure OpenAI + OpenAI) |
| `models.py` | AgentDefinition, AgentRun, AgentOrchestrationRun, AgentStep, AgentMessage, AgentRecommendation, AgentEscalation |
| `tasks.py` | `run_agent_pipeline_task` (Celery) |

### Tools app (`apps/tools/`)

| File | Responsibility |
|---|---|
| `registry/base.py` | BaseTool, ToolResult, ToolRegistry, @register_tool |
| `registry/tools.py` | All 6 tool implementations |
| `models.py` | ToolDefinition, ToolCall |

### ERP integration (`apps/erp_integration/`)

| File | Responsibility |
|---|---|
| `services/resolution_service.py` | `ERPResolutionService` -- central facade for all resolution types |
| `services/db_fallback/po_fallback.py` | Two-tier PO DB fallback (MIRROR_DB -> DB_FALLBACK) |
| `services/db_fallback/grn_fallback.py` | GRN DB fallback (MIRROR_DB) |
| `services/connectors/base.py` | `ERPResolutionResult` dataclass with provenance fields |
| `enums.py` | `ERPSourceType`, `ERPDataDomain`, `ERPResolutionType` |

### Key config and enum files

| File | What to find |
|---|---|
| `apps/core/enums.py` | `MatchStatus`, `ExceptionType`, `ExceptionSeverity`, `AgentType`, `RecommendationType`, `AgentRunStatus`, `ReconciliationMode`, `ReconciliationModeApplicability` |
| `apps/core/constants.py` | `THREE_WAY_ONLY_EXCEPTION_TYPES` |
| `config/settings.py` | `AGENT_TIMEOUT_SECONDS`, `AGENT_MAX_RETRIES`, `AGENT_CONFIDENCE_THRESHOLD`, `REVIEW_AUTO_CLOSE_THRESHOLD`, all ERP freshness settings |
