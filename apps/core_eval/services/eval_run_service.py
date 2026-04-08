"""Service for EvalRun CRUD operations."""
from __future__ import annotations

import logging
from typing import Any, Optional

from django.utils import timezone

from apps.core_eval.models import EvalRun

logger = logging.getLogger(__name__)


class EvalRunService:
    """Minimal, idempotent CRUD for EvalRun records."""

    @staticmethod
    def create(
        *,
        app_module: str,
        entity_type: str,
        entity_id: str,
        run_key: str = "",
        tenant_id: str = "",
        status: str = EvalRun.Status.CREATED,
        prompt_hash: str = "",
        prompt_slug: str = "",
        trace_id: str = "",
        triggered_by=None,
        config_json: Optional[dict] = None,
        input_snapshot_json: Optional[dict] = None,
        tenant=None,
    ) -> EvalRun:
        return EvalRun.objects.create(
            app_module=app_module,
            entity_type=entity_type,
            entity_id=str(entity_id),
            run_key=run_key,
            tenant_id=tenant_id,
            status=status,
            prompt_hash=prompt_hash,
            prompt_slug=prompt_slug,
            trace_id=trace_id,
            triggered_by=triggered_by,
            config_json=config_json or {},
            input_snapshot_json=input_snapshot_json or {},
            tenant=tenant,
        )

    @staticmethod
    def create_or_update(
        *,
        app_module: str,
        entity_type: str,
        entity_id: str,
        run_key: str = "",
        tenant_id: str = "",
        status: str = EvalRun.Status.CREATED,
        prompt_hash: str = "",
        prompt_slug: str = "",
        trace_id: str = "",
        triggered_by=None,
        config_json: Optional[dict] = None,
        input_snapshot_json: Optional[dict] = None,
        tenant=None,
    ) -> tuple[EvalRun, bool]:
        """Upsert an EvalRun keyed by (app_module, entity_type, entity_id, run_key).

        Returns (eval_run, created).
        """
        lookup = {
            "app_module": app_module,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "run_key": run_key,
        }
        defaults = {
            "tenant_id": tenant_id,
            "status": status,
            "prompt_hash": prompt_hash,
            "prompt_slug": prompt_slug,
            "trace_id": trace_id,
            "triggered_by": triggered_by,
            "config_json": config_json or {},
            "input_snapshot_json": input_snapshot_json or {},
            "tenant": tenant,
        }
        return EvalRun.objects.update_or_create(defaults=defaults, **lookup)

    @staticmethod
    def mark_running(eval_run: EvalRun) -> EvalRun:
        eval_run.status = EvalRun.Status.RUNNING
        eval_run.started_at = timezone.now()
        eval_run.save(update_fields=["status", "started_at", "updated_at"])
        return eval_run

    @staticmethod
    def mark_completed(
        eval_run: EvalRun,
        *,
        result_json: Optional[dict] = None,
        duration_ms: Optional[int] = None,
    ) -> EvalRun:
        eval_run.status = EvalRun.Status.COMPLETED
        eval_run.completed_at = timezone.now()
        if result_json is not None:
            eval_run.result_json = result_json
        if duration_ms is not None:
            eval_run.duration_ms = duration_ms
        eval_run.save(update_fields=[
            "status", "completed_at", "result_json", "duration_ms", "updated_at",
        ])
        return eval_run

    @staticmethod
    def mark_failed(
        eval_run: EvalRun,
        *,
        error_json: Optional[dict] = None,
    ) -> EvalRun:
        eval_run.status = EvalRun.Status.FAILED
        eval_run.completed_at = timezone.now()
        if error_json is not None:
            eval_run.error_json = error_json
        eval_run.save(update_fields=[
            "status", "completed_at", "error_json", "updated_at",
        ])
        return eval_run

    @staticmethod
    def get_by_entity(
        app_module: str,
        entity_type: str,
        entity_id: str,
        *,
        run_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        """Return eval runs for a given entity, newest first.

        If *run_key* is provided, filter to that specific key.
        If *tenant_id* is provided, scope to that tenant.
        """
        qs = EvalRun.objects.filter(
            app_module=app_module,
            entity_type=entity_type,
            entity_id=str(entity_id),
        )
        if run_key is not None:
            qs = qs.filter(run_key=run_key)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        return qs.order_by("-created_at")

    @staticmethod
    def get_latest(
        app_module: str,
        entity_type: str,
        entity_id: str,
        *,
        run_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[EvalRun]:
        """Return the most recent eval run for an entity, or None."""
        qs = EvalRun.objects.filter(
            app_module=app_module,
            entity_type=entity_type,
            entity_id=str(entity_id),
        )
        if run_key is not None:
            qs = qs.filter(run_key=run_key)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        return qs.order_by("-created_at").first()
