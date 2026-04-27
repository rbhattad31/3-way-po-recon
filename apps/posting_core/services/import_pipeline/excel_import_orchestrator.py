"""Excel Import Orchestrator — coordinates the full import lifecycle.

Responsibilities:
- Accept uploaded/imported Excel file
- Determine batch type
- Create ERPReferenceImportBatch
- Delegate to the appropriate importer
- Mark batch complete/partial/failed
- Write audit events
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    ERPReferenceBatchStatus,
    ERPReferenceBatchType,
)
from apps.core.decorators import observed_service
from apps.posting_core.models import ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.import_parsers import (
    compute_file_checksum,
    parse_excel_file,
)
from apps.posting_core.services.import_pipeline.import_validators import validate_columns
from apps.posting_core.services.import_pipeline.vendor_importer import VendorImporter
from apps.posting_core.services.import_pipeline.item_importer import ItemImporter
from apps.posting_core.services.import_pipeline.tax_importer import TaxImporter
from apps.posting_core.services.import_pipeline.cost_center_importer import CostCenterImporter
from apps.posting_core.services.import_pipeline.po_importer import POImporter
from apps.posting_core.services.import_pipeline.grn_importer import GRNImporter

logger = logging.getLogger(__name__)

IMPORTER_MAP = {
    ERPReferenceBatchType.VENDOR: VendorImporter,
    ERPReferenceBatchType.ITEM: ItemImporter,
    ERPReferenceBatchType.TAX: TaxImporter,
    ERPReferenceBatchType.COST_CENTER: CostCenterImporter,
    ERPReferenceBatchType.OPEN_PO: POImporter,
    ERPReferenceBatchType.GRN: GRNImporter,
}


class ExcelImportOrchestrator:
    """Stateless orchestrator for ERP reference Excel imports."""

    @classmethod
    @observed_service(
        "posting.excel_import",
        entity_type="ERPReferenceImportBatch",
        audit_event="ERP_REFERENCE_IMPORT_STARTED",
    )
    def run_import(
        cls,
        file_path: str,
        batch_type: str,
        *,
        tenant=None,
        user=None,
        source_as_of=None,
        column_map: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ERPReferenceImportBatch:
        """Run a full import pipeline for the given file and batch type.

        Args:
            file_path: Path to the Excel/CSV file on disk.
            batch_type: One of ERPReferenceBatchType values.
            user: The user performing the import (optional).
            source_as_of: When the source ERP data was exported (optional).
            column_map: Custom column mapping override (optional).
            metadata: Additional metadata to store on the batch (optional).

        Returns:
            The created ERPReferenceImportBatch.
        """
        # Validate batch type
        if batch_type not in ERPReferenceBatchType.values:
            raise ValueError(f"Invalid batch_type: {batch_type}")

        checksum = compute_file_checksum(file_path)

        # Extract source file name from path
        import os
        source_file_name = os.path.basename(file_path)

        batch = ERPReferenceImportBatch.objects.create(
            batch_type=batch_type,
            source_file_name=source_file_name,
            source_file_path=file_path,
            source_as_of=source_as_of,
            checksum=checksum,
            status=ERPReferenceBatchStatus.PENDING,
            imported_by=user,
            metadata_json=metadata or {},
            tenant=tenant,
        )

        cls._log_audit(
            batch,
            AuditEventType.ERP_REFERENCE_IMPORT_STARTED,
            f"Starting {batch_type} import from {source_file_name}",
            user=user,
        )

        try:
            rows, raw_headers = parse_excel_file(file_path, column_map)
            batch.row_count = len(rows)

            if not rows:
                batch.status = ERPReferenceBatchStatus.FAILED
                batch.error_summary = "No data rows found in file"
                batch.save(update_fields=["row_count", "status", "error_summary", "updated_at"])
                cls._log_audit(
                    batch,
                    AuditEventType.ERP_REFERENCE_IMPORT_FAILED,
                    "Import failed: no data rows",
                    user=user,
                )
                return batch

            # Validate columns present
            row_keys = set(rows[0].keys()) if rows else set()
            cols_valid, missing = validate_columns(batch_type, row_keys)
            if not cols_valid:
                batch.status = ERPReferenceBatchStatus.FAILED
                batch.error_summary = f"Missing required columns: {', '.join(missing)}"
                batch.save(update_fields=["row_count", "status", "error_summary", "updated_at"])
                cls._log_audit(
                    batch,
                    AuditEventType.ERP_REFERENCE_IMPORT_FAILED,
                    f"Import failed: missing columns {missing}",
                    user=user,
                )
                return batch

            # Delegate to type-specific importer
            importer_cls = IMPORTER_MAP.get(batch_type)
            if not importer_cls:
                raise ValueError(f"No importer for batch type: {batch_type}")

            valid_count, invalid_count, errors = importer_cls.import_rows(batch, rows)

            batch.valid_row_count = valid_count
            batch.invalid_row_count = invalid_count

            if invalid_count == 0:
                batch.status = ERPReferenceBatchStatus.COMPLETED
            elif valid_count > 0:
                batch.status = ERPReferenceBatchStatus.PARTIAL
            else:
                batch.status = ERPReferenceBatchStatus.FAILED

            if errors:
                batch.error_summary = "\n".join(errors[:50])  # Cap error summary

            batch.save(update_fields=[
                "row_count", "valid_row_count", "invalid_row_count",
                "status", "error_summary", "updated_at",
            ])

            cls._log_audit(
                batch,
                AuditEventType.ERP_REFERENCE_IMPORT_COMPLETED,
                f"Import completed: {valid_count} valid, {invalid_count} invalid rows",
                user=user,
                metadata={
                    "valid_count": valid_count,
                    "invalid_count": invalid_count,
                    "batch_id": batch.pk,
                },
            )

            logger.info(
                "ExcelImportOrchestrator: batch %s completed — %d valid, %d invalid",
                batch.pk, valid_count, invalid_count,
            )

        except Exception as exc:
            batch.status = ERPReferenceBatchStatus.FAILED
            batch.error_summary = f"{type(exc).__name__}: {str(exc)[:500]}"
            batch.save(update_fields=["status", "error_summary", "updated_at"])

            cls._log_audit(
                batch,
                AuditEventType.ERP_REFERENCE_IMPORT_FAILED,
                f"Import failed: {exc}",
                user=user,
            )
            logger.exception("ExcelImportOrchestrator: batch %s failed", batch.pk)
            raise

        return batch

    @staticmethod
    def _log_audit(batch, event_type, description, user=None, metadata=None):
        """Log an audit event for an import batch."""
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="ERPReferenceImportBatch",
                entity_id=batch.pk,
                event_type=event_type,
                description=description,
                user=user,
                metadata=metadata or {},
            )
        except Exception:
            logger.exception("Failed to log audit event for batch %s", batch.pk)
