"""API URL routes for posting_core."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.posting_core.views import (
    ERPCostCenterReferenceViewSet,
    ERPItemReferenceViewSet,
    ERPPOReferenceViewSet,
    ERPReferenceImportBatchViewSet,
    ERPReferenceUploadView,
    ERPTaxCodeReferenceViewSet,
    ERPVendorReferenceViewSet,
    ItemAliasMappingViewSet,
    PostingRuleViewSet,
    PostingRunViewSet,
    VendorAliasMappingViewSet,
)

router = DefaultRouter()
router.register("runs", PostingRunViewSet, basename="posting-run")
router.register("import-batches", ERPReferenceImportBatchViewSet, basename="import-batch")
router.register("vendors", ERPVendorReferenceViewSet, basename="erp-vendor")
router.register("items", ERPItemReferenceViewSet, basename="erp-item")
router.register("tax-codes", ERPTaxCodeReferenceViewSet, basename="erp-tax-code")
router.register("cost-centers", ERPCostCenterReferenceViewSet, basename="erp-cost-center")
router.register("po-refs", ERPPOReferenceViewSet, basename="erp-po-ref")
router.register("vendor-aliases", VendorAliasMappingViewSet, basename="vendor-alias")
router.register("item-aliases", ItemAliasMappingViewSet, basename="item-alias")
router.register("rules", PostingRuleViewSet, basename="posting-rule")

urlpatterns = [
    path("upload/", ERPReferenceUploadView.as_view(), name="erp-reference-upload"),
    path("", include(router.urls)),
]
