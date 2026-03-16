"""Query case AP-260316-0012 details."""
from apps.cases.models import APCase
from apps.documents.models import InvoiceLineItem

c = APCase.objects.filter(case_number='AP-260316-0012').select_related('invoice', 'vendor', 'purchase_order').first()
if c:
    print('Case ID:', c.id)
    print('Case Number:', c.case_number)
    print('Status:', c.status)
    print('Processing Path:', c.processing_path)
    print('Invoice Type:', c.invoice_type)
    print('Recon Mode:', c.reconciliation_mode)
    print('PO linked:', c.purchase_order_id)
    inv = c.invoice
    if inv:
        print('--- Invoice ---')
        print('Invoice ID:', inv.id)
        print('Invoice Number:', inv.invoice_number)
        print('PO Number:', inv.po_number)
        print('Normalized PO:', inv.normalized_po_number)
        print('Vendor ID:', inv.vendor_id)
        print('Vendor:', inv.vendor)
        print('Currency:', inv.currency)
        print('Subtotal:', inv.subtotal)
        print('Tax:', inv.tax_amount)
        print('Total:', inv.total_amount)
        print('Status:', inv.status)
        print('Invoice Date:', inv.invoice_date)
        # Line items
        lines = InvoiceLineItem.objects.filter(invoice=inv).order_by('line_number')
        print(f'--- Invoice Line Items ({lines.count()}) ---')
        for li in lines:
            print(f'  Line {li.line_number}: desc={li.description}, qty={li.quantity}, unit_price={li.unit_price}, line_amount={li.line_amount}, tax={li.tax_amount}, category={li.item_category}, is_service={li.is_service_item}, is_stock={li.is_stock_item}')
    if c.vendor:
        print('--- Vendor ---')
        print('Vendor ID:', c.vendor.id)
        print('Vendor Name:', c.vendor.name)
        print('Vendor Code:', c.vendor.vendor_code)
    # Check existing PO
    from apps.documents.models import PurchaseOrder
    if inv and inv.po_number:
        po = PurchaseOrder.objects.filter(po_number=inv.po_number).first()
        if po:
            print('--- Existing PO found ---')
            print('PO ID:', po.id)
        else:
            print('--- No PO found for po_number:', inv.po_number)
    # Check existing GRN
    from apps.documents.models import GoodsReceiptNote
    if inv and inv.po_number:
        po = PurchaseOrder.objects.filter(po_number=inv.po_number).first()
        if po:
            grns = GoodsReceiptNote.objects.filter(purchase_order=po)
            print(f'--- GRNs for PO ({grns.count()}) ---')
        else:
            print('--- No PO, cannot check GRNs ---')
else:
    print('Case not found')
