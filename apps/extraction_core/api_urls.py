"""API URL routes for extraction_core."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.extraction_core.views import (
    EntityExtractionProfileViewSet,
    ExtractionRuntimeSettingsViewSet,
    ExtractionSchemaDefinitionViewSet,
    ExtractionView,
    JurisdictionResolutionView,
    JurisdictionResolveView,
    SchemaLookupView,
    TaxJurisdictionProfileViewSet,
)

router = DefaultRouter()
router.register("jurisdictions", TaxJurisdictionProfileViewSet, basename="jurisdiction")
router.register("schemas", ExtractionSchemaDefinitionViewSet, basename="schema")
router.register("runtime-settings", ExtractionRuntimeSettingsViewSet, basename="runtime-settings")
router.register("entity-profiles", EntityExtractionProfileViewSet, basename="entity-profile")

urlpatterns = [
    path("resolve-jurisdiction/", JurisdictionResolveView.as_view(), name="resolve-jurisdiction"),
    path("resolve-jurisdiction-full/", JurisdictionResolutionView.as_view(), name="resolve-jurisdiction-full"),
    path("lookup-schema/", SchemaLookupView.as_view(), name="lookup-schema"),
    path("extract/", ExtractionView.as_view(), name="extract"),
    path("", include(router.urls)),
]
