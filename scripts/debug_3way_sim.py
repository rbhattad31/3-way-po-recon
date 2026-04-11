"""Debug: simulate full 3-way match and inspect GRN match result."""
import os, sys, django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.services.po_lookup_service import POLookupService
from apps.reconciliation.services.tolerance_engine import ToleranceEngine
from apps.reconciliation.services.three_way_match_service import ThreeWayMatchService
from apps.reconciliation.services.po_balance_service import POBalanceService
from apps.reconciliation.models import ReconciliationConfig

invoice = Invoice.objects.first()
po = PurchaseOrder.objects.first()
print(f"Invoice: {invoice.invoice_number} (pk={invoice.pk})")
print(f"PO: {po.po_number} (pk={po.pk})")

# Build PO result
from apps.reconciliation.services.po_lookup_service import POLookupResult
po_result = POLookupResult(found=True, purchase_order=po)

# Build PO balance
po_balance = POBalanceService.compute(po, invoice, partial_threshold_pct=95.0)
print(f"\nPO Balance:")
print(f"  is_partial={po_balance.is_partial}")
print(f"  is_first_partial={po_balance.is_first_partial}")
print(f"  prior_invoice_count={po_balance.prior_invoice_count}")

# Build tolerance engine
config = ReconciliationConfig.objects.first()
engine = ToleranceEngine(config)

# Run 3-way match
svc = ThreeWayMatchService(engine)
output = svc.match(invoice, po_result, po_balance=po_balance)

print(f"\n=== ThreeWayMatchOutput ===")
print(f"header_result: all_ok={output.header_result.all_ok if output.header_result else None}")
print(f"line_result: all_matched={output.line_result.all_lines_matched if output.line_result else None}")

if output.line_result:
    for i, pair in enumerate(output.line_result.pairs):
        print(f"  Pair #{i}: matched={pair.matched} "
              f"inv_line={pair.invoice_line.pk} po_line={pair.po_line.pk if pair.po_line else None}")

print(f"\ngrn_result exists: {output.grn_result is not None}")
if output.grn_result:
    print(f"  grn_available={output.grn_result.grn_available}")
    print(f"  line_comparisons count={len(output.grn_result.line_comparisons)}")
    for cmp in output.grn_result.line_comparisons:
        print(f"    po_line_id={cmp.po_line_id} qty_received={cmp.qty_received} "
              f"qty_invoiced={cmp.qty_invoiced} qty_ordered={cmp.qty_ordered}")
    print(f"  has_receipt_issues={output.grn_result.has_receipt_issues}")
    print(f"  erp_source_type={output.grn_result.erp_source_type}")

# Now simulate what result_service._save_line_results would do
print(f"\n=== Simulating _save_line_results GRN merge ===")
from decimal import Decimal
grn_qty_by_po_line = {}
if output.grn_result and output.grn_result.grn_available:
    for cmp in output.grn_result.line_comparisons:
        if cmp.po_line_id is not None and cmp.qty_received is not None:
            grn_qty_by_po_line[cmp.po_line_id] = cmp.qty_received
print(f"grn_qty_by_po_line = {grn_qty_by_po_line}")

if output.line_result:
    for pair in output.line_result.pairs:
        po_line_id = pair.po_line.pk if pair.po_line else None
        would_get = grn_qty_by_po_line.get(po_line_id) if po_line_id else None
        print(f"  pair po_line.pk={po_line_id}, would assign qty_received={would_get}")
