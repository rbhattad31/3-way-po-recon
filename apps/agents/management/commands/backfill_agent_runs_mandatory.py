from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.agents.models import AgentRun
from apps.core.enums import AgentRunStatus, AgentType


class Command(BaseCommand):
    help = "Backfill mandatory observability fields on AgentRun (confidence, role, model, trigger, tokens)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to DB.",
        )

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return ""

    @staticmethod
    def _parse_confidence(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        if parsed < 0.0:
            return 0.0
        if parsed > 1.0:
            return 1.0
        return parsed

    @staticmethod
    def _to_int(value: Any):
        try:
            if value is None:
                return None
            parsed = int(value)
            return parsed if parsed >= 0 else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _estimate_tokens(cls, input_payload: Any, output_payload: Any):
        input_text = str(input_payload or "")
        output_text = str(output_payload or "")
        prompt_tokens = max(1, int((len(input_text) / 4.0) + 0.5))
        completion_tokens = max(1, int((len(output_text) / 4.0) + 0.5))
        return prompt_tokens, completion_tokens, prompt_tokens + completion_tokens

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))

        system_agent_types = {
            AgentType.SYSTEM_REVIEW_ROUTING,
            AgentType.SYSTEM_CASE_SUMMARY,
            AgentType.SYSTEM_BULK_EXTRACTION_INTAKE,
            AgentType.SYSTEM_CASE_INTAKE,
            AgentType.SYSTEM_POSTING_PREPARATION,
        }

        missing_q = (
            Q(confidence__isnull=True)
            | Q(actor_primary_role="")
            | Q(llm_model_used="")
            | Q(invocation_reason="")
            | Q(prompt_tokens__isnull=True)
            | Q(completion_tokens__isnull=True)
            | Q(total_tokens__isnull=True)
        )
        benchmark_legacy_q = Q(agent_type=AgentType.PROCUREMENT_BENCHMARK) & (
            Q(llm_model_used__iexact="deterministic")
            | Q(llm_model_used__iexact="unknown")
        )

        qs = AgentRun.objects.filter(missing_q | benchmark_legacy_q).order_by("pk")
        total_candidates = qs.count()
        updated = 0

        self.stdout.write(self.style.WARNING(f"Found {total_candidates} candidate AgentRun rows."))

        for run in qs.iterator(chunk_size=500):
            changed_fields = []
            input_payload = run.input_payload or {}
            output_payload = run.output_payload or {}
            usage = output_payload.get("llm_usage") or output_payload.get("usage") or {}

            if run.confidence is None:
                inferred_conf = self._first_non_empty(
                    output_payload.get("confidence"),
                    output_payload.get("confidence_score"),
                    usage.get("confidence"),
                )
                if run.status in {AgentRunStatus.RUNNING, AgentRunStatus.PENDING} and inferred_conf == "":
                    run.confidence = 0.0
                else:
                    run.confidence = self._parse_confidence(inferred_conf)
                changed_fields.append("confidence")

            if not (run.actor_primary_role or "").strip():
                run.actor_primary_role = "SYSTEM_AGENT" if not run.actor_user_id else "USER"
                changed_fields.append("actor_primary_role")

            if not (run.llm_model_used or "").strip():
                inferred_model = self._first_non_empty(
                    output_payload.get("llm_model_used"),
                    output_payload.get("model_used"),
                    output_payload.get("llm_model"),
                    output_payload.get("model_name"),
                    output_payload.get("model"),
                    usage.get("model"),
                    usage.get("model_name"),
                )
                if not inferred_model and run.agent_type in system_agent_types:
                    inferred_model = "deterministic"
                if (
                    not inferred_model
                    and run.agent_type == AgentType.PROCUREMENT_MARKET_INTELLIGENCE
                    and output_payload.get("source_reference_label") == "Perplexity Source References"
                ):
                    inferred_model = getattr(settings, "PERPLEXITY_MODEL", "sonar")
                if not inferred_model and run.agent_type == AgentType.PROCUREMENT_BENCHMARK:
                    inferred_model = (
                        getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "")
                        or getattr(settings, "LLM_MODEL_NAME", "")
                        or "unknown"
                    )
                run.llm_model_used = inferred_model or "unknown"
                changed_fields.append("llm_model_used")
            elif (
                run.agent_type == AgentType.PROCUREMENT_BENCHMARK
                and str(run.llm_model_used).strip().lower() in {"deterministic", "unknown"}
            ):
                run.llm_model_used = (
                    getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "")
                    or getattr(settings, "LLM_MODEL_NAME", "")
                    or "unknown"
                )
                changed_fields.append("llm_model_used")

            if not (run.invocation_reason or "").strip():
                inferred_reason = self._first_non_empty(
                    input_payload.get("source"),
                    input_payload.get("requested_agent_type"),
                    output_payload.get("source"),
                    output_payload.get("agent_name"),
                )
                run.invocation_reason = inferred_reason or f"{run.agent_type}:legacy_backfill"
                changed_fields.append("invocation_reason")

            usage = output_payload.get("llm_usage") or output_payload.get("usage") or {}
            if run.prompt_tokens is None:
                prompt_tokens = self._to_int(output_payload.get("prompt_tokens"))
                if prompt_tokens is None and isinstance(usage, dict):
                    prompt_tokens = self._to_int(usage.get("prompt_tokens"))
                if prompt_tokens is None and run.status not in {AgentRunStatus.RUNNING, AgentRunStatus.PENDING}:
                    prompt_tokens = self._estimate_tokens(input_payload, output_payload)[0]
                if prompt_tokens is not None:
                    run.prompt_tokens = prompt_tokens
                    changed_fields.append("prompt_tokens")

            if run.completion_tokens is None:
                completion_tokens = self._to_int(output_payload.get("completion_tokens"))
                if completion_tokens is None and isinstance(usage, dict):
                    completion_tokens = self._to_int(usage.get("completion_tokens"))
                if completion_tokens is None and run.status not in {AgentRunStatus.RUNNING, AgentRunStatus.PENDING}:
                    completion_tokens = self._estimate_tokens(input_payload, output_payload)[1]
                if completion_tokens is not None:
                    run.completion_tokens = completion_tokens
                    changed_fields.append("completion_tokens")

            if run.total_tokens is None:
                total_tokens = self._to_int(output_payload.get("total_tokens"))
                if total_tokens is None and isinstance(usage, dict):
                    total_tokens = self._to_int(usage.get("total_tokens"))
                if total_tokens is None and (
                    run.prompt_tokens is not None or run.completion_tokens is not None
                ):
                    total_tokens = (run.prompt_tokens or 0) + (run.completion_tokens or 0)
                if total_tokens is None and run.status not in {AgentRunStatus.RUNNING, AgentRunStatus.PENDING}:
                    total_tokens = self._estimate_tokens(input_payload, output_payload)[2]
                if total_tokens is not None:
                    run.total_tokens = total_tokens
                    changed_fields.append("total_tokens")

            if changed_fields:
                updated += 1
                if not dry_run:
                    run.save(update_fields=list(dict.fromkeys(changed_fields + ["updated_at"])))

        mode = "DRY-RUN" if dry_run else "UPDATED"
        self.stdout.write(self.style.SUCCESS(f"{mode}: {updated} AgentRun rows processed."))
