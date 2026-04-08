"""QuotationService — manage supplier quotations and line items."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.db import transaction

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import ExtractionStatus
from apps.core.trace import TraceContext
from apps.procurement.models import (
    ProcurementRequest,
    QuotationLineItem,
    SupplierQuotation,
)

logger = logging.getLogger(__name__)


class QuotationService:
    """Manage supplier quotation lifecycle."""

    @staticmethod
    @observed_service("procurement.quotation.create")
    def create_quotation(
        *,
        request: ProcurementRequest,
        vendor_name: str,
        quotation_number: str = "",
        quotation_date=None,
        total_amount=None,
        currency: str = "USD",
        uploaded_document=None,
        created_by=None,
        tenant=None,
    ) -> SupplierQuotation:
        quotation = SupplierQuotation.objects.create(
            request=request,
            vendor_name=vendor_name,
            quotation_number=quotation_number,
            quotation_date=quotation_date,
            total_amount=total_amount,
            currency=currency,
            uploaded_document=uploaded_document,
            extraction_status=ExtractionStatus.PENDING,
            created_by=created_by,
            tenant=tenant,
        )
        AuditService.log_event(
            entity_type="SupplierQuotation",
            entity_id=quotation.pk,
            event_type="QUOTATION_UPLOADED",
            description=f"Quotation from {vendor_name} uploaded",
            user=created_by,
            trace_ctx=TraceContext.get_current(),
        )
        return quotation

    @staticmethod
    def add_line_items(
        quotation: SupplierQuotation,
        items: List[Dict[str, Any]],
    ) -> List[QuotationLineItem]:
        """Bulk-create line items for a quotation."""
        objs = []
        for idx, item in enumerate(items, start=1):
            objs.append(QuotationLineItem(
                quotation=quotation,
                line_number=item.get("line_number", idx),
                description=item["description"],
                normalized_description=item.get("normalized_description", ""),
                category_code=item.get("category_code", ""),
                quantity=item.get("quantity", 1),
                unit=item.get("unit", "EA"),
                unit_rate=item["unit_rate"],
                total_amount=item["total_amount"],
                brand=item.get("brand", ""),
                model=item.get("model", ""),
                extraction_confidence=item.get("extraction_confidence"),
            ))
        return QuotationLineItem.objects.bulk_create(objs)

    @staticmethod
    def update_extraction_status(
        quotation: SupplierQuotation,
        status: str,
        confidence: float | None = None,
    ) -> SupplierQuotation:
        quotation.extraction_status = status
        if confidence is not None:
            quotation.extraction_confidence = confidence
        quotation.save(update_fields=["extraction_status", "extraction_confidence", "updated_at"])
        return quotation


class LineItemNormalizationService:
    """Normalize quotation line item descriptions and categories."""

    @staticmethod
    def normalize_line_items(quotation: SupplierQuotation) -> int:
        """Normalize all line items in a quotation. Returns count normalized."""
        items = quotation.line_items.all()
        count = 0
        for item in items:
            normalized = LineItemNormalizationService._normalize_description(item.description)
            if normalized != item.normalized_description:
                item.normalized_description = normalized
                item.save(update_fields=["normalized_description", "updated_at"])
                count += 1
        return count

    @staticmethod
    def _normalize_description(description: str) -> str:
        """Basic normalization — lowercase, strip whitespace, collapse spaces."""
        import re
        text = description.strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text
