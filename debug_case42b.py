from apps.extraction.models import ExtractionResult, ExtractionApproval
from apps.cases.models import APCase
from apps.core.enums import InvoiceStatus

er = ExtractionResult.objects.select_related('invoice').get(pk=42)
inv = er.invoice
print(f'Invoice pk={inv.pk}, status={inv.status}, number={inv.invoice_number}')

cases = APCase.objects.filter(invoice=inv)
print(f'Cases count: {cases.count()}')

# Check pending invoices query (same as case_inbox view)
from apps.documents.models import Invoice
pending = Invoice.objects.filter(
    status=InvoiceStatus.READY_FOR_RECON,
).exclude(
    pk__in=APCase.objects.filter(is_active=True).values_list("invoice_id", flat=True)
)
print(f'Pending invoices (READY_FOR_RECON, no case): {pending.count()}')
for p in pending:
    print(f'  pk={p.pk}, number={p.invoice_number}, vendor={p.raw_vendor_name}')
