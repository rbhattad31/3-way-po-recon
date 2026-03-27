"""Duplicate Invoice DB fallback — checks local Invoice model for potential duplicates."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.conf import settings

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


class DuplicateInvoiceDBFallback:
    """DB fallback for duplicate invoice checks using the documents.Invoice model.

    This is a local-only check — the ERP may have additional posted invoices
    not present in the platform database. Confidence is capped at the
    ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD setting.
    """

    @staticmethod
    def lookup(
        invoice_number: str = "",
        vendor_code: str = "",
        fiscal_year: str = "",
        exclude_invoice_id: int | None = None,
        **kwargs,
    ) -> ERPResolutionResult:
        """Check for duplicate invoices in local database."""
        from apps.documents.models import Invoice

        if not invoice_number:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="invoice_number is required for duplicate check",
            )

        qs = Invoice.objects.filter(invoice_number=invoice_number)
        if exclude_invoice_id:
            qs = qs.exclude(pk=exclude_invoice_id)
        if vendor_code:
            qs = qs.filter(
                vendor__code=vendor_code
            ) | qs.filter(
                raw_vendor_name__icontains=vendor_code
            )
        if fiscal_year:
            qs = qs.filter(invoice_date__year=fiscal_year)

        duplicates = list(qs[:5])
        threshold = getattr(
            settings, "ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD", 0.8
        )

        if duplicates:
            dup_info = [
                {
                    "invoice_id": inv.pk,
                    "invoice_number": inv.invoice_number,
                    "vendor_name": inv.raw_vendor_name or "",
                    "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
                    "total_amount": str(inv.total_amount) if inv.total_amount else None,
                    "status": inv.status,
                }
                for inv in duplicates
            ]
            return ERPResolutionResult(
                resolved=True,
                value={
                    "is_duplicate": True,
                    "duplicate_count": len(duplicates),
                    "duplicates": dup_info,
                },
                source_type=ERPSourceType.DB_FALLBACK,
                confidence=threshold,
                reason=f"Found {len(duplicates)} potential duplicate(s) in local DB "
                       f"(confidence capped at {threshold})",
            )

        return ERPResolutionResult(
            resolved=True,
            value={"is_duplicate": False, "duplicate_count": 0, "duplicates": []},
            source_type=ERPSourceType.DB_FALLBACK,
            confidence=threshold,
            reason="No duplicates found in local DB (ERP not checked)",
        )
