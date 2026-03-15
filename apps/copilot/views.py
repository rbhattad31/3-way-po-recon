"""DRF API views for the AP Copilot."""
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def session_start(request):
    """POST /api/v1/copilot/session/start/ — start or resume a session."""
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
    include_archived = request.query_params.get("archived", "").lower() == "true"
    sessions = APCopilotService.list_sessions(request.user, include_archived)
    data = CopilotSessionListSerializer(sessions[:50], many=True).data
    return Response(data)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def session_detail(request, session_id):
    """GET/PATCH /api/v1/copilot/session/<session_id>/"""
    if request.method == "PATCH":
        action = request.data.get("action")
        if action == "archive":
            ok = APCopilotService.archive_session(request.user, str(session_id))
            return Response({"archived": ok})
        if action == "pin":
            pinned = APCopilotService.toggle_pin(request.user, str(session_id))
            return Response({"is_pinned": pinned})
        return Response({"error": "Unknown action"}, status=status.HTTP_400_BAD_REQUEST)

    session = APCopilotService.get_session_detail(request.user, str(session_id))
    if not session:
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response(CopilotSessionDetailSerializer(session).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def session_messages(request, session_id):
    """GET /api/v1/copilot/session/<session_id>/messages/"""
    messages = APCopilotService.load_session_messages(request.user, str(session_id))
    return Response(CopilotMessageSerializer(messages, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def chat(request):
    """POST /api/v1/copilot/chat/ — send a message and receive a structured response."""
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
    data = APCopilotService.build_case_context(case_id, request.user)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_timeline(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/timeline/"""
    data = APCopilotService.build_case_timeline(case_id, request.user)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_evidence(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/evidence/"""
    data = APCopilotService.build_case_evidence(case_id, request.user)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_governance(request, case_id):
    """GET /api/v1/copilot/case/<case_id>/governance/"""
    data = APCopilotService.build_case_governance(case_id, request.user)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def suggestions(request):
    """GET /api/v1/copilot/suggestions/"""
    prompts = APCopilotService.get_suggestions(request.user)
    return Response({"suggestions": prompts})
