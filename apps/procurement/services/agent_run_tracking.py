"""Helpers to execute procurement component agents with AgentRun tracking."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional, Tuple

from django.utils import timezone

from apps.agents.models import AgentRun
from apps.agents.services.base_agent import BaseAgent
from apps.core.enums import AgentRunStatus


def run_procurement_component_with_tracking(
	*,
	agent_type: str,
	invocation_reason: str,
	execute_fn: Callable[[], Any],
	input_payload: Optional[Dict[str, Any]] = None,
	actor_user: Any = None,
	tenant: Any = None,
	trace_id: str = "",
	span_id: str = "",
) -> Any:
	"""Execute a component function and persist AgentRun lifecycle metadata."""
	started_at = timezone.now()
	run = AgentRun.objects.create(
		tenant=tenant,
		agent_type=agent_type,
		status=AgentRunStatus.RUNNING,
		confidence=0.0,
		llm_model_used="unknown",
		input_payload=input_payload or {},
		invocation_reason=invocation_reason,
		actor_user_id=getattr(actor_user, "pk", None),
		actor_primary_role=(getattr(actor_user, "role", "") or "USER") if actor_user else "SYSTEM_AGENT",
		access_granted=True,
		started_at=started_at,
		trace_id=(trace_id or ""),
		span_id=(span_id or ""),
	)

	start_ts = time.monotonic()
	try:
		result = execute_fn()
		llm_model_used, prompt_tokens, completion_tokens, total_tokens = _extract_usage(result)
		run.status = AgentRunStatus.COMPLETED
		run.output_payload = _json_safe(result)
		run.summarized_reasoning = _extract_summary_text(result)
		run.confidence = _extract_confidence(result)
		run.llm_model_used = llm_model_used or "unknown"
		run.prompt_tokens = prompt_tokens
		run.completion_tokens = completion_tokens
		run.total_tokens = total_tokens
		run.completed_at = timezone.now()
		run.duration_ms = int((time.monotonic() - start_ts) * 1000)
		BaseAgent._calculate_actual_cost(run)
		run.save(update_fields=[
			"status",
			"output_payload",
			"summarized_reasoning",
			"confidence",
			"llm_model_used",
			"prompt_tokens",
			"completion_tokens",
			"total_tokens",
			"actual_cost_usd",
			"completed_at",
			"duration_ms",
			"updated_at",
		])
		return result
	except Exception as exc:
		run.status = AgentRunStatus.FAILED
		run.error_message = str(exc)
		run.completed_at = timezone.now()
		run.duration_ms = int((time.monotonic() - start_ts) * 1000)
		run.save(update_fields=["status", "error_message", "completed_at", "duration_ms", "updated_at"])
		raise


def _extract_summary_text(result: Any) -> str:
	if not isinstance(result, dict):
		return ""
	for key in ("reasoning_summary", "summary", "reasoning", "error"):
		value = result.get(key)
		if isinstance(value, str) and value.strip():
			return BaseAgent._sanitise_text(value)[:2000]
	return ""


def _extract_confidence(result: Any) -> float:
	if not isinstance(result, dict):
		return 0.0
	for key in ("confidence", "overall_confidence"):
		value = result.get(key)
		try:
			if value is not None:
				return max(0.0, min(1.0, float(value)))
		except (TypeError, ValueError):
			pass
	return 0.0


def _extract_usage(result: Any) -> Tuple[str, Optional[int], Optional[int], Optional[int]]:
	if not isinstance(result, dict):
		return "", None, None, None

	model = ""
	for key in ("llm_model_used", "model_used", "llm_model", "model_name", "model"):
		value = result.get(key)
		if isinstance(value, str) and value.strip():
			model = value.strip()
			break

	usage = result.get("llm_usage") if isinstance(result.get("llm_usage"), dict) else {}
	usage_alt = result.get("usage") if isinstance(result.get("usage"), dict) else {}

	prompt_tokens = _to_non_negative_int(result.get("prompt_tokens"))
	completion_tokens = _to_non_negative_int(result.get("completion_tokens"))
	total_tokens = _to_non_negative_int(result.get("total_tokens"))

	if prompt_tokens is None:
		prompt_tokens = _to_non_negative_int(usage.get("prompt_tokens"))
	if completion_tokens is None:
		completion_tokens = _to_non_negative_int(usage.get("completion_tokens"))
	if total_tokens is None:
		total_tokens = _to_non_negative_int(usage.get("total_tokens"))

	if prompt_tokens is None:
		prompt_tokens = _to_non_negative_int(usage_alt.get("prompt_tokens"))
	if completion_tokens is None:
		completion_tokens = _to_non_negative_int(usage_alt.get("completion_tokens"))
	if total_tokens is None:
		total_tokens = _to_non_negative_int(usage_alt.get("total_tokens"))

	if not model:
		for usage_payload in (usage, usage_alt):
			value = usage_payload.get("model") or usage_payload.get("model_name")
			if isinstance(value, str) and value.strip():
				model = value.strip()
				break

	return model, prompt_tokens, completion_tokens, total_tokens


def _to_non_negative_int(value: Any) -> Optional[int]:
	if value is None:
		return None
	try:
		parsed = int(value)
		return parsed if parsed >= 0 else None
	except (TypeError, ValueError):
		return None


def _json_safe(value: Any) -> Any:
	try:
		return json.loads(json.dumps(value, default=str))
	except Exception:
		return value
