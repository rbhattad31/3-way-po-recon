"""Vendor recommendation agent for benchmarking domain."""

from __future__ import annotations

import json
import logging

from apps.agents.services.llm_client import LLMClient, LLMMessage


logger = logging.getLogger(__name__)


class BenchmarkVendorRecommendationAgent:
    """Select best vendor (or no-vendor) and produce reason summary."""

    DEVIATION_THRESHOLD_PCT = 15.0
    HIGH_VARIANCE_RATIO_THRESHOLD = 0.25

    @classmethod
    def recommend(cls, vendor_cards: list[dict]) -> dict:
        if not vendor_cards:
            return {
                "recommended": False,
                "quotation_id": None,
                "best_vendor_name": "",
                "score": 0.0,
                "confidence": 0.4,
                "summary": "No vendor quotations are available for benchmarking.",
                "market_standards": cls.market_standards(),
            }

        ranked = []
        for card in vendor_cards:
            deviation = card.get("deviation_pct")
            status_counts = card.get("status_counts", {})
            high_count = int(status_counts.get("HIGH", 0) or 0)
            review_count = int(status_counts.get("NEEDS_REVIEW", 0) or 0)
            live_refs = int(card.get("live_reference_count", 0) or 0)
            line_count = max(int(card.get("line_count", 0) or 0), 1)
            benchmarked_line_count = max(int(card.get("benchmarked_line_count", 0) or 0), 0)
            ratio_base = max(benchmarked_line_count, 1)

            score = 100.0
            if deviation is not None:
                score -= abs(float(deviation)) * 1.8
            else:
                score -= 25.0

            score -= high_count * 8.0
            score -= review_count * 3.0
            score += min(live_refs, 8) * 1.5

            high_ratio = high_count / float(ratio_base)
            eligible = (
                deviation is not None
                and benchmarked_line_count > 0
                and abs(float(deviation)) <= cls.DEVIATION_THRESHOLD_PCT
                and high_ratio <= cls.HIGH_VARIANCE_RATIO_THRESHOLD
            )

            ranked.append({
                "card": card,
                "score": round(score, 2),
                "eligible": eligible,
            })

        ranked.sort(key=lambda x: x["score"], reverse=True)
        llm_result = cls._recommend_with_llm(ranked=ranked)
        if llm_result:
            return llm_result

        best = ranked[0]
        best_card = best["card"]
        best_vendor_name = (best_card.get("supplier_name") or "Top vendor").strip() or "Top vendor"
        deviation = best_card.get("deviation_pct")
        status_counts = best_card.get("status_counts", {})
        high_count = int(status_counts.get("HIGH", 0) or 0)
        live_refs = int(best_card.get("live_reference_count", 0) or 0)
        line_count = max(int(best_card.get("line_count", 0) or 0), 1)
        benchmarked_line_count = int(best_card.get("benchmarked_line_count", 0) or 0)
        high_ratio = high_count / float(max(benchmarked_line_count, 1))

        standards = cls.market_standards(
            deviation=deviation,
            high_count=high_count,
            benchmarked_line_count=benchmarked_line_count,
            line_count=line_count,
            live_refs=live_refs,
        )

        if not best["eligible"]:
            failed_checks = []
            if benchmarked_line_count <= 0 or deviation is None:
                failed_checks.append("benchmark alignment unavailable")
            elif abs(float(deviation)) > cls.DEVIATION_THRESHOLD_PCT:
                failed_checks.append(
                    f"absolute deviation {abs(float(deviation)):.2f}% exceeds {cls.DEVIATION_THRESHOLD_PCT:.0f}%"
                )
            if high_ratio > cls.HIGH_VARIANCE_RATIO_THRESHOLD:
                failed_checks.append(
                    (
                        f"HIGH-variance ratio {high_ratio * 100.0:.1f}% exceeds "
                        f"{cls.HIGH_VARIANCE_RATIO_THRESHOLD * 100.0:.0f}%"
                    )
                )
            if not failed_checks:
                failed_checks.append("insufficient benchmark quality signals")

            return {
                "recommended": False,
                "quotation_id": None,
                "best_vendor_name": "",
                "score": best["score"],
                "confidence": 0.6,
                "summary": (
                    f"Top evaluated vendor '{best_vendor_name}' is not recommended: "
                    + "; ".join(failed_checks)
                    + "."
                ),
                "market_standards": standards,
            }

        card = best["card"]
        deviation = card.get("deviation_pct")
        status_counts = card.get("status_counts", {})
        within_count = int(status_counts.get("WITHIN_RANGE", 0) or 0)
        moderate_count = int(status_counts.get("MODERATE", 0) or 0)
        high_count = int(status_counts.get("HIGH", 0) or 0)
        line_count = int(card.get("line_count", 0) or 0)
        benchmarked_line_count = int(card.get("benchmarked_line_count", 0) or 0)
        summary_line_count = benchmarked_line_count or line_count
        live_refs = int(card.get("live_reference_count", 0) or 0)

        summary = (
            f"Recommended vendor '{card.get('supplier_name')}' because deviation is "
            f"{deviation:.2f}% with {within_count + moderate_count}/{summary_line_count} lines "
            "within acceptable market bands and "
            f"{live_refs} live market reference citation(s). "
            f"High-variance lines: {high_count}."
        )

        return {
            "recommended": True,
            "quotation_id": card.get("quotation_id"),
            "best_vendor_name": card.get("supplier_name", ""),
            "score": best["score"],
            "confidence": 0.9,
            "summary": summary,
            "market_standards": standards,
        }

    @classmethod
    def _recommend_with_llm(cls, *, ranked: list[dict]) -> dict | None:
        if not ranked:
            return None

        llm_ranked = []
        for row in ranked[:8]:
            card = row.get("card") or {}
            status_counts = card.get("status_counts", {})
            high_count = int(status_counts.get("HIGH", 0) or 0)
            benchmarked_line_count = int(card.get("benchmarked_line_count", 0) or 0)
            high_ratio = high_count / float(max(benchmarked_line_count, 1))

            llm_ranked.append(
                {
                    "quotation_id": card.get("quotation_id"),
                    "supplier_name": card.get("supplier_name"),
                    "score": row.get("score"),
                    "eligible": bool(row.get("eligible")),
                    "deviation_pct": card.get("deviation_pct"),
                    "line_count": card.get("line_count"),
                    "benchmarked_line_count": benchmarked_line_count,
                    "high_count": high_count,
                    "high_ratio": round(high_ratio, 4),
                    "live_reference_count": card.get("live_reference_count", 0),
                }
            )

        prompt = {
            "task": "Select vendor recommendation from scored benchmark vendors.",
            "thresholds": {
                "deviation_threshold_pct": cls.DEVIATION_THRESHOLD_PCT,
                "high_variance_ratio_threshold": cls.HIGH_VARIANCE_RATIO_THRESHOLD,
            },
            "ranked_vendors": llm_ranked,
            "rules": [
                "Recommend a vendor only when benchmark alignment and variance quality are acceptable.",
                "If no vendor qualifies, return recommended=false and explain the top blocker.",
                "Use only provided data.",
                "Return strict JSON only.",
            ],
            "required_output_schema": {
                "recommended": "bool",
                "quotation_id": "int|null",
                "best_vendor_name": "string",
                "confidence": "float_0_to_1",
                "summary": "string",
                "market_standards": ["string"],
            },
        }

        try:
            llm = LLMClient(temperature=0.0, max_tokens=1200)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You are a procurement vendor recommendation analyst. Return JSON only.",
                    ),
                    LLMMessage(role="user", content=json.dumps(prompt)),
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.content or "")
            if not isinstance(parsed, dict):
                return None

            recommended = bool(parsed.get("recommended"))
            quoted_id = parsed.get("quotation_id")
            try:
                quotation_id = int(quoted_id) if quoted_id is not None else None
            except Exception:
                quotation_id = None

            selected = None
            if quotation_id is not None:
                for row in ranked:
                    card = row.get("card") or {}
                    if int(card.get("quotation_id") or 0) == quotation_id:
                        selected = row
                        break

            if selected is None:
                selected = ranked[0]
                if recommended:
                    recommended = bool(selected.get("eligible"))

            card = selected.get("card") or {}
            status_counts = card.get("status_counts", {})
            standards = parsed.get("market_standards")
            if not isinstance(standards, list) or not standards:
                standards = cls.market_standards(
                    deviation=card.get("deviation_pct"),
                    high_count=int(status_counts.get("HIGH", 0) or 0),
                    benchmarked_line_count=int(card.get("benchmarked_line_count", 0) or 0),
                    line_count=int(card.get("line_count", 0) or 0),
                    live_refs=int(card.get("live_reference_count", 0) or 0),
                )

            try:
                confidence = float(parsed.get("confidence"))
            except Exception:
                confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))

            best_vendor_name = str(parsed.get("best_vendor_name") or card.get("supplier_name") or "").strip()
            summary = str(parsed.get("summary") or "").strip()
            if not summary:
                if recommended:
                    summary = f"Recommended vendor '{best_vendor_name}' based on benchmark alignment and variance quality."
                else:
                    summary = f"Top evaluated vendor '{best_vendor_name or 'N/A'}' is not recommended based on benchmark quality checks."

            return {
                "recommended": recommended,
                "quotation_id": card.get("quotation_id") if recommended else None,
                "best_vendor_name": best_vendor_name if recommended else "",
                "score": selected.get("score"),
                "confidence": confidence,
                "summary": summary,
                "market_standards": [str(s) for s in standards][:5],
            }
        except Exception:
            logger.exception("Vendor recommendation LLM path failed; using deterministic fallback")
            return None

    @classmethod
    def market_standards(
        cls,
        *,
        deviation=None,
        high_count: int = 0,
        benchmarked_line_count: int = 0,
        line_count: int = 0,
        live_refs: int = 0,
    ) -> list[str]:
        observed_alignment = "n/a"
        if deviation is not None:
            observed_alignment = f"{abs(float(deviation)):.2f}%"

        high_ratio = high_count / float(max(benchmarked_line_count, 1))
        observed_ratio = f"{high_ratio * 100.0:.1f}% ({high_count}/{max(benchmarked_line_count, 1)} benchmarked lines)"
        line_scope = f"line scope: {benchmarked_line_count}/{max(line_count, 0)} benchmarked"

        return [
            (
                "Benchmark alignment threshold <= "
                f"{cls.DEVIATION_THRESHOLD_PCT:.0f}% absolute deviation "
                f"(observed: {observed_alignment}; {line_scope})."
            ),
            (
                "Variance quality threshold <= "
                f"{cls.HIGH_VARIANCE_RATIO_THRESHOLD * 100.0:.0f}% HIGH-variance ratio "
                f"(observed: {observed_ratio})."
            ),
            f"Market evidence observed: {live_refs} live reference citation(s).",
        ]
