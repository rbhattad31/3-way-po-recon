"""Tax code importer — imports tax code references from parsed rows."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.db import transaction

from apps.posting_core.models import ERPReferenceImportBatch, ERPTaxCodeReference
from apps.posting_core.services.import_pipeline.import_parsers import safe_bool, safe_decimal
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class TaxImporter:
    """Imports tax code reference rows into ERPTaxCodeReference.

    Natural key: tax_code.
    Skips intra-file duplicates and rows already present in this batch.
    """

    @staticmethod
    @transaction.atomic
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        valid_records: List[ERPTaxCodeReference] = []
        errors: List[str] = []
        invalid_count = 0
        seen_codes: set = set()

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("TAX", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            tax_code = str(row.get("tax_code", "")).strip()

            if tax_code in seen_codes:
                errors.append(f"Row {idx}: duplicate tax_code '{tax_code}' in file — skipped")
                invalid_count += 1
                continue
            seen_codes.add(tax_code)

            valid_records.append(ERPTaxCodeReference(
                batch=batch,
                tax_code=tax_code,
                tax_label=str(row.get("tax_label", "")).strip() or tax_code,
                country_code=str(row.get("country_code", "")).strip()[:3],
                rate=safe_decimal(row.get("rate")),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        if valid_records:
            existing_codes = set(
                ERPTaxCodeReference.objects
                .filter(batch=batch, tax_code__in=[r.tax_code for r in valid_records])
                .values_list("tax_code", flat=True)
            )
            if existing_codes:
                logger.warning(
                    "TaxImporter: %d tax_code(s) already in batch %s — skipping: %s",
                    len(existing_codes), batch.pk, sorted(existing_codes),
                )
                valid_records = [r for r in valid_records if r.tax_code not in existing_codes]

            if valid_records:
                ERPTaxCodeReference.objects.bulk_create(valid_records)

        logger.info(
            "TaxImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
