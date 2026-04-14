"""Vendor recommendation agent for benchmarking domain."""

from __future__ import annotations


class BenchmarkVendorRecommendationAgent:
    """Select best vendor (or no-vendor) and produce reason summary."""

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

            score = 100.0
            if deviation is not None:
                score -= abs(float(deviation)) * 1.8
            else:
                score -= 25.0

            score -= high_count * 8.0
            score -= review_count * 3.0
            score += min(live_refs, 8) * 1.5

            high_ratio = high_count / float(line_count)
            eligible = (
                deviation is not None
                and abs(float(deviation)) <= 15.0
                and high_ratio <= 0.25
            )

            ranked.append({
                "card": card,
                "score": round(score, 2),
                "eligible": eligible,
            })

        ranked.sort(key=lambda x: x["score"], reverse=True)
        best = ranked[0]

        if not best["eligible"]:
            return {
                "recommended": False,
                "quotation_id": None,
                "best_vendor_name": "",
                "score": best["score"],
                "confidence": 0.6,
                "summary": (
                    "No vendor meets market standards yet. "
                    "Top option has high variance exposure or insufficient benchmark alignment."
                ),
                "market_standards": cls.market_standards(),
            }

        card = best["card"]
        deviation = card.get("deviation_pct")
        status_counts = card.get("status_counts", {})
        within_count = int(status_counts.get("WITHIN_RANGE", 0) or 0)
        moderate_count = int(status_counts.get("MODERATE", 0) or 0)
        high_count = int(status_counts.get("HIGH", 0) or 0)
        line_count = int(card.get("line_count", 0) or 0)
        live_refs = int(card.get("live_reference_count", 0) or 0)

        summary = (
            f"Recommended vendor '{card.get('supplier_name')}' because deviation is "
            f"{deviation:.2f}% with {within_count + moderate_count}/{line_count} lines "
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
            "market_standards": cls.market_standards(),
        }

    @staticmethod
    def market_standards() -> list[str]:
        return [
            "Benchmark alignment: absolute deviation should stay within 15% of benchmark mid.",
            "Variance quality: HIGH variance lines should be minimal (<25% of total lines).",
            "Market evidence: vendor pricing should include live market references and citations.",
        ]
