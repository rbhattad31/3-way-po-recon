"""
LearningFeedbackService — Generates analytics from corrections and failures.

Uses:
- Corrections (ExtractionCorrection records)
- Rejected extraction runs
- Low-confidence fields

Generates:
- Field weakness stats
- Vendor extraction patterns
- Persists ExtractionAnalyticsSnapshot
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Optional

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.extraction_core.models import (
    ExtractionAnalyticsSnapshot,
    ExtractionCorrection,
    ExtractionFieldValue,
    ExtractionRun,
)
from apps.extraction_core.services.extraction_audit import ExtractionAuditService

logger = logging.getLogger(__name__)


class LearningFeedbackService:
    """
    Generates analytics snapshots from extraction outcomes.
    """

    DEFAULT_PERIOD_DAYS = 30

    @classmethod
    def generate_field_weakness_snapshot(
        cls,
        country_code: str = "",
        regime_code: str = "",
        period_days: int = DEFAULT_PERIOD_DAYS,
        user=None,
    ) -> ExtractionAnalyticsSnapshot:
        """
        Analyze which fields have the most corrections and lowest
        confidence, grouped by field_code.

        Returns a persisted ExtractionAnalyticsSnapshot.
        """
        period_start = date.today() - timedelta(days=period_days)
        period_end = date.today()

        # Query corrections in period
        corrections_qs = ExtractionCorrection.objects.filter(
            created_at__date__gte=period_start,
        )
        runs_qs = ExtractionRun.objects.filter(
            created_at__date__gte=period_start,
            status="COMPLETED",
        )

        if country_code:
            corrections_qs = corrections_qs.filter(
                extraction_run__country_code=country_code,
            )
            runs_qs = runs_qs.filter(country_code=country_code)
        if regime_code:
            corrections_qs = corrections_qs.filter(
                extraction_run__regime_code=regime_code,
            )
            runs_qs = runs_qs.filter(regime_code=regime_code)

        # Field correction counts
        correction_counts = (
            corrections_qs
            .values("field_code")
            .annotate(count=Count("id"))
            .order_by("-count")[:20]
        )

        # Low confidence fields
        low_confidence_fields = (
            ExtractionFieldValue.objects
            .filter(
                extraction_run__in=runs_qs,
                confidence__lt=0.6,
                confidence__isnull=False,
            )
            .values("field_code")
            .annotate(
                count=Count("id"),
                avg_confidence=Avg("confidence"),
            )
            .order_by("avg_confidence")[:20]
        )

        run_count = runs_qs.count()
        correction_count = corrections_qs.count()
        avg_confidence = runs_qs.aggregate(
            avg=Avg("overall_confidence"),
        )["avg"]

        data = {
            "most_corrected_fields": list(correction_counts),
            "lowest_confidence_fields": list(low_confidence_fields),
            "total_runs": run_count,
            "total_corrections": correction_count,
        }

        snapshot = ExtractionAnalyticsSnapshot.objects.create(
            snapshot_type="field_weakness",
            country_code=country_code,
            regime_code=regime_code,
            period_start=period_start,
            period_end=period_end,
            data_json=data,
            run_count=run_count,
            correction_count=correction_count,
            average_confidence=avg_confidence,
            created_by=user,
        )

        ExtractionAuditService.log_analytics_snapshot_created(
            snapshot_id=snapshot.pk,
            snapshot_type="field_weakness",
            user=user,
            country_code=country_code,
            regime_code=regime_code,
        )

        return snapshot

    @classmethod
    def generate_vendor_pattern_snapshot(
        cls,
        country_code: str = "",
        period_days: int = DEFAULT_PERIOD_DAYS,
        user=None,
    ) -> ExtractionAnalyticsSnapshot:
        """
        Analyze extraction patterns per vendor.

        Returns a persisted ExtractionAnalyticsSnapshot.
        """
        period_start = date.today() - timedelta(days=period_days)
        period_end = date.today()

        runs_qs = ExtractionRun.objects.filter(
            created_at__date__gte=period_start,
            status="COMPLETED",
        )
        if country_code:
            runs_qs = runs_qs.filter(country_code=country_code)

        # Group by document's vendor
        vendor_stats = (
            runs_qs
            .values("document__document_upload__invoice__raw_vendor_name")
            .annotate(
                run_count=Count("id"),
                avg_confidence=Avg("overall_confidence"),
            )
            .order_by("-run_count")[:30]
        )

        # Rejection stats
        rejected_runs = ExtractionRun.objects.filter(
            created_at__date__gte=period_start,
            approval__action="REJECTED",
        )
        if country_code:
            rejected_runs = rejected_runs.filter(country_code=country_code)

        data = {
            "vendor_stats": list(vendor_stats),
            "rejected_count": rejected_runs.count(),
        }

        snapshot = ExtractionAnalyticsSnapshot.objects.create(
            snapshot_type="vendor_pattern",
            country_code=country_code,
            period_start=period_start,
            period_end=period_end,
            data_json=data,
            run_count=runs_qs.count(),
            created_by=user,
        )

        ExtractionAuditService.log_analytics_snapshot_created(
            snapshot_id=snapshot.pk,
            snapshot_type="vendor_pattern",
            user=user,
            country_code=country_code,
        )

        return snapshot
