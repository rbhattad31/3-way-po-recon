"""Tests for InvoicePromptComposer — modular prompt composition and fallback behaviour."""
import pytest
from unittest.mock import patch


class TestInvoicePromptComposer:

    def test_compose_returns_prompt_composition(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer, PromptComposition
        result = InvoicePromptComposer.compose()
        assert isinstance(result, PromptComposition)
        assert isinstance(result.final_prompt, str)
        assert isinstance(result.components, dict)
        assert isinstance(result.prompt_hash, str)

    def test_base_prompt_included_by_default(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose()
        # Should include something from the base prompt
        assert len(result.final_prompt) > 100

    def test_travel_category_adds_overlay(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose(invoice_category="travel")
        assert "TRAVEL INVOICE" in result.final_prompt
        assert "CART Ref" in result.final_prompt

    def test_goods_category_adds_overlay(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose(invoice_category="goods")
        assert "GOODS INVOICE" in result.final_prompt
        assert "HSN" in result.final_prompt

    def test_service_category_adds_overlay(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose(invoice_category="service")
        assert "SERVICE INVOICE" in result.final_prompt

    def test_india_gst_country_overlay(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose(country_code="IN", regime_code="GST")
        assert "INDIA GST" in result.final_prompt
        assert "GSTIN" in result.final_prompt

    def test_unknown_category_skips_overlay(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result_no_cat = InvoicePromptComposer.compose()
        result_unknown = InvoicePromptComposer.compose(invoice_category="unknown_type")
        # unknown category should produce same prompt as no category
        assert result_no_cat.final_prompt == result_unknown.final_prompt

    def test_unknown_country_skips_overlay(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result_no_country = InvoicePromptComposer.compose()
        result_unknown_country = InvoicePromptComposer.compose(country_code="ZZ", regime_code="NONE")
        # no country overlay registered for ZZ → same base prompt
        assert result_no_country.final_prompt == result_unknown_country.final_prompt

    def test_prompt_hash_is_deterministic(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        r1 = InvoicePromptComposer.compose(invoice_category="travel")
        r2 = InvoicePromptComposer.compose(invoice_category="travel")
        assert r1.prompt_hash == r2.prompt_hash

    def test_different_categories_produce_different_hashes(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        r_travel = InvoicePromptComposer.compose(invoice_category="travel")
        r_goods = InvoicePromptComposer.compose(invoice_category="goods")
        assert r_travel.prompt_hash != r_goods.prompt_hash

    def test_prompt_hash_is_16_chars(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose()
        assert len(result.prompt_hash) == 16

    def test_components_dict_records_keys_used(self):
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        result = InvoicePromptComposer.compose(invoice_category="travel", country_code="IN", regime_code="GST")
        # Should have at least base key + category key + country key
        assert len(result.components) >= 3
        assert any("base" in k or "system" in k for k in result.components)
        assert any("travel" in k for k in result.components)
        assert any("india" in k or "gst" in k for k in result.components)

    def test_fallback_when_base_prompt_missing(self):
        """When both invoice_base and invoice_system are absent, returns empty composition."""
        from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
        from apps.core.prompt_registry import PromptRegistry

        with patch.object(PromptRegistry, "get_or_default", return_value=""):
            result = InvoicePromptComposer.compose(invoice_category="travel")
        assert result.final_prompt == ""
        assert result.prompt_hash == ""
