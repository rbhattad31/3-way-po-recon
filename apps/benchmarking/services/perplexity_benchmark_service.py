"""Perplexity Live-Pricing Service for Should-Cost Benchmarking.

Queries the Perplexity sonar-pro API (live web search) to retrieve current
market price corridors for HVAC line items. One batch API call is made per
BenchmarkRequest, covering all line items grouped by category.

Result structure (keyed by BenchmarkLineItem.pk):
  {
      "<pk>": {
          "description_matched": "...",
          "min_rate":  1100.0,
          "mid_rate":  1450.0,
          "max_rate":  1900.0,
          "uom":       "NR",
          "currency":  "AED",
          "confidence": 0.85,
          "source_note": "...",
          "citations": ["https://..."]
      },
      ...
  }

All methods are fail-silent -- errors are logged and an empty dict is returned
so the pipeline degrades gracefully when the API key is missing or the call fails.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PERPLEXITY_ENDPOINT = "https://api.perplexity.ai/chat/completions"
_TIMEOUT = 60  # sonar-pro can take a while with web search


# ---------------------------------------------------------------------------
# Currency map per geography (used in prompt)
# ---------------------------------------------------------------------------
_GEO_CURRENCY = {
    "UAE":   "AED",
    "KSA":   "SAR",
    "QATAR": "QAR",
    "KUWAIT": "KWD",
    "BAHRAIN": "BHD",
    "OMAN":  "OMR",
    "ALL":   "AED",
}


# ---------------------------------------------------------------------------
# System prompt -- instructs Perplexity what to return
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are a senior HVAC cost estimator with live access to supplier catalogues, "
    "GCC distributor price lists, and trade tender databases (UAE, KSA, Qatar, Kuwait). "
    "You specialise in commercial HVAC projects: VRF systems, chilled water plants, "
    "AHUs, ducting, insulation, controls (BMS), and installation services. "
    "You ALWAYS respond with a single valid JSON object and NOTHING else -- no prose, "
    "no markdown fences, no explanation outside the JSON."
)


# ---------------------------------------------------------------------------
# User prompt template
# ---------------------------------------------------------------------------
_USER_PROMPT_TPL = """\
I need current market price corridors for the following HVAC line items for a \
{scope_type} project in {geography} ({currency}).

For EACH line item return a JSON entry using the item INDEX (0-based integer) as the key.
Each entry must have:
  - "description_matched" : brief description of what you are pricing
  - "min_rate"            : lowest reasonable market unit rate (float, {currency} per {uom_hint})
  - "mid_rate"            : mid-market / fair value unit rate (float)
  - "max_rate"            : top-of-market unit rate (float)
  - "uom"                 : unit of measure you used (e.g. NR, m, m2, m3, LS, LM)
  - "currency"            : "{currency}"
  - "confidence"          : 0.0-1.0 float (how certain are you of these rates?)
  - "source_note"         : one sentence explaining your source/basis
  - "citations"           : list of up to 3 live web URLs used (may be empty list)

Return ONLY a JSON object with integer keys "0", "1", "2", ... -- no wrapper key.

=== LINE ITEMS ({total} items) ===
{line_items_block}
"""


class PerplexityBenchmarkService:
    """Fetch live HVAC market pricing from Perplexity sonar-pro for a benchmark request."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def fetch_prices_for_request(cls, bench_request) -> Dict[str, Any]:
        """
        Run one Perplexity API call covering all active line items of the request.

        Returns a dict keyed by BenchmarkLineItem.pk (as str) with pricing data.
        Returns {} on any error (fail-silent).
        """
        from django.conf import settings

        api_key = getattr(settings, "PERPLEXITY_API_KEY", "")
        model = getattr(settings, "PERPLEXITY_MODEL", "sonar-pro")

        if not api_key:
            logger.warning("PerplexityBenchmarkService: PERPLEXITY_API_KEY is not set.")
            return {}

        # Collect all active line items across quotations
        line_items = cls._collect_line_items(bench_request)
        if not line_items:
            logger.info(
                "PerplexityBenchmarkService: no line items found for request %s",
                bench_request.pk,
            )
            return {}

        # Build prompt
        geography = bench_request.geography or "UAE"
        currency = _GEO_CURRENCY.get(geography, "AED")
        scope_type = bench_request.scope_type or "SITC"

        prompt = cls._build_prompt(line_items, geography, currency, scope_type)

        # Call API
        raw_text = cls._call_perplexity(api_key, model, prompt)
        if raw_text is None:
            return {}

        # Parse response
        index_map = cls._parse_response(raw_text, line_items)
        return index_map

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_line_items(bench_request) -> List[Any]:
        """Return all BenchmarkLineItem objects for the request, with stable ordering."""
        from apps.benchmarking.models import BenchmarkLineItem

        return list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            ).order_by("quotation_id", "line_number")
        )

    @classmethod
    def _build_prompt(
        cls,
        line_items: List[Any],
        geography: str,
        currency: str,
        scope_type: str,
    ) -> str:
        """Build the user prompt with numbered line items."""
        rows = []
        for idx, item in enumerate(line_items):
            uom = item.uom or "NR"
            qty = float(item.quantity or 1)
            rate = float(item.quoted_unit_rate or 0)
            rows.append(
                f"  [{idx}] {item.description} | UOM: {uom} | Qty: {qty} | "
                f"Quoted rate: {rate:.2f} {currency}"
            )

        block = "\n".join(rows)

        # Guess most common UOM as hint
        uoms = [i.uom or "NR" for i in line_items]
        uom_hint = max(set(uoms), key=uoms.count) if uoms else "NR"

        return _USER_PROMPT_TPL.format(
            scope_type=scope_type,
            geography=geography,
            currency=currency,
            uom_hint=uom_hint,
            total=len(line_items),
            line_items_block=block,
        )

    @classmethod
    def _call_perplexity(cls, api_key: str, model: str, prompt: str) -> Optional[str]:
        """HTTP POST to Perplexity API. Returns raw response text or None on failure."""
        try:
            import requests  # type: ignore[import]
        except ImportError:
            logger.error("PerplexityBenchmarkService: 'requests' library is not installed.")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
            # sonar-pro specific: enable web search
            "search_domain_filter": [],
            "return_images": False,
            "return_related_questions": False,
            "search_recency_filter": "month",
        }

        try:
            resp = requests.post(
                _PERPLEXITY_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            logger.info(
                "PerplexityBenchmarkService: API call succeeded, response length=%d chars",
                len(raw),
            )
            return raw
        except Exception as exc:
            logger.exception("PerplexityBenchmarkService._call_perplexity failed: %s", exc)
            return None

    @classmethod
    def _parse_response(cls, raw_text: str, line_items: List[Any]) -> Dict[str, Any]:
        """
        Parse the Perplexity JSON response and map indices back to pk keys.

        Returns dict keyed by str(line_item.pk).
        """
        # Strip markdown code fences if present
        text = raw_text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()

        # Find outermost JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            logger.warning("PerplexityBenchmarkService: could not locate JSON in response.")
            return {}

        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            logger.warning("PerplexityBenchmarkService: JSON parse error: %s", exc)
            return {}

        result: Dict[str, Any] = {}

        for idx, item in enumerate(line_items):
            key = str(idx)
            entry = parsed.get(key) or parsed.get(idx)
            if not entry or not isinstance(entry, dict):
                logger.debug(
                    "PerplexityBenchmarkService: no entry for index %d (item pk=%d)",
                    idx,
                    item.pk,
                )
                continue

            # Coerce numeric fields
            entry = cls._coerce_entry(entry)
            if entry is None:
                continue

            result[str(item.pk)] = entry

        logger.info(
            "PerplexityBenchmarkService: parsed %d/%d line item prices from Perplexity",
            len(result),
            len(line_items),
        )
        return result

    @staticmethod
    def _coerce_entry(entry: dict) -> Optional[dict]:
        """Validate and coerce an individual price entry. Returns None if unusable."""
        required = ("min_rate", "mid_rate", "max_rate")
        for field in required:
            val = entry.get(field)
            if val is None:
                return None
            try:
                entry[field] = float(val)
            except (TypeError, ValueError):
                return None

        # Sanity: mid must be positive and between min and max
        if entry["mid_rate"] <= 0:
            return None
        if entry["min_rate"] > entry["mid_rate"]:
            entry["min_rate"] = entry["mid_rate"] * 0.8
        if entry["max_rate"] < entry["mid_rate"]:
            entry["max_rate"] = entry["mid_rate"] * 1.2

        entry.setdefault("confidence", 0.7)
        entry.setdefault("currency", "AED")
        entry.setdefault("uom", "NR")
        entry.setdefault("source_note", "Live market data from Perplexity sonar-pro")
        entry.setdefault("citations", [])
        entry.setdefault("description_matched", "")
        return entry
