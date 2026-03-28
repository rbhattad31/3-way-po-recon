"""Posting Submit Resolver — submits invoices to ERP via API.

API-only: no DB fallback, no cache.
Logs every attempt to ERPSubmissionLog + audit.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPSubmissionStatus, ERPSubmissionType
from apps.erp_integration.models import ERPSubmissionLog
from apps.erp_integration.services.audit_service import ERPAuditService
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPSubmissionResult,
)

logger = logging.getLogger(__name__)


class PostingSubmitResolver:
    """Resolves invoice posting submission to an ERP system.

    This is API-only — there is no DB fallback for submission.
    If no connector is available or the connector doesn't support posting,
    returns an unsupported result.
    """

    @staticmethod
    def submit_invoice(
        connector: Optional[BaseERPConnector],
        payload: Dict[str, Any],
        *,
        submission_type: str = ERPSubmissionType.CREATE_INVOICE,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        lf_trace_id: Optional[str] = None,
    ) -> ERPSubmissionResult:
        """Submit an invoice to the ERP system."""
        start = time.monotonic()

        if connector is None:
            result = ERPSubmissionResult(
                success=False,
                status=ERPSubmissionStatus.UNSUPPORTED,
                error_message="No ERP connector configured",
            )
            PostingSubmitResolver._log_submission(
                submission_type, result, payload, start,
                invoice_id=invoice_id, posting_run_id=posting_run_id,
            )
            return result

        # Check capability
        if submission_type == ERPSubmissionType.CREATE_INVOICE:
            capable = connector.supports_invoice_posting()
        elif submission_type == ERPSubmissionType.PARK_INVOICE:
            capable = connector.supports_invoice_parking()
        else:
            capable = False

        if not capable:
            result = ERPSubmissionResult(
                success=False,
                status=ERPSubmissionStatus.UNSUPPORTED,
                error_message=f"Connector '{connector.connector_name}' does not support {submission_type}",
                connector_name=connector.connector_name,
            )
            PostingSubmitResolver._log_submission(
                submission_type, result, payload, start,
                invoice_id=invoice_id, posting_run_id=posting_run_id,
            )
            return result

        # Execute submission
        try:
            if submission_type == ERPSubmissionType.CREATE_INVOICE:
                result = connector.create_invoice(payload)
            elif submission_type == ERPSubmissionType.PARK_INVOICE:
                result = connector.park_invoice(payload)
            else:
                result = ERPSubmissionResult(
                    success=False,
                    status=ERPSubmissionStatus.UNSUPPORTED,
                    error_message=f"Unknown submission type: {submission_type}",
                )
        except Exception as exc:
            logger.exception("ERP submission failed: %s", submission_type)
            result = ERPSubmissionResult(
                success=False,
                status=ERPSubmissionStatus.FAILED,
                error_message=str(exc),
                connector_name=connector.connector_name,
            )

        result.duration_ms = int((time.monotonic() - start) * 1000)

        try:
            from apps.core.langfuse_client import get_client, start_trace, end_span
            _output = {
                "success": result.success,
                "status": str(result.status),
                "erp_document_number": result.erp_document_number or "",
                "duration_ms": result.duration_ms,
                "error_message": result.error_message or "",
            }
            _level = "ERROR" if not result.success else "DEFAULT"
            if lf_trace_id:
                # Attach as a child span of the parent posting pipeline trace.
                lf_client = get_client()
                _lf_span = None
                if lf_client:
                    _lf_span = lf_client.span(
                        trace_id=lf_trace_id,
                        name="erp_submission",
                        metadata={
                            "submission_type": submission_type,
                            "connector_name": result.connector_name or "",
                            "posting_run_id": posting_run_id,
                        },
                    )
                if _lf_span is not None:
                    _lf_span.end(output=_output, level=_level)
            else:
                # Standalone fallback trace (no parent pipeline trace available).
                import uuid as _uuid
                _fallback_id = (
                    f"erp-sub-{posting_run_id}" if posting_run_id
                    else f"erp-inv-{invoice_id}" if invoice_id
                    else _uuid.uuid4().hex
                )
                _lf_trace = start_trace(
                    _fallback_id,
                    "erp_submission_standalone",
                    invoice_id=invoice_id,
                    metadata={
                        "submission_type": submission_type,
                        "connector_name": result.connector_name or "",
                        "posting_run_id": posting_run_id,
                    },
                )
                if _lf_trace is not None:
                    end_span(_lf_trace, output=_output, level=_level)
        except Exception:
            pass

        PostingSubmitResolver._log_submission(
            submission_type, result, payload, start,
            invoice_id=invoice_id, posting_run_id=posting_run_id,
        )
        return result

    @staticmethod
    def get_posting_status(
        connector: Optional[BaseERPConnector],
        erp_document_number: str,
        *,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
    ) -> ERPSubmissionResult:
        """Query the ERP for the status of a previously submitted document."""
        start = time.monotonic()

        if connector is None or not erp_document_number:
            return ERPSubmissionResult(
                success=False,
                status=ERPSubmissionStatus.UNSUPPORTED,
                error_message="No connector or document number provided",
            )

        try:
            result = connector.get_posting_status(erp_document_number)
        except Exception as exc:
            logger.exception("ERP status check failed for %s", erp_document_number)
            result = ERPSubmissionResult(
                success=False,
                status=ERPSubmissionStatus.FAILED,
                error_message=str(exc),
                connector_name=connector.connector_name,
            )

        result.duration_ms = int((time.monotonic() - start) * 1000)

        try:
            from apps.core.langfuse_client import start_trace, end_span
            import uuid as _uuid
            _trace_id = (
                f"erp-{posting_run_id}" if posting_run_id
                else f"erp-inv-{invoice_id}" if invoice_id
                else _uuid.uuid4().hex
            )
            _lf_trace = start_trace(
                _trace_id,
                "erp_status_check",
                invoice_id=invoice_id,
                metadata={
                    "document_number": erp_document_number,
                    "connector_name": result.connector_name or "",
                    "posting_run_id": posting_run_id,
                },
            )
            if _lf_trace is not None:
                end_span(
                    _lf_trace,
                    output={
                        "success": result.success,
                        "status": str(result.status),
                        "erp_document_number": result.erp_document_number or "",
                        "duration_ms": result.duration_ms,
                        "error_message": result.error_message or "",
                    },
                    level="ERROR" if not result.success else "DEFAULT",
                )
        except Exception:
            pass

        PostingSubmitResolver._log_submission(
            ERPSubmissionType.GET_STATUS, result, {"document_number": erp_document_number},
            start, invoice_id=invoice_id, posting_run_id=posting_run_id,
        )
        return result

    @staticmethod
    def _log_submission(
        submission_type: str,
        result: ERPSubmissionResult,
        payload: Dict[str, Any],
        start_time: float,
        *,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
    ) -> None:
        """Persist submission to ERPSubmissionLog + audit."""
        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Map result status to enum
        if result.success:
            log_status = ERPSubmissionStatus.SUCCESS
        elif result.status == ERPSubmissionStatus.UNSUPPORTED:
            log_status = ERPSubmissionStatus.UNSUPPORTED
        elif result.status == ERPSubmissionStatus.TIMEOUT:
            log_status = ERPSubmissionStatus.TIMEOUT
        else:
            log_status = ERPSubmissionStatus.FAILED

        try:
            ERPSubmissionLog.objects.create(
                submission_type=submission_type,
                status=log_status,
                connector_name=result.connector_name or "",
                request_payload_json=payload or {},
                response_json=result.response_data or {},
                erp_document_number=result.erp_document_number or "",
                error_code=result.error_code or "",
                error_message=result.error_message or "",
                duration_ms=duration_ms,
                related_invoice_id=invoice_id,
                related_posting_run_id=posting_run_id,
            )
        except Exception:
            logger.exception("Failed to persist ERPSubmissionLog")

        ERPAuditService.log_submission(
            event_type="ERP_SUBMISSION",
            description=f"{submission_type}: {'SUCCESS' if result.success else 'FAILED'}",
            submission_type=submission_type,
            invoice_id=invoice_id,
            posting_run_id=posting_run_id,
            connector_name=result.connector_name or "",
            erp_document_number=result.erp_document_number or "",
            success=result.success,
            error_message=result.error_message or "",
            duration_ms=duration_ms,
        )
