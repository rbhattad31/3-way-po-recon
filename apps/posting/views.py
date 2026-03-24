"""DRF views for posting app."""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posting.models import InvoicePosting
from apps.posting.serializers import (
    InvoicePostingDetailSerializer,
    InvoicePostingListSerializer,
    PostingApproveRequestSerializer,
    PostingPrepareRequestSerializer,
    PostingRejectRequestSerializer,
)
from apps.posting.services.posting_action_service import PostingActionService
from apps.posting.tasks import prepare_posting_task


class InvoicePostingViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only ViewSet for InvoicePosting records with approve/reject/submit/retry actions."""

    queryset = InvoicePosting.objects.select_related(
        "invoice", "reviewed_by",
    ).order_by("-created_at")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return InvoicePostingListSerializer
        return InvoicePostingDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        s = self.request.query_params.get("status")
        if s:
            qs = qs.filter(status=s)
        queue = self.request.query_params.get("review_queue")
        if queue:
            qs = qs.filter(review_queue=queue)
        return qs

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        ser = PostingApproveRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            posting = PostingActionService.approve_posting(
                posting_id=int(pk),
                user=request.user,
                corrections=ser.validated_data.get("corrections"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(InvoicePostingDetailSerializer(posting).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        ser = PostingRejectRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            posting = PostingActionService.reject_posting(
                posting_id=int(pk),
                user=request.user,
                reason=ser.validated_data.get("reason", ""),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(InvoicePostingDetailSerializer(posting).data)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        try:
            posting = PostingActionService.submit_posting(
                posting_id=int(pk),
                user=request.user,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(InvoicePostingDetailSerializer(posting).data)

    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        try:
            posting = PostingActionService.retry_posting(
                posting_id=int(pk),
                user=request.user,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(InvoicePostingDetailSerializer(posting).data)


class PostingPrepareView(APIView):
    """Trigger posting preparation for an invoice."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PostingPrepareRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        invoice_id = ser.validated_data["invoice_id"]
        trigger = ser.validated_data.get("trigger", "manual")

        prepare_posting_task.delay(
            invoice_id=invoice_id,
            user_id=request.user.pk,
            trigger=trigger,
        )
        return Response(
            {"message": "Posting preparation enqueued", "invoice_id": invoice_id},
            status=status.HTTP_202_ACCEPTED,
        )
