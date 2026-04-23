"""Market data analyzer agent for benchmarking domain."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from django.conf import settings
from apps.agents.services.llm_client import LLMClient, LLMMessage


logger = logging.getLogger(__name__)


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
		market_line_pks = {
			int(d.get("line_pk"))
			for d in market_data_decisions
			if d.get("line_pk") is not None
		}
		if market_line_pks:
			target_lines_for_market = [li for li in line_items if int(getattr(li, "pk", 0) or 0) in market_line_pks]
		elif market_data_decisions:
			target_lines_for_market = [
				li for li in line_items
				if any(d.get("line_number") == li.line_number for d in market_data_decisions)
			]
		else:
			target_lines_for_market = line_items

		market_price_updates, market_fetch_stats = cls._collect_market_prices(target_lines_for_market)

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

		llm_summary, llm_confidence = cls._llm_synthesize_summary(
			total_lines=total_lines,
			coverage_pct=coverage_pct,
			avg_abs_variance=avg_abs_variance,
			market_data_count=len(market_data_decisions),
			db_benchmark_count=len(db_benchmark_decisions),
			needs_review_count=len(needs_review_decisions),
			top_risk_lines=top_risk_lines,
		)
		if llm_summary:
			summary = llm_summary
		if llm_confidence is not None:
			confidence = llm_confidence

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
			"market_price_updates": market_price_updates,
			"market_fetch_stats": market_fetch_stats,
		}

	@classmethod
	def _llm_synthesize_summary(
		cls,
		*,
		total_lines: int,
		coverage_pct: float,
		avg_abs_variance: Optional[float],
		market_data_count: int,
		db_benchmark_count: int,
		needs_review_count: int,
		top_risk_lines: list,
	) -> tuple[Optional[str], Optional[float]]:
		payload = {
			"task": "Summarize benchmark market analysis for procurement reviewers.",
			"inputs": {
				"total_lines": total_lines,
				"live_coverage_pct": round(coverage_pct, 2),
				"average_abs_variance_pct": avg_abs_variance,
				"market_data_lines": market_data_count,
				"db_benchmark_lines": db_benchmark_count,
				"needs_review_lines": needs_review_count,
				"top_risk_lines": top_risk_lines[:3],
			},
			"required_output_schema": {
				"summary": "string",
				"confidence": "float_0_to_1",
			},
		}
		try:
			llm = LLMClient(temperature=0.0, max_tokens=500)
			response = llm.chat(
				messages=[
					LLMMessage(
						role="system",
						content="You are a procurement market analyst. Return only JSON.",
					),
					LLMMessage(role="user", content=json.dumps(payload)),
				],
				response_format={"type": "json_object"},
			)
			parsed = json.loads(response.content or "")
			if not isinstance(parsed, dict):
				return None, None
			summary = str(parsed.get("summary") or "").strip() or None
			try:
				confidence = float(parsed.get("confidence"))
			except Exception:
				confidence = None
			if confidence is not None:
				confidence = max(0.0, min(1.0, confidence))
			return summary, confidence
		except Exception:
			logger.exception("Market analyzer LLM synthesis failed; using deterministic summary")
			return None, None

	@classmethod
	def _collect_market_prices(cls, target_lines_for_market: list) -> tuple[list, dict]:
		updates = []
		attempted = 0
		succeeded = 0

		for line_item in target_lines_for_market:
			attempted += 1
			market_payload = cls._resolve_market_price(line_item)
			if not market_payload:
				continue

			mid_value = market_payload.get("benchmark_mid")
			if mid_value is None or float(mid_value) <= 0:
				continue

			succeeded += 1
			updates.append({
				"line_pk": getattr(line_item, "pk", None),
				"line_number": getattr(line_item, "line_number", 0),
				"benchmark_min": float(market_payload.get("benchmark_min") or 0.0),
				"benchmark_mid": float(mid_value),
				"benchmark_max": float(market_payload.get("benchmark_max") or 0.0),
				"confidence": float(market_payload.get("confidence") or 0.0),
				"currency": market_payload.get("currency") or "AED",
				"citations": market_payload.get("citations") or [],
				"source_note": market_payload.get("source_note") or "",
			})

		return updates, {
			"attempted": attempted,
			"succeeded": succeeded,
			"failed": max(attempted - succeeded, 0),
		}

	@classmethod
	def _resolve_market_price(cls, line_item: Any) -> Optional[Dict[str, Any]]:
		existing = cls._extract_from_live_price_json(getattr(line_item, "live_price_json", {}) or {})
		if existing:
			return existing

		api_key = str(getattr(settings, "PERPLEXITY_API_KEY", "") or "").strip()
		if not api_key:
			return None

		auto_fetch_enabled = bool(getattr(settings, "BENCHMARKING_MARKET_AUTO_FETCH_ENABLED", True))
		if not auto_fetch_enabled:
			return None

		try:
			return cls._fetch_from_perplexity(
				api_key=api_key,
				description=(getattr(line_item, "description", "") or "").strip(),
				uom=(getattr(line_item, "uom", "") or "").strip(),
			)
		except Exception:
			logger.debug("Benchmark market fetch failed for line_item=%s", getattr(line_item, "pk", None), exc_info=True)
			return None

	@classmethod
	def _extract_from_live_price_json(cls, payload: dict) -> Optional[Dict[str, Any]]:
		if not isinstance(payload, dict) or not payload:
			return None

		min_value = cls._to_float(payload.get("benchmark_min") or payload.get("min") or payload.get("market_min"))
		mid_value = cls._to_float(payload.get("benchmark_mid") or payload.get("mid") or payload.get("market_mid") or payload.get("price"))
		max_value = cls._to_float(payload.get("benchmark_max") or payload.get("max") or payload.get("market_max"))

		if mid_value is None:
			return None

		if min_value is None:
			min_value = round(mid_value * 0.95, 2)
		if max_value is None:
			max_value = round(mid_value * 1.05, 2)

		return {
			"benchmark_min": min_value,
			"benchmark_mid": mid_value,
			"benchmark_max": max_value,
			"confidence": cls._to_float(payload.get("confidence")) or 0.7,
			"currency": payload.get("currency") or "AED",
			"citations": payload.get("citations") or [],
			"source_note": payload.get("source_note") or "existing_live_price_json",
		}

	@classmethod
	def _fetch_from_perplexity(cls, *, api_key: str, description: str, uom: str) -> Optional[Dict[str, Any]]:
		if not description:
			return None

		try:
			import requests
		except Exception:
			return None

		model = getattr(settings, "PERPLEXITY_MODEL", "sonar-pro")
		headers = {
			"Authorization": f"Bearer {api_key}",
			"Content-Type": "application/json",
		}
		prompt = (
			"You are a market pricing analyst for HVAC procurement. "
			"Return strict JSON only with keys: benchmark_min, benchmark_mid, benchmark_max, currency, confidence, source_note. "
			"Estimate current market range for this item in UAE context. "
			f"Description: {description}. UOM: {uom or 'N/A'}."
		)
		payload = {
			"model": model,
			"messages": [
				{"role": "system", "content": "Respond in strict JSON only. Do not include markdown."},
				{"role": "user", "content": prompt},
			],
			"temperature": 0.1,
		}

		response = requests.post(
			"https://api.perplexity.ai/chat/completions",
			headers=headers,
			json=payload,
			timeout=45,
		)
		response.raise_for_status()
		body = response.json() if response.content else {}
		message = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
		if not message:
			return None

		parsed = cls._parse_json_block(message)
		if not isinstance(parsed, dict):
			return None

		mid_value = cls._to_float(parsed.get("benchmark_mid"))
		if mid_value is None:
			return None

		min_value = cls._to_float(parsed.get("benchmark_min"))
		max_value = cls._to_float(parsed.get("benchmark_max"))
		if min_value is None:
			min_value = round(mid_value * 0.95, 2)
		if max_value is None:
			max_value = round(mid_value * 1.05, 2)

		citations = body.get("citations") or []
		if not isinstance(citations, list):
			citations = []

		return {
			"benchmark_min": min_value,
			"benchmark_mid": mid_value,
			"benchmark_max": max_value,
			"confidence": cls._to_float(parsed.get("confidence")) or 0.7,
			"currency": parsed.get("currency") or "AED",
			"citations": citations,
			"source_note": parsed.get("source_note") or "perplexity_live",
		}

	@staticmethod
	def _parse_json_block(text: str) -> Optional[dict]:
		try:
			return json.loads(text)
		except Exception:
			pass

		match = re.search(r"\{[\s\S]*\}", text)
		if not match:
			return None

		try:
			return json.loads(match.group(0))
		except Exception:
			return None

	@staticmethod
	def _to_float(value: Any) -> Optional[float]:
		if value is None:
			return None
		try:
			return float(str(value).replace(",", "").strip())
		except Exception:
			return None

