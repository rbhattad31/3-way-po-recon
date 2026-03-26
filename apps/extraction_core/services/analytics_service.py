"""Service for analytics dashboard and data aggregation."""
from __future__ import annotations

from django.db.models import Avg, Count, Q, F
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.extraction_core.models import (
    ExtractionAnalyticsSnapshot,
    ExtractionApprovalRecord,
    ExtractionCorrection,
    ExtractionRun,
)


class AnalyticsService:
    """Analytics aggregation for the control center dashboard."""

    @classmethod
    def get_overview_stats(cls) -> dict:
        """Compute top-level KPIs."""
        total_runs = ExtractionRun.objects.count()
        completed = ExtractionRun.objects.filter(status="COMPLETED").count()
        failed = ExtractionRun.objects.filter(status="FAILED").count()
        pending_review = ExtractionRun.objects.filter(requires_review=True).count()

        # Touchless rate: completed without review / total completed
        auto_approved = ExtractionApprovalRecord.objects.filter(
            action__in=["APPROVED", "AUTO_APPROVED"]
        ).count()
        total_approved = ExtractionApprovalRecord.objects.exclude(action="").count()
        touchless_rate = (auto_approved / total_approved * 100) if total_approved else 0

        # Correction rate
        corrected_runs = ExtractionCorrection.objects.values("extraction_run").distinct().count()
        correction_rate = (corrected_runs / completed * 100) if completed else 0

        avg_confidence = ExtractionRun.objects.filter(
            overall_confidence__isnull=False
        ).aggregate(avg=Avg("overall_confidence"))["avg"] or 0

        return {
            "total_runs": total_runs,
            "completed": completed,
            "failed": failed,
            "pending_review": pending_review,
            "touchless_rate": round(touchless_rate, 1),
            "correction_rate": round(correction_rate, 1),
            "auto_approved": auto_approved,
            "avg_confidence": round(avg_confidence * 100, 1),
        }

    @classmethod
    def get_top_corrected_fields(cls, limit: int = 10) -> list[dict]:
        return list(
            ExtractionCorrection.objects.values("field_code")
            .annotate(count=Count("id"))
            .order_by("-count")[:limit]
        )

    @classmethod
    def get_confidence_by_country(cls) -> list[dict]:
        return list(
            ExtractionRun.objects.filter(overall_confidence__isnull=False)
            .values("country_code")
            .annotate(
                avg_confidence=Avg("overall_confidence"),
                run_count=Count("id"),
            )
            .order_by("-run_count")
        )

    @classmethod
    def get_queue_distribution(cls) -> list[dict]:
        return list(
            ExtractionRun.objects.filter(requires_review=True)
            .values("review_queue")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

    @classmethod
    def get_snapshot_history(cls, limit: int = 20) -> list:
        return list(
            ExtractionAnalyticsSnapshot.objects.order_by("-created_at")[:limit]
        )

    @classmethod
    def get_runs_today_count(cls) -> int:
        today = timezone.now().date()
        return ExtractionRun.objects.filter(created_at__date=today).count()


class CorrectionsExplorerService:
    """Service for browsing corrections with filters."""

    @classmethod
    def list_corrections(cls, filters: dict | None = None) -> list:
        qs = ExtractionCorrection.objects.select_related(
            "extraction_run", "corrected_by"
        ).order_by("-created_at")

        if not filters:
            return list(qs[:200])
        if filters.get("field_code"):
            qs = qs.filter(field_code__icontains=filters["field_code"])
        if filters.get("country_code"):
            qs = qs.filter(extraction_run__country_code__iexact=filters["country_code"])
        if filters.get("regime_code"):
            qs = qs.filter(extraction_run__regime_code__iexact=filters["regime_code"])
        if filters.get("search"):
            qs = qs.filter(
                Q(field_code__icontains=filters["search"])
                | Q(original_value__icontains=filters["search"])
                | Q(corrected_value__icontains=filters["search"])
            )
        return list(qs[:200])
