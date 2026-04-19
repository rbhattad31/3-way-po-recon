"""Compliance agent for benchmarking domain."""

from __future__ import annotations

import json
import logging

from apps.agents.services.llm_client import LLMClient, LLMMessage


logger = logging.getLogger(__name__)


class BenchmarkComplianceAgentBM:
    """Evaluate benchmark result against procurement compliance standards."""

    @classmethod
    def evaluate(cls, *, result, line_items: list) -> dict:
        deterministic = cls._evaluate_deterministic(result=result, line_items=line_items)
        llm_output = cls._evaluate_with_llm(deterministic=deterministic)
        if llm_output:
            return llm_output
        return deterministic

    @classmethod
    def _evaluate_with_llm(cls, *, deterministic: dict) -> dict | None:
        payload = {
            "task": "Evaluate benchmarking compliance and return strict JSON.",
            "rules": [
                "Status must be one of PASS, PARTIAL, FAIL.",
                "Use provided checks and thresholds only.",
                "Do not invent extra rules.",
                "Summary must be concise and actionable.",
            ],
            "input": deterministic,
            "required_output_schema": {
                "status": "PASS|PARTIAL|FAIL",
                "confidence": "float_0_to_1",
                "summary": "string",
                "violations": ["string"],
                "checks": [
                    {
                        "rule": "string",
                        "passed": "bool",
                        "actual": "number|string|null",
                        "threshold": "number|string|null",
                    }
                ],
            },
        }

        try:
            llm = LLMClient(temperature=0.0, max_tokens=1200)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You are a procurement compliance analyst. Return JSON only.",
                    ),
                    LLMMessage(role="user", content=json.dumps(payload)),
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.content or "")
            if not isinstance(parsed, dict):
                return None

            status = str(parsed.get("status") or "").strip().upper()
            if status not in {"PASS", "PARTIAL", "FAIL"}:
                return None

            checks = parsed.get("checks")
            if not isinstance(checks, list) or not checks:
                checks = deterministic.get("checks") or []

            violations = parsed.get("violations")
            if not isinstance(violations, list):
                violations = deterministic.get("violations") or []

            try:
                confidence = float(parsed.get("confidence"))
            except Exception:
                confidence = float(deterministic.get("confidence") or 0.7)
            confidence = max(0.0, min(1.0, confidence))

            summary = str(parsed.get("summary") or "").strip() or str(deterministic.get("summary") or "")

            return {
                "status": status,
                "confidence": confidence,
                "summary": summary,
                "checks": checks,
                "violations": violations,
            }
        except Exception:
            logger.exception("Compliance LLM path failed; using deterministic fallback")
            return None

    @classmethod
    def _evaluate_deterministic(cls, *, result, line_items: list) -> dict:
        total_lines = max(len(line_items), 1)
        high_lines = int(getattr(result, "lines_high", 0) or 0)
        review_lines = int(getattr(result, "lines_needs_review", 0) or 0)
        overall_deviation = getattr(result, "overall_deviation_pct", None)

        violations = []
        checks = []

        deviation_ok = overall_deviation is not None and abs(float(overall_deviation)) <= 15.0
        checks.append(
            {
                "rule": "overall_deviation_within_15pct",
                "passed": deviation_ok,
                "actual": overall_deviation,
                "threshold": 15.0,
            }
        )
        if not deviation_ok:
            violations.append("Overall deviation is outside acceptable 15% benchmark tolerance.")

        high_ratio = high_lines / float(total_lines)
        high_ratio_ok = high_ratio <= 0.25
        checks.append(
            {
                "rule": "high_variance_ratio_below_25pct",
                "passed": high_ratio_ok,
                "actual": round(high_ratio, 4),
                "threshold": 0.25,
            }
        )
        if not high_ratio_ok:
            violations.append("High-variance line ratio exceeds 25% of quotation scope.")

        review_ok = review_lines == 0
        checks.append(
            {
                "rule": "no_unresolved_review_lines",
                "passed": review_ok,
                "actual": review_lines,
                "threshold": 0,
            }
        )
        if not review_ok:
            violations.append("Some lines are still marked as NEEDS_REVIEW.")

        if not violations:
            status = "PASS"
            confidence = 0.95
            summary = "Compliance checks passed. Benchmark result is eligible for vendor decision."
        elif len(violations) == 1:
            status = "PARTIAL"
            confidence = 0.75
            summary = "Compliance partially satisfied. One corrective action is required before approval."
        else:
            status = "FAIL"
            confidence = 0.55
            summary = "Compliance failed. Multiple benchmark risk controls were violated."

        return {
            "status": status,
            "confidence": confidence,
            "summary": summary,
            "checks": checks,
            "violations": violations,
        }
