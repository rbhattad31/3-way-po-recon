"""Debug: inspect GRN qty flow for Result #2."""
import os, sys, django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.reconciliation.models import ReconciliationResult, ReconciliationResultLine
from apps.documents.models import GoodsReceiptNote, GRNLineItem, PurchaseOrderLineItem

# Get latest result
results = list(ReconciliationResult.objects.order_by("-created_at")[:2])
for r in results:
    print(f"\n=== Result #{r.pk} ({r.created_at}) ===")
    print(f"  match_status={r.match_status}")
    print(f"  grn_checked_flag={r.grn_checked_flag}")
    print(f"  grn_required_flag={r.grn_required_flag}")
    print(f"  grn_erp_source_type={r.grn_erp_source_type}")
    
    lines = ReconciliationResultLine.objects.filter(result=r)
    for rl in lines:
        print(f"  Line: inv_line_id={rl.invoice_line_id} po_line_id={rl.po_line_id} "
              f"qty_received={rl.qty_received} qty_po={rl.qty_po} qty_invoice={rl.qty_invoice}")

# Check GRN data  
print("\n=== GRN Data ===")
for grn in GoodsReceiptNote.objects.all():
    print(f"GRN #{grn.pk}: po={grn.purchase_order_id} receipt_date={grn.receipt_date}")
    for gl in GRNLineItem.objects.filter(grn=grn):
        print(f"  Line #{gl.pk}: po_line_id={gl.po_line_id} "
              f"qty_received={gl.quantity_received} qty_accepted={gl.quantity_accepted}")

# Check PO lines
print("\n=== PO Lines ===")
for pol in PurchaseOrderLineItem.objects.all():
    print(f"PO Line #{pol.pk}: po={pol.purchase_order_id} qty={pol.quantity} "
          f"price={pol.unit_price} amount={pol.line_amount}")

# --- Simulate GRN lookup to see what would happen ---
print("\n=== Simulating GRN Lookup ===")
from apps.reconciliation.services.grn_lookup_service import GRNLookupService
from apps.reconciliation.services.po_lookup_service import POLookupService

po_svc = POLookupService()
grn_svc = GRNLookupService()

# Find the PO
from apps.documents.models import PurchaseOrder
pos = PurchaseOrder.objects.all()
for po in pos:
    print(f"\nPO: {po.po_number} (pk={po.pk})")
    summary = grn_svc.lookup(po)
    print(f"  grn_available={summary.grn_available}")
    print(f"  grn_count={summary.grn_count}")
    print(f"  total_received_by_po_line={dict(summary.total_received_by_po_line)}")
    print(f"  fully_received={summary.fully_received}")
