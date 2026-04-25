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
                tenant=batch.tenant,
                cost_center_code=cc_code,
                cost_center_name=str(row.get("cost_center_name", "")).strip(),
                department=str(row.get("department", "")).strip(),
                business_unit=str(row.get("business_unit", "")).strip(),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        upserted = 0
        if valid_records:
            for r in valid_records:
                ERPCostCenterReference.objects.update_or_create(
                    tenant=r.tenant,
                    cost_center_code=r.cost_center_code,
                    defaults=dict(
                        batch=batch,
                        cost_center_name=r.cost_center_name,
                        department=r.department,
                        business_unit=r.business_unit,
                        is_active=r.is_active,
                        raw_json=r.raw_json,
                    ),
                )
                upserted += 1

        logger.info(
            "CostCenterImporter: imported %d valid, %d invalid for batch %s",
            upserted, invalid_count, batch.pk,
        )
        return upserted, invalid_count, errors
