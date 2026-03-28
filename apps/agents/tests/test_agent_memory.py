"""
Tests for AgentMemory — pure unit tests (no DB needed).

Key behaviours (from source):
  - record_agent_output stores reasoning summary (capped at 500 chars)
  - Only promotes current_recommendation if new confidence is HIGHER
  - resolved_po_number set from evidence["found_po"] when non-empty
  - facts dict holds arbitrary pre-seeded data
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from apps.agents.services.agent_memory import AgentMemory


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_output(reasoning="Test reasoning", recommendation_type=None,
                confidence=0.0, evidence=None):
    out = MagicMock()
    out.reasoning = reasoning
    out.recommendation_type = recommendation_type
    out.confidence = confidence
    out.evidence = evidence or {}
    return out


# ─── Initial state ────────────────────────────────────────────────────────────

class TestInitialState:
    def test_default_values(self):
        mem = AgentMemory()
        assert mem.resolved_po_number is None
        assert mem.resolved_grn_numbers == []
        assert mem.extraction_issues == []
        assert mem.agent_summaries == {}
        assert mem.current_recommendation is None
        assert mem.current_confidence == 0.0
        assert mem.facts == {}


# ─── record_agent_output — reasoning summary ─────────────────────────────────

class TestReasoningSummary:
    def test_reasoning_stored_in_agent_summaries(self):
        mem = AgentMemory()
        out = make_output(reasoning="Found vendor mismatch")
        mem.record_agent_output("EXCEPTION_ANALYSIS", out)
        assert mem.agent_summaries["EXCEPTION_ANALYSIS"] == "Found vendor mismatch"

    def test_reasoning_truncated_to_500_chars(self):
        mem = AgentMemory()
        long_reasoning = "A" * 600
        out = make_output(reasoning=long_reasoning)
        mem.record_agent_output("REVIEW_ROUTING", out)
        stored = mem.agent_summaries["REVIEW_ROUTING"]
        assert len(stored) == 500
        assert stored == "A" * 500

    def test_empty_reasoning_stored_as_empty(self):
        mem = AgentMemory()
        out = make_output(reasoning="")
        mem.record_agent_output("CASE_SUMMARY", out)
        assert mem.agent_summaries["CASE_SUMMARY"] == ""

    def test_multiple_agents_stored_separately(self):
        mem = AgentMemory()
        mem.record_agent_output("AGENT_A", make_output(reasoning="Summary A"))
        mem.record_agent_output("AGENT_B", make_output(reasoning="Summary B"))
        assert mem.agent_summaries["AGENT_A"] == "Summary A"
        assert mem.agent_summaries["AGENT_B"] == "Summary B"

    def test_later_agent_overwrites_same_type(self):
        mem = AgentMemory()
        mem.record_agent_output("AGENT_A", make_output(reasoning="First"))
        mem.record_agent_output("AGENT_A", make_output(reasoning="Second"))
        assert mem.agent_summaries["AGENT_A"] == "Second"


# ─── record_agent_output — recommendation promotion ─────────────────────────

class TestRecommendationPromotion:
    def test_first_recommendation_promoted(self):
        mem = AgentMemory()
        out = make_output(recommendation_type="AUTO_CLOSE", confidence=0.80)
        mem.record_agent_output("RECONCILIATION_ASSIST", out)
        assert mem.current_recommendation == "AUTO_CLOSE"
        assert mem.current_confidence == 0.80

    def test_higher_confidence_replaces_current(self):
        mem = AgentMemory()
        mem.record_agent_output("AGENT_A",
                                make_output(recommendation_type="SEND_TO_AP_REVIEW",
                                            confidence=0.60))
        mem.record_agent_output("AGENT_B",
                                make_output(recommendation_type="AUTO_CLOSE",
                                            confidence=0.90))
        assert mem.current_recommendation == "AUTO_CLOSE"
        assert mem.current_confidence == 0.90

    def test_lower_confidence_does_not_replace(self):
        mem = AgentMemory()
        mem.record_agent_output("AGENT_A",
                                make_output(recommendation_type="AUTO_CLOSE",
                                            confidence=0.90))
        mem.record_agent_output("AGENT_B",
                                make_output(recommendation_type="SEND_TO_AP_REVIEW",
                                            confidence=0.50))
        # Original stays
        assert mem.current_recommendation == "AUTO_CLOSE"
        assert mem.current_confidence == 0.90

    def test_equal_confidence_does_not_replace(self):
        """Strictly greater is required (not >=)."""
        mem = AgentMemory()
        mem.record_agent_output("AGENT_A",
                                make_output(recommendation_type="AUTO_CLOSE",
                                            confidence=0.80))
        mem.record_agent_output("AGENT_B",
                                make_output(recommendation_type="SEND_TO_AP_REVIEW",
                                            confidence=0.80))
        assert mem.current_recommendation == "AUTO_CLOSE"

    def test_none_recommendation_not_promoted(self):
        """recommendation_type=None is never promoted."""
        mem = AgentMemory()
        out = make_output(recommendation_type=None, confidence=0.99)
        mem.record_agent_output("AGENT_X", out)
        assert mem.current_recommendation is None
        assert mem.current_confidence == 0.0


# ─── record_agent_output — resolved_po_number ─────────────────────────────────

class TestResolvedPONumber:
    def test_found_po_sets_resolved_po_number(self):
        mem = AgentMemory()
        out = make_output(evidence={"found_po": "PO-2025-001"})
        mem.record_agent_output("PO_RETRIEVAL", out)
        assert mem.resolved_po_number == "PO-2025-001"

    def test_found_po_stripped_of_whitespace(self):
        mem = AgentMemory()
        out = make_output(evidence={"found_po": "  PO-2025-001  "})
        mem.record_agent_output("PO_RETRIEVAL", out)
        assert mem.resolved_po_number == "PO-2025-001"

    def test_empty_found_po_not_set(self):
        mem = AgentMemory()
        out = make_output(evidence={"found_po": ""})
        mem.record_agent_output("PO_RETRIEVAL", out)
        assert mem.resolved_po_number is None

    def test_whitespace_only_found_po_not_set(self):
        mem = AgentMemory()
        out = make_output(evidence={"found_po": "   "})
        mem.record_agent_output("PO_RETRIEVAL", out)
        assert mem.resolved_po_number is None

    def test_missing_found_po_key_not_set(self):
        mem = AgentMemory()
        out = make_output(evidence={"other_key": "value"})
        mem.record_agent_output("PO_RETRIEVAL", out)
        assert mem.resolved_po_number is None

    def test_non_string_found_po_not_set(self):
        mem = AgentMemory()
        out = make_output(evidence={"found_po": 12345})
        mem.record_agent_output("PO_RETRIEVAL", out)
        assert mem.resolved_po_number is None

    def test_later_agent_can_overwrite_po_number(self):
        mem = AgentMemory()
        mem.record_agent_output("PO_RETRIEVAL",
                                make_output(evidence={"found_po": "PO-OLD"}))
        mem.record_agent_output("RECONCILIATION_ASSIST",
                                make_output(evidence={"found_po": "PO-NEW"}))
        assert mem.resolved_po_number == "PO-NEW"


# ─── facts dict ──────────────────────────────────────────────────────────────

class TestFacts:
    def test_facts_accessible_after_seeding(self):
        mem = AgentMemory(facts={
            "grn_available": True,
            "is_two_way": False,
            "match_status": "PARTIAL_MATCH",
        })
        assert mem.facts["grn_available"] is True
        assert mem.facts["is_two_way"] is False
        assert mem.facts["match_status"] == "PARTIAL_MATCH"

    def test_facts_can_be_updated(self):
        mem = AgentMemory()
        mem.facts["vendor_name"] = "Test Corp"
        assert mem.facts["vendor_name"] == "Test Corp"

    def test_empty_facts_on_default_init(self):
        mem = AgentMemory()
        assert mem.facts == {}
