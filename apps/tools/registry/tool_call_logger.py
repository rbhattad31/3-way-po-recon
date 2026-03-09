"""Tool call logging service — persists every agent tool invocation for audit."""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from apps.agents.models import AgentRun
from apps.core.enums import ToolCallStatus
from apps.tools.models import ToolCall, ToolDefinition
from apps.tools.registry.base import ToolResult

logger = logging.getLogger(__name__)


def _safe_json(obj: Any) -> Any:
    """Make an object JSON-serialisable (Decimals → str)."""
    if obj is None:
        return None
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return json.loads(json.dumps(obj, default=_fallback_serialise))


def _fallback_serialise(o):
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


class ToolCallLogger:
    """Persist a ToolCall row for every tool invocation."""

    @staticmethod
    def log(
        agent_run: AgentRun,
        tool_name: str,
        input_payload: Optional[Dict[str, Any]],
        result: ToolResult,
    ) -> ToolCall:
        tool_def = ToolDefinition.objects.filter(name=tool_name).first()

        status = ToolCallStatus.SUCCESS if result.success else ToolCallStatus.FAILED

        tc = ToolCall.objects.create(
            agent_run=agent_run,
            tool_definition=tool_def,
            tool_name=tool_name,
            status=status,
            input_payload=_safe_json(input_payload),
            output_payload=_safe_json(result.data),
            error_message=result.error or "",
            duration_ms=result.duration_ms,
        )

        logger.info(
            "ToolCall logged: run=%s tool=%s status=%s duration=%dms",
            agent_run.pk, tool_name, status, result.duration_ms,
        )
        return tc
