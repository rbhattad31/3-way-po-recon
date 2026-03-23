"""HVAC Benchmark Resolver — deterministic price corridor lookup.

Matches a quotation line item description against the HVAC benchmark catalog
(GCC market rates, AED, 2024–2025).

Matching strategy:
  1. Exact category_code match
  2. Keyword fuzzy match against catalog descriptions
  3. Fallback to AI (BenchmarkAgent) when no match found
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any, Dict, Optional

from apps.procurement.hvac.constants import HVAC_BENCHMARK_CATALOG

logger = logging.getLogger(__name__)


def _tokenise(text: str) -> set:
    """Lowercase, strip punctuation, return set of tokens."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return set(text.split())


def _keyword_score(item_tokens: set, keyword_list: list) -> int:
    """Count how many catalog keywords appear in the item description tokens."""
    score = 0
    for kw in keyword_list:
        kw_tokens = _tokenise(kw)
        if kw_tokens.issubset(item_tokens):
            score += len(kw_tokens)  # longer keyword matches → higher score
    return score


class HVACBenchmarkResolver:
    """Resolve market benchmark price corridor for an HVAC line item.

    Usage::
        result = HVACBenchmarkResolver.resolve(description, category_code, unit)
        # result: { min, avg, max, source, catalog_key, match_score }
    """

    @staticmethod
    def resolve(
        description: str,
        category_code: Optional[str] = None,
        unit: Optional[str] = None,
        currency: str = "AED",
    ) -> Dict[str, Any]:
        """Return benchmark price corridor for the given line item.

        Returns a dict with min/avg/max (Decimal), source, catalog_key, match_score.
        Returns None values when no match is found.
        """
        # 1. Direct category_code match
        if category_code and category_code.upper() in HVAC_BENCHMARK_CATALOG:
            entry = HVAC_BENCHMARK_CATALOG[category_code.upper()]
            return HVACBenchmarkResolver._format_result(entry, category_code.upper(), 100)

        # 2. Keyword scoring against full catalog
        item_tokens = _tokenise(description or "")
        best_key = None
        best_score = 0

        for catalog_key, entry in HVAC_BENCHMARK_CATALOG.items():
            score = _keyword_score(item_tokens, entry.get("category_keywords", []))
            if score > best_score:
                best_score = score
                best_key = catalog_key

        # Require at least one keyword match
        if best_key and best_score >= 2:
            entry = HVAC_BENCHMARK_CATALOG[best_key]
            logger.debug(
                "HVAC benchmark matched '%s' → %s (score=%d)", description[:60], best_key, best_score
            )
            return HVACBenchmarkResolver._format_result(entry, best_key, best_score)

        # 3. No match
        logger.info("HVAC benchmark: no match for description '%s'", description[:80])
        return {
            "min": None,
            "avg": None,
            "max": None,
            "source": "no_match",
            "catalog_key": None,
            "match_score": 0,
            "reasoning": "No matching HVAC benchmark found for this line item. Will use AI estimation.",
        }

    @staticmethod
    def _format_result(entry: Dict[str, Any], key: str, score: int) -> Dict[str, Any]:
        return {
            "min": entry["benchmark_min"],
            "avg": entry["benchmark_avg"],
            "max": entry["benchmark_max"],
            "source": "hvac_catalog",
            "catalog_key": key,
            "match_score": score,
            "description": entry.get("description", ""),
            "notes": entry.get("notes", ""),
            "reasoning": (
                f"Matched HVAC benchmark catalog entry '{key}' "
                f"(GCC market rate, AED, 2024–2025). "
                f"Range: {entry['benchmark_min']}–{entry['benchmark_max']} AED/{entry.get('unit', 'unit')}."
            ),
        }
