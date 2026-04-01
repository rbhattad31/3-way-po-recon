from apps.extraction.models import ExtractionResult, ExtractionApproval
er = ExtractionResult.objects.select_related('invoice').get(pk=42)
inv = er.invoice
print(f'ExtractionResult pk={er.pk}, invoice_pk={inv.pk}, invoice_number={inv.invoice_number}')
print(f'Invoice status={inv.status}')
print(f'Invoice vendor={inv.raw_vendor_name}, po={inv.po_number}')

try:
    ea = ExtractionApproval.objects.get(invoice=inv)
    print(f'Approval: status={ea.status}, approved_by={ea.approved_by}')
except ExtractionApproval.DoesNotExist:
    print('No ExtractionApproval found')

from apps.cases.models import APCase
cases = APCase.objects.filter(invoice=inv)
print(f'Cases for this invoice: {cases.count()}')
for c in cases:
    print(f'  Case pk={c.pk}, number={c.case_number}, status={c.status}, is_active={c.is_active}')

# Check if invoice shows in pending list
from apps.core.enums import InvoiceStatus
print(f'Invoice status == READY_FOR_RECON? {inv.status == InvoiceStatus.READY_FOR_RECON}')
print(f'InvoiceStatus.READY_FOR_RECON = {InvoiceStatus.READY_FOR_RECON}')
