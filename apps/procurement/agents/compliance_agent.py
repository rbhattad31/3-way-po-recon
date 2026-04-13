"""ComplianceAgent -- AI-augmented procurement compliance analysis.

Invoked by RecommendationService step 5 when the rule-based ComplianceService
returns PARTIAL or when confidence-weighted risk signals require deeper analysis.

The agent does NOT replace the rule-based checks -- it augments them with
domain knowledge the rules engine cannot cover (e.g. complex ASHRAE trade-offs,
conflict-of-interest patterns, jurisdiction-specific regulatory nuances).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from apps.agents.services.base_agent import BaseAgent
from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.procurement.models import ProcurementRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt -- domain-aware
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a senior procurement compliance analyst specialising in capital expenditure
and technical equipment procurement. Your role is to review a procurement recommendation
and identify compliance risks the rule-based engine may have missed.

DOMAINS you cover:
1. HVAC procurement
   - ASHRAE 90.1 energy efficiency requirements
   - Refrigerant selection (phase-down regulations, F-Gas, Kigali amendment)
   - Ventilation adequacy (DOAS requirement for high fresh-air zones)
   - Anti-corrosion and dehumidification for coastal/high-humidity sites
   - Local authority pre-approval: UAE (DEWA, Civil Defence, DCD/Trakhees),
     KSA (SASO, MOMRA, SEC), Qatar (QCDD, Kahramaa), India (BEE star rating)

2. General procurement
   - Three-bid / competitive quotation requirement (minimum 3 suppliers)
   - Single-source justification: must be documented and authorised
   - Anti-collusion: identical prices from different vendors is a red flag
   - Budget threshold approval: escalation triggers at different spend levels
   - Conflict-of-interest: related-party vendor relationships must be disclosed
   - Split-order risk: artificially splitting orders to avoid approval thresholds

3. Geography-specific
   - UAE/GCC: local-content preference requirements, ADIMCO/DEWA pre-approval
   - KSA: SASO product certification, Saudisation/IKTVA supplier requirements
   - India: BEE star-rating label, Make-in-India preference for eligible categories
   - Qatar: NPRP/QCDD certification where applicable

ANALYSIS APPROACH:
- Review the provided rule_violations (from the rule engine) for context
- Identify additional compliance risks not already flagged
- Rate severity: HIGH (blocks award), MEDIUM (must resolve before PO), LOW (advisory)
- Be specific: name the regulation, standard, or policy being violated

OUTPUT FORMAT -- respond ONLY with valid JSON (no markdown, no preamble):
{
  "status": "PASS" | "PARTIAL" | "FAIL",
  "rules_checked": [
    {"rule": "<snake_case_rule_id>", "description": "<one-line description>"}
  ],
  "violations": [
    {
      "rule": "<snake_case_rule_id>",
      "detail": "<specific finding>",
      "severity": "HIGH" | "MEDIUM" | "LOW"
    }
  ],
  "recommendations": ["<actionable recommendation>"],
  "domain_flags": ["<domain-specific observation>"],
  "geography_flags": ["<geography-specific observation>"]
}

Rules:
- "status" is FAIL if any HIGH severity violation exists
- "status" is PARTIAL if only MEDIUM/LOW violations exist
- "status" is PASS if no violations
- Keep "rules_checked" to checks you actually performed (not the rule engine's checks)
- Keep "violations" and "recommendations" concise and actionable
- If no issues found beyond what the rule engine already flagged, return status=PASS
  with an empty violations list
"""


class ComplianceAgent:
    """AI-powered compliance analysis for procurement decisions.

    Augments the rule-based ComplianceService for PARTIAL results and complex
    domain-specific checks.
    """

    @staticmethod
    def check(
        request: ProcurementRequest,
        context: Dict[str, Any],
        attrs: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Run AI compliance analysis.

        Args:
            request:  The ProcurementRequest being evaluated.
            context:  Dict containing the merged recommendation result and any
                      rule-based violations already detected.
            attrs:    Optional pre-fetched attributes dict (avoids double DB hit).

        Returns a dict:
          {status, rules_checked, violations, recommendations,
           domain_flags, geography_flags}
        """
        llm = LLMClient()

        # Build a compact representation of the context for the LLM
        user_payload: Dict[str, Any] = {
            "domain": request.domain_code or "GENERAL",
            "geography_country": request.geography_country or "UNKNOWN",
            "recommendation": {
                "recommended_option": context.get("recommended_option"),
                "confidence": context.get("confidence"),
                "estimated_cost": context.get("estimated_cost"),
                "reasoning_summary": context.get("reasoning_summary"),
                "constraints": context.get("constraints") or [],
                "notes": context.get("notes") or [],
                "standards_notes": context.get("standards_notes") or "",
            },
            "rule_violations_already_detected": context.get("violations") or [],
            "quotation_count": request.quotations.count(),
        }
        if attrs:
            # Include key technical attributes for HVAC domain checks
            hvac_keys = [
                "humidity_level", "dust_exposure", "fresh_air_requirement",
                "system_type", "refrigerant_type", "area_sqft",
                "required_standards_local_notes", "heat_load_category",
                "landlord_constraints",
            ]
            user_payload["technical_attributes"] = {
                k: attrs[k] for k in hvac_keys if k in attrs
            }

        user_msg = (
            "Perform a compliance analysis on the following procurement recommendation. "
            "Focus on risks NOT already captured in 'rule_violations_already_detected'.\n\n"
            + json.dumps(user_payload, indent=2, default=str)
        )

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ],
            )
            raw = response.content.strip()

            # Strip accidental markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("ComplianceAgent: JSON parse failed: %s", exc)
            return ComplianceAgent._fallback("JSON parse error", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ComplianceAgent: LLM call failed: %s", exc)
            return ComplianceAgent._fallback("LLM call failed", str(exc))

        return ComplianceAgent._normalise(parsed)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(parsed: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure the parsed response has all required keys with correct types."""
        valid_statuses = {"PASS", "PARTIAL", "FAIL", "NOT_CHECKED"}
        status_raw = str(parsed.get("status") or "NOT_CHECKED").upper()
        status = status_raw if status_raw in valid_statuses else "NOT_CHECKED"

        def _coerce_list(val: Any) -> List:
            if isinstance(val, list):
                return val
            return []

        return {
            "status": status,
            "rules_checked": _coerce_list(parsed.get("rules_checked")),
            "violations": _coerce_list(parsed.get("violations")),
            "recommendations": [
                BaseAgent._sanitise_text(str(v)) for v in _coerce_list(parsed.get("recommendations"))
            ],
            "domain_flags": [
                BaseAgent._sanitise_text(str(v)) for v in _coerce_list(parsed.get("domain_flags"))
            ],
            "geography_flags": [
                BaseAgent._sanitise_text(str(v)) for v in _coerce_list(parsed.get("geography_flags"))
            ],
            "ai_augmented": True,
        }

    @staticmethod
    def _fallback(reason: str, detail: str) -> Dict[str, Any]:
        """Return a safe NOT_CHECKED response with the failure reason recorded."""
        return {
            "status": "NOT_CHECKED",
            "rules_checked": [],
            "violations": [],
            "recommendations": [BaseAgent._sanitise_text(f"AI compliance check skipped ({reason}): {detail}")],
            "domain_flags": [],
            "geography_flags": [],
            "ai_augmented": False,
        }
