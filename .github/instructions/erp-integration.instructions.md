---
description: "Use when working on ERP integration, connectors, resolvers, DB fallback adapters, ERPConnection models, cache service, or audit logging for ERP operations. Covers the resolution chain, connector factory, PO two-tier fallback, Langfuse ERP helpers, and metadata sanitization."
applyTo: "apps/erp_integration/**/*.py"
---
# ERP Integration Conventions

## Enum Location
ERP connector enums live in `apps/erp_integration/enums.py` (NOT `apps/core/enums.py`):
- `ERPConnectorType`, `ERPConnectionStatus`, `ERPSourceType`
- `ERPResolutionType`, `ERPSubmissionType`, `ERPSubmissionStatus`

## Resolution Chain (immutable order)
```
cache (ERPReferenceCacheRecord, TTL controlled by ERP_CACHE_TTL_SECONDS)
  -> ERP API connector (live lookup via BaseERPConnector subclass)
    -> DB fallback (BaseDBFallbackAdapter subclass)
```
NEVER skip a step. NEVER go directly to DB without checking cache and API first.

## PO Fallback — Two-Tier
- Tier 1: `documents.PurchaseOrder` (confidence 1.0, full transactional record)
- Tier 2: `posting_core.ERPPOReference` (confidence 0.75, add `_source_tier: "erp_reference_snapshot"` and `_warning` key to result)

## ConnectorFactory
- `get_default_connector()` returns the active default `ERPConnection` as a connector instance
- Returns `None` if no `ERPConnection` has `is_default=True`, `status=ACTIVE`, `is_active=True`
- `PostingMappingEngine` handles `None` gracefully — falls back to direct DB lookups

## Metadata Safety
- ALWAYS sanitize ERP metadata with `sanitize_erp_metadata()` from `langfuse_helpers.py` before logging
- `sanitize_erp_metadata()` redacts keys matching: `api_key`, `token`, `password`, `secret`, `auth`
- NEVER log raw API responses containing credentials

## Langfuse ERP Tracing
- Use `apps.erp_integration.services.langfuse_helpers` — NOT the core langfuse_client directly
- Per-stage traced wrappers: `trace_erp_cache_lookup`, `trace_erp_live_lookup`, `trace_erp_db_fallback`, `trace_erp_submission`
- Thread `lf_parent_span` through: `ERPResolutionService._trace_resolve()` -> `BaseResolver.resolve()` -> per-stage wrappers
- Source provenance helpers: `build_source_chain()`, `freshness_status_label()`, `is_authoritative_source()`

## Adding a New Connector — Checklist
1. Add `ERPConnectorType` enum value in `apps/erp_integration/enums.py`
2. Create class in `apps/erp_integration/services/connectors/<name>_connector.py` extending `BaseERPConnector`
3. Override ALL `supports_*()` capability flags
4. Register in `_CONNECTOR_MAP` in `connector_factory.py`
5. Create `ERPConnection` record via admin with new `connector_type`

## Adding a New Resolver — Checklist
1. Add `ERPResolutionType` enum value if missing
2. Create in `apps/erp_integration/services/resolution/<type>_resolver.py` extending `BaseResolver`
3. Implement: `_check_capability()`, `_api_lookup()`, `_db_fallback()`
4. Create DB fallback adapter in `apps/erp_integration/services/db_fallback/<type>_adapter.py`
