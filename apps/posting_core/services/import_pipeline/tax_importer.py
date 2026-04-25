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
                tenant=batch.tenant,
                tax_code=tax_code,
                tax_label=str(row.get("tax_label", "")).strip() or tax_code,
                country_code=str(row.get("country_code", "")).strip()[:3],
                rate=safe_decimal(row.get("rate")),
                is_active=safe_bool(row.get("is_active")),
                raw_json=row,
            ))

        upserted = 0
        if valid_records:
            for r in valid_records:
                ERPTaxCodeReference.objects.update_or_create(
                    tenant=r.tenant,
                    tax_code=r.tax_code,
                    defaults=dict(
                        batch=batch,
                        tax_label=r.tax_label,
                        country_code=r.country_code,
                        rate=r.rate,
                        is_active=r.is_active,
                        raw_json=r.raw_json,
                    ),
                )
                upserted += 1

        logger.info(
            "TaxImporter: imported %d valid, %d invalid for batch %s",
            upserted, invalid_count, batch.pk,
        )
        return upserted, invalid_count, errors
