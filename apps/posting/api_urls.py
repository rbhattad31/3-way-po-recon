"""API URL routes for posting app."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.posting.views import InvoicePostingViewSet, PostingPrepareView

router = DefaultRouter()
router.register("postings", InvoicePostingViewSet, basename="posting")

urlpatterns = [
    path("prepare/", PostingPrepareView.as_view(), name="posting-prepare"),
    path("", include(router.urls)),
]
