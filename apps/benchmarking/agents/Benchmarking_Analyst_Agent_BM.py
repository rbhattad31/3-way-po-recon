"""Benchmarking analyst agent for final domain-level recommendation synthesis."""

from __future__ import annotations


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
