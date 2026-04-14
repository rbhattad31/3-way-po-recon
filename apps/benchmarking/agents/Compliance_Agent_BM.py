"""Compliance agent for benchmarking domain."""

from __future__ import annotations


class BenchmarkComplianceAgentBM:
	"""Evaluate benchmark result against procurement compliance standards."""

	@classmethod
	def evaluate(cls, *, result, line_items: list) -> dict:
		total_lines = max(len(line_items), 1)
		high_lines = int(getattr(result, "lines_high", 0) or 0)
		review_lines = int(getattr(result, "lines_needs_review", 0) or 0)
		overall_deviation = getattr(result, "overall_deviation_pct", None)

		violations = []
		checks = []

		deviation_ok = overall_deviation is not None and abs(float(overall_deviation)) <= 15.0
		checks.append({
			"rule": "overall_deviation_within_15pct",
			"passed": deviation_ok,
			"actual": overall_deviation,
			"threshold": 15.0,
		})
		if not deviation_ok:
			violations.append("Overall deviation is outside acceptable 15% benchmark tolerance.")

		high_ratio = high_lines / float(total_lines)
		high_ratio_ok = high_ratio <= 0.25
		checks.append({
			"rule": "high_variance_ratio_below_25pct",
			"passed": high_ratio_ok,
			"actual": round(high_ratio, 4),
			"threshold": 0.25,
		})
		if not high_ratio_ok:
			violations.append("High-variance line ratio exceeds 25% of quotation scope.")

		review_ok = review_lines == 0
		checks.append({
			"rule": "no_unresolved_review_lines",
			"passed": review_ok,
			"actual": review_lines,
			"threshold": 0,
		})
		if not review_ok:
			violations.append("Some lines are still marked as NEEDS_REVIEW.")

		if not violations:
			status = "PASS"
			confidence = 0.95
			summary = "Compliance checks passed. Benchmark result is eligible for vendor decision."
		elif len(violations) == 1:
			status = "PARTIAL"
			confidence = 0.75
			summary = "Compliance partially satisfied. One corrective action is required before approval."
		else:
			status = "FAIL"
			confidence = 0.55
			summary = "Compliance failed. Multiple benchmark risk controls were violated."

		return {
			"status": status,
			"confidence": confidence,
			"summary": summary,
			"checks": checks,
			"violations": violations,
		}

