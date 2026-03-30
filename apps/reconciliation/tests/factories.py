"""Factory-boy factories for reconciliation test fixtures."""
from __future__ import annotations

import factory
from decimal import Decimal
from datetime import date

from apps.documents.models import Invoice, PurchaseOrder, InvoiceLineItem, PurchaseOrderLineItem
from apps.reconciliation.models import ReconciliationConfig, ReconciliationPolicy
from apps.core.enums import InvoiceStatus, ReconciliationMode


# ── Vendor (optional standalone factory if vendors app has a Vendor model) ──
# If your Vendor model lives in apps.vendors, adjust the import below.
# Fallback: create inline via InvoiceFactory.

try:
    from apps.vendors.models import Vendor  # noqa: F401

    class VendorFactory(factory.django.DjangoModelFactory):
        class Meta:
            model = "vendors.Vendor"

        name = factory.Sequence(lambda n: f"Vendor {n}")
        normalized_name = factory.LazyAttribute(lambda o: o.name.lower())

except ImportError:
    VendorFactory = None  # type: ignore


# ── ReconciliationConfig ──────────────────────────────────────────────────────

class ReconConfigFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ReconciliationConfig

    name = factory.Sequence(lambda n: f"Config {n}")
    is_default = True
    quantity_tolerance_pct = 2.0
    price_tolerance_pct = 1.0
    amount_tolerance_pct = 1.0
    default_reconciliation_mode = ReconciliationMode.THREE_WAY
    enable_mode_resolver = True
    enable_two_way_for_services = True
    enable_grn_for_stock_items = True


# ── ReconciliationPolicy ──────────────────────────────────────────────────────

class ReconPolicyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ReconciliationPolicy

    policy_code = factory.Sequence(lambda n: f"POL-{n:03d}")
    policy_name = factory.Sequence(lambda n: f"Policy {n}")
    reconciliation_mode = ReconciliationMode.THREE_WAY
    is_active = True
    priority = 10
    effective_from = None
    effective_to = None
    vendor = None
    invoice_type = ""
    item_category = ""
    business_unit = ""
    location_code = ""
    is_service_invoice = None
    is_stock_invoice = None


# ── Invoice ───────────────────────────────────────────────────────────────────

class InvoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Invoice

    invoice_number = factory.Sequence(lambda n: f"INV-{n:04d}")
    currency = "SAR"
    total_amount = Decimal("1000.00")
    tax_amount = Decimal("150.00")
    status = InvoiceStatus.READY_FOR_RECON
    extraction_confidence = 0.95
    is_duplicate = False
    raw_vendor_name = factory.Sequence(lambda n: f"Vendor {n}")

    # vendor FK — will be None unless explicitly set
    vendor = None


# ── PurchaseOrder ─────────────────────────────────────────────────────────────

class POFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PurchaseOrder

    po_number = factory.Sequence(lambda n: f"PO-{n:04d}")
    currency = "SAR"
    total_amount = Decimal("1000.00")
    tax_amount = Decimal("150.00")
    vendor = None
    department = ""


# ── InvoiceLineItem ───────────────────────────────────────────────────────────

class InvoiceLineItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InvoiceLineItem

    invoice = factory.SubFactory(InvoiceFactory)
    line_number = factory.Sequence(lambda n: n + 1)
    description = "Test Item"
    raw_description = "Test Item"
    normalized_description = "test item"
    quantity = Decimal("10.00")
    unit_price = Decimal("100.00")
    line_amount = Decimal("1000.00")
    tax_amount = None
    is_service_item = None
    is_stock_item = None
    item_category = ""


# ── PurchaseOrderLineItem ─────────────────────────────────────────────────────

class POLineItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PurchaseOrderLineItem

    purchase_order = factory.SubFactory(POFactory)
    line_number = factory.Sequence(lambda n: n + 1)
    description = "Test Item"
    quantity = Decimal("10.00")
    unit_price = Decimal("100.00")
    line_amount = Decimal("1000.00")
    tax_amount = None
    is_service_item = None
    is_stock_item = None
    item_category = ""
