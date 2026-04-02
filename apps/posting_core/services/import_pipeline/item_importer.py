"""Item importer — imports item/service references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.db import transaction

from apps.posting_core.models import ERPItemReference, ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.import_parsers import normalize_text, safe_bool
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class ItemImporter:
    """Imports item reference rows into ERPItemReference.

    Natural key: item_code.
    Skips intra-file duplicates and rows already present in this batch.
    """

    @staticmethod
    @transaction.atomic
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        valid_records: List[ERPItemReference] = []
        errors: List[str] = []
        invalid_count = 0
        seen_codes: set = set()

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("ITEM", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            item_code = str(row.get("item_code", "")).strip()
            item_name = str(row.get("item_name", "")).strip()

            if item_code in seen_codes:
                errors.append(f"Row {idx}: duplicate item_code '{item_code}' in file — skipped")
                invalid_count += 1
                continue
            seen_codes.add(item_code)

            valid_records.append(ERPItemReference(
                batch=batch,
                item_code=item_code,
                item_name=item_name,
                normalized_item_name=normalize_text(item_name),
                item_type=str(row.get("item_type", "")).strip(),
                category=str(row.get("category", "")).strip(),
                uom=str(row.get("uom", "")).strip(),
                tax_code=str(row.get("tax_code", "")).strip(),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        if valid_records:
            existing_codes = set(
                ERPItemReference.objects
                .filter(batch=batch, item_code__in=[r.item_code for r in valid_records])
                .values_list("item_code", flat=True)
            )
            if existing_codes:
                logger.warning(
                    "ItemImporter: %d item_code(s) already in batch %s — skipping: %s",
                    len(existing_codes), batch.pk, sorted(existing_codes),
                )
                valid_records = [r for r in valid_records if r.item_code not in existing_codes]

            if valid_records:
                ERPItemReference.objects.bulk_create(valid_records)

        logger.info(
            "ItemImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
