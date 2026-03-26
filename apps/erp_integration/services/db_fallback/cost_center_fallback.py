"""Cost Center DB fallback — wraps existing posting_core ERPCostCenterReference lookups."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


class CostCenterDBFallback:
    """DB fallback for cost center lookups using posting_core ERPCostCenterReference."""

    @staticmethod
    def lookup(cost_center_code: str = "", **kwargs) -> ERPResolutionResult:
        """Look up cost center from imported ERP reference tables."""
        from apps.core.enums import ERPReferenceBatchStatus, ERPReferenceBatchType
        from apps.posting_core.models import ERPCostCenterReference, ERPReferenceImportBatch

        if not cost_center_code:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="cost_center_code is required",
            )

        batch = (
            ERPReferenceImportBatch.objects
            .filter(batch_type=ERPReferenceBatchType.COST_CENTER, status=ERPReferenceBatchStatus.COMPLETED)
            .order_by("-imported_at")
            .first()
        )
        if not batch:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="No completed cost center reference batch available",
            )

        ref = ERPCostCenterReference.objects.filter(
            batch=batch, cost_center_code=cost_center_code, is_active=True
        ).first()
        if ref:
            return ERPResolutionResult(
                resolved=True,
                value={
                    "cost_center_code": ref.cost_center_code,
                    "description": ref.description,
                    "company_code": getattr(ref, "company_code", ""),
                },
                source_type=ERPSourceType.DB_FALLBACK,
                confidence=1.0,
                reason=f"Exact code match: {ref.cost_center_code}",
            )

        return ERPResolutionResult(
            resolved=False,
            source_type=ERPSourceType.DB_FALLBACK,
            reason=f"Cost center '{cost_center_code}' not found in DB",
        )
