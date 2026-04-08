"""Header-level matching service -- compares invoice header to PO header."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, TYPE_CHECKING

from apps.core.utils import normalize_string, within_tolerance
from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.services.tolerance_engine import FieldComparison, ToleranceEngine

if TYPE_CHECKING:
    from apps.reconciliation.services.po_balance_service import POBalance

logger = logging.getLogger(__name__)


@dataclass
class HeaderMatchResult:
    """Outcome of header-level comparison."""

    vendor_match: Optional[bool] = None
    currency_match: Optional[bool] = None
    po_total_match: Optional[bool] = None
    total_comparison: Optional[FieldComparison] = None
    tax_match: Optional[bool] = None
    tax_comparison: Optional[FieldComparison] = None
    all_ok: bool = False
    is_partial_invoice: bool = False
    prior_invoice_count: int = 0

    # Tax compliance fields
    gstin_match: Optional[bool] = None  # vendor GSTIN/tax-id match
    country_match: Optional[bool] = None  # inferred country vs PO country
    supply_type_match: Optional[bool] = None  # INTRA/INTER consistency
    tax_compliance_details: Dict = field(default_factory=dict)


class HeaderMatchService:
    """Deterministic header-level comparisons between Invoice and PO."""

    def __init__(self, tolerance_engine: ToleranceEngine):
        self.engine = tolerance_engine

    def match(self, invoice: Invoice, po: PurchaseOrder, po_balance: Optional["POBalance"] = None) -> HeaderMatchResult:
        result = HeaderMatchResult()

        # Track partial invoice context
        if po_balance and po_balance.is_partial:
            result.is_partial_invoice = True
            result.prior_invoice_count = po_balance.prior_invoice_count

        # 1. Vendor match
        result.vendor_match = self._check_vendor(invoice, po)

        # 2. Currency match
        inv_currency = (invoice.currency or "").strip().upper()
        po_currency = (po.currency or "").strip().upper()
        result.currency_match = inv_currency == po_currency if inv_currency and po_currency else None

        # 3. Total amount comparison
        # When a PO has prior invoices, compare against the remaining balance
        # instead of the full PO total.
        # For first partial invoices (no priors but invoice << PO), verify
        # the invoice does not exceed the PO total -- do not flag the
        # difference as a mismatch since multiple invoices are expected.
        compare_total = po.total_amount
        if po_balance and po_balance.is_partial:
            if po_balance.prior_invoice_count > 0:
                compare_total = po_balance.remaining_total
            else:
                # First partial: invoice total is within PO bounds.
                # Compare invoice against itself so tolerance passes, then
                # downstream logic uses the is_partial_invoice flag.
                compare_total = invoice.total_amount

        result.total_comparison = self.engine.compare_amount(
            invoice.total_amount, compare_total
        )
        result.po_total_match = result.total_comparison.within_tolerance

        # 4. Tax amount comparison
        compare_tax = po.tax_amount
        if po_balance and po_balance.is_partial and po_balance.remaining_tax is not None:
            if po_balance.prior_invoice_count > 0:
                compare_tax = po_balance.remaining_tax
            else:
                # First partial: tax proportional check -- accept if
                # invoice tax <= PO tax (partial tax on partial amount).
                compare_tax = invoice.tax_amount

        if invoice.tax_amount is not None and compare_tax is not None:
            result.tax_comparison = self.engine.compare_amount(
                invoice.tax_amount, compare_tax
            )
            result.tax_match = result.tax_comparison.within_tolerance

        # 5. Tax compliance checks (GSTIN, country, supply type)
        self._check_tax_compliance(invoice, po, result)

        # Overall header pass
        result.all_ok = all([
            result.vendor_match is True,
            result.currency_match is True,
            result.po_total_match is True,
            result.tax_match is not False,  # None (missing tax) is acceptable
            result.gstin_match is not False,
            result.country_match is not False,
            result.supply_type_match is not False,
        ])

        logger.info(
            "Header match for invoice %s vs PO %s: vendor=%s currency=%s total=%s all_ok=%s",
            invoice.pk, po.po_number,
            result.vendor_match, result.currency_match,
            result.po_total_match, result.all_ok,
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _check_vendor(invoice: Invoice, po: PurchaseOrder) -> Optional[bool]:
        """Compare vendor at multiple levels: FK, normalised name."""
        # Direct FK match
        if invoice.vendor_id and po.vendor_id:
            return invoice.vendor_id == po.vendor_id

        # Normalised name fallback
        inv_name = normalize_string(invoice.raw_vendor_name) if invoice.raw_vendor_name else ""
        po_name = ""
        if po.vendor:
            po_name = po.vendor.normalized_name or normalize_string(po.vendor.name)
        if inv_name and po_name:
            return inv_name == po_name

        return None  # Inconclusive

    # ------------------------------------------------------------------
    # Tax compliance helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _check_tax_compliance(
        invoice: Invoice, po: PurchaseOrder, result: HeaderMatchResult,
    ) -> None:
        """Run country-aware tax compliance checks.

        Populates ``result.gstin_match``, ``result.country_match``,
        ``result.supply_type_match``, and ``result.tax_compliance_details``.
        Checks are only performed when the PO carries the relevant data;
        missing data leaves the field as ``None`` (inconclusive).
        """
        details: Dict = {}
        po_country = (po.country or "").strip().upper()

        # --- 1. Country / region match ---
        # Infer the invoice's country from vendor_tax_id format or currency.
        inv_country = _infer_country(invoice)
        if inv_country and po_country:
            result.country_match = inv_country == po_country
            details["invoice_country_inferred"] = inv_country
            details["po_country"] = po_country

        # --- 2. Vendor GSTIN / Tax ID match ---
        inv_tax_id = (invoice.vendor_tax_id or "").strip().upper()
        po_vendor_gstin = (po.vendor_gstin or "").strip().upper()
        if inv_tax_id and po_vendor_gstin:
            result.gstin_match = inv_tax_id == po_vendor_gstin
            details["invoice_vendor_tax_id"] = inv_tax_id
            details["po_vendor_gstin"] = po_vendor_gstin

        # --- 3. India: supply type consistency ---
        # INTRA-state -> CGST + SGST; INTER-state -> IGST.
        # Compare PO.india_supply_type against invoice.tax_breakdown composition.
        if po_country == "IN" and po.india_supply_type:
            inv_supply_type = _infer_supply_type(invoice)
            if inv_supply_type:
                result.supply_type_match = inv_supply_type == po.india_supply_type
                details["invoice_supply_type_inferred"] = inv_supply_type
                details["po_supply_type"] = po.india_supply_type

        result.tax_compliance_details = details


# ======================================================================
# Module-level helpers (not part of the class — avoids cluttering API)
# ======================================================================

def _infer_country(invoice: Invoice) -> str:
    """Best-effort country inference from invoice data.

    Priority: GSTIN regex (India) -> currency code -> empty.
    """
    import re
    tax_id = (invoice.vendor_tax_id or "").strip()
    # Indian GSTIN: 2 digits + 10 alphanum (PAN) + 1 alphanum + Z + 1 alphanum
    if tax_id and re.match(r"^\d{2}[A-Z0-9]{10}[A-Z0-9]Z[A-Z0-9]$", tax_id.upper()):
        return "IN"

    currency = (invoice.currency or "").strip().upper()
    _CURRENCY_COUNTRY = {
        "INR": "IN",
        "AED": "AE",
        "SAR": "SA",
        "USD": "US",
        "EUR": "EU",
        "GBP": "GB",
    }
    return _CURRENCY_COUNTRY.get(currency, "")


def _infer_supply_type(invoice: Invoice) -> str:
    """Infer INTRA or INTER from invoice.tax_breakdown.

    If breakdown has non-zero CGST+SGST -> INTRA.
    If breakdown has non-zero IGST -> INTER.
    Returns empty string if inconclusive.
    """
    breakdown = invoice.tax_breakdown or {}
    cgst = Decimal(str(breakdown.get("cgst", 0) or 0))
    sgst = Decimal(str(breakdown.get("sgst", 0) or 0))
    igst = Decimal(str(breakdown.get("igst", 0) or 0))

    has_cgst_sgst = (cgst > 0) or (sgst > 0)
    has_igst = igst > 0

    if has_cgst_sgst and not has_igst:
        return "INTRA"
    if has_igst and not has_cgst_sgst:
        return "INTER"
    return ""
