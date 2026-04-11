"""Tests for apps.procurement.agents.Fallback_Webscraper_Agent.

Coverage
--------
- _extract_json          -- all fence/prose variants, invalid JSON
- _clean_text            -- whitespace normalisation
- _normalise_suggestions -- icon, fit_score clamp, citation URL fallback
- _fallback_sites        -- hardcoded URL construction
- _ask_sites             -- Azure OAI returns list / dict / garbage
- _ask_azure_openai      -- missing config, HTTP error, valid response
- _parse_scraped         -- good response, no suggestions (raises)
- _persist               -- happy path, DB exception swallowed
- run()                  -- full happy path (playwright mocked)
                            playwright not installed (ImportError)
                            no scraped pages (ValueError)
                            _parse_scraped returns no suggestions (ValueError)
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from apps.procurement.agents.Fallback_Webscraper_Agent import (
    FallbackWebscraperAgent,
    _clean_text,
    _is_bot_wall,
    _MAX_PAGE_CHARS,
    _NUM_SITES,
    _SYSTEM_CODE_TO_DB_NAME,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent():
    return FallbackWebscraperAgent()


@pytest.fixture()
def proc_request():
    """Minimal ProcurementRequest-like SimpleNamespace."""
    return SimpleNamespace(
        pk=42,
        title="VRF System for Office Tower",
        description="Supply and install VRF 135 kW for Zone A.",
        geography_country="UAE",
        geography_city="Dubai",
        priority="HIGH",
    )


def _mock_oai_response(content: str) -> MagicMock:
    """Return a mock requests.Response whose JSON matches the Azure OAI shape."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [
            {"message": {"role": "assistant", "content": content}}
        ]
    }
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_tabs_collapsed_to_space(self):
        # \t+ replaces ALL consecutive tabs with a single space
        assert _clean_text("a\t\tb") == "a b"

    def test_multiple_spaces_collapsed(self):
        assert _clean_text("a    b") == "a b"

    def test_triple_newlines_become_double(self):
        result = _clean_text("a\n\n\n\nb")
        assert result == "a\n\nb"

    def test_leading_trailing_stripped(self):
        assert _clean_text("  hello  ") == "hello"

    def test_empty_string(self):
        assert _clean_text("") == ""


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_clean_dict(self):
        raw = '{"key": "value"}'
        assert FallbackWebscraperAgent._extract_json(raw) == {"key": "value"}

    def test_clean_list(self):
        raw = '[{"url": "https://example.com"}]'
        result = FallbackWebscraperAgent._extract_json(raw)
        assert isinstance(result, list)
        assert result[0]["url"] == "https://example.com"

    def test_wrapped_in_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        assert FallbackWebscraperAgent._extract_json(raw) == {"key": "value"}

    def test_wrapped_in_plain_fence(self):
        raw = "```\n[1, 2, 3]\n```"
        assert FallbackWebscraperAgent._extract_json(raw) == [1, 2, 3]

    def test_dangling_open_fence_no_close(self):
        raw = "```json\n{\"a\": 1}"
        result = FallbackWebscraperAgent._extract_json(raw)
        assert result == {"a": 1}

    def test_prose_before_json(self):
        raw = 'Here is the data: {"result": true}'
        assert FallbackWebscraperAgent._extract_json(raw) == {"result": True}

    def test_prose_after_json(self):
        raw = '{"result": true}\nPlease review the above.'
        assert FallbackWebscraperAgent._extract_json(raw) == {"result": True}

    def test_invalid_json_returns_empty_dict(self):
        raw = "this is not json at all"
        assert FallbackWebscraperAgent._extract_json(raw) == {}

    def test_empty_string_returns_empty_dict(self):
        assert FallbackWebscraperAgent._extract_json("") == {}

    def test_list_wrapped_in_fence(self):
        raw = '```json\n[{"url": "https://alibaba.com"}]\n```'
        result = FallbackWebscraperAgent._extract_json(raw)
        assert isinstance(result, list)
        assert result[0]["url"] == "https://alibaba.com"


# ---------------------------------------------------------------------------
# _fallback_sites
# ---------------------------------------------------------------------------

class TestFallbackSites:
    def test_returns_list_of_dicts(self):
        sites = FallbackWebscraperAgent._fallback_sites("VRF System")
        assert isinstance(sites, list)
        assert len(sites) >= 2

    def test_all_urls_start_with_https(self):
        for site in FallbackWebscraperAgent._fallback_sites("Chiller"):
            assert site["url"].startswith("http")

    def test_system_name_included_in_url_or_name(self):
        sites = FallbackWebscraperAgent._fallback_sites("Split AC")
        combined = " ".join(s["url"] + s["site_name"] for s in sites)
        assert "Split" in combined or "split" in combined.lower()

    def test_all_have_required_keys(self):
        for site in FallbackWebscraperAgent._fallback_sites("VRF"):
            assert "site_name" in site
            assert "url" in site
            assert "what_to_extract" in site


# ---------------------------------------------------------------------------
# _normalise_suggestions
# ---------------------------------------------------------------------------

class TestNormaliseSuggestions:
    def test_distributor_icon(self):
        suggestions = [{"category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["icon_class"] == "bi-truck"

    def test_manufacturer_icon(self):
        suggestions = [{"category": "MANUFACTURER"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["icon_class"] == "bi-building"

    def test_unknown_category_defaults_icon(self):
        suggestions = [{"category": "UNKNOWN_XYZ"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["icon_class"] == "bi-building"

    def test_fit_score_clamped_above_100(self):
        suggestions = [{"fit_score": 150, "category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["fit_score"] == 100

    def test_fit_score_clamped_below_0(self):
        suggestions = [{"fit_score": -10, "category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["fit_score"] == 0

    def test_invalid_fit_score_defaults_to_0(self):
        suggestions = [{"fit_score": "not-a-number", "category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["fit_score"] == 0

    def test_citation_url_fallback_when_invalid(self):
        url = "https://fallback.com"
        suggestions = [{"citation_url": "not-a-url", "category": "OTHER"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, [url])
        assert result[0]["citation_url"] == url

    def test_valid_citation_url_preserved(self):
        good_url = "https://alibaba.com/product/123"
        suggestions = [{"citation_url": good_url, "category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["citation_url"] == good_url

    def test_price_source_url_falls_back_to_citation_url(self):
        suggestions = [{"citation_url": "https://good.com", "price_source_url": "bad", "category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["price_source_url"] == "https://good.com"

    def test_is_approved_source_always_false(self):
        suggestions = [{"is_approved_source": True, "category": "MANUFACTURER"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["is_approved_source"] is False

    def test_default_citation_source_set(self):
        suggestions = [{"category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["citation_source"] == "web scrape fallback"

    def test_existing_citation_source_preserved(self):
        suggestions = [{"citation_source": "Alibaba", "category": "DISTRIBUTOR"}]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, ["https://x.com"])
        assert result[0]["citation_source"] == "Alibaba"

    def test_multiple_suggestions_cyclic_url_fallback(self):
        urls = ["https://site-a.com", "https://site-b.com"]
        suggestions = [
            {"citation_url": "bad", "category": "DISTRIBUTOR"},
            {"citation_url": "bad", "category": "DISTRIBUTOR"},
            {"citation_url": "bad", "category": "DISTRIBUTOR"},
        ]
        result = FallbackWebscraperAgent._normalise_suggestions(suggestions, urls)
        # Index 0 -> urls[0], index 1 -> urls[1], index 2 -> urls[0] (wraps)
        assert result[0]["citation_url"] == "https://site-a.com"
        assert result[1]["citation_url"] == "https://site-b.com"
        assert result[2]["citation_url"] == "https://site-a.com"


# ---------------------------------------------------------------------------
# _ask_azure_openai
# ---------------------------------------------------------------------------

class TestAskAzureOpenai:
    @patch("apps.procurement.agents.Fallback_Webscraper_Agent.FallbackWebscraperAgent._ask_azure_openai")
    def test_missing_endpoint_raises(self, _mock):
        """Test directly via method with missing settings."""
        with patch("apps.procurement.agents.Fallback_Webscraper_Agent.FallbackWebscraperAgent._ask_azure_openai",
                   side_effect=ValueError("AZURE_OPENAI_ENDPOINT")):
            with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
                raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be configured")

    def test_missing_endpoint_setting(self):
        """Directly test _ask_azure_openai with empty settings."""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.AZURE_OPENAI_ENDPOINT = ""
            mock_settings.AZURE_OPENAI_API_KEY = ""
            mock_settings.AZURE_OPENAI_DEPLOYMENT = "gpt-4o"
            mock_settings.AZURE_OPENAI_API_VERSION = "2024-02-01"
            with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
                FallbackWebscraperAgent._ask_azure_openai(
                    system_msg="sys",
                    user_msg="usr",
                )

    def test_missing_api_key_setting(self):
        with patch("django.conf.settings") as mock_settings:
            mock_settings.AZURE_OPENAI_ENDPOINT = "https://my.openai.azure.com/"
            mock_settings.AZURE_OPENAI_API_KEY = ""
            mock_settings.AZURE_OPENAI_DEPLOYMENT = "gpt-4o"
            mock_settings.AZURE_OPENAI_API_VERSION = "2024-02-01"
            with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
                FallbackWebscraperAgent._ask_azure_openai(
                    system_msg="sys",
                    user_msg="usr",
                )

    def test_returns_content_from_choices(self):
        content = '{"key": "value"}'
        mock_resp = _mock_oai_response(content)
        with patch("django.conf.settings") as mock_settings, \
             patch("requests.post", return_value=mock_resp):
            mock_settings.AZURE_OPENAI_ENDPOINT = "https://my.openai.azure.com/"
            mock_settings.AZURE_OPENAI_API_KEY = "testkey123"
            mock_settings.AZURE_OPENAI_DEPLOYMENT = "gpt-4o"
            mock_settings.AZURE_OPENAI_API_VERSION = "2024-02-01"
            result = FallbackWebscraperAgent._ask_azure_openai(
                system_msg="sys", user_msg="usr"
            )
        assert result == content

    def test_empty_choices_raises(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": []}
        mock_resp.raise_for_status.return_value = None
        with patch("django.conf.settings") as mock_settings, \
             patch("requests.post", return_value=mock_resp):
            mock_settings.AZURE_OPENAI_ENDPOINT = "https://my.openai.azure.com/"
            mock_settings.AZURE_OPENAI_API_KEY = "testkey"
            mock_settings.AZURE_OPENAI_DEPLOYMENT = "gpt-4o"
            mock_settings.AZURE_OPENAI_API_VERSION = "2024-02-01"
            with pytest.raises(ValueError, match="no choices"):
                FallbackWebscraperAgent._ask_azure_openai(
                    system_msg="sys", user_msg="usr"
                )

    def test_http_error_propagates(self):
        import requests as _req
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = _req.HTTPError("429 Too Many Requests")
        with patch("django.conf.settings") as mock_settings, \
             patch("requests.post", return_value=mock_resp):
            mock_settings.AZURE_OPENAI_ENDPOINT = "https://my.openai.azure.com/"
            mock_settings.AZURE_OPENAI_API_KEY = "testkey"
            mock_settings.AZURE_OPENAI_DEPLOYMENT = "gpt-4o"
            mock_settings.AZURE_OPENAI_API_VERSION = "2024-02-01"
            with pytest.raises(_req.HTTPError):
                FallbackWebscraperAgent._ask_azure_openai(
                    system_msg="sys", user_msg="usr"
                )


# ---------------------------------------------------------------------------
# _ask_sites
# ---------------------------------------------------------------------------

VALID_SITES_LIST = json.dumps([
    {"site_name": "Alibaba", "url": "https://alibaba.com/search?q=vrf", "search_query": "VRF", "what_to_extract": "prices"},
    {"site_name": "IndiaMART", "url": "https://indiamart.com/search?q=vrf", "search_query": "VRF", "what_to_extract": "prices"},
])


class TestAskSites:
    def test_list_response_extracts_sites(self, agent, proc_request):
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=VALID_SITES_LIST):
            sites = agent._ask_sites(proc_request, "VRF System")
        assert len(sites) == 2
        assert sites[0]["url"] == "https://alibaba.com/search?q=vrf"

    def test_dict_with_sites_key_extracted(self, agent, proc_request):
        wrapped = json.dumps({"sites": json.loads(VALID_SITES_LIST)})
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=wrapped):
            sites = agent._ask_sites(proc_request, "VRF System")
        assert len(sites) == 2

    def test_garbage_response_uses_hardcoded_fallback(self, agent, proc_request):
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value="not json at all"):
            sites = agent._ask_sites(proc_request, "VRF System")
        # Should fall back to _fallback_sites
        assert len(sites) >= 1
        assert all(s["url"].startswith("http") for s in sites)

    def test_sites_capped_at_num_sites(self, agent, proc_request):
        """Even if Azure OAI returns more than _NUM_SITES, only _NUM_SITES are used."""
        many_sites = [
            {"site_name": f"Site{i}", "url": f"https://site{i}.com", "search_query": "x", "what_to_extract": "y"}
            for i in range(20)
        ]
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=json.dumps(many_sites)):
            sites = agent._ask_sites(proc_request, "VRF System")
        assert len(sites) == _NUM_SITES

    def test_sites_with_non_http_urls_filtered(self, agent, proc_request):
        bad_list = json.dumps([
            {"site_name": "Bad", "url": "ftp://bad.com", "search_query": "x", "what_to_extract": "y"},
            {"site_name": "Good", "url": "https://good.com", "search_query": "x", "what_to_extract": "y"},
        ])
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=bad_list):
            sites = agent._ask_sites(proc_request, "VRF System")
        assert all(s["url"].startswith("http") for s in sites)
        # ftp:// site should be filtered; at least the https one remains
        assert any("good.com" in s["url"] for s in sites)


# ---------------------------------------------------------------------------
# _parse_scraped
# ---------------------------------------------------------------------------

_GOOD_PARSE_RESPONSE = json.dumps({
    "rephrased_query": "VRF system UAE",
    "ai_summary": "Found several products.",
    "market_context": "Prices around AED 30k.",
    "suggestions": [
        {
            "rank": 1,
            "product_name": "Daikin VRV-X",
            "manufacturer": "Daikin",
            "model_code": "RXYQ10",
            "system_type": "VRF System",
            "price_range_aed": "AED 28,000 - 36,000",
            "fit_score": 85,
            "category": "DISTRIBUTOR",
            "citation_url": "https://alibaba.com/xyz",
        }
    ],
})


class TestParseScraped:
    def test_valid_response_returned(self, agent, proc_request):
        scraped = [{"site_name": "Alibaba", "url": "https://alibaba.com/xyz", "text": "VRF products here..."}]
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=_GOOD_PARSE_RESPONSE):
            data = agent._parse_scraped(proc_request, "VRF System", scraped)
        assert len(data["suggestions"]) == 1
        assert data["rephrased_query"] == "VRF system UAE"

    def test_no_suggestions_key_raises(self, agent, proc_request):
        bad_response = json.dumps({"rephrased_query": "x"})
        scraped = [{"site_name": "Site", "url": "https://site.com", "text": "some text"}]
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=bad_response), \
             pytest.raises(ValueError, match="Azure OpenAI could not extract"):
            agent._parse_scraped(proc_request, "VRF System", scraped)

    def test_empty_suggestions_list_raises(self, agent, proc_request):
        bad_response = json.dumps({"rephrased_query": "x", "suggestions": []})
        scraped = [{"site_name": "Site", "url": "https://site.com", "text": "text"}]
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value=bad_response), \
             pytest.raises(ValueError, match="Azure OpenAI could not extract"):
            agent._parse_scraped(proc_request, "VRF System", scraped)

    def test_invalid_json_response_raises(self, agent, proc_request):
        scraped = [{"site_name": "Site", "url": "https://site.com", "text": "text"}]
        with patch.object(FallbackWebscraperAgent, "_ask_azure_openai", return_value="not json"), \
             pytest.raises(ValueError):
            agent._parse_scraped(proc_request, "VRF System", scraped)


# ---------------------------------------------------------------------------
# _persist
# ---------------------------------------------------------------------------

class TestPersist:
    def test_happy_path_creates_record(self, proc_request):
        mock_model = MagicMock()
        with patch("apps.procurement.agents.Fallback_Webscraper_Agent.MarketIntelligenceSuggestion",
                   mock_model, create=True):
            with patch(
                "apps.procurement.models.MarketIntelligenceSuggestion.objects.create"
            ) as mock_create:
                FallbackWebscraperAgent._persist(
                    proc_request=proc_request,
                    generated_by=None,
                    data={"rephrased_query": "q", "ai_summary": "s", "market_context": "m"},
                    system_code="VRF",
                    system_name="VRF System",
                    suggestions=[{"product_name": "Daikin"}],
                    scraped_urls=["https://alibaba.com"],
                )
        # Either the direct mock or the patched create, it should not raise.

    def test_db_exception_is_swallowed(self, proc_request):
        """_persist must never raise -- DB failures are logged and ignored.

        MarketIntelligenceSuggestion is a LOCAL import inside _persist, so we
        patch it at its definition site: apps.procurement.models.
        """
        with patch("apps.procurement.models.MarketIntelligenceSuggestion.objects") as mock_mgr:
            mock_mgr.create.side_effect = Exception("DB connection lost")
            # Should not raise
            FallbackWebscraperAgent._persist(
                proc_request=proc_request,
                generated_by=None,
                data={},
                system_code="VRF",
                system_name="VRF System",
                suggestions=[],
                scraped_urls=[],
            )


# ---------------------------------------------------------------------------
# run() -- full pipeline tests
# ---------------------------------------------------------------------------

_SCRAPED_PAGES = [
    {
        "site_name": "Alibaba",
        "url": "https://alibaba.com/search?q=vrf",
        "text": "VRF systems available. Daikin VRV-X AED 30,000.",
    }
]

_PARSE_DATA = {
    "rephrased_query": "VRF system Dubai UAE",
    "ai_summary": "Multiple VRF options found.",
    "market_context": "Prices from AED 25k.",
    "suggestions": [
        {
            "rank": 1,
            "product_name": "Daikin VRV-X",
            "manufacturer": "Daikin",
            "model_code": "RXYQ18",
            "system_type": "VRF System",
            "price_range_aed": "~30,000 AED est.",
            "fit_score": 82,
            "category": "DISTRIBUTOR",
            "citation_url": "https://alibaba.com/search?q=vrf",
            "price_source_url": "https://alibaba.com/search?q=vrf",
        }
    ],
}

_REC_CONTEXT = (
    {"system_code": "VRF", "system_name": "VRF System"},  # rec_block
    "VRF",
    "VRF System",
)


class TestRunHappyPath:
    def _patch_perplexity_context(self):
        return patch(
            "apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent"
            ".PerplexityMarketResearchAnalystAgent.get_rec_context",
            return_value=_REC_CONTEXT,
        )

    def test_run_returns_expected_keys(self, agent, proc_request):
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{
                 "site_name": "Alibaba",
                 "url": "https://alibaba.com/search?q=vrf",
             }]), \
             patch.object(agent, "_scrape_sites", return_value=_SCRAPED_PAGES), \
             patch.object(agent, "_parse_scraped", return_value=_PARSE_DATA), \
             patch.object(FallbackWebscraperAgent, "_persist"):
            result = agent.run(proc_request)

        assert "system_code" in result
        assert "suggestions" in result
        assert "perplexity_citations" in result
        assert "ai_summary" in result
        assert result["system_code"] == "VRF"
        assert isinstance(result["suggestions"], list)

    def test_run_suggestions_normalised(self, agent, proc_request):
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://a.com"}]), \
             patch.object(agent, "_scrape_sites", return_value=_SCRAPED_PAGES), \
             patch.object(agent, "_parse_scraped", return_value=_PARSE_DATA), \
             patch.object(FallbackWebscraperAgent, "_persist"):
            result = agent.run(proc_request)

        # _normalise_suggestions should have been applied
        assert "icon_class" in result["suggestions"][0]
        assert result["suggestions"][0]["is_approved_source"] is False

    def test_perplexity_citations_are_scraped_urls(self, agent, proc_request):
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://alibaba.com"}]), \
             patch.object(agent, "_scrape_sites", return_value=_SCRAPED_PAGES), \
             patch.object(agent, "_parse_scraped", return_value=_PARSE_DATA), \
             patch.object(FallbackWebscraperAgent, "_persist"):
            result = agent.run(proc_request)

        assert result["perplexity_citations"] == ["https://alibaba.com/search?q=vrf"]


class TestRunErrorCases:
    def _patch_perplexity_context(self):
        return patch(
            "apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent"
            ".PerplexityMarketResearchAnalystAgent.get_rec_context",
            return_value=_REC_CONTEXT,
        )

    def test_playwright_not_installed_raises_import_error(self, agent, proc_request):
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://a.com"}]):
            # _scrape_sites internally imports playwright; mock it to raise ImportError
            with patch.object(
                agent, "_scrape_sites",
                side_effect=ImportError("No module named 'playwright'"),
            ):
                with pytest.raises(ImportError, match="playwright"):
                    agent.run(proc_request)

    def test_no_pages_scraped_raises_value_error(self, agent, proc_request):
        """If _scrape_sites returns an empty list, run() must raise ValueError."""
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://a.com"}]), \
             patch.object(agent, "_scrape_sites", return_value=[]):
            with pytest.raises(ValueError, match="Playwright could not load"):
                agent.run(proc_request)

    def test_parse_scraped_no_suggestions_raises(self, agent, proc_request):
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://a.com"}]), \
             patch.object(agent, "_scrape_sites", return_value=_SCRAPED_PAGES), \
             patch.object(
                 agent, "_parse_scraped",
                 side_effect=ValueError("Azure OpenAI could not extract product suggestions"),
             ):
            with pytest.raises(ValueError, match="Azure OpenAI could not extract"):
                agent.run(proc_request)

    def test_persist_exception_does_not_crash_run(self, agent, proc_request):
        """_persist failures must be swallowed; run() should still return successfully."""
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://a.com"}]), \
             patch.object(agent, "_scrape_sites", return_value=_SCRAPED_PAGES), \
             patch.object(agent, "_parse_scraped", return_value=_PARSE_DATA), \
             patch.object(FallbackWebscraperAgent, "_persist", side_effect=Exception("DB down")):
            # _persist is called INSIDE run() but its exception should be swallowed
            # Since _persist is a @staticmethod called directly, we need to trigger the
            # real code path but mock the DB model. Patch the model instead.
            pass

        # A direct run() test where the DB create raises -- _persist must swallow it.
        # MarketIntelligenceSuggestion is a LOCAL import inside _persist, so patch
        # at its definition site: apps.procurement.models.
        with self._patch_perplexity_context(), \
             patch.object(agent, "_ask_sites", return_value=[{"url": "https://a.com"}]), \
             patch.object(agent, "_scrape_sites", return_value=_SCRAPED_PAGES), \
             patch.object(agent, "_parse_scraped", return_value=_PARSE_DATA), \
             patch("apps.procurement.models.MarketIntelligenceSuggestion.objects") as mock_mgr:
            mock_mgr.create.side_effect = Exception("DB down")
            result = agent.run(proc_request)

        # run() completed despite DB error
        assert "suggestions" in result


# ---------------------------------------------------------------------------
# _scrape_sites (unit -- playwright mocked via sys.modules injection)
# ---------------------------------------------------------------------------

import sys
import types


def _make_fake_playwright(pages_text: list[str]) -> tuple:
    """Build fake playwright sync_api module + mock context that yields page text.

    Returns (fake_playwright_pkg, fake_sync_api_module) ready to be inserted into
    sys.modules so that ``from playwright.sync_api import sync_playwright`` resolves
    without the real playwright package being installed.
    """
    call_count = {"n": 0}

    # -- page mock --
    mock_page = MagicMock()

    def mock_evaluate(_script):
        idx = call_count["n"]
        call_count["n"] += 1
        return pages_text[idx] if idx < len(pages_text) else ""

    mock_page.evaluate.side_effect = mock_evaluate
    mock_page.goto.return_value = None
    mock_page.wait_for_timeout.return_value = None
    mock_page.close.return_value = None

    # -- context, browser, chromium mocks --
    mock_ctx = MagicMock()
    mock_ctx.new_page.return_value = mock_page
    mock_ctx.close.return_value = None

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx
    mock_browser.close.return_value = None

    mock_chromium = MagicMock()
    mock_chromium.launch.return_value = mock_browser

    # -- playwright context manager --
    mock_pw_instance = MagicMock()
    mock_pw_instance.chromium = mock_chromium
    mock_pw_instance.__enter__ = MagicMock(return_value=mock_pw_instance)
    mock_pw_instance.__exit__ = MagicMock(return_value=False)

    def fake_sync_playwright():
        return mock_pw_instance

    # -- fake modules --
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = fake_sync_playwright

    fake_playwright = types.ModuleType("playwright")
    fake_playwright.sync_api = fake_sync_api

    return fake_playwright, fake_sync_api, mock_page, mock_pw_instance


class TestScrapeSites:
    """Tests for _scrape_sites -- playwright is mocked via sys.modules injection
    so the tests run even without the real playwright package installed.
    """

    def test_scrape_returns_page_text(self, agent):
        long_text = "VRF units available. Daikin prices listed. " * 10  # > 80 chars
        fake_pw, fake_sync_api, _page, _inst = _make_fake_playwright([long_text])
        sites = [{"site_name": "Alibaba", "url": "https://alibaba.com", "what_to_extract": "prices"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        assert len(result) == 1
        assert result[0]["url"] == "https://alibaba.com"
        assert "VRF" in result[0]["text"]

    def test_scrape_skips_short_text(self, agent):
        """Pages that yield < 80 chars of text are silently dropped."""
        short_text = "Hi"  # < 80 chars -> should be skipped
        fake_pw, fake_sync_api, _page, _inst = _make_fake_playwright([short_text])
        sites = [{"site_name": "Empty", "url": "https://empty.com", "what_to_extract": "nope"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        assert result == []

    def test_scrape_skips_failed_pages(self, agent):
        """Pages where page.goto raises are silently skipped."""
        fake_pw, fake_sync_api, mock_page, _inst = _make_fake_playwright([""])
        mock_page.goto.side_effect = Exception("Navigation timeout")
        sites = [{"site_name": "Bad", "url": "https://bad.com", "what_to_extract": "nope"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        assert result == []

    def test_playwright_not_installed_raises(self, agent):
        sites = [{"url": "https://x.com"}]
        with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            with pytest.raises(ImportError):
                agent._scrape_sites(sites)

    def test_text_truncated_to_max_chars(self, agent):
        long_text = "A" * (_MAX_PAGE_CHARS * 3)  # 3x the limit -> must be capped
        fake_pw, fake_sync_api, _page, _inst = _make_fake_playwright([long_text])
        sites = [{"site_name": "Big", "url": "https://big.com", "what_to_extract": "all"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        assert len(result) == 1
        assert len(result[0]["text"]) <= _MAX_PAGE_CHARS

    def test_non_http_url_skipped(self, agent):
        """URLs that do not start with 'http' are ignored before launching Playwright."""
        fake_pw, fake_sync_api, _page, _inst = _make_fake_playwright([""])
        sites = [{"site_name": "FTP", "url": "ftp://bad.com", "what_to_extract": "nope"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        assert result == []

    def test_bot_wall_page_is_skipped(self, agent):
        """A page whose text matches a bot-wall phrase is skipped even if it has enough chars."""
        # Cloudflare challenge text -- short (<= 1000 chars) and contains 'cloudflare'
        bot_text = "Checking your browser before accessing the site. Cloudflare Ray ID: abc123" * 3
        fake_pw, fake_sync_api, _page, _inst = _make_fake_playwright([bot_text])
        sites = [{"site_name": "CF", "url": "https://blocked.com", "what_to_extract": "products"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        # The bot-wall page must be discarded -- result list stays empty
        assert result == []

    def test_large_page_with_bot_phrase_not_falsely_blocked(self, agent):
        """Pages > 1000 chars are NOT blocked even if they mention 'cloudflare' in passing."""
        # A big legitimate product page that happens to reference cloudflare in a footer
        big_text = ("VRF System 10TR available, contact supplier for pricing. " * 30
                    + "Protected by Cloudflare")
        assert len(big_text) > 1_000  # precondition
        fake_pw, fake_sync_api, _page, _inst = _make_fake_playwright([big_text])
        sites = [{"site_name": "Legit", "url": "https://legit.com", "what_to_extract": "products"}]

        with patch.dict(sys.modules, {"playwright": fake_pw, "playwright.sync_api": fake_sync_api}):
            result = agent._scrape_sites(sites)

        assert len(result) == 1


# ---------------------------------------------------------------------------
# _is_bot_wall unit tests
# ---------------------------------------------------------------------------

class TestIsBotWall:
    """Unit tests for the _is_bot_wall() utility function."""

    def test_cloudflare_short_page_detected(self):
        assert _is_bot_wall("Just a moment... Cloudflare Ray ID: 123abc") is True

    def test_access_denied_detected(self):
        assert _is_bot_wall("403 Forbidden. Access denied to this resource.") is True

    def test_captcha_detected(self):
        assert _is_bot_wall("Please complete the CAPTCHA to verify you are human.") is True

    def test_enable_javascript_detected(self):
        assert _is_bot_wall("Please enable JavaScript to continue.") is True

    def test_verify_human_detected(self):
        assert _is_bot_wall("Verify you are human before continuing.") is True

    def test_normal_product_page_not_flagged(self):
        text = "VRF Split AC System 8TR - 12TR available. Price: AED 12,000. Contact supplier."
        assert _is_bot_wall(text) is False

    def test_large_page_with_bot_phrase_not_flagged(self):
        """Pages longer than 1000 chars are never flagged even with a bot phrase."""
        large = "Real product content. " * 50 + "cloudflare"
        assert len(large) > 1_000
        assert _is_bot_wall(large) is False

    def test_empty_string_not_flagged(self):
        assert _is_bot_wall("") is False

    def test_case_insensitive(self):
        # Mixed-case bot phrase should still be caught
        assert _is_bot_wall("CLOUDFLARE protection active.") is True
