# 10 — Integrations and External Dependencies

**Generated**: 2026-04-09 | **Method**: Code-first inspection  
**Evidence files**: `requirements.txt`, `config/settings.py`, `tools/registry/tools.py`, `erp_integration/services/`, `core/langfuse_client.py` (inferred), `auditlog/models.py`

---

## 1. LLM / AI Services

### Azure OpenAI (GPT-4o)

| Aspect | Detail |
|--------|--------|
| Purpose | Invoice extraction, agent reasoning, prompt composition classification |
| Library | `openai==2.30.0`, `langchain-openai==1.1.12` |
| Config | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` |
| Model | `LLM_MODEL_NAME=gpt-4o` (deployment-specific) |
| Temperature | 0.0 for extraction, 0.1 for agents |
| Timeout | `LLM_REQUEST_TIMEOUT=120s` per call |
| Fallback | No LLM fallback — if Azure OpenAI is unavailable, extraction/agents fail |
| Alternative | `LLM_PROVIDER=openai` supported (direct OpenAI, not Azure) |
| Module | `apps/agents/services/llm_client.py` — `LLMClient` class |

---

### Azure Document Intelligence (OCR)

| Aspect | Detail |
|--------|--------|
| Purpose | PDF OCR — produces structured text blocks from invoice images |
| Library | `azure-ai-formrecognizer==3.3.2` |
| Config | `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY` |
| Override | `EXTRACTION_OCR_ENABLED=false` → falls back to native PDF text extraction (PyPDF2-based) |
| Runtime override | `ExtractionRuntimeSettings.ocr_enabled` |
| Module | `apps/extraction/services/extraction_adapter.py` |
| Fallback | PyPDF2 native extraction (lower quality, useful for comparison testing) |

---

### Langfuse (LLM Observability)

| Aspect | Detail |
|--------|--------|
| Purpose | Prompt management, trace hierarchy, LLM scoring, session replay |
| Library | `langfuse==4.0.1` |
| Config | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (optional — disables tracing if unset) |
| Module | `apps/core/langfuse_client.py` — `start_trace`, `start_span`, `end_span`, `score_trace`, `prompt_text` |
| Prompt labels | "production" label only served |
| Fail behavior | All Langfuse calls are fail-silent (try/except → log.debug → continue) |
| Session linking | `session_id = derive_session_id(case_number, invoice_id, case_id)` |

---

## 2. Azure Cloud Services

### Azure Blob Storage

| Aspect | Detail |
|--------|--------|
| Purpose | Persistent invoice PDF storage |
| Library | `azure-storage-blob==12.20.0` |
| Config | `AZURE_BLOB_CONNECTION_STRING`, `AZURE_BLOB_CONTAINER_NAME=finance-agents` |
| Path format | `input/{year}/{month}/{upload_id}_{filename}` |
| Fields on model | `blob_path`, `blob_container`, `blob_name`, `blob_url`, `blob_metadata`, `blob_uploaded_at` |
| Module | Upload handling in `apps/extraction/services/upload_service.py` |
| Fallback | Local `media/invoices/%Y/%m/` filesystem (dev mode) |

---

## 3. ERP Integration Framework

### Architecture

```
ERPResolutionService (resolution_service.py)
  ├── ConnectorFactory.get_connector()  → picks connector by type
  │    └── Returns one of: CustomERPConnector, SQLServerConnector, MySQLConnector,
  │                        DynamicsConnector, ZohoConnector, SalesforceConnector
  ├── CacheService.get(key)  → L1 (in-process dict) / L2 (DB) / L3 (Redis)
  │    ├── TTL: transactional = 24h, master = 168h (7 days)
  │    └── Freshness flags: ERP_TRANSACTIONAL_FRESHNESS_HOURS, ERP_MASTER_FRESHNESS_HOURS
  ├── Resolvers (per entity type):
  │    ├── POResolver       → resolve_po(po_number)
  │    ├── GRNResolver      → resolve_grn(po_number)
  │    ├── VendorResolver   → resolve_vendor(vendor_id/name)
  │    ├── ItemResolver     → resolve_item(item_code)
  │    ├── TaxResolver      → resolve_tax(tax_code)
  │    └── CostCenterResolver → resolve_cost_center(code)
  └── ERPAuditService  → logs ERP resolution attempts and outcomes
```

### Live Refresh Policy (from settings.py)
```python
ERP_ENABLE_LIVE_REFRESH_ON_MISS  = false  (default — no live lookup on cache miss)
ERP_ENABLE_LIVE_REFRESH_ON_STALE = false  (default — no live lookup on stale data)
ERP_RECON_USE_MIRROR_AS_PRIMARY  = true   (use internal mirror tables for recon)
ERP_POSTING_USE_MIRROR_AS_PRIMARY = true  (use reference import tables for posting)
```

**Default behavior**: Mirror tables (internal DB) are the primary source. Live ERP calls are disabled by default and must be explicitly enabled.

### Connector Types

| Connector | File | Purpose |
|-----------|------|---------|
| Custom API | `custom_erp.py` | Generic REST API ERP |
| SQL Server | `sqlserver.py` | Microsoft SQL Server direct connection |
| MySQL | `mysql.py` | MySQL direct connection |
| Dynamics 365 | `dynamics.py` | Microsoft Dynamics 365 API |
| Zoho Books | `zoho.py` | Zoho Books API |
| Salesforce | `salesforce.py` | Salesforce API |

### ERP Encryption
`erp_integration/crypto.py` — secrets are encrypted at rest (connector credentials stored encrypted in DB).

---

## 4. Message Broker / Cache

### Redis

| Aspect | Detail |
|--------|--------|
| Purpose | Celery message broker |
| Config | `CELERY_BROKER_URL=redis://127.0.0.1:6379/0` (dev default) |
| Production | Must use `rediss://` (TLS) with auth credentials |
| Also used for | Potentially ERP cache L3 (inferred from CacheService L1/L2/L3 design) |

---

## 5. Database

### MySQL

| Aspect | Detail |
|--------|--------|
| Charset | utf8mb4 (supports emoji, full Unicode) |
| SQL mode | STRICT_TRANS_TABLES |
| SSL | `ssl_mode=REQUIRED` — enforced in DB OPTIONS |
| Config | `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` |
| Result backend | django-celery-results also uses the MySQL DB |

---

## 6. Observability Infrastructure

### OpenTelemetry

| Aspect | Detail |
|--------|--------|
| Purpose | Distributed tracing (spans from Django through Celery tasks) |
| Libraries | `opentelemetry-api==1.40.0`, `opentelemetry-sdk==1.40.0`, `opentelemetry-exporter-otlp-proto-http==1.40.0` |
| OpenAI auto-instrumentation | `openinference-instrumentation-openai==0.1.43` |
| OTLP endpoint | Not found in settings.py — likely set via `OTEL_EXPORTER_OTLP_ENDPOINT` env var (standard OTEL convention) |

### Loki (Log Aggregation)

| Aspect | Detail |
|--------|--------|
| Purpose | Centralized JSON log aggregation |
| Library | `python-logging-loki==0.3.1` |
| Config | `LOKI_ENABLED=false` (default), `LOKI_URL`, `LOKI_APP_LABEL`, `LOKI_USER`, `LOKI_PASSWORD` |
| Handler | `SilentLokiHandler` — extends python-logging-loki with fail-silent behavior |
| Labels | `service: po-recon`, `env: {DJANGO_ENV}` |
| docker-compose.loki.yml | Present in repo root — Loki + Grafana stack for local dev |

---

## 7. String Matching Libraries

| Library | Version | Usage |
|---------|---------|-------|
| thefuzz | 0.22.1 | Fuzzy vendor name matching |
| python-Levenshtein | 0.27.3 | Edit distance (accelerates thefuzz) |
| RapidFuzz | 3.14.3 | Fast fuzzy matching for line item descriptions |

All used in `reconciliation/services/` for header and line matching.

---

## 8. Integration Risk Summary

| Integration | Risk | Notes |
|------------|------|-------|
| Azure OpenAI | HIGH — single LLM provider | No fallback if Azure endpoint unavailable; all extractions and agents fail |
| Azure Document Intelligence | MEDIUM | OCR fallback exists (PyPDF2) but quality significantly lower |
| Azure Blob Storage | MEDIUM | Invoices stored here; local media fallback in dev only |
| Langfuse | LOW | Fail-silent everywhere; losing tracing doesn't affect business flow |
| Redis (Celery broker) | HIGH — if down, no async processing | All extractions, reconciliations, agent runs stop |
| ERP connectors | MEDIUM | Mirror tables are primary; live ERP unavailability only affects cache refresh |
| MySQL | CRITICAL — no failover configured | Single DB; ORM strict mode prevents data corruption but no HA |

---

## 9. Environment Variable Requirements

### Required
```
DJANGO_SECRET_KEY           # Will raise ImproperlyConfigured if missing
DB_PASSWORD                 # MySQL password (DB_USER defaults to "root")
AZURE_OPENAI_API_KEY        # LLM extraction and agents fail without this
AZURE_OPENAI_ENDPOINT       # LLM extraction and agents fail without this
AZURE_OPENAI_DEPLOYMENT     # Required for Azure OpenAI calls
AZURE_DI_ENDPOINT           # OCR fails without this (unless EXTRACTION_OCR_ENABLED=false)
AZURE_DI_KEY                # OCR fails without this
AZURE_BLOB_CONNECTION_STRING # Document storage fails
```

### Optional (with safe defaults)
```
CELERY_BROKER_URL           # Defaults to local Redis (dev only)
LANGFUSE_PUBLIC_KEY         # Disables LLM tracing if unset
LANGFUSE_SECRET_KEY         # Disables LLM tracing if unset
AZURE_BLOB_CONTAINER_NAME   # Defaults to "finance-agents"
LLM_MODEL_NAME              # Defaults to "gpt-4o"
LLM_TEMPERATURE             # Defaults to 0.1
LLM_MAX_TOKENS              # Defaults to 4096
LLM_REQUEST_TIMEOUT         # Defaults to 120s
EXTRACTION_AUTO_APPROVE_THRESHOLD # Defaults to 0.85
EXTRACTION_OCR_ENABLED      # Defaults to "true"
AGENT_REASONING_ENGINE_ENABLED # Defaults to "false"
LOKI_ENABLED                # Defaults to "false"
ERP_ENABLE_LIVE_REFRESH_ON_MISS # Defaults to "false"
ERP_ENABLE_LIVE_REFRESH_ON_STALE # Defaults to "false"
```
