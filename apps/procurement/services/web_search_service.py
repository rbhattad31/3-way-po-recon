"""WebSearchService -- benchmark price lookup via public search engines.

Used as the last-resort fallback when no internal DB benchmark data is
available for a line item. Queries DuckDuckGo Instant Answer API (free,
no API key) first, then falls back to a lightweight Bing search scrape.

All prices returned are indicative estimates and marked source="WEB_SEARCH"
so downstream variance logic can apply a lower confidence weight.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Attempt to import requests; fall back to urllib if unavailable
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

_DDG_API = "https://api.duckduckgo.com/"
_BING_SEARCH = "https://www.bing.com/search"
_USER_AGENT = "Mozilla/5.0 (compatible; ProcurementBot/1.0; +https://procurement-platform)"
_TIMEOUT = 8  # seconds


def _http_get(url: str, params: Dict[str, str]) -> Optional[str]:
    """Fetch URL body as text. Uses requests if available, else urllib."""
    if _REQUESTS_AVAILABLE:
        try:
            resp = _requests.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            logger.debug("WebSearchService._http_get requests error: %s", exc)
            return None
    # urllib fallback
    try:
        import urllib.request as _urlreq
        full_url = url + "?" + urllib.parse.urlencode(params)
        req = _urlreq.Request(full_url, headers={"User-Agent": _USER_AGENT})
        with _urlreq.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("WebSearchService._http_get urllib error: %s", exc)
        return None


def _extract_prices_from_text(text: str) -> List[float]:
    """Extract numeric price values from a free-text snippet.

    Recognises patterns such as:
      AED 2,500  |  $1200  |  USD 800 to 1200  |  1,500 AED  |  AED 500 - 3,000
    Returns a sorted list of unique floats (may be empty).
    """
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Normalise separators
    text = text.replace(",", "")

    # Match: optional currency prefix, number, optional range second number
    pattern = re.compile(
        r"(?:AED|USD|SAR|KWD|QAR|OMR|BHD|EUR|GBP|INR|\$|Rs\.?)?\s*"
        r"(\d+(?:\.\d+)?)"
        r"(?:\s*(?:to|-|--|--)\s*(\d+(?:\.\d+)?))?",
        re.IGNORECASE,
    )
    found: List[float] = []
    for m in pattern.finditer(text):
        try:
            found.append(float(m.group(1)))
        except ValueError:
            pass
        if m.group(2):
            try:
                found.append(float(m.group(2)))
            except ValueError:
                pass

    # Filter out obviously wrong values (0, very small, very large)
    found = [v for v in found if 10 < v < 10_000_000]
    return sorted(set(found))


def _prices_to_corridor(prices: List[float]) -> Dict[str, Any]:
    """Convert a list of scraped price points into min/avg/max corridor."""
    if not prices:
        return {}
    lo = min(prices)
    hi = max(prices)
    avg = sum(prices) / len(prices)
    return {
        "min": Decimal(str(round(lo, 2))),
        "avg": Decimal(str(round(avg, 2))),
        "max": Decimal(str(round(hi, 2))),
    }


class WebSearchService:
    """Fetches indicative benchmark pricing from public search engines.

    Intended for use when the internal benchmark catalogue has no data
    for a given line item. Results are clearly marked as WEB_SEARCH
    sourced and carry a lower confidence weight in variance analysis.

    Usage:
        result = WebSearchService.search_benchmark(
            description="VRF outdoor unit 10 TR",
            geography="UAE",
            uom="UNIT",
            currency="AED",
        )
        # result: { min, avg, max, source, query, confidence, notes }
    """

    @staticmethod
    def search_benchmark(
        description: str,
        geography: str = "UAE",
        uom: str = "",
        currency: str = "AED",
    ) -> Dict[str, Any]:
        """Search for benchmark pricing and return a corridor dict.

        Returns:
          {
            "min": Decimal or None,
            "avg": Decimal or None,
            "max": Decimal or None,
            "source": "WEB_SEARCH",
            "query": str,
            "confidence": float,   # always <= 0.45 for web-scraped data
            "notes": str,
          }
        """
        query = WebSearchService._build_query(description, geography, currency)
        logger.info("WebSearchService: searching for benchmark | query='%s'", query)

        prices: List[float] = []
        notes_parts: List[str] = []

        # 1. DuckDuckGo Instant Answer API
        ddg_prices = WebSearchService._search_ddg(query)
        if ddg_prices:
            prices.extend(ddg_prices)
            notes_parts.append("DuckDuckGo IA results used.")
            logger.info("WebSearchService: DDG returned %d price points.", len(ddg_prices))

        # 2. Bing fallback when DDG returns nothing useful
        if not prices:
            bing_prices = WebSearchService._search_bing(query)
            if bing_prices:
                prices.extend(bing_prices)
                notes_parts.append("Bing search results used.")
                logger.info("WebSearchService: Bing returned %d price points.", len(bing_prices))

        if not prices:
            logger.info("WebSearchService: no prices found for query='%s'", query)
            return {
                "min": None,
                "avg": None,
                "max": None,
                "source": "WEB_SEARCH",
                "query": query,
                "confidence": 0.0,
                "notes": "No benchmark data found via web search.",
            }

        corridor = _prices_to_corridor(prices)
        notes = " ".join(notes_parts) + (
            f" Prices in {currency} ({geography}) — indicative only, manual validation required."
        )

        return {
            "min": corridor.get("min"),
            "avg": corridor.get("avg"),
            "max": corridor.get("max"),
            "source": "WEB_SEARCH",
            "query": query,
            "confidence": 0.35,  # Always low -- web scraped data
            "notes": notes,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(description: str, geography: str, currency: str) -> str:
        """Build a search query optimised for price discovery."""
        geo = geography.strip().upper()
        geo_labels = {
            "UAE": "Dubai UAE",
            "SAUDI ARABIA": "Riyadh Saudi Arabia",
            "KSA": "Riyadh Saudi Arabia",
            "KUWAIT": "Kuwait",
            "QATAR": "Doha Qatar",
            "BAHRAIN": "Manama Bahrain",
            "OMAN": "Muscat Oman",
        }
        geo_label = geo_labels.get(geo, geography)
        return f"{description} price cost {geo_label} {currency} 2024"

    @staticmethod
    def _search_ddg(query: str) -> List[float]:
        """Query DuckDuckGo Instant Answer API and extract prices."""
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        body = _http_get(_DDG_API, params)
        if not body:
            return []
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return []

        texts: List[str] = []
        if data.get("Abstract"):
            texts.append(data["Abstract"])
        for topic in data.get("RelatedTopics", []):
            if isinstance(topic, dict) and topic.get("Text"):
                texts.append(topic["Text"])
            elif isinstance(topic, dict):
                for sub in topic.get("Topics", []):
                    if isinstance(sub, dict) and sub.get("Text"):
                        texts.append(sub["Text"])

        all_prices: List[float] = []
        for t in texts:
            all_prices.extend(_extract_prices_from_text(t))
        return all_prices

    @staticmethod
    def _search_bing(query: str) -> List[float]:
        """Scrape Bing search result snippets and extract prices."""
        params = {"q": query, "count": "5"}
        body = _http_get(_BING_SEARCH, params)
        if not body:
            return []

        # Extract result captions between common Bing snippet tags
        snippet_pattern = re.compile(
            r'class="[^"]*b_caption[^"]*".*?</div>|'
            r'<p\s[^>]*>(.*?)</p>|'
            r'["\']([\w\s,\.]+(?:AED|USD|SAR)[^"\']{1,80})["\']',
            re.DOTALL | re.IGNORECASE,
        )
        texts: List[str] = []
        for m in snippet_pattern.finditer(body):
            snippet = m.group(0) or ""
            if any(kw in snippet.upper() for kw in ("AED", "USD", "SAR", "PRICE", "COST", "RATE")):
                texts.append(snippet)

        # Also run on raw body for price patterns directly
        texts.append(body[:8000])  # Limit to first 8K chars

        all_prices: List[float] = []
        for t in texts:
            all_prices.extend(_extract_prices_from_text(t))
        return all_prices

    # ------------------------------------------------------------------
    # Product / spec search (used by recommendation pipeline)
    # ------------------------------------------------------------------

    @staticmethod
    def search_product_info(
        system_type: str,
        capacity_tr: Optional[float] = None,
        geography: str = "UAE",
        currency: str = "AED",
        extra_keywords: str = "",
    ) -> Dict[str, Any]:
        """Search for HVAC product specs, brand options, and pricing in a given region.

        Returns:
          {
            "snippets": list[str],     -- raw text excerpts from search results
            "pricing": dict,           -- corridor dict (min/avg/max) if prices found
            "query": str,              -- the search query used
            "source": "WEB_SEARCH",
            "confidence": float,
            "notes": str,
          }
        """
        geo_labels = {
            "UAE": "Dubai UAE",
            "SAUDI ARABIA": "Riyadh Saudi Arabia",
            "KSA": "Riyadh Saudi Arabia",
            "KUWAIT": "Kuwait",
            "QATAR": "Doha Qatar",
            "BAHRAIN": "Manama Bahrain",
            "OMAN": "Muscat Oman",
        }
        geo_label = geo_labels.get(geography.strip().upper(), geography)

        # Build a natural language query
        cap_str = f"{int(capacity_tr)}TR " if capacity_tr else ""
        extra = (" " + extra_keywords.strip()) if extra_keywords else ""
        query = (
            f"{system_type.replace('_', ' ')} {cap_str}HVAC "
            f"price specifications brands {geo_label} {currency} 2025{extra}"
        )
        logger.info("WebSearchService.search_product_info: query='%s'", query)

        snippets: List[str] = []
        all_prices: List[float] = []

        # 1. DuckDuckGo snippets
        ddg_params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        ddg_body = _http_get(_DDG_API, ddg_params)
        if ddg_body:
            try:
                data = json.loads(ddg_body)
                for key in ("Abstract", "Answer", "Definition"):
                    if data.get(key):
                        snippets.append(data[key])
                for topic in data.get("RelatedTopics", [])[:8]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        snippets.append(topic["Text"])
                        all_prices.extend(_extract_prices_from_text(topic["Text"]))
            except Exception:
                pass

        # 2. Bing snippets when DDG is thin
        bing_body = _http_get(_BING_SEARCH, {"q": query, "count": "8"})
        if bing_body:
            # Extract visible text between <p> tags and .b_caption divs
            p_texts = re.findall(r"<p[^>]*>([^<]{20,400})</p>", bing_body, re.IGNORECASE)
            for text in p_texts[:10]:
                clean = re.sub(r"<[^>]+>", " ", text).strip()
                if len(clean) > 20:
                    snippets.append(clean)
                    all_prices.extend(_extract_prices_from_text(clean))

        # Deduplicate and trim snippets
        seen: set = set()
        clean_snippets: List[str] = []
        for s in snippets:
            key = s[:60]
            if key not in seen:
                seen.add(key)
                clean_snippets.append(s[:400])
        clean_snippets = clean_snippets[:12]

        pricing = _prices_to_corridor([p for p in all_prices if p > 100]) if all_prices else {}

        return {
            "snippets": clean_snippets,
            "pricing": {
                "min": str(pricing.get("min")) if pricing.get("min") else None,
                "avg": str(pricing.get("avg")) if pricing.get("avg") else None,
                "max": str(pricing.get("max")) if pricing.get("max") else None,
            },
            "query": query,
            "source": "WEB_SEARCH",
            "confidence": 0.40 if clean_snippets else 0.0,
            "notes": (
                f"Live web data for {system_type} in {geo_label} ({currency}). "
                "Indicative only -- validate before use."
            ),
        }
