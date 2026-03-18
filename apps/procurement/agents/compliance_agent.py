"""ComplianceAgent — AI-augmented compliance checking.

Only invoked when the rule-based ComplianceService needs extended analysis
(e.g., checking domain-specific regulations or complex constraint sets).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.procurement.models import ProcurementRequest

logger = logging.getLogger(__name__)


class ComplianceAgent:
    """AI-powered compliance analysis for procurement decisions."""

    SYSTEM_PROMPT = (
        "You are a procurement compliance analyst. Given a procurement recommendation "
        "and the request context, check for compliance issues.\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        '  "status": "PASS" | "FAIL" | "PARTIAL",\n'
        '  "rules_checked": [{"rule": "...", "description": "..."}],\n'
        '  "violations": [{"rule": "...", "detail": "..."}],\n'
        '  "recommendations": ["..."]\n'
        "}"
    )

    @staticmethod
    def check(
        request: ProcurementRequest,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        llm = LLMClient()

        user_msg = (
            f"Domain: {request.domain_code}\n"
            f"Geography: {request.geography_country}\n"
            f"Context:\n{json.dumps(context, indent=2, default=str)}\n\n"
            "Perform compliance analysis."
        )

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=ComplianceAgent.SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ],
            )
            return json.loads(response.content)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("ComplianceAgent failed: %s", exc)
            return {
                "status": "NOT_CHECKED",
                "rules_checked": [],
                "violations": [],
                "recommendations": [f"Compliance check failed: {exc}"],
            }
