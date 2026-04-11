"""
Minimal pure-Python PDF generator for seed / test quotation files.

Generates simple 1-page PDFs with basic text content.
No external libraries required.

Usage:
    from apps.benchmarking.fixtures.pdf_generator import generate_quotation_pdf
    pdf_bytes = generate_quotation_pdf("HVAC Quotation", [("1", "VRF Unit", "No", "2", "18500", "37000")])
"""
from __future__ import annotations

import textwrap
from io import BytesIO


def _escape_pdf_string(s: str) -> str:
    """Escape special characters for PDF string literals."""
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def generate_quotation_pdf(
    title: str,
    supplier_name: str,
    ref_number: str,
    rows: list[tuple],   # (line_no, description, uom, qty, unit_rate, amount)
    footer_text: str = "",
) -> bytes:
    """
    Generate a simple single-page PDF containing a quotation table.

    Args:
        title       : Document title e.g. 'HVAC Supply & Install Quotation'
        supplier_name: Company name for the header
        ref_number  : Quotation reference number
        rows        : List of tuples (line_no, description, uom, qty, unit_rate, amount)
        footer_text : Optional footer (e.g. terms)

    Returns:
        bytes   : Valid PDF file content
    """
    # --- Build the text content string ---
    lines_pdf = []

    # Header block
    lines_pdf.append(f"QUOTATION DOCUMENT")
    lines_pdf.append(f"")
    lines_pdf.append(f"Supplier  : {supplier_name}")
    lines_pdf.append(f"Reference : {ref_number}")
    lines_pdf.append(f"Subject   : {title}")
    lines_pdf.append(f"Currency  : AED")
    lines_pdf.append(f"")
    lines_pdf.append(f"{'No.':<5} {'Description':<45} {'UOM':<8} {'Qty':<7} {'Unit Rate':<12} {'Amount':<12}")
    lines_pdf.append("-" * 95)

    for row in rows:
        ln, desc, uom, qty, unit_rate, amount = row
        # Wrap long descriptions
        wrapped = textwrap.wrap(str(desc), width=43) or [""]
        first = True
        for part in wrapped:
            if first:
                lines_pdf.append(
                    f"{str(ln):<5} {part:<45} {str(uom):<8} {str(qty):<7} {str(unit_rate):<12} {str(amount):<12}"
                )
                first = False
            else:
                lines_pdf.append(f"{'':5} {part}")

    lines_pdf.append("-" * 95)

    # Compute total from amount column (last element of each row)
    total = 0.0
    for row in rows:
        try:
            total += float(str(row[5]).replace(",", ""))
        except (ValueError, IndexError):
            pass
    lines_pdf.append(f"{'':>74} TOTAL: AED {total:>14,.2f}")
    lines_pdf.append(f"")

    if footer_text:
        lines_pdf.append(footer_text)
    else:
        lines_pdf.append("Terms: As per agreed contract. Validity: 30 days.")
        lines_pdf.append("All rates are inclusive of material, labour, and VAT @ 5%.")

    text_content = "\n".join(lines_pdf)

    # --- Assemble the PDF binary ---
    # Minimal valid PDF (Text-only, no fonts embedded, uses Courier)
    return _build_minimal_pdf(text_content, title)


def _build_minimal_pdf(text: str, title: str = "Quotation") -> bytes:
    """
    Build the smallest valid PDF that contains the given text string.
    Uses PDF standard Courier (a built-in font -- no embedding needed).
    Page size: A4 (595 x 842 pt).
    """
    font_size = 8
    line_height = font_size + 2
    left_margin = 40
    top_margin = 800

    # Split into lines and build BT...ET block
    text_lines = text.split("\n")
    bt_cmds = []
    y = top_margin
    for txt_line in text_lines:
        escaped = _escape_pdf_string(txt_line)
        bt_cmds.append(f"{left_margin} {y} Td ({escaped}) Tj 0 {-line_height} Td")
        y -= line_height

    content_stream = (
        f"BT\n"
        f"/F1 {font_size} Tf\n"
        f"{left_margin} {top_margin} Td\n"
    )
    for escaped_line in text_lines:
        esc = _escape_pdf_string(escaped_line)
        content_stream += f"({esc}) Tj T*\n"
    content_stream += "ET\n"

    # Build object table
    objects = {}

    # Object 1: Catalog
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    # Object 2: Pages
    objects[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"

    # Object 3: Page
    objects[3] = (
        f"<< /Type /Page /Parent 2 0 R "
        f"/MediaBox [0 0 595 842] "
        f"/Contents 4 0 R "
        f"/Resources << /Font << /F1 5 0 R >> >> "
        f">>"
    ).encode()

    # Object 4: Content stream
    stream_bytes = content_stream.encode("latin-1", errors="replace")
    objects[4] = (
        f"<< /Length {len(stream_bytes)} >>\nstream\n"
    ).encode() + stream_bytes + b"\nendstream"

    # Object 5: Font (Courier -- built-in, no embedding)
    objects[5] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"

    # Build the PDF bytes
    buf = BytesIO()
    buf.write(b"%PDF-1.4\n")

    offsets = {}
    for obj_num in sorted(objects.keys()):
        offsets[obj_num] = buf.tell()
        obj_data = objects[obj_num]
        buf.write(f"{obj_num} 0 obj\n".encode())
        buf.write(obj_data)
        buf.write(b"\nendobj\n")

    # Cross-reference table
    xref_offset = buf.tell()
    buf.write(b"xref\n")
    buf.write(f"0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for obj_num in sorted(objects.keys()):
        buf.write(f"{offsets[obj_num]:010d} 00000 n \n".encode())

    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )

    return buf.getvalue()
