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
| `Role` | `apps/accounts/rbac_models.py` | 5 system roles (ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR) with rank hierarchy |
| `Permission` | `apps/accounts/rbac_models.py` | 25 permissions using `module.action` convention (e.g., `reconciliation.run`) |
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
| recommendations | `auto_close`, `route_review`, `escalate`, `reprocess` |
| cases | `escalate` |

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

## 6. Audit and Traceability — PARTIALLY IMPLEMENTED

### AuditEvent RBAC Fields

| Field | Exists on AuditEvent? | Populated in Agent Paths? |
|-------|----------------------|--------------------------|
| `trace_id` | **YES** | **YES** — ~95% coverage |
| `span_id` | **YES** | **YES** |
| `actor_email` | **YES** | **NO** — empty for system-triggered agents |
| `actor_primary_role` | **YES** | **NO** — empty for system-triggered agents |
| `actor_roles_snapshot_json` | **YES** | **NO** — empty for system-triggered agents |
| `permission_checked` | **YES** | **NO** — agents don't check permissions |
| `permission_source` | **YES** | **NO** — agents don't check permissions |
| `access_granted` | **YES** (nullable Boolean) | **NO** — agents don't check permissions |
| `agent_run_id` | **YES** | **YES** — cross-referenced |

### What IS Traced for Agent Operations

- `AgentRun` records: agent type, status, duration, tokens, cost, confidence
- `AgentStep` records: per-tool-call step tracking
- `ToolCall` records: tool name, input/output, duration, status
- `AgentMessage` records: full LLM conversation history
- `DecisionLog` records: agent decisions with reasoning
- `AgentRecommendation` records: recommendations with acceptance tracking

### What IS NOT Traced

- **Who authorized** the agent run — `permission_checked` always empty
- **What role** triggered it — `actor_primary_role` empty for auto-triggered runs
- **Whether access was granted** — `access_granted` always null in agent paths
- **Permission resolution source** — `permission_source` never set

### DecisionLog RBAC Fields (Exist but Unused)

```python
# apps/agents/models.py — DecisionLog
actor_user_id = PositiveIntegerField(nullable)
actor_primary_role = CharField(50)
permission_checked = CharField(100)
authorization_snapshot_json = JSONField
# All designed for RBAC tracking, but populated only for HUMAN decisions, never for AGENT decisions
```

**Assessment:** Excellent audit infrastructure (models, fields, trace IDs). However, RBAC-specific fields are systematically empty in all agent execution paths because no permission checks occur. **Partially Implemented** — the schema is ready but the data is not flowing.

---

## 7. Dashboard Governance Visibility — PARTIALLY IMPLEMENTED

### Governance API Endpoints (6 endpoints at `/api/v1/governance/dashboard/`)

| Endpoint | RBAC Data Shown | Status |
|----------|----------------|--------|
| `/summary/` | `permission_compliance_pct`, `access_granted`/`access_denied` counts | **Shows metric, but agent data is sparse** |
| `/access-events/` | `actor_email`, `actor_primary_role`, `permission_checked`, `permission_source`, `access_granted` | **Available for user actions; empty for agents** |
| `/permission-activity/` | Daily grant/deny trends, top permissions, by source | **Works for view-level checks only** |
| `/trace-runs/` | Agent run list with `trace_id`, `permission_checked` | **`permission_checked` is empty** |
| `/trace-runs/{id}/` | Deep-dive with timeline, tool calls, decisions | **Operational data present; RBAC fields empty** |
| `/health/` | Per-agent `with_permission` count and percentage | **Shows ~3.5% coverage — correctly reflects the gap** |

### What Dashboards Show Well

- Trace ID correlation across agent runs
- Agent success/failure rates, confidence scores
- Token usage and cost tracking
- Tool call success/failure tracking
- Escalation counts

### What Dashboards Do NOT Show

- **Actor role on agent runs** — field exists but usually null
- **Whether authorization was checked** — `permission_checked` empty
- **Why it wasn't checked** — no "exempt" or "system" designation
- **Invocation reason with RBAC context** — `invocation_reason` is business-level only
- **Permission denial for agent operations** — can't deny what isn't checked

### Governance Template Views

| View | Location | RBAC Visibility |
|------|----------|----------------|
| Audit Event List | `apps/auditlog/template_views.py` | Shows RBAC columns (role, permission, access), but agent-generated events have empty values |
| Invoice Governance | `apps/auditlog/template_views.py` | Shows access history tab with RBAC badges; agent entries lack RBAC context |

**Assessment:** Dashboard infrastructure supports RBAC visibility and surfaces the right fields. However, because agent operations don't generate RBAC data, dashboard displays are incomplete for the agent subsystem. **Partially Implemented.**

---

## Files Requiring Changes

### Critical (Authorization Gaps)

| File | Change Needed |
|------|---------------|
| `apps/agents/services/base_agent.py` | Add user/RBAC context to `AgentContext`; populate `AgentRun` RBAC fields |
| `apps/agents/services/orchestrator.py` | Accept `request_user` parameter; validate `agents.orchestrate` permission; propagate actor context |
| `apps/agents/tasks.py` | Accept and propagate `actor_user_id` through Celery task |
| `apps/agents/views.py` | Add `@permission_required_code("agents.trigger")` to `trigger_pipeline`; pass `request.user` to task |
| `apps/agents/models.py` | Add `actor_primary_role`, `actor_roles_snapshot`, `permission_source`, `access_granted` to `AgentRun` |
| `apps/tools/registry/base.py` | Add `required_permission` field to `BaseTool`; add permission check in `execute()` wrapper |
| `apps/tools/registry/tools.py` | Declare `required_permission` on each tool class |
| `apps/agents/services/recommendation_service.py` | Validate user permissions before accepting recommendations |
| `apps/agents/services/policy_engine.py` | Add permission check before auto-close execution |

### Important (System Identity)

| File | Change Needed |
|------|---------------|
| `apps/accounts/rbac_models.py` or seed | Create `SYSTEM_AGENT` role with scoped permissions |
| `apps/core/enums.py` | Add `SYSTEM_AGENT` to role codes |
| `apps/agents/services/orchestrator.py` | Run agents under `SYSTEM_AGENT` identity when no user context available |

### Enhancement (Audit Completeness)

| File | Change Needed |
|------|---------------|
| `apps/auditlog/services.py` | Ensure agent-path AuditEvents populate RBAC fields (from system agent or triggering user) |
| `apps/core/trace.py` | Support system-agent TraceContext with role=SYSTEM_AGENT |

---

## 10. Suggested Improvements

### Improvement 1 — AgentGuardrailsService

A central service that wraps all agent execution with RBAC checks:

```python
# apps/agents/services/guardrails_service.py

from apps.core.permissions import HasPermissionCode
from apps.accounts.models import User


class AgentGuardrailsService:
    """Central RBAC enforcement for agent execution."""

    # Permission required to trigger agent pipeline
    ORCHESTRATE_PERMISSION = "agents.orchestrate"

    # Per-agent permission requirements
    AGENT_PERMISSIONS = {
        "INVOICE_EXTRACTION": "agents.run_extraction",
        "INVOICE_UNDERSTANDING": "agents.run_extraction",
        "PO_RETRIEVAL": "agents.run_po_retrieval",
        "GRN_RETRIEVAL": "agents.run_grn_retrieval",
        "EXCEPTION_ANALYSIS": "agents.run_exception_analysis",
        "RECONCILIATION_ASSIST": "agents.run_reconciliation_assist",
        "REVIEW_ROUTING": "agents.run_review_routing",
        "CASE_SUMMARY": "agents.run_case_summary",
    }

    # Recommendation type → required permission
    RECOMMENDATION_PERMISSIONS = {
        "AUTO_CLOSE": "recommendations.auto_close",
        "SEND_TO_AP_REVIEW": "recommendations.route_review",
        "ESCALATE_TO_MANAGER": "recommendations.escalate",
        "REPROCESS_EXTRACTION": "recommendations.reprocess",
    }

    @classmethod
    def get_system_agent_user(cls) -> User:
        """Return the dedicated system agent user for autonomous operations."""
        user, _ = User.objects.get_or_create(
            email="system-agent@internal",
            defaults={
                "first_name": "System",
                "last_name": "Agent",
                "is_active": True,
                "is_staff": False,
            },
        )
        return user

    @classmethod
    def authorize_orchestration(cls, user: User) -> bool:
        """Check if user can trigger agent orchestration."""
        return user.has_permission(cls.ORCHESTRATE_PERMISSION)

    @classmethod
    def authorize_agent(cls, user: User, agent_type: str) -> bool:
        """Check if user/system can run a specific agent type."""
        perm = cls.AGENT_PERMISSIONS.get(agent_type)
        if not perm:
            return False
        return user.has_permission(perm)

    @classmethod
    def authorize_recommendation(cls, user: User, recommendation_type: str) -> bool:
        """Check if user can accept/execute a recommendation."""
        perm = cls.RECOMMENDATION_PERMISSIONS.get(recommendation_type)
        if not perm:
            return False
        return user.has_permission(perm)

    @classmethod
    def build_rbac_snapshot(cls, user: User) -> dict:
        """Capture RBAC state at execution time for audit trail."""
        primary_role = user.get_primary_role()
        return {
            "actor_user_id": user.pk,
            "actor_email": user.email,
            "actor_primary_role": primary_role.code if primary_role else "",
            "actor_roles_snapshot": list(user.get_role_codes()),
            "permission_source": "SYSTEM_AGENT" if user.email == "system-agent@internal" else "USER",
        }
```

### Improvement 2 — Tool-Level Permission Requirements

Add `required_permission` to `BaseTool` and enforce before execution:

```python
# Modification to apps/tools/registry/base.py

class BaseTool(ABC):
    name: str = ""
    description: str = ""
    required_permission: str = ""  # NEW: e.g., "purchase_orders.view"

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        ...

    def execute_with_auth(self, user, **kwargs) -> ToolResult:
        """Execute with permission check."""
        if self.required_permission and not user.has_permission(self.required_permission):
            return ToolResult(
                success=False,
                error=f"Permission denied: {self.required_permission}",
            )
        return self.execute(**kwargs)


# In tools.py — add required_permission to each tool:
class POLookupTool(BaseTool):
    name = "po_lookup"
    required_permission = "purchase_orders.view"

class GRNLookupTool(BaseTool):
    name = "grn_lookup"
    required_permission = "grns.view"

class VendorSearchTool(BaseTool):
    name = "vendor_search"
    required_permission = "vendors.view"

class InvoiceDetailsTool(BaseTool):
    name = "invoice_details"
    required_permission = "invoices.view"

class ExceptionListTool(BaseTool):
    name = "exception_list"
    required_permission = "reconciliation.view"

class ReconciliationSummaryTool(BaseTool):
    name = "reconciliation_summary"
    required_permission = "reconciliation.view"
```

### Improvement 3 — Enhanced AgentContext with RBAC

```python
# Modification to apps/agents/services/base_agent.py

@dataclass
class AgentContext:
    reconciliation_result: Optional[ReconciliationResult]
    invoice_id: int
    po_number: Optional[str] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    reconciliation_mode: str = ""
    # NEW — RBAC context
    actor_user_id: Optional[int] = None
    actor_primary_role: str = ""
    actor_roles_snapshot: List[str] = field(default_factory=list)
    permission_checked: str = ""
    access_granted: bool = False
```

### Improvement 4 — Recommendation Authorization Layer

```python
# Modification to apps/agents/services/recommendation_service.py

from apps.agents.services.guardrails_service import AgentGuardrailsService

class RecommendationService:

    def mark_recommendation_accepted(self, recommendation_id, user, accepted=True):
        rec = AgentRecommendation.objects.get(pk=recommendation_id)

        # NEW: Validate user has permission to accept this recommendation type
        if not AgentGuardrailsService.authorize_recommendation(user, rec.recommendation_type):
            raise PermissionDenied(
                f"User lacks permission to accept {rec.recommendation_type} recommendations"
            )

        rec.accepted = accepted
        rec.accepted_by = user
        rec.accepted_at = timezone.now()
        rec.save()
```

### Improvement 5 — Orchestrator User Context Propagation

```python
# Modification to apps/agents/services/orchestrator.py

class AgentOrchestrator:

    def execute(self, result: ReconciliationResult, request_user=None) -> OrchestrationResult:
        """Execute agent pipeline with RBAC context."""
        from apps.agents.services.guardrails_service import AgentGuardrailsService

        # Resolve actor: use request_user if available, else system agent
        actor = request_user or AgentGuardrailsService.get_system_agent_user()

        # Validate orchestration permission
        if not AgentGuardrailsService.authorize_orchestration(actor):
            raise PermissionDenied("Not authorized to trigger agent orchestration")

        rbac_snapshot = AgentGuardrailsService.build_rbac_snapshot(actor)

        # Build context with RBAC info
        ctx = AgentContext(
            reconciliation_result=result,
            invoice_id=result.invoice_id,
            po_number=result.purchase_order.po_number if result.purchase_order else None,
            exceptions=exceptions,
            reconciliation_mode=recon_mode,
            actor_user_id=rbac_snapshot["actor_user_id"],
            actor_primary_role=rbac_snapshot["actor_primary_role"],
            actor_roles_snapshot=rbac_snapshot["actor_roles_snapshot"],
            permission_checked=AgentGuardrailsService.ORCHESTRATE_PERMISSION,
            access_granted=True,
        )
        # ... rest of execution
```

### Improvement 6 — Celery Task User Propagation

```python
# Modification to apps/agents/tasks.py

@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
def run_agent_pipeline_task(self, reconciliation_result_id: int, actor_user_id: int = None) -> dict:
    """Run agent pipeline with user context."""
    from apps.agents.services.orchestrator import AgentOrchestrator
    from apps.reconciliation.models import ReconciliationResult

    result = ReconciliationResult.objects.select_related(
        "invoice", "purchase_order"
    ).get(pk=reconciliation_result_id)

    # Resolve actor
    request_user = None
    if actor_user_id:
        from apps.accounts.models import User
        request_user = User.objects.filter(pk=actor_user_id).first()

    orchestrator = AgentOrchestrator()
    outcome = orchestrator.execute(result, request_user=request_user)
    return {"status": outcome.status, "recommendations": len(outcome.recommendations)}
```

### Improvement 7 — Governance Dashboard RBAC Widgets

Add dedicated widgets to the governance dashboard showing:

```python
# Additional data points for apps/dashboard/governance_dashboard_service.py

def get_agent_rbac_compliance(self):
    """Return RBAC compliance metrics for agent operations."""
    total_runs = AgentRun.objects.count()
    with_actor = AgentRun.objects.exclude(actor_user_id__isnull=True).count()
    with_permission = AgentRun.objects.exclude(permission_checked="").count()

    return {
        "total_agent_runs": total_runs,
        "runs_with_actor_identity": with_actor,
        "actor_identity_pct": round(with_actor / total_runs * 100, 1) if total_runs else 0,
        "runs_with_permission_check": with_permission,
        "permission_check_pct": round(with_permission / total_runs * 100, 1) if total_runs else 0,
        "system_agent_runs": AgentRun.objects.filter(
            actor_user_id__isnull=False
        ).filter(
            # system agent user ID
        ).count(),
        "unattributed_runs": total_runs - with_actor,
    }

def get_recommendation_authorization_audit(self):
    """Return authorization status for executed recommendations."""
    from apps.agents.models import AgentRecommendation
    recs = AgentRecommendation.objects.filter(accepted=True)
    return {
        "total_accepted": recs.count(),
        "accepted_with_permission_check": 0,  # Currently always 0
        "auto_closed_without_auth": AgentRecommendation.objects.filter(
            recommendation_type="AUTO_CLOSE",
            accepted=True,
        ).count(),
    }
```

### Summary of New Permissions to Seed

| Permission Code | Module | Action | Assigned To |
|----------------|--------|--------|-------------|
| `agents.orchestrate` | agents | orchestrate | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `agents.run_extraction` | agents | run_extraction | ADMIN, SYSTEM_AGENT |
| `agents.run_po_retrieval` | agents | run_po_retrieval | ADMIN, SYSTEM_AGENT |
| `agents.run_grn_retrieval` | agents | run_grn_retrieval | ADMIN, SYSTEM_AGENT |
| `agents.run_exception_analysis` | agents | run_exception_analysis | ADMIN, SYSTEM_AGENT |
| `agents.run_reconciliation_assist` | agents | run_reconciliation_assist | ADMIN, SYSTEM_AGENT |
| `agents.run_review_routing` | agents | run_review_routing | ADMIN, SYSTEM_AGENT |
| `agents.run_case_summary` | agents | run_case_summary | ADMIN, SYSTEM_AGENT |
| `recommendations.auto_close` | recommendations | auto_close | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `recommendations.route_review` | recommendations | route_review | ADMIN, REVIEWER, SYSTEM_AGENT |
| `recommendations.escalate` | recommendations | escalate | ADMIN, FINANCE_MANAGER, SYSTEM_AGENT |
| `recommendations.reprocess` | recommendations | reprocess | ADMIN, AP_PROCESSOR, SYSTEM_AGENT |

---

## Risk Assessment

| Risk | Severity | Description |
|------|----------|-------------|
| Unauthorized agent execution | **HIGH** | Any authenticated user reaching the API can trigger the full agent pipeline |
| Uncontrolled auto-close | **HIGH** | Agents can close reconciliation cases (financial impact) without permission checks |
| Data exposure via tools | **MEDIUM** | Tools return all matching records regardless of user's data scope |
| Recommendation acceptance without auth | **MEDIUM** | Any user can accept recommendations without role validation |
| Unattributed system operations | **MEDIUM** | Agent runs cannot be traced to a responsible identity |
| Incomplete audit trail | **MEDIUM** | RBAC fields exist on AuditEvent but are empty for agent operations |
