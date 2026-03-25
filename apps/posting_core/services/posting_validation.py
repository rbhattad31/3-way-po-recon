"""Posting Validation — validates the posting proposal for completeness and consistency."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from apps.core.enums import ERPReferenceBatchStatus, ERPReferenceBatchType, PostingIssueSeverity
from apps.posting_core.models import ERPReferenceImportBatch
from apps.posting_core.services.posting_mapping_engine import PostingProposal

logger = logging.getLogger(__name__)

# Default freshness threshold in hours
REFERENCE_FRESHNESS_HOURS = getattr(settings, "POSTING_REFERENCE_FRESHNESS_HOURS", 168)  # 7 days


class PostingValidationService:
    """Validates posting proposals for completeness and data integrity."""

    @classmethod
    def validate(cls, proposal: PostingProposal, invoice) -> List[Dict[str, Any]]:
        """Run all validation checks on a posting proposal.

        Returns list of issue dicts (same format as proposal.issues).
        """
        issues: List[Dict[str, Any]] = []

        cls._check_vendor_resolved(proposal, issues)
        cls._check_header_completeness(invoice, issues)
        cls._check_lines_exist(proposal, issues)
        cls._check_line_totals(proposal, invoice, issues)
        cls._check_line_completeness(proposal, issues)
        cls._check_reference_freshness(proposal, issues)

        return issues

    @staticmethod
    def _check_vendor_resolved(proposal: PostingProposal, issues: List) -> None:
        if not proposal.header.vendor_code:
            issues.append({
                "severity": PostingIssueSeverity.ERROR,
                "field_code": "vendor_code",
                "check_type": "vendor_required",
                "message": "Vendor code not resolved — cannot post without vendor",
            })

    @staticmethod
    def _check_header_completeness(invoice, issues: List) -> None:
        required = {
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date,
            "currency": invoice.currency,
            "total_amount": invoice.total_amount,
        }
        for field, val in required.items():
            if not val:
                issues.append({
                    "severity": PostingIssueSeverity.ERROR,
                    "field_code": field,
                    "check_type": "header_required",
                    "message": f"Required header field '{field}' is missing or empty",
                })

    @staticmethod
    def _check_lines_exist(proposal: PostingProposal, issues: List) -> None:
        if not proposal.lines:
            issues.append({
                "severity": PostingIssueSeverity.ERROR,
                "field_code": "line_items",
                "check_type": "lines_required",
                "message": "At least one posting line item is required",
            })

    @staticmethod
    def _check_line_totals(proposal: PostingProposal, invoice, issues: List) -> None:
        if not proposal.lines or not invoice.total_amount:
            return
        line_total = sum(
            (lp.line_amount or Decimal("0")) for lp in proposal.lines
        )
        inv_total = invoice.total_amount
        tax = invoice.tax_amount or Decimal("0")
        expected_line_total = inv_total - tax

        if expected_line_total and abs(line_total - expected_line_total) > Decimal("0.05"):
            issues.append({
                "severity": PostingIssueSeverity.WARNING,
                "field_code": "line_amount_total",
                "check_type": "line_total_consistency",
                "message": (
                    f"Line total ({line_total}) differs from invoice subtotal "
                    f"({expected_line_total}) by "
                    f"{abs(line_total - expected_line_total)}"
                ),
            })

    @staticmethod
    def _check_line_completeness(proposal: PostingProposal, issues: List) -> None:
        for lp in proposal.lines:
            if not lp.erp_item_code and lp.confidence < 0.5:
                issues.append({
                    "severity": PostingIssueSeverity.WARNING,
                    "field_code": "item_code",
                    "check_type": "item_mapping_incomplete",
                    "message": f"Line {lp.line_index}: item code unresolved (confidence={lp.confidence:.0%})",
                    "line_item_index": lp.line_index,
                })
            if not lp.tax_code:
                issues.append({
                    "severity": PostingIssueSeverity.INFO,
                    "field_code": "tax_code",
                    "check_type": "tax_code_missing",
                    "message": f"Line {lp.line_index}: tax code not assigned",
                    "line_item_index": lp.line_index,
                })

    @staticmethod
    def _check_reference_freshness(proposal: PostingProposal, issues: List) -> None:
        """Check if reference batches used are fresh enough."""
        threshold = timezone.now() - timedelta(hours=REFERENCE_FRESHNESS_HOURS)

        for batch_type, batch_id in proposal.batch_refs.items():
            try:
                batch = ERPReferenceImportBatch.objects.get(pk=batch_id)
                if batch.imported_at < threshold:
                    days_old = (timezone.now() - batch.imported_at).days
                    issues.append({
                        "severity": PostingIssueSeverity.WARNING,
                        "field_code": f"reference_freshness_{batch_type}",
                        "check_type": "reference_staleness",
                        "message": (
                            f"{batch_type} reference data is {days_old} days old "
                            f"(batch {batch_id}, imported {batch.imported_at.strftime('%Y-%m-%d')})"
                        ),
                        "details_json": {
                            "batch_id": batch_id,
                            "batch_type": batch_type,
                            "imported_at": batch.imported_at.isoformat(),
                            "days_old": days_old,
                        },
                    })
            except ERPReferenceImportBatch.DoesNotExist:
                pass
