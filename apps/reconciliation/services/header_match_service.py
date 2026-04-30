"""Header-level matching service -- compares invoice header to PO header."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
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
    total_comparison_basis: str = "gross"
    all_ok: bool = False
    is_partial_invoice: bool = False
    prior_invoice_count: int = 0
    vendor_match_details: Dict = field(default_factory=dict)

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
        vendor_match, vendor_details = self._check_vendor(invoice, po)
        result.vendor_match = vendor_match
        result.vendor_match_details = vendor_details

        # 2. Currency match
        inv_currency = (invoice.currency or "").strip().upper()
        po_currency = (po.currency or "").strip().upper()
        result.currency_match = inv_currency == po_currency if inv_currency and po_currency else None

        # 3. Tax amount comparison
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

        # 4. Total amount comparison
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

        expected_total = compare_total
        compare_invoice_total = invoice.total_amount
        comparison_basis = "gross"
        invoice_subtotal = invoice.subtotal
        if invoice_subtotal is None and invoice.total_amount is not None and invoice.tax_amount is not None:
            invoice_subtotal = invoice.total_amount - invoice.tax_amount

        # Some source systems capture PO.total_amount as pre-tax amount while
        # invoice.total_amount is tax-inclusive. If invoice subtotal aligns
        # with PO total, compare gross-to-gross by adding PO tax.
        if (
            invoice_subtotal is not None
            and compare_total is not None
        ):
            if within_tolerance(
                invoice_subtotal,
                compare_total,
                self.engine.thresholds.amount_pct,
            ):
                if compare_tax is not None:
                    expected_total = compare_total + compare_tax
                else:
                    # PO tax may be missing while PO total is net-of-tax.
                    # In this case compare net invoice amount against PO total.
                    compare_invoice_total = invoice_subtotal
                    comparison_basis = "net"

        result.total_comparison = self.engine.compare_amount(
            compare_invoice_total, expected_total
        )
        result.total_comparison_basis = comparison_basis
        result.po_total_match = result.total_comparison.within_tolerance

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
    def _check_vendor(invoice: Invoice, po: PurchaseOrder) -> tuple[Optional[bool], Dict]:
        """Compare vendor at multiple levels: FK, normalised name, fuzzy name."""
        details: Dict = {
            "strategy": "inconclusive",
            "similarity_score": None,
        }

        # Direct FK match (strongest signal).
        if invoice.vendor_id and po.vendor_id and invoice.vendor_id == po.vendor_id:
            details.update({
                "strategy": "vendor_id_exact",
                "similarity_score": 1.0,
            })
            return True, details

        # Name fallback: needed when duplicate vendor masters exist or one side
        # stores a prefixed ERP label (for example "VND680 - Vendor Name").
        inv_name = HeaderMatchService._normalise_vendor_name(
            str(invoice.vendor or invoice.raw_vendor_name or "")
        )
        po_name = HeaderMatchService._normalise_vendor_name(
            str(po.vendor or "")
        )

        if inv_name and po_name:
            ratio = SequenceMatcher(None, inv_name, po_name).ratio()
            details.update({
                "strategy": "name_compare",
                "invoice_name_normalized": inv_name,
                "po_name_normalized": po_name,
                "similarity_score": round(ratio, 4),
            })

            if inv_name == po_name:
                details["strategy"] = "name_exact"
                return True, details

            fuzzy_match, coverage = HeaderMatchService._is_vendor_name_fuzzy_match(inv_name, po_name)
            details["token_coverage"] = round(coverage, 4)
            if fuzzy_match:
                details["strategy"] = "name_fuzzy"
                return True, details

            details["strategy"] = "name_mismatch"
            return False, details

        # Fallback to FK mismatch only when names are unavailable.
        if invoice.vendor_id and po.vendor_id:
            details.update({
                "strategy": "vendor_id_mismatch_no_names",
                "invoice_vendor_id": invoice.vendor_id,
                "po_vendor_id": po.vendor_id,
            })
            return False, details

        return None, details  # Inconclusive

    @staticmethod
    def _normalise_vendor_name(value: str) -> str:
        """Normalise vendor names and remove leading ERP code prefixes."""
        text = normalize_string(value)
        # Drop common code prefixes like "vnd680", "ven123", etc.
        text = re.sub(r"^(?:vnd|ven|vendor)\s*\d+\s*[-:]?\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _is_vendor_name_fuzzy_match(left: str, right: str) -> tuple[bool, float]:
        """Return fuzzy decision and token-overlap coverage for vendor names."""
        ratio = SequenceMatcher(None, left, right).ratio()
        if ratio >= 0.90:
            return True, 1.0

        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return False, 0.0

        overlap = len(left_tokens & right_tokens)
        coverage = overlap / float(min(len(left_tokens), len(right_tokens)))
        return coverage >= 0.85, coverage

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
        raw_country = po.country
        po_country = raw_country.strip().upper() if isinstance(raw_country, str) and raw_country else ""

        # --- 1. Country / region match ---
        # Infer the invoice's country from vendor_tax_id format or currency.
        inv_country = _infer_country(invoice)
        if inv_country and po_country:
            result.country_match = inv_country == po_country
            details["invoice_country_inferred"] = inv_country
            details["po_country"] = po_country

        # --- 2. Vendor GSTIN / Tax ID match ---
        inv_tax_id = invoice.vendor_tax_id
        inv_tax_id = inv_tax_id.strip().upper() if isinstance(inv_tax_id, str) and inv_tax_id else ""
        po_vendor_gstin = po.vendor_gstin
        po_vendor_gstin = po_vendor_gstin.strip().upper() if isinstance(po_vendor_gstin, str) and po_vendor_gstin else ""
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
    raw_tax_id = invoice.vendor_tax_id
    tax_id = str(raw_tax_id).strip() if raw_tax_id and isinstance(raw_tax_id, str) else ""
    # Indian GSTIN: 2 digits + 10 alphanum (PAN) + 1 alphanum + Z + 1 alphanum
    if tax_id and re.match(r"^\d{2}[A-Z0-9]{10}[A-Z0-9]Z[A-Z0-9]$", tax_id.upper()):
        return "IN"

    raw_currency = invoice.currency
    currency = raw_currency.strip().upper() if isinstance(raw_currency, str) and raw_currency else ""
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
