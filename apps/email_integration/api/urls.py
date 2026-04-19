"""API routes for email integration viewsets."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.email_integration.api.viewsets import (
    EmailActionViewSet,
    EmailMessageViewSet,
    EmailRoutingDecisionViewSet,
    EmailTemplateViewSet,
    EmailThreadViewSet,
    MailboxConfigViewSet,
)

router = DefaultRouter()
router.register("mailboxes", MailboxConfigViewSet, basename="email-mailbox")
router.register("threads", EmailThreadViewSet, basename="email-thread")
router.register("messages", EmailMessageViewSet, basename="email-message")
router.register("routing-decisions", EmailRoutingDecisionViewSet, basename="email-routing-decision")
router.register("actions", EmailActionViewSet, basename="email-action")
router.register("templates", EmailTemplateViewSet, basename="email-template")

urlpatterns = [
    path("", include(router.urls)),
]
