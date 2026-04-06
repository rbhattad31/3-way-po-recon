# ERP Integration Layer

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [ERP Connections](#3-erp-connections)
4. [Connector Types](#4-connector-types)
5. [Resolution Chain](#5-resolution-chain)
6. [ERPResolutionService -- single entry point](#6-erpresolutionservice)
7. [Resolvers](#7-resolvers)
8. [DB Fallback Adapters](#8-db-fallback-adapters)
9. [Cache Service](#9-cache-service)
10. [Provenance and Freshness](#10-provenance-and-freshness)
11. [Invoice Submission](#11-invoice-submission)
12. [Audit Service](#12-audit-service)
13. [Reference Data Import](#13-reference-data-import)
14. [Configuration Settings](#14-configuration-settings)
15. [API Endpoints](#15-api-endpoints)
16. [Reference Data UI](#16-reference-data-ui)
17. [Integration Points](#17-integration-points)
18. [Adding a New Connector](#18-adding-a-new-connector)
19. [Adding a New Resolver](#19-adding-a-new-resolver)
20. [Troubleshooting](#20-troubleshooting)
21. [Langfuse Observability](#21-langfuse-observability)

---

## 1. Overview

The ERP integration layer (`apps/erp_integration/`) is the shared infrastructure
that connects the 3-way PO reconciliation platform to external ERP systems. Every
ERP lookup -- whether triggered by the reconciliation engine, the invoice posting
pipeline, or an AI agent tool -- goes through this layer.

### What it provides

| Capability | Description |
|---|---|
| **Lookup** | Resolve PO, GRN, Vendor, Item, Tax Code, Cost Center from any configured ERP |
| **Submission** | POST reconciled invoices to ERP (create or park) |
| **Duplicate check** | Detect invoices already posted to ERP |
| **Cache** | TTL-based DB cache to avoid repeated API calls |
| **Reference import** | Batch ingest of ERP master data from Excel/CSV |
| **Provenance tracking** | Full source metadata (where, when, how fresh) on every resolved value |
| **Audit trail** | Every lookup and submission logged to `ERPResolutionLog` / `ERPSubmissionLog` |

### Key design principles

- **Single entry point**: All consumers call `ERPResolutionService`. No direct
  instantiation of individual resolvers from outside `erp_integration/`.
- **Fail-soft**: Staleness is a warning, not a failure. Missing live connectors
  fall through to DB fallback automatically.
- **No raw secrets**: Auth credentials are stored as env var references
  (`api_key_env`, `client_secret_env`), never as plain text in the DB.
- **Full provenance**: Every `ERPResolutionResult` carries source type, confidence,
  freshness timestamp, and warnings -- storable in JSON fields on `ReconciliationResult`
  and `PostingRun`.

---

## 2. Architecture

```
Reconciliation Engine          Posting Pipeline            Agent Tools
        |                             |                         |
  POLookupService             PostingMappingEngine        POLookupTool
  GRNLookupService                   |                   GRNLookupTool
        |                            |                         |
        +------------+---------------+-------------------------+
                     |
                     v
           ERPResolutionService          <-- single facade
                     |
         +-----------+-----------+
         |           |           |
    POResolver  GRNResolver  VendorResolver  ...7 resolvers total
         |           |           |
   +-----+-----+-----+-----+-----+
   |     |     |     |     |     |
CACHE   API  MIRROR  API  DB    DB
              _DB   FALL FALL  FALL
                    BACK  BACK  BACK

CACHE      -- ERPReferenceCacheRecord (TTL, SHA-256 keyed)
API        -- Live ERP connector (Dynamics / Zoho / Salesforce / Custom / SQL)
MIRROR_DB  -- documents.PurchaseOrder, documents.GoodsReceiptNote
DB_FALLBACK -- posting_core.ERP*Reference (imported from Excel/CSV)
```

### Source type priority (highest freshness first)

| ERPSourceType | Source | Confidence |
|---|---|---|
| `API` | Live call to connected ERP | 1.0 |
| `CACHE` | Recent API result, within TTL | inherited from cached call |
| `MIRROR_DB` | Local `documents.PurchaseOrder` / `GoodsReceiptNote` | 1.0 |
| `DB_FALLBACK` (tier 1) | `documents.PurchaseOrder` -- PO fallback first tier | 1.0 |
| `DB_FALLBACK` (tier 2) | `posting_core.ERPPOReference` snapshot | 0.75 |
| `DB_FALLBACK` (master) | `ERPVendorReference`, `ERPItemReference`, etc. | 0.8-0.9 |
| `MANUAL_OVERRIDE` | Human-corrected value | 1.0 |
| `NONE` | Resolution failed | 0.0 |

---

## 3. ERP Connections

### Model: `ERPConnection`

`apps/erp_integration/models.py`

Each ERP connection is a row in the DB. The platform supports multiple connections
simultaneously. One connection is designated as the default (`is_default=True`).

#### Key fields

| Field | Type | Purpose |
|---|---|---|
| `name` | CharField (unique) | Human-readable label |
| `connector_type` | CharField | `CUSTOM`, `DYNAMICS`, `ZOHO`, `SALESFORCE`, `SQLSERVER`, `MYSQL` |
| `base_url` | URLField | Base URL for REST connectors |
| `status` | CharField | `ACTIVE`, `INACTIVE`, `ERROR` |
| `is_default` | BooleanField | Used by `ConnectorFactory.get_default_connector()` |
| `timeout_seconds` | PositiveIntegerField | API call timeout (default 30 s) |
| `auth_type` | CharField | `BEARER`, `BASIC`, `API_KEY`, `OAUTH2` |
| `api_key_env` | CharField | Env var name holding API key / bearer token |
| `tenant_id` | CharField | OAuth tenant/org ID (cloud ERPs) |
| `client_id_env` | CharField | Env var name for OAuth client ID |
| `client_secret_env` | CharField | Env var name for OAuth client secret |
| `connection_string_env` | CharField | Env var name for ODBC connection string |
| `db_host` / `db_port` / `db_username` | CharField | SQL Server direct-DB fields |
| `db_password_encrypted` | TextField | Fernet-encrypted password |
| `db_trust_cert` | BooleanField | Append `TrustServerCertificate=yes` (on-prem SQL Server) |
| `metadata_json` | JSONField | Connector-specific extras, e.g. `{"endpoints": {...}}` for Custom ERP |

#### Security: no raw secrets

Credentials are never stored as plain text. The fields ending in `_env` store
the name of an environment variable. `resolve_secret()` (`apps/erp_integration/services/secrets_resolver.py`)
reads the value at runtime from `os.environ`. Passwords for SQL connectors are
Fernet-encrypted using `apps/erp_integration/crypto.py`.

### ConnectorFactory

`apps/erp_integration/services/connector_factory.py`

```python
from apps.erp_integration.services.connector_factory import ConnectorFactory

# Get the active default connector
connector = ConnectorFactory.get_default_connector()   # None if none configured

# Get a specific connection by name
connector = ConnectorFactory.get_connector_by_name("Dynamics Prod")

# Instantiate from a saved ERPConnection record
connector = ConnectorFactory.create_from_connection(connection)

# Instantiate without a DB record (testing / one-off)
connector = ConnectorFactory.create_from_config({
    "connector_type": "CUSTOM",
    "base_url": "https://erp.example.com",
    "api_key_env": "MY_ERP_KEY",
})
```

`get_default_connector()` requires an `ERPConnection` with `is_default=True`,
`status=ACTIVE`, and `is_active=True`. Returns `None` if none found.

---

## 4. Connector Types

All connectors extend `BaseERPConnector` (`apps/erp_integration/services/connectors/base.py`).

### Capability matrix

| Method | Custom | Dynamics | Zoho | Salesforce | SQLServer | MySQL |
|---|---|---|---|---|---|---|
| `supports_vendor_lookup()` | yes | yes | yes | yes | if custom SQL | if custom SQL |
| `supports_po_lookup()` | yes | yes | yes | no | if custom SQL | if custom SQL |
| `supports_grn_lookup()` | yes | yes | no | no | if custom SQL | if custom SQL |
| `supports_item_lookup()` | yes | yes | yes | yes | if custom SQL | if custom SQL |
| `supports_tax_lookup()` | yes | yes | yes | no | if custom SQL | if custom SQL |
| `supports_cost_center_lookup()` | yes | yes | no | yes | if custom SQL | if custom SQL |
| `supports_duplicate_check()` | yes | yes | no | no | if custom SQL | if custom SQL |
| `supports_invoice_posting()` | yes | yes | no | no | no | no |
| `supports_invoice_parking()` | yes | yes | no | no | no | no |

### CustomERPConnector

`apps/erp_integration/services/connectors/custom_erp.py`

Generic REST connector. Endpoint paths are configurable in `metadata_json["endpoints"]`
on the `ERPConnection` record. Falls back to built-in defaults when a path is not
overridden.

Default endpoints:

```json
{
  "vendor_lookup":    "/api/vendors/lookup",
  "item_lookup":      "/api/items/lookup",
  "tax_lookup":       "/api/tax-codes/lookup",
  "cost_center_lookup": "/api/cost-centers/lookup",
  "po_lookup":        "/api/purchase-orders/lookup",
  "grn_lookup":       "/api/grns/lookup",
  "duplicate_check":  "/api/invoices/duplicate-check",
  "invoice_create":   "/api/invoices/create",
  "invoice_park":     "/api/invoices/park",
  "invoice_status":   "/api/invoices/{document_number}/status"
}
```

Auth: reads env var named by `api_key_env` on the connection record; sends
as `Authorization: Bearer <token>`.

### DynamicsConnector

`apps/erp_integration/services/connectors/dynamics.py`

Microsoft Dynamics 365 (Business Central / F&O). Uses MSAL OAuth2 with
client credentials flow. Tenant, client ID, and client secret are all stored
as env var references.

### ZohoConnector

`apps/erp_integration/services/connectors/zoho.py`

Zoho Books / Zoho Inventory REST API. Uses OAuth2 access token flow.

### SalesforceConnector

`apps/erp_integration/services/connectors/salesforce.py`

Salesforce REST API. Vendor, item, and cost center lookups via SOQL queries.

### SQLServerERPConnector

`apps/erp_integration/services/connectors/sqlserver.py`

Direct database connector to on-premises SQL Server. Uses `pyodbc`. Connection
string built from DB host/port/username and Fernet-decrypted password. SQL queries
are defined by the implementing team in `metadata_json["queries"]`. All query
parameters are passed as bound parameters; no string formatting of user input.

### MySQLERPConnector

`apps/erp_integration/services/connectors/mysql.py`

Direct database connector to MySQL/MariaDB. Same pattern as SQLServer connector.

### `BaseERPConnector` contract

When implementing a new connector, override the relevant capability flags and
lookup methods. Non-overridden methods return an `ERPResolutionResult(resolved=False)`
or `ERPSubmissionResult(success=False, status=UNSUPPORTED)`.

```python
class BaseERPConnector:
    connector_name: str = "base"

    def __init__(self, connection_config: Dict[str, Any]) -> None: ...

    # -- Capabilities --
    def supports_vendor_lookup(self) -> bool: return False
    def supports_po_lookup(self) -> bool: return False
    # ... etc for all 9 capability methods

    # -- Connectivity test --
    def test_connectivity(self) -> tuple[bool, str]: ...

    # -- Lookup methods --
    def lookup_vendor(self, vendor_code: str, **kwargs) -> ERPResolutionResult: ...
    def lookup_po(self, po_number: str, **kwargs) -> ERPResolutionResult: ...
    def lookup_grn(self, po_number: str, **kwargs) -> ERPResolutionResult: ...
    def lookup_item(self, item_code: str, **kwargs) -> ERPResolutionResult: ...
    def lookup_tax_code(self, tax_code: str, **kwargs) -> ERPResolutionResult: ...
    def lookup_cost_center(self, cost_center_code: str, **kwargs) -> ERPResolutionResult: ...
    def check_duplicate_invoice(self, invoice_number: str, vendor_code: str, **kwargs) -> ERPResolutionResult: ...

    # -- Submission methods --
    def create_invoice(self, payload: Dict) -> ERPSubmissionResult: ...
    def park_invoice(self, payload: Dict) -> ERPSubmissionResult: ...
    def get_invoice_status(self, document_number: str) -> ERPSubmissionResult: ...
```

---

## 5. Resolution Chain

Every resolver follows the same chain managed by `BaseResolver`:

```
1. Check connector capability
         |
         | connector available and supports this lookup type?
         v
2. Check cache (ERPReferenceCacheRecord, TTL-controlled)
         |
         | cache hit? return immediately with source_type=CACHE
         v
3. Live API lookup via connector
         |
         | resolved? cache result, return with source_type=API
         v (not resolved or exception)
4. DB fallback adapter
         |
         | returns result with source_type=MIRROR_DB or DB_FALLBACK
         v
5. Log to ERPResolutionLog + AuditEvent
         |
         v
6. Return ERPResolutionResult to caller
```

The chain is implemented in `BaseResolver.resolve()` (`apps/erp_integration/services/resolution/base.py`).
Subclasses implement only three methods:

```python
class MyResolver(BaseResolver):
    resolution_type = ERPResolutionType.VENDOR

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_vendor_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_vendor(**params)

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return VendorFallbackAdapter.resolve(**params)
```

---

## 6. ERPResolutionService

`apps/erp_integration/services/resolution_service.py`

The single public facade used by all platform consumers. Wraps the 7 resolvers
and applies freshness checks after every DB-based resolution.

### Instantiation

```python
from apps.erp_integration.services.resolution_service import ERPResolutionService

# With the default active connector (preferred)
svc = ERPResolutionService.with_default_connector()

# With a specific connector (e.g. from PostingPipeline)
svc = ERPResolutionService(connector=my_connector)

# No connector -- DB-only path
svc = ERPResolutionService()
```

### Methods

```python
# Transactional data
svc.resolve_po(po_number, vendor_code="", *, invoice_id, reconciliation_result_id, posting_run_id)
svc.resolve_grn(po_number, *, invoice_id, reconciliation_result_id)

# Master data
svc.resolve_vendor(vendor_code, vendor_name="", *, posting_run_id)
svc.resolve_item(item_code, item_description="", *, posting_run_id)
svc.resolve_tax_code(tax_code, *, posting_run_id)
svc.resolve_cost_center(cost_center_code, *, posting_run_id)

# Duplicate check
svc.check_invoice_duplicate(invoice_number, vendor_code, amount, *, invoice_id)

# Manual refresh (bypasses cache)
svc.refresh_po(po_number)
svc.refresh_grn(po_number)
```

All methods return `ERPResolutionResult`. All cross-reference IDs (`invoice_id`,
`reconciliation_result_id`, `posting_run_id`) are stored in the `ERPResolutionLog`
for compliance tracing.

### Return value: `ERPResolutionResult`

```python
@dataclass
class ERPResolutionResult:
    resolved: bool              # True = a usable result was found
    value: Optional[Dict]       # Normalised data (when resolved=True)
    source_type: str            # ERPSourceType value
    fallback_used: bool         # True = secondary source used
    confidence: float           # 0.0-1.0 quality score
    source_as_of: Optional[datetime]   # When upstream data was valid
    synced_at: Optional[datetime]      # When our DB record was written
    is_stale: bool              # synced_at exceeded freshness threshold
    stale_reason: str           # Human-readable staleness explanation
    warnings: List[str]         # Non-blocking notices
    source_keys: Dict[str, str] # Raw ERP identifiers
    connector_name: str         # ERPConnection.name used
    reason: str                 # Short outcome description
    metadata: Dict[str, Any]    # Freeform extra data

    def to_provenance_dict(self) -> Dict:
        """Serialise for storage in JSON fields."""
```

---

## 7. Resolvers

Seven resolvers live in `apps/erp_integration/services/resolution/`.

### POResolver

Resolves a single PO by its PO number. The DB fallback is **two-tier**:

| Tier | Source | ERPSourceType | Confidence |
|---|---|---|---|
| 1 | `documents.PurchaseOrder` | `MIRROR_DB` | 1.0 |
| 2 | `posting_core.ERPPOReference` | `DB_FALLBACK` | 0.75 |

Tier 2 adds `_source_tier: "erp_reference_snapshot"` and `_warning` to
the result value dict so consumers know they are using a lower-quality snapshot.

### GRNResolver

Resolves all GRNs linked to a PO. DB fallback queries `documents.GoodsReceiptNote`
directly (`MIRROR_DB`). The `value` dict includes `grn_ids: List[int]` so callers
can hydrate ORM objects without a second query.

### VendorResolver

Strategy chain: exact vendor code match -> alias match -> name match ->
fuzzy name match. DB fallback queries `posting_core.ERPVendorReference`.

### ItemResolver

Strategy chain: exact item code -> alias -> description fuzzy match.
DB fallback queries `posting_core.ERPItemReference`.

### TaxResolver

Exact tax code match. DB fallback queries `posting_core.ERPTaxCodeReference`.

### CostCenterResolver

Exact cost center code match. DB fallback queries `posting_core.ERPCostCenterReference`.

### DuplicateInvoiceResolver

Checks whether an invoice number + vendor code combination already exists in ERP.
DB fallback queries `posting_core.ERPDuplicateInvoiceLog` if the table exists,
otherwise returns `resolved=False` (fail-safe -- never blocks posting).

Confidence threshold for treating a hit as a duplicate is controlled by
`ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` (default 0.8).

---

## 8. DB Fallback Adapters

`apps/erp_integration/services/db_fallback/`

One adapter per resolution type. Each adapter returns an `ERPResolutionResult`
populated from our local database, isolating the resolver from ORM details.

| Adapter file | Queries |
|---|---|
| `po_fallback.py` | `documents.PurchaseOrder` (tier 1), `posting_core.ERPPOReference` (tier 2) |
| `grn_fallback.py` | `documents.GoodsReceiptNote` |
| `vendor_fallback.py` | `posting_core.ERPVendorReference` |
| `item_fallback.py` | `posting_core.ERPItemReference` |
| `tax_fallback.py` | `posting_core.ERPTaxCodeReference` |
| `cost_center_fallback.py` | `posting_core.ERPCostCenterReference` |
| `duplicate_invoice_fallback.py` | `posting_core.ERPDuplicateInvoiceLog` |

### PO fallback detail

```
po_fallback._resolve()
    |
    +-- Tier 1: PurchaseOrder.objects.filter(po_number=...) [MIRROR_DB, conf=1.0]
    |       - populates synced_at from po.updated_at
    |       - includes po_id in value dict for ORM hydration
    |
    +-- Tier 2 (if tier 1 not found):
            ERPPOReference.objects.filter(po_number=...) [DB_FALLBACK, conf=0.75]
            - adds _source_tier="erp_reference_snapshot"
            - adds _warning to value dict
            - populates source_as_of from import batch metadata
```

---

## 9. Cache Service

`apps/erp_integration/services/cache_service.py` / model `ERPReferenceCacheRecord`

### How it works

- Cache key: `erp:{resolution_type}:{sha256(sorted_params)[:16]}`
- TTL: `ERP_CACHE_TTL_SECONDS` (default 3600 s = 1 hour)
- Only API results are cached (not DB fallback results)
- Expired entries are ignored, not deleted eagerly (cleaned up on next miss)

### API

```python
from apps.erp_integration.services.cache_service import ERPCacheService

# Read
result = ERPCacheService.get("PO", po_number="PO-001")   # None on miss/expiry

# Write (called automatically by BaseResolver on API success)
ERPCacheService.put("PO", result, po_number="PO-001")

# Invalidate all entries for a type (called after reference import)
count = ERPCacheService.invalidate_by_type("VENDOR")

# Invalidate everything
count = ERPCacheService.invalidate_all()
```

### Disabling cache per resolver

Set `use_cache = False` on a resolver subclass to skip the cache
entirely for that resolution type.

---

## 10. Provenance and Freshness

### Freshness thresholds

| Domain | Env var | Default | Data types |
|---|---|---|---|
| `TRANSACTIONAL` | `ERP_TRANSACTIONAL_FRESHNESS_HOURS` | 24 h | PO, GRN |
| `MASTER` | `ERP_MASTER_FRESHNESS_HOURS` | 168 h (7 d) | Vendor, Item, Tax, Cost Center |

After any DB resolution, `ERPResolutionService._apply_freshness()` checks
`synced_at` against the threshold. If exceeded, `is_stale=True` and
`stale_reason` is populated. The result is still returned -- staleness
is a warning, not a hard failure.

### Live refresh on stale / miss

| Setting | Default | Effect |
|---|---|---|
| `ERP_ENABLE_LIVE_REFRESH_ON_MISS` | `false` | When true, after a DB miss the service attempts a live API call |
| `ERP_ENABLE_LIVE_REFRESH_ON_STALE` | `false` | When true, after returning a stale result the service schedules an async API refresh |

### Where provenance is persisted

| Model field | Content |
|---|---|
| `ReconciliationResult.po_erp_source_type` | `ERPSourceType` used for the PO |
| `ReconciliationResult.grn_erp_source_type` | `ERPSourceType` used for the GRN(s) |
| `ReconciliationResult.data_is_stale` | `True` if PO or GRN exceeded freshness threshold |
| `ReconciliationResult.erp_source_metadata_json` | Full `to_provenance_dict()` for both PO and GRN |
| `PostingRun.erp_source_metadata_json` | Per-field provenance (vendor, items, tax, cost center) |

### Reading provenance in code

```python
result = ReconciliationResult.objects.get(pk=42)

# Quick check
if result.data_is_stale:
    print("One or more ERP sources exceeded the freshness threshold")

# Full metadata
meta = result.erp_source_metadata_json
po_meta = meta.get("po", {})
print(po_meta["source_type"])   # e.g. "MIRROR_DB"
print(po_meta["is_stale"])      # True / False
print(po_meta["synced_at"])     # ISO datetime string
print(po_meta["warnings"])      # list of strings
```

---

## 11. Invoice Submission

`apps/erp_integration/services/submission/posting_submit_resolver.py`

Submission is API-only -- there is no DB fallback. If the connector is
not available or does not support the submission type, the call returns
`ERPSubmissionResult(success=False, status=UNSUPPORTED)`.

### Flow

```
PostingActionService.submit_posting()
        |
        v
PostingSubmitResolver.submit_invoice(connector, payload,
    submission_type=CREATE_INVOICE or PARK_INVOICE)
        |
        +-- connector is None? --> UNSUPPORTED
        +-- connector.supports_invoice_posting()? --> UNSUPPORTED
        |
        v
connector.create_invoice(payload) or connector.park_invoice(payload)
        |
        v
ERPSubmissionLog.create(...)   + AuditEvent
        |
        v
ERPSubmissionResult(success, erp_document_number, error_code, ...)
```

### Return value: `ERPSubmissionResult`

```python
@dataclass
class ERPSubmissionResult:
    success: bool
    status: str                 # ERPSubmissionStatus value
    erp_document_number: str    # ERP-assigned document ref (on success)
    error_code: str
    error_message: str
    response_data: Dict         # Raw ERP response
    connector_name: str
    duration_ms: int
```

### Phase 1 note

`PostingActionService.submit_posting()` currently contains a **Phase 1 mock**
that returns success without making a real API call. Replace the mock with
`PostingSubmitResolver.submit_invoice(connector, payload)` for Phase 2.

---

## 12. Audit Service

`apps/erp_integration/services/audit_service.py`

`ERPAuditService` has two static methods:

```python
ERPAuditService.log_resolution(
    event_type, description,
    resolution_type, lookup_key, source_type, resolved,
    invoice_id, reconciliation_result_id, posting_run_id,
    connector_name, duration_ms, metadata,
)

ERPAuditService.log_submission(
    event_type, description,
    submission_type, invoice_id, posting_run_id,
    connector_name, duration_ms, success, metadata,
)
```

Both methods delegate to `AuditService.log_event()` after running `_mask_metadata()`
to redact any credential keys from the metadata dict. They are fail-silent --
exceptions are caught and logged, never re-raised.

### ERPResolutionLog

Every resolution attempt (cache hit, API hit, DB fallback, miss) is persisted to
`ERPResolutionLog`. Key fields: `resolution_type`, `lookup_key`, `source_type`,
`resolved`, `fallback_used`, `confidence`, `duration_ms`, `freshness_timestamp`,
plus foreign keys to `Invoice`, `ReconciliationResult`, `PostingRun`.

### ERPSubmissionLog

Every submission attempt is persisted to `ERPSubmissionLog`. Key fields:
`submission_type`, `status`, `erp_document_number`, `error_code`,
`connector_name`, `duration_ms`, plus a FK to `InvoicePosting`.

---

## 13. Reference Data Import

`apps/posting_core/services/import_pipeline/`

Master ERP reference data (vendors, items, tax codes, cost centers, open POs)
can be imported from Excel or CSV files via `ExcelImportOrchestrator`.

### Import targets

| Table | Importer class | Notes |
|---|---|---|
| `ERPVendorReference` | `VendorImporter` | Vendor code, name, country, currency |
| `ERPItemReference` | `ItemImporter` | Item code, description, UOM, GL account |
| `ERPTaxCodeReference` | `TaxImporter` | Tax code, rate, type |
| `ERPCostCenterReference` | `CostCenterImporter` | Cost center code, name, business unit |
| `ERPPOReference` | `POImporter` | Open PO snapshot (used as tier-2 PO fallback) |

### Import batch tracking

Each import creates an `ERPReferenceImportBatch` record with a file checksum, row
counts (new, updated, rejected per type), import status, and the user who triggered it.

### Cache invalidation on import

After each successful import, `ERPCacheService.invalidate_by_type()` is called
for the relevant resolution type so stale cached lookups are not served.

### UI

Imports are triggered via the Reference Data Import page at `/posting/imports/`.
The Reference Data browser at `/erp-connections/reference-data/` shows the current
state of all 5 reference tables with search and import-batch provenance.

---

## 14. Configuration Settings

All settings are in `config/settings.py` and can be overridden via env vars.

### ERP connector settings

| Setting | Env var | Default | Notes |
|---|---|---|---|
| `ERP_CACHE_TTL_SECONDS` | `ERP_CACHE_TTL_SECONDS` | `3600` | Cache entry TTL (seconds) |
| `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` | `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` | `0.8` | Min confidence to flag a duplicate |

### Freshness settings

| Setting | Env var | Default |
|---|---|---|
| `ERP_TRANSACTIONAL_FRESHNESS_HOURS` | same | `24` |
| `ERP_MASTER_FRESHNESS_HOURS` | same | `168` |
| `ERP_ENABLE_LIVE_REFRESH_ON_MISS` | same | `false` |
| `ERP_ENABLE_LIVE_REFRESH_ON_STALE` | same | `false` |

### Source priority settings

| Setting | Env var | Default | Effect |
|---|---|---|---|
| `ERP_RECON_USE_MIRROR_AS_PRIMARY` | same | `true` | Reconciliation uses MIRROR_DB before API |
| `ERP_POSTING_USE_MIRROR_AS_PRIMARY` | same | `true` | Posting mapping uses MIRROR_DB before API |

### Posting settings (related)

| Setting | Env var | Default |
|---|---|---|
| `POSTING_REFERENCE_FRESHNESS_HOURS` | same | `168` | Age limit for ERP reference data used in posting confidence scoring |

---

## 15. API Endpoints

All endpoints are under `/api/v1/erp/`.

### `GET /api/v1/erp/connections/`

List all `ERPConnection` records. Requires `erp.view_connections`.

### `POST /api/v1/erp/connections/{id}/test/`

Run `connector.test_connectivity()` and return `{ok: bool, message: str}`.

### `GET/POST /api/v1/erp/resolve/{resolution_type}/`

On-demand ERP resolution. `resolution_type` must be one of: `vendor`, `po`,
`grn`, `item`, `tax`, `cost_center`, `duplicate_invoice`.

```
POST /api/v1/erp/resolve/po/
{
  "po_number": "PO-2601",
  "vendor_code": "V001"
}

Response:
{
  "resolved": true,
  "source_type": "MIRROR_DB",
  "confidence": 1.0,
  "is_stale": false,
  "value": { ... },
  "warnings": []
}
```

### `GET /api/v1/erp/resolution-logs/`

List `ERPResolutionLog` records. Filterable by `resolution_type`, `source_type`,
`resolved`, `connector_name`, date range.

### `GET /api/v1/erp/submission-logs/`

List `ERPSubmissionLog` records. Filterable by `status`, `connector_name`, date range.

### `GET /api/v1/erp/cache/`

List active `ERPReferenceCacheRecord` entries.

### `DELETE /api/v1/erp/cache/{resolution_type}/`

Invalidate all cache entries for a resolution type.

---

## 16. Reference Data UI

### ERP Connections list

`/erp-connections/` -- lists all `ERPConnection` records with status badges,
default indicator, and a "Test Connection" action.

### Reference Data browser

`/erp-connections/reference-data/` (nav: ERP Integration > Reference Data)

Browse all 5 imported reference tables (Vendors, Items, Tax Codes, Cost Centers,
Open POs) with:
- KPI summary cards (total records per table)
- Search and pagination per table
- Import-batch provenance (which batch each record came from, when)

### Import UI

`/posting/imports/` -- upload Excel/CSV files to populate reference tables.
Shows import history with batch status, row counts, and error summaries.

---

## 17. Integration Points

### Reconciliation engine

```
ReconciliationRunnerService
    --> POLookupService.lookup(invoice)
            --> ERPResolutionService.resolve_po(po_number)
                    --> POResolver.resolve(connector, ...)
                    --> [cache -> MIRROR_DB -> API -> DB_FALLBACK]
            <-- POLookupResult(erp_source_type, erp_provenance, is_stale, ...)

    --> GRNLookupService.lookup_for_po(po)
            --> ERPResolutionService.resolve_grn(po_number)
                    --> GRNResolver.resolve(connector, ...)
            <-- GRNSummary(erp_source_type, erp_provenance, is_stale, ...)

ReconciliationResultService.save()
    --> writes po_erp_source_type, grn_erp_source_type,
              data_is_stale, erp_source_metadata_json
        to ReconciliationResult
```

### Posting pipeline

```
PostingPipeline._get_erp_connector()
    --> ConnectorFactory.get_default_connector()

PostingMappingEngine.__init__(connector=connector)
    --> ERPResolutionService(connector=connector)

PostingMappingEngine._try_vendor_via_resolver(vendor_code, vendor_name)
    --> svc.resolve_vendor(...)
    --> result.to_provenance_dict() stored in erp_source_metadata_json["vendor"]

PostingMappingEngine._load_po_refs(po_number)
    --> svc.resolve_po(po_number)
    --> builds List[_POLineData] from resolved line items

PostingActionService.submit_posting(posting)
    --> PostingSubmitResolver.submit_invoice(connector, payload)
```

### Agent tools

```
POLookupTool.execute(po_number)
    --> _resolve_via_erp()
    --> ERPResolutionService.with_default_connector().resolve_po(po_number)
    |   (imports ERPResolutionService; falls through to direct DB if import fails)
    --> returns result dict with _erp_is_stale flag

GRNLookupTool.execute(po_number)
    --> _resolve_via_erp()
    --> ERPResolutionService.with_default_connector().resolve_grn(po_number)
```

---

## 18. Adding a New Connector

1. **Create the connector class** in `apps/erp_integration/services/connectors/`:

   ```python
   # apps/erp_integration/services/connectors/my_erp.py
   from apps.erp_integration.services.connectors.base import BaseERPConnector, ERPResolutionResult

   class MyERPConnector(BaseERPConnector):
       connector_name = "my_erp"

       def supports_vendor_lookup(self) -> bool: return True
       def supports_po_lookup(self) -> bool: return True

       def lookup_vendor(self, vendor_code: str, **kwargs) -> ERPResolutionResult:
           # Call My ERP REST API here
           ...

       def lookup_po(self, po_number: str, **kwargs) -> ERPResolutionResult:
           ...
   ```

2. **Add the enum value** to `ERPConnectorType` in `apps/erp_integration/enums.py`:

   ```python
   MY_ERP = "MY_ERP", "My ERP System"
   ```

3. **Register in ConnectorFactory** (`apps/erp_integration/services/connector_factory.py`):

   ```python
   from apps.erp_integration.services.connectors.my_erp import MyERPConnector

   _CONNECTOR_MAP = {
       ...
       ERPConnectorType.MY_ERP: MyERPConnector,
   }
   ```

4. **Create a migration** for the new `ERPConnectorType` choice (Django auto-discovers
   TextChoices changes, but a data migration may be needed if existing rows use the old
   choices list).

5. **Create an `ERPConnection` record** via Django admin or a seed command with
   `connector_type="MY_ERP"`.

6. **Write a test** using `connector.test_connectivity()` to verify auth and reachability.

---

## 19. Adding a New Resolver

1. **Add the enum value** to `ERPResolutionType` in `apps/erp_integration/enums.py`:

   ```python
   CONTRACT = "CONTRACT", "Contract Lookup"
   ```

2. **Create the resolver** in `apps/erp_integration/services/resolution/`:

   ```python
   # apps/erp_integration/services/resolution/contract_resolver.py
   from apps.erp_integration.enums import ERPResolutionType
   from apps.erp_integration.services.resolution.base import BaseResolver

   class ContractResolver(BaseResolver):
       resolution_type = ERPResolutionType.CONTRACT

       def _check_capability(self, connector):
           return connector.supports_contract_lookup()   # add to BaseERPConnector

       def _api_lookup(self, connector, **params):
           return connector.lookup_contract(**params)

       def _db_fallback(self, **params):
           return ContractFallbackAdapter.resolve(**params)
   ```

3. **Create the DB fallback adapter** in `apps/erp_integration/services/db_fallback/`.

4. **Add a method to `ERPResolutionService`**:

   ```python
   def resolve_contract(self, contract_number: str, **kwargs) -> ERPResolutionResult:
       from apps.erp_integration.services.resolution.contract_resolver import ContractResolver
       resolver = ContractResolver()
       return resolver.resolve(self._connector, contract_number=contract_number, **kwargs)
   ```

5. **Add the capability method to `BaseERPConnector`**:

   ```python
   def supports_contract_lookup(self) -> bool: return False
   ```

   And override in connectors that support it.

6. **Wire freshness** -- add `ERPDataDomain.CONTRACT` if it has different
   freshness semantics from MASTER/TRANSACTIONAL, or reuse an existing domain.

---

## 20. Troubleshooting

### No data resolved -- everything is DB_FALLBACK

The live ERP connector is not being used. Check:

1. Does an `ERPConnection` record exist with `is_default=True`, `status=ACTIVE`,
   `is_active=True`? Check in Django admin or at `/admin/erp_integration/erpconnection/`.
2. Does the connector's `supports_<type>_lookup()` return `True` for the resolution
   type you expect?
3. Is the `api_key_env` / `client_id_env` / `client_secret_env` set to the correct
   env var name, and is that env var set?
4. Run `POST /api/v1/erp/connections/{id}/test/` and inspect the response.

### Posting stuck in MAPPING_IN_PROGRESS

1. Check `PostingRun.error_code` and `PostingIssue` records with `severity=ERROR`.
2. Check `ERPResolutionLog` for the matching `posting_run_id` -- look for `resolved=False`.
3. Verify ERP reference tables are populated (`/erp-connections/reference-data/`).
4. Confirm `PostingPipeline._get_erp_connector()` returns a non-None connector.

### Stale data warnings appearing

- Check `ERP_TRANSACTIONAL_FRESHNESS_HOURS` and `ERP_MASTER_FRESHNESS_HOURS` against
  how often your ERP data is actually refreshed.
- If using reference imports (DB_FALLBACK), re-import from `/posting/imports/`.
- If using MIRROR_DB, re-load the PO/GRN documents from ERP.
- Set `ERP_ENABLE_LIVE_REFRESH_ON_STALE=true` to trigger async refresh automatically
  (requires a live connector).

### ERP cache serving wrong data

- Delete stale cache entries: `DELETE /api/v1/erp/cache/{resolution_type}/`.
- Reduce `ERP_CACHE_TTL_SECONDS` if data changes frequently.
- Cache is automatically invalidated after each reference import for the affected type.

### SQLServer connector failing with certificate errors

Set `db_trust_cert=True` on the `ERPConnection` record. This appends
`TrustServerCertificate=yes;Encrypt=yes;` to the ODBC connection string.
Common for on-premises servers with self-signed certificates.

### Resolution logs missing cross-references

Ensure the calling code passes `invoice_id=`, `reconciliation_result_id=`, or
`posting_run_id=` to the `resolve_*()` method. These are optional kwargs on
`ERPResolutionService` but are essential for compliance tracing.

---

## 21. Langfuse Observability

Every ERP resolution and submission is instrumented with Langfuse spans and
evaluation-ready scores via `apps/erp_integration/services/langfuse_helpers.py`.

### Span hierarchy

All `resolve_*()` methods on `ERPResolutionService` accept `lf_parent_span=`.
When provided, the resolution chain creates nested child spans:

```
parent_pipeline_span (posting / reconciliation / agent)
  -- erp_resolution   (created by _trace_resolve)
     -- erp_cache_lookup   (BaseResolver cache check)
     -- erp_live_lookup    (BaseResolver live API call)
     -- erp_db_fallback    (BaseResolver DB fallback)
```

Submission and duplicate check follow the same pattern via `trace_erp_submission()`
and `trace_erp_duplicate_check()`.

### ERP-specific scores

| Score | Values | Emitted by |
|---|---|---|
| `erp_resolution_success` | 0.0 / 1.0 | `_trace_resolve()` |
| `erp_resolution_latency_ok` | 0.0 / 1.0 | `_trace_resolve()` |
| `erp_resolution_fresh` | 0.0 / 1.0 | `_trace_resolve()` |
| `erp_resolution_authoritative` | 0.0 / 1.0 | `_trace_resolve()` |
| `erp_cache_hit` | 0.0 / 1.0 | `trace_erp_cache_lookup()` |
| `erp_live_lookup_success` | 0.0 / 1.0 | `trace_erp_live_lookup()` |
| `erp_db_fallback_used` | 1.0 (always) | `trace_erp_db_fallback()` |
| `erp_submission_success` | 0.0 / 1.0 | `trace_erp_submission()` |

### Metadata sanitisation

`sanitize_erp_metadata()` recursively strips sensitive keys (API keys, tokens,
passwords) and truncates string values >2000 chars. `sanitize_erp_error()` maps
raw error messages to safe categories -- raw stack traces never reach Langfuse.

**Full Langfuse integration reference**: [LANGFUSE_INTEGRATION.md](LANGFUSE_INTEGRATION.md) Section 11.

---

## Related Documents

| Document | Link |
|---|---|
| Shared resolution architecture (refactoring history) | [ERP_SHARED_RESOLUTION.md](ERP_SHARED_RESOLUTION.md) |
| Invoice posting pipeline | [POSTING_AGENT.md](POSTING_AGENT.md) |
| Agent architecture and tools | [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md) |
| Platform overview and models | [PROJECT.md](PROJECT.md) |
| Langfuse observability integration | [LANGFUSE_INTEGRATION.md](LANGFUSE_INTEGRATION.md) |
