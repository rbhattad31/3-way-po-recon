"""Market data analyzer agent for benchmarking domain."""

from __future__ import annotations


class BenchmarkMarketDataAnalyzerAgentBM:
	"""Analyze market coverage and pricing risk quality from benchmarked lines.
	
	Uses decision guidance from Decision Maker to determine what market data to fetch:
	- If decision says MARKET_DATA: fetch live pricing
	- If decision says DB_BENCHMARK: use database corridors
	- If decision says NEEDS_REVIEW: flag for manual review
	"""

	@classmethod
	def analyze(cls, *, line_items: list, vendor_cards: list, result, decision_guidance: dict = None) -> dict:
		"""Analyze market data, guided by Decision Maker's source decisions."""
		total_lines = len(line_items)
		if total_lines == 0:
			return {
				"confidence": 0.4,
				"summary": "No benchmark line items are available for market analysis.",
				"live_coverage_pct": 0.0,
				"average_abs_variance_pct": None,
				"top_risk_lines": [],
				"vendor_snapshot": [],
				"decision_guidance_used": decision_guidance is not None,
			}

		# Use decision guidance to determine what to fetch
		guidance_decisions = (decision_guidance or {}).get("line_decisions", [])
		market_data_decisions = [d for d in guidance_decisions if d.get("source") == "MARKET_DATA"]
		db_benchmark_decisions = [d for d in guidance_decisions if d.get("source") == "DB_BENCHMARK"]
		needs_review_decisions = [d for d in guidance_decisions if d.get("source") == "NEEDS_REVIEW"]

		# Focus market data fetching on lines marked for MARKET_DATA
		target_lines_for_market = [
			li for li in line_items
			if any(d.get("line_number") == li.line_number for d in market_data_decisions)
		] if market_data_decisions else line_items

		live_lines = [
			li for li in target_lines_for_market
			if getattr(li, "benchmark_source", "") == "PERPLEXITY_LIVE"
		]
		coverage_pct = (len(live_lines) / float(max(len(target_lines_for_market), 1))) * 100.0 if market_data_decisions else 0.0

		variance_values = [
			abs(float(li.variance_pct))
			for li in line_items
			if getattr(li, "variance_pct", None) is not None
		]
		avg_abs_variance = (
			round(sum(variance_values) / len(variance_values), 2)
			if variance_values else None
		)

		risky = [li for li in line_items if getattr(li, "variance_status", "") in {"HIGH", "NEEDS_REVIEW"}]
		risky_sorted = sorted(
			risky,
			key=lambda li: abs(float(getattr(li, "variance_pct", 0.0) or 0.0)),
			reverse=True,
		)
		top_risk_lines = [
			{
				"line_number": li.line_number,
				"description": (li.description or "")[:120],
				"variance_pct": li.variance_pct,
				"variance_status": li.variance_status,
			}
			for li in risky_sorted[:5]
		]

		vendor_snapshot = [
			{
				"supplier_name": card.get("supplier_name"),
				"deviation_pct": card.get("deviation_pct"),
				"line_count": card.get("line_count"),
				"high_lines": int((card.get("status_counts") or {}).get("HIGH", 0) or 0),
			}
			for card in vendor_cards
		]

		confidence = 0.7
		if coverage_pct >= 70 and (avg_abs_variance is not None and avg_abs_variance <= 15):
			confidence = 0.9
		elif coverage_pct < 30:
			confidence = 0.6

		summary = (
			f"Market analysis completed across {total_lines} lines. "
			f"Live market coverage is {coverage_pct:.1f}% and average absolute variance is "
			f"{avg_abs_variance if avg_abs_variance is not None else 'n/a'}%. "
			f"Decision Maker routed {len(market_data_decisions)} lines to MARKET_DATA, "
			f"{len(db_benchmark_decisions)} to DB_BENCHMARK, "
			f"{len(needs_review_decisions)} to NEEDS_REVIEW."
		)

		return {
			"confidence": confidence,
			"summary": summary,
			"live_coverage_pct": round(coverage_pct, 2),
			"average_abs_variance_pct": avg_abs_variance,
			"top_risk_lines": top_risk_lines,
			"vendor_snapshot": vendor_snapshot,
			"overall_status": getattr(result, "overall_status", "NEEDS_REVIEW"),
			"decision_guidance_used": decision_guidance is not None,
			"market_data_lines": len(market_data_decisions),
			"db_benchmark_lines": len(db_benchmark_decisions),
			"needs_review_lines": len(needs_review_decisions),
		}

