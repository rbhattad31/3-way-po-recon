from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.documents.views import (
    DocumentUploadViewSet,
    GRNViewSet,
    InvoiceViewSet,
    PurchaseOrderViewSet,
)

app_name = "documents_api"

router = DefaultRouter()
router.register("uploads", DocumentUploadViewSet, basename="upload")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("purchase-orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("grns", GRNViewSet, basename="grn")

urlpatterns = [
    path("", include(router.urls)),
]
