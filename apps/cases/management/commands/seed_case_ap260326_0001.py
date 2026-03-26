"""
Seed data for case AP-260326-0001.

Creates: Vendor + aliases, DocumentUpload, Invoice (PO-backed, PO not found in
system) + line items, ExtractionResult, ExtractionApproval, ReconciliationRun,
ReconciliationResult (UNMATCHED / PO_NOT_FOUND), ReconciliationException,
APCase, APCaseStages, APCaseDecisions, APCaseSummary.

Scenario:
    Thermo Fisher Scientific India raises invoice TFS/AMC/2025/HYD/00312 for
    an Annual Maintenance Contract, referencing PO AMC-TFS-BEL-2025-HYD-089.
    That PO does not exist in the system, producing a PO_NOT_FOUND exception
    and an UNMATCHED result routed to AP Review.

    Invoice total: INR 1,065,540.00 (subtotal 903,000 + 18% GST 162,540).
    Processing path: TWO_WAY | Reconciliation mode: THREE_WAY.
    Case status: READY_FOR_REVIEW | Priority: HIGH.

Usage:
    python manage.py seed_case_ap260326_0001
"""

import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import (
    APCase,
    APCaseDecision,
    APCaseStage,
    APCaseSummary,
)
from apps.core.enums import (
    CasePriority,
    CaseStageType,
    CaseStatus,
    DecisionSource,
    DecisionType,
    DocumentType,
    ExceptionSeverity,
    ExceptionType,
    ExtractionApprovalStatus,
    FileProcessingState,
    InvoiceStatus,
    InvoiceType,
    MatchStatus,
    PerformedByType,
    ProcessingPath,
    ReconciliationMode,
    ReconciliationModeApplicability,
    ReconciliationRunStatus,
    SourceChannel,
    StageStatus,
)
from apps.documents.models import (
    DocumentUpload,
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.extraction.models import ExtractionApproval, ExtractionResult
from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationException,
    ReconciliationResult,
    ReconciliationRun,
)
from apps.vendors.models import Vendor, VendorAlias


class Command(BaseCommand):
    help = "Seed data for case AP-260326-0001 (Thermo Fisher, UNMATCHED / PO_NOT_FOUND)"

    def handle(self, *args, **options):
        now = timezone.now()
        admin_user = User.objects.get(email="admin@mcd-ksa.com")

        # -----------------------------------------------------------
        # 1. Vendor
        # -----------------------------------------------------------
        vendor, _ = Vendor.objects.update_or_create(
            code="V-TFS-001",
            defaults=dict(
                name="Thermo Fisher Scientific India Pvt. Ltd.",
                normalized_name="thermo fisher scientific india pvt ltd",
                tax_id="AABCT7845B1ZK",
                address=(
                    "Plot No. 4, Phase 2, IDA Cherlapally\n"
                    "Hyderabad, Telangana 500051\n"
                    "India"
                ),
                country="IN",
                currency="INR",
                payment_terms="Net 60",
                contact_email="ar.india@thermofisher.com",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  Vendor: {vendor.name} (pk={vendor.pk})")

        for alias in [
            "Thermo Fisher Scientific",
            "ThermoFisher Scientific India",
            "Thermo Fisher India",
            "THERMO FISHER SCIENTIFIC INDIA PVT LTD",
            "Thermo Fisher Sci. India",
            "TFS India",
        ]:
            VendorAlias.objects.get_or_create(
                vendor=vendor,
                alias_name=alias,
                defaults=dict(
                    normalized_alias=alias.lower().strip(),
                    source="SEED",
                    created_by=admin_user,
                ),
            )
        self.stdout.write("  Vendor aliases: 6")

        # -----------------------------------------------------------
        # 2. Purchase Order (AMC-TFS-BEL-2025-HYD-089)
        #    Created so that reprocessing can find and match it.
        # -----------------------------------------------------------
        po, _ = PurchaseOrder.objects.update_or_create(
            po_number="AMC-TFS-BEL-2025-HYD-089",
            defaults=dict(
                normalized_po_number="amc-tfs-bel-2025-hyd-089",
                vendor=vendor,
                po_date=(now - timedelta(days=45)).date(),
                currency="INR",
                total_amount=Decimal("1065540.00"),
                tax_amount=Decimal("162540.00"),
                status="OPEN",
                buyer_name="Srinivas Rao",
                department="Laboratory Operations -- Hyderabad",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  PurchaseOrder: {po.po_number} (pk={po.pk})")

        # PO line items mirror invoice exactly for a clean match on reprocess
        po_line_defs = [
            {
                "line_number": 1,
                "item_code": "TFS-AMC-BSC-001",
                "description": (
                    "Annual Maintenance Contract -- Biosafety Cabinets "
                    "(Class II Type A2)"
                ),
                "quantity": Decimal("2"),
                "unit_price": Decimal("185000.0000"),
                "tax_amount": Decimal("66600.00"),
                "line_amount": Decimal("370000.00"),
                "unit_of_measure": "EA",
                "item_category": "AMC_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "line_number": 2,
                "item_code": "TFS-AMC-PCR-002",
                "description": (
                    "Preventive Maintenance Visit -- PCR/RT-PCR Analyzers "
                    "Quarterly Service"
                ),
                "quantity": Decimal("4"),
                "unit_price": Decimal("82500.0000"),
                "tax_amount": Decimal("59400.00"),
                "line_amount": Decimal("330000.00"),
                "unit_of_measure": "EA",
                "item_category": "AMC_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "line_number": 3,
                "item_code": "TFS-CAL-LWB-003",
                "description": (
                    "Calibration and Certification Services -- "
                    "Laboratory Weighing Balances"
                ),
                "quantity": Decimal("6"),
                "unit_price": Decimal("25500.0000"),
                "tax_amount": Decimal("27540.00"),
                "line_amount": Decimal("153000.00"),
                "unit_of_measure": "EA",
                "item_category": "CALIBRATION_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "line_number": 4,
                "item_code": "TFS-CON-HEPA-004",
                "description": (
                    "HEPA Filter Replacement Kit -- "
                    "Biosafety Cabinet Consumable (Annual Supply)"
                ),
                "quantity": Decimal("10"),
                "unit_price": Decimal("5000.0000"),
                "tax_amount": Decimal("9000.00"),
                "line_amount": Decimal("50000.00"),
                "unit_of_measure": "EA",
                "item_category": "LAB_CONSUMABLES",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ]

        PurchaseOrderLineItem.objects.filter(purchase_order=po).delete()
        po_lines = []
        for pld in po_line_defs:
            pl = PurchaseOrderLineItem.objects.create(purchase_order=po, **pld)
            po_lines.append(pl)
        self.stdout.write(f"  PO Lines: {len(po_lines)}")

        # -----------------------------------------------------------
        # 3. GRN (linked to PO -- full receipt)
        # -----------------------------------------------------------
        grn, _ = GoodsReceiptNote.objects.update_or_create(
            grn_number="GRN-HYD-2025-AMC-0089",
            defaults=dict(
                purchase_order=po,
                vendor=vendor,
                receipt_date=(now - timedelta(days=10)).date(),
                status="RECEIVED",
                warehouse="HYD-LAB-01 Hyderabad Laboratory",
                receiver_name="Priya Venkatesh",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  GRN: {grn.grn_number} (pk={grn.pk})")

        GRNLineItem.objects.filter(grn=grn).delete()
        grn_line_defs = [
            {
                "line_number": 1,
                "po_line": po_lines[0],
                "item_code": "TFS-AMC-BSC-001",
                "description": (
                    "Annual Maintenance Contract -- Biosafety Cabinets "
                    "(Class II Type A2)"
                ),
                "quantity_received": Decimal("2"),
                "quantity_accepted": Decimal("2"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
            {
                "line_number": 2,
                "po_line": po_lines[1],
                "item_code": "TFS-AMC-PCR-002",
                "description": (
                    "Preventive Maintenance Visit -- PCR/RT-PCR Analyzers "
                    "Quarterly Service"
                ),
                "quantity_received": Decimal("4"),
                "quantity_accepted": Decimal("4"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
            {
                "line_number": 3,
                "po_line": po_lines[2],
                "item_code": "TFS-CAL-LWB-003",
                "description": (
                    "Calibration and Certification Services -- "
                    "Laboratory Weighing Balances"
                ),
                "quantity_received": Decimal("6"),
                "quantity_accepted": Decimal("6"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
            {
                "line_number": 4,
                "po_line": po_lines[3],
                "item_code": "TFS-CON-HEPA-004",
                "description": (
                    "HEPA Filter Replacement Kit -- "
                    "Biosafety Cabinet Consumable (Annual Supply)"
                ),
                "quantity_received": Decimal("10"),
                "quantity_accepted": Decimal("10"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
        ]
        for gld in grn_line_defs:
            GRNLineItem.objects.create(grn=grn, **gld)
        self.stdout.write(f"  GRN Lines: {len(grn_line_defs)}")

        # -----------------------------------------------------------
        # 5. DocumentUpload (simulated PDF)
        # -----------------------------------------------------------
        fake_hash = hashlib.sha256(
            b"seed-ap260326-0001-tfs-amc-hyd-00312"
        ).hexdigest()
        doc_upload, _ = DocumentUpload.objects.update_or_create(
            file_hash=fake_hash,
            defaults=dict(
                original_filename="TFS_AMC_2025_HYD_00312.pdf",
                file_size=421_888,
                content_type="application/pdf",
                document_type=DocumentType.INVOICE,
                processing_state=FileProcessingState.COMPLETED,
                processing_message="Extraction completed successfully",
                uploaded_by=admin_user,
            ),
        )
        self.stdout.write(f"  DocumentUpload: pk={doc_upload.pk}")

        # -----------------------------------------------------------
        # 6. Invoice
        # -----------------------------------------------------------
        invoice, _ = Invoice.objects.update_or_create(
            invoice_number="TFS/AMC/2025/HYD/00312",
            defaults=dict(
                document_upload=doc_upload,
                vendor=vendor,
                # Raw extracted values
                raw_vendor_name="Thermo Fisher Scientific India Pvt. Ltd.",
                raw_invoice_number="TFS/AMC/2025/HYD/00312",
                raw_invoice_date="2025-03-12",
                raw_po_number="AMC-TFS-BEL-2025-HYD-089",
                raw_currency="INR",
                raw_subtotal="9,03,000.00",
                raw_tax_amount="1,62,540.00",
                raw_total_amount="10,65,540.00",
                # Normalized values
                normalized_invoice_number="tfs/amc/2025/hyd/00312",
                invoice_date=now.date() - timedelta(days=14),
                po_number="AMC-TFS-BEL-2025-HYD-089",
                normalized_po_number="amc-tfs-bel-2025-hyd-089",
                currency="INR",
                subtotal=Decimal("903000.00"),
                tax_amount=Decimal("162540.00"),
                total_amount=Decimal("1065540.00"),
                # Status & confidence
                status=InvoiceStatus.RECONCILED,
                extraction_confidence=0.91,
                extraction_remarks=(
                    "High confidence extraction. "
                    "PO reference detected: AMC-TFS-BEL-2025-HYD-089."
                ),
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  Invoice: {invoice.invoice_number} (pk={invoice.pk})")

        # -----------------------------------------------------------
        # 7. Invoice Line Items
        #    AMC invoice for lab instruments -- Hyderabad facility
        #    Subtotal: 903,000 INR | GST 18%: 162,540 INR | Total: 1,065,540 INR
        # -----------------------------------------------------------
        line_defs = [
            {
                "line_number": 1,
                "description": (
                    "Annual Maintenance Contract -- Biosafety Cabinets "
                    "(Class II Type A2)"
                ),
                "normalized_description": (
                    "annual maintenance contract -- biosafety cabinets "
                    "(class ii type a2)"
                ),
                "raw_description": (
                    "Annual Maintenance Contract - Biosafety Cabinets "
                    "(Class II Type A2)"
                ),
                "quantity": Decimal("2"),
                "unit_price": Decimal("185000.0000"),
                "tax_amount": Decimal("66600.00"),    # 18% GST on 370,000
                "line_amount": Decimal("370000.00"),
                "raw_quantity": "2",
                "raw_unit_price": "1,85,000.00",
                "raw_tax_amount": "66,600.00",
                "raw_line_amount": "3,70,000.00",
                "item_category": "AMC_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
                "extraction_confidence": 0.93,
            },
            {
                "line_number": 2,
                "description": (
                    "Preventive Maintenance Visit -- PCR/RT-PCR Analyzers "
                    "Quarterly Service"
                ),
                "normalized_description": (
                    "preventive maintenance visit -- pcr/rt-pcr analyzers "
                    "quarterly service"
                ),
                "raw_description": (
                    "Preventive Maintenance Visit - PCR/RT-PCR Analyzers "
                    "Quarterly Service"
                ),
                "quantity": Decimal("4"),
                "unit_price": Decimal("82500.0000"),
                "tax_amount": Decimal("59400.00"),    # 18% GST on 330,000
                "line_amount": Decimal("330000.00"),
                "raw_quantity": "4",
                "raw_unit_price": "82,500.00",
                "raw_tax_amount": "59,400.00",
                "raw_line_amount": "3,30,000.00",
                "item_category": "AMC_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
                "extraction_confidence": 0.91,
            },
            {
                "line_number": 3,
                "description": (
                    "Calibration and Certification Services -- "
                    "Laboratory Weighing Balances"
                ),
                "normalized_description": (
                    "calibration and certification services -- "
                    "laboratory weighing balances"
                ),
                "raw_description": (
                    "Calibration and Certification Services - "
                    "Laboratory Weighing Balances"
                ),
                "quantity": Decimal("6"),
                "unit_price": Decimal("25500.0000"),
                "tax_amount": Decimal("27540.00"),    # 18% GST on 153,000
                "line_amount": Decimal("153000.00"),
                "raw_quantity": "6",
                "raw_unit_price": "25,500.00",
                "raw_tax_amount": "27,540.00",
                "raw_line_amount": "1,53,000.00",
                "item_category": "CALIBRATION_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
                "extraction_confidence": 0.90,
            },
            {
                "line_number": 4,
                "description": (
                    "HEPA Filter Replacement Kit -- "
                    "Biosafety Cabinet Consumable (Annual Supply)"
                ),
                "normalized_description": (
                    "hepa filter replacement kit -- "
                    "biosafety cabinet consumable (annual supply)"
                ),
                "raw_description": (
                    "HEPA Filter Replacement Kit - "
                    "Biosafety Cabinet Consumable (Annual Supply)"
                ),
                "quantity": Decimal("10"),
                "unit_price": Decimal("5000.0000"),
                "tax_amount": Decimal("9000.00"),     # 18% GST on 50,000
                "line_amount": Decimal("50000.00"),
                "raw_quantity": "10",
                "raw_unit_price": "5,000.00",
                "raw_tax_amount": "9,000.00",
                "raw_line_amount": "50,000.00",
                "item_category": "LAB_CONSUMABLES",
                "is_service_item": False,
                "is_stock_item": True,
                "extraction_confidence": 0.89,
            },
        ]

        InvoiceLineItem.objects.filter(invoice=invoice).delete()
        for ld in line_defs:
            InvoiceLineItem.objects.create(invoice=invoice, **ld)
        self.stdout.write(f"  Invoice Lines: {len(line_defs)}")

        # -----------------------------------------------------------
        # 8. ExtractionResult
        # -----------------------------------------------------------
        ext_result, _ = ExtractionResult.objects.update_or_create(
            document_upload=doc_upload,
            invoice=invoice,
            defaults=dict(
                engine_name="azure-document-intelligence",
                engine_version="2024-02-29-preview",
                raw_response={
                    "invoice_number": "TFS/AMC/2025/HYD/00312",
                    "vendor_name": "Thermo Fisher Scientific India Pvt. Ltd.",
                    "invoice_date": "2025-03-12",
                    "po_number": "AMC-TFS-BEL-2025-HYD-089",
                    "currency": "INR",
                    "subtotal": 903000.00,
                    "tax_amount": 162540.00,
                    "total_amount": 1065540.00,
                    "line_items": [
                        {
                            "description": ld["raw_description"],
                            "quantity": float(ld["quantity"]),
                            "unit_price": float(ld["unit_price"]),
                            "amount": float(ld["line_amount"]),
                        }
                        for ld in line_defs
                    ],
                },
                confidence=0.91,
                duration_ms=3180,
                success=True,
                ocr_page_count=3,
                ocr_duration_ms=1940,
                ocr_char_count=5830,
            ),
        )
        self.stdout.write(f"  ExtractionResult: pk={ext_result.pk}")

        # -----------------------------------------------------------
        # 9. ExtractionApproval (auto-approved, high confidence)
        # -----------------------------------------------------------
        ext_approval, _ = ExtractionApproval.objects.update_or_create(
            invoice=invoice,
            defaults=dict(
                extraction_result=ext_result,
                status=ExtractionApprovalStatus.APPROVED,
                reviewed_by=admin_user,
                reviewed_at=now - timedelta(hours=3),
                confidence_at_review=0.91,
                original_values_snapshot={
                    "invoice_number": "TFS/AMC/2025/HYD/00312",
                    "vendor_name": "Thermo Fisher Scientific India Pvt. Ltd.",
                    "po_number": "AMC-TFS-BEL-2025-HYD-089",
                    "total_amount": "1065540.00",
                },
                fields_corrected_count=0,
                is_touchless=True,
            ),
        )
        self.stdout.write(
            f"  ExtractionApproval: pk={ext_approval.pk} (touchless)"
        )

        # -----------------------------------------------------------
        # 10. ReconciliationRun + ReconciliationResult (UNMATCHED - historical)
        #     Shows the state when invoice first arrived. Reprocess to re-match.
        # -----------------------------------------------------------
        recon_config = ReconciliationConfig.objects.filter(
            is_default=True
        ).first()

        # Idempotency: remove stale results for this invoice
        old_results = ReconciliationResult.objects.filter(invoice=invoice)
        if old_results.exists():
            old_run_ids = list(old_results.values_list("run_id", flat=True))
            old_results.delete()
            ReconciliationRun.objects.filter(pk__in=old_run_ids).delete()

        recon_run = ReconciliationRun.objects.create(
            status=ReconciliationRunStatus.COMPLETED,
            config=recon_config,
            started_at=now - timedelta(hours=2, minutes=30),
            completed_at=now - timedelta(hours=2, minutes=29),
            total_invoices=1,
            matched_count=0,
            partial_count=0,
            unmatched_count=1,
            error_count=0,
            review_count=1,
            triggered_by=admin_user,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
            grn_required_flag=True,
            grn_checked_flag=False,
        )
        self.stdout.write(f"  ReconciliationRun: pk={recon_run.pk}")

        recon_result = ReconciliationResult.objects.create(
            run=recon_run,
            invoice=invoice,
            purchase_order=None,   # PO not found in system
            match_status=MatchStatus.UNMATCHED,
            requires_review=True,
            vendor_match=None,
            currency_match=None,
            po_total_match=None,
            invoice_total_vs_po=None,
            total_amount_difference=None,
            total_amount_difference_pct=None,
            grn_available=False,
            grn_fully_received=None,
            extraction_confidence=0.91,
            deterministic_confidence=0.0,
            summary=(
                "Invoice #TFS/AMC/2025/HYD/00312 could not be matched. "
                "Referenced PO AMC-TFS-BEL-2025-HYD-089 was not found in the system. "
                "Vendor: Thermo Fisher Scientific India Pvt. Ltd. | Amount: 1,065,540.00 INR. "
                "Routed to AP Review for PO recovery or manual approval."
            ),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
            grn_required_flag=True,
            grn_checked_flag=False,
            mode_resolution_reason=(
                "Default THREE_WAY mode applied. Invoice contains mixed line items "
                "(AMC services + stock consumables). PO lookup failed before mode "
                "could be refined."
            ),
            policy_applied="",
            is_two_way_result=False,
            is_three_way_result=True,
        )
        self.stdout.write(
            f"  ReconciliationResult: pk={recon_result.pk} (UNMATCHED)"
        )

        # -----------------------------------------------------------
        # 11. ReconciliationException (PO_NOT_FOUND -- historical state)
        # -----------------------------------------------------------
        recon_exc = ReconciliationException.objects.create(
            result=recon_result,
            result_line=None,
            exception_type=ExceptionType.PO_NOT_FOUND,
            severity=ExceptionSeverity.HIGH,
            message=(
                "Purchase order not found for PO number "
                "'AMC-TFS-BEL-2025-HYD-089'"
            ),
            details={
                "searched_po_number": "AMC-TFS-BEL-2025-HYD-089",
                "normalized_search": "amc-tfs-bel-2025-hyd-089",
                "vendor_code": "V-TFS-001",
                "vendor_name": "Thermo Fisher Scientific India Pvt. Ltd.",
                "invoice_total": "1065540.00",
                "currency": "INR",
                "suggestion": (
                    "Verify PO number with procurement. "
                    "The PO may exist under a different reference format "
                    "or may not have been raised yet."
                ),
            },
            resolved=False,
            applies_to_mode=ReconciliationModeApplicability.BOTH,
        )
        self.stdout.write(
            f"  ReconciliationException: pk={recon_exc.pk} (PO_NOT_FOUND / HIGH)"
        )

        # -----------------------------------------------------------
        # 12. APCase
        # -----------------------------------------------------------
        case, created = APCase.objects.update_or_create(
            case_number="AP-260326-0001",
            defaults=dict(
                invoice=invoice,
                vendor=vendor,
                purchase_order=po,
                reconciliation_result=recon_result,
                review_assignment=None,
                source_channel=SourceChannel.WEB_UPLOAD,
                invoice_type=InvoiceType.PO_BACKED,
                processing_path=ProcessingPath.TWO_WAY,
                status=CaseStatus.READY_FOR_REVIEW,
                current_stage=CaseStageType.REVIEW_ROUTING,
                priority=CasePriority.HIGH,
                risk_score=0.72,
                extraction_confidence=0.91,
                requires_human_review=True,
                requires_approval=True,
                eligible_for_posting=False,
                duplicate_risk_flag=False,
                reconciliation_mode=ReconciliationMode.THREE_WAY,
                budget_check_status="",
                coding_status="",
                created_by=admin_user,
            ),
        )
        self.stdout.write(
            f"  APCase: {case.case_number} (pk={case.pk}, "
            f"{'created' if created else 'updated'})"
        )

        # -----------------------------------------------------------
        # 13. APCaseStages
        # -----------------------------------------------------------
        APCaseStage.objects.filter(case=case).delete()

        stage_defs = [
            {
                "stage_name": CaseStageType.INTAKE,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.SYSTEM,
                "started_at": now - timedelta(hours=4),
                "completed_at": now - timedelta(hours=4) + timedelta(seconds=2),
                "duration_ms": 2000,
                "output_payload": {
                    "document_upload_id": doc_upload.pk,
                    "filename": doc_upload.original_filename,
                    "file_size_bytes": doc_upload.file_size,
                },
            },
            {
                "stage_name": CaseStageType.EXTRACTION,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.AGENT,
                "started_at": now - timedelta(hours=4) + timedelta(seconds=3),
                "completed_at": now - timedelta(hours=4) + timedelta(seconds=7),
                "duration_ms": 3180,
                "output_payload": {
                    "extraction_result_id": ext_result.pk,
                    "confidence": 0.91,
                    "line_items_extracted": len(line_defs),
                    "po_reference_detected": "AMC-TFS-BEL-2025-HYD-089",
                    "ocr_pages": 3,
                },
            },
            {
                "stage_name": CaseStageType.PATH_RESOLUTION,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.DETERMINISTIC,
                "started_at": now - timedelta(hours=3, minutes=55),
                "completed_at": (
                    now - timedelta(hours=3, minutes=55)
                    + timedelta(milliseconds=120)
                ),
                "duration_ms": 120,
                "output_payload": {
                    "resolved_path": ProcessingPath.TWO_WAY,
                    "invoice_type": InvoiceType.PO_BACKED,
                    "reason": (
                        "PO reference AMC-TFS-BEL-2025-HYD-089 present on invoice. "
                        "Dominant line item categories are service-based (AMC). "
                        "Resolved to TWO_WAY path; engine will attempt THREE_WAY "
                        "by default configuration."
                    ),
                },
            },
            {
                "stage_name": CaseStageType.THREE_WAY_MATCHING,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.DETERMINISTIC,
                "started_at": now - timedelta(hours=2, minutes=35),
                "completed_at": (
                    now - timedelta(hours=2, minutes=35)
                    + timedelta(milliseconds=480)
                ),
                "duration_ms": 480,
                "output_payload": {
                    "reconciliation_run_id": recon_run.pk,
                    "reconciliation_result_id": recon_result.pk,
                    "match_status": MatchStatus.UNMATCHED,
                    "reconciliation_mode": ReconciliationMode.THREE_WAY,
                    "po_looked_up": "AMC-TFS-BEL-2025-HYD-089",
                    "po_found": False,
                    "grn_checked": False,
                    "exceptions": ["PO_NOT_FOUND"],
                },
            },
            {
                "stage_name": CaseStageType.EXCEPTION_ANALYSIS,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.AGENT,
                "started_at": now - timedelta(hours=2, minutes=25),
                "completed_at": (
                    now - timedelta(hours=2, minutes=25)
                    + timedelta(seconds=8)
                ),
                "duration_ms": 8200,
                "output_payload": {
                    "exceptions_classified": 1,
                    "exception_types": ["PO_NOT_FOUND"],
                    "highest_severity": "HIGH",
                    "recommended_action": "SEND_TO_AP_REVIEW",
                    "agent_rationale": (
                        "Single PO_NOT_FOUND exception with no GRN or vendor "
                        "issues detected. Invoice amount (INR 1,065,540) exceeds "
                        "high-value threshold. Routed to AP Review for PO recovery."
                    ),
                },
            },
            {
                "stage_name": CaseStageType.REVIEW_ROUTING,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.DETERMINISTIC,
                "started_at": now - timedelta(hours=2, minutes=15),
                "completed_at": (
                    now - timedelta(hours=2, minutes=15)
                    + timedelta(milliseconds=95)
                ),
                "duration_ms": 95,
                "output_payload": {
                    "routed_to": "AP_REVIEW",
                    "priority": CasePriority.HIGH,
                    "reason": (
                        "PO_NOT_FOUND exception requires procurement verification. "
                        "Invoice amount INR 1,065,540 exceeds high-value threshold "
                        "of INR 500,000 -- escalated priority."
                    ),
                },
            },
        ]

        for sd in stage_defs:
            APCaseStage.objects.create(case=case, **sd)
        self.stdout.write(f"  APCaseStages: {len(stage_defs)} (completed)")

        # -----------------------------------------------------------
        # 14. APCaseDecisions
        # -----------------------------------------------------------
        APCaseDecision.objects.filter(case=case).delete()

        decision_defs = [
            {
                "decision_type": DecisionType.PATH_SELECTED,
                "decision_source": DecisionSource.DETERMINISTIC,
                "decision_value": ProcessingPath.TWO_WAY,
                "confidence": 0.88,
                "rationale": (
                    "PO reference (AMC-TFS-BEL-2025-HYD-089) present on invoice. "
                    "Dominant line item categories are service-oriented (AMC, calibration). "
                    "Resolved processing path to TWO_WAY. Engine will attempt THREE_WAY "
                    "by active configuration."
                ),
                "evidence": {
                    "po_reference": "AMC-TFS-BEL-2025-HYD-089",
                    "service_line_count": 3,
                    "stock_line_count": 1,
                    "path_resolver_version": "1.0",
                },
            },
            {
                "decision_type": DecisionType.MATCH_DETERMINED,
                "decision_source": DecisionSource.DETERMINISTIC,
                "decision_value": MatchStatus.UNMATCHED,
                "confidence": 1.0,
                "rationale": (
                    "PO AMC-TFS-BEL-2025-HYD-089 referenced on invoice "
                    "TFS/AMC/2025/HYD/00312 was not found in the system. "
                    "Reconciliation could not proceed. Match status: UNMATCHED."
                ),
                "evidence": {
                    "exception_type": ExceptionType.PO_NOT_FOUND,
                    "po_searched": "AMC-TFS-BEL-2025-HYD-089",
                    "normalized_search": "amc-tfs-bel-2025-hyd-089",
                    "reconciliation_result_id": recon_result.pk,
                    "invoice_total_inr": "1065540.00",
                },
            },
            {
                "decision_type": DecisionType.SENT_TO_REVIEW,
                "decision_source": DecisionSource.AGENT,
                "decision_value": "AP_REVIEW",
                "confidence": 0.95,
                "rationale": (
                    "Standard reconciliation exception (PO_NOT_FOUND) identified. "
                    "Invoice amount INR 1,065,540 exceeds high-value threshold. "
                    "Routing to AP Review for PO verification with procurement team "
                    "and manual approval if required."
                ),
                "evidence": {
                    "exception_count": 1,
                    "exception_types": [ExceptionType.PO_NOT_FOUND],
                    "invoice_amount_inr": "1065540.00",
                    "high_value_threshold_inr": "500000.00",
                    "priority_escalated": True,
                },
            },
        ]

        for dd in decision_defs:
            APCaseDecision.objects.create(case=case, **dd)
        self.stdout.write(f"  APCaseDecisions: {len(decision_defs)}")

        # -----------------------------------------------------------
        # 15. APCaseSummary
        # -----------------------------------------------------------
        APCaseSummary.objects.update_or_create(
            case=case,
            defaults=dict(
                latest_summary=(
                    "Case AP-260326-0001 -- Invoice TFS/AMC/2025/HYD/00312 from "
                    "Thermo Fisher Scientific India Pvt. Ltd. for INR 1,065,540 "
                    "(subtotal INR 903,000 + GST 18% INR 162,540). Invoice covers "
                    "Annual Maintenance Contract services for lab instruments "
                    "(Biosafety Cabinets, PCR Analyzers, Lab Balances) and HEPA "
                    "filter consumables at the Hyderabad facility. "
                    "The referenced PO AMC-TFS-BEL-2025-HYD-089 was not found in "
                    "the system, resulting in an UNMATCHED reconciliation. "
                    "Extraction confidence is high (91%). No vendor mismatch or "
                    "duplicate invoice detected. Case is HIGH priority due to "
                    "invoice value exceeding INR 500,000 threshold. "
                    "Requires AP Review to verify PO with procurement or approve "
                    "manually."
                ),
                reviewer_summary=(
                    "Invoice TFS/AMC/2025/HYD/00312 from Thermo Fisher Scientific "
                    "India (INR 1,065,540) cannot be matched -- the referenced PO "
                    "AMC-TFS-BEL-2025-HYD-089 does not exist in the system. "
                    "Please: (1) confirm the correct PO number with the procurement "
                    "or lab team at Hyderabad, (2) if PO exists under a different "
                    "reference, link it manually, (3) if no PO was raised, initiate "
                    "a retrospective PO or follow the non-PO approval workflow. "
                    "The invoice covers AMC and calibration services -- verify "
                    "contract scope and period (FY 2025)."
                ),
                finance_summary=(
                    "Thermo Fisher Scientific India | Invoice TFS/AMC/2025/HYD/00312 | "
                    "INR 1,065,540 (GST 18%: INR 162,540). PO reference "
                    "AMC-TFS-BEL-2025-HYD-089 not found -- no budget reservation "
                    "confirmed. Spend category: Lab Equipment AMC + Consumables. "
                    "High-value case requiring manual PO recovery or budget "
                    "authorization. GL coding pending PO confirmation."
                ),
                recommendation="SEND_TO_AP_REVIEW",
            ),
        )
        self.stdout.write("  APCaseSummary: created")

        # -----------------------------------------------------------
        # Done
        # -----------------------------------------------------------
        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Case data seeded:\n"
            f"  Vendor:  Thermo Fisher Scientific India Pvt. Ltd. (V-TFS-001) + 6 aliases\n"
            f"  PO:      AMC-TFS-BEL-2025-HYD-089 (pk={po.pk}) -- 4 lines, INR 1,065,540\n"
            f"  GRN:     GRN-HYD-2025-AMC-0089 (pk={grn.pk}) -- all 4 lines fully received\n"
            f"  Invoice: TFS/AMC/2025/HYD/00312 -- 4 lines, total INR 1,065,540\n"
            f"  Recon:   UNMATCHED (historical) -- PO_NOT_FOUND (HIGH severity)\n"
            f"           PO now exists in DB -- reprocess to get a MATCHED result.\n"
            f"  Case:    AP-260326-0001 -- TWO_WAY path, READY_FOR_REVIEW, HIGH priority\n"
            f"  Stages:  6 completed "
            f"(INTAKE -> EXTRACTION -> PATH_RESOLUTION -> THREE_WAY_MATCHING "
            f"-> EXCEPTION_ANALYSIS -> REVIEW_ROUTING)\n"
            f"  URL:     http://127.0.0.1:8000/cases/{case.pk}/agent/\n"
        ))
