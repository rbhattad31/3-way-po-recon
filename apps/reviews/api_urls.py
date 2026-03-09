from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.reviews.views import ReviewAssignmentViewSet

app_name = "reviews_api"

router = DefaultRouter()
router.register("", ReviewAssignmentViewSet, basename="review")

urlpatterns = [
    path("", include(router.urls)),
]
