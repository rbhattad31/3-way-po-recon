"""Eligibility Service — checks whether an invoice can enter posting.

Only invoices that satisfy ALL conditions may enter the posting flow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from apps.core.enums import InvoicePostingStatus, InvoiceStatus, PostingRunStatus
from apps.documents.models import Invoice
from apps.posting.models import InvoicePosting
from apps.posting_core.models import PostingRun

logger = logging.getLogger(__name__)


@dataclass
class EligibilityResult:
    eligible: bool = False
    reasons: List[str] = field(default_factory=list)


class PostingEligibilityService:
    """Stateless service for posting eligibility checks."""

    @classmethod
    def check(cls, invoice_id: int) -> EligibilityResult:
        """Check eligibility for a given invoice ID."""
        result = EligibilityResult()

        # 1. Invoice exists
        try:
            invoice = Invoice.objects.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            result.reasons.append("Invoice does not exist")
            return result

        # 2. Invoice must be reconciled before posting
        allowed_statuses = {
            InvoiceStatus.RECONCILED,
        }
        if invoice.status not in allowed_statuses:
            result.reasons.append(
                f"Invoice status is {invoice.status}, expected RECONCILED"
            )

        # 3. Not already successfully posted
        existing_posting = InvoicePosting.objects.filter(
            invoice=invoice,
            status=InvoicePostingStatus.POSTED,
        ).exists()
        if existing_posting:
            result.reasons.append("Invoice is already posted")

        # 4. Not duplicate / invalid
        if invoice.is_duplicate:
            result.reasons.append("Invoice is flagged as duplicate")
        if invoice.status == InvoiceStatus.INVALID:
            result.reasons.append("Invoice is marked as invalid")

        # 5. Approved extraction exists
        try:
            from apps.extraction.models import ExtractionApproval
            from apps.core.enums import ExtractionApprovalStatus
            has_approval = ExtractionApproval.objects.filter(
                invoice=invoice,
                status__in=[
                    ExtractionApprovalStatus.APPROVED,
                    ExtractionApprovalStatus.AUTO_APPROVED,
                ],
            ).exists()
            if not has_approval:
                result.reasons.append("No approved extraction exists")
        except Exception:
            result.reasons.append("Could not verify extraction approval")

        # 6. Required fields present
        if not invoice.invoice_number:
            result.reasons.append("Invoice number is missing")
        if not invoice.invoice_date:
            result.reasons.append("Invoice date is missing")
        if not invoice.currency:
            result.reasons.append("Currency is missing")
        if not invoice.total_amount:
            result.reasons.append("Total amount is missing")
        if not (invoice.raw_vendor_name or invoice.vendor_id):
            result.reasons.append("Vendor information is missing")

        # 7. No active RUNNING posting run
        active_run = PostingRun.objects.filter(
            invoice=invoice,
            status=PostingRunStatus.RUNNING,
        ).exists()
        if active_run:
            result.reasons.append("An active posting run is already in progress")

        result.eligible = len(result.reasons) == 0
        return result
