---
description: "Use when working on multi-tenant isolation, TenantMiddleware, TenantQuerysetMixin, scoped_queryset, tenant FK patterns, platform admin cross-tenant access, or Celery task tenant propagation."
applyTo: "apps/**/*.py"
---
# Multi-Tenant Isolation Conventions

## Tenant Entity
`CompanyProfile` (in `accounts` app) is the tenant entity. Every business model has:
```python
tenant = models.ForeignKey("accounts.CompanyProfile", on_delete=models.CASCADE, related_name="...")
```

## Middleware Stack
`TenantMiddleware` sets `request.tenant = request.user.company` on every authenticated request.

## Enforcement Points
| Layer | Pattern |
|-------|---------|
| ViewSet/CBV | `TenantQuerysetMixin` — auto-filters queryset to `request.tenant` |
| FBV | `@require_tenant()` decorator from `apps.core.tenant_utils` |
| Service | `scoped_queryset(queryset, tenant)` or `.filter(tenant=tenant)` |
| Agent Tool | `BaseTool._scoped(queryset, tenant)` |
| Celery Task | Accept `tenant_id: int` argument; resolve `CompanyProfile` at task start |

## Platform Admin Bypass
- `user.is_platform_admin=True` bypasses tenant scoping
- `SUPER_ADMIN` role (rank 1) gets cross-tenant access
- ADMIN role (rank 2) and SYSTEM_AGENT bypass scope checks within their tenant

## Celery Task Pattern
```python
@shared_task(bind=True)
def my_task(self, entity_id: int, tenant_id: int):
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.get(pk=tenant_id)
    # ... use tenant for scoped queries
```

## Common Mistakes to Avoid
- NEVER query business entities without a tenant filter (except platform admin views)
- NEVER use `Model.objects.all()` in a service — always use `scoped_queryset()` or `.filter(tenant=tenant)`
- NEVER hard-code `tenant_id` values
- NEVER store tenant-specific config without a `tenant` FK

## Testing Tenant Isolation
Every service/API test MUST include a tenant isolation test:
```python
def test_other_tenant_cannot_access(client, user_tenant_a, user_tenant_b, record_tenant_a):
    client.force_authenticate(user=user_tenant_b)
    response = client.get(f"/api/v1/.../{ record_tenant_a.pk}/")
    assert response.status_code == 404  # not 403 — don't leak existence
```
