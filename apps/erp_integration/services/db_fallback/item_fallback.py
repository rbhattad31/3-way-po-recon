"""Item DB fallback — wraps existing posting_core ERPItemReference lookups."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    if not text:
        return ""
    t = str(text).strip().lower()
    return re.sub(r"\s+", " ", t)


class ItemDBFallback:
    """DB fallback for item lookups using posting_core ERPItemReference."""

    @staticmethod
    def lookup(item_code: str = "", description: str = "", **kwargs) -> ERPResolutionResult:
        """Look up item from imported ERP reference tables.

        Precedence: exact code → alias → name match.
        """
        from apps.core.enums import ERPReferenceBatchStatus, ERPReferenceBatchType
        from apps.posting_core.models import (
            ERPItemReference,
            ERPReferenceImportBatch,
            ItemAliasMapping,
        )

        batch = (
            ERPReferenceImportBatch.objects
            .filter(batch_type=ERPReferenceBatchType.ITEM, status=ERPReferenceBatchStatus.COMPLETED)
            .order_by("-imported_at")
            .first()
        )
        if not batch:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="No completed item reference batch available",
            )

        # 1. Exact code match
        if item_code:
            ref = ERPItemReference.objects.filter(
                batch=batch, item_code=item_code, is_active=True
            ).first()
            if ref:
                return _item_result(ref, "Exact code match", 1.0)

        # 2. Alias mapping
        norm_desc = _normalize(description)
        if norm_desc:
            alias = (
                ItemAliasMapping.objects
                .filter(normalized_alias=norm_desc, is_active=True)
                .select_related("item_reference")
                .first()
            )
            if alias and alias.item_reference:
                return _item_result(
                    alias.item_reference,
                    f"Alias match: '{alias.alias_text}'",
                    alias.confidence,
                )

            # 3. Normalized name match
            ref = ERPItemReference.objects.filter(
                batch=batch, normalized_item_name=norm_desc, is_active=True
            ).first()
            if ref:
                return _item_result(ref, "Name match", 0.7)

        return ERPResolutionResult(
            resolved=False,
            source_type=ERPSourceType.DB_FALLBACK,
            reason=f"Item not found in DB: code='{item_code}', desc='{description[:80]}'",
        )


def _item_result(ref, reason: str, confidence: float) -> ERPResolutionResult:
    return ERPResolutionResult(
        resolved=True,
        value={
            "item_code": ref.item_code,
            "item_name": ref.item_name,
            "category": ref.category,
            "item_type": ref.item_type,
            "uom": ref.uom,
            "tax_code": ref.tax_code,
        },
        source_type=ERPSourceType.DB_FALLBACK,
        confidence=confidence,
        reason=reason,
    )
