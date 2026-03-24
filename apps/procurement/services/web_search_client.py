"""Web Search Client — DuckDuckGo-powered search for procurement benchmark tools.

Uses the ``ddgs`` library (no API key required) for market research queries.
Falls back to Bing Search API v7 if a Bing key is configured in settings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_RESULT_COUNT = 8


@dataclass
class SearchResult:
    """A single web search result."""
    title: str = ""
    url: str = ""
    snippet: str = ""


@dataclass
class SearchResponse:
    """Aggregated response from a web search query."""
    query: str = ""
    results: List[SearchResult] = field(default_factory=list)
    total_estimated: int = 0
    success: bool = True
    error: str = ""


class WebSearchClient:
    """Stateless web search client.  Uses DuckDuckGo by default (free,
    no API key).  If ``BING_SEARCH_API_KEY`` is set in Django settings,
    uses Bing Search v7 instead.

    Usage::

        client = WebSearchClient()
        response = client.search("Daikin RXYQ22TATF VRF price GCC 2026")
        for r in response.results:
            print(r.title, r.snippet)
    """

    def __init__(self):
        self._bing_key: str = getattr(settings, "BING_SEARCH_API_KEY", "")

    @property
    def backend(self) -> str:
        return "bing" if self._bing_key else "duckduckgo"

    def search(
        self,
        query: str,
        *,
        count: int = DEFAULT_RESULT_COUNT,
        market: str = "en-US",
        freshness: str = "",
    ) -> SearchResponse:
        """Execute a web search and return structured results.

        Args:
            query: The search query string.
            count: Number of results to request.
            market: Market/region code (e.g. "en-US", "en-AE").
            freshness: Recency filter — "Day", "Week", "Month", or "".

        Returns:
            SearchResponse with results list.
        """
        if self._bing_key:
            return self._search_bing(query, count=count, market=market, freshness=freshness)
        return self._search_ddg(query, count=count, market=market, freshness=freshness)

    # ------------------------------------------------------------------
    # DuckDuckGo backend (default — no API key needed)
    # ------------------------------------------------------------------
    def _search_ddg(
        self,
        query: str,
        *,
        count: int = DEFAULT_RESULT_COUNT,
        market: str = "en-US",
        freshness: str = "",
    ) -> SearchResponse:
        try:
            from ddgs import DDGS

            # Map freshness to DuckDuckGo timelimit
            timelimit_map = {"Day": "d", "Week": "w", "Month": "m"}
            timelimit = timelimit_map.get(freshness)

            ddgs = DDGS()
            raw_results = list(ddgs.text(
                query,
                max_results=min(count, 20),
                timelimit=timelimit,
            ))

            results = []
            for item in raw_results:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("href", ""),
                    snippet=item.get("body", ""),
                ))

            return SearchResponse(
                query=query,
                results=results,
                total_estimated=len(results),
                success=True,
            )

        except ImportError:
            logger.error("duckduckgo-search package not installed")
            return SearchResponse(
                query=query, success=False,
                error="ddgs package not installed — run: pip install ddgs",
            )
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for '%s': %s", query[:100], exc)
            return SearchResponse(
                query=query, success=False, error=str(exc),
            )

    # ------------------------------------------------------------------
    # Bing backend (used when BING_SEARCH_API_KEY is set)
    # ------------------------------------------------------------------
    def _search_bing(
        self,
        query: str,
        *,
        count: int = DEFAULT_RESULT_COUNT,
        market: str = "en-US",
        freshness: str = "",
    ) -> SearchResponse:
        import requests as _requests

        endpoint = getattr(
            settings, "BING_SEARCH_ENDPOINT",
            "https://api.bing.microsoft.com/v7.0/search",
        )
        headers = {"Ocp-Apim-Subscription-Key": self._bing_key}
        params: Dict[str, Any] = {
            "q": query,
            "count": min(count, 50),
            "mkt": market,
            "textFormat": "Raw",
            "safeSearch": "Moderate",
        }
        if freshness:
            params["freshness"] = freshness

        try:
            resp = _requests.get(endpoint, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            web_pages = data.get("webPages", {})
            results = []
            for item in web_pages.get("value", []):
                results.append(SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                ))

            return SearchResponse(
                query=query,
                results=results,
                total_estimated=web_pages.get("totalEstimatedMatches", 0),
                success=True,
            )

        except _requests.exceptions.Timeout:
            logger.warning("Bing Search timed out for query: %s", query[:100])
            return SearchResponse(query=query, success=False, error="Search request timed out")
        except _requests.exceptions.RequestException as exc:
            logger.warning("Bing Search failed for '%s': %s", query[:100], exc)
            return SearchResponse(query=query, success=False, error=str(exc))
