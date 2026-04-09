"""DRF viewsets and custom actions for the cases app."""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from apps.accounts.models import User
from apps.cases.api.permissions import CanAssignCase, CanEditCase, CanUseCopilot, CanViewCase
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.cases.api.serializers import (
    APCaseArtifactSerializer,
    APCaseCommentSerializer,
    APCaseDecisionSerializer,
    APCaseDetailSerializer,
    APCaseListSerializer,
    APCaseStageSerializer,
    APCaseSummarySerializer,
    AssignCaseSerializer,
    CopilotChatInputSerializer,
    ReroutePathSerializer,
    RunStageSerializer,
    ReviewAssignmentDetailSerializer,
    ReviewAssignmentListSerializer,
    ReviewAssignSerializer,
    ReviewCommentWriteSerializer,
    ReviewDecisionWriteSerializer,
)
from apps.cases.models import APCase, ReviewAssignment
from apps.cases.services.review_workflow_service import ReviewWorkflowService
from apps.core.permissions import IsAdminOrReadOnly
from apps.core.tenant_utils import TenantQuerysetMixin, require_tenant


class APCaseViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    """
    API viewset for AP Cases.

    list:   GET /api/v1/cases/
    detail: GET /api/v1/cases/{id}/
    """

    queryset = APCase.objects.all()
    permission_classes = [IsAuthenticated, CanViewCase]
    filterset_fields = ["processing_path", "status", "priority", "assigned_to"]
    search_fields = ["case_number", "invoice__invoice_number", "vendor__name"]
    ordering_fields = ["created_at", "priority", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()  # mixin handles tenant filter
        qs = CaseSelectors.inbox(
            processing_path=self.request.query_params.get("processing_path", ""),
            status=self.request.query_params.get("status", ""),
            priority=self.request.query_params.get("priority", ""),
            search=self.request.query_params.get("search", ""),
        )
        # Re-apply tenant filter after inbox rebuilds the queryset
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None and not self.request.user.is_superuser:
            qs = qs.filter(tenant=tenant)
        return CaseSelectors.scope_for_user(qs, self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return APCaseListSerializer
        return APCaseDetailSerializer

    # --- Custom actions ---

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated, CanViewCase])
    def timeline(self, request, pk=None):
        """GET /api/v1/cases/{id}/timeline/"""
        case = self.get_object()
        from apps.auditlog.timeline_service import CaseTimelineService

        events = CaseTimelineService.get_case_timeline(case.invoice_id, tenant=getattr(request, 'tenant', None))
        return Response({"events": events})

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated, CanViewCase])
    def artifacts(self, request, pk=None):
        """GET /api/v1/cases/{id}/artifacts/"""
        case = self.get_object()
        artifacts = case.artifacts.all()
        serializer = APCaseArtifactSerializer(artifacts, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated, CanViewCase])
    def decisions(self, request, pk=None):
        """GET /api/v1/cases/{id}/decisions/"""
        case = self.get_object()
        decisions = case.decisions.all()
        serializer = APCaseDecisionSerializer(decisions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated, CanViewCase])
    def stages(self, request, pk=None):
        """GET /api/v1/cases/{id}/stages/"""
        case = self.get_object()
        stages = case.stages.all()
        serializer = APCaseStageSerializer(stages, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated, CanViewCase])
    def summary(self, request, pk=None):
        """GET /api/v1/cases/{id}/summary/"""
        case = self.get_object()
        if hasattr(case, "summary") and case.summary:
            serializer = APCaseSummarySerializer(case.summary)
            return Response(serializer.data)
        return Response({"detail": "No summary available"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, CanAssignCase])
    def assign(self, request, pk=None):
        """POST /api/v1/cases/{id}/assign/"""
        case = self.get_object()
        serializer = AssignCaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.cases.services.case_assignment_service import CaseAssignmentService
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = None
        if serializer.validated_data.get("user_id"):
            user = User.objects.get(id=serializer.validated_data["user_id"])

        assignment = CaseAssignmentService.assign_for_review(
            case, user=user,
            role=serializer.validated_data.get("role"),
            queue=serializer.validated_data.get("queue"),
        )
        return Response({"assignment_id": assignment.id}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="run-stage", permission_classes=[IsAuthenticated, CanEditCase])
    def run_stage(self, request, pk=None):
        """POST /api/v1/cases/{id}/run-stage/"""
        case = self.get_object()
        serializer = RunStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

        orchestrator = CaseOrchestrator(case)
        orchestrator.run_from(serializer.validated_data["stage"])
        case.refresh_from_db()
        return Response(APCaseDetailSerializer(case).data)

    @action(detail=True, methods=["post"], url_path="reroute-path", permission_classes=[IsAuthenticated, CanEditCase])
    def reroute_path(self, request, pk=None):
        """POST /api/v1/cases/{id}/reroute-path/"""
        case = self.get_object()
        serializer = ReroutePathSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.cases.services.case_routing_service import CaseRoutingService

        CaseRoutingService.reroute_path(
            case,
            serializer.validated_data["new_path"],
            serializer.validated_data["reason"],
        )
        case.refresh_from_db()
        return Response(APCaseDetailSerializer(case).data)

    @action(detail=True, methods=["post"], url_path="copilot-chat", permission_classes=[IsAuthenticated, CanUseCopilot])
    def copilot_chat(self, request, pk=None):
        """POST /api/v1/cases/{id}/copilot-chat/"""
        case = self.get_object()
        serializer = CopilotChatInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # TODO: Invoke Reviewer Copilot Agent
        # For now, return a placeholder response
        return Response({
            "answer": "Copilot agent integration pending.",
            "evidence_refs": [],
            "suggested_actions": [],
        })

    @action(detail=True, methods=["get", "post"], url_path="comments", permission_classes=[IsAuthenticated, CanViewCase])
    def comments(self, request, pk=None):
        """GET/POST /api/v1/cases/{id}/comments/"""
        case = self.get_object()
        if request.method == "GET":
            serializer = APCaseCommentSerializer(case.comments.all(), many=True)
            return Response(serializer.data)

        serializer = APCaseCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(case=case, author=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, CanViewCase])
    def stats(self, request):
        """GET /api/v1/cases/stats/"""
        return Response(CaseSelectors.stats())


# ---------------------------------------------------------------------------
# Review viewset (merged from apps.reviews)
# ---------------------------------------------------------------------------

class ReviewAssignmentViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = (
        ReviewAssignment.objects.select_related(
            "reconciliation_result",
            "reconciliation_result__invoice",
            "assigned_to",
        )
        .prefetch_related("comments", "actions")
        .order_by("priority", "-created_at")
    )
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status", "assigned_to", "priority"]
    ordering_fields = ["priority", "created_at", "due_date"]
    ordering = ["priority", "-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return ReviewAssignmentListSerializer
        return ReviewAssignmentDetailSerializer

    # POST /reviews/{id}/assign/
    @action(detail=True, methods=["post"], url_path="assign")
    def assign_reviewer(self, request, pk=None):
        assignment = self.get_object()
        ser = ReviewAssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = User.objects.get(pk=ser.validated_data["assigned_to"])
        ReviewWorkflowService.assign_reviewer(assignment, user)
        return Response(ReviewAssignmentDetailSerializer(assignment).data)

    # POST /reviews/{id}/start/
    @action(detail=True, methods=["post"], url_path="start")
    def start_review(self, request, pk=None):
        assignment = self.get_object()
        ReviewWorkflowService.start_review(assignment)
        return Response(ReviewAssignmentDetailSerializer(assignment).data)

    # POST /reviews/{id}/decide/
    @action(detail=True, methods=["post"], url_path="decide")
    def decide(self, request, pk=None):
        assignment = self.get_object()
        ser = ReviewDecisionWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        decision_map = {
            "APPROVED": ReviewWorkflowService.approve,
            "REJECTED": ReviewWorkflowService.reject,
            "REPROCESSED": ReviewWorkflowService.request_reprocess,
        }
        handler = decision_map[ser.validated_data["decision"]]
        handler(assignment, request.user, ser.validated_data.get("reason", ""))
        assignment.refresh_from_db()
        return Response(ReviewAssignmentDetailSerializer(assignment).data)

    # POST /reviews/{id}/comment/
    @action(detail=True, methods=["post"], url_path="comment")
    def add_comment(self, request, pk=None):
        assignment = self.get_object()
        ser = ReviewCommentWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ReviewWorkflowService.add_comment(
            assignment, request.user,
            ser.validated_data["body"],
            ser.validated_data.get("is_internal", True),
        )
        assignment.refresh_from_db()
        return Response(ReviewAssignmentDetailSerializer(assignment).data)
