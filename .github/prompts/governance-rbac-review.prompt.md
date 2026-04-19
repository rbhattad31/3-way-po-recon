---
mode: agent
description: "Review or add RBAC permissions, roles, guardrails, and audit enforcement"
---

# Governance / RBAC Review

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/current_system_review/07_RBAC_and_Security_Posture.md` -- full RBAC architecture: 10 roles, 65+ permissions, middleware chain, permission precedence, data scoping
- `docs/current_system_review/08_Audit_and_Traceability.md` -- AuditEvent model (38+ event types, 20+ fields), CaseTimelineService, governance API
- `docs/AGENT_ARCHITECTURE.md` -- AgentGuardrailsService, per-tool/per-agent authorization, SYSTEM_AGENT identity, 9 guardrail audit event types
- `docs/MULTI_TENANT.md` -- tenant scoping, platform admin bypass, `is_platform_admin` flag

### Source Files
- `apps/accounts/rbac_models.py` -- Role, Permission, RolePermission, UserRole, UserPermissionOverride, `scope_json`
- `apps/accounts/rbac_services.py` -- `RBACEventService` (9 audit event types for RBAC mutations)
- `apps/core/permissions.py` -- all DRF/CBV/FBV permission classes: `HasPermissionCode`, `HasAnyPermission`, `HasRole`, `PermissionRequiredMixin`, `@permission_required_code`
- `apps/core/templatetags/rbac_tags.py` -- `{% has_permission %}`, `{% has_role %}`, `{% has_any_permission %}`, `{% if_can %}`
- `apps/agents/services/guardrails_service.py` -- `AgentGuardrailsService`: `authorize_orchestration()`, `authorize_agent()`, `authorize_tool()`, `authorize_recommendation()`, `authorize_data_scope()`
- `apps/core/middleware.py` -- `TenantMiddleware`, `RBACMiddleware` (permission cache pre-load), `LoginRequiredMiddleware`
- `apps/accounts/management/commands/seed_rbac.py` -- PERMISSIONS list, ROLE_MATRIX, system role definitions
- `apps/auditlog/models.py` -- `AuditEvent` model fields and `AuditEventType` enum
- `apps/auditlog/services.py` -- `AuditService` query helpers: `fetch_case_history()`, `fetch_access_history()`, `fetch_permission_denials()`
- `apps/auditlog/timeline_service.py` -- `CaseTimelineService` (8 event categories, RBAC badges)

### Comprehension Check
1. Permission precedence: ADMIN bypass -> user DENY override -> user ALLOW override -> role permissions
2. `UserRole.scope_json` restricts per-assignment: `allowed_business_units`, `allowed_vendor_ids`. Null = unrestricted. ADMIN/SYSTEM_AGENT bypass.
3. `AgentGuardrailsService.resolve_actor()` returns `system-agent@internal` with SYSTEM_AGENT role when no human user context
4. Every guardrail decision (grant/deny) emits an `AuditEvent` (9 types: GUARDRAIL_GRANTED/DENIED, TOOL_CALL_AUTHORIZED/DENIED, etc.)
5. Permission code convention: `{module}.{action}` (e.g. `invoices.view`, `agents.run_reconciliation`)

---

## RBAC Review Checklist

### Permission Coverage
- [ ] Every API endpoint has `permission_classes` with at least `IsAuthenticated` + one RBAC check
- [ ] Every template view uses `PermissionRequiredMixin` or `@permission_required_code`
- [ ] Every template action is gated with `{% has_permission "module.action" %}`
- [ ] Write operations require stricter permissions than read operations

### Role Matrix
- [ ] New permissions are mapped to roles in `seed_rbac.py` ROLE_MATRIX
- [ ] SYSTEM_AGENT role has all permissions needed for autonomous agent operations
- [ ] AP_PROCESSOR has appropriately scoped access (own invoices only)
- [ ] AUDITOR has read-only access to governance endpoints

### Data Scoping
- [ ] `UserRole.scope_json` restrictions are checked via `authorize_data_scope()`
- [ ] Business unit and vendor ID scoping is enforced before data access
- [ ] Platform admins bypass scoping correctly
- [ ] ADMIN and SYSTEM_AGENT bypass scope checks

### Agent Guardrails
- [ ] New agents have `agents.run_<type>` permission in PERMISSIONS list
- [ ] New tools have `required_permission` set and mapped to SYSTEM_AGENT role
- [ ] New recommendation types are covered by `recommendations.*` permissions
- [ ] Guardrail decisions emit AuditEvent records

### Audit Trail
- [ ] State-changing operations emit `AuditEvent` with correct `event_type`
- [ ] `AuditEvent` includes `actor`, `tenant`, `status_before`, `status_after`
- [ ] Sensitive operations include RBAC snapshot fields (`actor_primary_role`, `actor_roles_snapshot`)
- [ ] `CaseTimelineService` can render new events if they are case-related

---

## When Adding a New Permission

1. Add to `seed_rbac.py` PERMISSIONS list: `{"code": "module.action", "module": "module", "action": "action"}`
2. Map to roles in ROLE_MATRIX: decide which roles get this permission
3. Map to SYSTEM_AGENT if agents need it
4. Run `python manage.py seed_rbac --sync-users`
5. Use in DRF: `HasPermissionCode("module.action")`
6. Use in CBV: `required_permission = "module.action"`
7. Use in template: `{% has_permission "module.action" as can_do %}`

## When Adding a New Role

1. Add to `seed_rbac.py` SYSTEM_ROLES list with unique `rank` value
2. Assign permissions in ROLE_MATRIX
3. If system role: set `is_system_role=True`
4. Run seed command and verify
5. Test: user with new role can access expected endpoints, denied from others

---

## Constraints

- Permission codes are lowercase: `module.action` (not `Module.Action`)
- Never hardcode role names in business logic -- check permissions, not roles
- Never bypass RBAC checks with `.objects.all()` in views -- always use permission classes + tenant mixin
- ADMIN bypass is handled by permission classes, not by `if user.role == "ADMIN"` checks
- ASCII only in permission codes, role names, audit descriptions
