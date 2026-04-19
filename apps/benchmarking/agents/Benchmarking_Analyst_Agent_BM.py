"""Benchmarking analyst agent for final domain-level recommendation synthesis."""

from __future__ import annotations

import json
import logging

from apps.agents.services.llm_client import LLMClient, LLMMessage


logger = logging.getLogger(__name__)


class BenchmarkingAnalystAgentBM:
    """Produce executive benchmark analysis across sub-agent outputs."""

    @classmethod
    def summarize(
        cls,
        *,
        result,
        market_analysis: dict,
        compliance_assessment: dict,
        vendor_recommendation: dict,
        vendor_cards: list[dict],
    ) -> dict:
        deterministic = cls._summarize_deterministic(
            result=result,
            market_analysis=market_analysis,
            compliance_assessment=compliance_assessment,
            vendor_recommendation=vendor_recommendation,
            vendor_cards=vendor_cards,
        )
        llm_output = cls._summarize_with_llm(deterministic=deterministic)
        if llm_output:
            return llm_output
        return deterministic

    @classmethod
    def _summarize_with_llm(cls, *, deterministic: dict) -> dict | None:
        payload = {
            "task": "Produce benchmarking analyst final decision.",
            "rules": [
                "Decision must be one of APPROVE_VENDOR_SELECTION, REVIEW_REQUIRED, REQUEST_REQUOTE.",
                "Use only provided facts.",
                "Keep summary concise and procurement-ready.",
                "Return strict JSON only.",
            ],
            "input": deterministic,
            "required_output_schema": {
                "decision": "APPROVE_VENDOR_SELECTION|REVIEW_REQUIRED|REQUEST_REQUOTE",
                "confidence": "float_0_to_1",
                "summary": "string",
                "recommended_vendor": "string",
                "recommended": "bool",
                "next_action": "string",
            },
        }
        try:
            llm = LLMClient(temperature=0.0, max_tokens=1000)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You are a procurement benchmarking analyst. Return only JSON.",
                    ),
                    LLMMessage(role="user", content=json.dumps(payload)),
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.content or "")
            if not isinstance(parsed, dict):
                return None

            decision = str(parsed.get("decision") or "").strip().upper()
            if decision not in {"APPROVE_VENDOR_SELECTION", "REVIEW_REQUIRED", "REQUEST_REQUOTE"}:
                return None

            try:
                confidence = float(parsed.get("confidence"))
            except Exception:
                confidence = float(deterministic.get("confidence") or 0.7)
            confidence = max(0.0, min(1.0, confidence))

            summary = str(parsed.get("summary") or "").strip() or str(deterministic.get("summary") or "")
            recommended_vendor = str(parsed.get("recommended_vendor") or "").strip()
            if not recommended_vendor:
                recommended_vendor = str(deterministic.get("recommended_vendor") or "")

            recommended = bool(parsed.get("recommended"))
            next_action = str(parsed.get("next_action") or "").strip() or str(deterministic.get("next_action") or "")

            return {
                "decision": decision,
                "confidence": confidence,
                "summary": summary,
                "recommended_vendor": recommended_vendor,
                "recommended": recommended,
                "next_action": next_action,
                "inputs": deterministic.get("inputs") or {},
            }
        except Exception:
            logger.exception("Benchmarking Analyst LLM path failed; using deterministic fallback")
            return None

    @classmethod
    def _summarize_deterministic(
        cls,
        *,
        result,
        market_analysis: dict,
        compliance_assessment: dict,
        vendor_recommendation: dict,
        vendor_cards: list[dict],
    ) -> dict:
        total_vendors = len(vendor_cards)
        overall_dev = getattr(result, "overall_deviation_pct", None)
        overall_status = getattr(result, "overall_status", "NEEDS_REVIEW")

        recommended = bool(vendor_recommendation.get("recommended"))
        recommended_vendor = vendor_recommendation.get("best_vendor_name", "")
        compliance_status = (compliance_assessment or {}).get("status", "UNKNOWN")

        confidence_parts = [
            float((market_analysis or {}).get("confidence", 0.0) or 0.0),
            float((compliance_assessment or {}).get("confidence", 0.0) or 0.0),
            float((vendor_recommendation or {}).get("confidence", 0.0) or 0.0),
        ]
        confidence = round(sum(confidence_parts) / max(len(confidence_parts), 1), 3)

        if recommended and compliance_status in {"PASS", "PARTIAL"}:
            decision = "APPROVE_VENDOR_SELECTION"
            next_action = "Proceed with commercial negotiation using generated benchmark notes."
        elif compliance_status == "FAIL":
            decision = "REVIEW_REQUIRED"
            next_action = "Resolve compliance violations before selecting a vendor."
        else:
            decision = "REQUEST_REQUOTE"
            next_action = "Ask vendors for revised pricing with stronger benchmark alignment."

        summary = (
            f"Benchmarking analyst reviewed {total_vendors} vendor quotation(s). "
            f"Overall deviation is {overall_dev if overall_dev is not None else 'n/a'}% "
            f"with status {overall_status}. Compliance is {compliance_status}. "
            f"Recommended vendor: {recommended_vendor or 'none'}."
        )

        return {
            "decision": decision,
            "confidence": confidence,
            "summary": summary,
            "recommended_vendor": recommended_vendor,
            "recommended": recommended,
            "next_action": next_action,
            "inputs": {
                "market_analysis": market_analysis,
                "compliance_assessment": compliance_assessment,
                "vendor_recommendation": vendor_recommendation,
            },
        }
