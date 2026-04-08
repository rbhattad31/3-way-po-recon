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
        return EvalFieldOutcome.objects.create(
            eval_run=eval_run,
            field_name=field_name,
            status=status,
            predicted_value=predicted_value,
            ground_truth_value=ground_truth_value,
            confidence=confidence,
            tenant_id=tenant_id,
            detail_json=detail_json or {},
            tenant=tenant,
        )

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
        objs = [
            EvalFieldOutcome(
                eval_run=eval_run,
                field_name=o["field_name"],
                status=o["status"],
                predicted_value=o.get("predicted_value", ""),
                ground_truth_value=o.get("ground_truth_value", ""),
                confidence=o.get("confidence"),
                tenant_id=o.get("tenant_id", tenant_id),
                detail_json=o.get("detail_json", {}),
                tenant=o.get("tenant", tenant),
            )
            for o in outcomes
        ]
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
        objs = [
            EvalFieldOutcome(
                eval_run=eval_run,
                field_name=o["field_name"],
                status=o["status"],
                predicted_value=o.get("predicted_value", ""),
                ground_truth_value=o.get("ground_truth_value", ""),
                confidence=o.get("confidence"),
                tenant_id=o.get("tenant_id", tenant_id),
                detail_json=o.get("detail_json", {}),
                tenant=o.get("tenant", tenant),
            )
            for o in outcomes
        ]
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
