"""WebSearchService -- lightweight market-data helper for procurement agents.

This module provides non-blocking web context payloads for recommendation flows
and market-rate lookups using dynamic web data.
"""
from __future__ import annotations

import re
from statistics import mean
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup


class WebSearchService:
    """Market-data helper used by recommendation graph and procurement tools."""

    _SEARCH_URL = "https://duckduckgo.com/html/"
    _TIMEOUT_SECONDS = 8
    _MAX_RESULTS = 8

    @staticmethod
    def _build_query(*parts: str) -> str:
        tokens = [str(part).strip() for part in parts if str(part).strip()]
        return " ".join(tokens)

    @staticmethod
    def _extract_numeric_prices(text: str) -> List[float]:
        if not text:
            return []
        pattern = re.compile(
            r"(?:AED|USD|SAR|QAR|OMR|KWD|BHD|INR|PKR|EUR|GBP|\$|₹|د\.إ)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            flags=re.IGNORECASE,
        )
        values: List[float] = []
        for match in pattern.findall(text):
            try:
                numeric = float(match.replace(",", ""))
            except ValueError:
                continue
            if 1 <= numeric <= 100000000:
                values.append(numeric)
        return values

    @staticmethod
    def _fetch_snippets(query: str) -> List[Dict[str, Any]]:
        if not query:
            return []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        response = requests.get(
            WebSearchService._SEARCH_URL,
            params={"q": query},
            headers=headers,
            timeout=WebSearchService._TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        results: List[Dict[str, Any]] = []
        for block in soup.select(".result")[: WebSearchService._MAX_RESULTS]:
            title_el = block.select_one(".result__a")
            snippet_el = block.select_one(".result__snippet")
            if not title_el:
                continue
            title = " ".join(title_el.get_text(" ", strip=True).split())
            summary = ""
            if snippet_el:
                summary = " ".join(snippet_el.get_text(" ", strip=True).split())
            href = title_el.get("href") or ""
            results.append(
                {
                    "source": "web_search",
                    "title": title,
                    "summary": summary,
                    "url": href,
                }
            )
        return results

    @staticmethod
    def _build_pricing(snippets: List[Dict[str, Any]], currency: str) -> Dict[str, Any]:
        prices: List[float] = []
        for item in snippets:
            prices.extend(WebSearchService._extract_numeric_prices(item.get("summary") or ""))
            prices.extend(WebSearchService._extract_numeric_prices(item.get("title") or ""))

        if not prices:
            return {
                "min": None,
                "avg": None,
                "max": None,
                "unit": currency,
                "confidence": 0.0,
                "basis": "dynamic_web_no_prices_found",
            }

        return {
            "min": round(min(prices), 2),
            "avg": round(mean(prices), 2),
            "max": round(max(prices), 2),
            "unit": currency,
            "confidence": 0.55,
            "basis": "dynamic_web_search",
        }

    @staticmethod
    def search_product_info(
        *,
        system_type: str,
        capacity_tr: float | None = None,
        geography: str = "UAE",
        currency: str = "AED",
        extra_keywords: str = "",
    ) -> Dict[str, Any]:
        """Return market snippets + dynamically extracted pricing."""
        system_key = str(system_type or "").strip() or "market equipment"
        query = WebSearchService._build_query(system_key, geography, currency, extra_keywords)

        snippets: List[Dict[str, Any]] = []
        pricing: Dict[str, Any]
        error_note = ""
        try:
            snippets = WebSearchService._fetch_snippets(query)
            pricing = WebSearchService._build_pricing(snippets, currency)
        except Exception as exc:
            pricing = {
                "min": None,
                "avg": None,
                "max": None,
                "unit": currency,
                "confidence": 0.0,
                "basis": "dynamic_web_failed",
            }
            error_note = str(exc)

        return {
            "source": "WEB_DYNAMIC_SEARCH",
            "system_type": system_key,
            "geography": geography,
            "currency": currency,
            "capacity_tr": capacity_tr,
            "snippets": snippets,
            "pricing": pricing,
            "notes": (
                "Derived from dynamic web search results. "
                + (f"Fetch error: {error_note}" if error_note else "")
            ).strip(),
        }

    @staticmethod
    def search_market_rate(
        *,
        description: str,
        geography: str = "UAE",
        uom: str = "",
        currency: str = "AED",
    ) -> Dict[str, Any]:
        """Return a market-rate payload for tool callers."""
        system_key = str(description or "").strip() or "market equipment"

        product_info = WebSearchService.search_product_info(
            system_type=system_key,
            geography=geography,
            currency=currency,
            extra_keywords=uom,
        )
        pricing = product_info.get("pricing") or {}

        return {
            "description": description,
            "geography": geography,
            "uom": uom,
            "currency": currency,
            "market_min": pricing.get("min"),
            "market_avg": pricing.get("avg"),
            "market_max": pricing.get("max"),
            "source": product_info.get("source"),
            "system_type": system_key,
            "notes": product_info.get("notes"),
        }
