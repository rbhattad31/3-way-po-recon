from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    CategoryMaster,
)
from apps.procurement.models import Product


@dataclass
class StaticLine:
    description: str
    quantity: Decimal
    uom: str
    category_code: str
    base_unit_rate: Decimal


class Command(BaseCommand):
    help = (
        "Create one static benchmark case with 1 RFQ and 3 vendor quotations using "
        "ReportLab PDFs, then seed matching benchmark DB records and line items."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--title",
            type=str,
            default="STATIC RFQ - HVAC Benchmark Validation",
            help="Benchmark request title.",
        )
        parser.add_argument(
            "--geography",
            type=str,
            default="UAE",
            help="Benchmark geography (default: UAE).",
        )
        parser.add_argument(
            "--scope-type",
            type=str,
            default="SITC",
            help="Scope type (SITC/ITC/EQUIPMENT_ONLY).",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Soft-delete existing active request with the same title before creating a new one.",
        )

    def handle(self, *args, **options):
        title = options["title"].strip()
        geography = options["geography"].strip().upper()
        scope_type = options["scope_type"].strip().upper()
        replace = bool(options["replace"])

        if not title:
            self.stdout.write(self.style.ERROR("Title cannot be empty."))
            return

        static_lines = self._build_static_lines(geography=geography, scope_type=scope_type)
        if not static_lines:
            self.stdout.write(self.style.ERROR("Unable to build static lines from DB models."))
            return

        vendor_specs = [
            {
                "supplier_name": "Al Noor MEP Supplies LLC",
                "quotation_ref": "ANMS-2026-041",
                "multiplier": Decimal("0.95"),
                "layout": "classic",
            },
            {
                "supplier_name": "GulfTech Engineering Trading",
                "quotation_ref": "GTET-Q-077",
                "multiplier": Decimal("1.02"),
                "layout": "blue_table",
            },
            {
                "supplier_name": "PrimeBuild HVAC Solutions",
                "quotation_ref": "PBHS-QUO-112",
                "multiplier": Decimal("1.08"),
                "layout": "boxed",
            },
        ]

        with transaction.atomic():
            if replace:
                BenchmarkRequest.objects.filter(title=title, is_active=True).update(is_active=False)

            bench_request = BenchmarkRequest.objects.create(
                title=title,
                project_name="Static Benchmark Validation Project",
                geography=geography,
                scope_type=scope_type,
                store_type="STATIC_VALIDATION",
                status="PENDING",
                notes=(
                    "Static benchmark validation case. RFQ PDF has no category labels, "
                    "while DB line items retain category mapping."
                ),
                rfq_source="system",
                rfq_ref=f"RFQ-STATIC-{timezone.now().strftime('%Y%m%d-%H%M%S')}",
            )

            output_dir = (
                Path(settings.MEDIA_ROOT)
                / "benchmarking"
                / "manual-rfq-3-vendor-static"
                / f"request_{bench_request.pk}"
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            rfq_path = output_dir / "rfq_static_validation.pdf"
            self._build_rfq_pdf(
                target=rfq_path,
                rfq_ref=bench_request.rfq_ref,
                lines=static_lines,
                geography=geography,
                scope_type=scope_type,
            )
            with rfq_path.open("rb") as rfq_file:
                bench_request.rfq_document.save(rfq_path.name, File(rfq_file), save=False)
            bench_request.save(update_fields=["rfq_document", "updated_at"])

            created_quotations: List[BenchmarkQuotation] = []
            for index, spec in enumerate(vendor_specs, start=1):
                quotation = BenchmarkQuotation.objects.create(
                    request=bench_request,
                    supplier_name=spec["supplier_name"],
                    quotation_ref=spec["quotation_ref"],
                    extraction_status="DONE",
                )

                quotation_path = output_dir / f"quotation_{index}_{spec['quotation_ref']}.pdf"
                priced_lines = self._with_vendor_prices(static_lines, spec["multiplier"])
                self._build_vendor_pdf(
                    target=quotation_path,
                    rfq_ref=bench_request.rfq_ref,
                    quotation_ref=spec["quotation_ref"],
                    supplier_name=spec["supplier_name"],
                    lines=priced_lines,
                    layout=spec["layout"],
                )

                with quotation_path.open("rb") as quotation_file:
                    quotation.document.save(quotation_path.name, File(quotation_file), save=False)
                quotation.save(update_fields=["document", "updated_at"])

                self._create_line_items(quotation=quotation, lines=priced_lines)
                created_quotations.append(quotation)

        self.stdout.write(self.style.SUCCESS("Static benchmarking case created successfully."))
        self.stdout.write(f"BenchmarkRequest ID: {bench_request.pk}")
        self.stdout.write(f"RFQ Ref: {bench_request.rfq_ref}")
        self.stdout.write(f"Output folder: {output_dir}")
        self.stdout.write("Quotations:")
        for quotation in created_quotations:
            self.stdout.write(f" - {quotation.pk}: {quotation.supplier_name} ({quotation.quotation_ref})")

    def _build_static_lines(self, *, geography: str, scope_type: str) -> List[StaticLine]:
        products = list(Product.objects.filter(is_active=True).order_by("id")[:5])
        categories = {
            row.code: row
            for row in CategoryMaster.objects.filter(is_active=True)
        }

        equipment_code = "EQUIPMENT" if "EQUIPMENT" in categories else "UNCATEGORIZED"
        installation_code = "INSTALLATION" if "INSTALLATION" in categories else equipment_code
        tc_code = "TC" if "TC" in categories else installation_code

        static_lines: List[StaticLine] = []

        for product in products:
            desc = f"{product.manufacturer} {product.product_name} {product.capacity_kw}kW"
            corridor_rate = self._corridor_mid_rate(
                category_code=equipment_code,
                geography=geography,
                scope_type=scope_type,
            )
            if corridor_rate is None:
                corridor_rate = Decimal("1200.00")

            static_lines.append(
                StaticLine(
                    description=desc,
                    quantity=Decimal("2.00"),
                    uom="Nos",
                    category_code=equipment_code,
                    base_unit_rate=corridor_rate,
                )
            )

        if not static_lines:
            fallback_rate = self._corridor_mid_rate(
                category_code=equipment_code,
                geography=geography,
                scope_type=scope_type,
            ) or Decimal("1500.00")
            static_lines.extend(
                [
                    StaticLine(
                        description="Air Handling Unit 4500 CFM",
                        quantity=Decimal("4.00"),
                        uom="Nos",
                        category_code=equipment_code,
                        base_unit_rate=fallback_rate,
                    ),
                    StaticLine(
                        description="Chilled Water FCU 800 CFM",
                        quantity=Decimal("12.00"),
                        uom="Nos",
                        category_code=equipment_code,
                        base_unit_rate=fallback_rate,
                    ),
                ]
            )

        install_rate = self._corridor_mid_rate(
            category_code=installation_code,
            geography=geography,
            scope_type=scope_type,
        ) or Decimal("450.00")
        tc_rate = self._corridor_mid_rate(
            category_code=tc_code,
            geography=geography,
            scope_type=scope_type,
        ) or Decimal("900.00")

        static_lines.append(
            StaticLine(
                description="Installation, supports, and minor accessories",
                quantity=Decimal("1.00"),
                uom="Lot",
                category_code=installation_code,
                base_unit_rate=install_rate,
            )
        )
        static_lines.append(
            StaticLine(
                description="Testing and commissioning",
                quantity=Decimal("1.00"),
                uom="Lot",
                category_code=tc_code,
                base_unit_rate=tc_rate,
            )
        )

        return static_lines[:8]

    def _corridor_mid_rate(self, *, category_code: str, geography: str, scope_type: str) -> Optional[Decimal]:
        rule = (
            BenchmarkCorridorRule.objects.filter(
                is_active=True,
                category=category_code,
                geography__in=[geography, "ALL"],
                scope_type__in=[scope_type, "ALL"],
            )
            .order_by("priority", "id")
            .first()
        )
        if not rule or rule.mid_rate is None:
            return None
        return Decimal(str(rule.mid_rate))

    def _with_vendor_prices(self, lines: List[StaticLine], multiplier: Decimal) -> List[StaticLine]:
        priced_lines: List[StaticLine] = []
        for line in lines:
            priced_lines.append(
                StaticLine(
                    description=line.description,
                    quantity=line.quantity,
                    uom=line.uom,
                    category_code=line.category_code,
                    base_unit_rate=(line.base_unit_rate * multiplier).quantize(Decimal("0.01")),
                )
            )
        return priced_lines

    def _create_line_items(self, *, quotation: BenchmarkQuotation, lines: List[StaticLine]) -> None:
        for index, line in enumerate(lines, start=1):
            unit_rate = line.base_unit_rate.quantize(Decimal("0.01"))
            line_total = (line.quantity * unit_rate).quantize(Decimal("0.01"))
            BenchmarkLineItem.objects.create(
                quotation=quotation,
                line_number=index,
                description=line.description,
                uom=line.uom,
                quantity=line.quantity,
                quoted_unit_rate=unit_rate,
                line_amount=line_total,
                category=line.category_code,
                classification_source="MANUAL",
                classification_confidence=1.0,
                extraction_confidence=1.0,
                benchmark_source="NONE",
            )

    def _build_rfq_pdf(
        self,
        *,
        target: Path,
        rfq_ref: str,
        lines: List[StaticLine],
        geography: str,
        scope_type: str,
    ) -> None:
        styles = getSampleStyleSheet()
        normal = styles["BodyText"]
        title = styles["Title"]
        right = ParagraphStyle("Right", parent=normal, alignment=TA_RIGHT)

        doc = SimpleDocTemplate(
            str(target),
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=14 * mm,
            bottomMargin=14 * mm,
        )
        story = []
        story.append(Paragraph("Request for Quotation", title))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"RFQ Ref: <b>{rfq_ref}</b>", normal))
        story.append(Paragraph("Buyer: Bradsol Procurement Team", normal))
        story.append(Paragraph(f"Geography: {geography} | Scope: {scope_type}", normal))
        story.append(Paragraph("Currency: AED", normal))
        story.append(Spacer(1, 5 * mm))

        table_data = [["Line", "Item Description", "Qty", "UOM"]]
        for idx, line in enumerate(lines, start=1):
            table_data.append(
                [
                    str(idx),
                    line.description,
                    f"{line.quantity.normalize()}",
                    line.uom,
                ]
            )

        table = Table(table_data, colWidths=[14 * mm, 112 * mm, 22 * mm, 20 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9ca3af")),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (2, 1), (-1, -1), "CENTER"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("Please provide itemized quotation with lead time and payment terms.", normal))
        story.append(Paragraph("Required quotes: 3 vendors", right))
        doc.build(story)

    def _build_vendor_pdf(
        self,
        *,
        target: Path,
        rfq_ref: str,
        quotation_ref: str,
        supplier_name: str,
        lines: List[StaticLine],
        layout: str,
    ) -> None:
        styles = getSampleStyleSheet()
        body = styles["BodyText"]
        title = styles["Title"]
        heading = ParagraphStyle(
            "Heading",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=12,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
        )

        doc = SimpleDocTemplate(
            str(target),
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=14 * mm,
            bottomMargin=14 * mm,
        )
        story = []
        story.append(Paragraph(f"Supplier Quotation - {supplier_name}", title))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"Quotation Ref: <b>{quotation_ref}</b>", body))
        story.append(Paragraph(f"Client RFQ Ref: <b>{rfq_ref}</b>", body))
        story.append(Paragraph("Currency: AED", body))
        story.append(Spacer(1, 4 * mm))

        table_data = [["Line", "Description", "Qty", "UOM", "Unit Rate (AED)", "Line Total (AED)"]]
        subtotal = Decimal("0.00")
        for idx, line in enumerate(lines, start=1):
            line_total = (line.quantity * line.base_unit_rate).quantize(Decimal("0.01"))
            subtotal += line_total
            table_data.append(
                [
                    str(idx),
                    line.description,
                    f"{line.quantity.normalize()}",
                    line.uom,
                    f"{line.base_unit_rate:.2f}",
                    f"{line_total:.2f}",
                ]
            )

        vat = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
        grand_total = (subtotal + vat).quantize(Decimal("0.01"))
        table_data.extend(
            [
                ["", "", "", "", "Subtotal", f"{subtotal:.2f}"],
                ["", "", "", "", "VAT 5%", f"{vat:.2f}"],
                ["", "", "", "", "Grand Total", f"{grand_total:.2f}"],
            ]
        )

        table = Table(table_data, colWidths=[14 * mm, 84 * mm, 16 * mm, 16 * mm, 28 * mm, 28 * mm])

        if layout == "classic":
            header_bg = colors.HexColor("#f3f4f6")
            header_fg = colors.HexColor("#111827")
            body_bg = colors.white
        elif layout == "blue_table":
            header_bg = colors.HexColor("#1d4ed8")
            header_fg = colors.white
            body_bg = colors.HexColor("#eff6ff")
        else:
            header_bg = colors.HexColor("#111827")
            header_fg = colors.white
            body_bg = colors.HexColor("#f9fafb")

        table_style = [
            ("BACKGROUND", (0, 0), (-1, 0), header_bg),
            ("TEXTCOLOR", (0, 0), (-1, 0), header_fg),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9ca3af")),
            ("BACKGROUND", (0, 1), (-1, -4), body_bg),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (2, 1), (3, -1), "CENTER"),
            ("ALIGN", (4, 1), (5, -1), "RIGHT"),
            ("FONTNAME", (4, -3), (5, -1), "Helvetica-Bold"),
            ("BACKGROUND", (4, -3), (5, -1), colors.HexColor("#e5e7eb")),
        ]

        if layout == "boxed":
            table_style.extend(
                [
                    ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#111827")),
                    ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#111827")),
                ]
            )

        table.setStyle(TableStyle(table_style))

        story.append(Paragraph("Commercial Offer", heading))
        story.append(Spacer(1, 2 * mm))
        story.append(table)
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Lead time: 10-14 days", body))
        story.append(Paragraph("Payment terms: 30% advance, 70% against delivery", body))
        doc.build(story)