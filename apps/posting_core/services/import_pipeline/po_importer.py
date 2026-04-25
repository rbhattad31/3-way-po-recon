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
                tenant=batch.tenant,
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

        upserted = 0
        if valid_records:
            for r in valid_records:
                ERPPOReference.objects.update_or_create(
                    tenant=r.tenant,
                    po_number=r.po_number,
                    po_line_number=r.po_line_number,
                    defaults=dict(
                        batch=batch,
                        vendor_code=r.vendor_code,
                        item_code=r.item_code,
                        description=r.description,
                        normalized_description=r.normalized_description,
                        quantity=r.quantity,
                        unit_price=r.unit_price,
                        line_amount=r.line_amount,
                        currency=r.currency,
                        status=r.status,
                        is_open=r.is_open,
                        raw_json=r.raw_json,
                    ),
                )
                upserted += 1

        logger.info(
            "POImporter: imported %d valid, %d invalid for batch %s",
            upserted, invalid_count, batch.pk,
        )
        return upserted, invalid_count, errors
