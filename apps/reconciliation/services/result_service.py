"""Result persistence service — writes ReconciliationResult + ResultLine + Exception rows."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, List, Optional

from django.db import transaction

from apps.core.enums import MatchStatus, ReconciliationMode
from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.models import (
    ReconciliationException,
    ReconciliationResult,
    ReconciliationResultLine,
    ReconciliationRun,
)
from apps.reconciliation.services.grn_match_service import GRNMatchResult
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchPair, LineMatchResult
from apps.reconciliation.services.mode_resolver import ModeResolutionResult
from apps.reconciliation.services.po_lookup_service import POLookupResult

logger = logging.getLogger(__name__)


class ReconciliationResultService:
    """Persist all reconciliation comparison data into the DB."""

    @transaction.atomic
    def save(
        self,
        run: ReconciliationRun,
        invoice: Invoice,
        match_status: MatchStatus,
        po_result: POLookupResult,
        header_result: Optional[HeaderMatchResult],
        line_result: Optional[LineMatchResult],
        grn_result: Optional[GRNMatchResult],
        exceptions: Optional[List[ReconciliationException]] = None,
        reconciliation_mode: str = "",
        mode_resolution: Optional[ModeResolutionResult] = None,
    ) -> ReconciliationResult:
        po: Optional[PurchaseOrder] = po_result.purchase_order if po_result.found else None
        is_two_way = reconciliation_mode == ReconciliationMode.TWO_WAY

        # Header-level evidence
        tc = header_result.total_comparison if header_result and header_result.total_comparison else None

        result = ReconciliationResult.objects.create(
            run=run,
            invoice=invoice,
            purchase_order=po,
            match_status=match_status,
            requires_review=match_status in (
                MatchStatus.PARTIAL_MATCH,
                MatchStatus.UNMATCHED,
                MatchStatus.REQUIRES_REVIEW,
            ),
            vendor_match=header_result.vendor_match if header_result else None,
            currency_match=header_result.currency_match if header_result else None,
            po_total_match=header_result.po_total_match if header_result else None,
            invoice_total_vs_po=tc.difference if tc else None,
            total_amount_difference=tc.difference if tc else None,
            total_amount_difference_pct=tc.difference_pct if tc else None,
            grn_available=grn_result.grn_available if grn_result else False,
            grn_fully_received=grn_result.fully_received if grn_result else None,
            extraction_confidence=invoice.extraction_confidence,
            deterministic_confidence=self._compute_confidence(
                header_result, line_result, grn_result, reconciliation_mode,
            ),
            summary=self._build_summary(match_status, header_result, line_result, grn_result, reconciliation_mode),
            # Mode metadata
            reconciliation_mode=reconciliation_mode,
            grn_required_flag=mode_resolution.grn_required if mode_resolution else (not is_two_way),
            grn_checked_flag=grn_result is not None,
            mode_resolution_reason=mode_resolution.reason if mode_resolution else "",
            policy_applied=mode_resolution.policy_code if mode_resolution else "",
            is_two_way_result=is_two_way,
            is_three_way_result=not is_two_way,
            # ERP source provenance
            po_erp_source_type=getattr(po_result, "erp_source_type", ""),
            grn_erp_source_type=getattr(grn_result, "erp_source_type", ""),
            data_is_stale=(
                getattr(po_result, "is_stale", False)
                or getattr(grn_result, "is_stale", False)
            ),
            erp_source_metadata_json={
                "po": getattr(po_result, "erp_provenance", {}),
                "grn": getattr(grn_result, "erp_provenance", {}),
            },
        )

        # Line-level results
        result_line_map: Dict[int, ReconciliationResultLine] = {}
        if line_result:
            result_line_map = self._save_line_results(result, line_result)

        # Exceptions
        if exceptions:
            # Attach result_line references where possible
            for exc in exceptions:
                exc.result = result
            ReconciliationException.objects.bulk_create(exceptions)

        logger.info(
            "Saved ReconciliationResult %s: match_status=%s, %d line results, %d exceptions",
            result.pk, match_status, len(result_line_map), len(exceptions or []),
        )
        return result

    # ------------------------------------------------------------------
    # Line results
    # ------------------------------------------------------------------
    def _save_line_results(
        self, result: ReconciliationResult, line_result: LineMatchResult
    ) -> Dict[int, ReconciliationResultLine]:
        """Create ReconciliationResultLine rows. Returns {inv_line_id: result_line}."""
        objs: List[ReconciliationResultLine] = []

        for pair in line_result.pairs:
            rl = self._line_from_pair(result, pair)
            objs.append(rl)

        # Unmatched invoice lines not already covered
        covered = {p.invoice_line.pk for p in line_result.pairs}
        for inv_line in line_result.unmatched_invoice_lines:
            if inv_line.pk not in covered:
                objs.append(ReconciliationResultLine(
                    result=result,
                    invoice_line=inv_line,
                    match_status=MatchStatus.UNMATCHED,
                ))

        created = ReconciliationResultLine.objects.bulk_create(objs)

        return {
            rl.invoice_line_id: rl
            for rl in created
            if rl.invoice_line_id
        }

    @staticmethod
    def _line_from_pair(
        result: ReconciliationResult, pair: LineMatchPair
    ) -> ReconciliationResultLine:
        status = MatchStatus.MATCHED if pair.matched else MatchStatus.UNMATCHED

        # Determine if partial (matched but tolerance breaches)
        if pair.matched:
            tolerance_ok = all([
                (pair.qty_comparison and pair.qty_comparison.within_tolerance is True),
                (pair.price_comparison and pair.price_comparison.within_tolerance is True),
                (pair.amount_comparison and pair.amount_comparison.within_tolerance is True),
            ])
            if not tolerance_ok:
                status = MatchStatus.PARTIAL_MATCH

        rl = ReconciliationResultLine(
            result=result,
            invoice_line=pair.invoice_line,
            po_line=pair.po_line,
            match_status=status,
            description_similarity=pair.description_similarity,
        )

        # Qty
        if pair.qty_comparison:
            rl.qty_invoice = pair.qty_comparison.invoice_value
            rl.qty_po = pair.qty_comparison.po_value
            rl.qty_difference = pair.qty_comparison.difference
            rl.qty_within_tolerance = pair.qty_comparison.within_tolerance

        # Price
        if pair.price_comparison:
            rl.price_invoice = pair.price_comparison.invoice_value
            rl.price_po = pair.price_comparison.po_value
            rl.price_difference = pair.price_comparison.difference
            rl.price_within_tolerance = pair.price_comparison.within_tolerance

        # Amount
        if pair.amount_comparison:
            rl.amount_invoice = pair.amount_comparison.invoice_value
            rl.amount_po = pair.amount_comparison.po_value
            rl.amount_difference = pair.amount_comparison.difference
            rl.amount_within_tolerance = pair.amount_comparison.within_tolerance

        # Tax
        rl.tax_invoice = pair.tax_invoice
        rl.tax_po = pair.tax_po
        rl.tax_difference = pair.tax_difference

        return rl

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_confidence(
        header: Optional[HeaderMatchResult],
        lines: Optional[LineMatchResult],
        grn: Optional[GRNMatchResult],
        reconciliation_mode: str = "",
    ) -> float:
        """Compute a 0–1 deterministic confidence from comparison evidence."""
        is_two_way = reconciliation_mode == ReconciliationMode.TWO_WAY
        score = 0.0
        weight = 0.0

        if header:
            header_weight = 0.45 if is_two_way else 0.40
            weight += header_weight
            header_score = 0.0
            checks = [header.vendor_match, header.currency_match, header.po_total_match]
            total = sum(1 for c in checks if c is not None)
            passed = sum(1 for c in checks if c is True)
            if total:
                header_score = passed / total
            score += header_weight * header_score

        if lines:
            line_weight = 0.55 if is_two_way else 0.45
            weight += line_weight
            if lines.all_lines_matched and lines.all_within_tolerance:
                score += line_weight
            elif lines.all_lines_matched:
                score += line_weight * 0.667
            elif lines.pairs:
                matched_ratio = sum(1 for p in lines.pairs if p.matched) / len(lines.pairs)
                score += line_weight * matched_ratio * 0.5

        # GRN weight only applies in 3-way mode
        if not is_two_way and grn:
            weight += 0.15
            if grn.grn_available and not grn.has_receipt_issues:
                score += 0.15
            elif grn.grn_available:
                score += 0.05

        return round(score / weight, 4) if weight else 0.0

    @staticmethod
    def _build_summary(
        status: MatchStatus,
        header: Optional[HeaderMatchResult],
        lines: Optional[LineMatchResult],
        grn: Optional[GRNMatchResult],
        reconciliation_mode: str = "",
    ) -> str:
        is_two_way = reconciliation_mode == ReconciliationMode.TWO_WAY
        mode_label = "2-Way" if is_two_way else "3-Way"
        parts = [f"Mode: {mode_label}", f"Status: {status}"]
        if header:
            parts.append(
                f"Header: vendor={'OK' if header.vendor_match else 'MISMATCH'}, "
                f"currency={'OK' if header.currency_match else 'MISMATCH'}, "
                f"total={'OK' if header.po_total_match else 'MISMATCH'}"
            )
        if lines:
            matched_cnt = sum(1 for p in lines.pairs if p.matched)
            parts.append(
                f"Lines: {matched_cnt}/{len(lines.pairs)} matched, "
                f"{len(lines.unmatched_invoice_lines)} unmatched inv, "
                f"{len(lines.unmatched_po_lines)} unmatched PO"
            )
        if grn and not is_two_way:
            parts.append(
                f"GRN: available={grn.grn_available}, "
                f"fully_received={grn.fully_received}, "
                f"issues={grn.has_receipt_issues}"
            )
        elif is_two_way:
            parts.append("GRN: skipped (2-way mode)")
        return " | ".join(parts)
