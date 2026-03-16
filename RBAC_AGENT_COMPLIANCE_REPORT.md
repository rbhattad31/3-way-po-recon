# RBAC Compliance Report — AI Agent Architecture

**Project:** 3-Way PO Reconciliation Platform  
**Date:** 2026-03-14 (initial audit) | Updated: 2026-03-14 (post-implementation)  
**Scope:** RBAC enforcement across agent execution, tools, recommendations, audit, and governance dashboards

---

## Executive Summary

The platform has a **mature, well-architected RBAC infrastructure** with **full agent subsystem coverage**. The `AgentGuardrailsService` provides central RBAC enforcement for all agent operations — orchestration, per-agent authorization, per-tool authorization, recommendation acceptance, and post-policy actions (auto-close, escalation). A dedicated `SYSTEM_AGENT` role and service account (`system-agent@internal`) handle autonomous operations. All guardrail decisions are audited and `AgentRun` records carry complete RBAC snapshots.

| Section | Verdict |
|---------|---------|
| 1. RBAC Infrastructure | **COMPLIANT** |
| 2. Agent Execution Context | **IMPLEMENTED** |
| 3. Tool Permission Enforcement | **IMPLEMENTED** |
| 4. Recommendation Authorization | **IMPLEMENTED** |
| 5. System Agent Permissions | **IMPLEMENTED** |
| 6. Audit and Traceability | **IMPLEMENTED** |
| 7. Dashboard Governance Visibility | **IMPLEMENTED** |

---

## 1. RBAC Infrastructure — COMPLIANT

### Models

| Model | File | Purpose |
|-------|------|---------|
| `Role` | `apps/accounts/rbac_models.py` | 6 system roles (ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR, SYSTEM_AGENT) with rank hierarchy |
| `Permission` | `apps/accounts/rbac_models.py` | 40 permissions using `module.action` convention (e.g., `reconciliation.run`) |
| `RolePermission` | `apps/accounts/rbac_models.py` | Role → Permission mapping (many-to-many, `is_allowed` flag) |
| `UserRole` | `apps/accounts/rbac_models.py` | User → Role assignment with `is_primary`, `expires_at`, `assigned_by` |
| `UserPermissionOverride` | `apps/accounts/rbac_models.py` | Per-user ALLOW/DENY overrides with expiry |

### Permission Resolution

**Location:** `apps/accounts/models.py` — `User.get_effective_permissions()`

**3-Stage Precedence:**
1. **ADMIN bypass** → always granted
2. **User overrides** → DENY removes, ALLOW adds
3. **Role-level** → union of all active role permissions
4. **Default** → denied

**Caching:** `RBACMiddleware` (`apps/core/middleware.py`) pre-warms `_cached_permissions` and `_cached_role_codes` per request.

### Enforcement Mechanisms

| Mechanism | Location | Example |
|-----------|----------|---------|
| DRF Permission Classes | `apps/core/permissions.py` | `HasPermissionCode("invoices.view")`, `HasAnyPermission`, `HasRole` |
| CBV Mixins | `apps/core/permissions.py` | `PermissionRequiredMixin`, `AnyPermissionRequiredMixin`, `RoleRequiredMixin` |
| FBV Decorators | `apps/core/permissions.py` | `@permission_required_code("reconciliation.run")`, `@role_required("ADMIN")` |
| Template Tags | `apps/core/templatetags/rbac_tags.py` | `{% has_permission "invoices.view" %}`, `{% if_can "reconciliation.run" %}` |
| Context Processor | `apps/core/context_processors.py` | Injects `user_permissions`, `user_role_codes`, `is_admin` into every template |

**Assessment:** Full-featured enterprise RBAC with caching, override support, expiry, and multi-layer enforcement. **Compliant.**

---

## 2. Agent Execution Context — IMPLEMENTED

### Agent Classes

| Agent Class | File | Responsibilities |
|-------------|------|-----------------|
| `InvoiceExtractionAgent` | `apps/agents/services/agent_classes.py` | Extract invoice data via OCR + LLM |
| `InvoiceUnderstandingAgent` | `apps/agents/services/agent_classes.py` | Enhance low-confidence extractions |
| `PORetrievalAgent` | `apps/agents/services/agent_classes.py` | Recover missing purchase orders |
| `GRNRetrievalAgent` | `apps/agents/services/agent_classes.py` | Recover missing goods receipt notes |
| `ReconciliationAssistAgent` | `apps/agents/services/agent_classes.py` | Assist with complex reconciliation |
| `ExceptionAnalysisAgent` | `apps/agents/services/agent_classes.py` | Analyze reconciliation exceptions |
| `ReviewRoutingAgent` | `apps/agents/services/agent_classes.py` | Route cases to appropriate reviewers |
| `CaseSummaryAgent` | `apps/agents/services/agent_classes.py` | Generate case summaries |

### RBAC Context in AgentRun

| Field | Exists on AgentRun? | Populated During Execution? |
|-------|---------------------|-----------------------------|
| `actor_user_id` | **YES** (nullable) | **YES** — resolved via `AgentGuardrailsService.resolve_actor()` |
| `actor_primary_role` | **YES** | **YES** — from `build_rbac_snapshot()` |
| `actor_roles_snapshot_json` | **YES** | **YES** — JSON of all active roles |
| `permission_checked` | **YES** (CharField) | **YES** — records which permission was checked |
| `permission_source` | **YES** | **YES** — `USER` or `SYSTEM_AGENT` |
| `access_granted` | **YES** | **YES** — boolean result of auth check |

### AgentContext Dataclass

```python
# apps/agents/services/base_agent.py
@dataclass
class AgentContext:
    reconciliation_result: Optional[ReconciliationResult]
    invoice_id: int
    po_number: Optional[str] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    reconciliation_mode: str = ""
    # RBAC fields (populated by orchestrator from guardrails service)
    actor_user_id: Optional[int] = None
    actor_primary_role: str = ""
    actor_roles_snapshot: List[str] = field(default_factory=list)
    permission_checked: str = ""
    permission_source: str = ""
    access_granted: Optional[bool] = None
    trace_id: str = ""
    span_id: str = ""
```

### Entry Points

| Entry Point | Permission Check | User Context Propagated |
|-------------|-----------------|------------------------|
| `start_reconciliation` view | **YES** — `@permission_required_code("reconciliation.run")` | **YES** — `triggered_by=request.user` passed to task |
| `AgentRunViewSet.trigger_pipeline` API | **YES** — `HasPermissionCode("agents.orchestrate")` | **YES** — `request.user.pk` passed to task |
| `run_agent_pipeline_task` Celery task | **YES** — resolves actor via guardrails | **YES** — `actor_user_id` parameter propagated |
| `AgentOrchestrator.execute()` | **YES** — `authorize_orchestration(actor)` | **YES** — `request_user` parameter, full RBAC snapshot |
| `BaseAgent.run()` | **YES** — per-agent auth checked before call | **YES** — full `AgentContext` with RBAC fields |

**Assessment:** Full RBAC context propagation from view layer through Celery task to orchestrator to individual agent runs. Actor resolution falls back to SYSTEM_AGENT for autonomous operations. **Implemented.**

---

## 3. Tool Permission Enforcement — IMPLEMENTED

### Tool Inventory

| Tool | Class | File | DB Access | Permission Check |
|------|-------|------|-----------|-----------------|
| `po_lookup` | `POLookupTool` | `apps/tools/registry/tools.py` | `PurchaseOrder.objects.filter(po_number=...)` | **YES** — `purchase_orders.view` |
| `grn_lookup` | `GRNLookupTool` | `apps/tools/registry/tools.py` | `GoodsReceiptNote.objects.filter(purchase_order=po)` | **YES** — `grns.view` |
| `vendor_search` | `VendorSearchTool` | `apps/tools/registry/tools.py` | `Vendor.objects.filter(is_active=True)` | **YES** — `vendors.view` |
| `invoice_details` | `InvoiceDetailsTool` | `apps/tools/registry/tools.py` | `Invoice.objects.get(pk=...)` | **YES** — `invoices.view` |
| `exception_list` | `ExceptionListTool` | `apps/tools/registry/tools.py` | `ReconciliationException.objects.filter(result_id=...)` | **YES** — `reconciliation.view` |
| `reconciliation_summary` | `ReconciliationSummaryTool` | `apps/tools/registry/tools.py` | `ReconciliationResult.objects.get(pk=...)` | **YES** — `reconciliation.view` |

### Execution Flow

```
BaseAgent._execute_tool()
  → AgentGuardrailsService.authorize_tool(actor, tool)  ← Permission check
  → If denied: log TOOL_CALL_DENIED, return error → skip execution
  → If granted: log TOOL_CALL_AUTHORIZED
  → tool.execute(**arguments)
  → ToolCallLogger.log(...)                              ← Audit trail
```

### Implementation Details

- Each tool declares `required_permission` as a class attribute on `BaseTool`
- `BaseAgent._execute_tool()` resolves the actor via `_resolve_actor()` (from AgentContext)
- `AgentGuardrailsService.authorize_tool()` checks `user.has_permission(tool.required_permission)`
- Denied tool calls are logged via `log_guardrail_decision()` with `TOOL_CALL_DENIED` event type
- Granted tool calls are logged via `log_guardrail_decision()` with `TOOL_CALL_AUTHORIZED` event type

**Assessment:** All 6 tools declare required permissions and every tool call is authorized before execution. **Implemented.**

---

## 4. Recommendation Authorization — IMPLEMENTED

### Recommendation Types

| Type | Triggered By | Action Taken | Permission Check |
|------|-------------|-------------|-----------------|
| `AUTO_CLOSE` | PolicyEngine auto-close band | Sets `match_status=MATCHED`, `requires_review=False` | **YES** — `recommendations.auto_close` via `authorize_action()` |
| `SEND_TO_AP_REVIEW` | Review routing agent | Creates `ReviewAssignment` | **YES** — `recommendations.route_review` |
| `ESCALATE_TO_MANAGER` | Escalation logic | Creates `AgentEscalation` | **YES** — `cases.escalate` via `authorize_action()` |
| `REPROCESS_EXTRACTION` | Exception analysis agent | Re-triggers extraction | **YES** — `recommendations.reprocess` |

### Auto-Close Path (Now Protected)

```python
# apps/agents/services/orchestrator.py — _apply_post_policies()
if self.policy.should_auto_close(final_rec, confidence):
    granted = AgentGuardrailsService.authorize_action(
        self._actor, "auto_close_result"
    )
    if granted:
        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save()
        # ✅ AUTO_CLOSE_AUTHORIZED audit event logged
    else:
        # ✅ AUTO_CLOSE_DENIED audit event logged — auto-close skipped
```

### Recommendation Acceptance (Now Protected)

```python
# apps/agents/services/recommendation_service.py
def mark_recommendation_accepted(self, recommendation_id, user, accepted=True):
    rec = AgentRecommendation.objects.get(pk=recommendation_id)
    granted = AgentGuardrailsService.authorize_recommendation(
        user, rec.recommendation_type
    )
    if not granted:
        # ✅ RECOMMENDATION_DENIED audit event logged
        raise PermissionDenied("Insufficient permissions")
    # ✅ RECOMMENDATION_ACCEPTED audit event logged
    rec.accepted = accepted
    rec.accepted_by = user
    rec.accepted_at = timezone.now()
    rec.save()
```

**Assessment:** All recommendations are now authorized before execution. Auto-close requires `recommendations.auto_close`, escalation requires `cases.escalate`, and recommendation acceptance validates the user's permission for that recommendation type. **Implemented.**

---

## 5. System Agent Permissions — IMPLEMENTED

### Implementation

| Aspect | Status |
|--------|--------|
| Dedicated system user (`SYSTEM_AGENT`) | **Created** — `system-agent@internal` via `resolve_actor()` |
| Service account role with scoped permissions | **Created** — `SYSTEM_AGENT` role (rank 100, `is_system_role=True`) |
| Seeded via `seed_rbac` command | **YES** — role, permissions, and matrix |
| Agent-to-role mapping via `AGENT_PERMISSIONS` | **YES** — 8 per-agent permissions mapped |
| Per-tool permission enforcement | **YES** — 6 tools with `required_permission` |
| Principle of least privilege | **YES** — SYSTEM_AGENT has only agent/tool/recommendation permissions, not admin capabilities |

### SYSTEM_AGENT Role Permissions

| Module | Permissions |
|--------|------------|
| agents | `orchestrate`, `run_extraction`, `run_po_retrieval`, `run_grn_retrieval`, `run_exception_analysis`, `run_reconciliation_assist`, `run_review_routing`, `run_case_summary` |
| purchase_orders | `view` |
| grns | `view` |
| vendors | `view` |
| invoices | `view` |
| reconciliation | `view` |
| recommendations | `auto_close`, `route_review`, `escalate`, `reprocess`, `route_procurement`, `vendor_clarification` |
| cases | `escalate` |
| extraction | `reprocess` |
| reviews | `assign` |

### Actor Resolution Flow

```python
# AgentGuardrailsService.resolve_actor(request_user)
if request_user and request_user.is_authenticated:
    return request_user                    # Human user path
else:
    return get_system_agent_user()         # Autonomous path → system-agent@internal
```

**Assessment:** Full system agent identity with scoped permissions, seeded via management command. Autonomous operations run under `SYSTEM_AGENT` with principle of least privilege. **Implemented.**

---

## 6. Audit and Traceability — IMPLEMENTED

### AuditEvent RBAC Fields

| Field | Exists on AuditEvent? | Populated in Agent Paths? |
|-------|----------------------|--------------------------|
| `trace_id` | **YES** | **YES** — ~100% coverage |
| `span_id` | **YES** | **YES** |
| `actor_email` | **YES** | **YES** — system-agent@internal or triggering user |
| `actor_primary_role` | **YES** | **YES** — from `build_rbac_snapshot()` |
| `actor_roles_snapshot_json` | **YES** | **YES** — from `build_rbac_snapshot()` |
| `permission_checked` | **YES** | **YES** — populated by guardrail checks |
| `permission_source` | **YES** | **YES** — `USER` or `SYSTEM_AGENT` |
| `access_granted` | **YES** (nullable Boolean) | **YES** — populated by guardrail decisions |
| `agent_run_id` | **YES** | **YES** — cross-referenced |

### What IS Traced for Agent Operations

- `AgentRun` records: agent type, status, duration, tokens, cost, confidence, **RBAC snapshot** (actor_primary_role, actor_roles_snapshot_json, permission_source, access_granted)
- `AgentStep` records: per-tool-call step tracking
- `ToolCall` records: tool name, input/output, duration, status
- `AgentMessage` records: full LLM conversation history
- `DecisionLog` records: agent decisions with reasoning
- `AgentRecommendation` records: recommendations with acceptance tracking
- **Guardrail AuditEvents**: 9 event types for all authorization decisions (grant/deny)

### Guardrail Audit Event Types

| Event Type | Records |
|---|---|
| `GUARDRAIL_GRANTED` | Orchestration, per-agent authorization granted |
| `GUARDRAIL_DENIED` | Orchestration, per-agent authorization denied |
| `TOOL_CALL_AUTHORIZED` | Tool execution permitted |
| `TOOL_CALL_DENIED` | Tool execution blocked |
| `RECOMMENDATION_ACCEPTED` | Recommendation acceptance authorized |
| `RECOMMENDATION_DENIED` | Recommendation acceptance blocked |
| `AUTO_CLOSE_AUTHORIZED` | Auto-close action permitted |
| `AUTO_CLOSE_DENIED` | Auto-close action blocked |
| `SYSTEM_AGENT_USED` | SYSTEM_AGENT identity resolved for run |

**Assessment:** Full RBAC field population in all agent execution paths. All guardrail decisions are audited with 9 dedicated event types. AgentRun records carry complete RBAC snapshots. **Implemented.**

---

## 7. Dashboard Governance Visibility — IMPLEMENTED

### Governance API Endpoints (6 + 4 new guardrail endpoints)

| Endpoint | RBAC Data Shown | Status |
|----------|----------------|--------|
| `/summary/` | `permission_compliance_pct`, `access_granted`/`access_denied` counts | **Full data for both user and agent operations** |
| `/access-events/` | `actor_email`, `actor_primary_role`, `permission_checked`, `permission_source`, `access_granted` | **Full data — including agent guardrail decisions** |
| `/permission-activity/` | Daily grant/deny trends, top permissions, by source | **Works for both view-level and agent-level checks** |
| `/trace-runs/` | Agent run list with `trace_id`, `permission_checked` | **`permission_checked` populated** |
| `/trace-runs/{id}/` | Deep-dive with timeline, tool calls, decisions | **Full RBAC context available** |
| `/health/` | Per-agent `with_permission` count and percentage | **~100% coverage** |

### New Guardrail-Specific Dashboard Methods

| Method | Purpose |
|---|---|
| `get_agent_rbac_compliance()` | RBAC field population metrics across all AgentRun records |
| `get_guardrail_decisions()` | Grant/deny audit breakdown by event type |
| `get_tool_authorization_metrics()` | Tool authorization success/failure rates |
| `get_recommendation_authorization_audit()` | Recommendation acceptance/denial tracking |

### Governance Template Views

| View | Location | RBAC Visibility |
|------|----------|----------------|
| Audit Event List | `apps/auditlog/template_views.py` | Full RBAC columns — role, permission, access granted/denied, including agent guardrail events |
| Invoice Governance | `apps/auditlog/template_views.py` | Full dashboard: audit trail + agent trace + timeline + access history, with RBAC badges on all entries |

**Assessment:** Dashboard infrastructure fully populated with agent RBAC data. Guardrail decisions, tool authorizations, and recommendation authorizations all visible in governance views. **Implemented.**

---

## 8. Implementation Summary

All gaps identified in the original audit have been resolved. The following changes were made:

### New Files Created

| File | Purpose |
|------|---------|
| `apps/agents/services/guardrails_service.py` | Central RBAC enforcement (AgentGuardrailsService) |
| `apps/agents/migrations/0005_add_agentrun_rbac_fields.py` | Migration adding RBAC fields to AgentRun |

### Files Modified

| File | Changes |
|------|---------|
| `apps/agents/models.py` | Added `actor_primary_role`, `actor_roles_snapshot_json`, `permission_source`, `access_granted` to AgentRun |
| `apps/agents/services/base_agent.py` | Extended AgentContext with 8 RBAC fields; added `authorize_tool()` check in `_execute_tool()`; added `_resolve_actor()` helper; populated AgentRun RBAC fields from ctx |
| `apps/agents/services/orchestrator.py` | Added `request_user` parameter to `execute()`; actor resolution via guardrails; orchestration permission check; per-agent authorization; post-policy authorization (auto-close, escalation); RBAC snapshot on all AgentRun records |
| `apps/agents/services/recommendation_service.py` | Added `authorize_recommendation()` check before accept/reject; raises `PermissionDenied` on denial; logs guardrail events |
| `apps/agents/tasks.py` | Added `actor_user_id` parameter; resolves User and passes to orchestrator |
| `apps/agents/views.py` | Added `HasPermissionCode("agents.orchestrate")` to `trigger_pipeline`; passes `request.user.pk` to task |
| `apps/tools/registry/base.py` | Added `required_permission: str = ""` to BaseTool |
| `apps/tools/registry/tools.py` | All 6 tools now declare `required_permission` |
| `apps/core/enums.py` | Added 9 `AuditEventType` values for guardrail events |
| `apps/reconciliation/tasks.py` | Passes `triggered_by.pk` to agent pipeline task |
| `apps/dashboard/governance_dashboard_service.py` | Added 4 guardrail-specific dashboard methods |
| `apps/accounts/management/commands/seed_rbac.py` | Added SYSTEM_AGENT role, 19 new permissions, updated ROLE_MATRIX for all 6 roles |

### Permissions Seeded

| Permission Code | Module | Action | Assigned To |
|----------------|--------|--------|-------------|
| `agents.orchestrate` | agents | orchestrate | ADMIN, FINANCE_MANAGER, AP_PROCESSOR, SYSTEM_AGENT |
| `agents.run_extraction` | agents | run_extraction | ADMIN, SYSTEM_AGENT |
| `agents.run_po_retrieval` | agents | run_po_retrieval | ADMIN, SYSTEM_AGENT |
| `agents.run_grn_retrieval` | agents | run_grn_retrieval | ADMIN, SYSTEM_AGENT |
| `agents.run_exception_analysis` | agents | run_exception_analysis | ADMIN, SYSTEM_AGENT |
| `agents.run_reconciliation_assist` | agents | run_reconciliation_assist | ADMIN, SYSTEM_AGENT |
| `agents.run_review_routing` | agents | run_review_routing | ADMIN, SYSTEM_AGENT |
| `agents.run_case_summary` | agents | run_case_summary | ADMIN, SYSTEM_AGENT |
| `recommendations.auto_close` | recommendations | auto_close | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `recommendations.route_review` | recommendations | route_review | ADMIN, FINANCE_MANAGER, AP_PROCESSOR, SYSTEM_AGENT |
| `recommendations.escalate` | recommendations | escalate | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `recommendations.reprocess` | recommendations | reprocess | ADMIN, AP_PROCESSOR, SYSTEM_AGENT |
| `recommendations.route_procurement` | recommendations | route_procurement | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `recommendations.vendor_clarification` | recommendations | vendor_clarification | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `cases.escalate` | cases | escalate | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `extraction.reprocess` | extraction | reprocess | ADMIN, SYSTEM_AGENT |

### Risk Mitigation

| Original Risk | Severity | Resolution |
|------|----------|-------------|
| Unauthorized agent execution | **HIGH** → **RESOLVED** | `agents.orchestrate` permission required; `HasPermissionCode` on API view |
| Uncontrolled auto-close | **HIGH** → **RESOLVED** | `recommendations.auto_close` checked via `authorize_action()` before status change |
| Data exposure via tools | **MEDIUM** → **RESOLVED** | Each tool declares `required_permission`; `authorize_tool()` checked before every execution |
| Recommendation acceptance without auth | **MEDIUM** → **RESOLVED** | `authorize_recommendation()` checked before accept/reject; `PermissionDenied` raised on failure |
| Unattributed system operations | **MEDIUM** → **RESOLVED** | SYSTEM_AGENT service account with scoped permissions; `resolve_actor()` always provides identity |
| Incomplete audit trail | **MEDIUM** → **RESOLVED** | 9 guardrail event types; AgentRun RBAC fields populated on every run |

---

*This report was originally generated from codebase analysis. Updated after RBAC guardrails implementation to reflect current compliance status.*
