"""
Metrics collection service for the 3-Way PO Reconciliation platform.

Provides an in-process counter/histogram registry that:
- Records counts and durations for key business operations
- Persists periodic snapshots to the database for dashboard use
- Exposes a simple API consumable by future Prometheus /metrics endpoint

Design: lightweight, no external dependencies. Uses thread-safe counters
stored in-memory with periodic DB flush for dashboard queries.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Dict, Optional

from django.utils import timezone


# ============================================================================
# In-memory metric registry (thread-safe)
# ============================================================================

class _MetricStore:
    """Thread-safe in-memory metric accumulator."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._histograms: Dict[str, list] = defaultdict(list)

    def inc(self, name: str, labels: Optional[Dict[str, str]] = None, value: int = 1):
        key = self._label_key(labels)
        with self._lock:
            self._counters[name][key] += value

    def observe(self, name: str, value: float, labels: Optional[Dict[str, str]] = None):
        key = self._label_key(labels)
        with self._lock:
            self._histograms[f"{name}|{key}"].append(value)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            counters = {k: dict(v) for k, v in self._counters.items()}
            histograms = {}
            for k, vals in self._histograms.items():
                if vals:
                    histograms[k] = {
                        "count": len(vals),
                        "sum": sum(vals),
                        "min": min(vals),
                        "max": max(vals),
                        "avg": sum(vals) / len(vals),
                    }
            return {"counters": counters, "histograms": histograms, "ts": timezone.now().isoformat()}

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    @staticmethod
    def _label_key(labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


# Singleton
_store = _MetricStore()


# ============================================================================
# Public API — counters
# ============================================================================

class MetricsService:
    """Facade for recording operational metrics."""

    # --- RBAC ---
    @staticmethod
    def rbac_permission_check(permission: str, granted: bool):
        _store.inc("rbac_permission_checks_total", {"permission": permission})
        if granted:
            _store.inc("rbac_permission_granted_total", {"permission": permission})
        else:
            _store.inc("rbac_permission_denied_total", {"permission": permission})

    @staticmethod
    def rbac_permission_eval_duration(duration_ms: int):
        _store.observe("rbac_permission_eval_duration_ms", duration_ms)

    @staticmethod
    def rbac_role_change(change_type: str):
        _store.inc("rbac_role_assignment_changes_total", {"type": change_type})

    @staticmethod
    def rbac_matrix_change():
        _store.inc("rbac_role_matrix_changes_total")

    @staticmethod
    def rbac_unauthorized_sensitive_action(action: str):
        _store.inc("rbac_unauthorized_sensitive_action_total", {"action": action})

    # --- Extraction ---
    @staticmethod
    def invoice_uploaded():
        _store.inc("invoices_uploaded_total")

    @staticmethod
    def extraction_run(success: bool, duration_ms: int, confidence: Optional[float] = None):
        _store.inc("extraction_runs_total")
        if not success:
            _store.inc("extraction_failures_total")
        _store.observe("extraction_duration_ms", duration_ms)
        if confidence is not None:
            _store.observe("extraction_confidence_avg", confidence)

    # --- Reconciliation ---
    @staticmethod
    def reconciliation_run(success: bool, duration_ms: int):
        _store.inc("reconciliation_runs_total")
        if not success:
            _store.inc("reconciliation_failures_total")
        _store.observe("reconciliation_duration_ms", duration_ms)

    @staticmethod
    def mode_resolution(mode: str):
        _store.inc("mode_resolution_total", {"mode": mode})

    @staticmethod
    def match_status(status: str):
        _store.inc("match_status_total", {"status": status})

    @staticmethod
    def po_lookup_miss():
        _store.inc("po_lookup_miss_total")

    @staticmethod
    def grn_lookup_miss():
        _store.inc("grn_lookup_miss_total")

    @staticmethod
    def reprocess():
        _store.inc("reprocess_total")

    # --- Reviews ---
    @staticmethod
    def review_created():
        _store.inc("reviews_created_total")

    @staticmethod
    def review_completed(duration_ms: int):
        _store.inc("reviews_completed_total")
        _store.observe("review_duration_ms", duration_ms)

    @staticmethod
    def manual_field_correction():
        _store.inc("manual_field_corrections_total")

    # --- Agents ---
    @staticmethod
    def agent_run(agent_type: str, success: bool, duration_ms: int, tokens: int = 0):
        _store.inc("agent_runs_total", {"agent_type": agent_type})
        if not success:
            _store.inc("agent_failures_total", {"agent_type": agent_type})
        _store.observe("agent_duration_ms", duration_ms, {"agent_type": agent_type})
        if tokens:
            _store.inc("agent_token_usage_total", {"agent_type": agent_type}, tokens)

    @staticmethod
    def recommendation(rec_type: str):
        _store.inc("recommendation_total", {"type": rec_type})

    # --- Cases / System ---
    @staticmethod
    def case_created():
        _store.inc("cases_created_total")

    @staticmethod
    def stage_duration(stage: str, duration_ms: int):
        _store.observe("stage_duration_ms", duration_ms, {"stage": stage})

    @staticmethod
    def stage_retry(stage: str):
        _store.inc("stage_retry_total", {"stage": stage})

    @staticmethod
    def task_failure(task_name: str):
        _store.inc("task_failures_total", {"task": task_name})

    @staticmethod
    def task_retry(task_name: str):
        _store.inc("task_retries_total", {"task": task_name})

    # --- Snapshot access ---
    @staticmethod
    def get_snapshot() -> Dict[str, Any]:
        return _store.snapshot()

    @staticmethod
    def reset():
        _store.reset()


# ============================================================================
# DB-persisted metric snapshot (for dashboard queries)
# ============================================================================

def flush_metrics_to_db():
    """Persist current metric snapshot to ProcessingLog for dashboard use.

    Intended to be called periodically (e.g. Celery Beat every 60s).
    """
    from apps.auditlog.models import ProcessingLog

    snapshot = _store.snapshot()
    if not snapshot.get("counters") and not snapshot.get("histograms"):
        return

    ProcessingLog.objects.create(
        level="INFO",
        source="MetricsService",
        event="metrics_snapshot",
        message="Periodic metrics flush",
        details=snapshot,
    )
    _store.reset()
