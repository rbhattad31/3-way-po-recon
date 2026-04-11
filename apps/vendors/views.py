"""Vendor API viewsets."""
from rest_framework import viewsets
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import IsAdminOrReadOnly
from apps.core.tenant_utils import TenantQuerysetMixin
from apps.vendors.models import Vendor
from apps.vendors.serializers import VendorDetailSerializer, VendorListSerializer


class VendorViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = Vendor.objects.filter(is_active=True)
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["country", "currency"]
    search_fields = ["name", "code", "contact_email"]
    ordering_fields = ["name", "code", "created_at"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return VendorListSerializer
        return VendorDetailSerializer

    def perform_create(self, serializer):
        serializer.save(tenant=getattr(self.request, 'tenant', None))
