"""Cost center importer — imports cost center references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.db import transaction

from apps.posting_core.models import ERPCostCenterReference, ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.import_parsers import safe_bool
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class CostCenterImporter:
    """Imports cost center reference rows into ERPCostCenterReference.

    Natural key: cost_center_code.
    Skips intra-file duplicates and rows already present in this batch.
    """

    @staticmethod
    @transaction.atomic
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        valid_records: List[ERPCostCenterReference] = []
        errors: List[str] = []
        invalid_count = 0
        seen_codes: set = set()

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("COST_CENTER", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            cc_code = str(row.get("cost_center_code", "")).strip()

            if cc_code in seen_codes:
                errors.append(f"Row {idx}: duplicate cost_center_code '{cc_code}' in file — skipped")
                invalid_count += 1
                continue
            seen_codes.add(cc_code)

            valid_records.append(ERPCostCenterReference(
                batch=batch,
                cost_center_code=cc_code,
                cost_center_name=str(row.get("cost_center_name", "")).strip(),
                department=str(row.get("department", "")).strip(),
                business_unit=str(row.get("business_unit", "")).strip(),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        if valid_records:
            existing_codes = set(
                ERPCostCenterReference.objects
                .filter(batch=batch, cost_center_code__in=[r.cost_center_code for r in valid_records])
                .values_list("cost_center_code", flat=True)
            )
            if existing_codes:
                logger.warning(
                    "CostCenterImporter: %d cost_center_code(s) already in batch %s — skipping: %s",
                    len(existing_codes), batch.pk, sorted(existing_codes),
                )
                valid_records = [r for r in valid_records if r.cost_center_code not in existing_codes]

            if valid_records:
                ERPCostCenterReference.objects.bulk_create(valid_records)

        logger.info(
            "CostCenterImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
