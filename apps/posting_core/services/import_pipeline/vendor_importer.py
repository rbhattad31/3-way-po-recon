"""Vendor importer — imports vendor references from parsed rows.

On every import the pipeline also atomically upserts a matching Vendor master
record and ensures a canonical VendorAliasMapping entry exists, so that
extraction and reconciliation can resolve invoice.vendor automatically without
any manual setup step.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.db import transaction

from apps.core.utils import normalize_string
from apps.posting_core.models import ERPReferenceImportBatch, ERPVendorReference, VendorAliasMapping
from apps.posting_core.services.import_pipeline.import_parsers import normalize_text, safe_bool
from apps.posting_core.services.import_pipeline.import_validators import validate_row

logger = logging.getLogger(__name__)


class VendorImporter:
    """Imports vendor reference rows into ERPVendorReference.

    Side-effects (atomic with the main insert):
    - Upserts a Vendor master record for each imported vendor_code.
    - Ensures a VendorAliasMapping row exists linking vendor_name ->
      Vendor + ERPVendorReference so extraction/recon resolve vendor FKs
      automatically.
    """

    @staticmethod
    @transaction.atomic
    def import_rows(
        batch: ERPReferenceImportBatch,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int, List[str]]:
        """Import parsed vendor rows.

        Returns (valid_count, invalid_count, error_messages).
        """
        from apps.vendors.models import Vendor

        valid_records: List[ERPVendorReference] = []
        errors: List[str] = []
        invalid_count = 0
        parsed_rows: List[Dict[str, Any]] = []
        # Track vendor_codes seen in this file to catch intra-file duplicates
        seen_codes: set = set()

        for idx, row in enumerate(rows, start=1):
            is_valid, row_errors = validate_row("VENDOR", row, idx)
            if not is_valid:
                errors.extend(row_errors)
                invalid_count += 1
                continue

            vendor_code = str(row.get("vendor_code", "")).strip()
            vendor_name = str(row.get("vendor_name", "")).strip()

            # Skip duplicate vendor_code within the same file
            if vendor_code in seen_codes:
                errors.append(f"Row {idx}: duplicate vendor_code '{vendor_code}' in file — skipped")
                invalid_count += 1
                continue
            seen_codes.add(vendor_code)

            valid_records.append(ERPVendorReference(
                batch=batch,
                vendor_code=vendor_code,
                vendor_name=vendor_name,
                normalized_vendor_name=normalize_text(vendor_name),
                vendor_group=str(row.get("vendor_group", "")).strip(),
                country_code=str(row.get("country_code", "")).strip()[:3],
                is_active=safe_bool(row.get("is_active")),
                payment_terms=str(row.get("payment_terms", "")).strip(),
                currency=str(row.get("currency", "")).strip()[:10],
                raw_json=row,
            ))
            parsed_rows.append(row)

        if valid_records:
            # Check which vendor_codes already exist in *this batch* (re-run safety)
            existing_in_batch = set(
                ERPVendorReference.objects
                .filter(batch=batch, vendor_code__in=[r.vendor_code for r in valid_records])
                .values_list("vendor_code", flat=True)
            )
            if existing_in_batch:
                logger.warning(
                    "VendorImporter: %d vendor_code(s) already exist in batch %s — skipping: %s",
                    len(existing_in_batch), batch.pk, sorted(existing_in_batch),
                )
                valid_records = [r for r in valid_records if r.vendor_code not in existing_in_batch]
                parsed_rows = [
                    r for r in parsed_rows
                    if str(r.get("vendor_code", "")).strip() not in existing_in_batch
                ]

            ERPVendorReference.objects.bulk_create(valid_records)
            # Re-query to get DB-assigned PKs (bulk_create does not populate
            # them on MySQL, unlike PostgreSQL).
            created_refs = {
                r.vendor_code: r
                for r in ERPVendorReference.objects.filter(batch=batch)
            }

            # --- Upsert Vendor master records and alias mappings ---
            for ref_obj, row in zip(valid_records, parsed_rows):
                ref = created_refs.get(ref_obj.vendor_code, ref_obj)
                vendor_code = ref.vendor_code
                vendor_name = ref.vendor_name
                norm_name = normalize_string(vendor_name)

                # Upsert Vendor: update name/fields if code already exists
                vendor, vendor_created = Vendor.objects.update_or_create(
                    code=vendor_code,
                    defaults=dict(
                        name=vendor_name,
                        normalized_name=norm_name,
                        country=ref.country_code,
                        currency=ref.currency or "USD",
                        payment_terms=ref.payment_terms,
                        is_active=ref.is_active,
                    ),
                )

                # Ensure a canonical alias entry for the primary vendor_name
                norm_alias = normalize_string(vendor_name)
                alias, alias_created = VendorAliasMapping.objects.get_or_create(
                    normalized_alias=norm_alias,
                    defaults=dict(
                        alias_text=vendor_name,
                        vendor=vendor,
                        vendor_reference=ref,
                        source="erp_import",
                        confidence=1.0,
                        is_active=True,
                    ),
                )
                # If alias already existed but vendor FK was missing, back-fill it
                if not alias_created and (alias.vendor_id != vendor.pk or alias.vendor_reference_id != ref.pk):
                    alias.vendor = vendor
                    alias.vendor_reference = ref
                    alias.save(update_fields=["vendor", "vendor_reference", "updated_at"])

                if vendor_created:
                    logger.info("VendorImporter: created Vendor %s (%s)", vendor_code, vendor_name)
                else:
                    logger.debug("VendorImporter: updated Vendor %s (%s)", vendor_code, vendor_name)

        logger.info(
            "VendorImporter: imported %d valid, %d invalid for batch %s",
            len(valid_records), invalid_count, batch.pk,
        )
        return len(valid_records), invalid_count, errors
