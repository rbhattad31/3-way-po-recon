"""DRF API views for the AP Copilot."""
import hashlib
import logging
import os
import tempfile
import threading

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.copilot.serializers import (
    ChatRequestSerializer,
    CopilotMessageSerializer,
    CopilotSessionDetailSerializer,
    CopilotSessionListSerializer,
    StartSessionRequestSerializer,
)
from apps.copilot.services.copilot_service import APCopilotService
from apps.core.permissions import _has_permission_code

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


def _check_copilot_access(user) -> bool:
    """Return True if user has copilot access."""
    return _has_permission_code(user, "agents.use_copilot")


def _check_case_access(user) -> bool:
    """Return True if user has cases.view permission."""
    return _has_permission_code(user, "cases.view")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def session_start(request):
    """POST /api/v1/copilot/session/start/ — start or resume a session."""
    if not _check_copilot_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    ser = StartSessionRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    session = APCopilotService.start_session(
        user=request.user,
        case_id=ser.validated_data.get("case_id"),
    )
    return Response(
        CopilotSessionDetailSerializer(session).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def session_list(request):
    """GET /api/v1/copilot/sessions/ — list user's sessions."""
    if not _check_copilot_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    include_archived = request.query_params.get("archived", "").lower() == "true"
    sessions = APCopilotService.list_sessions(request.user, include_archived)
    data = CopilotSessionListSerializer(sessions[:50], many=True).data
    return Response(data)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def session_detail(request, session_id):
    """GET/PATCH /api/v1/copilot/session/<session_id>/"""
    if not _check_copilot_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    if request.method == "PATCH":
        action = request.data.get("action")
        if action == "archive":
            ok = APCopilotService.archive_session(request.user, str(session_id))
            return Response({"archived": ok})
        if action == "pin":
            pinned = APCopilotService.toggle_pin(request.user, str(session_id))
            return Response({"is_pinned": pinned})
        if action == "link_case":
            case_id = request.data.get("case_id")
            if not case_id:
                return Response({"error": "case_id required"}, status=status.HTTP_400_BAD_REQUEST)
            result = APCopilotService.link_case_to_session(
                request.user, str(session_id), int(case_id),
            )
            if result.get("error"):
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
            return Response(result)
        if action == "unlink_case":
            result = APCopilotService.unlink_case_from_session(
                request.user, str(session_id),
            )
            return Response(result)
        return Response({"error": "Unknown action"}, status=status.HTTP_400_BAD_REQUEST)

    session = APCopilotService.get_session_detail(request.user, str(session_id))
    if not session:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response(CopilotSessionDetailSerializer(session).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def session_messages(request, session_id):
    """GET /api/v1/copilot/session/<session_id>/messages/"""
    if not _check_copilot_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    messages = APCopilotService.load_session_messages(request.user, str(session_id))
    return Response(CopilotMessageSerializer(messages, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def chat(request):
    """POST /api/v1/copilot/chat/ — send a message and receive a structured response."""
    if not _check_copilot_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    ser = ChatRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    session = APCopilotService.get_session_detail(
        request.user, str(ser.validated_data["session_id"]),
    )
    if not session:
        return Response({"error": "Session not found"}, status=status.HTTP_404_NOT_FOUND)

    # Save user message
    user_msg = APCopilotService.save_user_message(
        session, ser.validated_data["message"],
    )

    # Generate response
    payload = APCopilotService.answer_question(
        request.user, ser.validated_data["message"], session,
    )

    # Save assistant message
    assistant_msg = APCopilotService.save_assistant_message(session, payload)

    return Response({
        "user_message": CopilotMessageSerializer(user_msg).data,
        "assistant_message": CopilotMessageSerializer(assistant_msg).data,
        "response": payload,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_context(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/context/"""
    if not _check_case_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    data = APCopilotService.build_case_context(case_id, request.user)
    if data.get("error"):
        return Response(data, status=status.HTTP_404_NOT_FOUND)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_timeline(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/timeline/"""
    if not _check_case_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    data = APCopilotService.build_case_timeline(case_id, request.user)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_evidence(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/evidence/"""
    if not _check_case_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    data = APCopilotService.build_case_evidence(case_id, request.user)
    if data.get("error"):
        return Response(data, status=status.HTTP_404_NOT_FOUND)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_governance(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/governance/"""
    if not _check_case_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    data = APCopilotService.build_case_governance(case_id, request.user)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def suggestions(request):
    """GET /api/v1/copilot/suggestions/"""
    if not _check_copilot_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    prompts = APCopilotService.get_suggestions(request.user)
    return Response({"suggestions": prompts})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_search(request):
    """GET /api/v1/copilot/cases/search/?q=<query> — search cases for linking."""
    if not _check_case_access(request.user):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
    q = request.query_params.get("q", "").strip()
    results = APCopilotService.search_cases(request.user, q)
    return Response({"results": results})


# ── Invoice Upload ──────────────────────────────────────────────


def _copilot_pipeline_worker(upload_id, user_pk, has_blob, case_id=None, case_number=None):
    """Run the full extraction + case pipeline in a background thread."""
    from django.db import connection
    from apps.core.enums import FileProcessingState

    try:
        if has_blob:
            from apps.extraction.tasks import process_invoice_upload_task
            process_invoice_upload_task.apply(
                kwargs={
                    "upload_id": upload_id,
                    "case_id": case_id,
                    "case_number": case_number,
                    "skip_agent_pipeline": True,
                },
                throw=True,
            )
        else:
            _copilot_local_pipeline(upload_id, user_pk, case_id=case_id, case_number=case_number)
    except Exception:
        logger.exception("Copilot pipeline worker failed for upload %s", upload_id)
        from apps.documents.models import DocumentUpload
        try:
            DocumentUpload.objects.filter(pk=upload_id).update(
                processing_state=FileProcessingState.FAILED,
                processing_message="Pipeline failed unexpectedly",
            )
        except Exception:
            logger.warning("Failed to mark upload %s as FAILED in cleanup handler", upload_id, exc_info=True)
    finally:
        connection.close()


def _copilot_local_pipeline(upload_id, user_pk, case_id=None, case_number=None):
    """Non-blob fallback: extraction + approval + case creation with local file."""
    from django.contrib.auth import get_user_model
    from apps.core.enums import FileProcessingState
    from apps.documents.models import DocumentUpload, Invoice
    from apps.extraction.services.credit_service import CreditService
    from apps.extraction.template_views import _run_extraction_pipeline

    User = get_user_model()
    upload = DocumentUpload.objects.get(pk=upload_id)
    user = User.objects.get(pk=user_pk)
    file_path = upload.file.path

    result = _run_extraction_pipeline(upload, file_path)

    if not result["success"]:
        CreditService.refund(
            user, credits=1,
            reference_type="document_upload",
            reference_id=str(upload_id),
            remarks=f"Refund (extraction failed): {upload.original_filename}",
        )
        return

    CreditService.consume(
        user, credits=1,
        reference_type="document_upload",
        reference_id=str(upload_id),
        remarks=f"Consumed for copilot extraction: {upload.original_filename}",
    )

    invoice = (
        Invoice.objects.filter(document_upload=upload)
        .order_by("-created_at")
        .first()
    )
    if not invoice:
        return

    # Link invoice to pre-created case + dispatch processing
    try:
        from apps.cases.services.case_creation_service import CaseCreationService
        from apps.cases.tasks import process_case_task
        from apps.core.utils import dispatch_task

        # If a case was pre-created at upload time, link invoice to it;
        # otherwise fall back to create_from_upload (backward compat).
        case = None
        if case_id:
            from apps.cases.models import APCase
            case = APCase.objects.filter(pk=case_id, is_active=True).first()
            if case:
                CaseCreationService.link_invoice_to_case(case, invoice)

        if not case:
            case = CaseCreationService.create_from_upload(
                invoice=invoice, uploaded_by=user,
            )

        DocumentUpload.objects.filter(pk=upload_id).update(
            processing_message="Matching against purchase orders and receipts..."
        )
        dispatch_task(process_case_task, getattr(case, 'tenant_id', None), case.pk, skip_agent_pipeline=True)
    except Exception:
        logger.exception("Case creation failed for invoice %s", invoice.pk)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def invoice_upload(request):
    """POST /api/v1/copilot/upload/ -- upload an invoice, return immediately.

    Pipeline runs in a background thread. Poll ``upload_status`` for progress.
    """
    if not _has_permission_code(request.user, "invoices.create"):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return Response({"error": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)

    if uploaded_file.content_type not in ALLOWED_CONTENT_TYPES:
        return Response(
            {"error": "Unsupported file type. Upload PDF, PNG, JPG, or TIFF."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if uploaded_file.size > MAX_UPLOAD_SIZE:
        return Response(
            {"error": "File too large. Maximum size is 20 MB."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Credit check
    from apps.extraction.services.credit_service import CreditService

    credit_check = CreditService.check_can_reserve(request.user, credits=1)
    if not credit_check.allowed:
        return Response({"error": credit_check.message}, status=status.HTTP_402_PAYMENT_REQUIRED)

    reserve_result = CreditService.reserve(
        request.user,
        credits=1,
        reference_type="document_upload",
        remarks=f"Reserved for copilot upload: {uploaded_file.name}",
    )
    if not reserve_result.allowed:
        return Response({"error": reserve_result.message}, status=status.HTTP_402_PAYMENT_REQUIRED)

    # Compute SHA-256 hash
    sha256 = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        sha256.update(chunk)
    file_hash = sha256.hexdigest()
    uploaded_file.seek(0)

    # Create DocumentUpload record
    from apps.core.enums import DocumentType, FileProcessingState
    from apps.documents.models import DocumentUpload

    try:
        doc_upload = DocumentUpload.objects.create(
            original_filename=uploaded_file.name,
            file_size=uploaded_file.size,
            file_hash=file_hash,
            content_type=uploaded_file.content_type,
            document_type=DocumentType.INVOICE,
            processing_state=FileProcessingState.PROCESSING,
            uploaded_by=request.user,
            tenant=getattr(request, 'tenant', None),
        )
    except Exception as exc:
        CreditService.refund(
            request.user,
            credits=1,
            reference_type="document_upload",
            remarks=f"Refund: DocumentUpload creation failed -- {exc}",
        )
        logger.exception("DocumentUpload creation failed during copilot upload")
        return Response(
            {"error": "Failed to create upload record."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Try blob upload (non-fatal if blob storage is not configured)
    from apps.extraction.template_views import _try_blob_upload
    _try_blob_upload(doc_upload, uploaded_file)

    has_blob = bool(doc_upload.blob_path)

    if not has_blob:
        # Save file to Django FileField so the background thread can access it
        uploaded_file.seek(0)
        doc_upload.file.save(uploaded_file.name, uploaded_file, save=True)

    # ── Create AP Case immediately after upload (before extraction) ──
    # This ensures case_id is available as Langfuse session_id for all traces.
    case_id = None
    case_number = None
    try:
        from apps.cases.services.case_creation_service import CaseCreationService
        case = CaseCreationService.create_from_document_upload(
            upload=doc_upload,
            uploaded_by=request.user,
            tenant=getattr(request, 'tenant', None),
        )
        case_id = case.pk
        case_number = case.case_number
    except Exception as case_exc:
        logger.warning("Pre-extraction case creation failed (non-fatal): %s", case_exc)

    # Start pipeline in a background thread -- returns immediately
    thread = threading.Thread(
        target=_copilot_pipeline_worker,
        args=(doc_upload.pk, request.user.pk, has_blob),
        kwargs={"case_id": case_id, "case_number": case_number},
        daemon=True,
    )
    thread.start()

    # Audit log
    try:
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        AuditService.log_event(
            entity_type="DocumentUpload",
            entity_id=doc_upload.pk,
            event_type=AuditEventType.COPILOT_UPLOAD_STARTED,
            description=f"Copilot upload started: {doc_upload.original_filename}",
            user=request.user,
            metadata={
                "filename": doc_upload.original_filename,
                "file_size": doc_upload.file_size,
                "content_type": doc_upload.content_type,
                "has_blob": has_blob,
            },
        )
    except Exception:
        logger.debug("Audit log for copilot upload start failed (non-fatal)", exc_info=True)

    resp_data = {
        "upload_id": doc_upload.pk,
        "filename": doc_upload.original_filename,
    }
    if case_id:
        resp_data["case_id"] = case_id
        resp_data["case_number"] = case_number
    return Response(resp_data, status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def upload_status(request, upload_id):
    """GET /api/v1/copilot/upload/<id>/status/ -- progressive pipeline status."""
    from apps.core.enums import CaseStatus, FileProcessingState, InvoiceStatus
    from apps.documents.models import DocumentUpload, Invoice

    if not _has_permission_code(request.user, "invoices.view"):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

    upload = DocumentUpload.objects.filter(
        pk=upload_id, uploaded_by=request.user,
    ).first()
    if not upload:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    steps = [{"label": "Document received", "done": True}]
    completed = False
    data = {"upload_id": upload_id, "filename": upload.original_filename}

    # Failed early?
    if upload.processing_state == FileProcessingState.FAILED:
        steps.append({"label": "Extraction failed", "done": True, "failed": True})
        return Response({
            "steps": steps, "completed": True,
            "error": upload.processing_message or "Extraction failed", **data,
        })

    # Check for AP Case (pre-created via document_upload before extraction)
    from apps.cases.models import APCase

    case = APCase.objects.filter(document_upload=upload, is_active=True).first()

    if case:
        steps.append({
            "label": f"AP case {case.case_number} created",
            "done": True,
        })
        data.update({"case_id": case.pk, "case_number": case.case_number})

    # Check for Invoice
    invoice = (
        Invoice.objects.filter(document_upload=upload)
        .order_by("-created_at")
        .first()
    )
    if not invoice:
        progress_label = upload.processing_message or "Reading the document..."
        steps.append({"label": progress_label, "done": False})
        return Response({"steps": steps, "completed": False, **data})

    # Invoice exists
    conf = float(invoice.extraction_confidence or 0)
    inv_label = invoice.invoice_number or "Invoice"
    steps.append({
        "label": f"Extracted {inv_label} with {round(conf * 100)}% confidence",
        "done": True,
    })
    data.update({
        "invoice_id": invoice.pk,
        "invoice_number": invoice.invoice_number,
        "extraction_confidence": conf,
        "invoice_status": invoice.status,
    })

    # Fall back to invoice-linked case if no upload-linked case found
    if not case:
        case = APCase.objects.filter(invoice=invoice, is_active=True).first()
        if case:
            data.update({"case_id": case.pk, "case_number": case.case_number})

    if not case:
        steps.append({"label": "Opening an AP case...", "done": False})
        return Response({"steps": steps, "completed": False, **data})

    data.update({"case_id": case.pk, "case_number": case.case_number})

    # Map case status to human-readable labels
    _STAGE_LABELS = {
        CaseStatus.NEW: ("Created AP case", False),
        CaseStatus.INTAKE_IN_PROGRESS: ("Setting up the case...", False),
        CaseStatus.EXTRACTION_IN_PROGRESS: ("Recording extraction results...", False),
        CaseStatus.EXTRACTION_COMPLETED: ("Extraction recorded", False),
        CaseStatus.PENDING_EXTRACTION_APPROVAL: ("Waiting for extraction approval", True),
        CaseStatus.PATH_RESOLUTION_IN_PROGRESS: ("Deciding on the reconciliation approach...", False),
        CaseStatus.TWO_WAY_IN_PROGRESS: ("Comparing invoice against the purchase order...", False),
        CaseStatus.THREE_WAY_IN_PROGRESS: ("Comparing invoice, PO, and goods receipt...", False),
        CaseStatus.NON_PO_VALIDATION_IN_PROGRESS: ("Validating non-PO invoice...", False),
        CaseStatus.GRN_ANALYSIS_IN_PROGRESS: ("Analyzing goods receipt data...", False),
        CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS: ("AI agents are analyzing exceptions...", False),
        CaseStatus.READY_FOR_REVIEW: ("Ready for review", True),
        CaseStatus.IN_REVIEW: ("In review", True),
        CaseStatus.REVIEW_COMPLETED: ("Review completed", True),
        CaseStatus.READY_FOR_APPROVAL: ("Ready for approval", True),
        CaseStatus.APPROVAL_IN_PROGRESS: ("Running approval workflow...", False),
        CaseStatus.READY_FOR_GL_CODING: ("Ready for GL coding", True),
        CaseStatus.READY_FOR_POSTING: ("Ready for posting", True),
        CaseStatus.CLOSED: ("Case closed", True),
        CaseStatus.REJECTED: ("Case rejected", True),
        CaseStatus.ESCALATED: ("Case escalated", True),
        CaseStatus.FAILED: ("Case processing failed", True),
    }

    label, is_done = _STAGE_LABELS.get(
        case.status, (str(case.status).replace("_", " ").title(), False),
    )
    # Skip the NEW status step -- the pre-created case step already says
    # "AP case {number} created", so adding "Created AP case" is redundant.
    if case.status != CaseStatus.NEW:
        steps.append({
            "label": label,
            "done": is_done,
            "failed": case.status == CaseStatus.FAILED,
        })
    data["case_status"] = case.status

    # Reconciliation result
    if is_done and case.status not in (
        CaseStatus.PENDING_EXTRACTION_APPROVAL, CaseStatus.FAILED,
    ):
        from apps.reconciliation.models import ReconciliationResult
        recon = (
            ReconciliationResult.objects
            .filter(invoice=invoice)
            .order_by("-created_at")
            .first()
        )
        if recon:
            match_display = str(recon.match_status).replace("_", " ").title()
            steps.append({
                "label": f"Match result: {match_display}",
                "done": True,
            })
            data["match_status"] = recon.match_status

    completed = is_done
    return Response({"steps": steps, "completed": completed, **data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_reprocess_status(request, case_id):
    """GET /api/v1/copilot/case/<id>/reprocess-status/ -- progressive reprocessing status."""
    from apps.cases.models import APCase
    from apps.core.enums import CaseStatus

    if not _has_permission_code(request.user, "cases.view"):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

    case = APCase.objects.filter(pk=case_id, is_active=True).first()
    if not case:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    steps = [{"label": "Reprocessing started", "done": True}]
    completed = False
    data = {"case_id": case.pk, "case_number": case.case_number, "case_status": case.status}

    _REPROCESS_LABELS = {
        CaseStatus.NEW: ("Preparing case...", False),
        CaseStatus.INTAKE_IN_PROGRESS: ("Setting up the case...", False),
        CaseStatus.EXTRACTION_IN_PROGRESS: ("Recording extraction results...", False),
        CaseStatus.EXTRACTION_COMPLETED: ("Extraction recorded", False),
        CaseStatus.PENDING_EXTRACTION_APPROVAL: ("Waiting for extraction approval", True),
        CaseStatus.PATH_RESOLUTION_IN_PROGRESS: ("Deciding on the reconciliation approach...", False),
        CaseStatus.TWO_WAY_IN_PROGRESS: ("Comparing invoice against the purchase order...", False),
        CaseStatus.THREE_WAY_IN_PROGRESS: ("Comparing invoice, PO, and goods receipt...", False),
        CaseStatus.NON_PO_VALIDATION_IN_PROGRESS: ("Validating non-PO invoice...", False),
        CaseStatus.GRN_ANALYSIS_IN_PROGRESS: ("Analyzing goods receipt data...", False),
        CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS: ("AI agents are analyzing exceptions...", False),
        CaseStatus.READY_FOR_REVIEW: ("Ready for review", True),
        CaseStatus.IN_REVIEW: ("In review", True),
        CaseStatus.CLOSED: ("Case closed", True),
        CaseStatus.REJECTED: ("Case rejected", True),
        CaseStatus.ESCALATED: ("Case escalated", True),
        CaseStatus.FAILED: ("Case processing failed", True),
    }

    label, is_done = _REPROCESS_LABELS.get(
        case.status, (str(case.status).replace("_", " ").title(), False),
    )
    steps.append({
        "label": label,
        "done": is_done,
        "failed": case.status == CaseStatus.FAILED,
    })

    # Reconciliation result for terminal statuses
    if is_done and case.status not in (
        CaseStatus.PENDING_EXTRACTION_APPROVAL, CaseStatus.FAILED,
    ):
        from apps.reconciliation.models import ReconciliationResult
        invoice = case.invoice
        if invoice:
            recon = (
                ReconciliationResult.objects
                .filter(invoice=invoice)
                .order_by("-created_at")
                .first()
            )
            if recon:
                match_display = str(recon.match_status).replace("_", " ").title()
                steps.append({
                    "label": f"Match result: {match_display}",
                    "done": True,
                })
                data["match_status"] = recon.match_status

    completed = is_done
    return Response({"steps": steps, "completed": completed, **data})


# ─────────────────────────────────────────────────────────────────────
# Supervisor Agent Trigger
# ─────────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def supervisor_run(request):
    """POST /api/v1/copilot/supervisor/run/ -- trigger the supervisor agent."""
    if not _has_permission_code(request.user, "agents.use_copilot"):
        return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

    invoice_id = request.data.get("invoice_id")
    reconciliation_result_id = request.data.get("reconciliation_result_id")
    case_id = request.data.get("case_id")
    session_id_non_stream = request.data.get("session_id")

    if not invoice_id:
        return Response(
            {"error": "invoice_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Resolve reconciliation_result_id from case if not provided
    if not reconciliation_result_id and case_id:
        from apps.cases.models import APCase
        case = APCase.objects.filter(pk=case_id, is_active=True).first()
        if case and case.reconciliation_result_id:
            reconciliation_result_id = case.reconciliation_result_id

    # Resolve reconciliation mode
    recon_mode = None
    if reconciliation_result_id:
        from apps.reconciliation.models import ReconciliationResult
        rr = ReconciliationResult.objects.filter(pk=reconciliation_result_id).first()
        if rr:
            recon_mode = getattr(rr, "reconciliation_mode", None)

    # Resolve tenant
    tenant_id = None
    if hasattr(request.user, "company_id") and request.user.company_id:
        tenant_id = request.user.company_id

    try:
        from apps.agents.tasks import run_supervisor_pipeline_task
        try:
            result = run_supervisor_pipeline_task.delay(
                invoice_id=invoice_id,
                reconciliation_result_id=reconciliation_result_id,
                reconciliation_mode=recon_mode or "",
                shadow_mode=False,
                actor_user_id=request.user.pk,
                tenant_id=tenant_id,
            )
        except Exception:
            logger.info("Celery broker unavailable -- running supervisor task synchronously")
            result = run_supervisor_pipeline_task.apply(kwargs={
                "invoice_id": invoice_id,
                "reconciliation_result_id": reconciliation_result_id,
                "reconciliation_mode": recon_mode or "",
                "shadow_mode": False,
                "actor_user_id": request.user.pk,
                "tenant_id": tenant_id,
            })
        # When CELERY_TASK_ALWAYS_EAGER=True, result is available immediately
        if hasattr(result, "result") and isinstance(result.result, dict):
            res_data = result.result
            # Fetch tool_calls for the agent run to show progress
            tool_calls_list = []
            agent_run_id = res_data.get("agent_run_id")
            if agent_run_id:
                try:
                    from apps.tools.models import ToolCall
                    tcs = ToolCall.objects.filter(
                        agent_run_id=agent_run_id,
                    ).order_by("created_at").values("tool_name", "status", "duration_ms")
                    tool_calls_list = [
                        {
                            "tool_name": tc["tool_name"],
                            "status": tc["status"],
                            "duration_ms": tc["duration_ms"],
                        }
                        for tc in tcs
                    ]
                except Exception:
                    pass

            # Persist supervisor messages to copilot session
            if session_id_non_stream and agent_run_id:
                try:
                    _session = APCopilotService.get_session_detail(
                        request.user, str(session_id_non_stream),
                    )
                    if _session:
                        from apps.agents.models import AgentRun
                        _agent_run = AgentRun.objects.filter(pk=agent_run_id).first()
                        if _agent_run:
                            _summary = _build_supervisor_summary(_agent_run)
                            _persist_supervisor_messages(_session, _summary, _agent_run)
                except Exception:
                    logger.warning("Failed to persist supervisor messages (non-stream)", exc_info=True)

            return Response({
                "success": True,
                "recommendation": res_data.get("recommendation", ""),
                "confidence": res_data.get("confidence", 0),
                "status": res_data.get("status", ""),
                "agent_run_id": agent_run_id,
                "tool_calls": tool_calls_list,
                "message": "Supervisor agent completed (eager mode)",
            })
        return Response({
            "success": True,
            "task_id": str(result.id) if hasattr(result, "id") else None,
            "message": "Supervisor agent triggered",
        }, status=status.HTTP_202_ACCEPTED)
    except Exception as exc:
        logger.exception("Failed to trigger supervisor agent")
        return Response(
            {"error": str(exc)[:200]},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ─────────────────────────────────────────────────────────────────────
# Supervisor Summary Builder
# ─────────────────────────────────────────────────────────────────────

_RECOMMENDATION_LABELS = {
    "AUTO_CLOSE": "Auto-close -- invoice matches within tolerance.",
    "SEND_TO_AP_REVIEW": "Send to AP review -- needs human attention.",
    "ESCALATE": "Escalate -- significant discrepancy or missing data.",
    "HOLD": "Hold for further investigation.",
    "REJECT": "Reject -- invoice does not pass validation.",
    "PARTIAL_MATCH": "Partial match -- some discrepancies found.",
    "REPROCESS": "Reprocess -- data needs re-extraction.",
}

_TOOL_LABELS = {
    "get_ocr_text": "Read document text",
    "classify_document": "Classify document type",
    "extract_invoice_fields": "Extract invoice fields",
    "validate_extraction": "Validate extracted data",
    "repair_extraction": "Repair extraction issues",
    "check_duplicate": "Check for duplicate invoices",
    "verify_vendor": "Verify vendor details",
    "verify_tax_computation": "Verify tax computation",
    "vendor_search": "Search vendor directory",
    "po_lookup": "Look up purchase order",
    "grn_lookup": "Look up goods receipt",
    "run_header_match": "Match header fields",
    "run_line_match": "Match line items",
    "run_grn_match": "Match goods receipt",
    "re_extract_field": "Re-extract specific field",
    "invoke_po_retrieval_agent": "Retrieve purchase order",
    "invoke_grn_retrieval_agent": "Retrieve goods receipt",
    "get_vendor_history": "Check vendor history",
    "get_case_history": "Review case history",
    "get_tolerance_config": "Check tolerance settings",
    "persist_invoice": "Save invoice data",
    "create_case": "Create AP case",
    "submit_recommendation": "Submit recommendation",
    "assign_reviewer": "Assign reviewer",
    "generate_case_summary": "Generate case summary",
}


def _build_supervisor_summary(agent_run):
    """Build a structured summary dict from the supervisor agent run.

    Returns a dict with keys: recommendation, confidence, findings, issues,
    tools_ok, tools_failed, analysis_text -- consumed by the JS summary card.
    """
    from apps.agents.models import AgentStep

    out = agent_run.output_payload or {}
    evidence = out.get("evidence", {}) or {}
    confidence = out.get("confidence", 0)
    rec_type = out.get("recommendation_type", "")

    # Collect tool steps
    steps = AgentStep.objects.filter(
        agent_run=agent_run,
    ).order_by("step_number").values("action", "success", "output_data")

    tools_ok = 0
    tools_failed_list = []
    for s in steps:
        action = s.get("action", "")
        tool_name = action.replace("tool_call:", "").split(":")[0]
        if not tool_name:
            continue
        label = _TOOL_LABELS.get(tool_name, tool_name.replace("_", " "))
        if s.get("success"):
            tools_ok += 1
        else:
            tools_failed_list.append(label)

    # Recommendation label
    rec_label = _RECOMMENDATION_LABELS.get(rec_type, rec_type.replace("_", " ").title())

    # Recommendation severity: success / warning / danger
    rec_severity = "warning"
    if rec_type in ("APPROVE", "AUTO_CLOSE"):
        rec_severity = "success"
    elif rec_type in ("REJECT", "ESCALATE_TO_MANAGER"):
        rec_severity = "danger"

    # Key findings
    findings = []

    inv_number = evidence.get("invoice_number") or evidence.get("inv_number", "")
    vendor_name = evidence.get("vendor_name", "")
    if inv_number:
        findings.append({"label": "Invoice", "value": inv_number})
    if vendor_name:
        findings.append({"label": "Vendor", "value": vendor_name})

    ext_conf = evidence.get("extraction_confidence")
    if ext_conf is not None:
        findings.append({"label": "Extraction confidence", "value": f"{round(float(ext_conf) * 100)}%"})

    match_status = evidence.get("match_status") or evidence.get("header_match_status", "")
    if match_status:
        findings.append({"label": "Match status", "value": match_status})

    is_dup = evidence.get("is_duplicate")
    if is_dup is True:
        findings.append({"label": "Duplicate", "value": "Yes", "severity": "danger"})
    elif is_dup is False:
        findings.append({"label": "Duplicate", "value": "No", "severity": "success"})

    vendor_verified = evidence.get("vendor_verified")
    if vendor_verified is True:
        findings.append({"label": "Vendor verified", "value": "Yes", "severity": "success"})
    elif vendor_verified is False:
        findings.append({"label": "Vendor verified", "value": "No", "severity": "danger"})

    tax_valid = evidence.get("tax_valid") or evidence.get("tax_verified")
    if tax_valid is True:
        findings.append({"label": "Tax computation", "value": "Verified", "severity": "success"})
    elif tax_valid is False:
        findings.append({"label": "Tax computation", "value": "Issues found", "severity": "danger"})

    po_found = evidence.get("po_found") or evidence.get("po_number")
    if po_found:
        po_val = po_found if isinstance(po_found, str) else "Found"
        findings.append({"label": "Purchase order", "value": po_val})

    # Issues
    issues = []
    for tf in tools_failed_list:
        issues.append(f"{tf} failed")
    warnings = evidence.get("_warnings") or evidence.get("_warning")
    if warnings:
        if isinstance(warnings, list):
            issues.extend(str(w) for w in warnings[:3])
        elif isinstance(warnings, str):
            issues.append(warnings)
    if evidence.get("_min_tool_calls_not_met"):
        issues.append("Fewer tools were called than expected")
    if evidence.get("_recommendation_submitted") is False:
        issues.append("Recommendation was not submitted via tool")

    # LLM analysis text
    case_summary = (evidence.get("case_summary", "") or "")[:500]

    return {
        "recommendation": rec_label,
        "recommendation_type": rec_type,
        "recommendation_severity": rec_severity,
        "confidence": round(confidence * 100),
        "findings": findings,
        "issues": issues,
        "tools_ok": tools_ok,
        "tools_failed": len(tools_failed_list),
        "analysis_text": case_summary,
    }


def _persist_supervisor_messages(session, summary_dict, agent_run):
    """Save the supervisor run as user + assistant CopilotMessages.

    Called from the background thread after the supervisor agent completes.
    This ensures the supervisor analysis appears in chat_messages on reload
    with the same rich format (evidence cards, follow-up chips) as regular
    copilot chat responses.
    """
    # Save "Run Supervisor Agent" as user message
    APCopilotService.save_user_message(session, "Run Supervisor Agent")

    # Build a markdown-style summary for the content field
    rec = summary_dict.get("recommendation", "")
    conf = summary_dict.get("confidence", 0)
    parts = []
    if rec:
        parts.append("## Supervisor Analysis")
        parts.append("")
        parts.append("**Recommendation:** %s" % rec)
    if conf:
        parts.append("**Confidence:** %s%%" % conf)

    findings = summary_dict.get("findings", [])
    if findings:
        parts.append("")
        parts.append("### Findings")
        for f in findings:
            label = f.get("label", "")
            value = f.get("value", "")
            if label and value:
                parts.append("- **%s:** %s" % (label, value))

    issues = summary_dict.get("issues", [])
    if issues:
        parts.append("")
        parts.append("### Issues")
        for issue in issues:
            parts.append("- %s" % issue)

    analysis = summary_dict.get("analysis_text", "")
    if analysis:
        parts.append("")
        parts.append(analysis)

    tools_ok = summary_dict.get("tools_ok", 0)
    tools_failed = summary_dict.get("tools_failed", 0)
    if tools_ok or tools_failed:
        parts.append("")
        tool_info = "%d tools executed" % (tools_ok + tools_failed)
        if tools_failed:
            tool_info += ", %d failed" % tools_failed
        parts.append("*%s*" % tool_info)

    content_text = "\n".join(parts) if parts else "Supervisor analysis completed."

    # Build evidence cards from supervisor findings
    evidence = []
    if rec or conf:
        evidence.append({
            "type": "decision",
            "label": "Supervisor Recommendation",
            "data": {
                "recommendation": rec or "Analysis Complete",
                "confidence": conf / 100.0 if conf else 0,
            },
        })
    for f in findings:
        label = f.get("label", "")
        value = f.get("value", "")
        severity = f.get("severity", "")
        if label and value:
            ev_type = "match" if severity == "success" else ("exception" if severity == "danger" else "info")
            evidence.append({
                "type": ev_type,
                "label": label,
                "data": {"result": value},
            })
    for issue in issues:
        evidence.append({
            "type": "exception",
            "label": "Issue",
            "data": {"description": issue},
        })

    follow_ups = [
        "Summarize this case",
        "What are the exceptions?",
        "What is the recommendation?",
    ]

    # Build a structured payload matching what answer_question returns
    payload = {
        "summary": content_text,
        "evidence": evidence,
        "follow_up_prompts": follow_ups,
        "consulted_agents": ["SUPERVISOR"],
        "recommendation": {
            "text": rec,
            "confidence": conf / 100.0 if conf else None,
            "read_only": True,
        } if rec else None,
        "governance": {},
        "supervisor_summary": summary_dict,
        "agent_run_id": agent_run.pk if agent_run else None,
    }
    APCopilotService.save_assistant_message(session, payload)


# ─────────────────────────────────────────────────────────────────────
# Supervisor Agent SSE Stream
# ─────────────────────────────────────────────────────────────────────

def supervisor_run_stream(request):
    """POST /api/v1/copilot/supervisor/stream/ -- SSE stream of supervisor progress.

    Runs the supervisor agent in a background thread and streams tool-call
    progress events via Server-Sent Events so the UI can render real-time
    updates (similar to ChatGPT/Claude tool-calling UX).
    """
    import json as _json
    import queue

    from django.http import JsonResponse, StreamingHttpResponse

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    if not _has_permission_code(request.user, "agents.use_copilot"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        body = _json.loads(request.body)
    except (ValueError, _json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    invoice_id = body.get("invoice_id")
    reconciliation_result_id = body.get("reconciliation_result_id")
    case_id = body.get("case_id")
    session_id = body.get("session_id")

    if not invoice_id:
        return JsonResponse({"error": "invoice_id is required"}, status=400)

    # Resolve reconciliation_result_id from case if not provided
    if not reconciliation_result_id and case_id:
        from apps.cases.models import APCase
        _case = APCase.objects.filter(pk=case_id, is_active=True).first()
        if _case and _case.reconciliation_result_id:
            reconciliation_result_id = _case.reconciliation_result_id

    # Resolve reconciliation mode
    recon_mode = ""
    recon_result = None
    if reconciliation_result_id:
        from apps.reconciliation.models import ReconciliationResult
        recon_result = ReconciliationResult.objects.filter(
            pk=reconciliation_result_id,
        ).select_related("invoice", "purchase_order").first()
        if recon_result:
            recon_mode = getattr(recon_result, "reconciliation_mode", "") or ""
            if not invoice_id:
                invoice_id = recon_result.invoice_id

    # Tenant
    tenant = None
    if hasattr(request.user, "company") and request.user.company:
        tenant = request.user.company

    # Capture user info before leaving the request thread
    user_pk = request.user.pk
    user_role = getattr(request.user, "role", "") or ""

    # Resolve PO number
    _po_number = None
    if recon_result and recon_result.purchase_order:
        _po_number = recon_result.purchase_order.po_number

    # Resolve copilot session for message persistence
    _copilot_session = None
    _copilot_user = request.user
    if session_id:
        try:
            _copilot_session = APCopilotService.get_session_detail(
                request.user, str(session_id),
            )
        except Exception:
            pass

    event_queue = queue.Queue()

    def on_progress(event):
        event_queue.put(event)

    def run_agent():
        """Background thread: run the supervisor agent pipeline."""
        try:
            from django.db import connection

            from apps.agents.services.supervisor_agent import SupervisorAgent
            from apps.agents.services.supervisor_context_builder import (
                build_supervisor_context,
            )

            ctx = build_supervisor_context(
                invoice_id=invoice_id,
                reconciliation_result=recon_result,
                po_number=_po_number,
                reconciliation_mode=recon_mode,
                actor_user_id=user_pk,
                actor_primary_role=user_role or "SYSTEM_AGENT",
                trace_id="",
                tenant=tenant,
            )

            agent = SupervisorAgent()
            agent_run = agent.run(ctx, progress_callback=on_progress)

            # Post-run: eval + learning (best-effort)
            try:
                from apps.agents.services.eval_adapter import AgentEvalAdapter
                AgentEvalAdapter.sync_for_agent_run(agent_run)
            except Exception:
                pass

            # Build complete event with human-readable summary
            out = agent_run.output_payload or {}
            summary = _build_supervisor_summary(agent_run)
            event_queue.put({
                "type": "complete",
                "recommendation": out.get("recommendation_type", ""),
                "confidence": out.get("confidence", 0),
                "summary": summary,
                "agent_run_id": agent_run.pk,
                "status": str(agent_run.status),
            })

            # Persist supervisor messages to copilot session
            if _copilot_session:
                try:
                    _persist_supervisor_messages(
                        _copilot_session, summary, agent_run,
                    )
                except Exception:
                    logger.warning("Failed to persist supervisor messages to session", exc_info=True)
        except Exception as exc:
            logger.exception("Supervisor stream agent failed")
            event_queue.put({
                "type": "error",
                "message": str(exc)[:200],
            })
        finally:
            event_queue.put(None)  # sentinel
            try:
                from django.db import connection
                connection.close()
            except Exception:
                pass

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    def event_stream():
        while True:
            try:
                event = event_queue.get(timeout=180)
            except queue.Empty:
                yield "data: {\"type\": \"heartbeat\"}\n\n"
                continue
            if event is None:
                break
            yield f"data: {_json.dumps(event)}\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ─────────────────────────────────────────────────────────────────────
# Case Actions (approve, reject, escalate, reprocess, request_info)
# ─────────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def case_action(request, case_id):
    """POST /api/v1/copilot/case/<id>/action/ -- perform a case action."""
    from apps.cases.models import APCase
    from apps.core.enums import CaseStatus

    action = request.data.get("action", "").strip().lower()
    valid_actions = ("approve", "reject", "escalate", "reprocess", "request_info")
    if action not in valid_actions:
        return Response(
            {"error": f"Invalid action. Must be one of: {', '.join(valid_actions)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    case = APCase.objects.filter(pk=case_id, is_active=True).first()
    if not case:
        return Response({"error": "Case not found"}, status=status.HTTP_404_NOT_FOUND)

    user = request.user

    try:
        if action == "approve":
            if not _has_permission_code(user, "reviews.decide"):
                return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
            from apps.cases.services.review_workflow_service import ReviewWorkflowService
            ra = case.review_assignment
            if ra:
                ReviewWorkflowService.approve(ra, user)
            else:
                return Response({"error": "No review assignment found"}, status=status.HTTP_400_BAD_REQUEST)

        elif action == "reject":
            if not _has_permission_code(user, "reviews.decide"):
                return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
            from apps.cases.services.review_workflow_service import ReviewWorkflowService
            ra = case.review_assignment
            if ra:
                ReviewWorkflowService.reject(ra, user, reason=request.data.get("reason", ""))
            else:
                return Response({"error": "No review assignment found"}, status=status.HTTP_400_BAD_REQUEST)

        elif action == "escalate":
            if not _has_permission_code(user, "cases.edit"):
                return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
            case.status = CaseStatus.ESCALATED
            case.save(update_fields=["status", "updated_at"])

        elif action == "reprocess":
            if not _has_permission_code(user, "cases.edit"):
                return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
            from apps.cases.tasks import reprocess_case_from_stage_task
            tenant_id = getattr(request, 'tenant', None)
            tenant_id = tenant_id.pk if tenant_id else None
            reprocess_case_from_stage_task.delay(
                tenant_id=tenant_id, case_id=case.pk, stage="INTAKE",
            )

        elif action == "request_info":
            if not _has_permission_code(user, "cases.edit"):
                return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)
            from apps.cases.services.review_workflow_service import ReviewWorkflowService
            ra = case.review_assignment
            if ra:
                ReviewWorkflowService.add_comment(
                    ra, user, "Additional information requested by AP operator."
                )
            # Log audit
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                event_type="REQUEST_INFO",
                entity_type="APCase",
                entity_id=case.pk,
                description=f"Info requested for case {case.case_number}",
                user=user,
                case_id=case.pk,
            )

        return Response({"success": True, "action": action, "case_id": case.pk})

    except Exception as exc:
        logger.exception("Case action '%s' failed for case %s", action, case_id)
        return Response(
            {"error": str(exc)[:200]},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
