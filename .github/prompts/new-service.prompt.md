---
mode: agent
description: "Add a new service class following the stateless service-layer pattern"
---

# Add a New Service

## Step 0 -- Read Existing Architecture First

Before writing any code, read these files to understand the service-layer pattern:

### Documentation
- `docs/current_system_review/02_Django_App_Landscape.md` -- app boundaries, service directory layout
- `docs/current_system_review/03_Configuration_and_Environment.md` -- settings patterns, feature flags
- `docs/current_system_review/08_Audit_and_Traceability.md` -- `@observed_service` decorator, ProcessingLog, AuditEvent
- `docs/LANGFUSE_INTEGRATION.md` -- Langfuse span/score patterns for services

### Source Files (study the patterns)
- `apps/reconciliation/services/runner_service.py` -- canonical multi-step service with Langfuse spans, audit events, error handling
- `apps/extraction/services/approval_service.py` -- service with status transitions, field corrections, analytics
- `apps/posting/services/posting_orchestrator.py` -- orchestrator that delegates to sub-services
- `apps/core/decorators.py` -- `@observed_service` decorator implementation
- `apps/core/tenant_utils.py` -- `scoped_queryset()` for tenant-safe ORM queries

### Comprehension Check
Before proceeding, confirm you understand:
1. Services are stateless: no instance variables holding request context
2. Services accept model instances or IDs, never request objects
3. `@observed_service` creates a child span, measures duration, writes `ProcessingLog`
4. Tenant scoping uses `scoped_queryset(Model, tenant)` or `Model.objects.filter(tenant=tenant, is_active=True)`
5. Celery tasks call services; views call services; services call the ORM. Never the reverse.

---

## Inputs

- **App name**: which `apps/<app>/services/` directory
- **Service name**: e.g. `MyFeatureService` (PascalCase, ends with `Service`)
- **Purpose**: what business operation this service encapsulates
- **Is it an orchestrator?** (delegates to multiple sub-services)
- **Needs Langfuse tracing?** (yes for pipeline-stage services, no for simple CRUD wrappers)

---

## Steps

### 1. Create Service File

Create `apps/<app>/services/<service_name>.py`:

```python
import logging
from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)


class MyFeatureService:
    """One-sentence description of what this service does."""

    @classmethod
    @observed_service(name="my_feature.operation_name")
    def do_something(cls, instance_or_id, tenant, **kwargs):
        """Public entry point. Type hints on all public methods."""
        # 1. Load/validate inputs
        # 2. Business logic (call ORM, delegate to helpers)
        # 3. Persist results
        # 4. Emit audit event if state changed
        # 5. Return result
        pass
```

### 2. Tenant Scoping

Every ORM query that touches business data must be tenant-scoped:

```python
from apps.core.tenant_utils import scoped_queryset

qs = scoped_queryset(MyModel, tenant).filter(status=MyEnum.ACTIVE)
```

Platform admin callers pass `tenant=None` -- `scoped_queryset` returns unfiltered in that case.

### 3. Audit Events

For state-changing operations, emit an `AuditEvent`:

```python
from apps.auditlog.models import AuditEvent
from apps.core.enums import AuditEventType

AuditEvent.objects.create(
    event_type=AuditEventType.RELEVANT_TYPE,
    actor=user,
    tenant=tenant,
    description="What happened in plain ASCII",
    invoice_id=invoice.pk if invoice else None,
    status_before=old_status,
    status_after=new_status,
)
```

### 4. Langfuse Tracing (for pipeline services)

If the service is a stage in a pipeline (extraction, reconciliation, posting, agent):

```python
from apps.core.langfuse_client import start_span, end_span, score_trace

lf_span = None
try:
    lf_span = start_span(lf_parent_span, name="stage_name", metadata={...}) if lf_parent_span else None
except Exception:
    lf_span = None

try:
    # ... do work ...
    pass
finally:
    try:
        if lf_span:
            end_span(lf_span, output={...}, level="ERROR" if failed else "DEFAULT")
    except Exception:
        pass
```

### 5. Error Handling

- Catch specific exceptions, not bare `except Exception`
- Log errors with `logger.error(...)` including relevant IDs
- For pipeline services: record failure in `ProcessingLog` (the `@observed_service` decorator handles this)
- Never let Langfuse/tracing errors propagate -- always `try/except/pass`

### 6. Wire to Callers

- **From a Celery task**: import and call in `apps/<app>/tasks.py`
- **From a view**: import and call in `apps/<app>/views.py` or `template_views.py`
- **From another service**: import directly (services can call other services)

### 7. Write Tests

Minimum test cases:
- Happy path: valid inputs produce expected output
- Tenant isolation: service cannot access data from another tenant
- Invalid input: missing required fields raise appropriate error
- State transition: status changes are persisted correctly
- If Langfuse-traced: verify spans do not break when `lf_parent_span=None`

---

## Constraints

- ASCII only in all string literals, log messages, comments
- Never import `request` into a service -- accept `tenant`, `user` as explicit arguments
- Never call a view from a service
- Keep services in the `services/` directory, not in `models.py` or `views.py`
- One service class per file (unless tightly coupled helpers)
