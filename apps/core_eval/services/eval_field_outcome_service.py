"""Service for EvalFieldOutcome CRUD operations."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from apps.core_eval.models import EvalFieldOutcome, EvalRun

logger = logging.getLogger(__name__)


class EvalFieldOutcomeService:
    """Minimal, idempotent CRUD for EvalFieldOutcome records."""

    @staticmethod
    def record(
        *,
        eval_run: Optional[EvalRun] = None,
        field_name: str,
        status: str,
        predicted_value: str = "",
        ground_truth_value: str = "",
        confidence: Optional[float] = None,
        tenant_id: str = "",
        detail_json: Optional[dict] = None,
        tenant=None,
    ) -> EvalFieldOutcome:
        kwargs = dict(
            eval_run=eval_run,
            field_name=field_name,
            status=status,
            predicted_value=predicted_value,
            ground_truth_value=ground_truth_value,
            confidence=confidence,
            detail_json=detail_json or {},
        )
        if tenant is not None:
            kwargs["tenant"] = tenant
        elif tenant_id:
            kwargs["tenant_id"] = tenant_id
        return EvalFieldOutcome.objects.create(**kwargs)

    @staticmethod
    def bulk_record(
        *,
        eval_run: Optional[EvalRun] = None,
        outcomes: List[dict],
        tenant_id: str = "",
        tenant=None,
    ) -> List[EvalFieldOutcome]:
        """Create multiple field outcomes in one batch.

        Each dict in *outcomes* must contain at minimum ``field_name`` and
        ``status``.  Optional keys: ``predicted_value``, ``ground_truth_value``,
        ``confidence``, ``detail_json``, ``tenant_id`` (per-row override).
        """
        _resolved_tenant = tenant
        _resolved_tenant_id = tenant_id
        objs = []
        for o in outcomes:
            kw = dict(
                eval_run=eval_run,
                field_name=o["field_name"],
                status=o["status"],
                predicted_value=o.get("predicted_value", ""),
                ground_truth_value=o.get("ground_truth_value", ""),
                confidence=o.get("confidence"),
                detail_json=o.get("detail_json", {}),
            )
            row_tenant = o.get("tenant", _resolved_tenant)
            row_tenant_id = o.get("tenant_id", _resolved_tenant_id)
            if row_tenant is not None:
                kw["tenant"] = row_tenant
            elif row_tenant_id:
                kw["tenant_id"] = row_tenant_id
            objs.append(EvalFieldOutcome(**kw))
        return EvalFieldOutcome.objects.bulk_create(objs)

    @staticmethod
    def replace_for_run(
        *,
        eval_run: EvalRun,
        outcomes: List[dict],
        tenant_id: str = "",
        tenant=None,
    ) -> List[EvalFieldOutcome]:
        """Delete existing outcomes for *eval_run* then bulk-create new ones.

        This makes field-outcome sync idempotent: callers can re-run
        without duplicating rows.
        """
        EvalFieldOutcome.objects.filter(eval_run=eval_run).delete()
        _resolved_tenant = tenant
        _resolved_tenant_id = tenant_id
        objs = []
        for o in outcomes:
            kw = dict(
                eval_run=eval_run,
                field_name=o["field_name"],
                status=o["status"],
                predicted_value=o.get("predicted_value", ""),
                ground_truth_value=o.get("ground_truth_value", ""),
                confidence=o.get("confidence"),
                detail_json=o.get("detail_json", {}),
            )
            row_tenant = o.get("tenant", _resolved_tenant)
            row_tenant_id = o.get("tenant_id", _resolved_tenant_id)
            if row_tenant is not None:
                kw["tenant"] = row_tenant
            elif row_tenant_id:
                kw["tenant_id"] = row_tenant_id
            objs.append(EvalFieldOutcome(**kw))
        return EvalFieldOutcome.objects.bulk_create(objs)

    @staticmethod
    def list_for_run(eval_run: EvalRun):
        return EvalFieldOutcome.objects.filter(eval_run=eval_run).order_by("field_name")

    @staticmethod
    def summary_for_run(eval_run: EvalRun) -> dict:
        """Return a count-by-status summary for a given run."""
        qs = EvalFieldOutcome.objects.filter(eval_run=eval_run)
        total = qs.count()
        if total == 0:
            return {"total": 0}
        counts = {}
        for choice_value, _ in EvalFieldOutcome.Status.choices:
            counts[choice_value.lower()] = qs.filter(status=choice_value).count()
        counts["total"] = total
        return counts
