"""ERP Audit Service — logs ERP resolution and submission events.

Follows the PostingAuditService → AuditService.log_event() pattern.
Masks sensitive values (API keys, auth tokens) before logging.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Keys whose values should be masked in audit metadata
_SENSITIVE_KEYS = re.compile(
    r"(api_key|secret|token|password|authorization|credential)", re.IGNORECASE
)


def _mask_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with sensitive values replaced by '***REDACTED***'."""
    masked = {}
    for key, value in data.items():
        if _SENSITIVE_KEYS.search(key):
            masked[key] = "***REDACTED***"
        elif isinstance(value, dict):
            masked[key] = _mask_metadata(value)
        else:
            masked[key] = value
    return masked


class ERPAuditService:
    """Centralized audit logging for ERP integration events."""

    @staticmethod
    def log_resolution(
        event_type: str,
        description: str,
        *,
        resolution_type: str = "",
        lookup_key: str = "",
        source_type: str = "",
        resolved: bool = False,
        invoice_id: Optional[int] = None,
        reconciliation_result_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        connector_name: str = "",
        duration_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an ERP resolution audit event."""
        try:
            from apps.auditlog.services import AuditService

            safe_meta = _mask_metadata(metadata or {})
            safe_meta.update({
                "resolution_type": resolution_type,
                "lookup_key": lookup_key,
                "source_type": source_type,
                "resolved": resolved,
                "connector_name": connector_name,
                "duration_ms": duration_ms,
                "reconciliation_result_id": reconciliation_result_id,
                "posting_run_id": posting_run_id,
            })

            entity_type = "Invoice" if invoice_id else "ERPResolution"
            entity_id = invoice_id or 0

            AuditService.log_event(
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                description=description,
                metadata=safe_meta,
            )
        except Exception:
            logger.exception("Failed to log ERP audit event: %s", event_type)

    @staticmethod
    def log_submission(
        event_type: str,
        description: str,
        *,
        submission_type: str = "",
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        connector_name: str = "",
        erp_document_number: str = "",
        success: bool = False,
        error_message: str = "",
        duration_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an ERP submission audit event."""
        try:
            from apps.auditlog.services import AuditService

            safe_meta = _mask_metadata(metadata or {})
            safe_meta.update({
                "submission_type": submission_type,
                "erp_document_number": erp_document_number,
                "connector_name": connector_name,
                "success": success,
                "error_message": error_message,
                "posting_run_id": posting_run_id,
                "duration_ms": duration_ms,
            })

            AuditService.log_event(
                entity_type="Invoice",
                entity_id=invoice_id or 0,
                event_type=event_type,
                description=description,
                metadata=safe_meta,
            )
        except Exception:
            logger.exception("Failed to log ERP submission audit event: %s", event_type)
