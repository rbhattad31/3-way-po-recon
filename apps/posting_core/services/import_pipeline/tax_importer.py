"""Tax code importer — imports tax code references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from apps.posting_core.models import ERPReferenceImportBatch, ERPTaxCodeReference
from apps.posting_core.services.import_pipeline.import_parsers import safe_bool, safe_decimal
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class TaxImporter:
    """Imports tax code reference rows into ERPTaxCodeReference."""

    @staticmethod
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        valid_records: List[ERPTaxCodeReference] = []
        errors: List[str] = []
        invalid_count = 0

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("TAX", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            valid_records.append(ERPTaxCodeReference(
                batch=batch,
                tax_code=str(row.get("tax_code", "")).strip(),
                tax_label=str(row.get("tax_label", "")).strip() or str(row.get("tax_code", "")).strip(),
                country_code=str(row.get("country_code", "")).strip()[:3],
                rate=safe_decimal(row.get("rate")),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        if valid_records:
            ERPTaxCodeReference.objects.bulk_create(valid_records)

        logger.info(
            "TaxImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
