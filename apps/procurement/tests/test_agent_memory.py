from __future__ import annotations

from apps.procurement.runtime.procurement_agent_memory import ProcurementAgentMemory


def test_record_agent_output_promotes_higher_confidence():
    mem = ProcurementAgentMemory()

    mem.record_agent_output(
        "recommendation",
        {
            "recommended_option": "Option-A",
            "confidence_score": 0.70,
            "reasoning_summary": "first",
        },
    )
    mem.record_agent_output(
        "benchmark",
        {
            "recommended_option": "Option-B",
            "confidence": 0.91,
            "reasoning": "second",
        },
    )

    assert mem.current_recommendation == "Option-B"
    assert mem.current_confidence == 0.91
    assert mem.agent_summaries["recommendation"] == "first"
    assert mem.agent_summaries["benchmark"] == "second"


def test_record_agent_output_absorbs_evidence_bags():
    mem = ProcurementAgentMemory()

    mem.record_agent_output(
        "benchmark",
        {
            "reasoning_summary": "ok",
            "confidence": 0.5,
            "evidence": {
                "benchmark_findings": {"line-1": {"avg": 100}},
                "compliance_result": {"status": "PASS"},
                "validation_flags": {"missing_spec": "Need COP value"},
                "market_signals": ["steel up 4%"],
            },
        },
    )

    assert "line-1" in mem.benchmark_findings
    assert mem.compliance_findings["status"] == "PASS"
    assert mem.validation_flags["missing_spec"] == "Need COP value"
    assert mem.market_signals == ["steel up 4%"]
