"""Query case AP-260316-0004 details on local DB."""
from apps.cases.models import APCase
from apps.documents.models import InvoiceLineItem, PurchaseOrder, GoodsReceiptNote
from apps.reconciliation.models import ReconciliationResult, ReconciliationException

c = APCase.objects.filter(case_number='AP-260316-0004').select_related('invoice', 'vendor', 'purchase_order', 'reconciliation_result').first()
if not c:
    print('Case not found')
    exit()

print('Case:', c.case_number, 'Status:', c.status, 'Path:', c.processing_path)
print('Invoice Type:', c.invoice_type, 'Recon Mode:', c.reconciliation_mode)

inv = c.invoice
if inv:
    print('\n--- Invoice ---')
    print('ID:', inv.id, 'Number:', inv.invoice_number)
    print('PO Number:', inv.po_number, 'Normalized:', inv.normalized_po_number)
    print('Vendor ID:', inv.vendor_id, 'Vendor:', inv.vendor)
    print('Status:', inv.status)
    print('Total:', inv.total_amount, 'Currency:', inv.currency)

    # Check if vendor is null
    if inv.vendor_id is None:
        print('>>> VENDOR IS NULL on invoice <<<')

# Check PO
if c.purchase_order:
    po = c.purchase_order
    print('\n--- PO ---')
    print('PO:', po.po_number, 'Vendor:', po.vendor_id, po.vendor)
else:
    print('\n--- No PO linked to case ---')
    if inv and inv.po_number:
        po = PurchaseOrder.objects.filter(po_number=inv.po_number).first()
        if po:
            print('But PO exists in DB:', po.po_number, 'Vendor:', po.vendor)
        else:
            npo = PurchaseOrder.objects.filter(normalized_po_number=inv.normalized_po_number).first()
            if npo:
                print('Found by normalized PO:', npo.po_number)
            else:
                print('No PO found for po_number:', inv.po_number, 'or normalized:', inv.normalized_po_number)

# Check reconciliation result
rr = c.reconciliation_result
if rr:
    print('\n--- Reconciliation Result ---')
    print('ID:', rr.id, 'Match Status:', rr.match_status)
    print('Mode:', rr.reconciliation_mode, 'Mode Resolved By:', rr.mode_resolved_by)
    print('PO ID on result:', rr.purchase_order_id)
    
    # Exceptions
    exceptions = ReconciliationException.objects.filter(reconciliation_result=rr).order_by('id')
    print(f'\n--- Exceptions ({exceptions.count()}) ---')
    for ex in exceptions:
        print(f'  [{ex.exception_type}] sev={ex.severity} detail={ex.detail[:200]}')
else:
    print('\n--- No reconciliation result ---')

# Also check if there are any recon results by invoice
all_results = ReconciliationResult.objects.filter(invoice=inv)
print(f'\nAll recon results for invoice: {all_results.count()}')
for r in all_results:
    print(f'  Result ID={r.id} match={r.match_status} mode={r.reconciliation_mode} po={r.purchase_order_id}')
