import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.cases.models import APCase

c = APCase.objects.filter(case_number='AP-260313-0015').first()
if c:
    print(f'pk={c.pk}, is_active={c.is_active}, status={c.status}, invoice_id={c.invoice_id}')
    print(f'vendor_id={c.vendor_id}, path={c.processing_path}')
    # Check if it appears in the inbox queryset
    from apps.cases.template_views import case_inbox
    qs = APCase.objects.filter(is_active=True).order_by('-created_at')
    found = qs.filter(pk=c.pk).exists()
    print(f'In default queryset: {found}')
else:
    print('Case not found')
    # Check without case_number filter
    latest = APCase.objects.order_by('-pk')[:5]
    for x in latest:
        print(f'  pk={x.pk}, case_number={x.case_number}, is_active={x.is_active}')
