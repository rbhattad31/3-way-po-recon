"""Fix PO line amounts for case AP-260316-0012 to match invoice line amounts."""
from decimal import Decimal
from apps.documents.models import PurchaseOrder, PurchaseOrderLineItem

po = PurchaseOrder.objects.get(po_number='2601017')

# Invoice line amounts (include tax): 98325, 55200, 22425, 37950
fixes = {
    1: Decimal('98325.00'),
    2: Decimal('55200.00'),
    3: Decimal('22425.00'),
    4: Decimal('37950.00'),
}

for line_num, new_amount in fixes.items():
    pol = PurchaseOrderLineItem.objects.get(purchase_order=po, line_number=line_num)
    old = pol.line_amount
    pol.line_amount = new_amount
    pol.save(update_fields=['line_amount'])
    print(f'Line {line_num}: {old} -> {new_amount}')

# Update PO total to match: sum of line amounts + tax
new_subtotal = sum(fixes.values())  # 213,900
po.total_amount = new_subtotal + po.tax_amount
po.save(update_fields=['total_amount'])
print(f'PO total updated to: {po.total_amount}')
print('Done.')
