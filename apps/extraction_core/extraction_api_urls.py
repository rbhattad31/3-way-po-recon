"""API URL routes for the enhanced extraction pipeline."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.extraction_core.extraction_views import (
    CountryPackViewSet,
    ExtractionAnalyticsViewSet,
    ExtractionRunViewSet,
    RunPipelineView,
)

router = DefaultRouter()
router.register("runs", ExtractionRunViewSet, basename="extraction-run")
router.register("analytics", ExtractionAnalyticsViewSet, basename="extraction-analytics")
router.register("country-packs", CountryPackViewSet, basename="country-pack")

urlpatterns = [
    path("run/", RunPipelineView.as_view(), name="run-pipeline"),
    path("", include(router.urls)),
]
