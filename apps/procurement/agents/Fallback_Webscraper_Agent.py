"""Fallback Web Scraper Agent.

Used automatically when Perplexity API is unavailable or fails.

Pipeline
--------
1. Ask Azure OpenAI which commercial / vendor websites to visit for this product.
2. Use Playwright (headless Chromium) to visit each URL and capture page text.
3. Ask Azure OpenAI to parse the scraped text into structured product suggestions
   in the same JSON format as PerplexityMarketResearchAnalystAgent.
4. Normalise and persist so all callers receive the same dict shape.

Entry point::

    agent = FallbackWebscraperAgent()
    result = agent.run(proc_request, generated_by=user_or_none)

Returns the same dict as PerplexityMarketResearchAnalystAgent.run():
    system_code, system_name, rephrased_query, ai_summary, market_context,
    suggestions (list), perplexity_citations (list of scraped URLs)

Requirements
------------
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Max chars to keep from a single scraped page (controls Azure OAI token cost)
_MAX_PAGE_CHARS = 6_000

# How many sites Azure OAI should suggest
_NUM_SITES = 6

# Playwright navigation timeout in milliseconds
_NAV_TIMEOUT = 30_000

# System-code -> human-readable DB name (mirror Perplexity agent)
_SYSTEM_CODE_TO_DB_NAME: dict[str, str] = {
    "SPLIT_AC":    "Split AC",
    "VRF":         "VRF System",
    "PACKAGED_DX": "Packaged Unit (Rooftop)",
    "CHILLER":     "Chilled Water System",
    "FCU":         "Chilled Water System",
    "AHU":         "Chilled Water System",
    "DUCTING":     "Ducting & Accessories",
}

_ICONS = {
    "MANUFACTURER": "bi-building",
    "DISTRIBUTOR":  "bi-truck",
    "OTHER":        "bi-link-45deg",
}

# Phrases that indicate a bot-detection or challenge page (case-insensitive check)
_BOT_WALL_PHRASES = [
    "access denied",
    "enable javascript",
    "verify you are human",
    "checking your browser",
    "cloudflare",
    "captcha",
    "ddos protection",
    "just a moment",
    "ray id",
    "error 1020",
    "403 forbidden",
    "robot or human",
    "automated access",
    "suspicious activity",
]

# ---------------------------------------------------------------------------
# Prompt: Step 1 -- ask Azure OAI which sites to scrape
# ---------------------------------------------------------------------------
_SITE_SELECTION_PROMPT = (
    "You are a senior HVAC procurement sourcing analyst.\n"
    "A procurement team needs to buy {system_name} products.\n\n"
    "Request details:\n"
    "  Title:       {title}\n"
    "  Description: {description}\n"
    "  Location:    {city}, {country}\n"
    "  Budget tier: {priority}\n\n"
    "Task: suggest exactly {num_sites} SPECIFIC URLS that a Playwright browser\n"
    "should visit RIGHT NOW to find real {system_name} products with:\n"
    "  - A product LISTING page (not a homepage, not a category root page)\n"
    "  - Vendor contact details or enquiry button\n"
    "  - Price or price range visible\n\n"
    "Source priority order (use this order):\n"
    "  1. B2B marketplace product search pages:\n"
    "       Alibaba (alibaba.com/trade/search?...), IndiaMART (dir.indiamart.com/...),\n"
    "       Tradeindia (tradeindia.com/search.html?...), made-in-china.com\n"
    "  2. UAE/GCC HVAC distributor or dealer catalogue / product pages\n"
    "  3. Manufacturer product page with a 'Get Quote' or 'Find Dealer' form\n\n"
    "For each URL provide:\n"
    "  - site_name:       friendly label (e.g. 'Alibaba - VRF System listings')\n"
    "  - url:             REAL, CURRENTLY ACCESSIBLE product listing or search URL\n"
    "  - search_query:    what a human would type on that site\n"
    "  - what_to_extract: brief note on what to look for\n\n"
    "Respond ONLY with a JSON array of exactly {num_sites} objects:\n"
    "[\n"
    "  {{\n"
    '    "site_name": "...",\n'
    '    "url": "https://...",\n'
    '    "search_query": "...",\n'
    '    "what_to_extract": "..."\n'
    "  }},\n"
    "  ...\n"
    "]\n"
    "Return NOTHING except the JSON array."
)

# ---------------------------------------------------------------------------
# Prompt: Step 3 -- ask Azure OAI to parse scraped text into JSON suggestions
# ---------------------------------------------------------------------------
_PARSE_PRODUCTS_PROMPT = (
    "You are an HVAC procurement analyst.\n"
    "Below is raw text scraped from {num_pages} vendor/marketplace web pages.\n\n"
    "Extract real {system_name} product suggestions for this procurement request:\n"
    "  Title:    {title}\n"
    "  Desc:     {description}\n"
    "  Location: {city}, {country}\n\n"
    "===SCRAPED PAGES===\n"
    "{scraped_text}\n"
    "===END SCRAPED PAGES===\n\n"
    "Using ONLY information visible in the scraped pages above, return a JSON object:\n"
    "{{\n"
    '  "rephrased_query": "<one-line product search query>",\n'
    '  "ai_summary": "<2-3 sentence summary of what was found across all pages>",\n'
    '  "market_context": "<brief note on availability, pricing trends, lead times>",\n'
    '  "suggestions": [\n'
    "    {{\n"
    '      "rank": 1,\n'
    '      "product_name": "<product name from the scraped page>",\n'
    '      "manufacturer": "<brand>",\n'
    '      "model_code": "<model code if visible, else empty string>",\n'
    '      "system_type": "{system_name}",\n'
    '      "cooling_capacity": "<e.g. 8 TR - 12 TR, or empty string>",\n'
    '      "cop_eer": "<e.g. COP 3.8, or empty string>",\n'
    '      "price_range_aed": "<price in AED from the page -- MUST NOT be blank; use ~X AED est. if not shown>",\n'
    '      "market_availability": "<stock / lead time note from the page>",\n'
    '      "key_benefits": ["benefit1", "benefit2"],\n'
    '      "limitations": ["limitation1"],\n'
    '      "fit_score": 75,\n'
    '      "fit_rationale": "<one sentence why this product matches the request>",\n'
    '      "standards_compliance": [],\n'
    '      "citation_index": 0,\n'
    '      "citation_url": "<EXACT URL of the page this product was found on>",\n'
    '      "citation_source": "<site name>",\n'
    '      "price_citation_index": 0,\n'
    '      "price_source_url": "<EXACT URL where the price was found -- same as citation_url if on the same page>",\n'
    '      "category": "DISTRIBUTOR"\n'
    "    }}\n"
    "  ]\n"
    "}}\n\n"
    "Rules:\n"
    "- citation_url MUST be one of the scraped page URLs above -- copy it exactly.\n"
    "- Return 5 to 7 suggestions from DIFFERENT manufacturers or vendors.\n"
    "- Do NOT invent product specs not present in the scraped text.\n"
    "- Rank by fit_score descending.\n"
    "- Respond with ONLY the JSON object and nothing else."
)


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class FallbackWebscraperAgent:
    """Fallback market intelligence agent.

    Step 1: Azure OpenAI selects which vendor/marketplace URLs to scrape.
    Step 2: Playwright headless Chromium visits each URL and captures page text.
    Step 3: Azure OpenAI parses the scraped text into structured product suggestions.
    Step 4: Normalise and persist -- same format as PerplexityMarketResearchAnalystAgent.

    Used automatically by MarketIntelligenceService when Perplexity fails.
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, proc_request: Any, generated_by: Any = None) -> dict:
        """Execute the web-scraping fallback pipeline.

        Args:
            proc_request: ProcurementRequest model instance.
            generated_by: User instance or None.

        Returns:
            dict with keys: system_code, system_name, rephrased_query, ai_summary,
            market_context, suggestions, perplexity_citations.

        Raises:
            ValueError: Azure OAI not configured, or scraping returned no data.
            ImportError: playwright not installed.
        """
        from apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent import (
            PerplexityMarketResearchAnalystAgent,
        )

        rec_block, system_code, system_name = (
            PerplexityMarketResearchAnalystAgent.get_rec_context(proc_request)
        )
        db_system_name = _SYSTEM_CODE_TO_DB_NAME.get(system_code, system_name)
        prompt_system_name = db_system_name or system_name or "HVAC System"

        logger.info(
            "FallbackWebscraperAgent.run: pk=%s system=%s",
            proc_request.pk, prompt_system_name,
        )

        # 1. Ask Azure OAI which sites to scrape
        sites = self._ask_sites(proc_request, prompt_system_name)
        logger.info("FallbackWebscraperAgent: %d sites selected by Azure OAI", len(sites))

        # 2. Playwright scrape
        scraped = self._scrape_sites(sites)
        logger.info("FallbackWebscraperAgent: %d pages scraped", len(scraped))

        if not scraped:
            raise ValueError(
                "FallbackWebscraperAgent: Playwright could not load any of the "
                "suggested URLs. Ensure Playwright + Chromium are installed: "
                "'pip install playwright && playwright install chromium'."
            )

        # 3. Ask Azure OAI to parse the scraped text
        data = self._parse_scraped(proc_request, prompt_system_name, scraped)
        scraped_urls = [s["url"] for s in scraped]

        # 4. Normalise
        suggestions = self._normalise_suggestions(
            suggestions=data.get("suggestions", []),
            scraped_urls=scraped_urls,
        )

        # 5. Persist
        self._persist(
            proc_request=proc_request,
            generated_by=generated_by,
            data=data,
            system_code=system_code,
            system_name=system_name,
            suggestions=suggestions,
            scraped_urls=scraped_urls,
        )

        logger.info(
            "FallbackWebscraperAgent.run: done pk=%s suggestions=%d",
            proc_request.pk, len(suggestions),
        )

        return {
            "system_code":        system_code,
            "system_name":        system_name,
            "rephrased_query":    data.get("rephrased_query", ""),
            "ai_summary":         data.get("ai_summary", "(Generated via web scraping fallback)"),
            "market_context":     data.get("market_context", ""),
            "suggestions":        suggestions,
            # Same key as Perplexity agent -- here it holds the scraped page URLs
            "perplexity_citations": scraped_urls,
        }

    # ------------------------------------------------------------------
    # Step 1: Ask Azure OAI which sites to scrape
    # ------------------------------------------------------------------

    def _ask_sites(self, proc_request: Any, system_name: str) -> list[dict]:
        """Return a list of site dicts [{site_name, url, search_query, what_to_extract}]."""
        prompt = _SITE_SELECTION_PROMPT.format(
            system_name=system_name,
            title=proc_request.title,
            description=proc_request.description or "(not provided)",
            country=proc_request.geography_country or "UAE",
            city=proc_request.geography_city or "",
            priority=proc_request.priority,
            num_sites=_NUM_SITES,
        )

        response_text = self._ask_azure_openai(
            system_msg=(
                "You are an HVAC procurement sourcing analyst who knows exactly "
                "which commercial websites carry HVAC products. "
                "Respond ONLY with a JSON array as instructed."
            ),
            user_msg=prompt,
            max_tokens=1_500,
        )

        # Log raw response so developers can see exactly what OpenAI suggested
        logger.info(
            "FallbackWebscraperAgent._ask_sites: raw OpenAI response[:500]=%r",
            response_text[:500],
        )

        parsed = self._extract_json(response_text)

        if isinstance(parsed, list):
            sites = [s for s in parsed if isinstance(s, dict) and s.get("url", "").startswith("http")]
        elif isinstance(parsed, dict) and isinstance(parsed.get("sites"), list):
            sites = [s for s in parsed["sites"] if isinstance(s, dict) and s.get("url", "").startswith("http")]
        else:
            logger.warning(
                "FallbackWebscraperAgent._ask_sites: unexpected structure, using hardcoded fallback. "
                "raw[:300]=%r", str(response_text)[:300],
            )
            sites = []

        if not sites:
            logger.warning("FallbackWebscraperAgent._ask_sites: no valid URLs returned, using hardcoded fallback")
            sites = self._fallback_sites(system_name)

        # Log the final URL list so you can see whether OpenAI gave good or bad URLs
        logger.info(
            "FallbackWebscraperAgent._ask_sites: %d URLs selected: %s",
            len(sites[:_NUM_SITES]),
            [s.get("url", "") for s in sites[:_NUM_SITES]],
        )

        return sites[:_NUM_SITES]

    @staticmethod
    def _fallback_sites(system_name: str) -> list[dict]:
        """Hardcoded B2B marketplace search URLs used when Azure OAI returns unusable data."""
        q = system_name.replace(" ", "+")
        return [
            {
                "site_name": f"Alibaba - {system_name}",
                "url": f"https://www.alibaba.com/trade/search?SearchText={q}&IndexArea=product_en",
                "search_query": system_name,
                "what_to_extract": "Product listings with prices and supplier contact button",
            },
            {
                "site_name": f"IndiaMART - {system_name}",
                "url": f"https://www.indiamart.com/search.mp?ss={q}",
                "search_query": system_name,
                "what_to_extract": "Product listings with prices and supplier details",
            },
            {
                "site_name": f"Tradeindia - {system_name}",
                "url": f"https://www.tradeindia.com/search.html?query={q}",
                "search_query": system_name,
                "what_to_extract": "Product listings with prices",
            },
            {
                "site_name": f"Made-in-China - {system_name}",
                "url": f"https://www.made-in-china.com/multi-search/{q}/F1/",
                "search_query": system_name,
                "what_to_extract": "Product listings with manufacturer and price",
            },
        ]

    # ------------------------------------------------------------------
    # Step 2: Playwright page scraping
    # ------------------------------------------------------------------

    def _scrape_sites(self, sites: list[dict]) -> list[dict]:
        """Visit each URL with Playwright headless Chromium.

        Returns list of dicts: {site_name, url, text, what_to_extract}.
        Pages that fail to load are silently skipped.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Run:\n"
                "    pip install playwright\n"
                "    playwright install chromium"
            )

        results = []

        with sync_playwright() as pw:
            # ---------------------------------------------------------------
            # Anti-bot Chromium launch: disable automation flags so sites
            # don't immediately fingerprint this as a headless bot.
            # ---------------------------------------------------------------
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/webp,*/*;q=0.8"
                    ),
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            # Override navigator.webdriver so sites see False instead of True
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.new_page()

            for site in sites:
                url = site.get("url", "")
                if not url.startswith("http"):
                    logger.warning("FallbackWebscraperAgent: skipping non-http URL %r", url)
                    continue
                try:
                    logger.info("FallbackWebscraperAgent: visiting %s", url)
                    page.goto(url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")

                    # Try to wait for full network quiet (JS-rendered content);
                    # many B2B sites inject product cards after initial HTML load.
                    try:
                        page.wait_for_load_state("networkidle", timeout=6_000)
                    except Exception:  # noqa: BLE001
                        # networkidle timed out -- continue with what we have
                        page.wait_for_timeout(3_000)

                    # Extract visible body text; strip chrome/navigation elements
                    text = page.evaluate(
                        """() => {
                            ['script','style','nav','header','footer','aside','iframe']
                                .forEach(tag =>
                                    document.querySelectorAll(tag).forEach(el => el.remove())
                                );
                            return document.body ? document.body.innerText : '';
                        }"""
                    )
                    text = _clean_text(text)

                    logger.info(
                        "FallbackWebscraperAgent: %s -> %d chars scraped",
                        url, len(text),
                    )

                    if len(text) < 80:
                        logger.warning(
                            "FallbackWebscraperAgent: very little text from %s (%d chars) -- skipping",
                            url, len(text),
                        )
                        continue

                    # Detect bot-detection / challenge pages
                    if _is_bot_wall(text):
                        logger.warning(
                            "FallbackWebscraperAgent: bot-wall detected at %s -- skipping. "
                            "first 200 chars: %r",
                            url, text[:200],
                        )
                        continue

                    results.append({
                        "site_name":        site.get("site_name", url),
                        "url":              url,
                        "text":             text[:_MAX_PAGE_CHARS],
                        "what_to_extract":  site.get("what_to_extract", "product listings"),
                    })
                    logger.info(
                        "FallbackWebscraperAgent: OK -- %s (%d chars kept)",
                        url, min(len(text), _MAX_PAGE_CHARS),
                    )

                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "FallbackWebscraperAgent._scrape_sites: failed %s: %s",
                        url, exc,
                    )

            page.close()
            ctx.close()
            browser.close()

        logger.info(
            "FallbackWebscraperAgent._scrape_sites: %d/%d pages successfully scraped",
            len(results), len(sites),
        )
        return results

    # ------------------------------------------------------------------
    # Step 3: Ask Azure OAI to parse scraped content
    # ------------------------------------------------------------------

    def _parse_scraped(self, proc_request: Any, system_name: str, scraped: list[dict]) -> dict:
        """Ask Azure OpenAI to extract product suggestions from scraped page text."""
        parts = []
        for i, s in enumerate(scraped, 1):
            parts.append(
                f"--- PAGE {i}: {s['site_name']} ---\n"
                f"URL: {s['url']}\n\n"
                + textwrap.shorten(s["text"], width=_MAX_PAGE_CHARS, placeholder="...[truncated]")
            )
        scraped_text = "\n\n".join(parts)

        prompt = _PARSE_PRODUCTS_PROMPT.format(
            num_pages=len(scraped),
            system_name=system_name,
            title=proc_request.title,
            description=proc_request.description or "(not provided)",
            country=proc_request.geography_country or "UAE",
            city=proc_request.geography_city or "",
            scraped_text=scraped_text,
        )

        response_text = self._ask_azure_openai(
            system_msg=(
                "You are an HVAC procurement analyst. "
                "Extract structured product data from scraped web text. "
                "Respond ONLY with the JSON object -- no code fences, no prose."
            ),
            user_msg=prompt,
            max_tokens=4_096,
        )

        data = self._extract_json(response_text)

        if not isinstance(data, dict) or not data.get("suggestions"):
            logger.error(
                "FallbackWebscraperAgent._parse_scraped: unusable response. raw[:500]=%r",
                str(response_text)[:500],
            )
            raise ValueError(
                "FallbackWebscraperAgent: Azure OpenAI could not extract product "
                "suggestions from the scraped content. No suggestions returned."
            )

        return data

    # ------------------------------------------------------------------
    # Azure OpenAI helper
    # ------------------------------------------------------------------

    @staticmethod
    def _ask_azure_openai(system_msg: str, user_msg: str, max_tokens: int = 2_000) -> str:
        """Make a single Azure OpenAI chat completion call and return the raw text content."""
        from django.conf import settings

        endpoint  = getattr(settings, "AZURE_OPENAI_ENDPOINT",   "")
        api_key   = getattr(settings, "AZURE_OPENAI_API_KEY",     "")
        deployment = getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        api_ver   = getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-02-01")

        if not endpoint or not api_key:
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be configured "
                "to use the web-scraping fallback agent."
            )

        try:
            import requests as _req
        except ImportError:
            raise ImportError("'requests' library is required")

        url = (
            f"{endpoint.rstrip('/')}/openai/deployments/{deployment}"
            f"/chat/completions?api-version={api_ver}"
        )
        headers = {"api-key": api_key, "Content-Type": "application/json"}
        payload = {
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens":  max_tokens,
        }

        resp = _req.post(url, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()

        choices = (resp.json().get("choices") or [])
        if not choices:
            raise ValueError(
                f"Azure OpenAI returned no choices. Keys: {list(resp.json().keys())}"
            )
        return (choices[0].get("message") or {}).get("content") or ""

    # ------------------------------------------------------------------
    # Normalise suggestions
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_suggestions(suggestions: list, scraped_urls: list[str]) -> list:
        """Apply icon class, clamp fit_score, and validate citation URLs."""
        for i, s in enumerate(suggestions):
            # Icon
            cat = s.get("category", "DISTRIBUTOR").upper()
            s["icon_class"] = _ICONS.get(cat, "bi-building")

            # Clamp fit_score
            try:
                s["fit_score"] = max(0, min(100, int(s.get("fit_score", 0))))
            except (TypeError, ValueError):
                s["fit_score"] = 0

            # citation_url -- must be a real http URL
            cit = s.get("citation_url", "")
            if not (isinstance(cit, str) and cit.startswith("http")):
                s["citation_url"] = scraped_urls[i % len(scraped_urls)] if scraped_urls else ""

            # price_source_url
            psu = s.get("price_source_url", "")
            if not (isinstance(psu, str) and psu.startswith("http")):
                s["price_source_url"] = s.get("citation_url", "")

            # Not checked against approved source registry (scraping fallback)
            s["is_approved_source"] = False
            s.setdefault("citation_source", "web scrape fallback")

        return suggestions

    # ------------------------------------------------------------------
    # Persist to DB
    # ------------------------------------------------------------------

    @staticmethod
    def _persist(
        proc_request: Any,
        generated_by: Any,
        data: dict,
        system_code: str,
        system_name: str,
        suggestions: list,
        scraped_urls: list[str],
    ) -> None:
        """Save MarketIntelligenceSuggestion to DB (fail-silent)."""
        from apps.procurement.models import MarketIntelligenceSuggestion
        try:
            MarketIntelligenceSuggestion.objects.create(
                request=proc_request,
                generated_by=generated_by,
                rephrased_query=data.get("rephrased_query", ""),
                ai_summary=data.get("ai_summary", ""),
                market_context=data.get("market_context", ""),
                system_code=system_code,
                system_name=system_name,
                suggestions_json=suggestions,
                suggestion_count=len(suggestions),
                perplexity_citations_json=scraped_urls,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FallbackWebscraperAgent._persist: DB save failed pk=%s: %s",
                proc_request.pk, exc,
            )

    # ------------------------------------------------------------------
    # JSON extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Strip markdown fences and parse JSON from Azure OAI response text."""
        text = text.strip()

        # 1. Complete ```...``` fence
        fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        else:
            # 2. Dangling opening fence with no closing fence
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()

        # 3. Jump to first { or [
        if text and text[0] not in "{[":
            start = min(
                (text.find(c) for c in "{[" if text.find(c) != -1),
                default=-1,
            )
            if start != -1:
                text = text[start:]

        # 4. Trim trailing prose after the last } or ]
        if text:
            last = max(text.rfind("}"), text.rfind("]"))
            if last != -1:
                text = text[: last + 1]

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "FallbackWebscraperAgent._extract_json: parse failed: %s | text[:300]=%r",
                exc, text[:300],
            )
            return {}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Collapse tabs and excessive blank lines from scraped page text."""
    text = re.sub(r"\t+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_bot_wall(text: str) -> bool:
    """Return True if the scraped text looks like a bot-detection / challenge page.

    Checks for known phrases from Cloudflare, IndiaMART bot guards, Alibaba
    anti-scrape pages, and generic 403 / forbidden responses.
    Only flags short pages (<= 1 000 chars) to avoid false positives on large
    legitimate pages that happen to mention 'captcha' in passing.
    """
    if len(text) > 1_000:
        return False
    lower = text.lower()
    return any(phrase in lower for phrase in _BOT_WALL_PHRASES)
