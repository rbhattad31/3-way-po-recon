"""Invoice extraction skill -- OCR, classification, field extraction."""
from apps.agents.skills.base import Skill, register_skill

invoice_extraction_skill = register_skill(Skill(
    name="invoice_extraction",
    description="Extract structured data from invoice documents via OCR and LLM.",
    prompt_extension=(
        "## UNDERSTAND Phase\n"
        "You have access to OCR and extraction tools. Your goal is to obtain "
        "structured invoice data (header fields + line items) from the document.\n\n"
        "Steps:\n"
        "1. Call `get_ocr_text` to retrieve raw OCR text from the document.\n"
        "2. Call `classify_document` to confirm the document is an invoice.\n"
        "3. Call `extract_invoice_fields` to obtain structured header and line data.\n"
        "4. Review the extraction confidence. If confidence < 0.7, consider calling "
        "`re_extract_field` for specific low-confidence fields before proceeding.\n\n"
        "Do NOT skip extraction -- it is the foundation of all downstream processing."
    ),
    tools=[
        "get_ocr_text",
        "classify_document",
        "extract_invoice_fields",
        "re_extract_field",
    ],
    decision_hints=[
        "If extraction confidence is below 0.5, call re_extract_field for critical "
        "fields (invoice_number, total_amount, po_number, vendor_name) before proceeding.",
        "If the document is not an invoice, stop and report classification mismatch.",
    ],
))
