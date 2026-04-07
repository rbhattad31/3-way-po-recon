"""Check invoice and line items for case AP-260406-0001."""
from apps.documents.models import Invoice, InvoiceLineItem
from apps.extraction.models import ExtractionResult

inv = Invoice.objects.get(pk=1)
print(f"Invoice: {inv.invoice_number}")
print(f"  po_number: '{inv.po_number}'")
print(f"  total_amount: {inv.total_amount}")
print(f"  currency: {inv.currency}")
print(f"  vendor: {inv.vendor}")
print(f"  raw_vendor_name: {inv.raw_vendor_name}")
print(f"  status: {inv.status}")

lines = InvoiceLineItem.objects.filter(invoice=inv)
print(f"Line items: {lines.count()}")
for li in lines:
    desc = li.description[:60] if li.description else ""
    print(f"  pk={li.pk}: desc='{desc}', qty={li.quantity}, price={li.unit_price}, total={li.line_total}")

er = ExtractionResult.objects.filter(document_upload__invoice=inv).first()
if er:
    extracted = er.extracted_data or {}
    print(f"Extracted PO: '{extracted.get('po_number', '')}'")
    print(f"Extracted keys: {list(extracted.keys())[:20]}")
