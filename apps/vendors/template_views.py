"""Vendor template views (server-side rendered)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.enums import UserRole
from apps.core.permissions import permission_required_code
from apps.documents.models import Invoice
from apps.vendors.forms import VendorForm
from apps.vendors.models import Vendor


def _scope_vendors_for_user(qs, user):
    """Restrict vendor queryset — AP_PROCESSOR sees only vendors linked to their invoices/POs."""
    if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
        return qs
    from apps.reconciliation.models import ReconciliationConfig
    config = ReconciliationConfig.objects.filter(is_default=True).first()
    if config and config.ap_processor_sees_all_cases:
        return qs
    user_vendor_ids = (
        Invoice.objects.filter(document_upload__uploaded_by=user, vendor__isnull=False)
        .values_list("vendor_id", flat=True)
        .distinct()
    )
    return qs.filter(pk__in=user_vendor_ids)


@login_required
@permission_required_code("vendors.view")
def vendor_list(request):
    qs = Vendor.objects.annotate(
        alias_count=Count("alias_mappings"),
        po_count=Count("purchase_orders", distinct=True),
        invoice_count=Count("invoices", distinct=True),
    ).order_by("name")
    qs = _scope_vendors_for_user(qs, request.user)

    # Filters
    country_filter = request.GET.get("country")
    if country_filter:
        qs = qs.filter(country=country_filter)

    currency_filter = request.GET.get("currency")
    if currency_filter:
        qs = qs.filter(currency=currency_filter)

    q = request.GET.get("q")
    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(tax_id__icontains=q)
            | Q(contact_email__icontains=q)
        )

    # Choices for filters (scoped)
    scoped_base = _scope_vendors_for_user(Vendor.objects.all(), request.user)
    country_choices = (
        scoped_base.exclude(country="")
        .order_by("country")
        .values_list("country", flat=True)
        .distinct()
    )
    currency_choices = (
        scoped_base.exclude(currency="")
        .order_by("currency")
        .values_list("currency", flat=True)
        .distinct()
    )

    # Stats (scoped)
    total = scoped_base.count()
    with_po = scoped_base.filter(purchase_orders__isnull=False).distinct().count()
    with_invoice = scoped_base.filter(invoices__isnull=False).distinct().count()
    from apps.posting_core.models import VendorAliasMapping
    total_aliases = VendorAliasMapping.objects.filter(vendor__in=scoped_base, is_active=True).count()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "vendors/vendor_list.html", {
        "vendors": page_obj,
        "page_obj": page_obj,
        "country_choices": country_choices,
        "currency_choices": currency_choices,
        "stats": {
            "total": total,
            "with_po": with_po,
            "with_invoice": with_invoice,
            "total_aliases": total_aliases,
        },
    })


@login_required
@permission_required_code("vendors.view")
def vendor_detail(request, pk):
    vendor = get_object_or_404(
        Vendor.objects.prefetch_related("alias_mappings"),
        pk=pk,
    )

    # Related POs
    purchase_orders = vendor.purchase_orders.order_by("-po_date")[:20]

    # Related Invoices
    invoices = vendor.invoices.select_related("document_upload").order_by("-created_at")[:20]

    # Related GRNs
    grns = vendor.grns.select_related("purchase_order").order_by("-receipt_date")[:20]

    return render(request, "vendors/vendor_detail.html", {
        "vendor": vendor,
        "purchase_orders": purchase_orders,
        "invoices": invoices,
        "grns": grns,
    })


@login_required
@permission_required_code("vendors.create")
def vendor_create(request):
    if request.method == "POST":
        form = VendorForm(request.POST)
        if form.is_valid():
            vendor = form.save()
            messages.success(request, f"Vendor '{vendor.name}' created successfully.")
            return redirect("vendors:vendor_detail", pk=vendor.pk)
    else:
        form = VendorForm()
    return render(request, "vendors/vendor_create.html", {"form": form})


@login_required
@permission_required_code("vendors.edit")
def vendor_edit(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk)
    if request.method == "POST":
        form = VendorForm(request.POST, instance=vendor)
        if form.is_valid():
            form.save()
            messages.success(request, f"Vendor '{vendor.name}' updated successfully.")
            return redirect("vendors:vendor_detail", pk=vendor.pk)
    else:
        form = VendorForm(instance=vendor)
    return render(request, "vendors/vendor_edit.html", {"form": form, "vendor": vendor})
