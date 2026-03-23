"""PrefillStatusService — manages prefill lifecycle status transitions."""
from __future__ import annotations

import logging

from apps.core.enums import PrefillStatus
from apps.procurement.models import ProcurementRequest, SupplierQuotation

logger = logging.getLogger(__name__)


class PrefillStatusService:
    """Update prefill lifecycle status on ProcurementRequest or SupplierQuotation."""

    @staticmethod
    def update_request_prefill_status(
        request: ProcurementRequest,
        status: str,
        *,
        confidence: float | None = None,
        payload: dict | None = None,
    ) -> ProcurementRequest:
        update_fields = ["prefill_status", "updated_at"]
        request.prefill_status = status

        if confidence is not None:
            request.prefill_confidence = confidence
            update_fields.append("prefill_confidence")

        if payload is not None:
            request.prefill_payload_json = payload
            update_fields.append("prefill_payload_json")

        request.save(update_fields=update_fields)
        logger.info(
            "Request %s prefill_status -> %s (confidence=%s)",
            request.request_id, status, confidence,
        )
        return request

    @staticmethod
    def update_quotation_prefill_status(
        quotation: SupplierQuotation,
        status: str,
        *,
        confidence: float | None = None,
        payload: dict | None = None,
    ) -> SupplierQuotation:
        update_fields = ["prefill_status", "updated_at"]
        quotation.prefill_status = status

        if confidence is not None:
            quotation.extraction_confidence = confidence
            update_fields.append("extraction_confidence")

        if payload is not None:
            quotation.prefill_payload_json = payload
            update_fields.append("prefill_payload_json")

        quotation.save(update_fields=update_fields)
        logger.info(
            "Quotation %s prefill_status -> %s (confidence=%s)",
            quotation.pk, status, confidence,
        )
        return quotation

    @staticmethod
    def mark_request_in_progress(request: ProcurementRequest) -> ProcurementRequest:
        return PrefillStatusService.update_request_prefill_status(
            request, PrefillStatus.IN_PROGRESS,
        )

    @staticmethod
    def mark_request_completed(
        request: ProcurementRequest, confidence: float, payload: dict,
    ) -> ProcurementRequest:
        return PrefillStatusService.update_request_prefill_status(
            request, PrefillStatus.REVIEW_PENDING,
            confidence=confidence, payload=payload,
        )

    @staticmethod
    def mark_request_failed(request: ProcurementRequest) -> ProcurementRequest:
        return PrefillStatusService.update_request_prefill_status(
            request, PrefillStatus.FAILED,
        )

    @staticmethod
    def mark_quotation_in_progress(quotation: SupplierQuotation) -> SupplierQuotation:
        return PrefillStatusService.update_quotation_prefill_status(
            quotation, PrefillStatus.IN_PROGRESS,
        )

    @staticmethod
    def mark_quotation_completed(
        quotation: SupplierQuotation, confidence: float, payload: dict,
    ) -> SupplierQuotation:
        return PrefillStatusService.update_quotation_prefill_status(
            quotation, PrefillStatus.REVIEW_PENDING,
            confidence=confidence, payload=payload,
        )

    @staticmethod
    def mark_quotation_failed(quotation: SupplierQuotation) -> SupplierQuotation:
        return PrefillStatusService.update_quotation_prefill_status(
            quotation, PrefillStatus.FAILED,
        )
