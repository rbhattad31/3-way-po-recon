"""
Management command: seed_three_way_invoices

Seeds realistic McDonald's Saudi Arabia goods invoices for the THREE_WAY
PO reconciliation path. Creates only invoices and supporting master/reference
data — no reconciliation runs, cases, or agent records.

Modes:
  --mode=demo   → 24 deterministic scenarios (default)
  --mode=qa     → 24 deterministic + 30 generated scenarios
  --mode=large  → 24 deterministic + 100 generated scenarios

Usage:
    python manage.py seed_three_way_invoices
    python manage.py seed_three_way_invoices --mode=demo
    python manage.py seed_three_way_invoices --mode=qa
    python manage.py seed_three_way_invoices --mode=large
    python manage.py seed_three_way_invoices --reset
    python manage.py seed_three_way_invoices --seed=42
    python manage.py seed_three_way_invoices --summary

Prerequisites:
    Run `python manage.py seed_rbac` first to create RBAC roles & permissions.

What it creates:
    ✓ AP_PROCESSOR seed user with RBAC role
    ✓ 12 goods-oriented vendors + aliases (OCR variations)
    ✓ Purchase Orders + PO line items
    ✓ GRNs + GRN line items (with receipt variations)
    ✓ Invoices + invoice line items (with extraction metadata)
    ✓ DocumentUpload records
    ✓ ExtractionResult records
    ✓ Raw/normalized extraction JSON payloads
    ✓ AuditEvent records (INVOICE_UPLOADED + EXTRACTION_COMPLETED per invoice)

What it does NOT create:
    ✗ ReconciliationRun / ReconciliationResult
    ✗ APCase / CaseStage
    ✗ ReviewAssignment
    ✗ AgentRun / AgentMessage / ToolCall / DecisionLog / AgentEscalation
    ✗ ManualReviewAction
"""
from __future__ import annotations

import logging
import random
import time

from django.core.management.base import BaseCommand
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Seed THREE_WAY goods invoices for McDonald's Saudi Arabia AP. "
        "Creates invoices, POs, GRNs, vendors — no recon/case/agent records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            type=str,
            default="demo",
            choices=["demo", "qa", "large"],
            help="Seed mode: demo (24), qa (+30), large (+100)",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all THREE_WAY seed data before re-creating",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for deterministic output",
        )
        parser.add_argument(
            "--summary",
            action="store_true",
            help="Print scenario summary table after seeding",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        do_reset = options["reset"]
        rand_seed = options["seed"]
        show_summary = options["summary"]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'=' * 70}\n"
            f"  McDonald's Saudi Arabia — THREE_WAY Invoice Seed Data\n"
            f"  Mode: {mode.upper()} | Reset: {do_reset} | Seed: {rand_seed}\n"
            f"{'=' * 70}\n"
        ))

        start = time.time()

        if do_reset:
            self._reset_data()

        with transaction.atomic():
            self._seed(mode, rand_seed)

        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(
            f"\n{'=' * 70}\n"
            f"  Seeding completed in {elapsed:.1f}s\n"
            f"{'=' * 70}"
        ))

        if show_summary or mode == "demo":
            self._print_summary()

    # ----------------------------------------------------------------
    # Reset
    # ----------------------------------------------------------------

    def _reset_data(self):
        self.stdout.write(self.style.WARNING(
            "  Resetting THREE_WAY seed data..."
        ))

        from apps.extraction.models import ExtractionResult
        from apps.documents.models import (
            GRNLineItem,
            GoodsReceiptNote,
            InvoiceLineItem,
            Invoice,
            PurchaseOrderLineItem,
            PurchaseOrder,
            DocumentUpload,
        )
        from apps.vendors.models import VendorAlias, Vendor

        # Delete THREE_WAY-specific records by prefix
        # Extraction results linked to our uploads
        three_way_uploads = DocumentUpload.objects.filter(
            file_hash__startswith=""  # Can't filter by hash prefix easily
        ).filter(original_filename__contains="_3W-")
        ExtractionResult.objects.filter(
            document_upload__in=three_way_uploads
        ).delete()

        # Audit events linked to THREE_WAY invoices
        from apps.auditlog.models import AuditEvent
        three_way_invoices = Invoice.objects.filter(
            invoice_number__startswith="INV-3W-"
        )
        inv_ids = list(three_way_invoices.values_list("id", flat=True))
        if inv_ids:
            n_audit = AuditEvent.objects.filter(invoice_id__in=inv_ids).delete()[0]
            self.stdout.write(f"  Deleted {n_audit} audit events")

        # Invoice line items and invoices
        InvoiceLineItem.objects.filter(invoice__in=three_way_invoices).delete()
        three_way_invoices.delete()

        # GRN line items and GRNs
        three_way_grns = GoodsReceiptNote.objects.filter(
            grn_number__startswith="GRN-3W-"
        )
        GRNLineItem.objects.filter(grn__in=three_way_grns).delete()
        three_way_grns.delete()

        # PO line items and POs
        three_way_pos = PurchaseOrder.objects.filter(
            po_number__startswith="PO-3W-"
        )
        PurchaseOrderLineItem.objects.filter(
            purchase_order__in=three_way_pos
        ).delete()
        three_way_pos.delete()

        # Document uploads
        three_way_uploads.delete()

        # Vendor aliases + vendors (THREE_WAY-specific)
        VendorAlias.objects.filter(vendor__code__startswith="V3W-").delete()
        Vendor.objects.filter(code__startswith="V3W-").delete()

        self.stdout.write(self.style.SUCCESS("  Reset complete."))

    # ----------------------------------------------------------------
    # Seed
    # ----------------------------------------------------------------

    def _seed(self, mode: str, rand_seed: int):
        from .seed_helpers.constants import SCENARIOS
        from .seed_helpers.helpers import (
            create_ap_processor_user,
            create_three_way_invoices,
            create_vendor_aliases,
            create_vendors,
            collect_stats,
            set_seed,
        )

        set_seed(rand_seed)

        # 1. AP Processor user
        self.stdout.write("  [1/5] Creating AP_PROCESSOR seed user...")
        ap_user = create_ap_processor_user()
        self.stdout.write(self.style.SUCCESS(
            f"        User: {ap_user.email} (role=AP_PROCESSOR)"
        ))

        # 2. Vendors & aliases
        self.stdout.write("  [2/5] Creating goods-oriented vendors & aliases...")
        vendors = create_vendors(ap_user)
        n_aliases = create_vendor_aliases(vendors, ap_user)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(vendors)} vendors, {n_aliases} aliases"
        ))

        # 3. Deterministic scenarios
        self.stdout.write(
            f"  [3/5] Creating THREE_WAY invoices ({len(SCENARIOS)} scenarios)..."
        )
        results = create_three_way_invoices(SCENARIOS, vendors, ap_user)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(results)} scenario record sets created"
        ))

        # 4. QA/Large mode — bulk generation
        if mode in ("qa", "large"):
            extra = 30 if mode == "qa" else 100
            self.stdout.write(
                f"  [4/5] Generating {extra} additional random THREE_WAY scenarios..."
            )
            bulk_results = self._generate_bulk(
                start_num=len(SCENARIOS) + 1,
                count=extra,
                vendors=vendors,
                ap_user=ap_user,
                rand_seed=rand_seed,
            )
            results.update(bulk_results)
            self.stdout.write(self.style.SUCCESS(
                f"        {len(bulk_results)} additional invoices created"
            ))
        else:
            self.stdout.write("  [4/5] Skipping bulk generation (demo mode)")

        # 5. Audit trail events for the governance page
        from apps.documents.models import Invoice
        all_invoices = list(
            Invoice.objects.filter(invoice_number__startswith="INV-3W-")
            .select_related("vendor", "document_upload")
            .order_by("invoice_number")
        )
        self.stdout.write("  [5/5] Creating audit trail events...")
        n_events = self._create_audit_trail(all_invoices, ap_user, rand_seed)
        self.stdout.write(self.style.SUCCESS(
            f"        {n_events} audit events created"
        ))

        # Print stats
        stats = collect_stats(results)
        stats["audit_events"] = n_events
        self._print_stats(stats, ap_user)

    # ----------------------------------------------------------------
    # Audit Trail — governance events for /governance/ page
    # ----------------------------------------------------------------

    def _create_audit_trail(self, invoices, actor, rand_seed: int) -> int:
        """Create INVOICE_UPLOADED + EXTRACTION_COMPLETED AuditEvents.

        Populates the /governance/ audit log with realistic lifecycle
        events for the seeded THREE_WAY invoices.
        """
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType, InvoiceStatus

        rng = random.Random(rand_seed + 3000)
        count = 0

        for inv in invoices:
            # INVOICE_UPLOADED
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=inv.id,
                event_type=AuditEventType.INVOICE_UPLOADED,
                description=(
                    f"Invoice {inv.invoice_number} uploaded from "
                    f"{inv.document_upload.original_filename if inv.document_upload else 'unknown'} "
                    f"(vendor: {inv.vendor.name if inv.vendor else inv.raw_vendor_name or '(missing)'}, "
                    f"path: THREE_WAY)"
                ),
                user=actor,
                invoice_id=inv.id,
                status_before="",
                status_after=InvoiceStatus.UPLOADED,
                metadata={
                    "source": "seed_three_way_invoices",
                    "reconciliation_path": "THREE_WAY",
                    "vendor_code": inv.vendor.code if inv.vendor else "",
                    "file_name": inv.document_upload.original_filename if inv.document_upload else "",
                },
            )
            count += 1

            # EXTRACTION_COMPLETED
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=inv.id,
                event_type=AuditEventType.EXTRACTION_COMPLETED,
                description=(
                    f"Extraction completed for {inv.invoice_number} — "
                    f"confidence {inv.extraction_confidence:.0%}, "
                    f"PO ref: {inv.po_number or '(none)'}, "
                    f"total: SAR {inv.total_amount:,.2f}"
                ),
                user=actor,
                invoice_id=inv.id,
                status_before=InvoiceStatus.UPLOADED,
                status_after=inv.status,
                duration_ms=rng.randint(1200, 4500),
                metadata={
                    "source": "seed_three_way_invoices",
                    "reconciliation_path": "THREE_WAY",
                    "engine": "azure_document_intelligence",
                    "confidence": float(inv.extraction_confidence) if inv.extraction_confidence else 0,
                    "po_detected": bool(inv.po_number),
                    "line_count": inv.line_items.count(),
                },
            )
            count += 1

        return count

    # ----------------------------------------------------------------
    # Bulk Generator
    # ----------------------------------------------------------------

    def _generate_bulk(
        self,
        start_num: int,
        count: int,
        vendors: dict,
        ap_user,
        rand_seed: int,
    ) -> dict:
        """Generate additional random THREE_WAY scenarios."""
        import random as stdlib_random
        from .seed_helpers.constants import (
            COST_CENTERS,
            WAREHOUSES as WH_LIST,
            BRANCHES as BR_LIST,
            GOODS_LINE_ITEMS,
        )
        from .seed_helpers.helpers import (
            create_three_way_invoices,
        )

        rng = stdlib_random.Random(rand_seed + 1000)
        categories = list(GOODS_LINE_ITEMS.keys())
        vendor_codes = list(vendors.keys())
        warehouse_codes = [w["code"] for w in WH_LIST]
        cc_codes = [c["code"] for c in COST_CENTERS]
        branch_codes = [b["code"] for b in BR_LIST]

        po_formats = ["clean", "clean", "clean", "normalized", "hash_prefix",
                       "ocr_damaged", "missing"]
        exception_pools = [
            [],
            [],
            ["RECEIPT_SHORTAGE", "QTY_MISMATCH"],
            ["OVER_RECEIPT"],
            ["GRN_NOT_FOUND"],
            ["DELAYED_RECEIPT"],
            ["PRICE_MISMATCH"],
            ["TAX_MISMATCH"],
            ["AMOUNT_MISMATCH"],
            ["DUPLICATE_INVOICE"],
            ["RECEIPT_LOCATION_MISMATCH"],
        ]
        statuses = [
            "READY_FOR_RECON", "READY_FOR_RECON", "READY_FOR_RECON",
            "VALIDATED", "EXTRACTED",
        ]
        expected_outcomes = [
            "MATCHED", "MATCHED", "PARTIAL_MATCH", "REVIEW_REQUIRED",
            "GRN_EXCEPTION", "AUTO_CLOSE",
        ]

        bulk_scenarios = []
        for i in range(count):
            num = start_num + i
            cat = rng.choice(categories)
            exc = rng.choice(exception_pools)
            fmt = rng.choice(po_formats)
            wh = rng.choice(warehouse_codes)
            special = {}

            if fmt == "missing":
                special = {}
            elif fmt == "ocr_damaged":
                special["ocr_po_variation"] = f"P0-3W-{num:04d}"

            if "GRN_NOT_FOUND" in exc:
                special["skip_grn"] = True
            if "RECEIPT_SHORTAGE" in exc:
                special["receipt_pct"] = round(rng.uniform(0.60, 0.85), 2)
            if "OVER_RECEIPT" in exc:
                special["receipt_pct"] = round(rng.uniform(1.05, 1.20), 2)
            if "DELAYED_RECEIPT" in exc:
                special["grn_delay_days"] = rng.randint(1, 5)
            if "RECEIPT_LOCATION_MISMATCH" in exc:
                other_wh = rng.choice([w for w in warehouse_codes if w != wh])
                special["grn_warehouse"] = other_wh
            if "PRICE_MISMATCH" in exc:
                special["price_inflate_pct"] = rng.randint(5, 20)

            conf = round(rng.uniform(0.45, 0.98), 2)

            bulk_scenarios.append({
                "num": num,
                "tag": f"3W-BULK-{num:04d}",
                "vendor_code": rng.choice(vendor_codes),
                "category": cat,
                "warehouse": wh,
                "cost_center": rng.choice(cc_codes),
                "branch": rng.choice(branch_codes),
                "description": f"Bulk-generated THREE_WAY scenario #{num}",
                "po_format": fmt,
                "extraction_confidence": conf,
                "expected_outcome": rng.choice(expected_outcomes),
                "invoice_status": rng.choice(statuses),
                "n_lines": rng.randint(2, 5),
                "qty_range": (rng.randint(5, 15), rng.randint(20, 60)),
                "exceptions": exc,
                "special": special,
            })

        return create_three_way_invoices(bulk_scenarios, vendors, ap_user)

    # ----------------------------------------------------------------
    # Stats Output
    # ----------------------------------------------------------------

    def _print_stats(self, stats: dict, ap_user) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'─' * 70}\n"
            f"  SEED SUMMARY\n"
            f"{'─' * 70}"
        ))
        rows = [
            ("Vendors created", stats["vendors_created"]),
            ("Vendor aliases created", stats["aliases_created"]),
            ("Invoices created", stats["invoices_created"]),
            ("POs created", stats["pos_created"]),
            ("GRNs created", stats["grns_created"]),
            ("Document uploads created", stats["uploads_created"]),
            ("Extraction results created", stats["extractions_created"]),
            ("Invoice line items", stats["line_items_invoice"]),
            ("PO line items", stats["line_items_po"]),
            ("", ""),
            ("Duplicate-prone invoices", stats["duplicate_invoices"]),
            ("Malformed PO references", stats["malformed_po_refs"]),
            ("PO-agent trigger invoices", stats["po_agent_trigger"]),
            ("GRN-agent trigger invoices", stats["grn_agent_trigger"]),
            ("High-value invoices", stats["high_value_invoices"]),
            ("Warehouse mismatch invoices", stats["warehouse_mismatch"]),
            ("Incomplete invoices", stats["incomplete_invoices"]),
            ("", ""),
            ("Low confidence (<0.70)", stats["low_confidence"]),
            ("Medium confidence (0.70–0.85)", stats["medium_confidence"]),
            ("High confidence (>0.85)", stats["high_confidence"]),
            ("", ""),
            ("Audit events created", stats.get("audit_events", 0)),
            ("", ""),
            ("Audit role used", "AP_PROCESSOR"),
            ("Audit user used", ap_user.email),
        ]
        for label, value in rows:
            if label == "":
                continue
            self.stdout.write(f"    {label:.<45} {value}")

    # ----------------------------------------------------------------
    # Summary Table
    # ----------------------------------------------------------------

    def _print_summary(self) -> None:
        from apps.documents.models import Invoice

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'=' * 120}\n"
            f"  THREE_WAY INVOICE SCENARIO SUMMARY\n"
            f"{'=' * 120}"
        ))
        self.stdout.write(
            f"  {'#':>3} {'Invoice':18} {'Vendor':35} {'PO Ref':16} "
            f"{'Status':18} {'Conf':5} {'Warehouse':24}"
        )
        self.stdout.write("  " + "─" * 116)

        invoices = (
            Invoice.objects
            .filter(invoice_number__startswith="INV-3W-")
            .select_related("vendor")
            .order_by("invoice_number")
        )
        for inv in invoices:
            vendor_name = inv.vendor.name[:33] if inv.vendor else inv.raw_vendor_name[:33] or "(missing)"
            po_ref = inv.po_number[:14] if inv.po_number else "(none)"
            conf = f"{inv.extraction_confidence:.2f}" if inv.extraction_confidence else "N/A"
            # Extract warehouse from raw JSON
            wh = ""
            if inv.extraction_raw_json and isinstance(inv.extraction_raw_json, dict):
                wh_data = inv.extraction_raw_json.get("warehouse_text", {})
                if isinstance(wh_data, dict):
                    wh = wh_data.get("value", "")[:22]
                elif isinstance(wh_data, str):
                    wh = wh_data[:22]

            self.stdout.write(
                f"  {inv.invoice_number[7:]:>3} {inv.invoice_number:18} "
                f"{vendor_name:35} {po_ref:16} "
                f"{inv.status:18} {conf:>5} {wh:24}"
            )

        total = invoices.count()
        self.stdout.write(f"\n  Total: {total} THREE_WAY invoices")

        # Distribution
        from django.db.models import Count
        self.stdout.write(self.style.MIGRATE_HEADING("\n  Distribution:"))

        by_status = (
            invoices.values("status")
            .annotate(c=Count("id"))
            .order_by("-c")
        )
        self.stdout.write(
            "    By Status:     "
            + "  |  ".join(f"{r['status']}: {r['c']}" for r in by_status)
        )

        by_vendor = (
            invoices.values("vendor__name")
            .annotate(c=Count("id"))
            .order_by("-c")[:6]
        )
        self.stdout.write(
            "    By Vendor:     "
            + "  |  ".join(
                f"{(r['vendor__name'] or 'None')[:20]}: {r['c']}"
                for r in by_vendor
            )
        )
