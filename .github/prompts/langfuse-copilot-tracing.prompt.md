---
mode: edit
description: Add Langfuse tracing to the AP Copilot chat pipeline and session lifecycle
---

# Langfuse Tracing — AP Copilot

Add Langfuse observability to `apps/copilot/services/copilot_service.py` so every copilot conversation appears as a named trace, each `answer_question()` call is a child span, and session quality is scored on archive.

## Target files

- `apps/copilot/services/copilot_service.py` — `APCopilotService`
- `apps/copilot/views.py` — `chat` view (to forward trace ID)

## What to implement

### 1. Trace ID per session

`CopilotSession` already has a `trace_id` field. Use it as the Langfuse trace ID throughout the session's lifetime. Resolve it once in the `chat` view and pass it into the service methods:

```python
# in views.py chat()
_lf_trace_id = str(session.trace_id) if session.trace_id else f"copilot-{session.pk}"
```

### 2. Root trace opened on session start

In `APCopilotService.start_session()`, after creating or resuming a session, open a root trace. The trace stays "open" across multiple chat turns — Langfuse groups them by `trace_id`.

```python
from apps.core.langfuse_client import start_trace

try:
    start_trace(
        str(session.trace_id),
        "copilot_session",
        metadata={
            "session_pk": str(session.pk),
            "user_id": user.pk,
            "actor_role": session.actor_primary_role,
            "linked_case_id": session.linked_case_id,
        },
    )
except Exception:
    pass
```

### 3. Per-message span in `answer_question()`

Add `lf_trace_id: str | None = None` to `answer_question()`. Open a child span at entry, close it with the response topic.

```python
from apps.core.langfuse_client import start_span, end_span, get_client

_lf_span = None
try:
    if lf_trace_id:
        lf_client = get_client()
        if lf_client:
            _lf_span = lf_client.span(
                trace_id=lf_trace_id,
                name="copilot_answer",
                metadata={
                    "message_length": len(message),
                    "has_linked_case": bool(session.linked_case_id),
                },
            )
except Exception:
    pass

try:
    response = _build_response(...)   # existing logic unchanged
    return response
finally:
    try:
        if _lf_span:
            _lf_span.end(
                output={
                    "topic": response.get("topic", "unknown"),
                    "follow_up_count": len(response.get("follow_up_prompts", [])),
                },
            )
    except Exception:
        pass
```

### 4. `copilot_session_length` score on archive

In `APCopilotService.archive_session()`, after setting `session.status = ARCHIVED`, count messages and emit the score:

```python
from apps.core.langfuse_client import score_trace

try:
    msg_count = CopilotMessage.objects.filter(session=session).count()
    score_trace(
        str(session.trace_id),
        "copilot_session_length",
        float(msg_count),
        comment=f"archived by user {user.pk}",
    )
except Exception:
    pass
```

### 5. Forward `lf_trace_id` in the chat view

```python
# apps/copilot/views.py  chat()
response_payload = APCopilotService.answer_question(
    user=request.user,
    message=message,
    session=session,
    lf_trace_id=_lf_trace_id,   # <-- add this kwarg
)
```

## Rules

- Every Langfuse call must be wrapped in `try/except Exception: pass`.
- Do NOT import `langfuse` directly; use only `apps.core.langfuse_client`.
- Trace ID is `str(session.trace_id)` — it already exists on the model, do not generate a new UUID.
- `copilot_session_length` score is a raw message count as a `float` (not normalised to 0–1).
- Do not alter the RBAC permission checks, small-talk detection logic, or response structure.
- All changes to `answer_question()` and `archive_session()` must be backward-compatible (new kwargs default to `None`).
