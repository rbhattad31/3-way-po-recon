from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.vendors.views import VendorViewSet

app_name = "vendors_api"

router = DefaultRouter()
router.register("", VendorViewSet, basename="vendor")

urlpatterns = [
    path("", include(router.urls)),
]
