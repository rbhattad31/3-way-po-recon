"""Quotation helper service.

Persistence helper only; extraction/prefill logic remains agent-driven.
"""
from __future__ import annotations

from apps.core.enums import ExtractionStatus, PrefillStatus
from apps.procurement.models import SupplierQuotation


class QuotationService:
    """Create quotation records for procurement requests."""

    @staticmethod
    def create_quotation(
        *,
        request,
        quotation_number: str = "",
        total_amount=None,
        currency: str = "USD",
        created_by=None,
        vendor_name: str = "",
    ) -> SupplierQuotation:
        return SupplierQuotation.objects.create(
            tenant=request.tenant,
            request=request,
            vendor_name=vendor_name or "Unknown Vendor",
            quotation_number=quotation_number or "",
            total_amount=total_amount,
            currency=currency or request.currency or "USD",
            extraction_status=ExtractionStatus.PENDING,
            prefill_status=PrefillStatus.NOT_STARTED,
        )
