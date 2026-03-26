"""
DRF views for the enhanced extraction pipeline.

All endpoints enforce RBAC via ``HasPermissionCode`` with the extraction
permission codes:
    extraction.view, extraction.run, extraction.correct,
    extraction.approve, extraction.reject, extraction.reprocess,
    extraction.escalate, extraction.audit.view, extraction.analytics.view
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasPermissionCode
from apps.extraction_core.extraction_serializers import (
    ApproveRejectRequestSerializer,
    CorrectFieldRequestSerializer,
    CountryPackSerializer,
    EscalateRequestSerializer,
    ExtractionAnalyticsSnapshotSerializer,
    ExtractionCorrectionSerializer,
    ExtractionEvidenceSerializer,
    ExtractionFieldValueSerializer,
    ExtractionIssueSerializer,
    ExtractionLineItemSerializer,
    ExtractionRunDetailSerializer,
    ExtractionRunListSerializer,
    RunPipelineRequestSerializer,
)
from apps.extraction_core.models import (
    CountryPack,
    ExtractionAnalyticsSnapshot,
    ExtractionApprovalRecord,
    ExtractionCorrection,
    ExtractionEvidence,
    ExtractionFieldValue,
    ExtractionIssue,
    ExtractionLineItem,
    ExtractionRun,
)


# ---------------------------------------------------------------------------
# ExtractionRun ViewSet — main CRUD + nested actions
# ---------------------------------------------------------------------------


class ExtractionRunViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for ExtractionRun with nested actions:

    - GET  /                     → list runs
    - GET  /{id}/                → run detail
    - GET  /{id}/summary/        → lightweight summary
    - GET  /{id}/fields/         → field values
    - GET  /{id}/line-items/     → line items
    - GET  /{id}/validation/     → issues
    - GET  /{id}/evidence/       → evidence records
    - GET  /{id}/corrections/    → correction audit trail
    - POST /{id}/correct-field/  → correct a field value
    - POST /{id}/approve/        → approve extraction
    - POST /{id}/reject/         → reject extraction
    - POST /{id}/reprocess/      → reprocess extraction
    - POST /{id}/escalate/       → escalate to different queue
    """

    queryset = ExtractionRun.objects.select_related(
        "document", "jurisdiction", "schema",
    ).order_by("-created_at")
    permission_classes = [HasPermissionCode]
    required_permission = "extraction.view"

    def get_serializer_class(self):
        if self.action == "list":
            return ExtractionRunListSerializer
        return ExtractionRunDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        country = self.request.query_params.get("country_code")
        if country:
            qs = qs.filter(country_code__iexact=country)
        st = self.request.query_params.get("status")
        if st:
            qs = qs.filter(status__iexact=st)
        queue = self.request.query_params.get("review_queue")
        if queue:
            qs = qs.filter(review_queue__iexact=queue)
        needs_review = self.request.query_params.get("requires_review")
        if needs_review is not None:
            qs = qs.filter(requires_review=needs_review.lower() in ("true", "1"))
        doc_id = self.request.query_params.get("document")
        if doc_id:
            qs = qs.filter(document_id=doc_id)
        return qs

    # --- GET summary ---

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        """Lightweight extraction run summary."""
        run = self.get_object()
        data = {
            "id": run.pk,
            "status": run.status,
            "country_code": run.country_code,
            "regime_code": run.regime_code,
            "jurisdiction_source": run.jurisdiction_source,
            "schema_code": run.schema_code,
            "schema_version": run.schema_version,
            "overall_confidence": run.overall_confidence,
            "extraction_method": run.extraction_method,
            "review_queue": run.review_queue,
            "requires_review": run.requires_review,
            "review_reasons": run.review_reasons_json or [],
            "field_count": run.field_count,
            "field_coverage_pct": run.field_coverage_pct,
            "mandatory_coverage_pct": run.mandatory_coverage_pct,
            "duration_ms": run.duration_ms,
            "has_approval": hasattr(run, "approval") and ExtractionApprovalRecord.objects.filter(extraction_run=run).exists(),
            "issue_count": run.issues.count(),
            "evidence_count": run.evidence_records.count(),
            "correction_count": run.corrections.count(),
        }
        return Response(data)

    # --- GET fields ---

    @action(detail=True, methods=["get"])
    def fields(self, request, pk=None):
        """Field values for an extraction run."""
        run = self.get_object()
        qs = run.field_values.all()
        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category__iexact=category)
        serializer = ExtractionFieldValueSerializer(qs, many=True)
        return Response(serializer.data)

    # --- GET line-items ---

    @action(detail=True, methods=["get"], url_path="line-items")
    def line_items(self, request, pk=None):
        """Line items for an extraction run."""
        run = self.get_object()
        serializer = ExtractionLineItemSerializer(
            run.line_items.all(), many=True,
        )
        return Response(serializer.data)

    # --- GET validation (issues) ---

    @action(detail=True, methods=["get"])
    def validation(self, request, pk=None):
        """Validation issues for an extraction run."""
        run = self.get_object()
        serializer = ExtractionIssueSerializer(
            run.issues.all(), many=True,
        )
        return Response(serializer.data)

    # --- GET evidence ---

    @action(detail=True, methods=["get"])
    def evidence(self, request, pk=None):
        """Evidence records for an extraction run."""
        run = self.get_object()
        serializer = ExtractionEvidenceSerializer(
            run.evidence_records.all(), many=True,
        )
        return Response(serializer.data)

    # --- GET corrections ---

    @action(detail=True, methods=["get"])
    def corrections(self, request, pk=None):
        """Correction audit trail for an extraction run."""
        run = self.get_object()
        serializer = ExtractionCorrectionSerializer(
            run.corrections.all(), many=True,
        )
        return Response(serializer.data)

    # --- POST correct-field ---

    @action(
        detail=True,
        methods=["post"],
        url_path="correct-field",
    )
    def correct_field(self, request, pk=None):
        """Correct a single field value."""
        self.required_permission = "extraction.correct"
        self.check_permissions(request)

        run = self.get_object()
        serializer = CorrectFieldRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        field_code = data["field_code"]
        corrected_value = data["corrected_value"]
        reason = data.get("correction_reason", "")

        # Find the field value
        fv = run.field_values.filter(
            field_code=field_code, line_item_index__isnull=True,
        ).first()
        if not fv:
            return Response(
                {"detail": f"Field '{field_code}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        original_value = fv.normalized_value or fv.value

        # Update field value
        fv.is_corrected = True
        fv.corrected_value = corrected_value
        fv.save(update_fields=["is_corrected", "corrected_value", "updated_at"])

        # Create correction record
        correction = ExtractionCorrection.objects.create(
            extraction_run=run,
            field_code=field_code,
            original_value=original_value,
            corrected_value=corrected_value,
            correction_reason=reason,
            corrected_by=request.user,
            created_by=request.user,
        )

        # Audit
        from apps.extraction_core.services.extraction_audit import (
            ExtractionAuditService,
        )
        ExtractionAuditService.log_field_corrected(
            extraction_run_id=run.pk,
            field_code=field_code,
            original_value=original_value,
            corrected_value=corrected_value,
            user=request.user,
        )

        return Response(
            ExtractionCorrectionSerializer(correction).data,
            status=status.HTTP_201_CREATED,
        )

    # --- POST approve ---

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Approve an extraction run.

        Delegates exclusively to GovernanceTrailService — the sole writer
        of ExtractionApprovalRecord.
        """
        self.required_permission = "extraction.approve"
        self.check_permissions(request)

        run = self.get_object()
        serializer = ApproveRejectRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.extraction_core.services.governance_trail import GovernanceTrailService
        record = GovernanceTrailService.record_approval_decision(
            run=run, action="APPROVE", user=request.user,
            comments=serializer.validated_data.get("comments", ""),
        )

        from apps.extraction_core.extraction_serializers import (
            ExtractionApprovalRecordSerializer,
        )
        return Response(
            ExtractionApprovalRecordSerializer(record).data,
            status=status.HTTP_200_OK,
        )

    # --- POST reject ---

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Reject an extraction run.

        Delegates exclusively to GovernanceTrailService — the sole writer
        of ExtractionApprovalRecord.
        """
        self.required_permission = "extraction.reject"
        self.check_permissions(request)

        run = self.get_object()
        serializer = ApproveRejectRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from apps.extraction_core.services.governance_trail import GovernanceTrailService
        record = GovernanceTrailService.record_approval_decision(
            run=run, action="REJECT", user=request.user,
            comments=serializer.validated_data.get("comments", ""),
        )

        from apps.extraction_core.extraction_serializers import (
            ExtractionApprovalRecordSerializer,
        )
        return Response(
            ExtractionApprovalRecordSerializer(record).data,
            status=status.HTTP_200_OK,
        )

    # --- POST reprocess ---

    @action(detail=True, methods=["post"])
    def reprocess(self, request, pk=None):
        """Reprocess an extraction run (re-run pipeline).

        Blocked if the run already has an APPROVED governance record —
        prevents reprocess from racing with approval finalization.
        """
        self.required_permission = "extraction.reprocess"
        self.check_permissions(request)

        run = self.get_object()

        # Guard: prevent reprocess if already approved
        if ExtractionApprovalRecord.objects.filter(
            extraction_run=run, action="APPROVED",
        ).exists():
            return Response(
                {"detail": "Cannot reprocess — extraction has already been approved."},
                status=status.HTTP_409_CONFLICT,
            )

        from apps.extraction_core.services.extraction_audit import (
            ExtractionAuditService,
        )
        ExtractionAuditService.log_extraction_reprocessed(
            extraction_run_id=run.pk,
            user=request.user,
        )

        from apps.extraction_core.services.extraction_pipeline import (
            ExtractionPipeline,
        )
        new_run = ExtractionPipeline.run(
            extraction_document_id=run.document_id,
            ocr_text=run.document.ocr_text or "",
            document_type=run.schema.document_type if run.schema else "INVOICE",
            user=request.user,
        )

        return Response(
            ExtractionRunDetailSerializer(new_run).data,
            status=status.HTTP_201_CREATED,
        )

    # --- POST escalate ---

    @action(detail=True, methods=["post"])
    def escalate(self, request, pk=None):
        """Escalate an extraction run to a different review queue."""
        self.required_permission = "extraction.escalate"
        self.check_permissions(request)

        run = self.get_object()
        serializer = EscalateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target_queue = data.get("target_queue") or "EXCEPTION_OPS"
        run.review_queue = target_queue
        run.requires_review = True
        run.save(update_fields=["review_queue", "requires_review", "updated_at"])

        from apps.extraction_core.services.extraction_audit import (
            ExtractionAuditService,
        )
        ExtractionAuditService.log_extraction_escalated(
            extraction_run_id=run.pk,
            user=request.user,
            target_queue=target_queue,
            comments=data.get("comments", ""),
        )

        return Response(
            {"detail": f"Escalated to {target_queue}", "review_queue": target_queue},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Run Pipeline — POST endpoint to trigger a governed extraction
# ---------------------------------------------------------------------------


class RunPipelineView(APIView):
    """
    POST /api/v1/extraction-pipeline/run/

    Run the governed extraction pipeline (ExtractionPipeline.run).
    """

    permission_classes = [HasPermissionCode]
    required_permission = "extraction.run"

    def post(self, request):
        serializer = RunPipelineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from apps.extraction_core.services.extraction_pipeline import (
            ExtractionPipeline,
        )

        run = ExtractionPipeline.run(
            extraction_document_id=data["extraction_document_id"],
            ocr_text=data["ocr_text"],
            document_type=data.get("document_type", "INVOICE"),
            vendor_id=data.get("vendor_id"),
            enable_llm=data.get("enable_llm", False),
            user=request.user,
        )

        http_status = (
            status.HTTP_200_OK
            if run.status == "COMPLETED"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response(
            ExtractionRunDetailSerializer(run).data,
            status=http_status,
        )


# ---------------------------------------------------------------------------
# Analytics ViewSet
# ---------------------------------------------------------------------------


class ExtractionAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for ExtractionAnalyticsSnapshot.

    Filterable by snapshot_type and country_code.
    """

    queryset = ExtractionAnalyticsSnapshot.objects.order_by("-created_at")
    serializer_class = ExtractionAnalyticsSnapshotSerializer
    permission_classes = [HasPermissionCode]
    required_permission = "extraction.analytics.view"

    def get_queryset(self):
        qs = super().get_queryset()
        snapshot_type = self.request.query_params.get("snapshot_type")
        if snapshot_type:
            qs = qs.filter(snapshot_type=snapshot_type)
        country = self.request.query_params.get("country_code")
        if country:
            qs = qs.filter(country_code__iexact=country)
        return qs


# ---------------------------------------------------------------------------
# CountryPack ViewSet
# ---------------------------------------------------------------------------


class CountryPackViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for CountryPack governance records.
    """

    queryset = CountryPack.objects.select_related("jurisdiction").order_by(
        "jurisdiction__country_code"
    )
    serializer_class = CountryPackSerializer
    permission_classes = [HasPermissionCode]
    required_permission = "extraction.view"

    def get_queryset(self):
        qs = super().get_queryset()
        pack_status = self.request.query_params.get("status")
        if pack_status:
            qs = qs.filter(pack_status__iexact=pack_status)
        return qs

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Activate a country pack."""
        self.required_permission = "extraction.run"
        self.check_permissions(request)

        pack = self.get_object()
        from apps.extraction_core.services.country_pack_service import (
            CountryPackService,
        )
        pack = CountryPackService.activate_pack(pack, user=request.user)
        return Response(CountryPackSerializer(pack).data)

    @action(detail=True, methods=["post"])
    def deprecate(self, request, pk=None):
        """Deprecate a country pack."""
        self.required_permission = "extraction.run"
        self.check_permissions(request)

        pack = self.get_object()
        from apps.extraction_core.services.country_pack_service import (
            CountryPackService,
        )
        pack = CountryPackService.deprecate_pack(pack, user=request.user)
        return Response(CountryPackSerializer(pack).data)
