"""
Seed PO and GRN data for case AP-260316-0012.
Invoice: ASD/INV/20688, PO: 2601017, Vendor: Al-Safi Danone Co. Ltd. (ID=3)

Invoice line items:
  1. COLA SYRUP BIB 20L (COLA-COLA BRAND) - qty=300, unit_price=285
  2. ORANGE JUICE SYRUP BIB 20L - qty=150, unit_price=320
  3. HOT CHOCOLATE MIX 5KG - qty=100, unit_price=195
  4. CARAMEL SAUCE DISPENSER PACK 2KG - qty=200, unit_price=165
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
case = APCase.objects.filter(case_number='AP-260316-0012').select_related('invoice').first()
if not case:
    print('ERROR: Case AP-260316-0012 not found')
    exit(1)

invoice = case.invoice
vendor = Vendor.objects.get(id=3)
po_number = '2601017'

# ── Check if PO already exists ───────────────────────────────────────
if PurchaseOrder.objects.filter(po_number=po_number).exists():
    print(f'PO {po_number} already exists. Skipping.')
    exit(0)

# ── Line item definitions ────────────────────────────────────────────
lines = [
    {
        'line_number': 1,
        'item_code': 'BEV-COLA-20L',
        'description': 'COLA SYRUP BIB 20L (COLA-COLA BRAND)',
        'quantity': Decimal('300'),
        'unit_price': Decimal('285.0000'),
        'tax_amount': Decimal('12825.00'),
        'line_amount': Decimal('85500.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Beverages',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 2,
        'item_code': 'BEV-OJ-20L',
        'description': 'ORANGE JUICE SYRUP BIB 20L',
        'quantity': Decimal('150'),
        'unit_price': Decimal('320.0000'),
        'tax_amount': Decimal('7200.00'),
        'line_amount': Decimal('48000.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Beverages',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 3,
        'item_code': 'BEV-CHOC-5KG',
        'description': 'HOT CHOCOLATE MIX 5KG',
        'quantity': Decimal('100'),
        'unit_price': Decimal('195.0000'),
        'tax_amount': Decimal('2925.00'),
        'line_amount': Decimal('19500.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Beverages',
        'is_service_item': False,
        'is_stock_item': True,
    },
    {
        'line_number': 4,
        'item_code': 'BEV-CARM-2KG',
        'description': 'CARAMEL SAUCE DISPENSER PACK 2KG',
        'quantity': Decimal('200'),
        'unit_price': Decimal('165.0000'),
        'tax_amount': Decimal('4950.00'),
        'line_amount': Decimal('33000.00'),
        'unit_of_measure': 'EA',
        'item_category': 'Beverages',
        'is_service_item': False,
        'is_stock_item': True,
    },
]

po_subtotal = sum(l['line_amount'] for l in lines)  # 186,000
po_tax = sum(l['tax_amount'] for l in lines)         # 27,900
po_total = po_subtotal + po_tax                      # 213,900

with transaction.atomic():
    # ── Create Purchase Order ────────────────────────────────────────
    po = PurchaseOrder.objects.create(
        po_number=po_number,
        normalized_po_number=po_number,
        vendor=vendor,
        po_date=date(2026, 2, 10),
        currency='SAR',
        total_amount=po_total,
        tax_amount=po_tax,
        status='OPEN',
        buyer_name='Ahmed Al-Rashidi',
        department='Food & Beverage Supply',
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
        print(f'  PO Line {pol.line_number}: {pol.description} qty={pol.quantity} @ {pol.unit_price}')

    # ── Create Goods Receipt Note ────────────────────────────────────
    grn = GoodsReceiptNote.objects.create(
        grn_number='GRN-2601017-001',
        purchase_order=po,
        vendor=vendor,
        receipt_date=date(2026, 2, 15),
        status='RECEIVED',
        warehouse='Riyadh Central Warehouse',
        receiver_name='Khalid Al-Mutairi',
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
        print(f'  GRN Line {grn_line.line_number}: {grn_line.description} received={grn_line.quantity_received}')

    # ── Link PO to case ──────────────────────────────────────────────
    case.purchase_order = po
    case.save(update_fields=['purchase_order'])
    print(f'Linked PO {po.po_number} to case {case.case_number}')

print('\n✅ Seed data created successfully!')
print(f'  PO: {po_number} with {len(lines)} line items')
print(f'  GRN: GRN-2601017-001 with {len(lines)} line items (all fully received)')
print(f'  PO Total: SAR {po_total:,.2f} (subtotal: {po_subtotal:,.2f} + tax: {po_tax:,.2f})')
