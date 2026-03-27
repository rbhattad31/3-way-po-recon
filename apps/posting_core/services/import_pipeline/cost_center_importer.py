"""Cost center importer — imports cost center references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from apps.posting_core.models import ERPCostCenterReference, ERPReferenceImportBatch
from apps.posting_core.services.import_pipeline.import_parsers import safe_bool
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class CostCenterImporter:
    """Imports cost center reference rows into ERPCostCenterReference."""

    @staticmethod
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        valid_records: List[ERPCostCenterReference] = []
        errors: List[str] = []
        invalid_count = 0

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("COST_CENTER", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            valid_records.append(ERPCostCenterReference(
                batch=batch,
                cost_center_code=str(row.get("cost_center_code", "")).strip(),
                cost_center_name=str(row.get("cost_center_name", "")).strip(),
                department=str(row.get("department", "")).strip(),
                business_unit=str(row.get("business_unit", "")).strip(),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        if valid_records:
            ERPCostCenterReference.objects.bulk_create(valid_records)

        logger.info(
            "CostCenterImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
