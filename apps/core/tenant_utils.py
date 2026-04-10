"""Tenant-scoping utilities for views, services, and Celery tasks.

Usage
-----
**Class-based views / DRF ViewSets**::

    from apps.core.tenant_utils import TenantQuerysetMixin

    class InvoiceListView(TenantQuerysetMixin, ListView):
        model = Invoice
        ...

**Function-based views**::

    from apps.core.tenant_utils import require_tenant

    def my_view(request):
        tenant = require_tenant(request)
        invoices = Invoice.objects.filter(tenant=tenant)

**Service layer** (no request available)::

    from apps.core.tenant_utils import scoped_queryset

    invoices = scoped_queryset(Invoice, tenant)
"""
from django.core.exceptions import PermissionDenied


def _tenant_filter_kwargs(model, tenant):
    """Return the correct filter kwargs for tenant scoping.

    Most models use ``tenant`` FK; the User model uses ``company``.
    """
    for f in model._meta.get_fields():
        if getattr(f, "name", None) == "tenant" and getattr(f, "related_model", None):
            return {"tenant": tenant}
    # Fallback: try 'company' (User model)
    for f in model._meta.get_fields():
        if getattr(f, "name", None) == "company" and getattr(f, "related_model", None):
            return {"company": tenant}
    return {"tenant": tenant}


class TenantQuerysetMixin:
    """Mixin for Django class-based views and DRF ViewSets.

    Automatically scopes every queryset to ``request.tenant``.
    Superusers and requests without a tenant (e.g. superuser admin work)
    bypass the filter and see all records.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = getattr(self.request, "tenant", None)
        if getattr(self.request.user, "is_platform_admin", False) or self.request.user.is_superuser or tenant is None:
            return qs
        return qs.filter(**_tenant_filter_kwargs(qs.model, tenant))


def require_tenant(request):
    """Return ``request.tenant``, raising ``PermissionDenied`` for regular
    users without a resolved tenant.

    Superusers always pass (they see all tenants).
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None and not (getattr(request.user, "is_platform_admin", False) or request.user.is_superuser):
        raise PermissionDenied("No tenant context for this request.")
    return tenant


def get_tenant_or_none(request):
    """Return ``request.tenant`` or ``None`` without raising.

    Useful when a view needs to behave differently for superusers vs
    tenant-scoped users.
    """
    return getattr(request, "tenant", None)


def scoped_queryset(model_class, tenant):
    """Return a queryset for *model_class* filtered to *tenant*.

    If *tenant* is ``None`` (superuser context), returns an unfiltered
    queryset so superusers can access all records.

    Use this in service-layer methods where ``request`` is not available::

        from apps.core.tenant_utils import scoped_queryset

        def get_open_cases(tenant):
            return scoped_queryset(APCase, tenant).filter(status='OPEN')
    """
    qs = model_class.objects.all()
    if tenant is not None:
        qs = qs.filter(**_tenant_filter_kwargs(model_class, tenant))
    return qs


def assert_tenant_access(obj, tenant):
    """Raise ``PermissionDenied`` if *obj.tenant* does not match *tenant*.

    Use in detail views / retrieve operations to prevent cross-tenant
    object access when the object was fetched without a tenant filter
    (e.g. via a raw pk lookup).

    Superuser context (tenant=None) always passes.
    """
    if tenant is None:
        return  # superuser — no check needed
    if hasattr(obj, "tenant_id") and obj.tenant_id != tenant.pk:
        raise PermissionDenied(
            f"Object pk={obj.pk} does not belong to tenant '{tenant.name}'."
        )
