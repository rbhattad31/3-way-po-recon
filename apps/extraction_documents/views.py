"""DRF views for extraction_documents."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.extraction_documents.models import ExtractionDocument
from apps.extraction_documents.serializers import (
    ExtractionDocumentListSerializer,
    ExtractionDocumentSerializer,
)


class ExtractionDocumentViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only viewset for extraction documents and their field results."""

    queryset = (
        ExtractionDocument.objects
        .select_related("resolved_jurisdiction", "resolved_schema")
        .prefetch_related("field_results")
        .all()
    )
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return ExtractionDocumentListSerializer
        return ExtractionDocumentSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status__iexact=status_filter)
        jurisdiction_id = self.request.query_params.get("jurisdiction")
        if jurisdiction_id:
            qs = qs.filter(resolved_jurisdiction_id=jurisdiction_id)
        is_valid = self.request.query_params.get("is_valid")
        if is_valid is not None:
            qs = qs.filter(is_valid=is_valid.lower() in ("true", "1"))
        return qs
