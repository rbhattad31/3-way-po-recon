"""Service for LearningSignal CRUD operations."""
from __future__ import annotations

import logging
from typing import Optional

from apps.core_eval.models import EvalRun, LearningSignal

logger = logging.getLogger(__name__)


class LearningSignalService:
    """Minimal, idempotent CRUD for LearningSignal records."""

    @staticmethod
    def record(
        *,
        app_module: str,
        signal_type: str,
        entity_type: str = "",
        entity_id: str = "",
        aggregation_key: str = "",
        confidence: float = 0.0,
        tenant_id: str = "",
        actor=None,
        field_name: str = "",
        old_value: str = "",
        new_value: str = "",
        payload_json: Optional[dict] = None,
        eval_run: Optional[EvalRun] = None,
    ) -> LearningSignal:
        return LearningSignal.objects.create(
            app_module=app_module,
            signal_type=signal_type,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else "",
            aggregation_key=aggregation_key,
            confidence=confidence,
            tenant_id=tenant_id,
            actor=actor,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            payload_json=payload_json or {},
            eval_run=eval_run,
        )

    @staticmethod
    def list_by_entity(
        entity_type: str,
        entity_id: str,
        signal_type: Optional[str] = None,
    ):
        qs = LearningSignal.objects.filter(
            entity_type=entity_type,
            entity_id=str(entity_id),
        )
        if signal_type:
            qs = qs.filter(signal_type=signal_type)
        return qs.order_by("-created_at")

    @staticmethod
    def list_by_module(
        app_module: str,
        signal_type: Optional[str] = None,
        limit: int = 100,
    ):
        qs = LearningSignal.objects.filter(app_module=app_module)
        if signal_type:
            qs = qs.filter(signal_type=signal_type)
        return qs.order_by("-created_at")[:limit]

    @staticmethod
    def count_by_field(
        app_module: str,
        signal_type: str = "field_correction",
    ) -> dict:
        """Return {field_name: count} for a given module and signal type."""
        from django.db.models import Count

        qs = (
            LearningSignal.objects.filter(
                app_module=app_module,
                signal_type=signal_type,
            )
            .exclude(field_name="")
            .values("field_name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return {row["field_name"]: row["count"] for row in qs}
