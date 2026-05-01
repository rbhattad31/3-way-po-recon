---
description: "Add a new RBAC permission, role, or permission assignment to the platform. Updates seed_rbac.py PERMISSIONS list, ROLE_MATRIX, and checks all enforcement points (ViewSet permission_classes, template {% has_permission %} tags, FBV decorators)."
agent: agent
argument-hint: "Permission to add (e.g. 'reports.export permission for FINANCE_MANAGER and ADMIN roles')"
tools: [read, edit, search]
---

Add a new RBAC permission to the 3-Way PO Reconciliation Platform.

**Step 1 — Check Existing Permissions**
- Read `apps/accounts/management/commands/seed_rbac.py`
- Verify the permission code does not already exist
- Confirm the naming convention: `{module}.{action}` (e.g. `reports.export`)

**Step 2 — Add Permission**
- Add to `PERMISSIONS` list in `seed_rbac.py` with: `code`, `module`, `action`, `description`
- Add to `ROLE_MATRIX` for each role that should have it (use True/False per role column)

**Step 3 — Add to Agent Permissions (if agent-related)**
- If this is an `agents.*` permission: add to `AGENT_PERMISSIONS` dict in `apps/agents/services/guardrails_service.py`
- If this is a tool permission: set as `required_permission` on the tool class

**Step 4 — Enforce in API**
- Find the relevant ViewSet in `apps/<app>/views.py`
- Add or update `permission_classes = [HasPermissionCode("<code>")]`

**Step 5 — Enforce in Templates (if applicable)**
- Add `{% has_permission "<code>" as can_do %}` gates in relevant templates
- Gate sidebar navigation entries in `templates/base.html`

**Step 6 — Enforce in CBV/FBV (if applicable)**
- CBV: add `required_permission = "<code>"` on the view class
- FBV: add `@permission_required_code("<code>")` decorator

**Step 7 — Seed Command**
- Provide the command to run: `python manage.py seed_rbac --sync-users`

**Permission to add**: $input
