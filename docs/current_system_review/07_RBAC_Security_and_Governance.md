# 07 — RBAC, Security, and Governance

**Generated**: 2026-04-09 | **Method**: Code-first inspection  
**Evidence files**: `accounts/rbac_models.py`, `accounts/rbac_services.py`, `agents/services/guardrails_service.py`, `config/settings.py` (MIDDLEWARE)

---

## 1. Auth / AuthZ Model

### Authentication
- **Django session-based authentication** (no JWT; `SessionAuthentication` in DRF)
- Custom user model: `accounts.User` (identified by `email`, not username)
- `LOGIN_URL = "/accounts/login/"`, `LOGIN_REDIRECT_URL = "/dashboard/"`
- All endpoints require authentication (enforced by `LoginRequiredMiddleware`)

### Authorization Stack

```
Middleware (request-level):
  TenantMiddleware      → sets request.tenant from User.company
  LoginRequiredMiddleware → redirects unauthenticated requests to login
  RBACMiddleware        → ?  (may enforce view-level permission checks)
  RequestTraceMiddleware → injects trace_id for distributed tracing

Service-level:
  AgentGuardrailsService → explicit RBAC for all agent/tool/recommendation actions
  
Tool-level:
  BaseTool.required_permission → checked before every tool invocation

DRF:
  DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]
  (view-level DRF permissions inferred per viewset, not globally inspected)
```

---

## 2. RBAC Models

### `Role`
| Field | Notes |
|-------|-------|
| code (unique) | Machine name: ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR, SYSTEM_AGENT |
| name | Display label |
| is_system_role | System roles cannot be deleted or have code changed |
| rank | Lower = higher authority; display ordering |

### `Permission`
| Field | Notes |
|-------|-------|
| code (unique) | Format: `module.action` (e.g. `invoices.view`, `reconciliation.run`) |
| module | Grouping: invoices, reconciliation, cases, agents, vendors, etc. |
| action | Verb: view, create, edit, delete, run, approve, etc. |

### `RolePermission`
Many-to-many mapping: Role → Permission with `is_allowed` bool.

### `UserRole`
| Field | Notes |
|-------|-------|
| user / role | The assignment |
| is_primary | Primary role synced to legacy `User.role` field |
| expires_at | Null = never expires |
| scope_json | Optional restrictions: `allowed_business_units`, `allowed_vendor_ids` |

### `UserPermissionOverride`
Per-user ALLOW or DENY override for a specific permission. Supports expiry.

### `MenuConfig`
Database-driven sidebar/menu visibility control by `required_permission` code.

---

## 3. Permission Precedence

From `rbac_models.py` docstring:
```
1. ADMIN role → always granted (bypass all checks)
2. User-level DENY override → blocks even if role grants it  
3. User-level ALLOW override → grants even without role
4. Role-level permissions → union of all active role permissions
```

Permission check logic in `rbac_services.py`:
- Checks `UserRole.is_effective` (is_active and not expired)
- Checks `UserPermissionOverride.is_effective` (is_active and not expired)

---

## 4. System Role Summary

| Role | Code | Purpose | Key Permissions |
|------|------|---------|----------------|
| Admin | `ADMIN` | Full platform control | All permissions (bypass) |
| AP Processor | `AP_PROCESSOR` | Invoice processing | invoices.*, extraction.*, reconciliation.run |
| Reviewer | `REVIEWER` | Exception review | reviews.*, cases.view |
| Finance Manager | `FINANCE_MANAGER` | Escalation + approvals | cases.escalate, approvals |
| Auditor | `AUDITOR` | Read-only compliance | *.view (all modules), no write |
| System Agent | `SYSTEM_AGENT` | Autonomous pipeline | agents.orchestrate, agents.run_*, agents.run_supervisor, invoices.view, reconciliation.view |

---

## 5. Agent RBAC — Permission Map

### Orchestration
```python
ORCHESTRATE_PERMISSION = "agents.orchestrate"
```

### Per-Agent Permissions
```python
AGENT_PERMISSIONS = {
    "INVOICE_EXTRACTION":          "agents.run_extraction",
    "INVOICE_UNDERSTANDING":       "agents.run_extraction",
    "PO_RETRIEVAL":                "agents.run_po_retrieval",
    "GRN_RETRIEVAL":               "agents.run_grn_retrieval",
    "RECONCILIATION_ASSIST":       "agents.run_reconciliation_assist",
    "EXCEPTION_ANALYSIS":          "agents.run_exception_analysis",
    "REVIEW_ROUTING":              "agents.run_review_routing",
    "CASE_SUMMARY":                "agents.run_case_summary",
    "SYSTEM_REVIEW_ROUTING":       "agents.run_system_review_routing",
    "SYSTEM_CASE_SUMMARY":         "agents.run_system_case_summary",
    "SYSTEM_BULK_EXTRACTION_INTAKE": "agents.run_system_bulk_extraction_intake",
    "SYSTEM_CASE_INTAKE":          "agents.run_system_case_intake",
    "SYSTEM_POSTING_PREPARATION":  "agents.run_system_posting_preparation",
    "SUPERVISOR":                  "agents.run_supervisor",
}
```

### Per-Tool Permissions
```python
TOOL_PERMISSIONS = {
    "po_lookup":               "purchase_orders.view",
    "grn_lookup":              "grns.view",
    "vendor_search":           "vendors.view",
    "invoice_details":         "invoices.view",
    "exception_list":          "reconciliation.view",
    "reconciliation_summary":  "reconciliation.view",
    # Supervisor-specific tools (24 additional):
    "get_ocr_text":              "invoices.view",
    "classify_document":         "invoices.view",
    "extract_invoice_fields":    "extraction.run",
    "re_extract_field":          "extraction.run",
    "validate_extraction":       "extraction.run",
    "repair_extraction":         "extraction.run",
    "check_duplicate":           "invoices.view",
    "verify_vendor":             "vendors.view",
    "verify_tax_computation":    "invoices.view",
    "run_header_match":          "reconciliation.run",
    "run_line_match":            "reconciliation.run",
    "run_grn_match":             "reconciliation.run",
    "get_tolerance_config":      "reconciliation.view",
    "invoke_po_retrieval_agent": "agents.run_po_retrieval",
    "invoke_grn_retrieval_agent":"agents.run_grn_retrieval",
    "get_vendor_history":        "vendors.view",
    "get_case_history":          "cases.view",
    "persist_invoice":           "invoices.edit",
    "create_case":               "cases.create",
    "submit_recommendation":     "recommendations.route_review",
    "assign_reviewer":           "reviews.assign",
    "generate_case_summary":     "cases.view",
    "auto_close_case":           "recommendations.auto_close",
    "escalate_case":             "cases.escalate",
}
```

### Per-Recommendation Permissions
```python
RECOMMENDATION_PERMISSIONS = {
    "AUTO_CLOSE":                   "recommendations.auto_close",
    "SEND_TO_AP_REVIEW":            "recommendations.route_review",
    "ESCALATE_TO_MANAGER":          "recommendations.escalate",
    "REPROCESS_EXTRACTION":         "recommendations.reprocess",
    "SEND_TO_PROCUREMENT":          "recommendations.route_procurement",
    "SEND_TO_VENDOR_CLARIFICATION": "recommendations.vendor_clarification",
}
```

### Per-Action Permissions
```python
ACTION_PERMISSIONS = {
    "auto_close_result":    "recommendations.auto_close",
    "assign_review":        "reviews.assign",
    "escalate_case":        "cases.escalate",
    "reprocess_extraction": "extraction.reprocess",
    "rerun_reconciliation": "reconciliation.run",
}
```

---

## 6. System Agent Identity

```python
SYSTEM_AGENT_EMAIL = "system-agent@internal"
SYSTEM_AGENT_ROLE_CODE = "SYSTEM_AGENT"
```

`AgentGuardrailsService.get_system_agent_user()` returns or creates this user.  
The system agent is **NOT an admin bypass** — it has the `SYSTEM_AGENT` role with scoped permissions only.

**Fail-closed design** (from guardrails_service.py docstring):
> "if identity cannot be resolved, deny by default"

---

## 7. RBAC Snapshots in Persistent Records

The following records capture RBAC context at the time of action:

| Model | Fields |
|-------|--------|
| `AgentRun` | actor_user_id, actor_primary_role, actor_roles_snapshot_json, permission_checked, permission_source, access_granted |
| `AuditEvent` | actor_email, actor_primary_role, actor_roles_snapshot_json, permission_checked, permission_source, access_granted |
| `DecisionLog` | actor_user_id, actor_primary_role, permission_checked, authorization_snapshot_json |
| `ProcessingLog` | actor_primary_role, permission_checked, access_granted |

`permission_source` values: `ROLE | USER_OVERRIDE_ALLOW | ADMIN_BYPASS | USER_OVERRIDE_DENY | NO_PERMISSION | USER_INACTIVE`

---

## 8. Enforcement Points

| Layer | Enforcement | Notes |
|-------|------------|-------|
| Middleware | `LoginRequiredMiddleware` | Session auth required for all routes |
| Middleware | `TenantMiddleware` | Tenant scope set per request |
| Middleware | `RBACMiddleware` | View-level permission checks (details not inspected) |
| DRF views | `IsAuthenticated` (default) | Applied to all DRF viewsets |
| Agent orchestration | `AgentGuardrailsService.check_orchestrate_permission()` | Per pipeline invocation |
| Agent execution | `AgentGuardrailsService.check_agent_permission()` | Per agent type |
| Tool execution | `AgentGuardrailsService.check_tool_permission()` | Per tool invocation |
| Recommendation apply | `AgentGuardrailsService.check_recommendation_permission()` | Per recommendation type |
| Action execution | `AgentGuardrailsService.check_action_permission()` | Per protected action |

---

## 9. Governance Gaps and Risks

| Gap | Risk Level | Notes |
|-----|-----------|-------|
| `prohibited_actions` on AgentDefinition not enforced | Medium | Field exists on model but enforcement code not verified |
| No DRF permission class per viewset inspection | Medium | Global `IsAuthenticated` confirmed; field-level DRF permissions not inspected |
| `scope_json` in `UserRole` partially implemented | Medium | Comment in code: "allowed_business_units, allowed_vendor_ids supported; country/legal_entity/cost_centre pending" |
| No token-based (JWT) auth for API | Low | Session-only; acceptable for browser clients; risks for programmatic API consumers |
| `RBACMiddleware` internals not inspected | Unknown | Confirmed in MIDDLEWARE; behavior not read |
| Redis broker unauthenticated in dev | Low-dev / HIGH-prod | Settings comment explicitly warns: "MUST be overridden via CELERY_BROKER_URL env var in non-dev environments" |
| DB password must not be empty | Low-dev / HIGH-prod | Settings raise if DJANGO_SECRET_KEY empty; DB_PASSWORD could be None |
