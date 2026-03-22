"""API URL routes for extraction_configs."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.extraction_configs.views import (
    NormalizationProfileViewSet,
    TaxFieldDefinitionViewSet,
)

router = DefaultRouter()
router.register("fields", TaxFieldDefinitionViewSet, basename="field-definition")
router.register("normalization-profiles", NormalizationProfileViewSet, basename="normalization-profile")

urlpatterns = [
    path("", include(router.urls)),
]
