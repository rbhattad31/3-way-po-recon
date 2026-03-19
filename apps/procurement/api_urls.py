"""API URL routing for procurement app — mounted at /api/v1/procurement/."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.procurement.views import (
    AnalysisRunValidationView,
    ProcurementRequestViewSet,
    SupplierQuotationViewSet,
    ValidationRuleSetViewSet,
)

router = DefaultRouter()
router.register(r"requests", ProcurementRequestViewSet, basename="procurement-request")
router.register(r"quotations", SupplierQuotationViewSet, basename="procurement-quotation")
router.register(r"validation/rulesets", ValidationRuleSetViewSet, basename="procurement-validation-ruleset")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "runs/<int:pk>/validation/",
        AnalysisRunValidationView.as_view({"get": "retrieve"}),
        name="procurement-run-validation",
    ),
]
