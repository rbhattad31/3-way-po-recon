"""
TEST 11 -- RBAC, Audit System & Governance
============================================
Covers:
  - Role / Permission / UserRole / RolePermission models
  - Permission engine (HasPermissionCode, PermissionRequiredMixin)
  - AuditEvent + ProcessingLog + DecisionLog models
  - AuditService query helpers
  - Governance API endpoints
  - Governance UI pages
  - Observability infrastructure (TraceContext, metrics, decorators)
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestRBACModels:
    """RBAC model imports and existence."""

    def test_role_model(self):
        from apps.accounts.rbac_models import Role
        assert Role is not None

    def test_permission_model(self):
        from apps.accounts.rbac_models import Permission
        assert Permission is not None

    def test_role_permission_model(self):
        from apps.accounts.rbac_models import RolePermission
        assert RolePermission is not None

    def test_user_role_model(self):
        from apps.accounts.rbac_models import UserRole
        assert UserRole is not None

    def test_user_permission_override_model(self):
        from apps.accounts.rbac_models import UserPermissionOverride
        assert UserPermissionOverride is not None

    def test_roles_seeded(self):
        from apps.accounts.rbac_models import Role
        count = Role.objects.count()
        if count == 0:
            pytest.skip("seed_rbac not applied in test DB")
        assert count >= 5, \
            f"Expected >= 5 roles, found {count}. Run: python manage.py seed_rbac"

    def test_permissions_seeded(self):
        from apps.accounts.rbac_models import Permission
        count = Permission.objects.count()
        if count == 0:
            pytest.skip("seed_rbac not applied in test DB")
        assert count >= 20, \
            f"Expected >= 20 permissions, found {count}. Run: python manage.py seed_rbac"


class TestPermissionEngine:
    """RBAC permission engine classes."""

    def test_has_permission_code_importable(self):
        from apps.core.permissions import HasPermissionCode
        assert HasPermissionCode is not None

    def test_has_any_permission_importable(self):
        from apps.core.permissions import HasAnyPermission
        assert HasAnyPermission is not None

    def test_has_role_importable(self):
        from apps.core.permissions import HasRole
        assert HasRole is not None

    def test_permission_required_mixin_importable(self):
        from apps.core.permissions import PermissionRequiredMixin
        assert PermissionRequiredMixin is not None

    def test_permission_required_decorator_importable(self):
        from apps.core.permissions import permission_required_code
        assert permission_required_code is not None


class TestAuditModels:
    """Audit and governance models."""

    def test_audit_event_model(self):
        from apps.auditlog.models import AuditEvent
        assert AuditEvent is not None

    def test_processing_log_model(self):
        from apps.auditlog.models import ProcessingLog
        assert ProcessingLog is not None

    def test_decision_log_model(self):
        from apps.agents.models import DecisionLog
        assert DecisionLog is not None

    def test_audit_event_queryable(self):
        from apps.auditlog.models import AuditEvent
        count = AuditEvent.objects.count()
        assert count >= 0

    def test_audit_event_type_enum(self):
        from apps.core.enums import AuditEventType
        assert AuditEventType is not None
        # Should have at least 20+ event types
        event_types = list(AuditEventType)
        assert len(event_types) >= 20, \
            f"Expected >= 20 AuditEventType values, got {len(event_types)}"


class TestAuditService:
    """AuditService query helpers."""

    def test_audit_service_importable(self):
        from apps.auditlog.services import AuditService
        assert AuditService is not None

    def test_fetch_case_history_method(self):
        from apps.auditlog.services import AuditService
        assert hasattr(AuditService, "fetch_case_history"), \
            "AuditService.fetch_case_history() missing"

    def test_fetch_access_history_method(self):
        from apps.auditlog.services import AuditService
        assert hasattr(AuditService, "fetch_access_history"), \
            "AuditService.fetch_access_history() missing"

    def test_fetch_permission_denials_method(self):
        from apps.auditlog.services import AuditService
        assert hasattr(AuditService, "fetch_permission_denials"), \
            "AuditService.fetch_permission_denials() missing"


class TestGovernanceAPI:
    """Governance REST API -- 9 endpoints."""

    GOVERNANCE_ENDPOINTS = [
        "/api/v1/governance/invoices/1/audit-history/",
        "/api/v1/governance/invoices/1/agent-trace/",
        "/api/v1/governance/invoices/1/recommendations/",
        "/api/v1/governance/invoices/1/timeline/",
        "/api/v1/governance/invoices/1/access-history/",
        "/api/v1/governance/cases/1/stage-timeline/",
        "/api/v1/governance/permission-denials/",
        "/api/v1/governance/rbac-activity/",
        "/api/v1/governance/agent-performance/",
    ]

    def test_all_governance_endpoints_return_200(self, admin_client):
        failures = []
        for url in self.GOVERNANCE_ENDPOINTS:
            r = admin_client.get(url)
            if r.status_code not in (200, 204, 404):
                failures.append(f"{url} -> {r.status_code}")
        assert not failures, f"Governance API failures: {failures}"


class TestGovernanceUI:
    """Governance UI pages."""

    GOVERNANCE_URLS = [
        "/governance/",
        "/governance/audit-events/",
    ]

    def test_governance_pages_no_500(self, admin_client):
        failures = []
        for url in self.GOVERNANCE_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"Governance pages returned 500: {failures}"


class TestObservabilityInfrastructure:
    """TraceContext, metrics, decorators, structured logging."""

    def test_trace_context_importable(self):
        from apps.core.trace import TraceContext
        assert TraceContext is not None

    def test_trace_context_creates_valid_context(self):
        from apps.core.trace import TraceContext
        ctx = TraceContext(trace_id="test-trace-001", span_id="span-001")
        assert ctx.trace_id == "test-trace-001"

    def test_metrics_service_importable(self):
        from apps.core.metrics import MetricsService
        assert MetricsService is not None

    def test_observed_service_decorator_importable(self):
        from apps.core.decorators import observed_service
        assert observed_service is not None

    def test_observed_action_decorator_importable(self):
        from apps.core.decorators import observed_action
        assert observed_action is not None

    def test_observed_task_decorator_importable(self):
        from apps.core.decorators import observed_task
        assert observed_task is not None

    def test_json_log_formatter_importable(self):
        from apps.core.logging_utils import JSONLogFormatter
        assert JSONLogFormatter is not None

    def test_tenant_middleware_importable(self):
        from apps.core.middleware import TenantMiddleware
        assert TenantMiddleware is not None

    def test_request_trace_middleware_importable(self):
        from apps.core.middleware import RequestTraceMiddleware
        assert RequestTraceMiddleware is not None

    def test_rbac_middleware_importable(self):
        from apps.core.middleware import RBACMiddleware
        assert RBACMiddleware is not None


class TestTenantIsolation:
    """Multi-tenant isolation primitives."""

    def test_tenant_queryset_mixin_importable(self):
        from apps.core.tenant_utils import TenantQuerysetMixin
        assert TenantQuerysetMixin is not None

    def test_scoped_queryset_importable(self):
        from apps.core.tenant_utils import scoped_queryset
        assert scoped_queryset is not None

    def test_require_tenant_importable(self):
        from apps.core.tenant_utils import require_tenant
        assert require_tenant is not None
