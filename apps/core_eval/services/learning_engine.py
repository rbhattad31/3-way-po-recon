"""Controlled Learning Engine -- aggregates signals and proposes actions.

This engine is deterministic and read-heavy: it scans LearningSignal records,
detects patterns via configurable threshold rules, and creates LearningAction
proposals.  It NEVER auto-applies changes to production behavior.

All rules are idempotent -- running the engine twice over the same data
produces no duplicate LearningActions (dedup by action_type + dedup_key).

Usage::

    engine = LearningEngine(days=7, min_confidence=0.0)
    summary = engine.run()                # all modules
    summary = engine.run(module="extraction")
    summary = engine.run(dry_run=True)    # preview only, no DB writes
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.core_eval.models import LearningAction, LearningSignal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
DEFAULT_DAYS = 7
DEFAULT_MIN_CONFIDENCE = 0.0

# Rule thresholds
FIELD_CORRECTION_MIN_COUNT = 20
PROMPT_WEAKNESS_MIN_CORRECTIONS = 10
PROMPT_WEAKNESS_CORRECTION_RATE = 0.30  # 30 %
AUTO_APPROVE_RISK_MIN_COUNT = 5
VALIDATION_CLUSTER_MIN_COUNT = 10
VENDOR_ISSUE_MIN_COUNT = 10

# Cooldown: skip proposing an action if an identical one was proposed
# within this many days.
COOLDOWN_DAYS = 3


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class EngineRunSummary:
    """Result of a single LearningEngine.run() invocation."""
    signals_scanned: int = 0
    rules_evaluated: int = 0
    actions_proposed: int = 0
    actions_skipped_dedup: int = 0
    actions_skipped_cooldown: int = 0
    details: list = field(default_factory=list)

    def log_summary(self) -> str:
        lines = [
            f"LearningEngine run complete:",
            f"  signals scanned   = {self.signals_scanned}",
            f"  rules evaluated   = {self.rules_evaluated}",
            f"  actions proposed  = {self.actions_proposed}",
            f"  skipped (dedup)   = {self.actions_skipped_dedup}",
            f"  skipped (cooldown)= {self.actions_skipped_cooldown}",
        ]
        for d in self.details:
            lines.append(f"  -> {d}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class LearningEngine:
    """Deterministic, rule-based learning engine.

    Scans LearningSignal records within a time window, applies threshold
    rules, and proposes LearningAction records for human review.
    """

    def __init__(
        self,
        *,
        days: int = DEFAULT_DAYS,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        cooldown_days: int = COOLDOWN_DAYS,
    ):
        self.days = days
        self.min_confidence = min_confidence
        self.cooldown_days = cooldown_days
        self._cutoff = timezone.now() - timedelta(days=self.days)
        self._cooldown_cutoff = timezone.now() - timedelta(days=self.cooldown_days)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        module: str = "",
        dry_run: bool = False,
    ) -> EngineRunSummary:
        """Execute all rules and return a summary.

        Args:
            module: If set, restrict to signals from this app_module only.
            dry_run: If True, detect patterns but do not write LearningActions.
        """
        summary = EngineRunSummary()
        base_qs = self._base_queryset(module)
        summary.signals_scanned = base_qs.count()

        rules = [
            self._rule_field_correction_hotspot,
            self._rule_prompt_weakness,
            self._rule_auto_approve_risk,
            self._rule_validation_failure_cluster,
            self._rule_vendor_specific_issue,
        ]

        for rule_fn in rules:
            summary.rules_evaluated += 1
            try:
                rule_fn(base_qs, summary, dry_run=dry_run)
            except Exception:
                logger.exception("Rule %s failed (non-fatal)", rule_fn.__name__)

        logger.info(summary.log_summary())

        try:
            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType

            AuditService.log_event(
                entity_type="LearningEngine",
                entity_id=0,
                event_type=AuditEventType.LEARNING_ENGINE_RUN,
                description=(
                    f"Learning engine run: {summary.signals_scanned} signals scanned, "
                    f"{summary.actions_proposed} actions proposed"
                ),
                agent="LearningEngine",
                metadata={
                    "module": module,
                    "dry_run": dry_run,
                    "signals_scanned": summary.signals_scanned,
                    "rules_evaluated": summary.rules_evaluated,
                    "actions_proposed": summary.actions_proposed,
                    "actions_skipped_dedup": summary.actions_skipped_dedup,
                    "actions_skipped_cooldown": summary.actions_skipped_cooldown,
                },
            )
        except Exception:
            logger.debug("Audit log for LEARNING_ENGINE_RUN failed (non-fatal)")

        return summary

    # ------------------------------------------------------------------
    # Aggregation helpers (public for direct use)
    # ------------------------------------------------------------------

    def aggregate_signals_by_key(
        self,
        aggregation_key: str,
        *,
        module: str = "",
    ) -> dict[str, Any]:
        """Aggregate signals sharing the same aggregation_key."""
        qs = self._base_queryset(module).filter(aggregation_key=aggregation_key)
        agg = qs.aggregate(
            total_count=Count("id"),
            avg_confidence=Avg("confidence"),
        )
        unique_entities = (
            qs.exclude(entity_id="")
            .values("entity_type", "entity_id")
            .distinct()
            .count()
        )
        samples = list(
            qs.order_by("-created_at")
            .values("signal_type", "field_name", "old_value", "new_value", "payload_json")[:5]
        )
        return {
            "total_count": agg["total_count"] or 0,
            "unique_entities": unique_entities,
            "avg_confidence": round(agg["avg_confidence"] or 0.0, 4),
            "sample_payloads": samples,
        }

    def aggregate_signals_by_field(
        self,
        field_code: str,
        *,
        module: str = "",
        signal_type: str = "field_correction",
    ) -> dict[str, Any]:
        """Aggregate signals for a specific field_name."""
        qs = self._base_queryset(module).filter(
            field_name=field_code,
            signal_type=signal_type,
        )
        agg = qs.aggregate(
            total_count=Count("id"),
            avg_confidence=Avg("confidence"),
        )
        # Top corrected-to values
        top_new = list(
            qs.exclude(new_value="")
            .values("new_value")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")[:5]
        )
        return {
            "field_code": field_code,
            "total_count": agg["total_count"] or 0,
            "avg_confidence": round(agg["avg_confidence"] or 0.0, 4),
            "top_corrected_values": top_new,
        }

    def aggregate_signals_by_module(
        self,
        module_name: str,
    ) -> dict[str, Any]:
        """Aggregate all signals for a module, grouped by signal_type."""
        qs = self._base_queryset(module_name)
        by_type = list(
            qs.values("signal_type")
            .annotate(cnt=Count("id"), avg_conf=Avg("confidence"))
            .order_by("-cnt")
        )
        return {
            "module": module_name,
            "total_count": sum(r["cnt"] for r in by_type),
            "by_signal_type": [
                {
                    "signal_type": r["signal_type"],
                    "count": r["cnt"],
                    "avg_confidence": round(r["avg_conf"] or 0.0, 4),
                }
                for r in by_type
            ],
        }

    def aggregate_signals_by_prompt(
        self,
        prompt_hash: str,
        *,
        module: str = "",
    ) -> dict[str, Any]:
        """Aggregate signals linked to EvalRuns with a specific prompt_hash."""
        qs = self._base_queryset(module).filter(
            eval_run__prompt_hash=prompt_hash,
        )
        agg = qs.aggregate(
            total_count=Count("id"),
            avg_confidence=Avg("confidence"),
        )
        by_type = list(
            qs.values("signal_type")
            .annotate(cnt=Count("id"))
            .order_by("-cnt")
        )
        return {
            "prompt_hash": prompt_hash,
            "total_count": agg["total_count"] or 0,
            "avg_confidence": round(agg["avg_confidence"] or 0.0, 4),
            "by_signal_type": {r["signal_type"]: r["cnt"] for r in by_type},
        }

    # ------------------------------------------------------------------
    # Rule implementations
    # ------------------------------------------------------------------

    def _rule_field_correction_hotspot(
        self, base_qs, summary: EngineRunSummary, *, dry_run: bool,
    ) -> None:
        """RULE 1: Detect fields that are corrected frequently."""
        qs = base_qs.filter(signal_type="field_correction").exclude(field_name="")
        field_counts = (
            qs.values("field_name", "app_module")
            .annotate(
                cnt=Count("id"),
                avg_conf=Avg("confidence"),
            )
            .filter(cnt__gte=FIELD_CORRECTION_MIN_COUNT)
            .order_by("-cnt")
        )

        for row in field_counts:
            field_name = row["field_name"]
            app_module = row["app_module"]
            count = row["cnt"]

            # Gather top corrected values
            top_values = list(
                qs.filter(field_name=field_name, app_module=app_module)
                .exclude(new_value="")
                .values("new_value")
                .annotate(cnt=Count("id"))
                .order_by("-cnt")[:5]
            )
            examples = list(
                qs.filter(field_name=field_name, app_module=app_module)
                .order_by("-created_at")
                .values("old_value", "new_value", "entity_id")[:5]
            )

            dedup_key = f"field_correction_hotspot:{app_module}:{field_name}"
            proposed = self._propose_action(
                action_type="field_normalization_candidate",
                app_module=app_module,
                dedup_key=dedup_key,
                target_description=(
                    f"Field '{field_name}' corrected {count} times "
                    f"in the last {self.days} days"
                ),
                rationale=(
                    f"Detected {count} field_correction signals for "
                    f"'{field_name}' in module '{app_module}'. "
                    f"Average confidence at correction time: "
                    f"{row['avg_conf']:.2f}. Consider adding a "
                    f"normalization rule or alias."
                ),
                input_signals_json={
                    "rule": "field_correction_hotspot",
                    "field_name": field_name,
                    "count": count,
                    "avg_confidence": round(row["avg_conf"] or 0.0, 4),
                    "time_window_days": self.days,
                },
                action_payload_json={
                    "field_code": field_name,
                    "issue": "frequent formatting corrections",
                    "top_corrected_values": [
                        {"value": v["new_value"], "count": v["cnt"]}
                        for v in top_values
                    ],
                    "examples": examples,
                    "suggested_fix": "normalize format before validation",
                },
                summary=summary,
                dry_run=dry_run,
            )

    def _rule_prompt_weakness(
        self, base_qs, summary: EngineRunSummary, *, dry_run: bool,
    ) -> None:
        """RULE 2: Detect prompts with high correction rates."""
        # Find prompt_hashes with corrections
        correction_qs = base_qs.filter(
            signal_type="field_correction",
            eval_run__isnull=False,
        ).exclude(eval_run__prompt_hash="")

        prompt_corrections = (
            correction_qs
            .values("eval_run__prompt_hash", "eval_run__app_module")
            .annotate(correction_count=Count("id"))
            .filter(correction_count__gte=PROMPT_WEAKNESS_MIN_CORRECTIONS)
        )

        for row in prompt_corrections:
            prompt_hash = row["eval_run__prompt_hash"]
            app_module = row["eval_run__app_module"]
            correction_count = row["correction_count"]

            # Count total eval runs with this prompt_hash in the window
            from apps.core_eval.models import EvalRun
            total_runs = EvalRun.objects.filter(
                prompt_hash=prompt_hash,
                app_module=app_module,
                created_at__gte=self._cutoff,
            ).count()

            if total_runs == 0:
                continue

            correction_rate = correction_count / total_runs
            if correction_rate < PROMPT_WEAKNESS_CORRECTION_RATE:
                continue

            dedup_key = f"prompt_weakness:{app_module}:{prompt_hash}"
            self._propose_action(
                action_type="prompt_review",
                app_module=app_module,
                dedup_key=dedup_key,
                target_description=(
                    f"Prompt {prompt_hash[:12]}... has "
                    f"{correction_rate:.0%} correction rate "
                    f"({correction_count}/{total_runs} runs)"
                ),
                rationale=(
                    f"Prompt hash '{prompt_hash}' in module '{app_module}' "
                    f"produced corrections in {correction_rate:.1%} of runs "
                    f"({correction_count} corrections across {total_runs} runs "
                    f"in the last {self.days} days). "
                    f"Review the prompt for ambiguity or missing instructions."
                ),
                input_signals_json={
                    "rule": "prompt_weakness",
                    "prompt_hash": prompt_hash,
                    "correction_count": correction_count,
                    "total_runs": total_runs,
                    "correction_rate": round(correction_rate, 4),
                    "time_window_days": self.days,
                },
                action_payload_json={
                    "prompt_hash": prompt_hash,
                    "correction_rate": round(correction_rate, 4),
                    "correction_count": correction_count,
                    "total_runs": total_runs,
                    "suggested_fix": (
                        "review prompt template for ambiguity; "
                        "consider adding field-level instructions"
                    ),
                },
                summary=summary,
                dry_run=dry_run,
            )

    def _rule_auto_approve_risk(
        self, base_qs, summary: EngineRunSummary, *, dry_run: bool,
    ) -> None:
        """RULE 3: Detect auto-approved items that were later corrected."""
        # Find entities that were auto-approved AND then had corrections
        auto_qs = base_qs.filter(signal_type="auto_approve_outcome")
        auto_entity_ids = set(
            auto_qs.exclude(entity_id="")
            .values_list("entity_id", flat=True)
        )
        if not auto_entity_ids:
            return

        # Check which of those entities also have field_correction signals
        corrected_after_auto = (
            base_qs.filter(
                signal_type="field_correction",
                entity_id__in=auto_entity_ids,
            )
            .values("entity_id")
            .annotate(cnt=Count("id"))
        )

        risk_count = corrected_after_auto.count()
        if risk_count < AUTO_APPROVE_RISK_MIN_COUNT:
            return

        total_auto = len(auto_entity_ids)
        risk_rate = risk_count / total_auto if total_auto else 0.0

        # Aggregate confidence at auto-approve time
        avg_conf = auto_qs.filter(
            entity_id__in=[r["entity_id"] for r in corrected_after_auto],
        ).aggregate(avg=Avg("confidence"))["avg"] or 0.0

        app_modules = list(
            auto_qs.values_list("app_module", flat=True).distinct()
        )
        module_str = app_modules[0] if len(app_modules) == 1 else ",".join(app_modules)

        dedup_key = f"auto_approve_risk:{module_str}"
        self._propose_action(
            action_type="threshold_tune",
            app_module=module_str,
            dedup_key=dedup_key,
            target_description=(
                f"{risk_count}/{total_auto} auto-approved items were "
                f"later corrected ({risk_rate:.0%})"
            ),
            rationale=(
                f"{risk_count} entities auto-approved in the last "
                f"{self.days} days were subsequently corrected "
                f"(out of {total_auto} total auto-approvals). "
                f"Average confidence at auto-approve time: {avg_conf:.2f}. "
                f"Consider raising the auto-approve confidence threshold."
            ),
            input_signals_json={
                "rule": "auto_approve_risk",
                "risk_count": risk_count,
                "total_auto_approvals": total_auto,
                "risk_rate": round(risk_rate, 4),
                "avg_confidence_at_approval": round(avg_conf, 4),
                "time_window_days": self.days,
            },
            action_payload_json={
                "current_risk_rate": round(risk_rate, 4),
                "risk_count": risk_count,
                "avg_confidence_at_approval": round(avg_conf, 4),
                "suggested_fix": (
                    "raise auto-approve threshold or add field-level "
                    "confidence gates"
                ),
            },
            summary=summary,
            dry_run=dry_run,
        )

    def _rule_validation_failure_cluster(
        self, base_qs, summary: EngineRunSummary, *, dry_run: bool,
    ) -> None:
        """RULE 4: Detect repeated validation failures with same patterns."""
        qs = base_qs.filter(signal_type="validation_failure")

        # Group by app_module and look for repeated error patterns
        by_module = (
            qs.values("app_module")
            .annotate(cnt=Count("id"))
            .filter(cnt__gte=VALIDATION_CLUSTER_MIN_COUNT)
        )

        for row in by_module:
            app_module = row["app_module"]
            total_failures = row["cnt"]

            # Extract error text from payloads to find clusters
            module_signals = qs.filter(app_module=app_module).values(
                "payload_json",
            ).order_by("-created_at")[:200]

            error_counter: Counter = Counter()
            for sig in module_signals:
                payload = sig.get("payload_json") or {}
                error_text = str(payload.get("error", ""))[:100]
                if error_text:
                    error_counter[error_text] += 1

            # Only propose for clusters above threshold
            for error_text, err_count in error_counter.most_common(5):
                if err_count < VALIDATION_CLUSTER_MIN_COUNT:
                    continue

                dedup_key = (
                    f"validation_cluster:{app_module}:"
                    f"{hashlib.md5(error_text.encode()).hexdigest()[:12]}"
                )
                self._propose_action(
                    action_type="validation_rule_candidate",
                    app_module=app_module,
                    dedup_key=dedup_key,
                    target_description=(
                        f"Validation error repeated {err_count} times: "
                        f"'{error_text[:80]}...'"
                    ),
                    rationale=(
                        f"The same validation failure pattern appeared "
                        f"{err_count} times in module '{app_module}' "
                        f"over the last {self.days} days ({total_failures} "
                        f"total failures). Consider adding a targeted "
                        f"validation rule or pre-processing step."
                    ),
                    input_signals_json={
                        "rule": "validation_failure_cluster",
                        "error_pattern": error_text,
                        "occurrence_count": err_count,
                        "total_failures_in_module": total_failures,
                        "time_window_days": self.days,
                    },
                    action_payload_json={
                        "error_pattern": error_text,
                        "occurrence_count": err_count,
                        "suggested_fix": (
                            "add pre-processing normalization or "
                            "targeted validation rule"
                        ),
                    },
                    summary=summary,
                    dry_run=dry_run,
                )

    def _rule_vendor_specific_issue(
        self, base_qs, summary: EngineRunSummary, *, dry_run: bool,
    ) -> None:
        """RULE 5: Detect corrections clustering around specific vendors."""
        qs = base_qs.filter(signal_type="field_correction")

        # Group by aggregation_key (which often encodes vendor context)
        by_key = (
            qs.exclude(aggregation_key="")
            .values("aggregation_key", "app_module")
            .annotate(
                cnt=Count("id"),
                avg_conf=Avg("confidence"),
            )
            .filter(cnt__gte=VENDOR_ISSUE_MIN_COUNT)
            .order_by("-cnt")
        )

        for row in by_key:
            agg_key = row["aggregation_key"]
            app_module = row["app_module"]
            count = row["cnt"]

            # Top corrected fields for this vendor/key
            top_fields = list(
                qs.filter(aggregation_key=agg_key, app_module=app_module)
                .exclude(field_name="")
                .values("field_name")
                .annotate(cnt=Count("id"))
                .order_by("-cnt")[:5]
            )

            dedup_key = (
                f"vendor_issue:{app_module}:"
                f"{hashlib.md5(agg_key.encode()).hexdigest()[:12]}"
            )
            self._propose_action(
                action_type="vendor_rule_candidate",
                app_module=app_module,
                dedup_key=dedup_key,
                target_description=(
                    f"Vendor/group '{agg_key}' has {count} corrections "
                    f"in the last {self.days} days"
                ),
                rationale=(
                    f"Aggregation key '{agg_key}' in module '{app_module}' "
                    f"accumulated {count} field corrections. "
                    f"Average confidence: {row['avg_conf']:.2f}. "
                    f"Consider adding vendor-specific normalization rules "
                    f"or alias mappings."
                ),
                input_signals_json={
                    "rule": "vendor_specific_issue",
                    "aggregation_key": agg_key,
                    "correction_count": count,
                    "avg_confidence": round(row["avg_conf"] or 0.0, 4),
                    "time_window_days": self.days,
                },
                action_payload_json={
                    "aggregation_key": agg_key,
                    "correction_count": count,
                    "top_corrected_fields": [
                        {"field": f["field_name"], "count": f["cnt"]}
                        for f in top_fields
                    ],
                    "suggested_fix": (
                        "add vendor-specific alias or normalization rule"
                    ),
                },
                summary=summary,
                dry_run=dry_run,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_queryset(self, module: str = ""):
        """Return the base LearningSignal queryset filtered by time + confidence."""
        qs = LearningSignal.objects.filter(created_at__gte=self._cutoff)
        if self.min_confidence > 0:
            qs = qs.filter(confidence__gte=self.min_confidence)
        if module:
            qs = qs.filter(app_module=module)
        return qs

    def _propose_action(
        self,
        *,
        action_type: str,
        app_module: str,
        dedup_key: str,
        target_description: str,
        rationale: str,
        input_signals_json: dict,
        action_payload_json: dict,
        summary: EngineRunSummary,
        dry_run: bool,
    ) -> Optional[LearningAction]:
        """Create a LearningAction if not duplicated or on cooldown.

        Dedup logic:
        1. If an open (PROPOSED/APPROVED) action with the same dedup_key exists
           -> skip (dedup).
        2. If the last action with the same dedup_key was created within
           cooldown_days -> skip (cooldown).

        The dedup_key is stored as a ``[dedup_key:...]`` tag at the end of
        ``target_description`` so lookups work on all DB backends (SQLite
        included) without requiring JSONField __contains.
        """
        dedup_tag = f"[dedup_key:{dedup_key}]"
        # Store dedup_key in payload for posterity
        action_payload_json["_dedup_key"] = dedup_key
        target_description_full = f"{target_description} {dedup_tag}"

        # Check for open (PROPOSED or APPROVED) action with same dedup_key
        open_exists = LearningAction.objects.filter(
            action_type=action_type,
            status__in=[
                LearningAction.Status.PROPOSED,
                LearningAction.Status.APPROVED,
            ],
            target_description__contains=dedup_tag,
        ).exists()

        if open_exists:
            summary.actions_skipped_dedup += 1
            summary.details.append(
                f"DEDUP: {action_type} / {dedup_key}"
            )
            return None

        # Cooldown: skip if a recent (any status) action with same dedup_key
        recent_exists = LearningAction.objects.filter(
            action_type=action_type,
            target_description__contains=dedup_tag,
            created_at__gte=self._cooldown_cutoff,
        ).exists()

        if recent_exists:
            summary.actions_skipped_cooldown += 1
            summary.details.append(
                f"COOLDOWN: {action_type} / {dedup_key}"
            )
            return None

        if dry_run:
            summary.actions_proposed += 1
            summary.details.append(
                f"DRY-RUN WOULD PROPOSE: {action_type} / {dedup_key}"
            )
            return None

        from apps.core_eval.services.learning_action_service import LearningActionService

        action = LearningActionService.propose(
            action_type=action_type,
            app_module=app_module,
            target_description=target_description_full,
            rationale=rationale,
            input_signals_json=input_signals_json,
            action_payload_json=action_payload_json,
        )

        summary.actions_proposed += 1
        summary.details.append(
            f"PROPOSED: {action_type} / {dedup_key} -> LearningAction#{action.pk}"
        )
        logger.info(
            "LearningEngine proposed action: type=%s dedup_key=%s pk=%s",
            action_type, dedup_key, action.pk,
        )

        try:
            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType

            AuditService.log_event(
                entity_type="LearningAction",
                entity_id=action.pk,
                event_type=AuditEventType.LEARNING_ACTION_PROPOSED,
                description=f"Learning engine proposed {action_type}: {dedup_key}",
                agent="LearningEngine",
                metadata={
                    "action_type": action_type,
                    "app_module": app_module,
                    "dedup_key": dedup_key,
                },
            )
        except Exception:
            logger.debug("Audit log for LEARNING_ACTION_PROPOSED failed (non-fatal)")

        return action
