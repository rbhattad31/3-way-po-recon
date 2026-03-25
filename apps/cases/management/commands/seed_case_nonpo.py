"""
Seed data for a Non-PO invoice case AP-260325-0001.

Creates: Vendor + aliases, DocumentUpload, Invoice (no PO reference) + line items,
         ExtractionResult, ExtractionApproval, APCase (NON_PO path), APCaseStages,
         APCaseSummary.

This simulates a fully extracted and approved non-PO invoice ready for the
non-PO validation pipeline. There is no PO, no GRN, no reconciliation result.

Usage:
    python manage.py seed_case_nonpo
"""

import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.models import APCase, APCaseStage, APCaseSummary
from apps.core.enums import (
    CasePriority,
    CaseStageType,
    CaseStatus,
    DocumentType,
    ExtractionApprovalStatus,
    FileProcessingState,
    InvoiceStatus,
    InvoiceType,
    PerformedByType,
    ProcessingPath,
    SourceChannel,
    StageStatus,
)
from apps.documents.models import DocumentUpload, Invoice, InvoiceLineItem
from apps.extraction.models import ExtractionApproval, ExtractionResult
from apps.vendors.models import Vendor, VendorAlias


class Command(BaseCommand):
    help = "Seed data for non-PO case AP-260325-0001"

    def handle(self, *args, **options):
        now = timezone.now()
        admin_user = User.objects.get(email="admin@mcd-ksa.com")

        # -------------------------------------------------------
        # 1. Vendor
        # -------------------------------------------------------
        vendor, _ = Vendor.objects.update_or_create(
            code="V-ARC-001",
            defaults=dict(
                name="ArcPoint Consulting Group",
                normalized_name="arcpoint consulting group",
                tax_id="311-89-4521",
                address="2100 Market Street, Suite 450\nPhiladelphia, PA 19103\nUnited States",
                country="US",
                currency="USD",
                payment_terms="Net 45",
                contact_email="invoices@arcpoint-consulting.com",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  Vendor: {vendor.name} (pk={vendor.pk})")

        for alias in [
            "ArcPoint Consulting",
            "ARCPOINT CONSULTING GROUP",
            "Arc Point Consulting Group",
            "ArcPoint Consulting Grp",
            "ArcPoint CG",
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
        self.stdout.write("  Vendor aliases: 5")

        # -------------------------------------------------------
        # 2. DocumentUpload (simulated PDF)
        # -------------------------------------------------------
        fake_hash = hashlib.sha256(b"seed-nonpo-arcpoint-inv-2025-1847").hexdigest()
        doc_upload, _ = DocumentUpload.objects.update_or_create(
            file_hash=fake_hash,
            defaults=dict(
                original_filename="ArcPoint_INV-2025-1847.pdf",
                file_size=284_512,
                content_type="application/pdf",
                document_type=DocumentType.INVOICE,
                processing_state=FileProcessingState.COMPLETED,
                processing_message="Extraction completed successfully",
                uploaded_by=admin_user,
            ),
        )
        self.stdout.write(f"  DocumentUpload: pk={doc_upload.pk}")

        # -------------------------------------------------------
        # 3. Invoice (non-PO -- no po_number)
        # -------------------------------------------------------
        invoice, _ = Invoice.objects.update_or_create(
            invoice_number="INV-2025-1847",
            defaults=dict(
                document_upload=doc_upload,
                vendor=vendor,
                # Raw extracted values
                raw_vendor_name="ArcPoint Consulting Group",
                raw_invoice_number="INV-2025-1847",
                raw_invoice_date="2025-03-10",
                raw_po_number="",
                raw_currency="USD",
                raw_subtotal="18,750.00",
                raw_tax_amount="1,406.25",
                raw_total_amount="20,156.25",
                # Normalized values
                normalized_invoice_number="inv-2025-1847",
                invoice_date=now.date() - timedelta(days=15),
                po_number="",
                normalized_po_number="",
                currency="USD",
                subtotal=Decimal("18750.00"),
                tax_amount=Decimal("1406.25"),
                total_amount=Decimal("20156.25"),
                # Status & confidence
                status=InvoiceStatus.READY_FOR_RECON,
                extraction_confidence=0.92,
                extraction_remarks="High confidence extraction. No PO reference detected.",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  Invoice: {invoice.invoice_number} (pk={invoice.pk})")

        # -------------------------------------------------------
        # 4. Invoice Line Items (consulting services -- no PO match)
        # -------------------------------------------------------
        line_defs = [
            {
                "line_number": 1,
                "description": "Strategic Advisory -- Digital Transformation Roadmap",
                "normalized_description": "strategic advisory -- digital transformation roadmap",
                "raw_description": "Strategic Advisory - Digital Transformation Roadmap",
                "quantity": Decimal("40"),
                "unit_price": Decimal("250.0000"),
                "tax_amount": Decimal("750.00"),
                "line_amount": Decimal("10000.00"),
                "raw_quantity": "40",
                "raw_unit_price": "250.00",
                "raw_tax_amount": "750.00",
                "raw_line_amount": "10,000.00",
                "item_category": "CONSULTING",
                "is_service_item": True,
                "is_stock_item": False,
                "extraction_confidence": 0.94,
            },
            {
                "line_number": 2,
                "description": "Workshop Facilitation -- Stakeholder Alignment (2 days)",
                "normalized_description": "workshop facilitation -- stakeholder alignment (2 days)",
                "raw_description": "Workshop Facilitation - Stakeholder Alignment (2 days)",
                "quantity": Decimal("2"),
                "unit_price": Decimal("2500.0000"),
                "tax_amount": Decimal("375.00"),
                "line_amount": Decimal("5000.00"),
                "raw_quantity": "2",
                "raw_unit_price": "2,500.00",
                "raw_tax_amount": "375.00",
                "raw_line_amount": "5,000.00",
                "item_category": "CONSULTING",
                "is_service_item": True,
                "is_stock_item": False,
                "extraction_confidence": 0.91,
            },
            {
                "line_number": 3,
                "description": "Travel Expenses -- On-site engagement Philadelphia to Chicago",
                "normalized_description": "travel expenses -- on-site engagement philadelphia to chicago",
                "raw_description": "Travel Expenses - On-site engagement Philadelphia to Chicago",
                "quantity": Decimal("1"),
                "unit_price": Decimal("3750.0000"),
                "tax_amount": Decimal("281.25"),
                "line_amount": Decimal("3750.00"),
                "raw_quantity": "1",
                "raw_unit_price": "3,750.00",
                "raw_tax_amount": "281.25",
                "raw_line_amount": "3,750.00",
                "item_category": "TRAVEL",
                "is_service_item": True,
                "is_stock_item": False,
                "extraction_confidence": 0.89,
            },
        ]

        InvoiceLineItem.objects.filter(invoice=invoice).delete()
        for ld in line_defs:
            InvoiceLineItem.objects.create(invoice=invoice, **ld)
        self.stdout.write(f"  Invoice Lines: {len(line_defs)}")

        # -------------------------------------------------------
        # 5. ExtractionResult
        # -------------------------------------------------------
        ext_result, _ = ExtractionResult.objects.update_or_create(
            document_upload=doc_upload,
            invoice=invoice,
            defaults=dict(
                engine_name="azure-document-intelligence",
                engine_version="2024-02-29-preview",
                raw_response={
                    "invoice_number": "INV-2025-1847",
                    "vendor_name": "ArcPoint Consulting Group",
                    "invoice_date": "2025-03-10",
                    "po_number": None,
                    "currency": "USD",
                    "subtotal": 18750.00,
                    "tax_amount": 1406.25,
                    "total_amount": 20156.25,
                    "line_items": [
                        {"description": ld["raw_description"], "quantity": float(ld["quantity"]),
                         "unit_price": float(ld["unit_price"]), "amount": float(ld["line_amount"])}
                        for ld in line_defs
                    ],
                },
                confidence=0.92,
                duration_ms=3420,
                success=True,
                ocr_page_count=2,
                ocr_duration_ms=1850,
                ocr_char_count=4210,
            ),
        )
        self.stdout.write(f"  ExtractionResult: pk={ext_result.pk}")

        # -------------------------------------------------------
        # 6. ExtractionApproval (auto-approved)
        # -------------------------------------------------------
        ext_approval, _ = ExtractionApproval.objects.update_or_create(
            invoice=invoice,
            defaults=dict(
                extraction_result=ext_result,
                status=ExtractionApprovalStatus.APPROVED,
                reviewed_by=admin_user,
                reviewed_at=now - timedelta(hours=2),
                confidence_at_review=0.92,
                original_values_snapshot={
                    "invoice_number": "INV-2025-1847",
                    "vendor_name": "ArcPoint Consulting Group",
                    "total_amount": "20156.25",
                },
                fields_corrected_count=0,
                is_touchless=True,
            ),
        )
        self.stdout.write(f"  ExtractionApproval: pk={ext_approval.pk} (touchless)")

        # -------------------------------------------------------
        # 7. APCase (Non-PO path)
        # -------------------------------------------------------
        case, created = APCase.objects.update_or_create(
            case_number="AP-260325-0001",
            defaults=dict(
                invoice=invoice,
                vendor=vendor,
                purchase_order=None,
                reconciliation_result=None,
                review_assignment=None,
                source_channel=SourceChannel.WEB_UPLOAD,
                invoice_type=InvoiceType.NON_PO,
                processing_path=ProcessingPath.NON_PO,
                status=CaseStatus.READY_FOR_REVIEW,
                current_stage=CaseStageType.NON_PO_VALIDATION,
                priority=CasePriority.MEDIUM,
                risk_score=0.45,
                extraction_confidence=0.92,
                requires_human_review=True,
                requires_approval=True,
                eligible_for_posting=False,
                duplicate_risk_flag=False,
                reconciliation_mode="",
                budget_check_status="",
                coding_status="",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  APCase: {case.case_number} (pk={case.pk}, {'created' if created else 'updated'})")

        # -------------------------------------------------------
        # 8. APCaseStages (completed pipeline stages)
        # -------------------------------------------------------
        stage_defs = [
            {
                "stage_name": CaseStageType.INTAKE,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.SYSTEM,
                "started_at": now - timedelta(hours=4),
                "completed_at": now - timedelta(hours=4) + timedelta(seconds=2),
                "duration_ms": 2000,
                "output_payload": {"document_upload_id": doc_upload.pk, "filename": doc_upload.original_filename},
            },
            {
                "stage_name": CaseStageType.EXTRACTION,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.AGENT,
                "started_at": now - timedelta(hours=4) + timedelta(seconds=3),
                "completed_at": now - timedelta(hours=4) + timedelta(seconds=7),
                "duration_ms": 3420,
                "output_payload": {
                    "extraction_result_id": ext_result.pk,
                    "confidence": 0.92,
                    "line_items_extracted": 3,
                },
            },
            {
                "stage_name": CaseStageType.PATH_RESOLUTION,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.DETERMINISTIC,
                "started_at": now - timedelta(hours=3, minutes=55),
                "completed_at": now - timedelta(hours=3, minutes=55) + timedelta(milliseconds=150),
                "duration_ms": 150,
                "output_payload": {
                    "resolved_path": ProcessingPath.NON_PO,
                    "invoice_type": InvoiceType.NON_PO,
                    "reason": "No PO reference detected on invoice",
                },
            },
            {
                "stage_name": CaseStageType.NON_PO_VALIDATION,
                "stage_status": StageStatus.COMPLETED,
                "performed_by_type": PerformedByType.DETERMINISTIC,
                "started_at": now - timedelta(hours=3, minutes=50),
                "completed_at": now - timedelta(hours=3, minutes=50) + timedelta(seconds=1),
                "duration_ms": 1000,
                "output_payload": {
                    "checks_run": 9,
                    "checks_passed": 7,
                    "checks_warned": 2,
                    "checks_failed": 0,
                    "risk_score": 0.45,
                    "warnings": [
                        "Amount exceeds $5,000 -- supporting documentation recommended",
                        "Travel expenses line item -- receipt verification recommended",
                    ],
                },
            },
        ]

        # Clear existing stages for idempotency
        APCaseStage.objects.filter(case=case).delete()
        for sd in stage_defs:
            APCaseStage.objects.create(case=case, **sd)
        self.stdout.write(f"  APCaseStages: {len(stage_defs)} (completed)")

        # -------------------------------------------------------
        # 9. APCaseSummary
        # -------------------------------------------------------
        APCaseSummary.objects.update_or_create(
            case=case,
            defaults=dict(
                latest_summary=(
                    "Non-PO invoice from ArcPoint Consulting Group for $20,156.25 covering "
                    "digital transformation advisory and workshop facilitation services. "
                    "No purchase order was referenced. Extraction confidence is high (92%). "
                    "Two validation warnings: (1) invoice exceeds $5,000 threshold requiring "
                    "supporting documentation, (2) travel expense line item requires receipt "
                    "verification. Vendor is active and known in the system. No duplicate "
                    "invoice detected. Requires human review for spend approval and cost "
                    "center assignment."
                ),
                reviewer_summary=(
                    "This $20,156.25 invoice from ArcPoint Consulting covers consulting "
                    "services and travel expenses with no PO backing. Please verify: "
                    "(1) appropriate cost center / GL account coding, (2) supporting "
                    "documentation for amount over $5K, (3) travel receipts for the "
                    "$3,750 travel expense line."
                ),
                finance_summary=(
                    "Unplanned consulting spend of $20,156.25 (ArcPoint Consulting). "
                    "No budget reservation (no PO). Tax: $1,406.25 (7.5% effective rate). "
                    "Requires cost center assignment and budget impact assessment."
                ),
                recommendation="SEND_TO_AP_REVIEW",
            ),
        )
        self.stdout.write("  APCaseSummary: created")

        # -------------------------------------------------------
        # Done
        # -------------------------------------------------------
        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Non-PO case data seeded:\n"
            f"  Vendor: {vendor.name} ({vendor.code}) + 5 aliases\n"
            f"  Invoice: {invoice.invoice_number} -- 3 lines, total USD 20,156.25 (no PO)\n"
            f"  Case: {case.case_number} -- NON_PO path, READY_FOR_REVIEW\n"
            f"  Extraction: confidence 92%, touchless approved\n"
            f"  Stages: 4 completed (INTAKE -> EXTRACTION -> PATH_RESOLUTION -> NON_PO_VALIDATION)\n"
            f"\nOpen the case in the AP Cases inbox to continue processing."
        ))
