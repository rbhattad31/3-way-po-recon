---
description: "Use when writing or reviewing RBAC logic, permissions, roles, permission enforcement in views, template tags, or the guardrails service. Covers HasPermissionCode, PermissionRequiredMixin, permission_required_code, rbac_tags, and the 10-role system."
applyTo: "apps/accounts/**/*.py,apps/core/permissions.py,apps/core/templatetags/rbac_tags.py"
---
# RBAC Conventions

## Role Hierarchy (rank: lower = higher authority)
| Role | Rank | Notes |
|------|------|-------|
| SUPER_ADMIN | 1 | Platform admin; cross-tenant |
| ADMIN | 2 | Tenant admin; bypasses scope checks |
| FINANCE_MANAGER | 3 | Approves escalations |
| REVIEWER | 4 | Reviews exceptions |
| AP_PROCESSOR | 5 | Uploads invoices, triggers recon |
| AUDITOR | 6 | Read-only governance |
| PROCUREMENT | 7 | Procurement requests |
| SYSTEM_AGENT | 100 | Internal; bypasses scope checks |

## Permission Code Convention
Format: `{module}.{action}` — e.g. `invoices.view`, `agents.run_reconciliation`, `reports.export`

## Permission Precedence
1. ADMIN bypass (rank <= 2 or `is_platform_admin`)
2. User DENY override (`UserPermissionOverride.effect = DENY`)
3. User ALLOW override
4. Role permissions (via `RolePermission`)

## Enforcement Patterns
```python
# DRF ViewSet
permission_classes = [HasPermissionCode("invoices.view")]
permission_classes = [HasAnyPermission(["invoices.view", "invoices.edit"])]

# CBV
class MyView(PermissionRequiredMixin, View):
    required_permission = "invoices.view"

# FBV
@permission_required_code("invoices.view")
def my_view(request): ...

# Template
{% has_permission "invoices.view" as can_view %}
{% if can_view %}...{% endif %}

{% if_can "invoices.edit" %}...{% end_if_can %}
```

## Data Scope (UserRole.scope_json)
- Per-assignment scope restrictions: `allowed_business_units` (list[str]), `allowed_vendor_ids` (list[int])
- Null means unrestricted
- ADMIN and SYSTEM_AGENT always bypass scope checks
- `AgentGuardrailsService.authorize_data_scope()` enforces this

## AP_PROCESSOR Scoping
AP_PROCESSOR sees only POs/GRNs/Vendors linked to their own uploaded invoices.
Service-layer: `_scope_pos_for_user()`, `_scope_grns_for_user()`, `_scope_vendors_for_user()`

## Adding a Permission — Checklist
1. Add to `PERMISSIONS` list in `seed_rbac.py` with code, module, action, description
2. Add to `ROLE_MATRIX` (True/False per role column)
3. If `agents.*`: add to `AGENT_PERMISSIONS` in `guardrails_service.py`
4. Enforce with `HasPermissionCode` in ViewSet
5. Gate with `{% has_permission %}` in templates
6. Run: `python manage.py seed_rbac --sync-users`
