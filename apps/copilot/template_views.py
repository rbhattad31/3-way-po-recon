"""Template views for the AP Copilot workspace."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.copilot.services.copilot_service import APCopilotService
from apps.core.permissions import _has_permission_code


@login_required
def copilot_workspace(request):
    """Legacy /copilot/ -- redirects to the unified copilot page."""
    return redirect("copilot:case_hub")


@login_required
def copilot_case(request, case_id):
    """Case-linked shortcut -- /copilot/case/<id>/ -- starts a session
    for this case then redirects to the unified page with that session."""
    if not _has_permission_code(request.user, "agents.use_copilot"):
        return HttpResponseForbidden("Permission denied")
    if not _has_permission_code(request.user, "cases.view"):
        return HttpResponseForbidden("Permission denied")
    session = APCopilotService.start_session(request.user, case_id=case_id)
    url = "/copilot/session/%s/" % session.id
    if request.GET.get("auto_run"):
        url += "?auto_run=1"
    return redirect(url)


@login_required
def copilot_session(request, session_id):
    """Resume a specific session -- /copilot/session/<uuid>/
    If the session is linked to a case with valid data, render the full
    case workspace (header + tabs + chat).  Otherwise fall back to the
    plain ChatGPT-style page."""
    if not _has_permission_code(request.user, "agents.use_copilot"):
        return HttpResponseForbidden("Permission denied")

    session = APCopilotService.get_session_detail(request.user, str(session_id))
    sessions = APCopilotService.list_sessions(request.user)[:30]
    messages_qs = []
    case_id = None
    context_data = {}
    if session:
        messages_qs = list(APCopilotService.load_session_messages(
            request.user, str(session_id),
        ))
        case_id = session.linked_case_id
        if case_id:
            context_data = APCopilotService.build_case_context(case_id, request.user)

    # Single template handles both case workspace and plain chat mode;
    # template conditionals are driven by whether ctx.case exists.
    auto_run = request.GET.get("auto_run") == "1"
    ctx = context_data if (context_data and context_data.get("case") and not context_data.get("error")) else (context_data if context_data else None)

    return render(request, "copilot/ap_copilot.html", {
        "ctx": ctx,
        "sessions": sessions,
        "case_id": case_id,
        "session_id": str(session_id),
        "active_session": session,
        "chat_messages": messages_qs,
        "auto_run_supervisor": auto_run,
    })


@login_required
def copilot_case_hub(request):
    """Unified copilot page -- /copilot/cases/

    ChatGPT-style interface: sidebar with sessions | main chat area.
    Each session is linked to a case.
    """
    if not _has_permission_code(request.user, "agents.use_copilot"):
        return HttpResponseForbidden("Permission denied")

    sessions = APCopilotService.list_sessions(request.user)[:30]

    return render(request, "copilot/ap_copilot.html", {
        "sessions": sessions,
        "case_id": None,
        "session_id": None,
        "active_session": None,
        "chat_messages": [],
        "ctx": None,
    })
