"""Query invoice referencing PO 2601005."""
from apps.documents.models import Invoice, InvoiceLineItem, PurchaseOrder
from apps.cases.models import APCase

inv = Invoice.objects.filter(po_number='2601005').first()
if inv:
    print('Invoice ID:', inv.id)
    print('Invoice Number:', inv.invoice_number)
    print('Vendor ID:', inv.vendor_id, '| Vendor:', inv.vendor)
    print('Currency:', inv.currency)
    print('Total:', inv.total_amount, '| Tax:', inv.tax_amount, '| Subtotal:', inv.subtotal)
    print('Invoice Date:', inv.invoice_date)
    for li in InvoiceLineItem.objects.filter(invoice=inv).order_by('line_number'):
        print(f'  Line {li.line_number}: {li.description[:60]} qty={li.quantity} price={li.unit_price} amt={li.line_amount} tax={li.tax_amount}')
    case = APCase.objects.filter(invoice=inv).first()
    if case:
        print('Case:', case.case_number, '| Status:', case.status)
else:
    print('No invoice found with PO 2601005')

if PurchaseOrder.objects.filter(po_number='2601005').exists():
    print('PO 2601005 already exists!')
else:
    print('No PO 2601005 yet.')
