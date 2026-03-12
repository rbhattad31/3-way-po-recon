"""API URL configuration for the cases app."""

from rest_framework.routers import DefaultRouter

from apps.cases.api.views import APCaseViewSet

router = DefaultRouter()
router.register("", APCaseViewSet, basename="case")

urlpatterns = router.urls
