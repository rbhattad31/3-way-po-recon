"""Negotiation talking points agent for procurement benchmarking."""

from __future__ import annotations

import json
import logging
from typing import Optional

from apps.agents.services.llm_client import LLMClient, LLMMessage


logger = logging.getLogger(__name__)


class BenchmarkNegotiationTalkingPointsAgentBM:
    """Generate dynamic negotiation guidance from benchmark context."""

    @classmethod
    def generate(
        cls,
        *,
        bench_request,
        line_items: list,
        vendor_cards: list,
        ai_output: dict,
        compliance_output: dict,
        vendor_output: dict,
    ) -> dict:
        context_payload = cls._build_context_payload(
            bench_request=bench_request,
            line_items=line_items,
            vendor_cards=vendor_cards,
            ai_output=ai_output,
            compliance_output=compliance_output,
            vendor_output=vendor_output,
        )
        llm_output = cls._build_with_llm(context_payload=context_payload)
        if llm_output:
            return llm_output
        return cls._build_dynamic_fallback(context_payload=context_payload)

    @classmethod
    def respond(
        cls,
        *,
        bench_request,
        line_items: list,
        vendor_cards: list,
        ai_output: dict,
        compliance_output: dict,
        vendor_output: dict,
        user_prompt: str,
    ) -> dict:
        context_payload = cls._build_context_payload(
            bench_request=bench_request,
            line_items=line_items,
            vendor_cards=vendor_cards,
            ai_output=ai_output,
            compliance_output=compliance_output,
            vendor_output=vendor_output,
        )

        dynamic = cls._build_chat_response_with_llm(
            context_payload=context_payload,
            user_prompt=user_prompt,
        )
        if dynamic:
            return dynamic

        fallback = cls._build_dynamic_fallback(context_payload=context_payload)
        top_point = (fallback.get("talking_points") or [""])[0]
        return {
            "answer": top_point,
            "confidence": float(fallback.get("confidence") or 0.55),
            "recommended_vendor": context_payload.get("vendor_recommendation", {}).get("best_vendor_name", ""),
            "source": "fallback",
            "context_snapshot": context_payload.get("context_snapshot", {}),
        }

    @classmethod
    def _build_with_llm(cls, *, context_payload: dict) -> Optional[dict]:
        prompt = {
            "task": "Create negotiation talking points for a procurement benchmark review.",
            "rules": [
                "Use only the provided data.",
                "Focus on commercial negotiation language and requests to vendor.",
                "Prioritize concrete, quantified ask points based on variance and benchmark gaps.",
                "Return strict JSON only.",
            ],
            "input": context_payload,
            "required_output_schema": {
                "summary": "string",
                "confidence": "float_0_to_1",
                "talking_points": ["string"],
                "fallback_positions": ["string"],
                "red_flags": ["string"],
            },
        }

        try:
            llm = LLMClient(temperature=0.0, max_tokens=1400)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You are a procurement negotiation strategist. Return JSON only.",
                    ),
                    LLMMessage(role="user", content=json.dumps(prompt)),
                ],
                response_format={"type": "json_object"},
            )

            parsed = json.loads(response.content or "")
            if not isinstance(parsed, dict):
                return None

            summary = str(parsed.get("summary") or "").strip()
            talking_points = [
                str(item).strip() for item in (parsed.get("talking_points") or []) if str(item).strip()
            ]
            fallback_positions = [
                str(item).strip() for item in (parsed.get("fallback_positions") or []) if str(item).strip()
            ]
            red_flags = [
                str(item).strip() for item in (parsed.get("red_flags") or []) if str(item).strip()
            ]

            if not summary or not talking_points:
                return None

            try:
                confidence = float(parsed.get("confidence"))
            except Exception:
                confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))

            return {
                "summary": summary,
                "confidence": confidence,
                "talking_points": talking_points[:12],
                "fallback_positions": fallback_positions[:8],
                "red_flags": red_flags[:8],
                "context_snapshot": context_payload.get("context_snapshot", {}),
                "source": "llm",
            }
        except Exception:
            logger.exception("Negotiation Talking Points LLM path failed; deterministic fallback used")
            return None

    @classmethod
    def _build_chat_response_with_llm(
        cls,
        *,
        context_payload: dict,
        user_prompt: str,
    ) -> Optional[dict]:
        prompt = {
            "task": "Answer a procurement negotiator question using request-specific benchmark context.",
            "rules": [
                "Use only the provided benchmark context.",
                "Do not hallucinate vendors, rates, or compliance status.",
                "If data is missing, say that clearly and suggest the next best question.",
                "Return strict JSON only.",
            ],
            "negotiator_question": str(user_prompt or "").strip(),
            "context": context_payload,
            "required_output_schema": {
                "answer": "string",
                "confidence": "float_0_to_1",
                "next_best_question": "string",
                "risk_flags": ["string"],
                "suggested_vendor": "string",
            },
        }

        try:
            llm = LLMClient(temperature=0.0, max_tokens=1400)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You are a procurement negotiation talking agent. "
                            "Answer as a concise commercial advisor using only provided context. "
                            "Return JSON only."
                        ),
                    ),
                    LLMMessage(role="user", content=json.dumps(prompt)),
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.content or "")
            if not isinstance(parsed, dict):
                return None

            answer = str(parsed.get("answer") or "").strip()
            if not answer:
                return None

            try:
                confidence = float(parsed.get("confidence"))
            except Exception:
                confidence = 0.65
            confidence = max(0.0, min(1.0, confidence))

            return {
                "answer": answer,
                "confidence": confidence,
                "next_best_question": str(parsed.get("next_best_question") or "").strip(),
                "risk_flags": [
                    str(item).strip()
                    for item in (parsed.get("risk_flags") or [])
                    if str(item).strip()
                ][:8],
                "recommended_vendor": str(
                    parsed.get("suggested_vendor")
                    or context_payload.get("vendor_recommendation", {}).get("best_vendor_name")
                    or ""
                ).strip(),
                "source": "llm",
                "context_snapshot": context_payload.get("context_snapshot", {}),
            }
        except Exception:
            logger.exception("Negotiation chat LLM path failed; fallback used")
            return None

    @classmethod
    def _build_context_payload(
        cls,
        *,
        bench_request,
        line_items: list,
        vendor_cards: list,
        ai_output: dict,
        compliance_output: dict,
        vendor_output: dict,
    ) -> dict:
        total_lines = len(line_items)
        high_items = [li for li in line_items if getattr(li, "variance_status", "") == "HIGH"]
        moderate_items = [li for li in line_items if getattr(li, "variance_status", "") == "MODERATE"]
        unresolved_items = [li for li in line_items if getattr(li, "benchmark_mid", None) is None]

        compliance_status = str(compliance_output.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"

        top_variance_lines = []
        sortable = []
        for li in line_items:
            try:
                variance_abs = abs(float(getattr(li, "variance_pct", 0.0) or 0.0))
            except Exception:
                variance_abs = 0.0
            sortable.append((variance_abs, li))
        sortable.sort(key=lambda row: row[0], reverse=True)
        for _, li in sortable[:8]:
            top_variance_lines.append(
                {
                    "line_number": int(getattr(li, "line_number", 0) or 0),
                    "description": str(getattr(li, "description", "") or "")[:180],
                    "category": str(getattr(li, "category", "") or "UNCATEGORIZED"),
                    "quoted_unit_rate": float(getattr(li, "quoted_unit_rate", 0.0) or 0.0),
                    "benchmark_mid": (
                        float(getattr(li, "benchmark_mid", 0.0) or 0.0)
                        if getattr(li, "benchmark_mid", None) is not None
                        else None
                    ),
                    "variance_pct": (
                        float(getattr(li, "variance_pct", 0.0) or 0.0)
                        if getattr(li, "variance_pct", None) is not None
                        else None
                    ),
                    "variance_status": str(getattr(li, "variance_status", "") or "NEEDS_REVIEW"),
                    "benchmark_source": str(getattr(li, "benchmark_source", "") or "NONE"),
                }
            )

        compact_vendor_cards = []
        for card in vendor_cards[:8]:
            compact_vendor_cards.append(
                {
                    "quotation_id": card.get("quotation_id"),
                    "supplier_name": card.get("supplier_name") or "",
                    "deviation_pct": card.get("deviation_pct"),
                    "line_count": int(card.get("line_count", 0) or 0),
                    "benchmarked_line_count": int(card.get("benchmarked_line_count", 0) or 0),
                    "status_counts": card.get("status_counts") or {},
                    "live_reference_count": int(card.get("live_reference_count", 0) or 0),
                    "total_quoted": card.get("total_quoted"),
                    "total_benchmark": card.get("total_benchmark"),
                }
            )

        ai_actions = [
            str(item).strip()
            for item in (ai_output.get("actions") or [])
            if str(item).strip()
        ][:8]

        ai_insights = [
            str(item).strip()
            for item in (ai_output.get("insights") or [])
            if str(item).strip()
        ][:12]

        return {
            "request": {
                "request_pk": int(getattr(bench_request, "pk", 0) or 0),
                "title": str(getattr(bench_request, "title", "") or ""),
                "geography": str(getattr(bench_request, "geography", "") or ""),
                "scope_type": str(getattr(bench_request, "scope_type", "") or ""),
                "store_type": str(getattr(bench_request, "store_type", "") or ""),
            },
            "vendor_recommendation": {
                "recommended": bool(vendor_output.get("recommended")),
                "best_vendor_name": str(vendor_output.get("best_vendor_name") or "").strip(),
                "summary": str(vendor_output.get("summary") or "").strip(),
                "market_standards": [
                    str(item).strip()
                    for item in (vendor_output.get("market_standards") or [])
                    if str(item).strip()
                ][:8],
            },
            "compliance": {
                "status": compliance_status,
                "summary": str(compliance_output.get("summary") or "").strip(),
                "blocking_issues": [
                    str(item).strip()
                    for item in (compliance_output.get("blocking_issues") or [])
                    if str(item).strip()
                ][:8],
            },
            "ai_signals": {
                "actions": ai_actions,
                "insights": ai_insights,
                "summary": str(ai_output.get("summary") or "").strip(),
            },
            "vendors": compact_vendor_cards,
            "top_variance_lines": top_variance_lines,
            "context_snapshot": {
                "total_lines": total_lines,
                "vendor_count": len(vendor_cards),
                "high_count": len(high_items),
                "moderate_count": len(moderate_items),
                "unresolved_count": len(unresolved_items),
                "compliance_status": compliance_status,
            },
        }

    @classmethod
    def _build_dynamic_fallback(cls, *, context_payload: dict) -> dict:
        snapshot = context_payload.get("context_snapshot", {})
        recommended_vendor = str(
            context_payload.get("vendor_recommendation", {}).get("best_vendor_name") or ""
        ).strip()
        compliance_status = str(snapshot.get("compliance_status") or "UNKNOWN")
        high_count = int(snapshot.get("high_count", 0) or 0)
        unresolved_count = int(snapshot.get("unresolved_count", 0) or 0)
        moderate_count = int(snapshot.get("moderate_count", 0) or 0)

        headline = "Prioritize line-level repricing against benchmark mid values."
        if recommended_vendor:
            headline = (
                f"Open negotiation with {recommended_vendor} using benchmark-backed line-level counters "
                "before final award."
            )

        talking_points = [
            headline,
            f"Address HIGH variance items first: {high_count} line(s) currently exceed tolerance.",
            f"Bundle MODERATE variance items: {moderate_count} line(s) for package discount negotiation.",
            (
                "Request documentary rate basis for unresolved lines: "
                f"{unresolved_count} line(s) have no benchmark mid value."
            ),
        ]

        red_flags = []
        if compliance_status in {"FAIL", "PARTIAL"}:
            red_flags.append(
                f"Compliance status is {compliance_status}. Keep award conditional on corrective commercial response."
            )
        if unresolved_count > 0:
            red_flags.append(
                f"{unresolved_count} unresolved line(s) reduce pricing certainty and must be closed before commitment."
            )
        if not red_flags:
            red_flags.append("No blocking compliance or benchmark completeness red flags detected.")

        fallback_positions = [
            "Use benchmark corridor and market references as non-negotiable price guardrails.",
            "If vendor cannot match, negotiate phased rebates, bundled discount, or scope/value trade-off.",
        ]

        return {
            "summary": (
                "Dynamic fallback guidance generated from benchmark context: "
                f"high={high_count}, moderate={moderate_count}, unresolved={unresolved_count}, "
                f"compliance={compliance_status}."
            ),
            "confidence": 0.58,
            "talking_points": [item for item in talking_points if item][:12],
            "fallback_positions": fallback_positions[:8],
            "red_flags": red_flags[:8],
            "context_snapshot": snapshot,
            "source": "dynamic_fallback",
        }
