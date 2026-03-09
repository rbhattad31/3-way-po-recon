from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.reconciliation.views import (
    ReconciliationConfigViewSet,
    ReconciliationResultViewSet,
    ReconciliationRunViewSet,
)

app_name = "reconciliation_api"

router = DefaultRouter()
router.register("configs", ReconciliationConfigViewSet, basename="config")
router.register("runs", ReconciliationRunViewSet, basename="run")
router.register("results", ReconciliationResultViewSet, basename="result")

urlpatterns = [
    path("", include(router.urls)),
]
