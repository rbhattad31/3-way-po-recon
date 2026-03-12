"""Reconciliation API viewsets."""
from django.db.models import Count
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import IsAdminOrReadOnly
from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationPolicy,
    ReconciliationResult,
    ReconciliationRun,
)
from apps.reconciliation.serializers import (
    ReconciliationConfigSerializer,
    ReconciliationPolicySerializer,
    ReconciliationResultDetailSerializer,
    ReconciliationResultListSerializer,
    ReconciliationRunDetailSerializer,
    ReconciliationRunListSerializer,
)


class ReconciliationConfigViewSet(viewsets.ModelViewSet):
    queryset = ReconciliationConfig.objects.all()
    serializer_class = ReconciliationConfigSerializer
    permission_classes = [IsAdminOrReadOnly]


class ReconciliationRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReconciliationRun.objects.select_related("triggered_by").order_by("-created_at")
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status", "reconciliation_mode"]
    ordering_fields = ["created_at", "started_at", "completed_at"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return ReconciliationRunListSerializer
        return ReconciliationRunDetailSerializer

    @action(detail=False, methods=["post"], url_path="trigger")
    def trigger_run(self, request):
        """Trigger a new reconciliation run via Celery."""
        from apps.reconciliation.tasks import run_reconciliation_task

        invoice_id = request.data.get("invoice_id")
        if not invoice_id:
            return Response(
                {"error": "invoice_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        task = run_reconciliation_task.delay(int(invoice_id))
        return Response(
            {"task_id": task.id, "invoice_id": invoice_id},
            status=status.HTTP_202_ACCEPTED,
        )


class ReconciliationResultViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        ReconciliationResult.objects.select_related(
            "invoice", "invoice__vendor", "purchase_order", "run",
        )
        .annotate(exception_count=Count("exceptions"))
        .order_by("-created_at")
    )
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        "match_status", "requires_review", "run",
        "reconciliation_mode", "is_two_way_result", "is_three_way_result",
    ]
    search_fields = ["invoice__invoice_number", "purchase_order__po_number"]
    ordering_fields = ["created_at", "match_status", "deterministic_confidence"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return ReconciliationResultListSerializer
        return ReconciliationResultDetailSerializer


class ReconciliationPolicyViewSet(viewsets.ModelViewSet):
    queryset = (
        ReconciliationPolicy.objects
        .select_related("vendor")
        .order_by("priority", "policy_code")
    )
    serializer_class = ReconciliationPolicySerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["reconciliation_mode", "is_active", "vendor"]
    search_fields = ["policy_code", "policy_name"]
    ordering_fields = ["priority", "policy_code", "created_at"]
    ordering = ["priority"]
