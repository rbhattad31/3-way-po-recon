"""ProcurementRequest/Attribute helper services.

Agent-first compatible: these helpers manage persistence only.
Decision-making remains in agents/orchestrators.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional

from apps.core.enums import (
    AttributeDataType,
    ExtractionSourceType,
    ProcurementRequestStatus,
)
from apps.procurement.models import ProcurementRequest, ProcurementRequestAttribute


class AttributeService:
    """Attribute persistence and normalization helpers."""

    @staticmethod
    def bulk_set_attributes(proc_request: ProcurementRequest, attributes: Iterable[Dict[str, Any]]) -> None:
        for row in attributes or []:
            code = str(row.get("attribute_code", "") or "").strip()
            if not code:
                continue

            label = str(row.get("attribute_label", code) or code)
            data_type = str(row.get("data_type", AttributeDataType.TEXT) or AttributeDataType.TEXT)
            value_text = str(row.get("value_text", "") or "")
            value_json = row.get("value_json", None)
            is_required = bool(row.get("is_required", False))

            value_number = None
            raw_number = row.get("value_number", None)
            if raw_number not in (None, ""):
                try:
                    value_number = Decimal(str(raw_number))
                except (InvalidOperation, TypeError, ValueError):
                    value_number = None

            normalized_value = value_text.strip().upper() if value_text else ""
            if value_number is not None:
                normalized_value = str(value_number)

            defaults = {
                "tenant": proc_request.tenant,
                "attribute_label": label,
                "data_type": data_type,
                "value_text": value_text,
                "value_number": value_number,
                "value_json": value_json,
                "is_required": is_required,
                "normalized_value": normalized_value,
                "extraction_source": row.get("extraction_source", ExtractionSourceType.MANUAL),
                "confidence_score": row.get("confidence_score", None),
            }

            ProcurementRequestAttribute.objects.update_or_create(
                request=proc_request,
                attribute_code=code,
                defaults=defaults,
            )

    @staticmethod
    def get_attributes_dict(proc_request: ProcurementRequest) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {}

        for item in proc_request.attributes.all():
            if item.value_number is not None:
                attrs[item.attribute_code] = float(item.value_number)
            elif item.value_text:
                attrs[item.attribute_code] = item.value_text
            elif item.value_json is not None:
                attrs[item.attribute_code] = item.value_json

        if proc_request.geography_country and "country" not in attrs:
            attrs["country"] = proc_request.geography_country
        if proc_request.geography_city and "city" not in attrs:
            attrs["city"] = proc_request.geography_city

        return attrs


class ProcurementRequestService:
    """Request CRUD helpers with light validation."""

    @staticmethod
    def create_request(
        *,
        title: str,
        description: str,
        domain_code: str,
        schema_code: str,
        request_type: str,
        priority: str = "MEDIUM",
        geography_country: str = "",
        geography_city: str = "",
        currency: str = "USD",
        created_by=None,
        tenant=None,
        attributes: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> ProcurementRequest:
        proc_request = ProcurementRequest.objects.create(
            tenant=tenant or getattr(created_by, "company", None),
            title=title,
            description=description or "",
            domain_code=domain_code,
            schema_code=schema_code or "",
            request_type=request_type,
            status=ProcurementRequestStatus.PENDING_RFQ,
            priority=priority or "MEDIUM",
            geography_country=geography_country or "",
            geography_city=geography_city or "",
            currency=currency or "USD",
            created_by=created_by,
        )

        if attributes:
            AttributeService.bulk_set_attributes(proc_request, attributes)

        return proc_request

    @staticmethod
    def update_status(proc_request: ProcurementRequest, status_value: str, user=None) -> ProcurementRequest:
        proc_request.status = status_value
        proc_request.save(update_fields=["status", "updated_at"])
        return proc_request

    @staticmethod
    def mark_pending_rfq(proc_request: ProcurementRequest, user=None) -> ProcurementRequest:
        return ProcurementRequestService.update_status(
            proc_request,
            ProcurementRequestStatus.PENDING_RFQ,
            user=user,
        )

    @staticmethod
    def mark_ready_rfq(proc_request: ProcurementRequest, user=None) -> ProcurementRequest:
        return ProcurementRequestService.update_status(
            proc_request,
            ProcurementRequestStatus.READY_RFQ,
            user=user,
        )

    @staticmethod
    def mark_ready(proc_request: ProcurementRequest, user=None) -> ProcurementRequest:
        required_missing = []
        for attr in proc_request.attributes.filter(is_required=True):
            has_value = bool(attr.value_text or attr.value_number is not None or attr.value_json not in (None, {}, []))
            if not has_value:
                required_missing.append(attr.attribute_label or attr.attribute_code)

        if required_missing:
            raise ValueError("Missing required attributes: " + ", ".join(required_missing))

        return ProcurementRequestService.mark_pending_rfq(proc_request, user=user)
