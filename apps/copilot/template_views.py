"""Template views for the AP Copilot workspace."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.copilot.services.copilot_service import APCopilotService


@login_required
def copilot_workspace(request):
    """Main copilot workspace — /copilot/"""
    sessions = APCopilotService.list_sessions(request.user)[:20]
    suggestions = APCopilotService.get_suggestions(request.user)
    return render(request, "copilot/ap_copilot_workspace.html", {
        "sessions": sessions,
        "suggestions": suggestions,
        "case_id": None,
        "session_id": None,
    })


@login_required
def copilot_case(request, case_id):
    """Case-linked copilot workspace — /copilot/case/<case_id>/"""
    session = APCopilotService.start_session(request.user, case_id=case_id)
    sessions = APCopilotService.list_sessions(request.user)[:20]
    suggestions = APCopilotService.get_suggestions(request.user)
    context_data = APCopilotService.build_case_context(case_id, request.user)
    messages_qs = list(APCopilotService.load_session_messages(request.user, str(session.id)))
    return render(request, "copilot/ap_copilot_workspace.html", {
        "sessions": sessions,
        "suggestions": suggestions,
        "case_id": case_id,
        "session_id": str(session.id),
        "active_session": session,
        "case_context": context_data,
        "chat_messages": messages_qs,
    })


@login_required
def copilot_session(request, session_id):
    """Resume a specific session — /copilot/session/<session_id>/"""
    session = APCopilotService.get_session_detail(request.user, str(session_id))
    sessions = APCopilotService.list_sessions(request.user)[:20]
    suggestions = APCopilotService.get_suggestions(request.user)
    messages_qs = []
    context_data = {}
    case_id = None
    if session:
        messages_qs = list(APCopilotService.load_session_messages(request.user, str(session_id)))
        case_id = session.linked_case_id
        if case_id:
            context_data = APCopilotService.build_case_context(case_id, request.user)
    return render(request, "copilot/ap_copilot_workspace.html", {
        "sessions": sessions,
        "suggestions": suggestions,
        "case_id": case_id,
        "session_id": str(session_id),
        "active_session": session,
        "case_context": context_data,
        "chat_messages": messages_qs,
    })
