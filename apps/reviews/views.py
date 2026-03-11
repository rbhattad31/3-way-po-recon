"""Review workflow API viewsets."""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from apps.accounts.models import User
from apps.core.permissions import IsAdminOrReadOnly
from apps.reviews.models import ReviewAssignment
from apps.reviews.serializers import (
    ReviewAssignmentDetailSerializer,
    ReviewAssignmentListSerializer,
    ReviewAssignSerializer,
    ReviewCommentWriteSerializer,
    ReviewDecisionWriteSerializer,
)
from apps.reviews.services import ReviewWorkflowService


class ReviewAssignmentViewSet(viewsets.ModelViewSet):
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
