"""Duplicate detection service — checks if an invoice has already been processed."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from apps.documents.models import Invoice
from apps.extraction.services.normalization_service import NormalizedInvoice

from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool = False
    duplicate_invoice_id: Optional[int] = None
    reason: str = ""


class DuplicateDetectionService:
    """Detect potential duplicate invoices using normalised fields."""

    @observed_service("extraction.duplicate_check", entity_type="Invoice")
    def check(self, inv: NormalizedInvoice, exclude_invoice_id: Optional[int] = None) -> DuplicateCheckResult:
        """Return a DuplicateCheckResult.

        Checks:
        1. Exact normalised invoice number + vendor name match.
        2. Exact normalised invoice number + amount match.
        """
        if not inv.normalized_invoice_number:
            return DuplicateCheckResult()

        qs = Invoice.objects.filter(
            normalized_invoice_number=inv.normalized_invoice_number,
        ).exclude(is_duplicate=True)

        if exclude_invoice_id:
            qs = qs.exclude(pk=exclude_invoice_id)

        # Check 1: same invoice number + normalised vendor
        if inv.vendor_name_normalized:
            match = qs.filter(
                vendor__normalized_name=inv.vendor_name_normalized,
            ).first()
            if match:
                logger.warning(
                    "Duplicate detected: inv_num=%s vendor=%s existing_id=%s",
                    inv.normalized_invoice_number, inv.vendor_name_normalized, match.pk,
                )
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_invoice_id=match.pk,
                    reason=f"Same invoice number and vendor (existing Invoice #{match.pk})",
                )

        # Check 2: same invoice number + same total
        if inv.total_amount is not None:
            match = qs.filter(total_amount=inv.total_amount).first()
            if match:
                logger.warning(
                    "Duplicate detected: inv_num=%s amount=%s existing_id=%s",
                    inv.normalized_invoice_number, inv.total_amount, match.pk,
                )
                return DuplicateCheckResult(
                    is_duplicate=True,
                    duplicate_invoice_id=match.pk,
                    reason=f"Same invoice number and total amount (existing Invoice #{match.pk})",
                )

        return DuplicateCheckResult()
