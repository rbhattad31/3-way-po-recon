"""Debug script to inspect case #1 reconciliation data."""
import os, sys, django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.cases.models import APCase
from apps.documents.models import (
    Invoice, InvoiceLineItem, PurchaseOrder, PurchaseOrderLineItem,
    GoodsReceiptNote, GRNLineItem,
)
from apps.reconciliation.models import (
    ReconciliationResult, ReconciliationException, ReconciliationResultLine,
)

c = APCase.objects.get(pk=1)
inv = c.invoice
print("=== CASE ===")
print(f"Case #{c.pk} status={c.status}")
print(f"Invoice #{inv.pk} number={inv.invoice_number} total={inv.total_amount} subtotal={inv.subtotal} tax={inv.tax_amount}")
print(f"  PO ref: {inv.po_number}")
print(f"  is_partial_invoice: {getattr(inv, 'is_partial_invoice', 'N/A')}")

for li in InvoiceLineItem.objects.filter(invoice=inv).order_by("line_number"):
    print(f"  Line {li.line_number}: desc={li.description[:60] if li.description else ''}")
    print(f"    qty={li.quantity} unit_price={li.unit_price} line_amount={li.line_amount} tax={li.tax_amount}")

po = PurchaseOrder.objects.filter(po_number=inv.po_number).first()
if po:
    print(f"\n=== PO ===")
    print(f"PO #{po.pk} number={po.po_number} total={po.total_amount}")
    for li in PurchaseOrderLineItem.objects.filter(purchase_order=po).order_by("line_number"):
        print(f"  Line {li.line_number}: desc={li.description[:60] if li.description else ''}")
        print(f"    qty={li.quantity} unit_price={li.unit_price} amount={li.line_amount} tax={li.tax_amount}")
else:
    print("NO PO FOUND")

grns = GoodsReceiptNote.objects.filter(purchase_order=po) if po else GoodsReceiptNote.objects.none()
print(f"\n=== GRNs: {grns.count()} ===")
for g in grns:
    print(f"GRN #{g.pk} number={g.grn_number}")
    for li in GRNLineItem.objects.filter(goods_receipt_note=g):
        print(f"  Line {li.line_number}: qty_received={li.quantity_received} qty_ordered={li.quantity_ordered}")

print(f"\n=== RECON RESULTS ===")
for rr in ReconciliationResult.objects.filter(invoice=inv):
    print(f"Result #{rr.pk} match={rr.match_status} mode={rr.reconciliation_mode}")
    print(f"  vendor_match={rr.vendor_match} currency_match={rr.currency_match} po_total_match={rr.po_total_match}")
    for ex in ReconciliationException.objects.filter(result=rr).order_by("pk"):
        print(f"  EX: type={ex.exception_type} sev={ex.severity}")
        print(f"      {ex.message[:120]}")
    for rl in ReconciliationResultLine.objects.filter(result=rr).order_by("line_number"):
        print(f"  RL#{rl.line_number}: match={rl.match_status} inv_amt={rl.invoice_amount} po_amt={rl.po_amount} grn_qty={rl.grn_quantity}")
