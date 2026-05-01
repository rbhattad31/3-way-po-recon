---
description: "Use when writing or modifying Python files in this Django project. Enforces BaseModel inheritance, soft delete, enum placement in apps/core/enums.py, ASCII-only code, service-layer pattern, and multi-tenant FK conventions."
applyTo: "apps/**/*.py"
---
# Django Python Conventions

## Model Rules
- Inherit from `apps.core.models.BaseModel` (includes `TimestampMixin` + `AuditMixin`) for all business models
- Lightweight join/log tables use `TimestampMixin` only
- Every business model MUST have a `tenant` FK to `CompanyProfile`
- Soft-delete via `SoftDeleteMixin` — NEVER hard-delete business entities
- All enums go in `apps/core/enums.py` — NEVER inline string choices on model fields
- Exception: ERP connector enums live in `apps/erp_integration/enums.py`
- Constants in `apps/core/constants.py`; shared utilities in `apps/core/utils.py`

## ASCII-Only Rule
NEVER use Unicode arrows (->  ok, -> not ok), fancy quotes, em/en dashes, ellipsis characters, or any non-ASCII in:
- Python source code
- String literals
- Comments
- Docstrings
- LLM-generated text persisted to DB

Use plain ASCII: `->` for arrows, `--` for dashes, `...` for ellipsis, straight quotes.

## Service-Layer Pattern
- Business logic goes ONLY in `apps/<app>/services/` — never in views, serializers, or tasks
- Services are stateless — accept model instances or IDs, return results
- Views/tasks call services; services call the ORM
- Views are thin: request parsing + permission check + response formatting only

## Tenant Isolation
- `TenantMiddleware` sets `request.tenant` from `user.company`
- All ViewSets/CBVs use `TenantQuerysetMixin`
- FBVs use `require_tenant()` decorator
- Services use `scoped_queryset()` from `apps.core.tenant_utils`
- Celery tasks accept `tenant_id` argument

## Observability Decorators
- Service entry points: `@observed_service` from `apps.core.decorators`
- View entry points (FBV): `@observed_action`
- Celery tasks: `@observed_task`

## Agent Output Safety
Apply `_sanitise_text()` before any `.save()` on:
- `AgentRun.summarized_reasoning`
- `ReconciliationResult.summary`
- `ReviewAssignment.reviewer_summary`
- `DecisionLog.rationale`
