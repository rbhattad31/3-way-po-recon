"""Decision Maker agent for Flow B should-cost benchmarking.

Flow-B logic implemented:
- classify each line item into DB-backed categories (CategoryMaster)
- decide benchmark source per line:
  - MARKET_DATA for dynamic equipment pricing
  - DB_BENCHMARK when corridor table is configured
  - NEEDS_REVIEW when neither source is available
"""

from __future__ import annotations

from typing import Any, Dict, List

from apps.benchmarking.models import BenchmarkCorridorRule, CategoryMaster


class BenchmarkDecisionMakerAgentBM:
	"""Classify and route benchmark source for each line item."""

	MARKET_FIRST_CATEGORIES = {"EQUIPMENT"}

	@classmethod
	def decide_for_line_items(
		cls,
		*,
		line_items: List[Any],
		geography: str = "",
		scope_type: str = "",
	) -> Dict[str, Any]:
		decisions = []
		classified_count = 0
		db_source_count = 0
		market_source_count = 0
		review_source_count = 0

		for item in line_items:
			description = getattr(item, "description", "") or ""
			category_info = cls._classify_from_db(description)
			category = category_info.get("category", "UNCATEGORIZED")
			source_decision = cls._choose_source(
				category=category,
				geography=geography,
				scope_type=scope_type,
			)

			if category != "UNCATEGORIZED":
				classified_count += 1

			source = source_decision.get("source")
			if source == "DB_BENCHMARK":
				db_source_count += 1
			elif source == "MARKET_DATA":
				market_source_count += 1
			else:
				review_source_count += 1

			decisions.append({
				"line_number": getattr(item, "line_number", 0),
				"description": description[:200],
				"category": category,
				"classification_confidence": category_info.get("confidence", 0.0),
				"source": source,
				"source_reason": source_decision.get("reason", ""),
				"corridor_rule_found": source_decision.get("corridor_rule_found", False),
			})

		total = max(len(line_items), 1)
		coverage = classified_count / float(total)
		confidence = round(min(0.95, 0.5 + (coverage * 0.45)), 3)

		summary = (
			f"Decision maker processed {len(line_items)} line(s): "
			f"classified {classified_count}, DB benchmark {db_source_count}, "
			f"market data {market_source_count}, review required {review_source_count}."
		)

		return {
			"confidence": confidence,
			"summary": summary,
			"line_decisions": decisions,
			"routing_totals": {
				"db_benchmark": db_source_count,
				"market_data": market_source_count,
				"needs_review": review_source_count,
			},
		}

	@classmethod
	def _classify_from_db(cls, description: str) -> Dict[str, Any]:
		if not description.strip():
			return {"category": "UNCATEGORIZED", "confidence": 0.0}

		text = description.lower()
		for row in CategoryMaster.objects.filter(is_active=True).order_by("sort_order", "code"):
			keywords = row.keyword_list()
			for keyword in keywords:
				if keyword and keyword in text:
					return {
						"category": row.code,
						"confidence": 0.9,
					}

			if row.code.lower() in text or row.name.lower() in text:
				return {
					"category": row.code,
					"confidence": 0.75,
				}

		return {"category": "UNCATEGORIZED", "confidence": 0.0}

	@classmethod
	def _choose_source(cls, *, category: str, geography: str, scope_type: str) -> Dict[str, Any]:
		corridor_exists = BenchmarkCorridorRule.objects.filter(
			is_active=True,
			category=category,
			geography__in=[geography, "ALL"],
			scope_type__in=[scope_type, "ALL"],
		).exists()

		if category in cls.MARKET_FIRST_CATEGORIES:
			return {
				"source": "MARKET_DATA",
				"reason": "Dynamic equipment category uses live market pricing first.",
				"corridor_rule_found": corridor_exists,
			}

		if corridor_exists:
			return {
				"source": "DB_BENCHMARK",
				"reason": "Benchmark corridor rule exists in configuration table.",
				"corridor_rule_found": True,
			}

		return {
			"source": "NEEDS_REVIEW",
			"reason": "No matching benchmark corridor and not market-first category.",
			"corridor_rule_found": False,
		}