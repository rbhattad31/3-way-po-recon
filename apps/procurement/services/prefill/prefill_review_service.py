"""PrefillReviewService — accept user-reviewed prefill data and persist final values."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction

from apps.core.decorators import observed_service
from apps.core.enums import (
    ExtractionSourceType,
    ExtractionStatus,
    PrefillStatus,
    ProcurementRequestStatus,
)
from apps.procurement.models import (
    ProcurementRequest,
    ProcurementRequestAttribute,
    QuotationLineItem,
    SupplierQuotation,
)

logger = logging.getLogger(__name__)


class PrefillReviewService:
    """Accept user-edited prefill data, persist final request/quotation data."""

    @staticmethod
    @observed_service("procurement.confirm_request_prefill")
    @transaction.atomic
    def confirm_request_prefill(
        request: ProcurementRequest,
        reviewed_data: dict[str, Any],
    ) -> ProcurementRequest:
        """Persist user-confirmed prefill data onto the ProcurementRequest.

        Args:
            request: The draft ProcurementRequest.
            reviewed_data: User-reviewed payload with:
                - core_fields: {field: value}
                - attributes: [{attribute_code, attribute_label, data_type, value}]
        """
        core = reviewed_data.get("core_fields", {})
        attributes = reviewed_data.get("attributes", [])

        # Track overrides for audit
        overrides_count = 0
        prefill_payload = request.prefill_payload_json or {}
        original_core = prefill_payload.get("core_fields", {})

        # Apply core fields
        core_field_map = {
            "title": "title",
            "description": "description",
            "domain_code": "domain_code",
            "geography_country": "geography_country",
            "geography_city": "geography_city",
            "currency": "currency",
        }
        for field_key, model_attr in core_field_map.items():
            new_val = core.get(field_key)
            if new_val is not None:
                old_val = original_core.get(field_key, {}).get("value", "")
                if str(new_val) != str(old_val):
                    overrides_count += 1
                setattr(request, model_attr, str(new_val).strip())

        # Suggested request_type / schema_code
        if "request_type" in core:
            request.request_type = core["request_type"]
        if "schema_code" in core:
            request.schema_code = core["schema_code"]

        request.prefill_status = PrefillStatus.COMPLETED
        request.status = ProcurementRequestStatus.DRAFT
        request.save()

        # Apply attributes
        for attr_data in attributes:
            code = attr_data.get("attribute_code", "").strip()
            if not code:
                continue

            value = attr_data.get("value", "")
            data_type = attr_data.get("data_type", "TEXT")
            confidence = attr_data.get("confidence")

            value_text = ""
            value_number = None
            value_json = None

            if data_type == "NUMBER":
                try:
                    value_number = Decimal(str(value).replace(",", ""))
                except (InvalidOperation, ValueError):
                    value_text = str(value)
            elif data_type in ("JSON",):
                value_json = value if isinstance(value, (dict, list)) else {"raw": value}
            else:
                value_text = str(value)

            ProcurementRequestAttribute.objects.update_or_create(
                request=request,
                attribute_code=code,
                defaults={
                    "attribute_label": attr_data.get("attribute_label", code.replace("_", " ").title()),
                    "data_type": data_type,
                    "value_text": value_text,
                    "value_number": value_number,
                    "value_json": value_json,
                    "is_required": attr_data.get("is_required", False),
                    "extraction_source": ExtractionSourceType.PREFILL,
                    "confidence_score": confidence,
                },
            )

        # Audit
        PrefillReviewService._log_prefill_confirmed(
            request, "REQUEST", overrides_count, len(attributes),
        )

        logger.info(
            "Request %s: prefill confirmed, %d core overrides, %d attributes saved",
            request.request_id, overrides_count, len(attributes),
        )
        return request

    @staticmethod
    @observed_service("procurement.confirm_quotation_prefill")
    @transaction.atomic
    def confirm_quotation_prefill(
        quotation: SupplierQuotation,
        reviewed_data: dict[str, Any],
    ) -> SupplierQuotation:
        """Persist user-confirmed prefill data onto the SupplierQuotation.

        Args:
            quotation: The draft SupplierQuotation.
            reviewed_data: User-reviewed payload with:
                - header_fields: {field: value}
                - line_items: [{line_number, description, category_code, quantity, unit, unit_rate, total_amount, brand, model}]
        """
        header = reviewed_data.get("header_fields", {})
        line_items = reviewed_data.get("line_items", [])

        overrides_count = 0
        prefill_payload = quotation.prefill_payload_json or {}
        original_header = prefill_payload.get("header_fields", {})

        # Header fields
        header_map = {
            "vendor_name": ("vendor_name", str),
            "quotation_number": ("quotation_number", str),
            "total_amount": ("total_amount", lambda v: Decimal(str(v).replace(",", "")) if v else None),
            "currency": ("currency", str),
        }
        for field_key, (model_attr, converter) in header_map.items():
            new_val = header.get(field_key)
            if new_val is not None:
                old_val = original_header.get(field_key, {}).get("value", "")
                if str(new_val) != str(old_val):
                    overrides_count += 1
                try:
                    setattr(quotation, model_attr, converter(new_val))
                except (InvalidOperation, ValueError, TypeError):
                    pass

        # quotation_date (special handling)
        if "quotation_date" in header:
            from apps.core.utils import parse_date
            parsed = parse_date(header["quotation_date"])
            if parsed:
                quotation.quotation_date = parsed

        quotation.prefill_status = PrefillStatus.COMPLETED
        quotation.extraction_status = ExtractionStatus.COMPLETED
        quotation.save()

        # Clear existing line items and recreate from confirmed data
        QuotationLineItem.objects.filter(quotation=quotation).delete()
        line_objs = []
        for item in line_items:
            line_objs.append(QuotationLineItem(
                quotation=quotation,
                line_number=item.get("line_number", len(line_objs) + 1),
                description=item.get("description", ""),
                normalized_description=str(item.get("description", "")).lower().strip(),
                category_code=item.get("category_code", ""),
                quantity=Decimal(str(item.get("quantity", 1))),
                unit=item.get("unit", "EA"),
                unit_rate=Decimal(str(item.get("unit_rate", 0))),
                total_amount=Decimal(str(item.get("total_amount", 0))),
                brand=item.get("brand", ""),
                model=item.get("model", ""),
                extraction_confidence=item.get("confidence"),
                extraction_source=ExtractionSourceType.PREFILL,
            ))
        if line_objs:
            QuotationLineItem.objects.bulk_create(line_objs)

        PrefillReviewService._log_prefill_confirmed(
            quotation.request, "QUOTATION", overrides_count, len(line_objs),
            quotation_id=quotation.pk,
        )

        logger.info(
            "Quotation %s: prefill confirmed, %d header overrides, %d line items",
            quotation.pk, overrides_count, len(line_objs),
        )
        return quotation

    @staticmethod
    def _log_prefill_confirmed(
        request: ProcurementRequest,
        entity_type: str,
        overrides_count: int,
        item_count: int,
        *,
        quotation_id: int | None = None,
    ) -> None:
        """Log a PREFILL_CONFIRMED audit event."""
        try:
            from apps.auditlog.models import AuditEvent
            AuditEvent.objects.create(
                entity_type="ProcurementRequest",
                entity_id=request.pk,
                action="prefill_confirmed",
                event_type="PREFILL_CONFIRMED",
                event_description=(
                    f"{entity_type} prefill confirmed for request {request.request_id}"
                ),
                metadata_json={
                    "entity_type": entity_type,
                    "request_id": str(request.request_id),
                    "quotation_id": quotation_id,
                    "overrides_count": overrides_count,
                    "item_count": item_count,
                },
                actor_email=getattr(request, "_confirmed_by_email", ""),
            )
        except Exception:
            logger.debug("Could not log PREFILL_CONFIRMED audit event", exc_info=True)
