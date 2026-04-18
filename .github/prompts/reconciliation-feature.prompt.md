---
mode: agent
description: "Add or modify a reconciliation feature (matching, tolerance, exceptions, mode resolution, line matching)"
---

# Reconciliation Feature

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/RECON_AGENT.md` -- full reconciliation + agent pipeline reference (29 sections covering runner, mode resolution, PO lookup, matching, tolerance, classification, exceptions, agent orchestration, tools)
- `docs/current_system_review/05_Features_and_Workflows.md` -- reconciliation workflow overview, mode-aware pipeline, status transitions
- `docs/current_system_review/06_Data_Model_and_Entity_Guide.md` -- ReconciliationRun, ReconciliationResult, ReconciliationResultLine, ReconciliationException models

### Source Files (read in this order)
1. `apps/reconciliation/services/runner_service.py` -- `ReconciliationRunnerService` orchestrator (study the per-invoice loop, mode resolution, Langfuse spans)
2. `apps/reconciliation/services/mode_resolver.py` -- 3-tier mode cascade: ReconciliationPolicy -> heuristic -> config default
3. `apps/reconciliation/services/po_lookup_service.py` -- ERP-backed PO lookup with fallback to `documents.PurchaseOrder`
4. `apps/reconciliation/services/execution_router.py` -- `ReconciliationExecutionRouter` dispatches to TwoWayMatch or ThreeWayMatch
5. `apps/reconciliation/services/two_way_match_service.py` -- Invoice vs PO matching (header + lines)
6. `apps/reconciliation/services/three_way_match_service.py` -- Invoice vs PO vs GRN matching
7. `apps/reconciliation/services/tolerance_engine.py` -- tiered tolerance: strict (qty 2%, price 1%, amount 1%) + auto-close (qty 5%, price 3%, amount 3%)
8. `apps/reconciliation/services/classification_service.py` -- match status assignment (MATCHED, PARTIAL_MATCH, UNMATCHED, REQUIRES_REVIEW)
9. `apps/reconciliation/services/exception_builder_service.py` -- structured exception creation with `applies_to_mode` tagging
10. `apps/reconciliation/services/line_match_service.py` -- deterministic multi-signal line scorer (v2: 11 signals, ambiguity detection)
11. `apps/reconciliation/services/line_match_types.py` -- `LineCandidateScore`, `LineMatchDecision`, threshold constants
12. `apps/reconciliation/services/line_match_helpers.py` -- text normalization, token similarity, UOM equivalence
13. `apps/core/enums.py` -- `ReconciliationMode`, `MatchStatus`, `ExceptionType`, `ReconciliationModeApplicability`

### Comprehension Check
1. The runner loops per-invoice: PO lookup -> mode resolution -> GRN lookup (if 3-way) -> match execution -> classification -> exception build -> result persist -> review trigger
2. Mode resolver cascade: (a) ReconciliationPolicy table match, (b) heuristic from PO line item flags, (c) ReconciliationConfig default
3. Line matching uses 11 weighted signals (item_code 0.30, desc_exact 0.20, token_sim 0.15, fuzzy 0.10, qty 0.10, ...) with 4 penalty types and 5 confidence bands
4. Tolerance engine has two bands: strict (for MATCHED determination) and auto-close (for PARTIAL_MATCH that can be auto-closed by PolicyEngine)
5. Exceptions carry `applies_to_mode` (TWO_WAY, THREE_WAY, BOTH) to suppress irrelevant exceptions
6. Classification assigns `ReconciliationResult.match_status` + `reconciliation_mode` + `mode_resolved_by`

---

## When Adding a New Exception Type

1. Add enum value to `ExceptionType` in `apps/core/enums.py`
2. Set `applies_to_mode` (TWO_WAY_ONLY, THREE_WAY_ONLY, BOTH) using `ReconciliationModeApplicability`
3. Add creation logic in `apps/reconciliation/services/exception_builder_service.py`
4. Update classification rules in `classification_service.py` if the new exception affects match status
5. Write tests: exception created for correct mode, suppressed for wrong mode

## When Modifying Tolerance Thresholds

1. Update `ReconciliationConfig` model defaults or the config record in DB
2. Verify `tolerance_engine.py` reads from config, not hardcoded values
3. Test both strict and auto-close bands are applied correctly
4. Test the boundary: value exactly at threshold, value 0.01% above, value 0.01% below

## When Adding a New Line-Match Signal

1. Add the signal weight constant to `line_match_types.py` (ensure all weights still sum to 1.0)
2. Implement the scorer function in `line_match_helpers.py`
3. Call it from `LineMatchService._score_candidate()` in `line_match_service.py`
4. Add the signal score field to `ReconciliationResultLine` model if it needs persistence
5. Run migration if model changed
6. Test: signal contributes correct weight, does not break existing match outcomes

## When Modifying the Mode Resolver

1. If adding a new resolution tier, insert it at the correct priority position in `mode_resolver.py`
2. Set `mode_resolved_by` on the result to indicate which tier decided
3. Test the full cascade: policy match -> heuristic match -> default fallback

---

## Constraints

- Never modify tolerance thresholds without also updating tests for boundary cases
- Line match signal weights must sum to 1.0
- Exceptions must declare `applies_to_mode` -- never create mode-unaware exceptions
- All reconciliation results carry `reconciliation_mode` and `mode_resolved_by`
- ASCII only in exception descriptions, match summaries, log messages
- Agent pipeline runs automatically for non-MATCHED results -- verify your change does not break this trigger
