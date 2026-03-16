"""Fix PO total_amount to match invoice total."""
from decimal import Decimal
from apps.documents.models import PurchaseOrder

po = PurchaseOrder.objects.get(po_number='2601017')
print(f'Before: total={po.total_amount}, tax={po.tax_amount}')
po.total_amount = Decimal('212400.00')
po.save(update_fields=['total_amount'])
print(f'After: total={po.total_amount}, tax={po.tax_amount}')
print('Done.')
