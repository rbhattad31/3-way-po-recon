"""Service for EvalMetric CRUD operations."""
from __future__ import annotations

import logging
from typing import Any, Optional

from apps.core_eval.models import EvalMetric, EvalRun

logger = logging.getLogger(__name__)


class EvalMetricService:
    """Minimal, idempotent CRUD for EvalMetric records."""

    @staticmethod
    def record(
        *,
        eval_run: Optional[EvalRun] = None,
        metric_name: str,
        metric_value: Optional[float] = None,
        string_value: str = "",
        json_value: Optional[Any] = None,
        unit: str = "",
        tenant_id: str = "",
        dimension_json: Optional[dict] = None,
        metadata_json: Optional[dict] = None,
    ) -> EvalMetric:
        return EvalMetric.objects.create(
            eval_run=eval_run,
            metric_name=metric_name,
            metric_value=metric_value,
            string_value=string_value,
            json_value=json_value,
            unit=unit,
            tenant_id=tenant_id,
            dimension_json=dimension_json or {},
            metadata_json=metadata_json or {},
        )

    @staticmethod
    def upsert(
        *,
        eval_run: EvalRun,
        metric_name: str,
        metric_value: Optional[float] = None,
        string_value: str = "",
        json_value: Optional[Any] = None,
        unit: str = "",
        tenant_id: str = "",
        dimension_json: Optional[dict] = None,
        metadata_json: Optional[dict] = None,
    ) -> tuple[EvalMetric, bool]:
        """Create or update a metric for a given run + name.

        Returns (metric, created).
        """
        defaults = {
            "metric_value": metric_value,
            "string_value": string_value,
            "json_value": json_value,
            "unit": unit,
            "tenant_id": tenant_id,
            "dimension_json": dimension_json or {},
            "metadata_json": metadata_json or {},
        }
        return EvalMetric.objects.update_or_create(
            eval_run=eval_run,
            metric_name=metric_name,
            defaults=defaults,
        )

    @staticmethod
    def list_for_run(eval_run: EvalRun):
        return EvalMetric.objects.filter(eval_run=eval_run).order_by("metric_name")

    @staticmethod
    def list_by_name(metric_name: str, limit: int = 100):
        return EvalMetric.objects.filter(metric_name=metric_name).order_by("-created_at")[:limit]
