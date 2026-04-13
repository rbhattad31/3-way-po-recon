"""Exception builder -- creates structured ReconciliationException records from comparison evidence."""
from __future__  import annotations

import logging
from decimal import Decimal
from typing import List, Optional, TYPE_CHECKING

from apps.core.constants import THREE_WAY_ONLY_EXCEPTION_TYPES
from apps.core.enums import ExceptionSeverity, ExceptionType, ReconciliationMode, ReconciliationModeApplicability
from apps.reconciliation.models import ReconciliationException, ReconciliationResult, ReconciliationResultLine
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchPair, LineMatchResult
from apps.reconciliation.services.grn_match_service import GRNMatchResult
from apps.reconciliation.services.po_lookup_service import POLookupResult

if TYPE_CHECKING:
    from apps.reconciliation.services.po_balance_service import POBalance

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
        po_balance: Optional["POBalance"] = None,
    ) -> List[ReconciliationException]:
        """Return a list of unsaved ReconciliationException instances."""
        is_two_way = reconciliation_mode == ReconciliationMode.TWO_WAY
        is_non_po = reconciliation_mode == ReconciliationMode.NON_PO
        exceptions: List[ReconciliationException] = []

        # PO not found -- skip for NON_PO invoices (no PO expected)
        if not po_result.found:
            if not is_non_po:
                exceptions.append(self._make(
                    result=result,
                    exc_type=ExceptionType.PO_NOT_FOUND,
                    severity=ExceptionSeverity.HIGH,
                    message=f"Purchase order not found for PO number '{result.invoice.po_number}'",
                    details={"po_number": result.invoice.po_number},
                ))
            return exceptions  # No further checks possible

        # Duplicate invoice
        invoice = result.invoice
        if getattr(invoice, 'is_duplicate', False):
            dup_details = {"duplicate_of_id": invoice.duplicate_of_id}
            if invoice.duplicate_of_id:
                try:
                    from apps.documents.models import Invoice as InvModel
                    dup_inv = InvModel.objects.only('invoice_number').get(pk=invoice.duplicate_of_id)
                    dup_details["duplicate_of_invoice"] = dup_inv.invoice_number
                except InvModel.DoesNotExist:
                    pass
            exceptions.append(self._make(
                result=result,
                exc_type=ExceptionType.DUPLICATE_INVOICE,
                severity=ExceptionSeverity.HIGH,
                message=f"Invoice flagged as duplicate of Invoice #{invoice.duplicate_of_id}",
                details=dup_details,
            ))

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
            exceptions.extend(self._header_exceptions(result, header_result, po_balance))

        # Line-level exceptions
        if line_result and result_line_map:
            exceptions.extend(self._line_exceptions(result, line_result, result_line_map))

        # GRN exceptions (3-way only)
        if not is_two_way and grn_result:
            exceptions.extend(self._grn_exceptions(result, grn_result, po_balance))

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
        self, result: ReconciliationResult, header: HeaderMatchResult,
        po_balance: Optional["POBalance"] = None,
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

        # Partial invoice informational exception
        if header.is_partial_invoice and po_balance:
            covers_pct = po_balance.invoice_covers_pct
            prior_count = po_balance.prior_invoice_count
            if po_balance.is_first_partial:
                partial_msg = (
                    f"Partial invoice detected: invoice total={result.invoice.total_amount} "
                    f"covers {covers_pct}% of PO total={po_balance.po_total}. "
                    f"This PO may have multiple invoices (milestone/partial billing)."
                )
            else:
                partial_msg = (
                    f"Partial invoice: invoice total={result.invoice.total_amount} "
                    f"against PO remaining={po_balance.remaining_total} "
                    f"(PO total={po_balance.po_total}, "
                    f"prior invoiced={po_balance.prior_invoiced_total}, "
                    f"prior invoices={prior_count})."
                )
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.PARTIAL_INVOICE,
                severity=ExceptionSeverity.LOW,
                message=partial_msg,
                details={
                    "is_partial_invoice": True,
                    "is_first_partial": po_balance.is_first_partial,
                    "invoice_total": str(result.invoice.total_amount),
                    "po_total": str(po_balance.po_total),
                    "covers_pct": str(covers_pct),
                    "prior_invoiced_total": str(po_balance.prior_invoiced_total),
                    "remaining_total": str(po_balance.remaining_total),
                    "prior_invoice_count": prior_count,
                },
            ))

        if header.po_total_match is False and header.total_comparison:
            tc = header.total_comparison
            # Partial invoice context
            partial_note = ""
            details = {
                "invoice_total": str(tc.invoice_value),
                "po_total": str(tc.po_value),
                "difference": str(tc.difference),
                "difference_pct": str(tc.difference_pct),
            }
            if header.is_partial_invoice and po_balance:
                partial_note = (
                    f" (partial invoice: PO total={po_balance.po_total}, "
                    f"prior invoiced={po_balance.prior_invoiced_total}, "
                    f"remaining={po_balance.remaining_total}, "
                    f"prior invoices={po_balance.prior_invoice_count})"
                )
                details["is_partial_invoice"] = True
                details["po_original_total"] = str(po_balance.po_total)
                details["prior_invoiced_total"] = str(po_balance.prior_invoiced_total)
                details["remaining_total"] = str(po_balance.remaining_total)
                details["prior_invoice_count"] = po_balance.prior_invoice_count

            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.AMOUNT_MISMATCH,
                severity=ExceptionSeverity.HIGH,
                message=(
                    f"Total amount mismatch: invoice={tc.invoice_value}, "
                    f"PO remaining={tc.po_value}, diff={tc.difference} ({tc.difference_pct}%)"
                    f"{partial_note}"
                ),
                details=details,
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

        # -- Tax compliance exceptions (GSTIN, country, supply type) --
        if header.gstin_match is False:
            d = header.tax_compliance_details
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.GSTIN_MISMATCH,
                severity=ExceptionSeverity.HIGH,
                message=(
                    f"Vendor GSTIN/Tax-ID mismatch: "
                    f"invoice={d.get('invoice_vendor_tax_id', '?')}, "
                    f"PO={d.get('po_vendor_gstin', '?')}"
                ),
                details=d,
            ))

        if header.country_match is False:
            d = header.tax_compliance_details
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.COUNTRY_MISMATCH,
                severity=ExceptionSeverity.HIGH,
                message=(
                    f"Country mismatch: invoice country (inferred)="
                    f"{d.get('invoice_country_inferred', '?')}, "
                    f"PO country={d.get('po_country', '?')}"
                ),
                details=d,
            ))

        if header.supply_type_match is False:
            d = header.tax_compliance_details
            excs.append(self._make(
                result=result,
                exc_type=ExceptionType.SUPPLY_TYPE_MISMATCH,
                severity=ExceptionSeverity.MEDIUM,
                message=(
                    f"Supply type mismatch: invoice inferred="
                    f"{d.get('invoice_supply_type_inferred', '?')}, "
                    f"PO={d.get('po_supply_type', '?')} "
                    f"(INTRA=CGST+SGST, INTER=IGST)"
                ),
                details=d,
            ))

        return excs
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

            # Tax rate mismatch (component-level rate comparison)
            if pair.tax_rate_match is False and pair.tax_rate_details:
                d = pair.tax_rate_details
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.TAX_RATE_MISMATCH,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Line {pair.invoice_line.line_number}: tax rate mismatch "
                        f"invoice={d.get('invoice_tax_rate', '?')}% vs "
                        f"PO={d.get('po_effective_tax_rate', '?')}% "
                        f"(diff={d.get('rate_difference', '?')}%)"
                    ),
                    details={
                        "line_number": pair.invoice_line.line_number,
                        **d,
                    },
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

        # v2: deterministic scorer-derived exceptions
        for decision in getattr(line_result, "decisions", []):
            rl = result_line_map.get(decision.invoice_line.pk)
            ln = decision.invoice_line.line_number

            if decision.status == "UNRESOLVED" and decision.candidate_count > 0:
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.NO_CONFIDENT_PO_LINE_MATCH,
                    severity=ExceptionSeverity.HIGH,
                    message=(
                        f"Line {ln}: no PO-line candidate reached confidence threshold "
                        f"(best score {decision.best_score:.2f})"
                    ),
                    details={
                        "line_number": ln,
                        "best_score": decision.best_score,
                        "candidate_count": decision.candidate_count,
                        "confidence_band": decision.confidence_band_val,
                    },
                ))

            if decision.is_ambiguous:
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.MULTIPLE_PO_LINE_CANDIDATES,
                    severity=ExceptionSeverity.MEDIUM,
                    message=(
                        f"Line {ln}: ambiguous match -- {decision.candidate_count} candidates, "
                        f"top gap {decision.top_gap:.2f}"
                    ),
                    details={
                        "line_number": ln,
                        "best_score": decision.best_score,
                        "second_best_score": decision.second_best_score,
                        "top_gap": decision.top_gap,
                        "candidate_count": decision.candidate_count,
                    },
                ))

            if (
                decision.status == "MATCHED"
                and decision.confidence_band_val in ("LOW", "MODERATE")
            ):
                excs.append(self._make(
                    result=result,
                    result_line=rl,
                    exc_type=ExceptionType.LINE_MATCH_LOW_CONFIDENCE,
                    severity=ExceptionSeverity.LOW,
                    message=(
                        f"Line {ln}: matched with {decision.confidence_band_val} confidence "
                        f"(score {decision.total_score:.2f})"
                    ),
                    details={
                        "line_number": ln,
                        "total_score": decision.total_score,
                        "confidence_band": decision.confidence_band_val,
                        "match_method": decision.match_method,
                        "matched_signals": decision.matched_signals,
                    },
                ))

        return excs

    # ------------------------------------------------------------------
    # GRN exceptions
    # ------------------------------------------------------------------
    def _grn_exceptions(
        self, result: ReconciliationResult, grn: GRNMatchResult,
        po_balance: Optional["POBalance"] = None,
    ) -> List[ReconciliationException]:
        excs: List[ReconciliationException] = []
        is_partial = po_balance.is_partial if po_balance else False

        if not grn.grn_available:
            if is_partial:
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.GRN_NOT_FOUND,
                    severity=ExceptionSeverity.LOW,
                    message=(
                        "No goods receipt notes found for this PO. "
                        "This is a partial invoice -- GRN may arrive separately."
                    ),
                ))
            else:
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.GRN_NOT_FOUND,
                    severity=ExceptionSeverity.MEDIUM,
                    message="No goods receipt notes found for this PO",
                ))
            return excs

        for cmp in grn.line_comparisons:
            # Receipt-availability overbilling check (most specific, takes priority)
            if cmp.invoiced_exceeds_available:
                excs.append(self._make(
                    result=result,
                    exc_type=ExceptionType.INVOICE_QTY_EXCEEDS_AVAILABLE,
                    severity=ExceptionSeverity.HIGH,
                    message=(
                        f"Invoiced quantity ({cmp.qty_invoiced}) exceeds available "
                        f"receipt ({cmp.available_qty}) for PO line {cmp.po_line_id} "
                        f"(received={cmp.cumulative_received_qty}, "
                        f"prior consumed={cmp.previously_consumed_qty})"
                    ),
                    details={
                        "po_line_id": cmp.po_line_id,
                        "qty_invoiced": str(cmp.qty_invoiced),
                        "cumulative_received_qty": str(cmp.cumulative_received_qty),
                        "previously_consumed_qty": str(cmp.previously_consumed_qty),
                        "available_qty": str(cmp.available_qty),
                        "qty_received": str(cmp.qty_received),
                        "contributing_grn_line_ids": cmp.contributing_grn_line_ids,
                    },
                ))
            elif cmp.invoiced_exceeds_received:
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
            tenant=result.tenant,
        )
