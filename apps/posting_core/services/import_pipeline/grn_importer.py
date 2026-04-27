"""GRN importer -- imports GRN line items from parsed ERP rows."""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from django.db import transaction

from apps.posting_core.models import ERPGRNReference, ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.import_parsers import (
    safe_date,
    safe_decimal,
)
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


def _json_safe(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert any non-JSON-serializable values (date, Decimal) to JSON-safe types."""
    result = {}
    for k, v in row.items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result


class GRNImporter:
    """Imports GRN reference rows into ERPGRNReference.

    Natural key: (tenant, grn_number, po_voucher_no, po_line_number).
    Skips intra-file duplicates; upserts on matching natural key.
    """

    @staticmethod
    @transaction.atomic
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        errors: List[str] = []
        invalid_count = 0
        seen_keys: set = set()
        upserted = 0

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("GRN", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            grn_number = str(row.get("grn_number", "")).strip()
            po_voucher_no = str(row.get("po_voucher_no", "") or "").strip()
            po_line_number = str(row.get("po_line_number", "") or "").strip()
            key = (grn_number, po_voucher_no, po_line_number)

            if key in seen_keys:
                errors.append(
                    f"Row {idx}: duplicate (grn_number, po_voucher_no, po_line_number) "
                    f"'{grn_number}/{po_voucher_no}/{po_line_number}' in file -- skipped"
                )
                invalid_count += 1
                continue
            seen_keys.add(key)

            ERPGRNReference.objects.update_or_create(
                tenant=batch.tenant,
                grn_number=grn_number,
                po_voucher_no=po_voucher_no,
                po_line_number=po_line_number,
                defaults=dict(
                    batch=batch,
                    po_number=str(row.get("po_number", "") or "").strip(),
                    receipt_date=safe_date(row.get("receipt_date")),
                    supplier_code=str(row.get("supplier_code", "") or "").strip(),
                    supplier_name=str(row.get("supplier_name", "") or "").strip(),
                    item_code=str(row.get("item_code", "") or "").strip(),
                    item_description=str(row.get("item_description", "") or "").strip(),
                    order_qty=safe_decimal(row.get("order_qty")),
                    grn_qty=safe_decimal(row.get("grn_qty")),
                    grn_price=safe_decimal(row.get("grn_price")),
                    grn_value=safe_decimal(row.get("grn_value")),
                    currency=str(row.get("currency", "") or "").strip()[:10],
                    po_date=safe_date(row.get("po_date")),
                    raw_json=_json_safe(row),
                ),
            )
            upserted += 1

        logger.info(
            "GRNImporter: batch %s -- %d upserted, %d invalid",
            batch.pk,
            upserted,
            invalid_count,
        )
        return upserted, invalid_count, errors
