"""Service for the control center overview dashboard."""
from __future__ import annotations

from django.utils import timezone

from apps.extraction_core.models import (
    CountryPack,
    ExtractionPromptTemplate,
    ExtractionRun,
    ExtractionSchemaDefinition,
    TaxJurisdictionProfile,
)
from apps.extraction_core.services.analytics_service import AnalyticsService
from apps.extraction_core.services.extraction_governance_service import ExtractionGovernanceService


class OverviewService:
    """Aggregates data for the control center overview dashboard."""

    @classmethod
    def get_dashboard_data(cls) -> dict:
        today = timezone.now().date()

        active_jurisdictions = TaxJurisdictionProfile.objects.filter(is_active=True).count()
        active_country_packs = CountryPack.objects.filter(pack_status="ACTIVE").count()
        active_schemas = ExtractionSchemaDefinition.objects.filter(is_active=True).count()
        active_prompts = ExtractionPromptTemplate.objects.filter(status="ACTIVE").count()
        runs_today = ExtractionRun.objects.filter(created_at__date=today).count()

        stats = AnalyticsService.get_overview_stats()
        top_corrected = AnalyticsService.get_top_corrected_fields(limit=5)
        confidence_by_country = AnalyticsService.get_confidence_by_country()
        queue_dist = AnalyticsService.get_queue_distribution()
        recent_changes = list(ExtractionGovernanceService.get_recent_governance_changes(limit=8))

        return {
            "active_jurisdictions": active_jurisdictions,
            "active_country_packs": active_country_packs,
            "active_schemas": active_schemas,
            "active_prompts": active_prompts,
            "runs_today": runs_today,
            "pending_review": stats.get("pending_review", 0),
            "touchless_rate": stats.get("touchless_rate", 0),
            "avg_confidence": stats.get("avg_confidence", 0),
            "correction_rate": stats.get("correction_rate", 0),
            "top_corrected": top_corrected,
            "confidence_by_country": confidence_by_country,
            "queue_distribution": queue_dist,
            "recent_changes": recent_changes,
        }
