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
| `tenant_id` | CharField | OAuth tenant/org ID (cloud ERPs) -- **Note:** this is the _ERP system's_ tenant/org identifier, not the platform `CompanyProfile` tenant. The platform tenant FK is inherited from `BaseModel.tenant` and is used for row-level multi-tenant isolation. |", "oldString": "| `tenant_id` | CharField | OAuth tenant/org ID (cloud ERPs) |
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

---

## Appendix: ERP Shared Resolution Architecture

> Describes the unified ERP data resolution model shared by the reconciliation engine and the posting pipeline.

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

---

## Appendix: ERP Imports & Export Mapping — Functional Document

> Functional behavior for ERP connectivity, direct and batch reference imports, and purchase-invoice export mapping. v1.1 — 2026-04-24.

# ERP Integration + ERP Imports + Export Mapping Functional Document

Version: 1.1
Last Updated: 2026-04-24
Scope: Functional behavior for ERP connectivity, direct and batch reference imports, and purchase-invoice export mapping.

---

## 1. Purpose

This document defines how ERP data is connected, imported, and consumed for export mapping in the AP platform.

It is intended for:
- Product owners
- AP operations
- Implementation engineers
- QA and UAT teams
- Support and incident response teams

It includes:
- End-to-end functional flows
- Data ownership and precedence rules
- Failure handling and fallback behavior
- Configuration controls
- Runbook steps for safe operations
- Acceptance criteria for release and UAT

It excludes:
- Procurement-specific flows
- Frontend visual design details
- ERP connector implementation internals beyond functional behavior

---

## 2. Functional Scope

### 2.1 In scope

1. ERP integration layer as shared platform capability.
2. ERP reference data imports:
- Vendor
- Item
- Tax code
- Cost center
- Open PO
3. Direct ERP imports through configured connectors.
4. ERP import flush operations (global and tenant-specific).
5. Export field mapping for purchase invoice workbook (single and bulk export).
6. Deterministic-first mapping with optional AI fallback for unresolved fields only.
7. First-class platform system-agent registration for export mapping governance.

### 2.2 Out of scope

1. Final ERP posting submission lifecycle details (covered by posting docs).
2. Full reconciliation business rules (covered by reconciliation docs).
3. Non-invoice export templates.

---

## 3. Business Objectives

1. Ensure export templates are populated from ERP-authoritative data whenever available.
2. Reduce blank fields in export outputs without sacrificing determinism.
3. Keep mapping auditable, repeatable, and safe for finance workflows.
4. Allow phased AI usage only where deterministic mapping cannot resolve fields.
5. Support tenant-safe operations across import, lookup, and export.

---

## 4. High-Level Architecture

Primary modules:

1. ERP Integration Layer
- Path: apps/erp_integration/
- Responsibility: connectors, resolution chain, caching, fallback, audit.

2. Reference Import Pipeline
- Path: apps/posting_core/services/direct_erp_importer.py
- Responsibility: source extraction and normalization into ERP reference tables.

3. Export Resolution and Mapping
- Paths:
  - apps/extraction/template_views.py
  - apps/extraction/services/export_mapping_agent.py
- Responsibility: map invoice export fields from imported ERP references.

4. Reference Storage
- Path: apps/posting_core/models.py
- Responsibility: ERP reference snapshots and import batch records.

---

## 5. Primary Users and Roles

1. AP Processor
- Uses extraction workbench exports.
- Relies on mapped fields in output workbook.

2. Finance Manager
- Reviews exported accounting fields for posting workflows.

3. Admin / Platform Ops
- Configures ERP connectors.
- Runs imports and flush commands.
- Handles outage recovery and re-import cycles.

4. Auditor
- Verifies repeatability and source provenance in operational process.

---

## 6. End-to-End Functional Flow

### 6.1 ERP connection and data availability

1. Admin configures an active default ERP connection.
2. Connector validates connectivity.
3. Import process reads ERP data and writes normalized reference snapshots.
4. Export routines consume latest completed snapshots per tenant scope.

### 6.2 Reference import flow

1. Import is triggered for one batch type.
2. System validates batch type and connector.
3. Connectivity is tested with transient retry behavior.
4. Rows are queried from ERP and normalized.
5. Validation and importer pipeline persists rows in reference tables.
6. Import batch is marked completed with row statistics.

### 6.3 Export mapping flow

1. User triggers single or bulk purchase invoice export.
2. Export initializes reference resolver using latest completed batches.
3. Mapping agent resolves header and line fields using deterministic rules first.
4. Optional AI fallback runs only for unresolved fields when enabled.
5. Workbook sheets are generated with mapped values and defaults.
6. File is returned as xlsx response.

---

## 7. ERP Imports Functional Details

## 7.1 Import types

Supported batch types:
1. VENDOR
2. ITEM
3. TAX
4. COST_CENTER
5. OPEN_PO

## 7.2 Source and target behavior

1. Source rows are connector-specific raw payloads.
2. Import layer normalizes into platform schema.
3. Original source details are retained in raw_json for downstream mapping.
4. Latest completed batch per type is used by export resolver.

## 7.3 Connectivity resilience

1. Import orchestration retries transient connectivity errors.
2. SQL transient unavailability errors (for example 40613) are treated as retryable.
3. If retries fail, import is marked failed and no partial silent success is reported as complete.

## 7.4 Tenant behavior

1. Import batches are tenant-scoped when tenant is provided.
2. Export resolver reads latest completed batch within tenant scope.
3. Tenant-specific flush can remove imports for one tenant without affecting others.

---

## 8. ERP Import Flush Functional Behavior

Command:
- apps/posting_core/management/commands/flush_erp_imports.py

Modes:
1. Global flush (all tenants)
2. Tenant flush (--tenant-id)

Deleted artifacts:
1. ERPReferenceImportBatch (and cascaded reference rows)
2. VendorAliasMapping linked to imported vendor references
3. ItemAliasMapping linked to imported item references
4. Related AuditEvent rows for ERPReferenceImportBatch entities

Safety controls:
1. Interactive confirmation by default
2. Non-interactive mode with --confirm

Operational impact:
1. Export mapping quality drops immediately after flush until re-import completes.
2. Any resolver that depends on reference snapshots can return blanks/defaults until repopulation.

---

## 9. Export Mapping Functional Design

## 9.1 Export surfaces

1. Single invoice workbook export
- Function: extraction_export_purchase_invoice_excel

2. Bulk workbook export
- Function: extraction_export_purchase_invoice_excel_bulk

Both use same resolver and mapping agent behavior for consistency.

## 9.2 Deterministic reference resolver

Resolver class:
- _ExportReferenceResolver in apps/extraction/template_views.py

Responsibilities:
1. Load latest completed ERP snapshot batches per type.
2. Build lookup indexes for vendor, item, PO, cost center references.
3. Resolve fields using deterministic priority and canonical alias matching.
4. Normalize raw_json key variants (case, spacing, underscore variations).

## 9.3 Mapping agent (new)

Class:
- ExportFieldMappingAgent
- Path: apps/extraction/services/export_mapping_agent.py

Behavior:
1. Always call deterministic resolver first.
2. Identify unresolved fields only.
3. If AI fallback is disabled, return deterministic result as final.
4. If AI fallback is enabled, call LLM for unresolved fields only.
5. Apply AI suggestions only when confidence threshold is met.
6. Never override already-resolved deterministic fields.

## 9.3.1 First-class platform agent type

The export mapping capability is now a first-class system agent type in the central platform agent stack.

Agent type:
1. SYSTEM_EXPORT_FIELD_MAPPING

Core wiring:
1. Agent enum registration in core enum catalog.
2. Central agent registry mapping to deterministic system agent class.
3. Guardrail permission mapping to agents.run_system_export_field_mapping.
4. Governance screen classification as deterministic (not LLM coverage gap).
5. Eval adapter mapping for agent-run telemetry compatibility.

## 9.4 Resolved field sets

Header field focus:
1. party_account
2. purchase_account
3. currency
4. due_days
5. due_date

Line field focus:
1. item_code
2. uom
3. cost_center
4. department
5. purchase_account

## 9.5 Field precedence (functional)

For each field:
1. Deterministic ERP snapshot resolution
2. Deterministic fallback (invoice/vendor defaults)
3. Optional AI unresolved-only suggestion (feature-gated)
4. Final hard default (empty or configured default where applicable)

Important safety rule:
- Deterministic value is authoritative and is not replaced by AI.

## 9.6 Governance runtime emission during exports

Export execution now emits a best-effort AgentRun for SYSTEM_EXPORT_FIELD_MAPPING so runtime governance history shows actual export-mapping activity.

Emission characteristics:
1. One AgentRun per export request (single or bulk), not per invoice line.
2. Non-blocking: emission failures never fail the export response.
3. RBAC-aware: emission occurs only when actor is authorized for the system agent permission.
4. Captured telemetry includes:
- scope (single or bulk)
- invoices_count
- header_unresolved_count
- line_unresolved_count
- ai_fallback_enabled
- ai_fallback_used
- ai_fields_applied

---

## 10. AI Fallback Controls (Phase 2)

Settings:
1. EXPORT_MAPPING_AI_FALLBACK_ENABLED (default false)
2. EXPORT_MAPPING_AI_MIN_CONFIDENCE (default 0.80)

Functional rollout:
1. Phase 1: deterministic-only in production (recommended baseline).
2. Phase 2: enable AI fallback in test/UAT.
3. Promote to production only after measurable quality gain and no regression.

AI guardrails:
1. AI only sees unresolved fields.
2. Structured JSON contract only.
3. Low-confidence outputs are discarded.
4. Missing or invalid JSON is ignored safely.

---

## 11. Data Quality and Mapping Rules

1. ERP master or transactional snapshot values should be preferred over extracted OCR text for accounting-sensitive columns.
2. raw_json is treated as source evidence for connector-specific fields.
3. Alias normalization is required to absorb connector key drift and reduce recurring blank regressions.
4. Purchase account should derive from PO/vendor ERP data before falling back to party account.
5. Due Days and Due Date should resolve from explicit invoice dates or payment terms-derived logic.

---

## 12. Failure Modes and Expected Behavior

## 12.1 ERP connector unavailable

Expected:
1. Import fails with explicit status.
2. Existing snapshot remains usable.
3. Export continues using last completed batches.

## 12.2 Imports flushed without re-import

Expected:
1. Export field population decreases.
2. Deterministic resolver may return blanks for ERP-dependent fields.
3. Re-import restores mapping quality.

## 12.3 Missing keys in raw_json

Expected:
1. Canonical alias resolver attempts normalized key variants.
2. If still unresolved, deterministic fallback applies.
3. Optional AI fallback may propose values for unresolved fields only.

## 12.4 AI fallback returns low confidence or invalid response

Expected:
1. Suggestions are ignored.
2. Deterministic result remains final.
3. Export still completes successfully.

---

## 13. Security, Governance, and Audit

1. ERP secrets are resolved via environment variable references, not stored in plaintext.
2. Tenant scoping is preserved for import and lookup reads.
3. Import batch status and row counts support operational auditability.
4. Export mapping remains deterministic by default to support reproducible financial output.
5. AI fallback is explicitly feature-gated and confidence-gated.
6. Export mapping governance now records runtime system-agent runs for operational traceability.

### 13.1 Migration-safe rollout items

Recent migration updates introduced as part of this feature set:

1. accounts.0007_add_system_export_field_mapping_permission
- Adds agents.run_system_export_field_mapping permission.
- Grants to SUPER_ADMIN, ADMIN, and SYSTEM_AGENT.

2. agents.0018_alter_agentdefinition_agent_type_and_more
- Adds SYSTEM_EXPORT_FIELD_MAPPING to AgentDefinition and AgentRun choices.
- Seeds AgentDefinition record for system export field mapping.

3. reconciliation.0019_remove_global_partial_unique_constraint
- Removes MySQL-unsupported conditional unique constraint on global config names.
- Warning models.W036 is eliminated.
- Global-name uniqueness remains enforced at application validation layer.

---

## 14. Operational Runbook

## 14.1 Initial setup

1. Configure active default ERP connection.
2. Run ERP reference imports for all required batch types.
3. Validate reference row counts and latest batch statuses.
4. Execute sample single and bulk exports for sanity check.

## 14.2 Post-flush recovery

1. Run flush_erp_imports (global or tenant-specific as needed).
2. Immediately re-import all required ERP reference types.
3. Validate that latest completed batches exist.
4. Re-run export validation samples.

## 14.3 AI fallback rollout

1. Keep fallback disabled in baseline.
2. Enable in test environment only.
3. Compare blank rate, accuracy, and reconciliation with finance users.
4. Tune confidence threshold if needed.
5. Enable in production only after sign-off.

---

## 15. UAT Acceptance Criteria

Functional acceptance is met when all criteria below are satisfied:

1. ERP imports complete successfully for all 5 batch types.
2. Latest completed snapshots are consumed by both single and bulk export.
3. Header fields (party_account, purchase_account, due_days, due_date, currency) populate as expected from ERP-preferred rules.
4. Line fields (item_code, uom, cost_center, department, purchase_account) populate consistently between single and bulk exports.
5. After flush without re-import, reduced population is observable and expected.
6. After re-import, mapping quality is restored.
7. With AI fallback disabled, behavior is deterministic and repeatable.
8. With AI fallback enabled, only unresolved fields are enriched and low-confidence suggestions are rejected.

---

## 16. QA Test Matrix (Minimum)

1. Happy path imports for each batch type.
2. Transient ERP outage during import (retry behavior).
3. Tenant-specific import and tenant-specific flush.
4. Single export mapping verification for one invoice with PO.
5. Bulk export mapping verification across mixed invoices.
6. Blank-key regression test with variant raw_json key styles.
7. AI fallback off vs on comparison.
8. Confidence threshold rejection test for AI fallback.

---

## 17. Known Constraints

1. Mapping quality is bounded by reference data freshness and connector query coverage.
2. If fields are absent from both current snapshots and source schema, export cannot fill them without schema/query extension.
3. AI fallback is advisory for unresolved fields and is intentionally constrained to avoid nondeterministic overrides.

---

## 18. Change Log

1.0 (2026-04-24)
1. Consolidated functional view across ERP integration, imports, and export mapping.
2. Added deterministic-first export mapping agent model.
3. Added phased AI fallback controls and guardrails.
4. Added operational and UAT acceptance guidance.

1.1 (2026-04-24)
1. Added first-class system agent type documentation for export mapping.
2. Documented runtime AgentRun emission in governance history for export requests.
3. Added migration-safe rollout section with accounts.0007 and agents.0018.
4. Added reconciliation MySQL warning remediation note for models.W036 removal.

---

## Appendix: Export Excel Gap Report (Phase 2)

> Gap analysis of BLANK_TEMPLATE columns vs. imports_formats/script.sql. Source baseline: docs/export_excel_column_mapping.csv.

# Export Excel Gap Report (Phase 2)

Source schema: imports_formats/script.sql

Input baseline: docs/export_excel_column_mapping.csv (BLANK_TEMPLATE rows only)

## Summary
- Total BLANK_TEMPLATE columns reviewed: 200\n
- FILLABLE_FROM_CURRENT_ERP_TABLES: 164\n- FILLABLE_WITH_SCHEMA_EXTENSIONS: 34\n- REQUIRES_NEW_FIELD_OR_TABLE: 2\n
## By Sheet
- Item Body: FILLABLE_FROM_CURRENT_ERP_TABLES=48, FILLABLE_WITH_SCHEMA_EXTENSIONS=10\n- Header: FILLABLE_FROM_CURRENT_ERP_TABLES=25, FILLABLE_WITH_SCHEMA_EXTENSIONS=6, REQUIRES_NEW_FIELD_OR_TABLE=1\n- Summary: FILLABLE_FROM_CURRENT_ERP_TABLES=26, FILLABLE_WITH_SCHEMA_EXTENSIONS=6\n- Advance Set Off Body: FILLABLE_FROM_CURRENT_ERP_TABLES=8, REQUIRES_NEW_FIELD_OR_TABLE=1, FILLABLE_WITH_SCHEMA_EXTENSIONS=2\n- Voucher: FILLABLE_FROM_CURRENT_ERP_TABLES=5, FILLABLE_WITH_SCHEMA_EXTENSIONS=2\n- Adjustments Body: FILLABLE_FROM_CURRENT_ERP_TABLES=33, FILLABLE_WITH_SCHEMA_EXTENSIONS=8\n- Payments Body: FILLABLE_FROM_CURRENT_ERP_TABLES=8\n- Reference: FILLABLE_FROM_CURRENT_ERP_TABLES=1\n- Other Info: FILLABLE_FROM_CURRENT_ERP_TABLES=5\n- Transaction Details: FILLABLE_FROM_CURRENT_ERP_TABLES=5\n
## Notes
- FILLABLE_FROM_CURRENT_ERP_TABLES means a likely source exists in the currently integrated connector tables.\n- FILLABLE_WITH_SCHEMA_EXTENSIONS means source exists in script.sql but not in currently integrated table/query set.\n- REQUIRES_NEW_FIELD_OR_TABLE means no close match was found in script.sql and likely needs schema/process changes.\n
---

## Appendix: Export Excel Gap Report Phase 2 (Strict)

> Strict semantic cross-reference of BLANK_TEMPLATE columns against imports_formats/script.sql using exact/alias matching.

# Export Excel Gap Report Phase 2 (Strict)

Cross-reference baseline BLANK_TEMPLATE columns against imports_formats/script.sql using exact/alias semantic matching.

- Total BLANK_TEMPLATE columns reviewed: 200\n- FILLABLE_FROM_CURRENT_ERP_TABLES: 119\n- REQUIRES_NEW_FIELD_OR_TABLE: 35\n- FILLABLE_WITH_SCHEMA_EXTENSIONS: 46\n
## By Sheet
- Adjustments Body: FILLABLE_FROM_CURRENT_ERP_TABLES=19, REQUIRES_NEW_FIELD_OR_TABLE=1, FILLABLE_WITH_SCHEMA_EXTENSIONS=21\n- Advance Set Off Body: FILLABLE_FROM_CURRENT_ERP_TABLES=6, REQUIRES_NEW_FIELD_OR_TABLE=2, FILLABLE_WITH_SCHEMA_EXTENSIONS=3\n- Header: FILLABLE_FROM_CURRENT_ERP_TABLES=16, REQUIRES_NEW_FIELD_OR_TABLE=13, FILLABLE_WITH_SCHEMA_EXTENSIONS=3\n- Item Body: FILLABLE_FROM_CURRENT_ERP_TABLES=37, REQUIRES_NEW_FIELD_OR_TABLE=7, FILLABLE_WITH_SCHEMA_EXTENSIONS=14\n- Other Info: FILLABLE_FROM_CURRENT_ERP_TABLES=5\n- Payments Body: FILLABLE_FROM_CURRENT_ERP_TABLES=8\n- Reference: FILLABLE_FROM_CURRENT_ERP_TABLES=1\n- Summary: FILLABLE_FROM_CURRENT_ERP_TABLES=19, REQUIRES_NEW_FIELD_OR_TABLE=9, FILLABLE_WITH_SCHEMA_EXTENSIONS=4\n- Transaction Details: FILLABLE_FROM_CURRENT_ERP_TABLES=5\n- Voucher: REQUIRES_NEW_FIELD_OR_TABLE=3, FILLABLE_FROM_CURRENT_ERP_TABLES=3, FILLABLE_WITH_SCHEMA_EXTENSIONS=1\n