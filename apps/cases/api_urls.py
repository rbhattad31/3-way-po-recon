"""API URL configuration for the cases app."""

from rest_framework.routers import DefaultRouter

from apps.cases.api.views import APCaseViewSet, ReviewAssignmentViewSet

router = DefaultRouter()
router.register("", APCaseViewSet, basename="case")

# Review API URLs (served at /api/v1/reviews/ via core/api_urls.py)
review_router = DefaultRouter()
review_router.register("", ReviewAssignmentViewSet, basename="review")

urlpatterns = router.urls
