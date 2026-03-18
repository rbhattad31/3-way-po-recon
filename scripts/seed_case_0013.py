"""
Seed PO and GRN data for case AP-260316-0013.
Invoice: NAD/PSI/20356, PO: 2601015, Vendor: NADEC (ID=4)

Invoice line items:
  1. PROCESSED CHEESE SLICES 4.54KG - qty=1200, unit_price=115, line_amount=158700, tax=20700
  2. CHEDDAR SHREDDED CHEESE 2.5KG - qty=400, unit_price=175, line_amount=80500, tax=10500
Total: 238,700 (subtotal=207,500 + tax=31,200)
"""
from decimal import Decimal
from datetime import date
from django.db import transaction
from apps.documents.models import (
    PurchaseOrder, PurchaseOrderLineItem,
    GoodsReceiptNote, GRNLineItem,
)
from apps.vendors.models import Vendor
from apps.cases.models import APCase

# ── Verify case exists ───────────────────────────────────────────────
case = APCase.objects.filter(case_number='AP-260316-0013').select_related('invoice').first()
if not case:
    print('ERROR: Case AP-260316-0013 not found')
    exit(1)

invoice = case.invoice
vendor = Vendor.objects.get(id=4)
po_number = '2601015'

# ── Check if PO already exists ───────────────────────────────────────
if PurchaseOrder.objects.filter(po_number=po_number).exists():
    print(f'PO {po_number} already exists. Skipping.')
    exit(0)

# ── Line item definitions (amounts match invoice line_amounts) ───────
lines = [
    {
        'line_number': 1,
        'item_code': 'DAIRY-CHSLC-454',
        'description': 'PROCESSED CHEESE SLICES - 4.54KG, American-style, 240 slices/tray, Halal, +4 degC. MCD spec #CS-240',
        'quantity': Decimal('1200'),
        'unit_price': Decimal('115.0000'),
        'tax_amount': Decimal('20700.00'),
        'line_amount': Decimal('158700.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Dairy',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 2,
        'item_code': 'DAIRY-CHSHR-250',
        'description': 'CHEDDAR SHREDDED CHEESE 2.5KG, Halal, Natural Cheddar, 4x2.5 KG/carton. +4 degC',
        'quantity': Decimal('400'),
        'unit_price': Decimal('175.0000'),
        'tax_amount': Decimal('10500.00'),
        'line_amount': Decimal('80500.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Dairy',
        'is_service_item': False,
        'is_stock_item': True,
    },
]

po_total = Decimal('238700.00')   # matches invoice total
po_tax = Decimal('31200.00')      # matches invoice tax

with transaction.atomic():
    # ── Create Purchase Order ────────────────────────────────────────
    po = PurchaseOrder.objects.create(
        po_number=po_number,
        normalized_po_number=po_number,
        vendor=vendor,
        po_date=date(2026, 2, 5),
        currency='SAR',
        total_amount=po_total,
        tax_amount=po_tax,
        status='OPEN',
        buyer_name='Faisal Al-Harbi',
        department='Food & Dairy Supply',
    )
    print(f'Created PO: {po.po_number} (ID={po.id})')

    # ── Create PO Line Items ────────────────────────────────────────
    po_lines = []
    for l in lines:
        pol = PurchaseOrderLineItem.objects.create(
            purchase_order=po,
            line_number=l['line_number'],
            item_code=l['item_code'],
            description=l['description'],
            quantity=l['quantity'],
            unit_price=l['unit_price'],
            tax_amount=l['tax_amount'],
            line_amount=l['line_amount'],
            unit_of_measure=l['unit_of_measure'],
            item_category=l['item_category'],
            is_service_item=l['is_service_item'],
            is_stock_item=l['is_stock_item'],
        )
        po_lines.append(pol)
        print(f'  PO Line {pol.line_number}: {pol.description[:60]} qty={pol.quantity} @ {pol.unit_price}')

    # ── Create Goods Receipt Note ────────────────────────────────────
    grn = GoodsReceiptNote.objects.create(
        grn_number=f'GRN-{po_number}-001',
        purchase_order=po,
        vendor=vendor,
        receipt_date=date(2026, 2, 10),
        status='RECEIVED',
        warehouse='Riyadh Cold Storage',
        receiver_name='Omar Al-Qahtani',
    )
    print(f'Created GRN: {grn.grn_number} (ID={grn.id})')

    # ── Create GRN Line Items (all fully received) ───────────────────
    for i, l in enumerate(lines):
        grn_line = GRNLineItem.objects.create(
            grn=grn,
            line_number=l['line_number'],
            po_line=po_lines[i],
            item_code=l['item_code'],
            description=l['description'],
            quantity_received=l['quantity'],
            quantity_accepted=l['quantity'],
            quantity_rejected=Decimal('0'),
            unit_of_measure=l['unit_of_measure'],
        )
        print(f'  GRN Line {grn_line.line_number}: {grn_line.description[:60]} received={grn_line.quantity_received}')

    # ── Link PO to case ──────────────────────────────────────────────
    case.purchase_order = po
    case.save(update_fields=['purchase_order'])
    print(f'Linked PO {po.po_number} to case {case.case_number}')

print(f'\nDone! PO {po_number} + GRN GRN-{po_number}-001 created for case AP-260316-0013')
