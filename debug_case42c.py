from apps.cases.models import APCase
from apps.documents.models import Invoice

inv = Invoice.objects.get(pk=67)
cases = APCase.objects.filter(invoice=inv)
for c in cases:
    print(f'Case pk={c.pk}, number={c.case_number}, status={c.status}, is_active={c.is_active}')
    print(f'  processing_path={c.processing_path}, current_stage={c.current_stage}')
    print(f'  created_at={c.created_at}')
    # Check stages
    stages = c.stages.all().order_by('sequence_order')
    for s in stages:
        print(f'  Stage: {s.stage_type} status={s.status} error={s.error_message}')
