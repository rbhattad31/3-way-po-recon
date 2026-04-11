"""Unit tests for line_match_helpers -- pure functions, no DB needed."""
from __future__ import annotations

import pytest
from decimal import Decimal

from apps.reconciliation.services.line_match_helpers import (
    normalize_line_text,
    extract_meaningful_tokens,
    token_similarity,
    fuzzy_similarity,
    quantity_proximity,
    price_proximity,
    amount_proximity,
    uom_compatibility,
    category_compatibility,
    service_stock_compatibility,
)


# ===================================================================
# normalize_line_text
# ===================================================================

class TestNormalizeLineText:
    def test_lowercase(self):
        assert normalize_line_text("Chicken BREAST") == "chicken breast"

    def test_strips_punctuation(self):
        result = normalize_line_text("Item #123 - Heavy/Duty (Steel)")
        assert "#" not in result
        assert "(" not in result
        assert ")" not in result

    def test_collapses_whitespace(self):
        assert normalize_line_text("  a   b    c  ") == "a b c"

    def test_replaces_separators_with_space(self):
        result = normalize_line_text("oil/filter-change:monthly")
        assert "oil" in result
        assert "filter" in result
        assert "change" in result
        assert "monthly" in result

    def test_none_returns_empty(self):
        assert normalize_line_text(None) == ""

    def test_empty_string_returns_empty(self):
        assert normalize_line_text("") == ""


# ===================================================================
# extract_meaningful_tokens
# ===================================================================

class TestExtractMeaningfulTokens:
    def test_removes_stopwords(self):
        tokens = extract_meaningful_tokens("Supply of goods and materials for the project")
        assert "supply" not in tokens  # 'supply' is in stopwords as 'supplies'
        assert "goods" not in tokens
        assert "materials" not in tokens
        assert "the" not in tokens
        assert "and" not in tokens
        assert "for" not in tokens
        assert "of" not in tokens
        assert "project" in tokens

    def test_meaningful_tokens_kept(self):
        tokens = extract_meaningful_tokens("Frozen Boneless Chicken Breast")
        assert "frozen" in tokens
        assert "boneless" in tokens
        assert "chicken" in tokens
        assert "breast" in tokens

    def test_none_returns_empty(self):
        assert extract_meaningful_tokens(None) == set()

    def test_empty_returns_empty(self):
        assert extract_meaningful_tokens("") == set()


# ===================================================================
# token_similarity
# ===================================================================

class TestTokenSimilarity:
    def test_identical_strings(self):
        sim = token_similarity("Chicken Breast", "Chicken Breast")
        assert sim == 1.0

    def test_no_overlap(self):
        sim = token_similarity("Apple Orange", "Motor Engine")
        assert sim == 0.0

    def test_partial_overlap(self):
        sim = token_similarity("Red Apple Juice", "Green Apple Cider")
        assert 0.0 < sim < 1.0
        # Shared: apple; Union: red, green, apple, juice, cider = 5
        assert sim == pytest.approx(1 / 5, abs=0.15)

    def test_stopwords_ignored(self):
        # "the", "of", "supply", "goods" are all stopwords.
        # After removal: "items" vs empty set -> 0.0
        # Use words that survive stopword removal on both sides.
        sim = token_similarity("frozen chicken breast", "frozen beef breast")
        # tokens_a = {frozen, chicken, breast}, tokens_b = {frozen, beef, breast}
        # Jaccard = 2/4 = 0.5
        assert sim == pytest.approx(0.5, abs=0.01)

    def test_none_input(self):
        assert token_similarity(None, "test") == 0.0
        assert token_similarity("test", None) == 0.0


# ===================================================================
# fuzzy_similarity
# ===================================================================

class TestFuzzySimilarity:
    def test_identical(self):
        score = fuzzy_similarity("Chicken Breast 1KG", "Chicken Breast 1KG")
        assert score >= 99.0

    def test_reordered_tokens(self):
        score = fuzzy_similarity("Fresh Chicken Breast", "Chicken Breast Fresh")
        # token_sort_ratio should handle reordering
        assert score >= 90.0

    def test_completely_different(self):
        score = fuzzy_similarity("AAAA BBBB", "XXXX YYYY")
        assert score < 30.0

    def test_none_returns_zero(self):
        assert fuzzy_similarity(None, "test") == 0.0
        assert fuzzy_similarity("test", None) == 0.0


# ===================================================================
# quantity_proximity
# ===================================================================

class TestQuantityProximity:
    def test_exact_match(self):
        var, score = quantity_proximity(Decimal("10"), Decimal("10"))
        assert var == 0.0
        assert score == 0.10

    def test_within_2_percent(self):
        # 10.1 vs 10.0 = 1% diff
        var, score = quantity_proximity(Decimal("10.1"), Decimal("10.0"))
        assert var is not None
        assert var <= 2.0
        assert score == 0.08

    def test_within_5_percent(self):
        # 10.4 vs 10.0 = 4%
        var, score = quantity_proximity(Decimal("10.4"), Decimal("10.0"))
        assert var is not None
        assert 2.0 < var <= 5.0
        assert score == 0.05

    def test_within_10_percent(self):
        # 10.8 vs 10.0 = 8%
        var, score = quantity_proximity(Decimal("10.8"), Decimal("10.0"))
        assert var is not None
        assert 5.0 < var <= 10.0
        assert score == 0.02

    def test_beyond_10_percent(self):
        var, score = quantity_proximity(Decimal("20"), Decimal("10"))
        assert var is not None
        assert var > 10.0
        assert score == 0.0

    def test_none_input(self):
        var, score = quantity_proximity(None, Decimal("10"))
        assert var is None
        assert score == 0.0

    def test_both_zero(self):
        var, score = quantity_proximity(Decimal("0"), Decimal("0"))
        assert var == 0.0
        assert score == 0.10


# ===================================================================
# price_proximity
# ===================================================================

class TestPriceProximity:
    def test_exact_match(self):
        var, score = price_proximity(Decimal("100"), Decimal("100"))
        assert score == 0.07

    def test_within_1_percent(self):
        var, score = price_proximity(Decimal("100.5"), Decimal("100"))
        assert score == 0.07

    def test_within_3_percent(self):
        var, score = price_proximity(Decimal("102"), Decimal("100"))
        assert score == 0.05

    def test_within_5_percent(self):
        var, score = price_proximity(Decimal("104"), Decimal("100"))
        assert score == 0.03

    def test_beyond_5_percent(self):
        var, score = price_proximity(Decimal("200"), Decimal("100"))
        assert score == 0.0


# ===================================================================
# amount_proximity
# ===================================================================

class TestAmountProximity:
    def test_exact_match(self):
        var, score = amount_proximity(Decimal("1000"), Decimal("1000"))
        assert score == 0.03

    def test_within_1_percent(self):
        var, score = amount_proximity(Decimal("1005"), Decimal("1000"))
        assert score == 0.03

    def test_within_3_percent(self):
        var, score = amount_proximity(Decimal("1025"), Decimal("1000"))
        assert score == 0.02

    def test_beyond_5_percent(self):
        var, score = amount_proximity(Decimal("1100"), Decimal("1000"))
        assert score == 0.0


# ===================================================================
# uom_compatibility
# ===================================================================

class TestUOMCompatibility:
    def test_exact_match(self):
        reason, score = uom_compatibility("kg", "kg")
        assert reason == "exact"
        assert score == 0.02

    def test_equivalent(self):
        reason, score = uom_compatibility("KG", "Kilograms")
        assert reason == "equivalent"
        assert score == 0.015

    def test_ea_pcs_equivalent(self):
        reason, score = uom_compatibility("EA", "PCS")
        assert reason == "equivalent"
        assert score == 0.015

    def test_one_side_missing(self):
        reason, score = uom_compatibility("", "kg")
        assert reason == "one_side_missing"
        assert score == 0.005

    def test_both_missing(self):
        reason, score = uom_compatibility("", "")
        assert reason == "one_side_missing"
        assert score == 0.005

    def test_incompatible(self):
        reason, score = uom_compatibility("kg", "litre")
        assert reason == "incompatible"
        assert score == 0.0

    def test_none_input(self):
        reason, score = uom_compatibility(None, "kg")
        assert reason == "one_side_missing"
        assert score == 0.005


# ===================================================================
# category_compatibility
# ===================================================================

class TestCategoryCompatibility:
    def test_same_category(self):
        reason, score = category_compatibility("Electrical", "Electrical")
        assert reason == "same"
        assert score == 0.01

    def test_case_insensitive(self):
        reason, score = category_compatibility("ELECTRICAL", "electrical")
        assert reason == "same"
        assert score == 0.01

    def test_one_missing(self):
        reason, score = category_compatibility("", "Electrical")
        assert reason == "one_side_missing"
        assert score == 0.003

    def test_mismatch(self):
        reason, score = category_compatibility("Electrical", "Mechanical")
        assert reason == "mismatch"
        assert score == 0.0

    def test_none_input(self):
        reason, score = category_compatibility(None, "Electrical")
        assert reason == "one_side_missing"
        assert score == 0.003


# ===================================================================
# service_stock_compatibility
# ===================================================================

class TestServiceStockCompatibility:
    def test_both_service(self):
        reason, score, contradiction = service_stock_compatibility(
            True, False, True, False,
        )
        assert reason == "compatible"
        assert score == 0.01
        assert contradiction is False

    def test_both_stock(self):
        reason, score, contradiction = service_stock_compatibility(
            False, True, False, True,
        )
        assert reason == "compatible"
        assert score == 0.01
        assert contradiction is False

    def test_service_vs_stock_contradiction(self):
        reason, score, contradiction = service_stock_compatibility(
            True, False, False, True,
        )
        assert reason == "contradiction"
        assert score == 0.0
        assert contradiction is True

    def test_one_side_unknown(self):
        reason, score, contradiction = service_stock_compatibility(
            None, None, True, False,
        )
        assert reason == "one_side_unknown"
        assert score == 0.003
        assert contradiction is False

    def test_both_unknown(self):
        reason, score, contradiction = service_stock_compatibility(
            None, None, None, None,
        )
        assert reason == "one_side_unknown"
        assert score == 0.003
        assert contradiction is False
