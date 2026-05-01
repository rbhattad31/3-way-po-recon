---
description: "Use when working on the invoice posting pipeline, PostingPipeline stages, PostingMappingEngine, ERP reference imports, posting status lifecycle, or PostingRun confidence scoring. Also covers the InvoicePosting model, posting tasks, and posting governance trail."
applyTo: "apps/posting/**/*.py,apps/posting_core/**/*.py"
---
# Invoice Posting Pipeline Conventions

## Two-Layer Architecture
- `apps/posting/` — business/UI layer (eligibility, orchestrator, action service, views)
- `apps/posting_core/` — platform/core layer (mapping engine, pipeline, validation, confidence, review routing, governance trail)

## 9-Stage Pipeline (PostingPipeline)
1. ELIGIBILITY_CHECK — verify invoice is approved and ready
2. SNAPSHOT_BUILD — capture invoice data snapshot
3. MAPPING — resolve vendor/item/tax/cost-center via ERP then DB fallback
4. VALIDATION — validate mapped values
5. CONFIDENCE — 5-dimensional weighted score
6. REVIEW_ROUTING — determine if human review needed
7. PAYLOAD_BUILD — construct ERP-ready payload
8. FINALIZATION — record final state
9. STATUS — update `InvoicePosting.status` + duplicate check

## Posting Status Lifecycle
```
NOT_READY -> READY_FOR_POSTING -> MAPPING_IN_PROGRESS
  -> MAPPING_REVIEW_REQUIRED | READY_TO_SUBMIT
  -> SUBMISSION_IN_PROGRESS -> POSTED | POST_FAILED
  -> RETRY_PENDING | REJECTED | SKIPPED
```

## Confidence Scoring — 5 Dimensions
| Dimension | Weight |
|-----------|--------|
| Header completeness | 15% |
| Vendor mapping | 25% |
| Line item mapping | 30% |
| Tax completeness | 15% |
| Reference freshness | 15% |

`is_touchless=True` when no review routing needed.

## ERP Reference Resolution (PostingMappingEngine)
- Strategy chain per field: exact code -> alias -> name -> fuzzy
- When `connector` kwarg provided: ERP resolver first, then DB fallback
- Per-field ERP provenance stored in `PostingRun.erp_source_metadata_json`

## Review Queues
`VENDOR_MAPPING_REVIEW`, `ITEM_MAPPING_REVIEW`, `TAX_REVIEW`, `COST_CENTER_REVIEW`, `PO_REVIEW`, `POSTING_OPS`

## Governance
- 17 posting-specific `AuditEventType` values
- `PostingGovernanceTrailService` is the SOLE writer of `PostingApprovalRecord`
- Phase 1 submit is mock — `PostingActionService.submit_posting()` is placeholder

## Reference Freshness
Controlled by `POSTING_REFERENCE_FRESHNESS_HOURS` (default 168h / 7 days).

## Trigger
`ExtractionApprovalService.approve()` / `try_auto_approve()` enqueues `prepare_posting_task` automatically (best-effort; never blocks approval).
