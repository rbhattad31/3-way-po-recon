"""API URL routing for procurement app — mounted at /api/v1/procurement/."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.procurement.views import (
    AnalysisRunValidationView,
    ProcurementRequestViewSet,
    SupplierQuotationViewSet,
    ValidationRuleSetViewSet,
    RoomViewSet,
    ProductViewSet,
    VendorViewSet,
    VendorProductViewSet,
    PurchaseHistoryViewSet,
    RecommendationViewSet,
)

router = DefaultRouter()
router.register(r"requests", ProcurementRequestViewSet, basename="procurement-request")
router.register(r"quotations", SupplierQuotationViewSet, basename="procurement-quotation")
router.register(r"validation/rulesets", ValidationRuleSetViewSet, basename="procurement-validation-ruleset")

# RoomWise Pre-Procurement Recommender routes
router.register(r"roomwise/rooms", RoomViewSet, basename="roomwise-room")
router.register(r"roomwise/products", ProductViewSet, basename="roomwise-product")
router.register(r"roomwise/vendors", VendorViewSet, basename="roomwise-vendor")
router.register(r"roomwise/vendor-products", VendorProductViewSet, basename="roomwise-vendor-product")
router.register(r"roomwise/purchase-history", PurchaseHistoryViewSet, basename="roomwise-purchase-history")
router.register(r"roomwise/recommendations", RecommendationViewSet, basename="roomwise-recommendation")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "runs/<int:pk>/validation/",
        AnalysisRunValidationView.as_view({"get": "retrieve"}),
        name="procurement-run-validation",
    ),
]
