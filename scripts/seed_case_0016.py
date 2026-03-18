"""
Seed PO and GRN data for case AP-260316-0016.
Invoice: SEA/INV/20445, PO: 2601006, Vendor: None (not identified on invoice)

Invoice line items:
  1. FLOOR CLEANER CONCENTRATE 5L      - qty=200,  unit_price=145, line_amount=33350.00,  tax=4350.00
  2. GRILL CLEANER - HEAVY DUTY 5L     - qty=150,  unit_price=185, line_amount=31912.50,  tax=4162.50
  3. HAND SANITIZER GEL 500ML          - qty=300,  unit_price=95,  line_amount=32775.00,  tax=4275.00
  4. SANITIZING WIPES (FOOD CONTACT)   - qty=250,  unit_price=78,  line_amount=22425.00,  tax=2925.00
  5. PAPER TOWEL ROLL - 2 PLY          - qty=400,  unit_price=55,  line_amount=25300.00,  tax=3300.00
Total: 146,512.50 (subtotal=127,500.00 + tax=19,012.50)
"""
from decimal import Decimal
from datetime import date
from django.db import transaction
from apps.documents.models import (
    PurchaseOrder, PurchaseOrderLineItem,
    GoodsReceiptNote, GRNLineItem,
)
from apps.cases.models import APCase

# ── Verify case exists ───────────────────────────────────────────────
case = APCase.objects.filter(case_number='AP-260316-0016').select_related('invoice').first()
if not case:
    print('ERROR: Case AP-260316-0016 not found')
    exit(1)

invoice = case.invoice
po_number = '2601006'

# ── Check if PO already exists ───────────────────────────────────────
if PurchaseOrder.objects.filter(po_number=po_number).exists():
    print(f'PO {po_number} already exists. Skipping.')
    exit(0)

# ── Line item definitions (amounts match invoice line_amounts incl. tax) ─
lines = [
    {
        'line_number': 1,
        'item_code': 'CHEM-FLRCLN-5L',
        'description': 'FLOOR CLEANER CONCENTRATE 5L',
        'quantity': Decimal('200'),
        'unit_price': Decimal('145.0000'),
        'tax_amount': Decimal('4350.00'),
        'line_amount': Decimal('33350.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Cleaning Supplies',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 2,
        'item_code': 'CHEM-GRILLCLN-5L',
        'description': 'GRILL CLEANER - HEAVY DUTY 5L',
        'quantity': Decimal('150'),
        'unit_price': Decimal('185.0000'),
        'tax_amount': Decimal('4162.50'),
        'line_amount': Decimal('31912.50'),
        'unit_of_measure': 'EA',
        'item_category': 'Cleaning Supplies',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 3,
        'item_code': 'HYG-SNGEL-500ML',
        'description': 'HAND SANITIZER GEL 500ML',
        'quantity': Decimal('300'),
        'unit_price': Decimal('95.0000'),
        'tax_amount': Decimal('4275.00'),
        'line_amount': Decimal('32775.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Hygiene Supplies',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 4,
        'item_code': 'HYG-SNWIPE-FC',
        'description': 'SANITIZING WIPES (FOOD CONTACT)',
        'quantity': Decimal('250'),
        'unit_price': Decimal('78.0000'),
        'tax_amount': Decimal('2925.00'),
        'line_amount': Decimal('22425.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Hygiene Supplies',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 5,
        'item_code': 'PPR-TWLRL-2PLY',
        'description': 'PAPER TOWEL ROLL - 2 PLY',
        'quantity': Decimal('400'),
        'unit_price': Decimal('55.0000'),
        'tax_amount': Decimal('3300.00'),
        'line_amount': Decimal('25300.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Paper & Disposables',
        'is_service_item': False,
        'is_stock_item': True,
    },
]

po_total = Decimal('146512.50')
po_tax = Decimal('19012.50')

with transaction.atomic():
    # ── Create Purchase Order (no vendor — matches null vendor on invoice) ─
    po = PurchaseOrder.objects.create(
        po_number=po_number,
        normalized_po_number=po_number,
        vendor=None,
        po_date=date(2026, 1, 10),
        currency='SAR',
        total_amount=po_total,
        tax_amount=po_tax,
        status='OPEN',
        buyer_name='Tariq Al-Zahrani',
        department='Facilities & Hygiene',
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
        print(f'  PO Line {pol.line_number}: {pol.description[:55]} qty={pol.quantity} @ {pol.unit_price}')

    # ── Create Goods Receipt Note ────────────────────────────────────
    grn = GoodsReceiptNote.objects.create(
        grn_number=f'GRN-{po_number}-001',
        purchase_order=po,
        vendor=None,
        receipt_date=date(2026, 1, 15),
        status='RECEIVED',
        warehouse='Jeddah Operations Warehouse',
        receiver_name='Nasser Al-Ghamdi',
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
        print(f'  GRN Line {grn_line.line_number}: {grn_line.description[:55]} received={grn_line.quantity_received}')

    # ── Link PO to case ──────────────────────────────────────────────
    case.purchase_order = po
    case.save(update_fields=['purchase_order'])
    print(f'Linked PO {po.po_number} to case {case.case_number}')

print(f'\nDone! PO {po_number} + GRN GRN-{po_number}-001 created for case AP-260316-0016')
