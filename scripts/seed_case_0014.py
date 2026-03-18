"""
Seed PO and GRN data for case AP-260316-0014 (PO: 2601005).
Invoice: GPI/PSI/20089, PO: 2601005, Vendor: None (not identified on invoice)

Invoice line items:
  1. BURGER BOX - LARGE (PRINTED MCD)          - qty=400, price=95,  amt=43700.00,  tax=5700.00
  2. BURGER BOX - MEDIUM (PRINTED MCD)         - qty=500, price=78,  amt=44850.00,  tax=5850.00
  3. PAPER BAG LARGE - MCD BRANDED             - qty=600, price=42,  amt=29580.00,  tax=3780.00
  4. FRENCH FRIES CONTAINER - LARGE            - qty=300, price=65,  amt=22425.00,  tax=2925.00
  5. DRINK CUP 32 OZ + LID                     - qty=500, price=88,  amt=50600.00,  tax=6600.00
Total: 197,355.00 (subtotal=172,500.00 + tax=24,855.00)
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
case = APCase.objects.filter(case_number='AP-260316-0014').select_related('invoice').first()
if not case:
    print('ERROR: Case AP-260316-0014 not found')
    exit(1)

invoice = case.invoice
po_number = '2601005'

# ── Check if PO already exists ───────────────────────────────────────
if PurchaseOrder.objects.filter(po_number=po_number).exists():
    print(f'PO {po_number} already exists. Skipping.')
    exit(0)

# ── Line item definitions (amounts match invoice line_amounts incl. tax) ─
lines = [
    {
        'line_number': 1,
        'item_code': 'PKG-BBOXLG-MCD',
        'description': 'BURGER BOX - LARGE (PRINTED MCD), Grease-resistant, Recycled',
        'quantity': Decimal('400'),
        'unit_price': Decimal('95.0000'),
        'tax_amount': Decimal('5700.00'),
        'line_amount': Decimal('43700.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Packaging',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 2,
        'item_code': 'PKG-BBOXMD-MCD',
        'description': 'BURGER BOX - MEDIUM (PRINTED MCD), Grease-resistant, Recycled',
        'quantity': Decimal('500'),
        'unit_price': Decimal('78.0000'),
        'tax_amount': Decimal('5850.00'),
        'line_amount': Decimal('44850.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Packaging',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 3,
        'item_code': 'PKG-BAGLG-MCD',
        'description': 'PAPER BAG LARGE - MCD BRANDED, Brown Kraft, 250 pcs/pack',
        'quantity': Decimal('600'),
        'unit_price': Decimal('42.0000'),
        'tax_amount': Decimal('3780.00'),
        'line_amount': Decimal('29580.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Packaging',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 4,
        'item_code': 'PKG-FFCTNLG-MCD',
        'description': 'FRENCH FRIES CONTAINER - LARGE, Cardboard, Grease-proof, MCD',
        'quantity': Decimal('300'),
        'unit_price': Decimal('65.0000'),
        'tax_amount': Decimal('2925.00'),
        'line_amount': Decimal('22425.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Packaging',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 5,
        'item_code': 'PKG-CUP32OZ-MCD',
        'description': 'DRINK CUP 32 OZ + LID, Paper cup, Double-wall, MCD print, 500/cs',
        'quantity': Decimal('500'),
        'unit_price': Decimal('88.0000'),
        'tax_amount': Decimal('6600.00'),
        'line_amount': Decimal('50600.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Packaging',
        'is_service_item': False,
        'is_stock_item': True,
    },
]

po_total = Decimal('197355.00')
po_tax = Decimal('24855.00')

with transaction.atomic():
    # ── Create Purchase Order ────────────────────────────────────────
    po = PurchaseOrder.objects.create(
        po_number=po_number,
        normalized_po_number=po_number,
        vendor=None,
        po_date=date(2026, 1, 7),
        currency='SAR',
        total_amount=po_total,
        tax_amount=po_tax,
        status='OPEN',
        buyer_name='Mohammed Al-Shehri',
        department='Operations & Packaging',
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
        receipt_date=date(2026, 1, 12),
        status='RECEIVED',
        warehouse='Riyadh Central Warehouse',
        receiver_name='Abdulrahman Al-Dosari',
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

print(f'\nDone! PO {po_number} + GRN GRN-{po_number}-001 created for case AP-260316-0014')
