"""Vendor DB fallback — wraps existing posting_core ERPVendorReference lookups."""
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


class VendorDBFallback:
    """DB fallback for vendor lookups using posting_core ERPVendorReference."""

    @staticmethod
    def lookup(vendor_code: str = "", vendor_name: str = "", **kwargs) -> ERPResolutionResult:
        """Look up vendor from imported ERP reference tables.

        Precedence: exact code → alias → exact name → partial name.
        """
        from apps.core.enums import ERPReferenceBatchStatus, ERPReferenceBatchType
        from apps.posting_core.models import (
            ERPReferenceImportBatch,
            ERPVendorReference,
            VendorAliasMapping,
        )

        batch = (
            ERPReferenceImportBatch.objects
            .filter(batch_type=ERPReferenceBatchType.VENDOR, status=ERPReferenceBatchStatus.COMPLETED)
            .order_by("-imported_at")
            .first()
        )
        if not batch:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="No completed vendor reference batch available",
            )

        # 1. Exact code match
        if vendor_code:
            ref = ERPVendorReference.objects.filter(
                batch=batch, vendor_code=vendor_code, is_active=True
            ).first()
            if ref:
                return _vendor_result(ref, "Exact code match", 1.0)

        # 2. Alias mapping
        norm_name = _normalize(vendor_name)
        if norm_name:
            alias = (
                VendorAliasMapping.objects
                .filter(normalized_alias=norm_name, is_active=True)
                .select_related("vendor_reference")
                .first()
            )
            if alias and alias.vendor_reference:
                return _vendor_result(
                    alias.vendor_reference,
                    f"Alias match: '{alias.alias_text}'",
                    alias.confidence,
                )

            # 3. Exact normalized name
            ref = ERPVendorReference.objects.filter(
                batch=batch, normalized_vendor_name=norm_name, is_active=True
            ).first()
            if ref:
                return _vendor_result(ref, "Exact name match", 0.9)

            # 4. Partial / contains
            if len(norm_name) >= 3:
                ref = ERPVendorReference.objects.filter(
                    batch=batch, normalized_vendor_name__icontains=norm_name, is_active=True
                ).first()
                if ref:
                    return _vendor_result(ref, f"Partial match: '{vendor_name}'", 0.6)

        return ERPResolutionResult(
            resolved=False,
            source_type=ERPSourceType.DB_FALLBACK,
            reason=f"Vendor not found in DB: code='{vendor_code}', name='{vendor_name}'",
        )


def _vendor_result(ref, reason: str, confidence: float) -> ERPResolutionResult:
    return ERPResolutionResult(
        resolved=True,
        value={
            "vendor_code": ref.vendor_code,
            "vendor_name": ref.vendor_name,
            "vendor_id": ref.pk,
        },
        source_type=ERPSourceType.DB_FALLBACK,
        confidence=confidence,
        reason=reason,
    )
