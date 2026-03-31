"""
Observability decorators for the 3-Way PO Reconciliation platform.

Provides:
- @observed_service — milestone tracing for service methods
- @observed_action — RBAC-aware audit + tracing for sensitive actions
- @observed_task — Celery task wrapper with trace propagation
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Dict, Optional

from apps.core.logging_utils import DurationTimer, get_trace_logger, redact_dict
from apps.core.metrics import MetricsService
from apps.core.trace import TraceContext

logger = logging.getLogger(__name__)


# ============================================================================
# @observed_service — service-method-level tracing
# ============================================================================

def observed_service(
    service_name: str,
    *,
    audit_event: str = "",
    entity_type: str = "",
    log_input: bool = False,
    log_output: bool = False,
):
    """Decorator for major service methods.

    Creates a child span, measures duration, writes ProcessingLog on
    completion/failure, and optionally emits an AuditEvent.

    Usage::

        class ReconciliationRunnerService:
            @observed_service("ReconciliationRunnerService", audit_event="RECONCILIATION_STARTED")
            def run(self, invoices, triggered_by=None):
                ...
    """

    def decorator(func: Callable) -> Callable:
        tlog = get_trace_logger(f"apps.observed.{service_name}")

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Build child span from current context
            parent_ctx = TraceContext.get_current() or TraceContext()
            ctx = parent_ctx.child(
                source_service=service_name,
                source_layer="SERVICE",
            )
            TraceContext.set_current(ctx)

            tlog.info(
                "Service %s.%s started",
                service_name,
                func.__name__,
                extra={"event_name": f"{service_name}.{func.__name__}.started"},
            )

            timer = DurationTimer()
            error_msg = ""
            success = True

            try:
                with timer:
                    result = func(*args, **kwargs)
                return result
            except Exception as exc:
                success = False
                error_msg = f"{type(exc).__name__}: {str(exc)[:500]}"
                tlog.exception(
                    "Service %s.%s failed: %s",
                    service_name,
                    func.__name__,
                    error_msg,
                    extra={"event_name": f"{service_name}.{func.__name__}.failed"},
                )
                raise
            finally:
                # Write ProcessingLog
                _write_processing_log(
                    event_name=f"{service_name}.{func.__name__}",
                    service_name=service_name,
                    ctx=ctx,
                    duration_ms=timer.duration_ms,
                    success=success,
                    error_message=error_msg,
                )

                # Write AuditEvent if configured
                if audit_event and success:
                    _write_audit_event(
                        event_type=audit_event,
                        entity_type=entity_type or _guess_entity_type(ctx),
                        entity_id=_guess_entity_id(ctx),
                        ctx=ctx,
                        description=f"{service_name}.{func.__name__} completed in {timer.duration_ms}ms",
                    )

                # Restore parent context
                TraceContext.set_current(parent_ctx)

        return wrapper
    return decorator


# ============================================================================
# @observed_action — RBAC-aware sensitive action tracing
# ============================================================================

def observed_action(
    action_name: str,
    *,
    permission: str = "",
    entity_type: str = "",
    audit_event: str = "",
):
    """Decorator for sensitive actions requiring RBAC-aware audit trail.

    Captures actor identity, role snapshot, permission checked, and
    writes both an AuditEvent and ProcessingLog.

    Usage::

        @observed_action("trigger_reconciliation", permission="reconciliation.run")
        def start_reconciliation(request, invoice_ids):
            ...
    """

    def decorator(func: Callable) -> Callable:
        tlog = get_trace_logger(f"apps.action.{action_name}")

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Try to find the request object (first arg for views)
            request = _find_request(args, kwargs)
            user = getattr(request, "user", None) if request else None

            parent_ctx = TraceContext.get_current() or TraceContext()
            ctx = parent_ctx.child(
                source_service=action_name,
                source_layer="UI" if request else "SERVICE",
            )

            # Enrich with RBAC context
            if user and permission:
                perm_source = _resolve_permission_source(user, permission)
                access = _check_permission(user, permission)
                ctx = ctx.with_rbac(
                    user,
                    permission_checked=permission,
                    permission_source=perm_source,
                    access_granted=access,
                )
                MetricsService.rbac_permission_check(permission, access)
                if not access:
                    MetricsService.rbac_unauthorized_sensitive_action(action_name)
            elif user:
                ctx = ctx.with_rbac(user)

            TraceContext.set_current(ctx)

            tlog.info(
                "Action '%s' initiated by %s",
                action_name,
                ctx.actor_email or "system",
                extra={"event_name": f"action.{action_name}.started"},
            )

            timer = DurationTimer()
            success = True
            error_msg = ""

            try:
                with timer:
                    result = func(*args, **kwargs)
                return result
            except Exception as exc:
                success = False
                error_msg = f"{type(exc).__name__}: {str(exc)[:500]}"
                raise
            finally:
                evt_type = audit_event or f"ACTION_{action_name.upper()}"
                _write_audit_event(
                    event_type=evt_type,
                    entity_type=entity_type or _guess_entity_type(ctx),
                    entity_id=_guess_entity_id(ctx),
                    ctx=ctx,
                    description=(
                        f"Action '{action_name}' by {ctx.actor_email or 'system'} "
                        f"({'granted' if ctx.access_granted else 'denied' if ctx.access_granted is False else 'unchecked'}) "
                        f"completed={'OK' if success else 'FAIL'} in {timer.duration_ms}ms"
                    ),
                    duration_ms=timer.duration_ms,
                    success=success,
                    error_message=error_msg,
                )
                _write_processing_log(
                    event_name=f"action.{action_name}",
                    service_name=action_name,
                    ctx=ctx,
                    duration_ms=timer.duration_ms,
                    success=success,
                    error_message=error_msg,
                )
                TraceContext.set_current(parent_ctx)

        return wrapper
    return decorator


# ============================================================================
# @observed_task — Celery task trace propagation wrapper
# ============================================================================

def observed_task(
    task_name: str,
    *,
    audit_event: str = "",
    entity_type: str = "",
):
    """Decorator for Celery task functions.

    Reconstructs TraceContext from Celery kwargs, creates a child span,
    and logs task lifecycle (started/completed/failed).

    Usage::

        @shared_task(bind=True, max_retries=2)
        @observed_task("process_invoice_upload", audit_event="EXTRACTION_COMPLETED")
        def process_invoice_upload_task(self, upload_id, **kwargs):
            ...

    The task should accept **kwargs to receive trace_context_* headers.
    """

    def decorator(func: Callable) -> Callable:
        tlog = get_trace_logger(f"apps.task.{task_name}")

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Reconstruct trace context from kwargs — only strip actual trace
            # propagation headers (keys starting with "trace_"), never task
            # payload arguments like invoice_id, case_id, user_id, etc.
            trace_headers = {k: v for k, v in kwargs.items() if k.startswith("trace_")}

            if trace_headers.get("trace_id"):
                parent_ctx = TraceContext.from_celery_headers(trace_headers)
            else:
                parent_ctx = TraceContext()

            ctx = parent_ctx.child(
                source_service=task_name,
                source_layer="TASK",
                task_id=kwargs.get("task_id", ""),
            )
            TraceContext.set_current(ctx)

            tlog.info("Task %s started", task_name, extra={"event_name": f"task.{task_name}.started"})

            timer = DurationTimer()
            success = True
            error_msg = ""

            try:
                with timer:
                    # Remove trace headers from kwargs before calling actual task
                    clean_kwargs = {k: v for k, v in kwargs.items() if k not in trace_headers}
                    result = func(*args, **clean_kwargs)
                tlog.info(
                    "Task %s completed in %dms",
                    task_name,
                    timer.duration_ms,
                    extra={"event_name": f"task.{task_name}.completed", "duration_ms": timer.duration_ms},
                )
                return result
            except Exception as exc:
                success = False
                error_msg = f"{type(exc).__name__}: {str(exc)[:500]}"
                MetricsService.task_failure(task_name)
                tlog.exception(
                    "Task %s failed: %s",
                    task_name,
                    error_msg,
                    extra={"event_name": f"task.{task_name}.failed"},
                )
                raise
            finally:
                _write_processing_log(
                    event_name=f"task.{task_name}",
                    service_name=task_name,
                    ctx=ctx,
                    duration_ms=timer.duration_ms,
                    success=success,
                    error_message=error_msg,
                    task_name=task_name,
                )
                TraceContext.set_current(None)

        return wrapper
    return decorator


# ============================================================================
# Internal helpers
# ============================================================================

def _find_request(args, kwargs):
    """Best-effort extraction of HttpRequest from function args."""
    from django.http import HttpRequest
    for a in args:
        if isinstance(a, HttpRequest):
            return a
    for v in kwargs.values():
        if isinstance(v, HttpRequest):
            return v
    # Check if first arg has 'user' attribute (common in DRF views)
    if args and hasattr(args[0], "user") and hasattr(args[0], "method"):
        return args[0]
    return None


def _check_permission(user, permission_code: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    from apps.core.permissions import _has_permission_code
    return _has_permission_code(user, permission_code)


def _resolve_permission_source(user, permission_code: str) -> str:
    """Determine how a permission resolves for the user."""
    if not user or not getattr(user, "is_authenticated", False):
        return "USER_INACTIVE"
    if not getattr(user, "is_active", True):
        return "USER_INACTIVE"

    from apps.core.permissions import _is_admin
    if _is_admin(user):
        return "ADMIN_BYPASS"

    # Check user-level overrides
    if hasattr(user, "get_permission_overrides"):
        try:
            overrides = user.get_permission_overrides()
            if permission_code in overrides:
                otype = overrides[permission_code]
                if otype == "DENY":
                    return "USER_OVERRIDE_DENY"
                return "USER_OVERRIDE_ALLOW"
        except Exception:
            pass

    # Check role-level
    from apps.core.permissions import _has_permission_code
    if _has_permission_code(user, permission_code):
        return "ROLE"

    return "NO_PERMISSION"


def _guess_entity_type(ctx: TraceContext) -> str:
    if ctx.case_id:
        return "APCase"
    if ctx.invoice_id:
        return "Invoice"
    if ctx.reconciliation_result_id:
        return "ReconciliationResult"
    if ctx.review_assignment_id:
        return "ReviewAssignment"
    return "System"


def _guess_entity_id(ctx: TraceContext) -> int:
    return ctx.invoice_id or ctx.case_id or ctx.reconciliation_result_id or ctx.review_assignment_id or 0


def _write_processing_log(
    event_name: str,
    service_name: str,
    ctx: TraceContext,
    duration_ms: int,
    success: bool,
    error_message: str = "",
    task_name: str = "",
):
    """Write a ProcessingLog record for operational visibility."""
    try:
        from apps.auditlog.models import ProcessingLog

        ProcessingLog.objects.create(
            level="INFO" if success else "ERROR",
            source=service_name,
            event=event_name,
            message=f"{'OK' if success else 'FAIL'} in {duration_ms}ms" + (f" — {error_message[:200]}" if error_message else ""),
            details={
                "duration_ms": duration_ms,
                "success": success,
                "error": error_message[:500] if error_message else None,
                "trace_id": ctx.trace_id,
                "span_id": ctx.span_id,
                "actor_user_id": ctx.actor_user_id,
                "actor_email": ctx.actor_email,
                "actor_primary_role": ctx.actor_primary_role,
                "permission_checked": ctx.permission_checked or None,
                "access_granted": ctx.access_granted,
                "task_name": task_name or None,
            },
            invoice_id=ctx.invoice_id,
            reconciliation_result_id=ctx.reconciliation_result_id,
            agent_run_id=ctx.agent_run_id,
            user_id=ctx.actor_user_id,
            trace_id=ctx.trace_id or "",
        )
    except Exception:
        logger.debug("Failed to write ProcessingLog (non-critical)", exc_info=True)


def _write_audit_event(
    event_type: str,
    entity_type: str,
    entity_id: int,
    ctx: TraceContext,
    description: str = "",
    duration_ms: int = 0,
    success: bool = True,
    error_message: str = "",
):
    """Write an AuditEvent record for business audit trail."""
    try:
        from apps.auditlog.models import AuditEvent

        AuditEvent.objects.create(
            entity_type=entity_type or "System",
            entity_id=entity_id or 0,
            action=event_type,
            event_type=event_type,
            event_description=description[:2000],
            performed_by_id=ctx.actor_user_id,
            metadata_json={
                **ctx.as_audit_dict(),
                "duration_ms": duration_ms,
                "success": success,
                "error": error_message[:500] if error_message else None,
            },
        )
    except Exception:
        logger.debug("Failed to write AuditEvent (non-critical)", exc_info=True)
