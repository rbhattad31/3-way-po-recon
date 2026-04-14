"""Prefill status persistence helpers."""
from __future__ import annotations

from apps.core.enums import ExtractionStatus, PrefillStatus


class PrefillStatusService:
    @staticmethod
    def mark_request_in_progress(request) -> None:
        request.prefill_status = PrefillStatus.IN_PROGRESS
        request.save(update_fields=["prefill_status", "updated_at"])

    @staticmethod
    def mark_request_completed(request, *, confidence: float, payload: dict) -> None:
        request.prefill_status = PrefillStatus.REVIEW_PENDING
        request.prefill_confidence = confidence
        request.prefill_payload_json = payload
        request.save(update_fields=["prefill_status", "prefill_confidence", "prefill_payload_json", "updated_at"])

    @staticmethod
    def mark_request_failed(request, error_message: str = "") -> None:
        request.prefill_status = PrefillStatus.FAILED
        payload = request.prefill_payload_json or {}
        if error_message:
            payload["error"] = str(error_message)
            request.prefill_payload_json = payload
            request.save(update_fields=["prefill_status", "prefill_payload_json", "updated_at"])
            return
        request.save(update_fields=["prefill_status", "updated_at"])

    @staticmethod
    def mark_quotation_in_progress(quotation) -> None:
        quotation.prefill_status = PrefillStatus.IN_PROGRESS
        quotation.extraction_status = ExtractionStatus.IN_PROGRESS
        quotation.save(update_fields=["prefill_status", "extraction_status", "updated_at"])

    @staticmethod
    def mark_quotation_completed(quotation, *, confidence: float, payload: dict) -> None:
        quotation.prefill_status = PrefillStatus.REVIEW_PENDING
        quotation.extraction_status = ExtractionStatus.COMPLETED
        quotation.extraction_confidence = confidence
        quotation.prefill_payload_json = payload
        quotation.save(
            update_fields=[
                "prefill_status",
                "extraction_status",
                "extraction_confidence",
                "prefill_payload_json",
                "updated_at",
            ],
        )

    @staticmethod
    def mark_quotation_failed(quotation, error_message: str = "") -> None:
        quotation.prefill_status = PrefillStatus.FAILED
        quotation.extraction_status = ExtractionStatus.FAILED
        payload = quotation.prefill_payload_json or {}
        if error_message:
            payload["error"] = str(error_message)
            quotation.prefill_payload_json = payload
            quotation.save(update_fields=["prefill_status", "extraction_status", "prefill_payload_json", "updated_at"])
            return
        quotation.save(update_fields=["prefill_status", "extraction_status", "updated_at"])
