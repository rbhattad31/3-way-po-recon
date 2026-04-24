"""Direct ERP Database Importer — queries ERP connectors directly for reference data.

Queries ERP via configured connectors (SQL Server, Dynamics, Zoho, etc.) and
imports vendor, item, tax code, cost center, and PO data directly without
requiring manual Excel uploads.

Reuses the same import logic as ExcelImportOrchestrator, but sources data
from live ERP instead of files.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Dict, Generator, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    ERPReferenceBatchStatus,
    ERPReferenceBatchType,
)
from apps.core.decorators import observed_service
from apps.posting_core.models import ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.vendor_importer import VendorImporter
from apps.posting_core.services.import_pipeline.item_importer import ItemImporter
from apps.posting_core.services.import_pipeline.tax_importer import TaxImporter
from apps.posting_core.services.import_pipeline.cost_center_importer import CostCenterImporter
from apps.posting_core.services.import_pipeline.po_importer import POImporter
from apps.posting_core.services.import_pipeline.import_validators import validate_columns
from apps.erp_integration.services.connector_factory import ConnectorFactory

logger = logging.getLogger(__name__)

IMPORTER_MAP = {
    ERPReferenceBatchType.VENDOR: VendorImporter,
    ERPReferenceBatchType.ITEM: ItemImporter,
    ERPReferenceBatchType.TAX: TaxImporter,
    ERPReferenceBatchType.COST_CENTER: CostCenterImporter,
    ERPReferenceBatchType.OPEN_PO: POImporter,
}


class DirectERPImporter:
    """Query ERP connector directly and yield reference data rows."""

    _INDIA_TAX_RATE_BY_CODE = {
        "SGST": 9.0,
        "CGST": 9.0,
        "IGST": 18.0,
    }
    _TAX_CODE_BY_RATE = {
        9.0: "SGST",
        18.0: "IGST",
    }

    @staticmethod
    def query_vendors(connector, **params) -> Generator[Dict[str, Any], None, None]:
        """Query ERP for vendor master data."""
        try:
            rows = connector.execute_bulk_query("vendor_bulk", params=[])
            for row in rows:
                if row and row.get("vendor_code"):
                    tax_id = str(row.get("tax_id", "") or "").strip().upper()
                    # Voucher schema often omits country; default to IN when GSTIN is present.
                    country_code = str(row.get("country_code", "") or "").strip()
                    if not country_code and tax_id:
                        country_code = "IN"
                    yield {
                        "vendor_code": str(row.get("vendor_code", "")).strip(),
                        "vendor_name": str(row.get("vendor_name", "")).strip(),
                        "vendor_group": str(row.get("vendor_group", "") or "").strip(),
                        "tax_id": tax_id,
                        "country_code": country_code,
                        "currency": str(row.get("currency", "") or "").strip() or "INR",
                        "payment_terms": str(row.get("payment_terms", "") or "").strip(),
                        "is_active": bool(row.get("is_active", True)),
                    }
        except Exception as exc:
            logger.warning("Failed to query vendors from ERP: %s", exc)
            return

    @staticmethod
    def query_items(connector, **params) -> Generator[Dict[str, Any], None, None]:
        """Query ERP for item master data."""
        try:
            rows = connector.execute_bulk_query("item_bulk", params=[])
            for row in rows:
                if row and row.get("item_code"):
                    item_type = str(row.get("item_type", "") or "").strip()
                    if not item_type:
                        item_type = str(row.get("item_group", "") or "").strip()

                    tax_code = DirectERPImporter._derive_item_tax_code(row)
                    yield {
                        "item_code": str(row.get("item_code", "")).strip(),
                        "item_name": str(row.get("item_name", "")).strip(),
                        "item_type": item_type,
                        "category": str(row.get("category", "") or "").strip(),
                        "uom": str(row.get("unit_of_measure", "") or "").strip(),
                        "description": str(row.get("description", "") or "").strip(),
                        "tax_code": tax_code,
                        "is_active": bool(row.get("is_active", True)),
                        "item_group": str(row.get("item_group", "") or "").strip(),
                        "is_service_item": bool(row.get("is_service_item", False)),
                        "is_stock_item": bool(row.get("is_stock_item", True)),
                    }
        except Exception as exc:
            logger.warning("Failed to query items from ERP: %s", exc)
            return

    @staticmethod
    def query_tax_codes(connector, **params) -> Generator[Dict[str, Any], None, None]:
        """Query ERP for tax code master data."""
        try:
            rows = connector.execute_bulk_query("tax_bulk", params=[])
            for row in rows:
                if row and row.get("tax_code"):
                    tax_code = str(row.get("tax_code", "")).strip().upper()
                    raw_rate = row.get("rate", None)
                    rate = float(raw_rate) if raw_rate not in (None, "") else None
                    if rate is None:
                        rate = DirectERPImporter._INDIA_TAX_RATE_BY_CODE.get(tax_code, 0.0)
                    yield {
                        "tax_code": tax_code,
                        "tax_label": str(row.get("tax_component", "")).strip(),
                        "rate": rate,
                        "country_code": str(row.get("country_code", "") or "").strip() or "IN",
                        "is_active": bool(row.get("is_active", True)),
                    }
        except Exception as exc:
            logger.warning("Failed to query tax codes from ERP: %s", exc)
            return

    @staticmethod
    def query_cost_centers(connector, **params) -> Generator[Dict[str, Any], None, None]:
        """Query ERP for cost center master data."""
        try:
            rows = connector.execute_bulk_query("cost_center_bulk", params=[])
            for row in rows:
                if row and row.get("cost_center_code"):
                    department = str(row.get("department", "") or "").strip()
                    business_unit = str(row.get("business_unit", "") or "").strip() or department
                    yield {
                        "cost_center_code": str(row.get("cost_center_code", "")).strip(),
                        "cost_center_name": str(row.get("description", "")).strip(),
                        "department": department,
                        "business_unit": business_unit,
                        "is_active": bool(row.get("is_active", True)),
                    }
        except Exception as exc:
            logger.warning("Failed to query cost centers from ERP: %s", exc)
            return

    @staticmethod
    def _derive_item_tax_code(row: Dict[str, Any]) -> str:
        """Derive item-level tax code from ERP fields, then fallback defaults."""
        explicit_code = DirectERPImporter._normalise_tax_code(
            row.get("tax_code") or row.get("default_tax_code")
        )
        if explicit_code:
            return explicit_code

        component_code = DirectERPImporter._normalise_tax_component(
            row.get("tax_component") or row.get("tax_label")
        )
        if component_code:
            return component_code

        rate = DirectERPImporter._extract_numeric_rate(
            row.get("tax_rate")
            or row.get("gst_rate")
            or row.get("vat_rate")
            or row.get("rate_percent")
        )
        if rate is not None:
            inferred = DirectERPImporter._infer_tax_code_from_rate(rate)
            if inferred:
                return inferred

        desc = str(row.get("description", "") or "").upper()
        for candidate in ("IGST", "SGST", "CGST"):
            if candidate in desc:
                return candidate

        # Rule-based default for voucher schemas lacking item-tax fields.
        return "IGST"

    @staticmethod
    def _normalise_tax_code(value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if "IGST" in text:
            return "IGST"
        if "SGST" in text:
            return "SGST"
        if "CGST" in text:
            return "CGST"
        if text in {"GST", "VAT", "TAX"}:
            return ""
        return text

    @staticmethod
    def _normalise_tax_component(value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        for candidate in ("IGST", "SGST", "CGST"):
            if candidate in text:
                return candidate
        return ""

    @classmethod
    def _infer_tax_code_from_rate(cls, rate: float) -> str:
        rounded = round(float(rate), 2)
        if rounded in cls._TAX_CODE_BY_RATE:
            return cls._TAX_CODE_BY_RATE[rounded]
        # Common split GST representation where each leg is 9 and effective is 18.
        if 17.5 <= rounded <= 18.5:
            return "IGST"
        if 8.5 <= rounded <= 9.5:
            return "SGST"
        return ""

    @staticmethod
    def _extract_numeric_rate(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            pass

        text = str(value)
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except Exception:
            return None

    @staticmethod
    def query_pos(connector, **params) -> Generator[Dict[str, Any], None, None]:
        """Query ERP for open purchase order data."""
        try:
            rows = connector.execute_bulk_query("po_bulk", params=[])
            for row in rows:
                if row and row.get("po_number"):
                    yield {
                        "po_number": str(row.get("po_number", "")).strip(),
                        "po_line_number": str(row.get("po_line_number", "") or "1").strip(),
                        "vendor_code": str(row.get("vendor_code", row.get("vendor_name", "")) or "").strip(),
                        "purchase_account": str(
                            row.get("purchase_account", row.get("vendor_code", row.get("vendor_name", ""))) or ""
                        ).strip(),
                        "item_code": str(row.get("item_code", "") or "").strip(),
                        "description": str(row.get("description", row.get("remarks", "")) or "").strip(),
                        "quantity": float(row.get("quantity", 0) or 0),
                        "uom": str(row.get("uom", "") or "").strip(),
                        "unit_price": float(row.get("unit_price", 0) or 0),
                        "line_amount": float(row.get("total_amount", 0) or 0),
                        "currency": str(row.get("currency", "") or "").strip(),
                        "status": str(row.get("status", "OPEN") or "OPEN").strip(),
                        "is_open": bool(row.get("is_open", True)),
                        "po_date": str(row.get("po_date", "")).strip(),
                    }
        except Exception as exc:
            logger.warning("Failed to query POs from ERP: %s", exc)
            return

    @classmethod
    def get_query_method(cls, batch_type: str):
        """Return the query method for the given batch type."""
        query_map = {
            ERPReferenceBatchType.VENDOR: cls.query_vendors,
            ERPReferenceBatchType.ITEM: cls.query_items,
            ERPReferenceBatchType.TAX: cls.query_tax_codes,
            ERPReferenceBatchType.COST_CENTER: cls.query_cost_centers,
            ERPReferenceBatchType.OPEN_PO: cls.query_pos,
        }
        return query_map.get(batch_type)


class DirectERPImportOrchestrator:
    """Orchestrate direct ERP database imports."""

    _CONNECTIVITY_RETRY_ATTEMPTS = 3
    _CONNECTIVITY_RETRY_BACKOFF_SECONDS = (1, 2)

    @classmethod
    @observed_service(
        "posting.direct_erp_import",
        entity_type="ERPReferenceImportBatch",
        audit_event="ERP_REFERENCE_IMPORT_STARTED",
    )
    def run_import(
        cls,
        batch_type: str,
        connector_name: str,
        *,
        tenant=None,
        user=None,
        source_as_of: Optional[date] = None,
        metadata: Optional[Dict[str, Any]] = None,
        existing_batch_id: Optional[int] = None,
    ) -> ERPReferenceImportBatch:
        """Run a direct ERP import for the given batch type.

        Args:
            batch_type: One of ERPReferenceBatchType values.
            connector_name: Name of the ERPConnection to use.
            tenant: The CompanyProfile to scope data to (optional).
            user: The user performing the import (optional).
            source_as_of: When the source ERP data was as-of (optional).
            metadata: Additional metadata to store on the batch (optional).

        Returns:
            The created ERPReferenceImportBatch.

        Raises:
            ValueError: If batch_type invalid, connector not found, or connector
                        doesn't support the required operation.
        """
        # Validate batch type
        if batch_type not in ERPReferenceBatchType.values:
            raise ValueError(f"Invalid batch_type: {batch_type}")

        # Get connector
        from apps.erp_integration.models import ERPConnection
        from apps.erp_integration.enums import ERPConnectionStatus

        try:
            conn_record = ERPConnection.objects.get(
                name=connector_name,
                status=ERPConnectionStatus.ACTIVE,
                is_active=True,
            )
        except ERPConnection.DoesNotExist:
            raise ValueError(
                f"ERP Connection '{connector_name}' not found or not active"
            )

        connector = ConnectorFactory.create_from_connection(conn_record)
        if not connector:
            raise ValueError(
                f"Failed to instantiate connector for '{connector_name}'"
            )

        # Test connectivity with retry on transient SQL availability failures.
        success, msg = cls._test_connectivity_with_retry(connector, connector_name)
        if not success:
            raise ValueError(f"Connector test failed: {msg}")

        # Create or reuse batch record
        if existing_batch_id:
            batch = ERPReferenceImportBatch.objects.get(pk=existing_batch_id)
            batch.batch_type = batch_type
            batch.source_file_path = connector_name
            if source_as_of is not None:
                batch.source_as_of = source_as_of
            batch.status = ERPReferenceBatchStatus.PENDING
            batch.row_count = 0
            batch.valid_row_count = 0
            batch.invalid_row_count = 0
            batch.error_summary = ""
            if metadata:
                merged = dict(batch.metadata_json or {})
                merged.update(metadata)
                batch.metadata_json = merged
            batch.save(update_fields=[
                "batch_type", "source_file_path", "source_as_of", "status",
                "row_count", "valid_row_count", "invalid_row_count", "error_summary",
                "metadata_json", "updated_at",
            ])
        else:
            source_file_name = f"direct_erp_{batch_type}_{timezone.now().isoformat()}"
            batch = ERPReferenceImportBatch.objects.create(
                batch_type=batch_type,
                source_file_name=source_file_name,
                source_file_path=connector_name,  # Store connector name as "path"
                source_as_of=source_as_of,
                checksum="direct_erp",  # Not from file
                status=ERPReferenceBatchStatus.PENDING,
                imported_by=user,
                metadata_json=metadata or {"source": "direct_erp_connector", "connector_name": connector_name},
                tenant=tenant,
            )

        cls._log_audit(
            batch,
            AuditEventType.ERP_REFERENCE_IMPORT_STARTED,
            f"Starting {batch_type} direct import from ERP connector '{connector_name}'",
            user=user,
        )

        try:
            # Query ERP
            query_method = DirectERPImporter.get_query_method(batch_type)
            if not query_method:
                raise ValueError(f"No query method for batch type: {batch_type}")

            rows = list(query_method(connector))
            batch.row_count = len(rows)

            if not rows:
                # No rows is a valid no-op outcome for some reference types.
                batch.status = ERPReferenceBatchStatus.COMPLETED
                batch.error_summary = "No rows returned from ERP for this import type."
                batch.save(update_fields=["row_count", "status", "error_summary", "updated_at"])
                cls._log_audit(
                    batch,
                    AuditEventType.ERP_REFERENCE_IMPORT_COMPLETED,
                    "Import completed with no rows returned from ERP",
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

            batch.row_count = len(rows)
            batch.valid_row_count = valid_count
            batch.invalid_row_count = invalid_count

            if errors:
                batch.error_summary = "\n".join(errors[:100])  # Cap at 100 errors
                batch.save(update_fields=[
                    "valid_row_count", "invalid_row_count", "error_summary", "updated_at"
                ])

            if valid_count > 0:
                batch.status = (
                    ERPReferenceBatchStatus.COMPLETED if invalid_count == 0
                    else ERPReferenceBatchStatus.PARTIAL
                )
            else:
                batch.status = ERPReferenceBatchStatus.FAILED

            batch.save(update_fields=[
                "row_count",
                "valid_row_count",
                "invalid_row_count",
                "status",
                "updated_at",
            ])

            cls._log_audit(
                batch,
                AuditEventType.ERP_REFERENCE_IMPORT_COMPLETED,
                f"Import completed: {valid_count} valid, {invalid_count} invalid",
                user=user,
            )

            logger.info(
                "DirectERPImport completed: batch_id=%s, type=%s, valid=%d, invalid=%d",
                batch.pk, batch_type, valid_count, invalid_count,
            )

            return batch

        except Exception as exc:
            logger.exception("DirectERPImport failed: %s", connector_name)
            batch.status = ERPReferenceBatchStatus.FAILED
            batch.error_summary = str(exc)[:500]
            batch.save(update_fields=["status", "error_summary", "updated_at"])
            cls._log_audit(
                batch,
                AuditEventType.ERP_REFERENCE_IMPORT_FAILED,
                f"Import failed: {str(exc)[:200]}",
                user=user,
            )
            raise

    @staticmethod
    def _log_audit(batch: ERPReferenceImportBatch, event_type: str, message: str, user=None):
        """Log audit event for import."""
        from apps.auditlog.models import AuditEvent
        AuditEvent.objects.create(
            entity_type="ERPReferenceImportBatch",
            entity_id=batch.pk,
            action="import",
            event_type=event_type,
            event_description=message,
            performed_by=user,
            status_after=batch.status,
            tenant=batch.tenant,
        )

    @classmethod
    def _test_connectivity_with_retry(cls, connector, connector_name: str) -> Tuple[bool, str]:
        """Run connector connectivity checks with limited retry on transient errors."""
        attempts = max(1, int(cls._CONNECTIVITY_RETRY_ATTEMPTS))
        last_msg = "Unknown connector connectivity failure"

        for attempt in range(1, attempts + 1):
            success, msg = connector.test_connectivity()
            if success:
                return True, msg

            last_msg = str(msg or "")
            is_transient = cls._is_transient_connectivity_error(last_msg)
            should_retry = is_transient and attempt < attempts
            if not should_retry:
                break

            backoff_index = min(
                attempt - 1,
                len(cls._CONNECTIVITY_RETRY_BACKOFF_SECONDS) - 1,
            )
            backoff_s = cls._CONNECTIVITY_RETRY_BACKOFF_SECONDS[backoff_index]
            logger.warning(
                "Direct ERP connectivity failed for '%s' (attempt %s/%s): %s; retrying in %ss",
                connector_name,
                attempt,
                attempts,
                last_msg,
                backoff_s,
            )
            time.sleep(backoff_s)

        return False, last_msg

    @staticmethod
    def _is_transient_connectivity_error(message: str) -> bool:
        """Detect transient Azure SQL availability errors that are safe to retry."""
        msg = (message or "").upper()
        return "(40613)" in msg or "DATABASE" in msg and "NOT CURRENTLY AVAILABLE" in msg
