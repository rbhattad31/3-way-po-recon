"""API URL routing for procurement app — mounted at /api/v1/procurement/."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.procurement.views import ProcurementRequestViewSet, SupplierQuotationViewSet

router = DefaultRouter()
router.register(r"requests", ProcurementRequestViewSet, basename="procurement-request")
router.register(r"quotations", SupplierQuotationViewSet, basename="procurement-quotation")

urlpatterns = [
    path("", include(router.urls)),
]
