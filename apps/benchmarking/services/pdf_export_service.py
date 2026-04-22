"""
BenchmarkPDFExportService
--------------------------
Generates a professional ReportLab PDF report for a BenchmarkRequest.

Sections:
  1. Cover header -- title, metadata badges
  2. Executive summary KPIs
  3. Vendor comparison table
  4. Itemised cost analysis (line items, capped at 200 rows)
  5. AI insights & negotiation talking points
  6. Category summary table
  7. Footer on every page
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
BRAND_DARK = colors.HexColor("#1e3c72")
BRAND_BLUE = colors.HexColor("#2a84d2")
BRAND_LIGHT = colors.HexColor("#eff6ff")
GREEN = colors.HexColor("#1b8f5a")
GREEN_BG = colors.HexColor("#e6f7ee")
RED = colors.HexColor("#d32f2f")
RED_BG = colors.HexColor("#fde2e2")
AMBER = colors.HexColor("#b26a00")
AMBER_BG = colors.HexColor("#fff4e5")
GREY = colors.HexColor("#64748b")
GREY_BG = colors.HexColor("#f8fafc")
DIVIDER = colors.HexColor("#e2e8f0")
BLACK = colors.HexColor("#0f172a")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_currency(value: Any, default: str = "--") -> str:
    try:
        return f"AED {float(value):,.0f}" if value is not None else default
    except (TypeError, ValueError):
        return default


def _fmt_pct(value: Any, default: str = "--") -> str:
    try:
        if value is None:
            return default
        sign = "+" if float(value) > 0 else ""
        return f"{sign}{float(value):.1f}%"
    except (TypeError, ValueError):
        return default


def _variance_colours(status: str):
    """Return (text_color, background_color) for a variance status string."""
    mapping = {
        "WITHIN_RANGE": (GREEN, GREEN_BG),
        "MODERATE": (AMBER, AMBER_BG),
        "HIGH": (RED, RED_BG),
        "NEEDS_REVIEW": (GREY, GREY_BG),
    }
    return mapping.get(status, (GREY, GREY_BG))


def _status_label(status: str) -> str:
    return {
        "WITHIN_RANGE": "Optimal",
        "MODERATE": "Moderate",
        "HIGH": "High Variance",
        "NEEDS_REVIEW": "Needs Review",
    }.get(status, status.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Style sheet
# ---------------------------------------------------------------------------

def _build_styles() -> dict:
    base = getSampleStyleSheet()
    styles = {}

    styles["cover_title"] = ParagraphStyle(
        "cover_title",
        fontSize=18,
        leading=22,
        textColor=colors.white,
        fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    styles["cover_subtitle"] = ParagraphStyle(
        "cover_subtitle",
        fontSize=9,
        leading=14,
        textColor=colors.HexColor("#dbeafe"),
        fontName="Helvetica",
    )
    styles["cover_badge"] = ParagraphStyle(
        "cover_badge",
        fontSize=8,
        leading=12,
        textColor=colors.white,
        fontName="Helvetica",
        borderPadding=2,
    )
    styles["section_title"] = ParagraphStyle(
        "section_title",
        fontSize=12,
        leading=16,
        textColor=BRAND_DARK,
        fontName="Helvetica-Bold",
        spaceAfter=6,
        spaceBefore=10,
    )
    styles["kpi_label"] = ParagraphStyle(
        "kpi_label",
        fontSize=7,
        leading=10,
        textColor=GREY,
        fontName="Helvetica-Bold",
        spaceAfter=2,
    )
    styles["kpi_value"] = ParagraphStyle(
        "kpi_value",
        fontSize=14,
        leading=18,
        textColor=BLACK,
        fontName="Helvetica-Bold",
    )
    styles["th"] = ParagraphStyle(
        "th",
        fontSize=7,
        leading=10,
        textColor=GREY,
        fontName="Helvetica-Bold",
        alignment=TA_LEFT,
    )
    styles["td"] = ParagraphStyle(
        "td",
        fontSize=7.5,
        leading=11,
        textColor=BLACK,
        fontName="Helvetica",
    )
    styles["td_r"] = ParagraphStyle(
        "td_r",
        fontSize=7.5,
        leading=11,
        textColor=BLACK,
        fontName="Helvetica",
        alignment=TA_RIGHT,
    )
    styles["td_c"] = ParagraphStyle(
        "td_c",
        fontSize=7.5,
        leading=11,
        textColor=BLACK,
        fontName="Helvetica",
        alignment=TA_CENTER,
    )
    styles["insight"] = ParagraphStyle(
        "insight",
        fontSize=8,
        leading=13,
        textColor=colors.HexColor("#334155"),
        fontName="Helvetica",
        leftIndent=10,
        spaceBefore=3,
        spaceAfter=3,
        bulletText="*",
        bulletFontName="Helvetica",
        bulletFontSize=10,
        bulletColor=BRAND_BLUE,
        bulletIndent=2,
    )
    styles["footer"] = ParagraphStyle(
        "footer",
        fontSize=7,
        leading=10,
        textColor=GREY,
        fontName="Helvetica",
        alignment=TA_CENTER,
    )
    styles["normal"] = base["Normal"]
    return styles


# ---------------------------------------------------------------------------
# Page footer callback
# ---------------------------------------------------------------------------

def _footer_callback(canvas, doc):
    canvas.saveState()
    w, h = doc.pagesize
    canvas.setFillColor(DIVIDER)
    canvas.rect(0, 0, w, 18, fill=1, stroke=0)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(20, 5, "Confidential -- Should-Cost Benchmarking Report")
    canvas.drawRightString(w - 20, 5, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _cover_section(bench_request, styles, page_width) -> list:
    """Blue gradient-style header block."""
    from reportlab.platypus import InlineImage
    from datetime import date

    story = []

    # Header table (simulates the blue gradient card)
    meta_parts = [bench_request.geography, bench_request.scope_type]
    if bench_request.store_type:
        meta_parts.append(bench_request.store_type)
    meta_parts.append(f"Status: {bench_request.status}")
    meta_text = "   |   ".join(meta_parts)

    submitted_by = ""
    if bench_request.submitted_by:
        submitted_by = getattr(bench_request.submitted_by, "get_full_name", lambda: "")() or str(bench_request.submitted_by)

    date_str = ""
    if bench_request.created_at:
        date_str = bench_request.created_at.strftime("%d %b %Y")

    header_data = [[
        Paragraph(f"<b>{bench_request.title}</b>", styles["cover_title"]),
    ]]
    header_table = Table(header_data, colWidths=[page_width])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_DARK),
        ("ROUNDEDCORNERS", [12, 12, 12, 12]),
        ("TOPPADDING", (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
    ]))
    story.append(header_table)

    # Sub-header meta row
    meta_row_data = [[
        Paragraph("Benchmarking Report", styles["cover_subtitle"]),
        Paragraph(meta_text, styles["cover_badge"]),
    ]]
    if submitted_by or date_str:
        right_text = f"{submitted_by}  |  {date_str}" if submitted_by and date_str else (submitted_by or date_str)
        meta_row_data[0].append(Paragraph(right_text, ParagraphStyle("mr", fontSize=8, textColor=GREY, fontName="Helvetica", alignment=TA_RIGHT)))
        col_widths = [page_width * 0.35, page_width * 0.40, page_width * 0.25]
    else:
        col_widths = [page_width * 0.45, page_width * 0.55]

    meta_row = Table(meta_row_data, colWidths=col_widths)
    meta_row.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
    ]))
    story.append(meta_row)
    story.append(Spacer(1, 14))
    return story


def _kpi_section(vendor_cards, all_line_items, result, styles, page_width) -> list:
    """5-cell KPI card row."""
    story = []
    story.append(Paragraph("Executive Summary", styles["section_title"]))

    # Compute KPIs
    vendor_count = len(vendor_cards)
    total_lines = len(all_line_items)
    high_count = sum(1 for li in all_line_items if getattr(li, "variance_status", "") == "HIGH")

    potential_savings = 0.0
    for li in all_line_items:
        try:
            if li.benchmark_mid is not None and li.quoted_unit_rate is not None and li.quantity is not None:
                diff = (float(li.quoted_unit_rate) - float(li.benchmark_mid)) * float(li.quantity)
                if diff > 0:
                    potential_savings += diff
        except (TypeError, ValueError):
            pass

    variance_values = [
        float(li.variance_pct)
        for li in all_line_items
        if li.variance_pct is not None
    ]
    avg_variance = (sum(variance_values) / len(variance_values)) if variance_values else None

    def _kpi_cell(label: str, value: str, color=BLACK):
        return [
            Paragraph(label.upper(), styles["kpi_label"]),
            Paragraph(f'<font color="{color.hexval() if hasattr(color, "hexval") else "#0f172a"}">{value}</font>',
                      styles["kpi_value"]),
        ]

    avg_var_str = _fmt_pct(avg_variance)
    avg_color = RED if (avg_variance or 0) > 15 else (AMBER if (avg_variance or 0) > 5 else GREEN)
    savings_str = _fmt_currency(potential_savings) if potential_savings > 0 else "--"

    kpi_cells = [
        _kpi_cell("Vendors Compared", str(vendor_count)),
        _kpi_cell("Total Line Items", str(total_lines)),
        _kpi_cell("High Variance Items", str(high_count), RED if high_count > 0 else GREEN),
        _kpi_cell("Potential Savings", savings_str, GREEN),
        _kpi_cell("Avg Variance", avg_var_str, avg_color),
    ]

    cell_width = page_width / 5
    kpi_table = Table([kpi_cells], colWidths=[cell_width] * 5, rowHeights=[56])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#eff6ff")),
        ("BACKGROUND", (1, 0), (1, 0), GREY_BG),
        ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#fff1f2")),
        ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#ecfdf3")),
        ("BACKGROUND", (4, 0), (4, 0), colors.HexColor("#fff7ed")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("BOX", (0, 0), (-1, -1), 0.5, DIVIDER),
        ("LINEAFTER", (0, 0), (3, 0), 0.5, DIVIDER),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 14))
    return story


def _vendor_table_section(vendor_cards, styles, page_width) -> list:
    """Vendor comparison table."""
    story = []
    story.append(Paragraph("Vendor Comparison", styles["section_title"]))
    story.append(HRFlowable(width=page_width, thickness=0.5, color=DIVIDER, spaceAfter=6))

    col_widths = [
        page_width * 0.28,  # Vendor
        page_width * 0.18,  # Total Quote
        page_width * 0.18,  # Should Cost
        page_width * 0.15,  # % Variance
        page_width * 0.21,  # Status
    ]

    header = [
        Paragraph("VENDOR", styles["th"]),
        Paragraph("TOTAL QUOTE (AED)", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("SHOULD COST (AED)", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("% VARIANCE", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("STATUS", ParagraphStyle("thc", parent=styles["th"], alignment=TA_CENTER)),
    ]
    rows = [header]
    row_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, DIVIDER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, DIVIDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    for i, card in enumerate(vendor_cards):
        status = card.get("status", "NEEDS_REVIEW")
        txt_color, bg_color = _variance_colours(status)
        dev = card.get("deviation_pct")
        dev_str = _fmt_pct(dev)

        row = [
            Paragraph(f"<b>{card.get('supplier_name', '--')}</b><br/>"
                      f"<font size='6' color='#6b7280'>{card.get('quotation_ref', '') or ''}</font>",
                      styles["td"]),
            Paragraph(_fmt_currency(card.get("total_quoted"), "--"), styles["td_r"]),
            Paragraph(_fmt_currency(card.get("total_benchmark"), "--"), styles["td_r"]),
            Paragraph(dev_str, ParagraphStyle("varpct", parent=styles["td_r"],
                                              textColor=txt_color, fontName="Helvetica-Bold")),
            Paragraph(_status_label(status),
                      ParagraphStyle("stat", parent=styles["td_c"], textColor=txt_color, fontName="Helvetica-Bold")),
        ]
        rows.append(row)
        row_idx = i + 1
        row_styles.append(("BACKGROUND", (4, row_idx), (4, row_idx), bg_color))
        if i % 2 == 0:
            row_styles.append(("BACKGROUND", (0, row_idx), (3, row_idx), colors.white))

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(row_styles))
    story.append(table)
    story.append(Spacer(1, 14))
    return story


def _line_items_section(vendor_cards, styles, page_width) -> list:
    """Itemised cost analysis, limited to 200 rows."""
    story = []
    story.append(PageBreak())
    story.append(Paragraph("Itemised Cost Analysis", styles["section_title"]))
    story.append(HRFlowable(width=page_width, thickness=0.5, color=DIVIDER, spaceAfter=6))

    LINE_CAP = 200
    col_widths = [
        page_width * 0.05,   # #
        page_width * 0.30,   # Description
        page_width * 0.12,   # Vendor
        page_width * 0.09,   # Category
        page_width * 0.08,   # Qty
        page_width * 0.10,   # Unit Rate
        page_width * 0.11,   # Benchmark Mid
        page_width * 0.07,   # Variance
        page_width * 0.08,   # Status
    ]

    header = [
        Paragraph("#", styles["th"]),
        Paragraph("DESCRIPTION", styles["th"]),
        Paragraph("VENDOR", styles["th"]),
        Paragraph("CATEGORY", styles["th"]),
        Paragraph("QTY", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("UNIT RATE", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("BENCHMARK", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("VAR%", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("STATUS", ParagraphStyle("thc", parent=styles["th"], alignment=TA_CENTER)),
    ]
    rows = [header]
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, DIVIDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, DIVIDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]

    row_num = 0
    truncated = False
    for card in vendor_cards:
        vendor_label = card.get("supplier_name", "--")
        for li in card.get("line_items", []):
            if row_num >= LINE_CAP:
                truncated = True
                break
            status = getattr(li, "variance_status", "NEEDS_REVIEW")
            txt_color, bg_color = _variance_colours(status)
            cat = getattr(li, "category", "") or ""
            var_pct = getattr(li, "variance_pct", None)
            var_str = _fmt_pct(var_pct, "--")

            row = [
                Paragraph(str(row_num + 1), styles["td"]),
                Paragraph((getattr(li, "description", "") or "")[:100], styles["td"]),
                Paragraph(vendor_label[:20], styles["td"]),
                Paragraph(cat.replace("_", " ").title(), styles["td"]),
                Paragraph(str(getattr(li, "quantity", "--") or "--"), styles["td_r"]),
                Paragraph(_fmt_currency(getattr(li, "quoted_unit_rate", None), "--"), styles["td_r"]),
                Paragraph(_fmt_currency(getattr(li, "benchmark_mid", None), "--"), styles["td_r"]),
                Paragraph(var_str, ParagraphStyle("vr", parent=styles["td_r"], textColor=txt_color)),
                Paragraph(_status_label(status), ParagraphStyle(
                    "sc", parent=styles["td_c"], textColor=txt_color, fontName="Helvetica-Bold")),
            ]
            rows.append(row)
            table_row_idx = row_num + 1
            style_cmds.append(("BACKGROUND", (8, table_row_idx), (8, table_row_idx), bg_color))
            if row_num % 2 == 1:
                style_cmds.append(("BACKGROUND", (0, table_row_idx), (7, table_row_idx), colors.HexColor("#f8fafc")))
            row_num += 1
        if truncated:
            break

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    if truncated:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"<i>Table truncated at {LINE_CAP} rows. Download CSV export for the full dataset.</i>",
            ParagraphStyle("note", fontSize=7, textColor=GREY, fontName="Helvetica-Oblique"),
        ))
    story.append(Spacer(1, 14))
    return story


def _insights_section(result, styles, page_width) -> list:
    """AI insights and negotiation talking points."""
    story = []
    insights = []
    negotiation_notes = []
    if result is not None:
        insights = result.negotiation_notes_json or []
        negotiation_notes = result.negotiation_notes_json or []
        # Try to get insights from a dedicated field if present
        from apps.benchmarking.models import BenchmarkResult
        if hasattr(result, "negotiation_notes_json"):
            negotiation_notes = result.negotiation_notes_json or []

    # AI Insights are stored alongside negotiation notes in most setups.
    # Render them as one combined section.
    if not insights and not negotiation_notes:
        return story

    story.append(PageBreak())
    story.append(Paragraph("AI Insights & Negotiation Talking Points", styles["section_title"]))
    story.append(HRFlowable(width=page_width, thickness=0.5, color=DIVIDER, spaceAfter=8))

    combined = list(insights)
    for note in negotiation_notes:
        if note not in combined:
            combined.append(note)

    for note in combined:
        if isinstance(note, str) and note.strip():
            story.append(Paragraph(note.strip(), styles["insight"]))

    story.append(Spacer(1, 10))
    return story


def _category_section(category_summary: dict, styles, page_width) -> list:
    """Category summary table."""
    if not category_summary:
        return []

    story = []
    story.append(Paragraph("Category Summary", styles["section_title"]))
    story.append(HRFlowable(width=page_width, thickness=0.5, color=DIVIDER, spaceAfter=6))

    col_widths = [
        page_width * 0.30,
        page_width * 0.17,
        page_width * 0.17,
        page_width * 0.17,
        page_width * 0.19,
    ]

    header = [
        Paragraph("CATEGORY", styles["th"]),
        Paragraph("QUOTED (AED)", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("BENCHMARK MID (AED)", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("DEVIATION", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
        Paragraph("LINES", ParagraphStyle("thr", parent=styles["th"], alignment=TA_RIGHT)),
    ]
    rows = [header]
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, DIVIDER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, DIVIDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    for i, (cat, dat) in enumerate(category_summary.items()):
        dev = dat.get("deviation_pct")
        dev_str = _fmt_pct(dev) if dev is not None else "--"
        dev_status = "WITHIN_RANGE" if abs(dev or 0) < 5 else ("MODERATE" if abs(dev or 0) < 15 else "HIGH")
        txt_color, _ = _variance_colours(dev_status)

        row = [
            Paragraph(str(cat).replace("_", " ").title(), styles["td"]),
            Paragraph(_fmt_currency(dat.get("quoted"), "--"), styles["td_r"]),
            Paragraph(_fmt_currency(dat.get("benchmark_mid"), "--"), styles["td_r"]),
            Paragraph(dev_str, ParagraphStyle("dr", parent=styles["td_r"], textColor=txt_color)),
            Paragraph(str(dat.get("count", "--")), styles["td_r"]),
        ]
        rows.append(row)
        if i % 2 == 1:
            style_cmds.append(("BACKGROUND", (0, i + 1), (-1, i + 1), colors.HexColor("#f8fafc")))

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    story.append(Spacer(1, 14))
    return story


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class BenchmarkPDFExportService:
    """
    Usage::

        pdf_bytes = BenchmarkPDFExportService.generate(bench_request)
    """

    @staticmethod
    def generate(bench_request) -> bytes:
        """
        Build and return a PDF (bytes) for the given BenchmarkRequest.
        Accepts any BenchmarkRequest instance; gracefully handles missing
        result / line items.
        """
        buffer = BytesIO()

        # Use landscape A4 so wide tables fit.
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=2 * cm,
            bottomMargin=1.8 * cm,
            author="Should-Cost Benchmarking Platform",
            title=bench_request.title,
        )
        page_width = landscape(A4)[0] - 3 * cm  # usable width

        styles = _build_styles()

        # -- Fetch data -------------------------------------------------------
        quotations = list(
            bench_request.quotations.filter(is_active=True).prefetch_related("line_items")
        )
        vendor_cards = []
        all_line_items = []

        for idx, q in enumerate(quotations, start=1):
            fallback = f"Vendor {chr(64 + idx)}" if idx <= 26 else f"Vendor {idx}"
            supplier_name = (q.supplier_name or "").strip() or fallback
            q_items = list(q.line_items.filter(is_active=True))
            all_line_items.extend(q_items)

            q_total = sum(float(li.line_amount or 0) for li in q_items)
            q_bench = 0.0
            q_bench_covered = 0.0
            for li in q_items:
                if li.benchmark_mid is not None:
                    qty = float(li.quantity or 1)
                    q_bench += float(li.benchmark_mid) * qty
                    q_bench_covered += float(li.line_amount or 0)

            dev = None
            if q_bench > 0:
                dev = ((q_bench_covered - q_bench) / q_bench) * 100
            status = "NEEDS_REVIEW"
            if dev is not None:
                status = "WITHIN_RANGE" if abs(dev) < 5 else ("MODERATE" if abs(dev) < 15 else "HIGH")

            vendor_cards.append({
                "supplier_name": supplier_name,
                "quotation_ref": q.quotation_ref or "",
                "line_items": q_items,
                "total_quoted": q_total,
                "total_benchmark": q_bench if q_bench > 0 else None,
                "deviation_pct": dev,
                "status": status,
            })

        result = None
        category_summary = {}
        try:
            result = bench_request.result
            category_summary = result.category_summary_json or {}
        except Exception:
            pass

        # -- Assemble story ---------------------------------------------------
        story: list = []

        story.extend(_cover_section(bench_request, styles, page_width))
        story.extend(_kpi_section(vendor_cards, all_line_items, result, styles, page_width))
        story.extend(_vendor_table_section(vendor_cards, styles, page_width))
        story.extend(_line_items_section(vendor_cards, styles, page_width))
        story.extend(_insights_section(result, styles, page_width))
        story.extend(_category_section(category_summary, styles, page_width))

        try:
            doc.build(story, onFirstPage=_footer_callback, onLaterPages=_footer_callback)
        except Exception:
            logger.exception("PDF generation failed for BenchmarkRequest pk=%s", bench_request.pk)
            raise

        return buffer.getvalue()
