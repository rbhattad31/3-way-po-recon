"""
TraceContext — shared traceability context for the 3-Way PO Reconciliation platform.

Provides milestone-based tracing across the full invoice lifecycle:
upload → extraction → reconciliation → agent → review → case close.

Design principles:
- One root trace_id per invoice upload, propagated everywhere
- Child spans for major milestones (not every helper function)
- RBAC context snapshot at action time
- Celery-safe serialization
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class TraceContext:
    """Immutable (by convention) context bag propagated across service calls.

    Fields are intentionally nullable — only populate what is known at each layer.
    """

    # --- Correlation IDs ---
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    request_id: str = ""

    # --- Business entity IDs ---
    invoice_id: Optional[int] = None
    case_id: Optional[int] = None
    reconciliation_run_id: Optional[int] = None
    reconciliation_result_id: Optional[int] = None
    review_assignment_id: Optional[int] = None
    review_action_id: Optional[int] = None
    agent_run_id: Optional[int] = None
    task_id: str = ""

    # --- Processing context ---
    processing_path: str = ""
    stage_name: str = ""
    source_service: str = ""
    source_layer: str = ""  # UI / API / TASK / SERVICE / AGENT / SYSTEM

    # --- RBAC snapshot ---
    actor_user_id: Optional[int] = None
    actor_email: str = ""
    actor_primary_role: str = ""
    actor_roles_snapshot: List[str] = field(default_factory=list)
    permission_checked: str = ""
    permission_source: str = ""  # ROLE / USER_OVERRIDE_ALLOW / ADMIN_BYPASS / ...
    access_granted: Optional[bool] = None

    # -----------------------------------------------------------------
    # Constructors
    # -----------------------------------------------------------------
    @classmethod
    def new_root(
        cls,
        *,
        invoice_id: Optional[int] = None,
        case_id: Optional[int] = None,
        source_service: str = "",
        source_layer: str = "SYSTEM",
        request_id: str = "",
        **kwargs: Any,
    ) -> "TraceContext":
        """Create a brand-new root trace (e.g. on invoice upload)."""
        tid = _new_id()
        return cls(
            trace_id=tid,
            span_id=tid,
            parent_span_id="",
            request_id=request_id or _new_id(),
            invoice_id=invoice_id,
            case_id=case_id,
            source_service=source_service,
            source_layer=source_layer,
            **kwargs,
        )

    def child(
        self,
        *,
        source_service: str = "",
        source_layer: str = "",
        stage_name: str = "",
        **overrides: Any,
    ) -> "TraceContext":
        """Derive a child span inheriting the root trace_id."""
        d = asdict(self)
        d["parent_span_id"] = self.span_id
        d["span_id"] = _new_id()
        if source_service:
            d["source_service"] = source_service
        if source_layer:
            d["source_layer"] = source_layer
        if stage_name:
            d["stage_name"] = stage_name
        d.update(overrides)
        return TraceContext(**d)

    def with_rbac(
        self,
        user,
        *,
        permission_checked: str = "",
        permission_source: str = "",
        access_granted: Optional[bool] = None,
    ) -> "TraceContext":
        """Return a copy enriched with RBAC snapshot from a Django user."""
        d = asdict(self)
        if user and getattr(user, "is_authenticated", False):
            d["actor_user_id"] = user.pk
            d["actor_email"] = getattr(user, "email", "")
            d["actor_primary_role"] = getattr(user, "role", "")
            if hasattr(user, "get_role_codes"):
                try:
                    d["actor_roles_snapshot"] = list(user.get_role_codes())
                except Exception:
                    d["actor_roles_snapshot"] = [getattr(user, "role", "")]
            else:
                d["actor_roles_snapshot"] = [getattr(user, "role", "")]
        if permission_checked:
            d["permission_checked"] = permission_checked
        if permission_source:
            d["permission_source"] = permission_source
        if access_granted is not None:
            d["access_granted"] = access_granted
        return TraceContext(**d)

    # -----------------------------------------------------------------
    # Serialization helpers
    # -----------------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        """Full dictionary (for Celery kwargs / JSON storage)."""
        return {k: v for k, v in asdict(self).items() if v is not None and v != "" and v != []}

    def as_log_dict(self) -> Dict[str, Any]:
        """Subset suitable for structured log records."""
        keys = (
            "trace_id", "span_id", "parent_span_id", "invoice_id", "case_id",
            "reconciliation_result_id", "review_assignment_id", "agent_run_id",
            "task_id", "source_service", "source_layer", "stage_name",
            "actor_user_id", "actor_email", "actor_primary_role",
            "permission_checked", "access_granted",
        )
        d = asdict(self)
        return {k: d[k] for k in keys if d.get(k) not in (None, "", [])}

    def as_audit_dict(self) -> Dict[str, Any]:
        """Fields suitable for storing in AuditEvent.metadata_json."""
        keys = (
            "trace_id", "span_id", "parent_span_id", "request_id",
            "invoice_id", "case_id", "reconciliation_run_id",
            "reconciliation_result_id", "review_assignment_id",
            "agent_run_id", "task_id", "processing_path", "stage_name",
            "source_service", "source_layer",
            "actor_user_id", "actor_email", "actor_primary_role",
            "actor_roles_snapshot", "permission_checked", "permission_source",
            "access_granted",
        )
        d = asdict(self)
        return {k: d[k] for k in keys if d.get(k) not in (None, "", [])}

    def as_celery_headers(self) -> Dict[str, Any]:
        """Minimal dict for Celery task kwargs propagation."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "invoice_id": self.invoice_id,
            "case_id": self.case_id,
            "actor_user_id": self.actor_user_id,
            "actor_email": self.actor_email,
            "actor_primary_role": self.actor_primary_role,
        }

    @classmethod
    def from_celery_headers(cls, headers: Dict[str, Any]) -> "TraceContext":
        """Reconstruct from Celery task kwargs."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in headers.items() if k in valid_keys and v is not None}
        return cls(**filtered)

    # -----------------------------------------------------------------
    # Thread-local storage for request scope
    # -----------------------------------------------------------------
    _current: Optional["TraceContext"] = None

    @classmethod
    def get_current(cls) -> Optional["TraceContext"]:
        """Return the request-scoped trace context (set by middleware)."""
        import threading
        return getattr(threading.current_thread(), "_trace_context", None)

    @classmethod
    def set_current(cls, ctx: Optional["TraceContext"]) -> None:
        import threading
        threading.current_thread()._trace_context = ctx

    @classmethod
    def current_or_empty(cls) -> "TraceContext":
        return cls.get_current() or cls()
