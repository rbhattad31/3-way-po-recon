# Multi-Tenant Architecture

> **Version**: 1.0 -- **Last Updated**: April 2026

---

## Table of Contents

1. [Overview](#1-overview)
2. [Tenant Model](#2-tenant-model)
3. [User-Tenant Relationship](#3-user-tenant-relationship)
4. [Platform Admin (Super Admin)](#4-platform-admin-super-admin)
5. [Tenant Middleware](#5-tenant-middleware)
6. [Data Isolation Strategy](#6-data-isolation-strategy)
7. [RBAC Integration](#7-rbac-integration)
8. [Agent & Tool Tenant Scoping](#8-agent--tool-tenant-scoping)
9. [Celery Task Tenant Propagation](#9-celery-task-tenant-propagation)
10. [Service Layer Patterns](#10-service-layer-patterns)
11. [Template View Patterns](#11-template-view-patterns)
12. [API ViewSet Patterns](#12-api-viewset-patterns)
13. [Key Files Reference](#13-key-files-reference)
14. [Adding a New Tenant-Scoped Model](#14-adding-a-new-tenant-scoped-model)
15. [Adding a New Tenant-Scoped View](#15-adding-a-new-tenant-scoped-view)
16. [Debugging Tenant Issues](#16-debugging-tenant-issues)
17. [Design Decisions](#17-design-decisions)

---

## 1. Overview

The platform uses a **shared-database, shared-schema** multi-tenant architecture. All tenants share the same MySQL database and tables, with row-level isolation enforced via a `tenant` foreign key on every business model. This approach was chosen for:

- **Simplicity**: Single database, single deployment, single migration path.
- **Cost efficiency**: No per-tenant database provisioning.
- **Query flexibility**: Platform admins can run cross-tenant analytics.
- **Operational ease**: One Celery worker pool serves all tenants.

The tenant entity is `CompanyProfile` (`apps/accounts/models.py`). Every business model (Invoice, PO, GRN, Vendor, ReconciliationResult, AgentRun, PostingRun, etc.) has a nullable `tenant` FK pointing to `CompanyProfile`.

---

## 2. Tenant Model

```python
# apps/accounts/models.py
class CompanyProfile(BaseModel, SoftDeleteMixin):
    name            = CharField(max_length=255)
    legal_name      = CharField(max_length=255, blank=True)
    country         = CharField(max_length=100, blank=True)
    currency        = CharField(max_length=10, default="INR")
    industry        = CharField(max_length=100, blank=True)
    size_category   = CharField(max_length=50, blank=True)
    website         = URLField(blank=True)
    logo            = ImageField(blank=True, null=True)
    # ... additional fields
```

Related models:
- `CompanyAlias` -- alternate names for the same company
- `CompanyTaxID` -- tax identifiers (GST, VAT, EIN, etc.)

---

## 3. User-Tenant Relationship

```
User.company  -->  CompanyProfile (FK, nullable)
```

- Every non-platform user belongs to exactly one tenant via `User.company`.
- `system-agent@internal` has `company=NULL` -- it is a platform-level service account that inherits tenant context from the entity it processes.
- `superadmin@bradsol.com` (or any `is_platform_admin=True` user) has `company=NULL` -- operates cross-tenant.

---

## 4. Platform Admin (Super Admin)

The platform has a two-tier admin model:

| Level | Flag | Scope | RBAC Role |
|---|---|---|---|
| **Tenant Admin** | `User.role = "ADMIN"` | Full access within their own tenant | `ADMIN` (rank 10) |
| **Platform Admin** | `User.is_platform_admin = True` | Cross-tenant access, platform settings | `SUPER_ADMIN` (rank 1) |

### Platform Admin Privileges

- Bypasses all tenant scoping (`request.tenant = None` -- sees all data).
- Bypasses all permission checks (`_is_platform_admin()` returns True early in every permission helper).
- Can impersonate a specific tenant by sending the `X-Tenant-ID` HTTP header.
- Has 4 exclusive permissions: `tenants.view`, `tenants.manage`, `tenants.impersonate`, `platform.settings`.
- `is_platform_admin` is a dedicated BooleanField on User -- independent of Django's `is_superuser`.

### Permission Resolution with Platform Admin

```
0. Platform Admin bypass -> all permissions granted (checked first)
1. Tenant Admin bypass -> all non-platform permissions granted
2. User DENY overrides -> explicitly blocked
3. User ALLOW overrides -> explicitly granted
4. Role permissions -> union of all active role permissions
5. Legacy fallback -> uses User.role field if no UserRole entries exist
```

---

## 5. Tenant Middleware

```python
# apps/core/middleware.py
class TenantMiddleware:
```

Runs after `AuthenticationMiddleware`. Sets `request.tenant` on every request:

| User Type | `request.tenant` Value |
|---|---|
| Anonymous | `None` |
| Platform Admin (`is_platform_admin=True` or `is_superuser=True`) | `None` (cross-tenant), or `CompanyProfile` if `X-Tenant-ID` header sent |
| Regular user with `company` | `User.company` |
| Regular user without `company` | `None` (will be blocked by `require_tenant()`) |

Exempt paths (no tenant resolution): `/admin/`, `/accounts/login/`, `/accounts/logout/`, `/health/`.

---

## 6. Data Isolation Strategy

### 6.1 Schema-Level: tenant FK on all business models

Every model that inherits from `BaseModel` (which is all business entities) has:

```python
tenant = models.ForeignKey(
    "accounts.CompanyProfile",
    on_delete=models.SET_NULL,
    null=True, blank=True,
    related_name="+",
    db_index=True,
)
```

This covers 28+ models across all apps: Invoice, PurchaseOrder, GoodsReceiptNote, Vendor, ReconciliationRun, ReconciliationResult, ReconciliationException, AgentOrchestrationRun, AgentRun, AgentStep, AgentMessage, DecisionLog, AgentRecommendation, ReviewAssignment, ReviewDecision, APCase, CaseStage, DocumentUpload, ExtractionResult, ExtractionApproval, InvoicePosting, PostingRun, EvalRun, AuditEvent, ProcessingLog, ERPConnection, CopilotSession, ProcurementRequest, and more.

### 6.2 Query-Level: Automatic scoping

All query paths enforce tenant filtering:

| Layer | Mechanism | File |
|---|---|---|
| DRF ViewSets | `TenantQuerysetMixin` on `get_queryset()` | `apps/core/tenant_utils.py` |
| Template CBV views | `TenantQuerysetMixin` | `apps/core/tenant_utils.py` |
| Template FBV views | `require_tenant(request)` | `apps/core/tenant_utils.py` |
| Service layer | `scoped_queryset(Model, tenant)` | `apps/core/tenant_utils.py` |
| Agent tools | `BaseTool._scoped(queryset)` | `apps/tools/registry/base.py` |
| Detail views | `assert_tenant_access(obj, tenant)` | `apps/core/tenant_utils.py` |

### 6.3 Creation-Level: Tenant propagation

When creating new records, the tenant is set at creation time:

```python
# In views / tasks / services:
Invoice.objects.create(
    ...,
    tenant=request.tenant,  # or tenant passed from Celery task
)
```

All record-creation sites (~32 locations across the codebase) propagate the tenant from:
- `request.tenant` (web requests)
- `tenant` parameter (Celery tasks)
- Parent entity's `.tenant` (FK chains -- e.g., ReconciliationResult inherits from Invoice)

---

## 7. RBAC Integration

### Tenant-scoped RBAC

- RBAC roles and permissions are **platform-global** (shared across tenants). A role like `AP_PROCESSOR` means the same thing in every tenant.
- **UserRole assignments** are per-user (and each user belongs to one tenant). So a user's roles are implicitly tenant-scoped.
- `UserRole.scope_json` can further restrict a user within their tenant: `allowed_business_units` and `allowed_vendor_ids` are checked by `AgentGuardrailsService.authorize_data_scope()`.

### Admin hierarchy

```
SUPER_ADMIN (rank 1, platform-wide)
  |
  +-- ADMIN (rank 10, tenant-scoped) -- per-tenant full access
        |
        +-- FINANCE_MANAGER (rank 20) -- per-tenant
        +-- AUDITOR (rank 30) -- per-tenant
        +-- REVIEWER (rank 40) -- per-tenant
        +-- AP_PROCESSOR (rank 50) -- per-tenant
```

### Template context

The `rbac_context` processor injects:
- `is_admin` -- True for `SUPER_ADMIN`, `ADMIN`, or `is_platform_admin` users
- `user_permissions` -- frozenset of effective permission codes
- `user_role_codes` -- set of active RBAC role codes

---

## 8. Agent & Tool Tenant Scoping

### How agents inherit tenant context

```
1. View/Task sets tenant (from request.tenant or task arg)
2. AgentOrchestrator.execute(result, tenant=tenant)
3. AgentContext(tenant=tenant) created
4. BaseAgent stores self._agent_context = ctx
5. _execute_tool() injects tenant into tool kwargs
6. BaseTool.execute() extracts tenant -> self._tenant
7. Tool.run() uses self._scoped(queryset) for all DB queries
```

### BaseTool._scoped()

```python
def _scoped(self, queryset):
    """Apply tenant filter if tenant was provided."""
    if self._tenant is not None:
        return queryset.filter(tenant=self._tenant)
    return queryset
```

All 6 agent tools use `self._scoped()` on every database query:
- **POLookupTool** -- PO lookup by number, vendor PO listing
- **GRNLookupTool** -- GRN lookup via PO
- **VendorSearchTool** -- vendor name/code/alias search
- **InvoiceDetailsTool** -- invoice detail retrieval
- **ExceptionListTool** -- reconciliation exception listing
- **ReconciliationSummaryTool** -- reconciliation result summary

### System agent behavior

- `system-agent@internal` has `company=NULL` (no tenant).
- When processing a specific entity (e.g., ReconciliationResult), the tenant is resolved from the entity itself and threaded through the pipeline.
- If no tenant is provided (e.g., platform-admin-triggered or legacy code path), tools execute without tenant filtering -- this is safe because the entity context constrains the data boundary.

---

## 9. Celery Task Tenant Propagation

All tenant-aware Celery tasks accept `tenant_id` as the first argument:

```python
@shared_task(bind=True)
def process_case_task(self, tenant_id=None, case_id=0):
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    # ... use tenant for entity lookup and downstream propagation
```

### Task-level entity guards

Tasks fetch entities with tenant filtering when a tenant is provided:

```python
qs = APCase.objects.all()
if tenant:
    qs = qs.filter(tenant=tenant)
case = qs.get(id=case_id)
```

This prevents a task from accidentally processing an entity from the wrong tenant.

### Tasks with tenant propagation

| Task | File | Tenant Handling |
|---|---|---|
| `process_case_task` | `apps/cases/tasks.py` | `tenant_id` arg, guards APCase fetch |
| `reprocess_case_from_stage_task` | `apps/cases/tasks.py` | `tenant_id` arg, guards APCase fetch |
| `run_agent_pipeline_task` | `apps/agents/tasks.py` | `tenant_id` arg, guards ReconciliationResult fetch, passes to orchestrator |
| `process_invoice_upload_task` | `apps/extraction/tasks.py` | `tenant_id` arg, propagated to created records |
| `prepare_posting_task` | `apps/posting/tasks.py` | `tenant_id` arg |
| `run_reconciliation_task` | `apps/reconciliation/tasks.py` | `tenant_id` arg |

### Call-site pattern

When enqueuing a task, always pass `tenant_id`:

```python
process_case_task.delay(
    tenant_id=request.tenant.pk if request.tenant else None,
    case_id=case.pk,
)
```

---

## 10. Service Layer Patterns

Services that need tenant scoping accept `tenant=None`:

```python
class AuditService:
    @staticmethod
    def fetch_case_history(case_id, tenant=None, limit=100):
        qs = AuditEvent.objects.filter(case_id=case_id)
        if tenant:
            qs = qs.filter(tenant=tenant)
        return qs.order_by("-created_at")[:limit]
```

For services that always need a tenant (e.g., creating records):

```python
from apps.core.tenant_utils import scoped_queryset

def get_open_cases(tenant):
    return scoped_queryset(APCase, tenant).filter(status="OPEN")
```

---

## 11. Template View Patterns

### Class-based views

```python
from apps.core.tenant_utils import TenantQuerysetMixin

class InvoiceListView(TenantQuerysetMixin, PermissionRequiredMixin, ListView):
    model = Invoice
    required_permission = "invoices.view"
    # TenantQuerysetMixin automatically filters get_queryset() by request.tenant
```

### Function-based views

```python
from apps.core.tenant_utils import require_tenant

def invoice_detail(request, pk):
    tenant = require_tenant(request)
    invoice = get_object_or_404(Invoice, pk=pk, tenant=tenant)
    ...
```

### Cross-tenant guards on entity-specific views

For views that load a specific entity (audit history, governance, etc.), the entity is verified to belong to the correct tenant:

```python
from apps.core.tenant_utils import assert_tenant_access

def invoice_audit_history(request, invoice_id):
    tenant = require_tenant(request)
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    if tenant:
        assert_tenant_access(invoice, tenant)
    ...
```

---

## 12. API ViewSet Patterns

All DRF ViewSets use `TenantQuerysetMixin`:

```python
from apps.core.tenant_utils import TenantQuerysetMixin

class InvoiceViewSet(TenantQuerysetMixin, ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    # TenantQuerysetMixin scopes queryset to request.tenant
```

---

## 13. Key Files Reference

| File | Purpose |
|---|---|
| `apps/accounts/models.py` | `CompanyProfile` model, `User.company` FK, `User.is_platform_admin` |
| `apps/core/middleware.py` | `TenantMiddleware` resolves `request.tenant` |
| `apps/core/tenant_utils.py` | `TenantQuerysetMixin`, `require_tenant()`, `get_tenant_or_none()`, `scoped_queryset()`, `assert_tenant_access()` |
| `apps/core/permissions.py` | `_is_platform_admin()`, `_is_admin()` with SUPER_ADMIN support |
| `apps/core/context_processors.py` | `rbac_context` injects `is_admin` (SUPER_ADMIN aware) |
| `apps/core/templatetags/rbac_tags.py` | `has_role` checks SUPER_ADMIN |
| `apps/core/enums.py` | `UserRole.SUPER_ADMIN` enum value |
| `apps/tools/registry/base.py` | `BaseTool._scoped()` tenant helper |
| `apps/tools/registry/tools.py` | All 6 tools use `self._scoped()` |
| `apps/agents/services/base_agent.py` | `_execute_tool()` injects tenant from `AgentContext` |
| `apps/agents/services/orchestrator.py` | Threads `tenant` to `AgentContext` and audit records |
| `apps/cases/orchestrators/stage_executor.py` | Passes `tenant=case.tenant` to agent orchestrator |
| `apps/accounts/management/commands/seed_rbac.py` | SUPER_ADMIN role (rank 1) + platform permissions |

---

## 14. Adding a New Tenant-Scoped Model

1. Inherit from `BaseModel` (which includes `tenant` FK via migration 0005).
2. In all creation sites, set `tenant=request.tenant` or `tenant=parent.tenant`.
3. Register in admin with list_filter including `tenant`.
4. In API ViewSets, apply `TenantQuerysetMixin`.
5. In template views, use `require_tenant()` or `TenantQuerysetMixin`.
6. In Celery tasks, accept `tenant_id` and resolve to `CompanyProfile`.

---

## 15. Adding a New Tenant-Scoped View

1. For CBVs: add `TenantQuerysetMixin` to the class hierarchy.
2. For FBVs: call `require_tenant(request)` at the top.
3. For detail views: use `assert_tenant_access(obj, tenant)` after fetching the object.
4. For create views: set `obj.tenant = request.tenant` before saving.

---

## 16. Debugging Tenant Issues

| Symptom | Cause | Fix |
|---|---|---|
| User sees all tenants' data | `is_superuser=True` or `is_platform_admin=True` on the user | Clear `is_superuser` for non-platform-admin users |
| `PermissionDenied: No tenant context` | User has `company=NULL` and is not a platform admin | Assign a `CompanyProfile` to the user |
| New records have `tenant=NULL` | Creation site missing `tenant=` | Add `tenant=request.tenant` at the creation site |
| Agent tools return cross-tenant data | `AgentContext.tenant` is None | Ensure the task/view passes tenant to the orchestrator |
| Celery task processes wrong tenant's entity | Task fetches entity without tenant filter | Add `if tenant: qs = qs.filter(tenant=tenant)` guard |
| Admin console shows all users | Admin user has `is_platform_admin=True` | Expected behavior -- platform admins see all |
| Dashboard counts are inflated | Dashboard service queries not tenant-scoped | Use `scoped_queryset()` in service methods |

---

## 17. Design Decisions

| Decision | Rationale |
|---|---|
| **Shared-database, shared-schema** | Simplest model for Django; single migration path; no connection routing |
| **Nullable tenant FK** | Platform-level entities (PromptTemplate, AgentDefinition, Role, Permission) are tenant-independent |
| **is_platform_admin vs is_superuser** | `is_superuser` is Django's built-in flag with too much implicit power (admin site, model permissions). `is_platform_admin` is a purpose-built flag for platform-level access |
| **SUPER_ADMIN as RBAC role** | Keeps role hierarchy consistent. Platform admin gets the SUPER_ADMIN role (rank 1) with all permissions including platform-exclusive ones (tenants.*, platform.settings) |
| **Tenant inherited by system agent** | `system-agent@internal` is tenant-less. It inherits tenant context from the entity it processes, not from its own user record |
| **Tool-level scoping** | Agent tools apply `self._scoped(queryset)` because tools are the data-access boundary for LLM agents. Agents themselves do not query DB directly |
| **Task-level entity guards** | Celery tasks verify the fetched entity belongs to the expected tenant. Prevents race conditions where a task ID could be reused across tenants |
| **Tenant on audit records** | All agent runs, audit events, and governance records carry `tenant` FK for per-tenant reporting and compliance |
