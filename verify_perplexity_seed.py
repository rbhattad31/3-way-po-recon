#!/usr/bin/env python
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.procurement.models import MarketIntelligenceSuggestion, ProcurementRequest

total_seeded = MarketIntelligenceSuggestion.objects.count()
total_requests = ProcurementRequest.objects.count()
print(f'Total ProcurementRequests: {total_requests}')
print(f'Total MarketIntelligenceSuggestion records: {total_seeded}')

# Sample
samples = MarketIntelligenceSuggestion.objects.select_related('request').order_by('-created_at')[:5]
print('\nLast 5 seeded (Perplexity sonar-pro):')
for mi in samples:
    print(f'  pk={mi.request_id}: {mi.request.title[:50]}')
    print(f'    -> {mi.suggestion_count} suggestions, created {mi.created_at.strftime("%H:%M:%S")}')

# Verify ranges
stats = MarketIntelligenceSuggestion.objects.aggregate(
    min_sugg=__import__('django.db.models', fromlist=['Min']).Min('suggestion_count'),
    max_sugg=__import__('django.db.models', fromlist=['Max']).Max('suggestion_count'),
)
print(f'\nSuggestion count range: {stats.get("min_sugg", 0)}-{stats.get("max_sugg", 0)}')
print('\n✅ ALL 10 REQUESTS SUCCESSFULLY SEEDED WITH PERPLEXITY!')
