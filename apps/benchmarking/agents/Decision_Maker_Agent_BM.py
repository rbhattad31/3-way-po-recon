"""Decision Maker agent for Flow B should-cost benchmarking.

LLM-first implementation with deterministic fallback:
- classify each line item into DB-backed categories (CategoryMaster)
- decide benchmark source per line:
  - MARKET_DATA for dynamic equipment pricing
  - DB_BENCHMARK when corridor table is configured
  - NEEDS_REVIEW when neither source is available
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.benchmarking.models import BenchmarkCorridorRule, CategoryMaster


logger = logging.getLogger(__name__)


class BenchmarkDecisionMakerAgentBM:
    """Classify and route benchmark source for each line item."""

    @classmethod
    def decide_for_line_items(
        cls,
        *,
        line_items: List[Any],
        geography: str = "",
        scope_type: str = "",
    ) -> Dict[str, Any]:
        deterministic = cls._decide_deterministic(
            line_items=line_items,
            geography=geography,
            scope_type=scope_type,
        )

        llm_output = cls._decide_with_llm(
            line_items=line_items,
            geography=geography,
            scope_type=scope_type,
            fallback_output=deterministic,
        )
        if llm_output:
            return llm_output
        return deterministic

    @classmethod
    def _decide_with_llm(
        cls,
        *,
        line_items: List[Any],
        geography: str,
        scope_type: str,
        fallback_output: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not line_items:
            return fallback_output

        categories = list(CategoryMaster.objects.filter(is_active=True).order_by("sort_order", "code"))
        category_payload = []
        allowed_codes = {"UNCATEGORIZED"}
        for row in categories:
            code = (row.code or "").strip()
            if not code:
                continue
            allowed_codes.add(code)
            category_payload.append(
                {
                    "code": code,
                    "name": row.name,
                    "pricing_type": row.pricing_type,
                    "keywords": row.keyword_list()[:20],
                }
            )

        corridor_categories = set(
            BenchmarkCorridorRule.objects.filter(
                is_active=True,
                geography__in=[geography, "ALL"],
                scope_type__in=[scope_type, "ALL"],
            ).values_list("category", flat=True)
        )

        line_payload = []
        for item in line_items:
            line_payload.append(
                {
                    "line_pk": getattr(item, "pk", None),
                    "line_number": getattr(item, "line_number", 0),
                    "description": (getattr(item, "description", "") or "")[:240],
                    "uom": (getattr(item, "uom", "") or "")[:20],
                    "quantity": str(getattr(item, "quantity", "") or ""),
                }
            )

        fallback_decisions = {
            (d.get("line_pk"), d.get("line_number")): d
            for d in (fallback_output.get("line_decisions") or [])
        }

        prompt = {
            "task": "Classify and route each benchmark line item.",
            "rules": [
                "Use one category code from allowed categories. If no fit, use UNCATEGORIZED.",
                "Use pricing_type MARKET, BENCHMARK, or HYBRID.",
                "MARKET always routes to MARKET_DATA.",
                "BENCHMARK routes to DB_BENCHMARK only when corridor exists, else NEEDS_REVIEW.",
                "HYBRID uses DB_BENCHMARK plus market intent when corridor exists; otherwise MARKET_DATA.",
                "Return strict JSON only.",
            ],
            "geography": geography,
            "scope_type": scope_type,
            "categories": category_payload,
            "categories_with_corridor": sorted([c for c in corridor_categories if c]),
            "line_items": line_payload,
            "required_output_schema": {
                "summary": "string",
                "line_decisions": [
                    {
                        "line_pk": "int|null",
                        "line_number": "int",
                        "category": "string",
                        "pricing_type": "MARKET|BENCHMARK|HYBRID",
                        "classification_confidence": "float_0_to_1",
                        "source": "DB_BENCHMARK|MARKET_DATA|NEEDS_REVIEW",
                        "source_reason": "string",
                        "hybrid_use_benchmark": "bool",
                        "hybrid_use_market": "bool",
                    }
                ],
            },
        }

        try:
            llm = LLMClient(temperature=0.0, max_tokens=2500)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You are a procurement benchmark decision agent. "
                            "Return only valid JSON."
                        ),
                    ),
                    LLMMessage(role="user", content=json.dumps(prompt)),
                ],
                response_format={"type": "json_object"},
            )
            parsed = cls._parse_json(response.content or "")
            if not isinstance(parsed, dict):
                return None

            raw_decisions = parsed.get("line_decisions") or []
            if not isinstance(raw_decisions, list):
                return None

            by_key = {}
            for row in raw_decisions:
                if not isinstance(row, dict):
                    continue
                key = (row.get("line_pk"), row.get("line_number"))
                by_key[key] = row

            normalized_decisions = []
            classified_count = 0
            db_source_count = 0
            market_source_count = 0
            review_source_count = 0

            for item in line_items:
                key = (getattr(item, "pk", None), getattr(item, "line_number", 0))
                model_row = by_key.get(key) or by_key.get((None, key[1])) or {}
                fallback = fallback_decisions.get(key) or fallback_decisions.get((None, key[1])) or {}

                category = str(model_row.get("category") or fallback.get("category") or "UNCATEGORIZED").strip()
                if category not in allowed_codes:
                    category = fallback.get("category") or "UNCATEGORIZED"

                pricing_type = str(model_row.get("pricing_type") or fallback.get("pricing_type") or "BENCHMARK").strip().upper()
                if pricing_type not in {"MARKET", "BENCHMARK", "HYBRID"}:
                    pricing_type = str(fallback.get("pricing_type") or "BENCHMARK").strip().upper() or "BENCHMARK"

                source_decision = cls._choose_source(
                    category=category,
                    geography=geography,
                    scope_type=scope_type,
                    pricing_type=pricing_type,
                )
                source = source_decision.get("source")

                if category != "UNCATEGORIZED":
                    classified_count += 1

                if source == "DB_BENCHMARK":
                    db_source_count += 1
                elif source == "MARKET_DATA":
                    market_source_count += 1
                else:
                    review_source_count += 1

                conf = model_row.get("classification_confidence")
                try:
                    conf_value = float(conf)
                except Exception:
                    conf_value = float(fallback.get("classification_confidence") or 0.0)
                conf_value = max(0.0, min(1.0, conf_value))

                normalized_decisions.append(
                    {
                        "line_number": key[1],
                        "line_pk": key[0],
                        "description": (getattr(item, "description", "") or "")[:200],
                        "category": category,
                        "pricing_type": pricing_type,
                        "classification_confidence": conf_value,
                        "source": source,
                        "source_reason": str(model_row.get("source_reason") or source_decision.get("reason") or "")[:300],
                        "corridor_rule_found": bool(source_decision.get("corridor_rule_found")),
                        "hybrid_use_benchmark": bool(source_decision.get("hybrid_use_benchmark", False)),
                        "hybrid_use_market": bool(source_decision.get("hybrid_use_market", False)),
                    }
                )

            total = max(len(line_items), 1)
            coverage = classified_count / float(total)
            confidence = round(min(0.97, 0.55 + (coverage * 0.4)), 3)
            summary = str(parsed.get("summary") or "").strip() or (
                f"LLM decision maker processed {len(line_items)} line(s)."
            )

            return {
                "confidence": confidence,
                "summary": summary,
                "line_decisions": normalized_decisions,
                "routing_totals": {
                    "db_benchmark": db_source_count,
                    "market_data": market_source_count,
                    "needs_review": review_source_count,
                },
            }
        except Exception:
            logger.exception("Decision Maker LLM path failed; using deterministic fallback")
            return None

    @classmethod
    def _decide_deterministic(
        cls,
        *,
        line_items: List[Any],
        geography: str,
        scope_type: str,
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
            pricing_type = category_info.get("pricing_type", "BENCHMARK")
            source_decision = cls._choose_source(
                category=category,
                geography=geography,
                scope_type=scope_type,
                pricing_type=pricing_type,
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

            decisions.append(
                {
                    "line_number": getattr(item, "line_number", 0),
                    "line_pk": getattr(item, "pk", None),
                    "description": description[:200],
                    "category": category,
                    "pricing_type": pricing_type,
                    "classification_confidence": category_info.get("confidence", 0.0),
                    "source": source,
                    "source_reason": source_decision.get("reason", ""),
                    "corridor_rule_found": source_decision.get("corridor_rule_found", False),
                    "hybrid_use_benchmark": source_decision.get("hybrid_use_benchmark", False),
                    "hybrid_use_market": source_decision.get("hybrid_use_market", False),
                }
            )

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
                        "pricing_type": row.pricing_type,
                        "confidence": 0.9,
                    }

            if row.code.lower() in text or row.name.lower() in text:
                return {
                    "category": row.code,
                    "pricing_type": row.pricing_type,
                    "confidence": 0.75,
                }

        return {"category": "UNCATEGORIZED", "pricing_type": "BENCHMARK", "confidence": 0.0}

    @classmethod
    def _choose_source(
        cls,
        *,
        category: str,
        geography: str,
        scope_type: str,
        pricing_type: str,
    ) -> Dict[str, Any]:
        corridor_exists = BenchmarkCorridorRule.objects.filter(
            is_active=True,
            category=category,
            geography__in=[geography, "ALL"],
            scope_type__in=[scope_type, "ALL"],
        ).exists()

        pricing_mode = (pricing_type or "BENCHMARK").strip().upper()

        if category == "UNCATEGORIZED":
            return {
                "source": "MARKET_DATA",
                "reason": "Uncategorized line; using market research fallback.",
                "corridor_rule_found": corridor_exists,
                "hybrid_use_benchmark": False,
                "hybrid_use_market": False,
            }

        if pricing_mode == "MARKET":
            return {
                "source": "MARKET_DATA",
                "reason": "Pricing type MARKET routes to market research.",
                "corridor_rule_found": corridor_exists,
                "hybrid_use_benchmark": False,
                "hybrid_use_market": False,
            }

        if pricing_mode == "HYBRID":
            if corridor_exists:
                return {
                    "source": "DB_BENCHMARK",
                    "reason": "HYBRID with corridor uses benchmark plus market values.",
                    "corridor_rule_found": True,
                    "hybrid_use_benchmark": True,
                    "hybrid_use_market": True,
                }
            return {
                "source": "MARKET_DATA",
                "reason": "HYBRID without corridor uses market values only.",
                "corridor_rule_found": False,
                "hybrid_use_benchmark": False,
                "hybrid_use_market": True,
            }

        if corridor_exists:
            return {
                "source": "DB_BENCHMARK",
                "reason": "BENCHMARK with corridor uses database benchmark rules.",
                "corridor_rule_found": True,
                "hybrid_use_benchmark": False,
                "hybrid_use_market": False,
            }

        return {
            "source": "NEEDS_REVIEW",
            "reason": "BENCHMARK category has no corridor rule and requires review.",
            "corridor_rule_found": False,
            "hybrid_use_benchmark": False,
            "hybrid_use_market": False,
        }

    @staticmethod
    def _parse_json(payload: str) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception:
            return None
