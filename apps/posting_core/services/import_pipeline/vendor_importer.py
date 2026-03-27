"""Vendor importer — imports vendor references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from apps.posting_core.models import ERPReferenceImportBatch, ERPVendorReference
from apps.posting_core.services.import_pipeline.import_parsers import normalize_text, safe_bool
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class VendorImporter:
    """Imports vendor reference rows into ERPVendorReference."""

    @staticmethod
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        """Import parsed vendor rows.

        Returns (valid_count, invalid_count, error_messages).
        """
        valid_records: List[ERPVendorReference] = []
        errors: List[str] = []
        invalid_count = 0

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("VENDOR", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            vendor_code = str(row.get("vendor_code", "")).strip()
            vendor_name = str(row.get("vendor_name", "")).strip()

            valid_records.append(ERPVendorReference(
                batch=batch,
                vendor_code=vendor_code,
                vendor_name=vendor_name,
                normalized_vendor_name=normalize_text(vendor_name),
                vendor_group=str(row.get("vendor_group", "")).strip(),
                country_code=str(row.get("country_code", "")).strip()[:3],
                is_active=safe_bool(row.get("is_active")),
                payment_terms=str(row.get("payment_terms", "")).strip(),
                currency=str(row.get("currency", "")).strip()[:10],
                raw_json=row,
            ))

        if valid_records:
            ERPVendorReference.objects.bulk_create(valid_records)

        logger.info(
            "VendorImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
