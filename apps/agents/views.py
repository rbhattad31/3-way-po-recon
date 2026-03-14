"""Agent API viewsets."""
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from apps.core.permissions import IsAdminOrReadOnly
from apps.agents.models import AgentDefinition, AgentRun
from apps.agents.serializers import (
    AgentDefinitionSerializer,
    AgentRunDetailSerializer,
    AgentRunListSerializer,
)


class AgentDefinitionViewSet(viewsets.ModelViewSet):
    queryset = AgentDefinition.objects.all()
    serializer_class = AgentDefinitionSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["agent_type", "enabled"]


class AgentRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        AgentRun.objects.select_related(
            "agent_definition", "reconciliation_result",
            "reconciliation_result__invoice",
        )
        .prefetch_related("steps", "tool_calls", "decisions", "recommendations", "escalations")
        .order_by("-created_at")
    )
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["agent_type", "status", "reconciliation_result"]
    ordering_fields = ["created_at", "confidence", "total_tokens"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        from apps.core.enums import UserRole
        user = self.request.user
        user_role = getattr(user, "role", None)
        if user_role == UserRole.AP_PROCESSOR:
            from apps.reconciliation.models import ReconciliationConfig
            config = ReconciliationConfig.objects.filter(is_default=True).first()
            if not (config and config.ap_processor_sees_all_cases):
                return qs.filter(
                    reconciliation_result__invoice__document_upload__uploaded_by=user
                )
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return AgentRunListSerializer
        return AgentRunDetailSerializer

    @action(detail=False, methods=["post"], url_path="trigger")
    def trigger_pipeline(self, request):
        """Trigger the agentic pipeline for a reconciliation result."""
        from apps.agents.tasks import run_agent_pipeline_task

        result_id = request.data.get("reconciliation_result_id")
        if not result_id:
            return Response(
                {"error": "reconciliation_result_id is required"}, status=400
            )
        from apps.core.utils import dispatch_task
        result = dispatch_task(run_agent_pipeline_task, int(result_id))
        task_id = getattr(result, 'id', None)
        return Response(
            {"task_id": task_id, "reconciliation_result_id": result_id},
            status=202,
        )
