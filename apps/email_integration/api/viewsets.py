"""DRF viewsets for email integration entities."""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.tenant_utils import TenantQuerysetMixin
from apps.email_integration.api.permissions import (
    CanManageEmailIntegration,
    CanManageMailboxes,
    CanReadEmailThread,
    CanRouteEmail,
    CanTriageEmail,
    CanViewEmailIntegration,
)
from apps.email_integration.api.serializers import (
    EmailActionSerializer,
    EmailMessageDetailSerializer,
    EmailMessageListSerializer,
    EmailRoutingDecisionSerializer,
    EmailTemplateSerializer,
    EmailThreadSerializer,
    MailboxConfigSerializer,
)
from apps.email_integration.models import (
    EmailAction,
    EmailMessage,
    EmailRoutingDecision,
    EmailTemplate,
    EmailThread,
    MailboxConfig,
)
from apps.email_integration.services.mailbox_service import MailboxService
from apps.email_integration.services.processing_service import EmailProcessingService
from apps.email_integration.tasks import poll_mailboxes_task, relink_email_threads_task, retry_failed_email_actions_task, sync_mailbox_task


class MailboxConfigViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = MailboxConfig.objects.all().order_by("name")
    serializer_class = MailboxConfigSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanViewEmailIntegration()]
        return [CanManageMailboxes()]

    @action(detail=False, methods=["post"], url_path="trigger-poll")
    def trigger_poll(self, request):
        tenant = getattr(request, "tenant", None)
        poll_mailboxes_task.delay(tenant_id=getattr(tenant, "pk", None))
        return Response({"queued": True})

    @action(detail=True, methods=["post"], url_path="sync")
    def sync_mailbox(self, request, pk=None):
        mailbox = self.get_object()
        sync_mailbox_task.delay(mailbox_id=mailbox.pk, tenant_id=getattr(mailbox.tenant, "pk", None))
        return Response({"queued": True, "mailbox_id": mailbox.pk})

    @action(detail=True, methods=["post"], url_path="test")
    def test_mailbox(self, request, pk=None):
        mailbox = self.get_object()
        result = MailboxService.test_mailbox(mailbox)
        return Response(result)


class EmailThreadViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmailThread.objects.select_related("mailbox").all().order_by("-last_message_at")
    serializer_class = EmailThreadSerializer
    permission_classes = [CanReadEmailThread]

    @action(detail=False, methods=["post"], permission_classes=[CanTriageEmail], url_path="relink")
    def relink(self, request):
        tenant = getattr(request, "tenant", None)
        relink_email_threads_task.delay(tenant_id=getattr(tenant, "pk", None))
        return Response({"queued": True})


class EmailMessageViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmailMessage.objects.select_related("mailbox", "thread").prefetch_related("attachments").all().order_by("-received_at")
    permission_classes = [CanViewEmailIntegration]

    def get_serializer_class(self):
        if self.action == "list":
            return EmailMessageListSerializer
        return EmailMessageDetailSerializer

    @action(detail=True, methods=["post"], permission_classes=[CanRouteEmail], url_path="reprocess")
    def reprocess(self, request, pk=None):
        message = self.get_object()
        result = EmailProcessingService.process_message(message, actor_user=request.user)
        return Response(result)

    @action(detail=True, methods=["post"], permission_classes=[CanRouteEmail], url_path="route")
    def route_message(self, request, pk=None):
        message = self.get_object()
        target_domain = (request.data.get("target_domain") or "").strip() or None
        result = EmailProcessingService.process_message(message, actor_user=request.user, target_domain=target_domain)
        return Response(result)

    @action(detail=True, methods=["post"], permission_classes=[CanTriageEmail], url_path="link-entity")
    def link_entity(self, request, pk=None):
        message = self.get_object()
        entity_type = (request.data.get("entity_type") or "").strip()
        entity_id = request.data.get("entity_id")
        if not entity_type or not entity_id:
            return Response({"detail": "entity_type and entity_id are required."}, status=status.HTTP_400_BAD_REQUEST)
        result = EmailProcessingService.link_message_to_entity(
            message,
            entity_type=entity_type,
            entity_id=int(entity_id),
            actor_user=request.user,
        )
        return Response(result)


class EmailRoutingDecisionViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmailRoutingDecision.objects.select_related("email_message").all().order_by("-created_at")
    serializer_class = EmailRoutingDecisionSerializer
    permission_classes = [CanViewEmailIntegration]


class EmailActionViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmailAction.objects.select_related("email_message", "thread", "performed_by_user").all().order_by("-created_at")
    serializer_class = EmailActionSerializer
    permission_classes = [CanViewEmailIntegration]

    @action(detail=False, methods=["post"], permission_classes=[CanTriageEmail], url_path="retry-failed")
    def retry_failed(self, request):
        tenant = getattr(request, "tenant", None)
        retry_failed_email_actions_task.delay(tenant_id=getattr(tenant, "pk", None))
        return Response({"queued": True})


class EmailTemplateViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = EmailTemplate.objects.all().order_by("template_code")
    serializer_class = EmailTemplateSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanViewEmailIntegration()]
        return [CanManageEmailIntegration()]
