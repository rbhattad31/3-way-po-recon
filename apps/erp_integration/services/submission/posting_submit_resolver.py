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

        # --- Langfuse tracing (fail-silent) ---
        try:
            from apps.erp_integration.services.langfuse_helpers import (
                start_erp_span,
                end_erp_span,
                score_erp_observation,
                sanitize_erp_error,
                ERP_LATENCY_THRESHOLD_MS,
            )
            _level = "ERROR" if not result.success else "DEFAULT"
            _conn_name = result.connector_name or ""
            _meta = {
                "submission_type": submission_type,
                "connector_name": _conn_name,
                "posting_run_id": posting_run_id,
                "invoice_id": invoice_id,
                "submission_mode": "sync",
            }
            _output = {
                "submission_attempted": True,
                "submission_success": result.success,
                "response_status": str(result.status),
                "erp_document_number_present": bool(result.erp_document_number),
                "latency_ms": result.duration_ms,
                "retryable_failure": (
                    not result.success
                    and result.status in (ERPSubmissionStatus.FAILED, ERPSubmissionStatus.TIMEOUT)
                ),
                "connector_name": _conn_name,
                "payload_built": True,
            }
            if result.error_message:
                _output["error_type"] = sanitize_erp_error(result.error_message)

            _lf_span = None
            _lf_root = None
            if lf_trace_id:
                # Attach as a child span under the parent posting pipeline trace.
                from apps.core.langfuse_client import get_client
                lf_client = get_client()
                if lf_client:
                    _lf_span = lf_client.span(
                        trace_id=lf_trace_id,
                        name="erp_submission",
                        metadata=_meta,
                    )
            else:
                # Standalone fallback trace (no parent pipeline trace available).
                from apps.core.langfuse_client import start_trace_safe
                import uuid as _uuid
                _fallback_id = (
                    f"erp-sub-{posting_run_id}" if posting_run_id
                    else f"erp-inv-{invoice_id}" if invoice_id
                    else _uuid.uuid4().hex
                )
                _lf_root = start_trace_safe(
                    _fallback_id,
                    "erp_submission_pipeline",
                    invoice_id=invoice_id,
                    session_id=f"erp-sub-{invoice_id}" if invoice_id else None,
                    metadata=_meta,
                )
                _lf_span = start_erp_span(_lf_root, "erp_submission", metadata=_meta)

            if _lf_span is not None:
                end_erp_span(_lf_span, output=_output, level=_level)

                # Observation-level scores
                score_erp_observation(_lf_span, "erp_submission_attempted", 1.0)
                score_erp_observation(
                    _lf_span, "erp_submission_success",
                    1.0 if result.success else 0.0,
                    comment=f"type={submission_type} status={result.status}",
                )
                score_erp_observation(
                    _lf_span, "erp_submission_latency_ok",
                    1.0 if result.duration_ms <= ERP_LATENCY_THRESHOLD_MS else 0.0,
                    comment=f"{result.duration_ms}ms",
                )
                score_erp_observation(
                    _lf_span, "erp_submission_retryable_failure",
                    1.0 if _output["retryable_failure"] else 0.0,
                )
                score_erp_observation(
                    _lf_span, "erp_document_number_present",
                    1.0 if result.erp_document_number else 0.0,
                )

            # Close standalone root if created
            if _lf_root is not None:
                from apps.core.langfuse_client import end_span_safe
                end_span_safe(
                    _lf_root,
                    output={
                        "erp_final_success": result.success,
                        "submission_type": submission_type,
                        "latency_ms": result.duration_ms,
                    },
                    level=_level,
                    is_root=True,
                )
        except Exception:
            logger.debug("Langfuse span finalization failed for submit (non-fatal)", exc_info=True)

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
            from apps.erp_integration.services.langfuse_helpers import (
                start_erp_span,
                end_erp_span,
                score_erp_observation,
                sanitize_erp_error,
            )
            from apps.core.langfuse_client import start_trace_safe, end_span_safe
            import uuid as _uuid
            _trace_id = (
                f"erp-{posting_run_id}" if posting_run_id
                else f"erp-inv-{invoice_id}" if invoice_id
                else _uuid.uuid4().hex
            )
            _meta = {
                "operation_type": "check_submission_status",
                "document_number": erp_document_number,
                "connector_name": result.connector_name or "",
                "posting_run_id": posting_run_id,
                "invoice_id": invoice_id,
            }
            _lf_trace = start_trace_safe(
                _trace_id,
                "erp_status_check",
                invoice_id=invoice_id,
                session_id=f"erp-status-{invoice_id}" if invoice_id else None,
                metadata=_meta,
            )
            if _lf_trace is not None:
                _level = "ERROR" if not result.success else "DEFAULT"
                _output = {
                    "success": result.success,
                    "status": str(result.status),
                    "erp_document_number_present": bool(result.erp_document_number),
                    "duration_ms": result.duration_ms,
                }
                if result.error_message:
                    _output["error_type"] = sanitize_erp_error(result.error_message)
                end_span_safe(
                    _lf_trace,
                    output=_output,
                    level=_level,
                    is_root=True,
                )
        except Exception:
            logger.debug("Langfuse span finalization failed for get_status (non-fatal)", exc_info=True)

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
