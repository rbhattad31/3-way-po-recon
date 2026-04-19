---
mode: agent
description: "Add or modify an invoice posting feature (pipeline stages, mapping, validation, ERP submission)"
---

# Posting Feature

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/POSTING_AGENT.md` -- full posting architecture: 9-stage pipeline, mapping engine, validation, confidence scoring, review routing, governance trail, ERP reference import
- `docs/ERP_INTEGRATION.md` -- resolution chain (cache -> API -> DB fallback), connector integration, provenance tracking
- `docs/current_system_review/10_Integrations_and_External_Dependencies.md` -- ERP framework, live refresh policy, mirror tables as primary source

### Source Files (read in this order)
1. `apps/posting/services/eligibility_service.py` -- 7-check eligibility gate (study what blocks posting)
2. `apps/posting/services/posting_orchestrator.py` -- `PostingOrchestrator.prepare_posting()` lifecycle
3. `apps/posting_core/services/posting_pipeline.py` -- 9-stage pipeline: ELIGIBILITY -> SNAPSHOT -> MAPPING -> VALIDATION -> CONFIDENCE -> REVIEW_ROUTING -> PAYLOAD_BUILD -> FINALIZATION -> STATUS
4. `apps/posting_core/services/posting_mapping_engine.py` -- core value resolution: vendor, item, tax, cost-center, PO. Strategy chain: exact code -> alias -> name -> fuzzy. `connector=` kwarg for ERP API resolution.
5. `apps/posting_core/services/posting_validation.py` -- validate proposal completeness
6. `apps/posting_core/services/posting_confidence.py` -- 5-dimensional weighted score (header 15%, vendor 25%, line mapping 30%, tax 15%, reference freshness 15%)
7. `apps/posting_core/services/posting_review_routing.py` -- 6 review queues: VENDOR_MAPPING_REVIEW, ITEM_MAPPING_REVIEW, TAX_REVIEW, COST_CENTER_REVIEW, PO_REVIEW, POSTING_OPS
8. `apps/posting_core/services/posting_governance_trail.py` -- `PostingGovernanceTrailService` (sole writer of `PostingApprovalRecord`)
9. `apps/posting/services/posting_action_service.py` -- approve / reject / submit / retry actions
10. `apps/posting/models.py` -- `InvoicePosting` (11 statuses), `InvoicePostingFieldCorrection`
11. `apps/posting_core/models.py` -- `PostingRun`, `PostingFieldValue`, `PostingLineItem`, `PostingIssue`, `PostingEvidence`, ERP reference tables
12. `apps/core/enums.py` -- `InvoicePostingStatus`, `PostingStage`, `PostingReviewQueue`, `PostingIssueType`

### Comprehension Check
1. Two-layer architecture: `apps/posting/` (business/UI) + `apps/posting_core/` (platform/core)
2. Pipeline stages run sequentially; failure at any stage records `PostingIssue` and may halt
3. `PostingRun.erp_source_metadata_json` captures per-field ERP resolution provenance
4. `is_touchless=True` when no review needed (all mappings confident, no issues)
5. Confidence thresholds: each dimension produces a sub-score, weighted sum yields `posting_confidence`
6. Phase 1 submission is mock -- `PostingActionService.submit_posting()` does not call real ERP
7. Status lifecycle: `NOT_READY -> READY_FOR_POSTING -> MAPPING_IN_PROGRESS -> MAPPING_REVIEW_REQUIRED | READY_TO_SUBMIT -> SUBMISSION_IN_PROGRESS -> POSTED | POST_FAILED`

---

## When Adding a New Pipeline Stage

1. Add the stage enum value to `PostingStage` in `apps/core/enums.py`
2. Add the stage method to `PostingPipeline` in `posting_pipeline.py` following the existing pattern
3. Add a Langfuse span for the stage (see existing stages for the pattern)
4. If the stage can produce issues, add relevant `PostingIssueType` enum values
5. Wire the stage into the sequential pipeline execution at the correct position
6. Test: stage runs, produces expected output, records correct `PostingRun.stage`

## When Modifying the Mapping Engine

1. Mapping follows a strategy chain per entity: exact code -> alias table -> name match -> fuzzy
2. To add a new resolution strategy, insert it at the correct priority position
3. `PostingMappingEngine` accepts `connector=` kwarg -- when provided, ERP API resolution runs first
4. Each resolved field records source metadata in `erp_source_metadata_json`
5. Test: all strategies in priority order, verify fallback chain works when higher-priority fails

## When Adding a New Review Queue

1. Add enum value to `PostingReviewQueue` in `apps/core/enums.py`
2. Add routing rule in `posting_review_routing.py` that assigns the new queue
3. The rule should check specific issue types or confidence thresholds
4. Test: correct invoices land in the new queue, `is_touchless=False` when review needed

## When Modifying Confidence Scoring

1. Dimension weights in `posting_confidence.py` must sum to 1.0 (header 15%, vendor 25%, line 30%, tax 15%, freshness 15%)
2. Each dimension produces a 0.0-1.0 sub-score
3. Changes to weights require updating all tests that assert confidence values
4. `POSTING_REFERENCE_FRESHNESS_HOURS` (default 168h/7 days) controls freshness decay

---

## Constraints

- Two-layer boundary: business logic in `apps/posting/services/`, pipeline mechanics in `apps/posting_core/services/`
- Governance trail writes go through `PostingGovernanceTrailService` only -- never write `PostingApprovalRecord` directly
- Confidence dimension weights must sum to 1.0
- Never modify `PostingRun.erp_source_metadata_json` after the MAPPING stage completes
- Phase 1 submission is mock -- do not implement real ERP submission without explicit request
- ASCII only in all mapping summaries, issue descriptions, payload values
