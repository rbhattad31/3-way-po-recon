"""Flush imported ERP reference data and related records.

Deletes:
- ERPReferenceImportBatch rows (and cascaded reference rows)
- ERPVendorReference / ERPItemReference / ERPTaxCodeReference /
  ERPCostCenterReference / ERPPOReference (via batch cascade)
- VendorAliasMapping / ItemAliasMapping linked to imported references
- AuditEvent rows linked to ERPReferenceImportBatch entities

Usage:
    python manage.py flush_erp_imports
    python manage.py flush_erp_imports --confirm
    python manage.py flush_erp_imports --tenant-id 1 --confirm
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Delete ERP imported reference data and related records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Skip the interactive confirmation prompt",
        )
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=None,
            help="Flush imports only for a specific tenant (CompanyProfile ID)",
        )

    def handle(self, *args, **options):
        tenant_id = options.get("tenant_id")

        if not options["confirm"]:
            if tenant_id is None:
                answer = input(
                    "This will DELETE ERP import batches, imported reference rows,\n"
                    "linked alias mappings, and related audit events for all tenants.\n"
                    "Type 'yes' to continue: "
                )
            else:
                answer = input(
                    f"This will DELETE ERP import data for tenant_id={tenant_id},\n"
                    "including linked alias mappings and related audit events.\n"
                    "Type 'yes' to continue: "
                )
            if answer.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        scope_msg = (
            "all tenants" if tenant_id is None else f"tenant_id={tenant_id}"
        )
        self.stdout.write(self.style.WARNING(f"Flushing ERP import data for {scope_msg}..."))

        with transaction.atomic():
            summary = self._flush(tenant_id=tenant_id)

        self.stdout.write(self.style.SUCCESS("Flush complete."))
        for key, value in summary.items():
            self.stdout.write(f"  {key}: {value}")

    def _flush(self, tenant_id=None):
        from apps.auditlog.models import AuditEvent
        from apps.posting_core.models import (
            ERPReferenceImportBatch,
            ERPVendorReference,
            ERPItemReference,
            ItemAliasMapping,
            VendorAliasMapping,
        )

        batch_qs = ERPReferenceImportBatch.objects.all()
        if tenant_id is not None:
            batch_qs = batch_qs.filter(tenant_id=tenant_id)

        batch_ids = list(batch_qs.values_list("id", flat=True))
        if not batch_ids:
            return {
                "ERPReferenceImportBatch": 0,
                "VendorAliasMapping": 0,
                "ItemAliasMapping": 0,
                "AuditEvent": 0,
            }

        vendor_ref_ids = list(
            ERPVendorReference.objects.filter(batch_id__in=batch_ids).values_list("id", flat=True)
        )
        item_ref_ids = list(
            ERPItemReference.objects.filter(batch_id__in=batch_ids).values_list("id", flat=True)
        )

        vendor_alias_qs = VendorAliasMapping.objects.filter(vendor_reference_id__in=vendor_ref_ids)
        if tenant_id is not None:
            vendor_alias_qs = vendor_alias_qs.filter(tenant_id=tenant_id)
        deleted_vendor_alias = vendor_alias_qs.delete()[0]

        item_alias_qs = ItemAliasMapping.objects.filter(item_reference_id__in=item_ref_ids)
        if tenant_id is not None:
            item_alias_qs = item_alias_qs.filter(tenant_id=tenant_id)
        deleted_item_alias = item_alias_qs.delete()[0]

        deleted_audit = AuditEvent.objects.filter(
            entity_type="ERPReferenceImportBatch",
            entity_id__in=batch_ids,
        ).delete()[0]

        deleted_batches = batch_qs.delete()[0]

        return {
            "ERPReferenceImportBatch_and_cascades": deleted_batches,
            "VendorAliasMapping": deleted_vendor_alias,
            "ItemAliasMapping": deleted_item_alias,
            "AuditEvent": deleted_audit,
        }
