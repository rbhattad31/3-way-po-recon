"""Service for EvalMetric CRUD operations."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from apps.core_eval.models import EvalMetric, EvalRun

logger = logging.getLogger(__name__)


def _encode_raw(value: Any, value_type: str) -> str:
    """Serialise *value* into a string suitable for ``raw_value``."""
    if value is None:
        return ""
    if value_type == "json":
        return json.dumps(value, default=str)
    return str(value)


def _infer_value_type(
    *,
    metric_value: Any = None,
    string_value: Any = None,
    json_value: Any = None,
    value_type: Optional[str] = None,
    value: Any = None,
) -> tuple[str, str]:
    """Return ``(value_type, raw_value)`` from legacy or new-style kwargs.

    Supports both the **new** interface (``value`` + ``value_type``) and the
    **legacy** interface (``metric_value`` / ``string_value`` / ``json_value``)
    so that callers migrated in later phases keep working during the transition.
    """
    if value_type is not None and value is not None:
        return value_type, _encode_raw(value, value_type)

    # Legacy interface compat: detect which old kwarg was provided.
    if json_value is not None:
        return "json", _encode_raw(json_value, "json")
    if string_value:
        return "string", str(string_value)
    if metric_value is not None:
        return "float", str(metric_value)
    if value is not None:
        vt = value_type or "float"
        return vt, _encode_raw(value, vt)
    return (value_type or "float"), ""


class EvalMetricService:
    """Minimal, idempotent CRUD for EvalMetric records."""

    # ------------------------------------------------------------------
    # New canonical API
    # ------------------------------------------------------------------
    @staticmethod
    def record_metric(
        eval_run: Optional[EvalRun],
        metric_name: str,
        value: Any,
        value_type: str = "float",
        *,
        unit: str = "",
        tenant_id: str = "",
        dimension_json: Optional[dict] = None,
        metadata_json: Optional[dict] = None,
        tenant=None,
    ) -> EvalMetric:
        raw_value = _encode_raw(value, value_type)
        kwargs = dict(
            eval_run=eval_run,
            metric_name=metric_name,
            value_type=value_type,
            raw_value=raw_value,
            unit=unit,
            dimension_json=dimension_json or {},
            metadata_json=metadata_json or {},
        )
        if tenant is not None:
            kwargs["tenant"] = tenant
        elif tenant_id:
            kwargs["tenant_id"] = tenant_id
        return EvalMetric.objects.create(**kwargs)

    # ------------------------------------------------------------------
    # Legacy-compatible wrappers (accept old kwarg signatures)
    # ------------------------------------------------------------------
    @staticmethod
    def record(
        *,
        eval_run: Optional[EvalRun] = None,
        metric_name: str,
        metric_value: Optional[float] = None,
        string_value: str = "",
        json_value: Optional[Any] = None,
        value: Any = None,
        value_type: Optional[str] = None,
        unit: str = "",
        tenant_id: str = "",
        dimension_json: Optional[dict] = None,
        metadata_json: Optional[dict] = None,
        tenant=None,
    ) -> EvalMetric:
        vt, rv = _infer_value_type(
            metric_value=metric_value,
            string_value=string_value,
            json_value=json_value,
            value_type=value_type,
            value=value,
        )
        kwargs = dict(
            eval_run=eval_run,
            metric_name=metric_name,
            value_type=vt,
            raw_value=rv,
            unit=unit,
            dimension_json=dimension_json or {},
            metadata_json=metadata_json or {},
        )
        if tenant is not None:
            kwargs["tenant"] = tenant
        elif tenant_id:
            kwargs["tenant_id"] = tenant_id
        return EvalMetric.objects.create(**kwargs)

    @staticmethod
    def upsert(
        *,
        eval_run: EvalRun,
        metric_name: str,
        metric_value: Optional[float] = None,
        string_value: str = "",
        json_value: Optional[Any] = None,
        value: Any = None,
        value_type: Optional[str] = None,
        unit: str = "",
        tenant_id: str = "",
        dimension_json: Optional[dict] = None,
        metadata_json: Optional[dict] = None,
        tenant=None,
    ) -> tuple[EvalMetric, bool]:
        """Create or update a metric for a given run + name.

        Returns (metric, created).
        """
        vt, rv = _infer_value_type(
            metric_value=metric_value,
            string_value=string_value,
            json_value=json_value,
            value_type=value_type,
            value=value,
        )
        defaults = {
            "value_type": vt,
            "raw_value": rv,
            "unit": unit,
            "dimension_json": dimension_json or {},
            "metadata_json": metadata_json or {},
        }
        if tenant is not None:
            defaults["tenant"] = tenant
        elif tenant_id:
            defaults["tenant_id"] = tenant_id
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
