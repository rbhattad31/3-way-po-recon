"""Exception builder — creates structured ReconciliationException records from comparison evidence."""
from __future__  import annotations

import logging
from decimal import Decimal
from typing import List, Optional

from apps.core.constants import THREE_WAY_ONLY_EXCEPTION_TYPES
from apps.core.enums import ExceptionSeverity, ExceptionType, ReconciliationMode, ReconciliationModeApplicability
from apps.reconciliation.models import ReconciliationException, ReconciliationResult, ReconciliationResultLine
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchPair, LineMatchResult
from apps.reconciliation.services.grn_match_service import GRNMatchResult
from apps.reconciliation.services.po_lookup_service import POLookupResult

logger = logging.getLogger(__name__)


class ExceptionBuilderService:
    """Build ReconciliationException objects from comparison evidence.

    Does NOT save to DB — returns a list of unsaved instances so the caller
    can bulk-create within a transaction.
    """

    def build(
        self,
        result: ReconciliationResult,
        po_result: POLookupResult,
        header_result: Optional[HeaderMatchResult],
        line_result: Optional[LineMatchResult],
        grn_result: Optional[GRNMatchResult],
        result_line_map: Optional[dict] = None,
        extraction_confidence: Optional[float] = None,
        confidence_threshold: float = 0.75,
        reconciliation_mode: str = "",
    ) -> List[ReconciliationException]:
        """Return a list of unsaved ReconciliationException instances."""
        is_two_way = reconciliation_mode == ReconciliationMode.TWO_WAY
        exceptions: List[ReconciliationException] = []

        # PO not found
        if not po_result.found:
            exceptions.append(self._make(
                result=result,
                exc_type=ExceptionType.PO_NOT_FOUND,
                severity=ExceptionSeverity.HIGH,
                message=f"Purchase order not found for PO number '{result.invoice.po_number}'",
                details={"po_number": result.invoice.po_number},
            ))
            return exceptions  # No further checks possible

        # Low confidence
        if extraction_confidence is not None and extraction_confidence < confidence_threshold:
            exceptions.append(self._make(
                result=result,
                exc_type=ExceptionType.EXTRACTION_LOW_CONFIDENCE,
                severity=ExceptionSeverity.MEDIUM,
                message=f"Extraction confidence {extraction_confidence:.2f} below threshold {confidence_threshold}",
                details={"confidence": extraction_confidence, "threshold": confidence_threshold},
            ))

        # Header-level exceptions
        if header_result:
            exceptions.extend(self._header_exceptions(result, header_result))

        # Line-level exceptions
        if line_result and result_line_map:
            exceptions.extend(self._line_exceptions(result, line_result, result_line_map))

        # GRN exceptions (3-way only)
        if not is_two_way and grn_result:
            exceptions.extend(self._grn_exceptions(result, grn_result))

        # Tag each exception with the applicable mode
        for exc in exceptions:
            exc_type = exc.exception_type
            if exc_type in THREE_WAY_ONLY_EXCEPTION_TYPES:
                exc.applies_to_mode = ReconciliationModeApplicability.THREE_WAY
            else:
                exc.applies_to_mode = ReconciliationModeApplicability.BOTH

        logger.info("Built %d exceptions for result %s (mode=%s)", len(exceptions), result.pk, reconciliation_mode)
        return exceptions

    # ------------------------------------------------------------------
    # Header exceptions
    # ------------------------------------------------------------------
    def _header_exceptions(
        self, result: ReconciliationResult, header: HeaderMatchResult
    ) -> List[ReconciliationException]:
        excs: List[ReconciliationException] = []

        if header.vendor_match is False:
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.VENDOR_MISMATCH,
                severity=ExceptionSeverity.HIGH,
                message="Vendor on invoice does not match vendor on PO",
                details={
                    "invoice_vendor": str(result.invoice.vendor or result.invoice.raw_vendor_name),
                    "po_vendor": str(result.purchase_order.vendor if result.purchase_order else ""),
                },
            ))

        if header.currency_match is False:
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.CURRENCY_MISMATCH,
                severity=ExceptionSeverity.MEDIUM,
                message=f"Currency mismatch: invoice={result.invoice.currency}, PO={result.purchase_order.currency if result.purchase_order else ''}",
            ))

        if header.po_total_match is False and header.total_comparison:
            tc = header.total_comparison
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.AMOUNT_MISMATCH,
                severity=ExceptionSeverity.HIGH,
                message=(
                    f"Total amount mismatch: invoice={tc.invoice_value}, "
                    f"PO={tc.po_value}, diff={tc.difference} ({tc.difference_pct}%)"
                ),
                details={
                    "invoice_total": str(tc.invoice_value),
                    "po_total": str(tc.po_value),
                    "difference": str(tc.difference),
                    "difference_pct": str(tc.difference_pct),
                },
            ))

        if header.tax_match is False and header.tax_comparison:
            txc = header.tax_comparison
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.TAX_MISMATCH,
                severity=ExceptionSeverity.MEDIUM,
                message=(
                    f"Tax amount mismatch: invoice={txc.invoice_value}, "
                    f"PO={txc.po_value}, diff={txc.difference} ({txc.difference_pct}%)"
                ),
                details={
                    "invoice_tax": str(txc.invoice_value),
                    "po_tax": str(txc.po_value),
                    "difference": str(txc.difference),
                    "difference_pct": str(txc.difference_pct),
                },
            ))

        return excs

    # ------------------------------------------------------------------
    # Line exceptions
    # ------------------------------------------------------------------
    def _line_exceptions(
        self,
        result: ReconciliationResult,
        line_result: LineMatchResult,
        result_line_map: dict,
    ) -> List[ReconciliationException]:
        excs: List[ReconciliationException] = []

        for pair in line_result.pairs:
            if not pair.matched:
                continue

            rl = result_line_map.get(pair.invoice_line.pk)

            # Quantity mismatch
            if pair.qty_comparison and pair.qty_comparison.within_tolerance is False:
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.QTY_MISMATCH,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Line {pair.invoice_line.line_number}: qty mismatch "
                        f"invoice={pair.qty_comparison.invoice_value} vs PO={pair.qty_comparison.po_value}"
                    ),
                    details={
                        "line_number": pair.invoice_line.line_number,
                        "invoice_qty": str(pair.qty_comparison.invoice_value),
                        "po_qty": str(pair.qty_comparison.po_value),
                        "difference_pct": str(pair.qty_comparison.difference_pct),
                    },
                ))

            # Price mismatch
            if pair.price_comparison and pair.price_comparison.within_tolerance is False:
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.PRICE_MISMATCH,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Line {pair.invoice_line.line_number}: price mismatch "
                        f"invoice={pair.price_comparison.invoice_value} vs PO={pair.price_comparison.po_value}"
                    ),
                    details={
                        "line_number": pair.invoice_line.line_number,
                        "invoice_price": str(pair.price_comparison.invoice_value),
                        "po_price": str(pair.price_comparison.po_value),
                        "difference_pct": str(pair.price_comparison.difference_pct),
                    },
                ))

            # Amount mismatch
            if pair.amount_comparison and pair.amount_comparison.within_tolerance is False:
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.AMOUNT_MISMATCH,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Line {pair.invoice_line.line_number}: amount mismatch "
                        f"invoice={pair.amount_comparison.invoice_value} vs PO={pair.amount_comparison.po_value}"
                    ),
                    details={
                        "line_number": pair.invoice_line.line_number,
                        "invoice_amount": str(pair.amount_comparison.invoice_value),
                        "po_amount": str(pair.amount_comparison.po_value),
                        "difference_pct": str(pair.amount_comparison.difference_pct),
                    },
                ))

            # Tax mismatch (simple diff check)
            if pair.tax_difference is not None and pair.tax_difference != Decimal("0"):
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.TAX_MISMATCH,
                    severity=ExceptionSeverity.LOW,
                    message=(
                        f"Line {pair.invoice_line.line_number}: tax mismatch "
                        f"invoice={pair.tax_invoice} vs PO={pair.tax_po}"
                    ),
                ))

        # Unmatched invoice lines
        for inv_line in line_result.unmatched_invoice_lines:
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.ITEM_MISMATCH,
                severity=ExceptionSeverity.HIGH,
                message=f"Invoice line {inv_line.line_number} has no matching PO line",
                details={
                    "line_number": inv_line.line_number,
                    "description": inv_line.description or inv_line.raw_description,
                },
            ))

        return excs

    # ------------------------------------------------------------------
    # GRN exceptions
    # ------------------------------------------------------------------
    def _grn_exceptions(
        self, result: ReconciliationResult, grn: GRNMatchResult
    ) -> List[ReconciliationException]:
        excs: List[ReconciliationException] = []

        if not grn.grn_available:
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.GRN_NOT_FOUND,
                severity=ExceptionSeverity.MEDIUM,
                message="No goods receipt notes found for this PO",
            ))
            return excs

        for cmp in grn.line_comparisons:
            if cmp.invoiced_exceeds_received:
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED,
                    severity=ExceptionSeverity.HIGH,
                    message=(
                        f"Invoiced quantity ({cmp.qty_invoiced}) exceeds "
                        f"received quantity ({cmp.qty_received}) for PO line {cmp.po_line_id}"
                    ),
                    details={
                        "po_line_id": cmp.po_line_id,
                        "qty_invoiced": str(cmp.qty_invoiced),
                        "qty_received": str(cmp.qty_received),
                    },
                ))

            if cmp.over_receipt:
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.OVER_RECEIPT,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Over-delivery: received {cmp.qty_received} vs "
                        f"ordered {cmp.qty_ordered} for PO line {cmp.po_line_id}"
                    ),
                    details={
                        "po_line_id": cmp.po_line_id,
                        "qty_ordered": str(cmp.qty_ordered),
                        "qty_received": str(cmp.qty_received),
                    },
                ))

            if cmp.under_receipt and not cmp.invoiced_exceeds_received:
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.RECEIPT_SHORTAGE,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Partial receipt: received {cmp.qty_received} vs "
                        f"ordered {cmp.qty_ordered} for PO line {cmp.po_line_id}"
                    ),
                    details={
                        "po_line_id": cmp.po_line_id,
                        "qty_ordered": str(cmp.qty_ordered),
                        "qty_received": str(cmp.qty_received),
                    },
                ))

        # Delayed receipt: GRN received long after PO date
        po = result.purchase_order
        po_date = po.po_date if po else None
        if po_date and grn.latest_receipt_date:
            days_since_po = (grn.latest_receipt_date - po_date).days
            if days_since_po > 30:
                severity = ExceptionSeverity.HIGH if days_since_po > 45 else ExceptionSeverity.MEDIUM
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.DELAYED_RECEIPT,
                    severity=severity,
                    message=(
                        f"Goods received {days_since_po} day(s) after PO date "
                        f"(PO: {po_date}, receipt: {grn.latest_receipt_date})"
                    ),
                    details={
                        "po_date": str(po_date),
                        "latest_receipt_date": str(grn.latest_receipt_date),
                        "days_late": days_since_po,
                    },
                ))

        return excs

    # ------------------------------------------------------------------
    # Factory helper
    # ------------------------------------------------------------------
    @staticmethod
    def _make(
        result: ReconciliationResult,
        exc_type: str,
        severity: str,
        message: str,
        result_line: Optional[ReconciliationResultLine] = None,
        details: Optional[dict] = None,
    ) -> ReconciliationException:
        return ReconciliationException(
            result=result,
            result_line=result_line,
            exception_type=exc_type,
            severity=severity,
            message=message,
            details=details,
        )
