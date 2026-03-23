"""API URL routes for extraction_documents."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.extraction_documents.views import ExtractionDocumentViewSet

router = DefaultRouter()
router.register("documents", ExtractionDocumentViewSet, basename="extraction-document")

urlpatterns = [
    path("", include(router.urls)),
]
