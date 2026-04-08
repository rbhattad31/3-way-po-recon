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
            pass
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
        dispatch_task(process_case_task, getattr(case, 'tenant_id', None), case.pk)
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
        pass

    return Response({
        "upload_id": doc_upload.pk,
        "filename": doc_upload.original_filename,
    }, status=status.HTTP_202_ACCEPTED)


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
