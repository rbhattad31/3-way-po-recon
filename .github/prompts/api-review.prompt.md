---
mode: agent
description: "Review or add a DRF API endpoint with tenant isolation, RBAC, pagination, and filtering"
---

# API Review / New API Endpoint

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/current_system_review/02_Django_App_Landscape.md` -- app-level API surface, URL mounting conventions
- `docs/current_system_review/07_RBAC_and_Security_Posture.md` -- RBAC permission classes, middleware chain, permission precedence
- `docs/MULTI_TENANT.md` -- tenant isolation in ViewSets, `TenantQuerysetMixin`, platform admin bypass

### Source Files
- `apps/core/permissions.py` -- `HasPermissionCode`, `HasAnyPermission`, `HasRole`, `IsPlatformAdmin` (study all DRF permission classes)
- `apps/core/tenant_utils.py` -- `TenantQuerysetMixin` (study `get_queryset()` override)
- `apps/reconciliation/views.py` -- canonical ViewSet example with full RBAC + filtering + pagination
- `apps/posting/views.py` -- example with custom actions (`@action(detail=True)`) and status transitions
- `apps/auditlog/views.py` -- governance API examples (read-only, multi-filter, ADMIN-only)
- `apps/extraction/serializers.py` -- separate List/Detail serializer pattern
- `config/urls.py` -- how `api_urls.py` files are included under `/api/v1/<app>/`

### Comprehension Check
1. All APIs are under `/api/v1/<app>/`, registered via `DefaultRouter` in `api_urls.py`
2. `TenantQuerysetMixin` filters `get_queryset()` by `request.tenant` -- platform admins see all
3. `perform_create()` must set `tenant=request.tenant` on new objects
4. Permission classes: `[IsAuthenticated, HasPermissionCode("module.action")]`
5. Default pagination: `PageNumberPagination` with `page_size=25`
6. Filtering: `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter` in `filter_backends`

---

## API Review Checklist

When reviewing an existing API endpoint, verify all of these:

### Tenant Isolation
- [ ] ViewSet uses `TenantQuerysetMixin` (or manually filters by `request.tenant`)
- [ ] `perform_create()` sets `tenant=request.tenant`
- [ ] Nested lookups filter by tenant (e.g. FK validation)
- [ ] Platform admin bypass works correctly (sees cross-tenant data)

### RBAC Enforcement
- [ ] `permission_classes` includes `IsAuthenticated` + at least one RBAC check
- [ ] Permission code follows `{module}.{action}` convention
- [ ] Custom actions (`@action`) have their own `permission_classes`
- [ ] Write operations require higher permission than read (e.g. `module.edit` vs `module.view`)

### Serializer Security
- [ ] `tenant` field excluded from input (set server-side only)
- [ ] Sensitive fields in `read_only_fields` (e.g. `created_by`, `updated_at`, `is_active`)
- [ ] No raw SQL or `.extra()` calls in serializer validation
- [ ] Nested serializers do not expose cross-tenant data

### Pagination and Filtering
- [ ] `pagination_class` set (defaults to 25/page)
- [ ] `filter_backends` includes `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`
- [ ] `filterset_fields` covers the most common query patterns
- [ ] `ordering_fields` is explicit (not `__all__`)
- [ ] `search_fields` uses `^` prefix for starts-with on indexed columns

### Response Format
- [ ] Consistent error responses (DRF standard `{"detail": "..."}`)
- [ ] List endpoints return paginated results (not unbounded querysets)
- [ ] Detail endpoints return full nested data

---

## New API Endpoint Steps

### 1. Create Serializer

In `apps/<app>/serializers.py`:
```python
class MyModelListSerializer(serializers.ModelSerializer):
    class Meta:
        model = MyModel
        fields = ["id", "name", "status", "created_at"]

class MyModelDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = MyModel
        fields = "__all__"
        read_only_fields = ["tenant", "created_at", "updated_at", "created_by"]
```

### 2. Create ViewSet

In `apps/<app>/views.py`:
```python
from apps.core.permissions import HasPermissionCode
from apps.core.tenant_utils import TenantQuerysetMixin

class MyModelViewSet(TenantQuerysetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, HasPermissionCode("module.view")]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return MyModelListSerializer
        return MyModelDetailSerializer

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant, created_by=self.request.user)
```

### 3. Register Routes

In `apps/<app>/api_urls.py`:
```python
from rest_framework.routers import DefaultRouter
router = DefaultRouter()
router.register(r"my-models", MyModelViewSet, basename="my-model")
urlpatterns = router.urls
```

### 4. Add Permissions

In `seed_rbac.py`, add `module.view`, `module.create`, `module.edit`, `module.delete` permissions and map to roles.

---

## Constraints

- Never expose `tenant` as a writable field
- Never use `__all__` for `ordering_fields` (information disclosure risk)
- Never return unbounded querysets -- always paginate
- ASCII only in error messages and response strings
