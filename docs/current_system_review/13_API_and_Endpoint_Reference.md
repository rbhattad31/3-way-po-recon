# 13 — API and Endpoint Reference

**Generated**: 2026-04-09 | **Method**: Inspection of `config/urls.py` and app-level `api_urls.py` / `urls.py` files  
**Confidence**: High for URL registration; Medium for specific endpoint behavior (views not fully read)

---

## 1. URL Registration Summary (`config/urls.py`)

| Mount Path | App | Type |
|-----------|-----|------|
| `/health/`, `/health/live/`, `/health/ready/` | core | Health checks (login-exempt) |
| `/admin/` | Django admin | Admin console |
| `/accounts/` | accounts | Auth (login, logout, registration) |
| `/api/` | core.api_urls | Core DRF API router |
| `/api/v1/governance/` | auditlog.api_urls | Governance / audit API |
| `/api/v1/cases/` | cases.api_urls | Cases DRF API |
| `/api/v1/copilot/` | copilot.api_urls | Copilot API |
| `/api/v1/procurement/` | procurement.api_urls | Procurement API |
| `/api/v1/posting/` | posting.api_urls | Posting workflow API |
| `/api/v1/posting-core/` | posting_core.api_urls | Posting reference data API |
| `/erp/` | erp_integration.api_urls | ERP integration API |
| `/copilot/` | copilot.urls | Copilot template views |
| `/cases/` | cases.urls | Case template views |
| `/dashboard/` | dashboard.urls | Dashboard views |
| `/invoices/` | documents.urls | Invoice template views |
| `/purchase-orders/` | documents.po_urls | PO template views |
| `/grns/` | documents.grn_urls | GRN template views |
| `/extraction/` | extraction.urls | Extraction pipeline views |
| `/extraction/control-center/` | extraction_core.urls | Extraction control center |
| `/reconciliation/` | reconciliation.urls | Reconciliation views |
| `/reviews/` | cases.review_urls | Review queue views |
| `/reports/` | reports.urls | Reports (stub) |
| `/agents/` | agents.urls | Agent management views |
| `/vendors/` | vendors.urls | Vendor views |
| `/governance/` | auditlog.urls | Governance template views |
| `/eval/` | core_eval.urls | Evaluation framework views |
| `/procurement/` | procurement.urls | Procurement template views |
| `/posting/` | posting.urls | Posting workflow template views |
| `/erp-connections/` | erp_integration.urls | ERP connection management |

---

## 2. DRF API Design

### Global DRF Configuration
```python
DEFAULT_AUTHENTICATION_CLASSES = [SessionAuthentication]
DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]
DEFAULT_PAGINATION_CLASS = PageNumberPagination (PAGE_SIZE=25)
DEFAULT_FILTER_BACKENDS = [DjangoFilterBackend, SearchFilter, OrderingFilter]
```

All DRF endpoints require session authentication. Browsable API only in DEBUG mode.

---

## 3. Core API (`/api/`)

Routes registered in `apps/core/api_urls.py`. Likely includes:
- `/api/v1/invoices/` — Invoice CRUD + filtering
- `/api/v1/purchase-orders/` — PO read endpoints
- `/api/v1/grns/` — GRN read endpoints
- `/api/v1/vendors/` — Vendor CRUD
- `/api/v1/reconciliation/` — Reconciliation results, exceptions

---

## 4. Governance API (`/api/v1/governance/`)

From README: "9 governance API endpoints"

Likely includes:
- Audit event queries (filter by entity, event_type, actor, date range)
- Agent RBAC compliance metrics
- Decision log access
- Agent run summaries
- Permission/role management

---

## 5. Cases API (`/api/v1/cases/`)

From `apps/cases/api/` directory — DRF viewsets for:
- APCase list/detail/filter
- ReviewAssignment management
- APCaseDecision CRUD
- Bulk assignment
- Case status transitions

---

## 6. ERP API (`/erp/`)

From `apps/erp_integration/api_urls.py`:
- ERP connector CRUD (create/update/test connection)
- Manual cache invalidation
- ERP data resolution testing

---

## 7. Health Check Endpoints

```
GET /health/        → basic health check (DB, Redis ping)
GET /health/live/   → liveness (is process running)
GET /health/ready/  → readiness (is DB and dependencies ready)
```

All three are exempt from `LoginRequiredMiddleware` — accessible without auth.

---

## 8. Key API Behaviors

- **Pagination**: 25 items per page by default
- **Filtering**: `DjangoFilterBackend` on all viewsets
- **Search**: `SearchFilter` on text fields
- **Ordering**: `OrderingFilter` available
- **Tenant scoping**: `TenantMiddleware` sets `request.tenant`; all queryset views should filter by tenant automatically

---

## 9. Template-Based Views (Non-API)

The platform has 34+ Bootstrap 5 templates with template views:

| URL Prefix | Feature |
|-----------|---------|
| `/dashboard/` | Main dashboard with analytics |
| `/extraction/` | Extraction workbench (6-tab console) |
| `/extraction/control-center/` | Extraction control center and settings |
| `/invoices/` | Invoice list and detail |
| `/reconciliation/` | Reconciliation workbench |
| `/cases/` | Case console |
| `/reviews/` | Review queue |
| `/governance/` | Audit log and governance console |
| `/agents/` | Agent run and definition management |
| `/vendors/` | Vendor management |
| `/procurement/` | Procurement intelligence |
| `/posting/` | Invoice posting workflow |
| `/eval/` | Evaluation framework dashboard |
| `/erp-connections/` | ERP connection management |
| `/copilot/` | Copilot conversational UI |
