# ERP Shared Resolution Architecture

## Overview

Both the **reconciliation engine** and the **posting pipeline** previously
had diverging paths to ERP data. This document describes the unified
resolution model implemented to eliminate that divergence.

---

## Old Architecture (what was replaced)

| Consumer | How it got data | Problem |
|---|---|---|
| `POLookupService` | Direct `PurchaseOrder.objects.filter()` | Bypassed ERP layer entirely |
| `GRNLookupService` | Direct `GoodsReceiptNote.objects.filter()` | No provenance, no freshness check |
| `PostingMappingEngine._load_po_refs()` | Direct `ERPPOReference.objects.filter()` | Used reference snapshot instead of canonical mirror |
| `PostingMappingEngine._try_vendor_via_resolver()` | Instantiated `VendorResolver` directly | Bypassed `ERPResolutionService` facade |
| `POLookupTool._resolve_via_erp()` | Instantiated `POResolver` directly | Same |
| `GRNLookupTool._resolve_via_erp()` | Instantiated `GRNResolver` directly | Same |

Result: no shared freshness semantics, no audit provenance for where data
came from, and no standard "stale data" warning path.

---

## New Architecture

### Single Entry Point: `ERPResolutionService`

`apps/erp_integration/services/resolution_service.py`

All ERP data access -- whether from reconciliation, posting, or agent tools
-- goes through `ERPResolutionService`. It wraps the existing resolver chain
(cache => MIRROR_DB => API => DB_FALLBACK) and applies freshness checks
after every DB-based resolution.

```
Reconciliation          Posting Pipeline        Agent Tools
      |                        |                      |
      v                        v                      v
 POLookupService        PostingMappingEngine    POLookupTool
 GRNLookupService            |                 GRNLookupTool
      |                      |                      |
      +----------+-----------+----------------------+
                 |
                 v
        ERPResolutionService
                 |
       +---------+---------+
       |                   |
   POResolver          GRNResolver
   VendorResolver      ItemResolver
   TaxResolver         CostCenterResolver
   DuplicateInvoiceResolver
       |
   +---+---+
   |   |   |
CACHE API  DB Fallback
           |
     +-----+-----+
     |           |
  MIRROR_DB  DB_FALLBACK
(documents.*) (posting_core.ERP*Reference)
```

### Source Type Priority (highest to lowest freshness guarantee)

| ERPSourceType | Source | Used for |
|---|---|---|
| `API` | Live ERP system call | When connector available and capable |
| `CACHE` | TTL DB cache (`ERPReferenceCacheRecord`) | Repeated lookups within cache TTL |
| `MIRROR_DB` | `documents.PurchaseOrder`, `documents.GoodsReceiptNote` | Transactional PO/GRN data (default) |
| `DB_FALLBACK` | `posting_core.ERP*Reference` (Excel/CSV imports) | Master data (vendor/item/tax/cost center) and PO snapshot fallback |
| `MANUAL_OVERRIDE` | Human-corrected value | Field corrections from review queue |
| `NONE` | Not resolved | Resolution failed |

---

## Data Domain Freshness

The service applies different freshness thresholds based on data domain:

| Domain | Config Setting | Default | Data types |
|---|---|---|---|
| `TRANSACTIONAL` | `ERP_TRANSACTIONAL_FRESHNESS_HOURS` | 24 h | PO, GRN |
| `MASTER` | `ERP_MASTER_FRESHNESS_HOURS` | 168 h (7 d) | Vendor, Item, Tax, Cost Center |

Results returned as `is_stale=True` when `synced_at` exceeds the threshold.
Staleness is a **warning, not a hard failure** -- stale results are still
returned so matching can proceed.

Live refresh on stale data is controlled by `ERP_ENABLE_LIVE_REFRESH_ON_STALE`
(default `false` -- async refresh must be scheduled separately).

---

## Provenance Tracking

Every `ERPResolutionResult` now carries full provenance metadata:

```python
@dataclass
class ERPResolutionResult:
    resolved: bool
    value: Optional[Dict]
    source_type: str          # ERPSourceType value
    fallback_used: bool
    confidence: float
    source_as_of: Optional[datetime]   # when upstream ERP data was valid
    synced_at: Optional[datetime]      # when record was written to our DB
    is_stale: bool
    stale_reason: str
    warnings: List[str]
    source_keys: Dict[str, str]        # raw ERP identifiers
    connector_name: str
    reason: str

    def to_provenance_dict(self) -> Dict:
        """Serialise for storage in JSON fields."""
```

### Where provenance is persisted

| Model field | Content |
|---|---|
| `ReconciliationResult.po_erp_source_type` | `ERPSourceType` value for the PO used |
| `ReconciliationResult.grn_erp_source_type` | `ERPSourceType` value for the GRN(s) used |
| `ReconciliationResult.data_is_stale` | `True` if PO or GRN was beyond freshness threshold |
| `ReconciliationResult.erp_source_metadata_json` | Full `to_provenance_dict()` for both PO and GRN |
| `PostingRun.erp_source_metadata_json` | Per-field provenance (vendor, items, tax, cost center) |

---

## Key File Changes

### `apps/erp_integration/enums.py`
- Added `MIRROR_DB` and `MANUAL_OVERRIDE` to `ERPSourceType`
- Added new `ERPDataDomain` enum (`TRANSACTIONAL`, `MASTER`)

### `apps/erp_integration/services/connectors/base.py`
- Extended `ERPResolutionResult` with 6 new provenance fields
- Added `to_provenance_dict()` method

### `apps/erp_integration/services/resolution_service.py` (new)
- Centralised ERP facade used by all consumers
- Methods: `resolve_po`, `resolve_grn`, `resolve_vendor`, `resolve_item`,
  `resolve_tax_code`, `resolve_cost_center`, `check_invoice_duplicate`,
  `refresh_po`, `refresh_grn`
- `_apply_freshness()` static method checks `synced_at` vs domain threshold

### `apps/erp_integration/services/db_fallback/po_fallback.py`
- Tier 1 now uses `ERPSourceType.MIRROR_DB` (was `DB_FALLBACK`)
- Populates `synced_at` from `po.updated_at`
- Includes `po_id` in value dict for ORM hydration
- Tier 2 (ERPPOReference) still uses `DB_FALLBACK`; populates `source_as_of`
  from import batch metadata

### `apps/erp_integration/services/db_fallback/grn_fallback.py`
- Uses `ERPSourceType.MIRROR_DB`
- Includes `grn_ids: List[int]` in value dict so callers can hydrate ORM objects
- Populates `synced_at` from latest `grn.updated_at`

### `apps/reconciliation/services/po_lookup_service.py`
- Rewritten as thin wrapper over `ERPResolutionService.resolve_po()`
- `POLookupResult` carries `erp_source_type`, `erp_confidence`, `is_stale`,
  `warnings`, `erp_provenance`
- Vendor+amount discovery still exists as a pure-ORM fallback when invoice
  carries no PO number reference

### `apps/reconciliation/services/grn_lookup_service.py`
- Rewritten to call `ERPResolutionService.resolve_grn()` then hydrate
  `GoodsReceiptNote` ORM objects from `result.value["grn_ids"]`
- `GRNSummary` now carries ERP provenance fields

### `apps/reconciliation/services/grn_match_service.py`
- `GRNMatchResult` has new fields: `erp_source_type`, `erp_provenance`, `is_stale`

### `apps/reconciliation/services/three_way_match_service.py`
- Copies GRN provenance from `GRNSummary` into `GRNMatchResult` so it flows
  to `ReconciliationResultService`

### `apps/reconciliation/models.py`
- `ReconciliationResult` has 4 new fields: `po_erp_source_type`,
  `grn_erp_source_type`, `data_is_stale`, `erp_source_metadata_json`
- Migration: `0005_erp_source_provenance`

### `apps/reconciliation/services/result_service.py`
- `save()` now persists ERP provenance from `po_result` and `grn_result`
  into the new `ReconciliationResult` fields

### `apps/posting_core/services/posting_mapping_engine.py`
- Added `_POLineData` dataclass (lightweight PO line data)
- `_load_po_refs()` now tries `ERPResolutionService.resolve_po()` first
  and builds `_POLineData` from the resolved line items; falls back to
  direct `ERPPOReference` query if resolution fails
- `_match_po_line()` works with `List[_POLineData]` (not `ERPPOReference`)
- `_try_vendor_via_resolver()`, `_try_item_via_resolver()`,
  `_try_tax_via_resolver()`, `_try_cost_center_via_resolver()` all call
  `ERPResolutionService` instead of instantiating individual resolvers
- `erp_source_metadata["vendor"]` now uses `result.to_provenance_dict()`

### `apps/tools/registry/tools.py`
- `POLookupTool._resolve_via_erp()` uses `ERPResolutionService.with_default_connector()`
- `GRNLookupTool._resolve_via_erp()` uses `ERPResolutionService.with_default_connector()`
- Tool results now include `_erp_is_stale` field

---

## New Settings

```python
# config/settings.py

# Freshness thresholds (hours)
ERP_TRANSACTIONAL_FRESHNESS_HOURS = int(os.getenv("ERP_TRANSACTIONAL_FRESHNESS_HOURS", "24"))
ERP_MASTER_FRESHNESS_HOURS = int(os.getenv("ERP_MASTER_FRESHNESS_HOURS", "168"))

# Live refresh behaviour
ERP_ENABLE_LIVE_REFRESH_ON_MISS = os.getenv("ERP_ENABLE_LIVE_REFRESH_ON_MISS", "false").lower() == "true"
ERP_ENABLE_LIVE_REFRESH_ON_STALE = os.getenv("ERP_ENABLE_LIVE_REFRESH_ON_STALE", "false").lower() == "true"

# Source priority (mirror = documents.* tables; non-mirror = try API first)
ERP_RECON_USE_MIRROR_AS_PRIMARY = os.getenv("ERP_RECON_USE_MIRROR_AS_PRIMARY", "true").lower() == "true"
ERP_POSTING_USE_MIRROR_AS_PRIMARY = os.getenv("ERP_POSTING_USE_MIRROR_AS_PRIMARY", "true").lower() == "true"
```

---

## Usage Patterns

### Reconciliation

```python
# In runner_service.py (via POLookupService)
from apps.reconciliation.services.po_lookup_service import POLookupService

svc = POLookupService()                          # binds default connector
po_result = svc.lookup(invoice)
# po_result.erp_source_type, po_result.is_stale, po_result.erp_provenance
```

### Posting

```python
# In PostingMappingEngine.__init__
from apps.erp_integration.services.resolution_service import ERPResolutionService

svc = ERPResolutionService(self._connector)
result = svc.resolve_vendor(vendor_code="V001", vendor_name="Acme Ltd")
# result.to_provenance_dict() stored in erp_source_metadata_json
```

### Direct resolution (agent tools, API endpoints)

```python
from apps.erp_integration.services.resolution_service import ERPResolutionService

svc = ERPResolutionService.with_default_connector()
po_result = svc.resolve_po("PO-12345")
grn_result = svc.resolve_grn(po_number="PO-12345")
```

---

## What is NOT changed

- Reconciliation matching logic (`ThreeWayMatchService`, `TwoWayMatchService`,
  `ToleranceEngine`) -- unchanged
- Posting pipeline stages (eligibility, validation, confidence, review routing,
  payload build, finalization) -- unchanged
- ERP resolver internals (`POResolver`, `GRNResolver`, etc.) -- unchanged
- ERP DB fallback adapters (except `po_fallback.py` and `grn_fallback.py`
  source type labelling and `grn_ids` addition) -- unchanged
- Posting import pipeline (`ExcelImportOrchestrator`) -- unchanged
- Everything in `apps/agents/` (except `tools.py`) -- unchanged

---

## Next Steps

1. **Real-time staleness alerts** -- Surface `data_is_stale=True` in the
   reconciliation case console and posting workbench UIs as a warning banner.
2. **Async live refresh** -- Implement a Celery task triggered when
   `ERP_ENABLE_LIVE_REFRESH_ON_STALE=True` and a stale record is encountered.
3. **Provenance API** -- Expose `/api/v1/reconciliation/{id}/erp-provenance/`
   to allow AP teams to inspect the data lineage for any match decision.
4. **`data_is_stale` filter** -- Add to reconciliation result list view and API
   filter backends so ops can quickly find results with potentially outdated data.

---

## Langfuse Observability

All `resolve_*()` methods on `ERPResolutionService` accept an optional
`lf_parent_span` kwarg. When provided, the resolution chain creates nested
Langfuse child spans (`erp_resolution` -> `erp_cache_lookup` / `erp_live_lookup` /
`erp_db_fallback`) with evaluation-ready observation scores.

Helpers live in `apps/erp_integration/services/langfuse_helpers.py` -- see
[LANGFUSE_INTEGRATION.md](LANGFUSE_INTEGRATION.md) Section 11 for the full
span hierarchy, scores reference, metadata sanitisation rules, and caller
threading patterns.
