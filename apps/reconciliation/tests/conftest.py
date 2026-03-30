"""
Shared fixtures for reconciliation tests.

Uses factory-boy factories from factories.py.
DB-touching fixtures are marked with the `db` fixture from pytest-django.
"""
from __future__ import annotations

import pytest
from decimal import Decimal

from apps.reconciliation.services.tolerance_engine import ToleranceEngine, ToleranceThresholds


# ─── Tolerance engines ────────────────────────────────────────────────────────

@pytest.fixture
def default_tolerance_engine():
    """ToleranceEngine with default thresholds (qty=2%, price=1%, amount=1%)."""
    engine = ToleranceEngine.__new__(ToleranceEngine)
    engine.thresholds = ToleranceThresholds(
        quantity_pct=2.0,
        price_pct=1.0,
        amount_pct=1.0,
    )
    return engine


@pytest.fixture
def wide_tolerance_engine():
    """ToleranceEngine with relaxed thresholds (qty=5%, price=3%, amount=3%)."""
    engine = ToleranceEngine.__new__(ToleranceEngine)
    engine.thresholds = ToleranceThresholds(
        quantity_pct=5.0,
        price_pct=3.0,
        amount_pct=3.0,
    )
    return engine


# ─── DB fixtures (require @pytest.mark.django_db on the test) ─────────────────

@pytest.fixture
def recon_config(db):
    """Default ReconciliationConfig with standard tolerances."""
    from apps.reconciliation.tests.factories import ReconConfigFactory
    return ReconConfigFactory(is_default=True)


@pytest.fixture
def vendor(db):
    """A Vendor model instance, if Vendor model is available."""
    try:
        from apps.vendors.models import Vendor
        return Vendor.objects.create(name="Test Vendor", normalized_name="test vendor")
    except Exception:
        return None


@pytest.fixture
def invoice(db):
    """A basic invoice in READY_FOR_RECON state."""
    from apps.reconciliation.tests.factories import InvoiceFactory
    return InvoiceFactory()


@pytest.fixture
def purchase_order(db):
    """A basic purchase order."""
    from apps.reconciliation.tests.factories import POFactory
    return POFactory()


@pytest.fixture
def invoice_with_vendor(db, vendor):
    """Invoice associated with a Vendor FK."""
    from apps.reconciliation.tests.factories import InvoiceFactory
    return InvoiceFactory(vendor=vendor)


@pytest.fixture
def po_with_vendor(db, vendor):
    """PO associated with a Vendor FK."""
    from apps.reconciliation.tests.factories import POFactory
    return POFactory(vendor=vendor)


@pytest.fixture
def invoice_line(db, invoice):
    """A single invoice line item."""
    from apps.reconciliation.tests.factories import InvoiceLineItemFactory
    return InvoiceLineItemFactory(invoice=invoice)


@pytest.fixture
def po_line(db, purchase_order):
    """A single PO line item."""
    from apps.reconciliation.tests.factories import POLineItemFactory
    return POLineItemFactory(purchase_order=purchase_order)
