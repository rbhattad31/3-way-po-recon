"""AI insights analyzer agent for procurement benchmarking detail panels."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from apps.agents.services.llm_client import LLMClient, LLMMessage


logger = logging.getLogger(__name__)


class BenchmarkAIAnalyzerAgentBM:
	"""Generate detailed AI insights from benchmark pipeline outputs."""

	@staticmethod
	def _json_safe(value: Any) -> Any:
		try:
			return json.loads(json.dumps(value, default=str))
		except Exception:
			return value

	@classmethod
	def analyze(
		cls,
		*,
		bench_request,
		line_items: list,
		vendor_cards: list,
		decision_output: dict,
		market_output: dict,
		analyst_output: dict,
		compliance_output: dict,
		vendor_output: dict,
	) -> dict:
		llm_context = cls._build_llm_context(
			bench_request=bench_request,
			line_items=line_items,
			vendor_cards=vendor_cards,
			decision_output=decision_output,
			market_output=market_output,
			analyst_output=analyst_output,
			compliance_output=compliance_output,
			vendor_output=vendor_output,
		)
		deterministic = cls._analyze_deterministic(
			bench_request=bench_request,
			line_items=line_items,
			vendor_cards=vendor_cards,
			decision_output=decision_output,
			market_output=market_output,
			analyst_output=analyst_output,
			compliance_output=compliance_output,
			vendor_output=vendor_output,
		)
		llm_output = cls._analyze_with_llm(deterministic=deterministic, llm_context=llm_context)
		if llm_output:
			return llm_output
		return deterministic

	@classmethod
	def _build_llm_context(
		cls,
		*,
		bench_request,
		line_items: list,
		vendor_cards: list,
		decision_output: dict,
		market_output: dict,
		analyst_output: dict,
		compliance_output: dict,
		vendor_output: dict,
	) -> dict:
		total_lines = len(line_items)
		status_counts = {"WITHIN_RANGE": 0, "MODERATE": 0, "HIGH": 0, "NEEDS_REVIEW": 0}
		source_counts = {"CORRIDOR_DB": 0, "PERPLEXITY_LIVE": 0, "NONE": 0, "OTHER": 0}
		line_snapshot = []

		for li in line_items:
			status = str(getattr(li, "variance_status", "NEEDS_REVIEW") or "NEEDS_REVIEW").strip().upper()
			if status not in status_counts:
				status = "NEEDS_REVIEW"
			status_counts[status] += 1

			source = str(getattr(li, "benchmark_source", "NONE") or "NONE").strip().upper()
			if source in source_counts:
				source_counts[source] += 1
			else:
				source_counts["OTHER"] += 1

			if len(line_snapshot) < 20:
				_live_json = getattr(li, "live_price_json", None) or {}
				_citation_count = len((_live_json.get("citations") or [])) if isinstance(_live_json, dict) else 0
				line_snapshot.append(
					{
						"line_number": getattr(li, "line_number", 0),
						"description": (getattr(li, "description", "") or "")[:180],
						"category": getattr(li, "category", "") or "UNCATEGORIZED",
						"quoted_unit_rate": getattr(li, "quoted_unit_rate", None),
						"benchmark_mid": getattr(li, "benchmark_mid", None),
						"benchmark_source": source,
						"variance_pct": getattr(li, "variance_pct", None),
						"variance_status": status,
						"market_citation_count": _citation_count,
					}
				)

		vendor_snapshot = []
		for card in vendor_cards:
			vendor_snapshot.append(
				{
					"supplier_name": card.get("supplier_name"),
					"quotation_id": card.get("quotation_id"),
					"line_count": card.get("line_count"),
					"benchmarked_line_count": card.get("benchmarked_line_count"),
					"deviation_pct": card.get("deviation_pct"),
					"status": card.get("status"),
					"status_counts": card.get("status_counts") or {},
					"live_reference_count": card.get("live_reference_count", 0),
				}
			)

		routed = (decision_output or {}).get("routing_totals") or {}
		coverage = {
			"db_pct": round((source_counts["CORRIDOR_DB"] / float(total_lines)) * 100.0, 1) if total_lines else 0.0,
			"market_pct": round((source_counts["PERPLEXITY_LIVE"] / float(total_lines)) * 100.0, 1) if total_lines else 0.0,
			"unresolved_pct": round((source_counts["NONE"] / float(total_lines)) * 100.0, 1) if total_lines else 0.0,
		}

		return cls._json_safe(
			{
				"request": {
					"request_pk": getattr(bench_request, "pk", None),
					"title": getattr(bench_request, "title", "") or "",
					"geography": getattr(bench_request, "geography", "") or "",
					"scope_type": getattr(bench_request, "scope_type", "") or "",
					"status": getattr(bench_request, "status", "") or "",
				},
				"totals": {
					"line_count": total_lines,
					"vendor_count": len(vendor_cards),
					"variance_status_counts": status_counts,
					"benchmark_source_counts": source_counts,
					"coverage": coverage,
				},
				"routing": {
					"decision_routing_totals": routed,
					"decision_sample": ((decision_output or {}).get("line_decisions") or [])[:12],
				},
				"vendor_recommendation": vendor_output or {},
				"compliance": compliance_output or {},
				"analyst": analyst_output or {},
				"market": {
					"summary": (market_output or {}).get("summary"),
					"confidence": (market_output or {}).get("confidence"),
					"fetch_stats": (market_output or {}).get("market_fetch_stats") or {},
					"top_risk_lines": (market_output or {}).get("top_risk_lines") or [],
				},
				"vendors": vendor_snapshot,
				"line_items_sample": line_snapshot,
			}
		)

	@classmethod
	def _analyze_with_llm(cls, *, deterministic: dict, llm_context: dict) -> Optional[dict]:
		payload = {
			"task": "Create realistic and dynamic AI insights for a procurement benchmarking request detail page.",
			"rules": [
				"Use only the provided input facts.",
				"Generate concise, practical, action-oriented insights for AP and procurement users with concrete evidence from vendors and lines.",
				"Do not repeat generic text; use the actual request metrics, variance mix, routing, and recommendation/compliance details.",
				"Do not include markdown.",
				"Return strict JSON only.",
			],
			"input": {
				"summary_fallback": deterministic,
				"request_data": llm_context,
			},
			"required_output_schema": {
				"summary": "string",
				"confidence": "float_0_to_1",
				"insights": ["string"],
				"risk_flags": ["string"],
				"actions": ["string"],
			},
		}
		try:
			llm = LLMClient(temperature=0.0, max_tokens=1400)
			response = llm.chat(
				messages=[
					LLMMessage(
						role="system",
						content="You are a senior procurement benchmarking analyst. Return JSON only.",
					),
					LLMMessage(role="user", content=json.dumps(payload, default=str)),
				],
				response_format={"type": "json_object"},
			)
			parsed = json.loads(response.content or "")
			if not isinstance(parsed, dict):
				return None

			summary = str(parsed.get("summary") or parsed.get("executive_summary") or "").strip()

			raw_insights = (
				parsed.get("insights")
				or parsed.get("key_insights")
				or parsed.get("key_findings")
				or parsed.get("findings")
				or []
			)
			raw_risks = (
				parsed.get("risk_flags")
				or parsed.get("risks")
				or parsed.get("risk_items")
				or []
			)
			raw_actions = (
				parsed.get("actions")
				or parsed.get("recommendations")
				or parsed.get("next_actions")
				or []
			)

			if isinstance(raw_insights, str):
				raw_insights = [raw_insights]
			if isinstance(raw_risks, str):
				raw_risks = [raw_risks]
			if isinstance(raw_actions, str):
				raw_actions = [raw_actions]

			insights = [str(x).strip() for x in raw_insights if str(x).strip()]
			risk_flags = [str(x).strip() for x in raw_risks if str(x).strip()]
			actions = [str(x).strip() for x in raw_actions if str(x).strip()]

			if not summary:
				summary = str(parsed.get("analysis") or parsed.get("narrative") or "").strip()

			if not insights and summary:
				insights = [summary]

			if not summary and not insights:
				return None

			try:
				confidence = float(parsed.get("confidence"))
			except Exception:
				confidence = float(deterministic.get("confidence") or 0.75)
			confidence = max(0.0, min(1.0, confidence))

			return {
				"summary": summary,
				"confidence": confidence,
				"insights": insights[:12],
				"risk_flags": risk_flags[:8],
				"actions": actions[:8],
				"source": "llm",
				"input_snapshot": llm_context,
			}
		except Exception:
			logger.exception("Benchmark AI Analyzer LLM path failed; deterministic fallback used")
			return None

	@classmethod
	def _analyze_deterministic(
		cls,
		*,
		bench_request,
		line_items: list,
		vendor_cards: list,
		decision_output: dict,
		market_output: dict,
		analyst_output: dict,
		compliance_output: dict,
		vendor_output: dict,
	) -> dict:
		total_lines = len(line_items)
		if total_lines <= 0:
			return {
				"summary": "No extracted line items are available yet. Upload valid quotation files and rerun analysis.",
				"confidence": 0.45,
				"insights": [
					"No line-level benchmark data exists for this request.",
					"Run extraction first, then proceed to AI analysis.",
				],
				"risk_flags": ["Missing line items"],
				"actions": ["Upload valid PDF or ZIP quotations and reprocess the request."],
				"source": "deterministic",
				"input_snapshot": {"total_lines": 0},
			}

		high_lines = [li for li in line_items if getattr(li, "variance_status", "") == "HIGH"]
		moderate_lines = [li for li in line_items if getattr(li, "variance_status", "") == "MODERATE"]
		review_lines = [li for li in line_items if getattr(li, "variance_status", "") == "NEEDS_REVIEW"]
		within_lines = [li for li in line_items if getattr(li, "variance_status", "") == "WITHIN_RANGE"]

		market_lines = [li for li in line_items if getattr(li, "benchmark_source", "") == "PERPLEXITY_LIVE"]
		db_lines = [li for li in line_items if getattr(li, "benchmark_source", "") == "CORRIDOR_DB"]
		unresolved_lines = [li for li in line_items if getattr(li, "benchmark_mid", None) is None]

		market_cov = round((len(market_lines) / float(total_lines)) * 100.0, 1)
		db_cov = round((len(db_lines) / float(total_lines)) * 100.0, 1)
		unresolved_cov = round((len(unresolved_lines) / float(total_lines)) * 100.0, 1)

		recommended_vendor = str(vendor_output.get("best_vendor_name") or "").strip()
		recommended = bool(vendor_output.get("recommended"))
		vendor_reco_summary = str(vendor_output.get("summary") or "").strip()

		compliance_status = str(compliance_output.get("status") or "UNKNOWN")
		analyst_decision = str(analyst_output.get("decision") or "").strip()

		insights: List[str] = []
		risk_flags: List[str] = []
		actions: List[str] = []

		insights.append(
			f"Request {bench_request.pk} processed {total_lines} line(s) across {len(vendor_cards)} vendor quotation(s)."
		)
		insights.append(
			f"Benchmark source coverage: DB {db_cov}%, live market {market_cov}%, unresolved {unresolved_cov}%."
		)
		insights.append(
			"Variance distribution: "
			f"within={len(within_lines)}, moderate={len(moderate_lines)}, high={len(high_lines)}, needs_review={len(review_lines)}."
		)

		if recommended and recommended_vendor:
			insights.append(f"Vendor recommendation selected '{recommended_vendor}' based on current benchmark signals.")
		else:
			insights.append("No final vendor recommendation was made due to benchmark quality or risk thresholds.")
		if vendor_reco_summary:
			insights.append(vendor_reco_summary)

		if compliance_status:
			insights.append(f"Compliance assessment status: {compliance_status}.")
		if analyst_decision:
			insights.append(f"Benchmarking analyst decision: {analyst_decision}.")

		top_high = sorted(
			high_lines,
			key=lambda li: abs(float(getattr(li, "variance_pct", 0.0) or 0.0)),
			reverse=True,
		)[:3]
		if top_high:
			top_high_text = "; ".join(
				[
					f"L{getattr(li, 'line_number', 0)} {str(getattr(li, 'description', '') or '')[:40]} ({float(getattr(li, 'variance_pct', 0.0) or 0.0):+.1f}%)"
					for li in top_high
				]
			)
			insights.append(f"Top high-variance lines: {top_high_text}.")

		vendor_deviation_lines = []
		for card in vendor_cards[:5]:
			vname = str(card.get("supplier_name") or "Unknown Vendor")
			dev = card.get("deviation_pct")
			if dev is None:
				vendor_deviation_lines.append(f"{vname}: n/a")
			else:
				vendor_deviation_lines.append(f"{vname}: {float(dev):+.1f}%")
		if vendor_deviation_lines:
			insights.append("Vendor deviation snapshot: " + " | ".join(vendor_deviation_lines) + ".")

		if unresolved_lines:
			risk_flags.append(
				f"{len(unresolved_lines)} line(s) do not have benchmark_mid values and remain unresolved."
			)
		if high_lines:
			risk_flags.append(
				f"{len(high_lines)} line(s) are HIGH variance and should be prioritized for negotiation or requote."
			)
		if compliance_status in {"FAIL", "PARTIAL"}:
			risk_flags.append(f"Compliance status is {compliance_status}, indicating gating checks are not fully satisfied.")

		if high_lines:
			actions.append("Review HIGH variance lines first and confirm category mapping, UOM, and quoted unit rate inputs.")
			actions.append("Engage vendor for repricing on top deviation lines before approval.")
		if unresolved_lines:
			actions.append("Add benchmark corridor coverage or market references for unresolved lines to reduce manual review load.")
		if review_lines:
			actions.append("Close NEEDS_REVIEW lines by validating extraction and decision routing outputs line-by-line.")
		if recommended and recommended_vendor:
			actions.append(f"Proceed with commercial negotiation package for '{recommended_vendor}' using generated notes.")
		else:
			actions.append("Hold final award and request revised quotations with benchmark-aligned rates.")

		confidence = 0.7
		if not unresolved_lines and not high_lines and compliance_status == "PASS":
			confidence = 0.9
		elif len(high_lines) > max(1, int(total_lines * 0.25)):
			confidence = 0.6

		summary = (
			f"AI insights generated for request {bench_request.pk}: "
			f"coverage DB/live/unresolved={db_cov}%/{market_cov}%/{unresolved_cov}%, "
			f"high variance lines={len(high_lines)}, compliance={compliance_status or 'UNKNOWN'}."
		)

		input_snapshot: Dict[str, Any] = {
			"total_lines": total_lines,
			"vendor_count": len(vendor_cards),
			"market_data_lines": len(market_lines),
			"db_benchmark_lines": len(db_lines),
			"unresolved_lines": len(unresolved_lines),
			"high_variance_lines": len(high_lines),
			"decision_routed_market": len((decision_output or {}).get("line_decisions", []) or []),
			"market_output_confidence": market_output.get("confidence"),
			"analyst_output_confidence": analyst_output.get("confidence"),
			"compliance_output_confidence": compliance_output.get("confidence"),
			"vendor_output_confidence": vendor_output.get("confidence"),
		}

		return {
			"summary": summary,
			"confidence": confidence,
			"insights": insights[:12],
			"risk_flags": risk_flags[:8],
			"actions": actions[:8],
			"source": "deterministic",
			"input_snapshot": input_snapshot,
		}

