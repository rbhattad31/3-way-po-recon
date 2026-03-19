"""ProcurementRequestService — CRUD and lifecycle for ProcurementRequest."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import ProcurementRequestStatus
from apps.core.trace import TraceContext
from apps.procurement.models import ProcurementRequest, ProcurementRequestAttribute

logger = logging.getLogger(__name__)


class ProcurementRequestService:
    """Stateless service for managing procurement requests."""

    @staticmethod
    @observed_service("procurement.request.create", audit_event="PROCUREMENT_REQUEST_CREATED")
    def create_request(
        *,
        title: str,
        description: str = "",
        domain_code: str,
        schema_code: str = "",
        request_type: str,
        priority: str = "MEDIUM",
        geography_country: str = "",
        geography_city: str = "",
        currency: str = "USD",
        created_by=None,
        assigned_to=None,
        attributes: Optional[List[Dict[str, Any]]] = None,
    ) -> ProcurementRequest:
        ctx = TraceContext.get_current()
        with transaction.atomic():
            request = ProcurementRequest.objects.create(
                title=title,
                description=description,
                domain_code=domain_code,
                schema_code=schema_code,
                request_type=request_type,
                status=ProcurementRequestStatus.DRAFT,
                priority=priority,
                geography_country=geography_country,
                geography_city=geography_city,
                currency=currency,
                created_by=created_by,
                assigned_to=assigned_to,
                trace_id=ctx.trace_id if ctx else "",
            )
            if attributes:
                AttributeService.bulk_set_attributes(request, attributes)

        AuditService.log_event(
            entity_type="ProcurementRequest",
            entity_id=request.pk,
            event_type="PROCUREMENT_REQUEST_CREATED",
            description=f"Procurement request '{title}' created",
            user=created_by,
            trace_ctx=ctx,
            status_after=ProcurementRequestStatus.DRAFT,
        )
        return request

    @staticmethod
    def update_status(request: ProcurementRequest, new_status: str, user=None) -> ProcurementRequest:
        old_status = request.status
        request.status = new_status
        request.updated_by = user
        request.save(update_fields=["status", "updated_by", "updated_at"])

        AuditService.log_event(
            entity_type="ProcurementRequest",
            entity_id=request.pk,
            event_type="PROCUREMENT_REQUEST_STATUS_CHANGED",
            description=f"Status changed from {old_status} to {new_status}",
            user=user,
            trace_ctx=TraceContext.get_current(),
            status_before=old_status,
            status_after=new_status,
        )
        return request

    @staticmethod
    def mark_ready(request: ProcurementRequest, user=None) -> ProcurementRequest:
        """Validate attributes and transition to READY."""
        required_attrs = request.attributes.filter(is_required=True)
        for attr in required_attrs:
            if not attr.value_text and attr.value_number is None and not attr.value_json:
                raise ValueError(f"Required attribute '{attr.attribute_code}' is missing a value.")
        return ProcurementRequestService.update_status(
            request, ProcurementRequestStatus.READY, user=user,
        )

    @staticmethod
    def get_request(request_id) -> ProcurementRequest:
        """Fetch request by PK or UUID."""
        if isinstance(request_id, str) and len(request_id) > 10:
            return ProcurementRequest.objects.get(request_id=request_id)
        return ProcurementRequest.objects.get(pk=request_id)


class AttributeService:
    """Manage dynamic attributes for a procurement request."""

    @staticmethod
    def bulk_set_attributes(
        request: ProcurementRequest,
        attributes: List[Dict[str, Any]],
    ) -> List[ProcurementRequestAttribute]:
        created = []
        for attr_data in attributes:
            obj, _ = ProcurementRequestAttribute.objects.update_or_create(
                request=request,
                attribute_code=attr_data["attribute_code"],
                defaults={
                    "attribute_label": attr_data.get("attribute_label", attr_data["attribute_code"]),
                    "data_type": attr_data.get("data_type", "TEXT"),
                    "value_text": attr_data.get("value_text", ""),
                    "value_number": attr_data.get("value_number"),
                    "value_json": attr_data.get("value_json"),
                    "is_required": attr_data.get("is_required", False),
                    "normalized_value": attr_data.get("normalized_value", ""),
                },
            )
            created.append(obj)
        return created

    @staticmethod
    def get_attributes_dict(request: ProcurementRequest) -> Dict[str, Any]:
        """Return attributes as a simple dict keyed by attribute_code."""
        result = {}
        for attr in request.attributes.all():
            if attr.data_type == "NUMBER" and attr.value_number is not None:
                result[attr.attribute_code] = float(attr.value_number)
            elif attr.data_type == "JSON" and attr.value_json is not None:
                result[attr.attribute_code] = attr.value_json
            else:
                result[attr.attribute_code] = attr.value_text
        return result
