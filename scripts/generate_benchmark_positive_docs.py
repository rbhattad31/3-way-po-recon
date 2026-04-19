from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


OUTPUT_DIR = Path("media/benchmarking/positive_test_docs")


def _money(value: float) -> str:
    return f"AED {value:,.2f}"


def _build_pdf(file_path: Path, payload: dict) -> None:
    doc = SimpleDocTemplate(
        str(file_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"<b>Supplier Quotation - {payload['supplier_name']}</b>", styles["Title"]))
    story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            (
                f"Quotation Ref: <b>{payload['quotation_ref']}</b><br/>"
                f"Project: <b>{payload['project_name']}</b><br/>"
                f"Client RFQ Ref: <b>{payload['rfq_ref']}</b><br/>"
                f"Geography: <b>{payload['geography']}</b><br/>"
                f"Scope Type: <b>{payload['scope_type']}</b><br/>"
                f"Validity: <b>{payload['validity_days']} days</b>"
            ),
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 10))

    table_data = [["Line", "Item Description", "Qty", "Unit", "Unit Price (AED)", "Line Total (AED)"]]
    subtotal = 0.0
    for index, line in enumerate(payload["lines"], start=1):
        line_total = line["qty"] * line["unit_price"]
        subtotal += line_total
        table_data.append(
            [
                str(index),
                line["description"],
                str(line["qty"]),
                line["unit"],
                _money(line["unit_price"]),
                _money(line_total),
            ]
        )

    vat = subtotal * 0.05
    grand_total = subtotal + vat
    table_data.append(["", "", "", "", "Subtotal", _money(subtotal)])
    table_data.append(["", "", "", "", "VAT 5%", _money(vat)])
    table_data.append(["", "", "", "", "Grand Total", _money(grand_total)])

    table = Table(table_data, colWidths=[14 * mm, 74 * mm, 18 * mm, 20 * mm, 32 * mm, 32 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ALIGN", (2, 1), (2, -1), "CENTER"),
                ("ALIGN", (4, 1), (5, -1), "RIGHT"),
                ("FONTNAME", (4, -3), (5, -1), "Helvetica-Bold"),
                ("BACKGROUND", (4, -3), (5, -1), colors.HexColor("#f3f4f6")),
                ("SPAN", (0, -3), (3, -3)),
                ("SPAN", (0, -2), (3, -2)),
                ("SPAN", (0, -1), (3, -1)),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    story.append(table)
    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            (
                "Notes:<br/>"
                "1. Delivery: 3-4 weeks from approved PO.<br/>"
                "2. Payment Terms: 30% advance, 70% after delivery.<br/>"
                "3. Prices are in AED and exclusive of freight unless mentioned."
            ),
            styles["BodyText"],
        )
    )

    doc.build(story)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docs = [
        {
            "file_name": "quotation_01_al_najah_hvac.pdf",
            "supplier_name": "Al Najah HVAC Trading LLC",
            "quotation_ref": "ANH-QUO-2026-001",
            "project_name": "Dubai Mall Expansion Phase 3",
            "rfq_ref": "RFQ-HVAC-DXB-1001",
            "geography": "UAE",
            "scope_type": "SITC",
            "validity_days": 30,
            "lines": [
                {"description": "AHU 4500 CFM with VFD", "qty": 4, "unit": "Nos", "unit_price": 12850.0},
                {"description": "Chilled Water FCU 800 CFM", "qty": 18, "unit": "Nos", "unit_price": 2450.0},
                {"description": "GI Ducting Fabrication and Installation", "qty": 2100, "unit": "Sqm", "unit_price": 42.0},
                {"description": "Copper Piping 22mm with Insulation", "qty": 900, "unit": "Mtr", "unit_price": 29.0},
                {"description": "Testing and Commissioning", "qty": 1, "unit": "Lot", "unit_price": 12500.0},
            ],
        },
        {
            "file_name": "quotation_02_desert_cooling_solutions.pdf",
            "supplier_name": "Desert Cooling Solutions",
            "quotation_ref": "DCS-QTN-2624",
            "project_name": "Warehouse Retrofit - Abu Dhabi",
            "rfq_ref": "RFQ-HVAC-AUH-2045",
            "geography": "UAE",
            "scope_type": "ITC",
            "validity_days": 21,
            "lines": [
                {"description": "VRF Outdoor Unit 20HP", "qty": 3, "unit": "Nos", "unit_price": 21400.0},
                {"description": "VRF Indoor Cassette Unit", "qty": 24, "unit": "Nos", "unit_price": 2850.0},
                {"description": "Refrigerant Piping Set", "qty": 1300, "unit": "Mtr", "unit_price": 36.0},
                {"description": "Control Cable and Accessories", "qty": 900, "unit": "Mtr", "unit_price": 9.5},
                {"description": "Installation Supervision and T&C", "qty": 1, "unit": "Lot", "unit_price": 9800.0},
            ],
        },
        {
            "file_name": "quotation_03_polar_air_mep.pdf",
            "supplier_name": "Polar Air MEP Services",
            "quotation_ref": "PAM-Quote-1189",
            "project_name": "Retail Hypermarket Fitout",
            "rfq_ref": "RFQ-HVAC-SHJ-3120",
            "geography": "UAE",
            "scope_type": "EQUIPMENT_ONLY",
            "validity_days": 25,
            "lines": [
                {"description": "Air Cooled Chiller 120 TR", "qty": 2, "unit": "Nos", "unit_price": 86500.0},
                {"description": "Primary Pump Set with Panel", "qty": 2, "unit": "Set", "unit_price": 22400.0},
                {"description": "Plate Heat Exchanger", "qty": 2, "unit": "Nos", "unit_price": 18900.0},
                {"description": "Expansion Tank 500L", "qty": 2, "unit": "Nos", "unit_price": 3700.0},
                {"description": "Vibration Isolator Kit", "qty": 10, "unit": "Set", "unit_price": 420.0},
            ],
        },
        {
            "file_name": "quotation_04_gulf_climate_technologies.pdf",
            "supplier_name": "Gulf Climate Technologies",
            "quotation_ref": "GCT-2026-447",
            "project_name": "Mixed Use Tower HVAC Upgrade",
            "rfq_ref": "RFQ-HVAC-RKT-7781",
            "geography": "UAE",
            "scope_type": "SITC",
            "validity_days": 35,
            "lines": [
                {"description": "Ducted Split Unit 10 TR", "qty": 6, "unit": "Nos", "unit_price": 17450.0},
                {"description": "Fresh Air Handling Unit 3000 CFM", "qty": 4, "unit": "Nos", "unit_price": 14700.0},
                {"description": "MS Support and Hangers", "qty": 1, "unit": "Lot", "unit_price": 11800.0},
                {"description": "Pre-insulated Duct Panels", "qty": 1450, "unit": "Sqm", "unit_price": 51.0},
                {"description": "BMS Integration and Functional Testing", "qty": 1, "unit": "Lot", "unit_price": 16400.0},
            ],
        },
    ]

    generated = []
    for item in docs:
        path = OUTPUT_DIR / item["file_name"]
        _build_pdf(path, item)
        generated.append(path)

    print("Generated positive test quotation PDFs:")
    for path in generated:
        print(f"- {path}")


if __name__ == "__main__":
    main()
