"""PO importer — imports open PO references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.db import transaction

from apps.posting_core.models import ERPPOReference, ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.import_parsers import (
    normalize_text,
    safe_bool,
    safe_decimal,
)
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class POImporter:
    """Imports PO reference rows into ERPPOReference.

    Natural key: (po_number, po_line_number).
    Skips intra-file duplicates and rows already present in this batch.
    """

    @staticmethod
    @transaction.atomic
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        valid_records: List[ERPPOReference] = []
        errors: List[str] = []
        invalid_count = 0
        seen_keys: set = set()

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("OPEN_PO", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            po_number = str(row.get("po_number", "")).strip()
            po_line = str(row.get("po_line_number", "")).strip()
            key = (po_number, po_line)

            if key in seen_keys:
                errors.append(
                    f"Row {idx}: duplicate (po_number, po_line_number) '{po_number}/{po_line}' in file — skipped"
                )
                invalid_count += 1
                continue
            seen_keys.add(key)

            description = str(row.get("description", "")).strip()
            valid_records.append(ERPPOReference(
                batch=batch,
                po_number=po_number,
                po_line_number=po_line,
                vendor_code=str(row.get("vendor_code", "")).strip(),
                item_code=str(row.get("item_code", "")).strip(),
                description=description,
                normalized_description=normalize_text(description),
                quantity=safe_decimal(row.get("quantity")),
                unit_price=safe_decimal(row.get("unit_price")),
                line_amount=safe_decimal(row.get("line_amount")),
                currency=str(row.get("currency", "")).strip()[:10],
                status=str(row.get("status", "")).strip(),
                is_open=safe_bool(row.get("is_open")),
                raw_json=row,
            ))

        if valid_records:
            existing_keys = set(
                ERPPOReference.objects
                .filter(
                    batch=batch,
                    po_number__in=[r.po_number for r in valid_records],
                )
                .values_list("po_number", "po_line_number")
            )
            if existing_keys:
                logger.warning(
                    "POImporter: %d (po_number, po_line) pair(s) already in batch %s — skipping",
                    len(existing_keys), batch.pk,
                )
                valid_records = [
                    r for r in valid_records
                    if (r.po_number, r.po_line_number) not in existing_keys
                ]

            if valid_records:
                ERPPOReference.objects.bulk_create(valid_records)

        logger.info(
            "POImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
