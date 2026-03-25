"""Tax DB fallback — wraps existing posting_core ERPTaxCodeReference lookups."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


class TaxDBFallback:
    """DB fallback for tax code lookups using posting_core ERPTaxCodeReference."""

    @staticmethod
    def lookup(tax_code: str = "", rate: float = 0.0, **kwargs) -> ERPResolutionResult:
        """Look up tax code from imported ERP reference tables.

        Precedence: exact code → rate match (with tolerance).
        """
        from apps.core.enums import ERPReferenceBatchStatus, ERPReferenceBatchType
        from apps.posting_core.models import ERPReferenceImportBatch, ERPTaxCodeReference

        batch = (
            ERPReferenceImportBatch.objects
            .filter(batch_type=ERPReferenceBatchType.TAX, status=ERPReferenceBatchStatus.COMPLETED)
            .order_by("-imported_at")
            .first()
        )
        if not batch:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="No completed tax reference batch available",
            )

        # 1. Exact code
        if tax_code:
            ref = ERPTaxCodeReference.objects.filter(
                batch=batch, tax_code=tax_code, is_active=True
            ).first()
            if ref:
                return _tax_result(ref, "Exact code match", 1.0)

        # 2. Rate match with tolerance
        if rate:
            tolerance = Decimal("0.005")
            rate_dec = Decimal(str(round(rate, 4)))
            ref = ERPTaxCodeReference.objects.filter(
                batch=batch,
                rate__gte=rate_dec - tolerance,
                rate__lte=rate_dec + tolerance,
                is_active=True,
            ).first()
            if ref:
                return _tax_result(ref, f"Rate match: {rate}", 0.85)

        return ERPResolutionResult(
            resolved=False,
            source_type=ERPSourceType.DB_FALLBACK,
            reason=f"Tax code not found in DB: code='{tax_code}', rate={rate}",
        )


def _tax_result(ref, reason: str, confidence: float) -> ERPResolutionResult:
    return ERPResolutionResult(
        resolved=True,
        value={
            "tax_code": ref.tax_code,
            "description": ref.description,
            "rate": str(ref.rate) if ref.rate else None,
        },
        source_type=ERPSourceType.DB_FALLBACK,
        confidence=confidence,
        reason=reason,
    )
