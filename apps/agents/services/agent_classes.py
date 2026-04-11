"""Concrete agent implementations for the PO Reconciliation agentic layer.

Each agent is specialised for one phase of the reconciliation intelligence pipeline.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from apps.agents.services.base_agent import AgentOutput, BaseAgent, AgentContext
from apps.core.enums import AgentRunStatus, AgentType, RecommendationType
from apps.core.evaluation_constants import EXTRACTION_CONFIDENCE
from apps.core.prompt_registry import PromptRegistry
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared prompt fragments
# ---------------------------------------------------------------------------


def _mode_context(ctx: AgentContext) -> str:
    """Return a short reconciliation-mode context string for user messages."""
    mode = ctx.reconciliation_mode or ctx.extra.get("reconciliation_mode", "")
    if mode == "TWO_WAY":
        return (
            "Reconciliation Mode: 2-WAY (Invoice vs PO only — GRN/receipt data is NOT part of this reconciliation. "
            "Do NOT flag GRN-related issues.)\n"
        )
    return "Reconciliation Mode: 3-WAY (Invoice vs PO vs GRN)\n"


def _parse_agent_json(content: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from LLM content."""
    content = content.strip()
    # Strip markdown fences
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]
    content = content.strip()
    if content.startswith("json"):
        content = content[4:].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def _to_agent_output(data: Dict[str, Any], raw: str) -> AgentOutput:
    from apps.agents.services.agent_output_schema import AgentOutputSchema
    import logging as _log
    _logger = _log.getLogger(__name__)
    try:
        validated = AgentOutputSchema.model_validate(data)
    except Exception as exc:
        _logger.warning("AgentOutputSchema validation failed (%s) -- using defaults", exc)
        validated = AgentOutputSchema()
    return AgentOutput(
        reasoning=validated.reasoning or raw[:500],
        recommendation_type=validated.recommendation_type,
        confidence=validated.confidence,
        evidence=validated.evidence,
        decisions=[d.model_dump() for d in validated.decisions],
        tools_used=validated.tools_used,
        raw_content=raw,
    )


# ============================================================================
# 1. Exception Analysis Agent
# ============================================================================
class ExceptionAnalysisAgent(BaseAgent):
    """Analyses reconciliation exceptions, determines root causes, and recommends actions.

    After the standard ReAct analysis loop, a second targeted LLM call
    generates a structured reviewer-facing summary that is persisted on the
    ReviewAssignment so human reviewers can see it immediately when opening
    the review ticket.
    """

    agent_type = AgentType.EXCEPTION_ANALYSIS

    # Self-contained system prompt for the dedicated reviewer summary call.
    _REVIEWER_SUMMARY_SYSTEM_PROMPT = (
        "You are an AP review assistant. You will receive a reconciliation analysis result.\n"
        "Your job is to produce a structured reviewer summary for a human AP reviewer.\n"
        "The reviewer is not technical -- write in plain business language.\n"
        "Respond ONLY with valid JSON in this exact schema:\n"
        '{\n'
        '  "recommendation": "APPROVE|APPROVE_WITH_FIXES|REJECT|NEEDS_INFO|ESCALATE",\n'
        '  "risk_level": "LOW|MEDIUM|HIGH",\n'
        '  "confidence": 0.85,\n'
        '  "summary": "One paragraph. State what matched, what did not, and what action is recommended.",\n'
        '  "suggested_actions": [\n'
        '    {"action": "string", "field": "field name", "current_value": "x", "suggested_value": "y"}\n'
        '  ]\n'
        '}'
    )

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.exception_analysis")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Extraction Confidence: {ctx.reconciliation_result.extraction_confidence}\n"
            f"Exceptions ({len(ctx.exceptions)}):\n"
            + json.dumps(ctx.exceptions, indent=2, default=str)
            + "\n\nAnalyse these exceptions. Use tools if you need more context."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["po_lookup", "grn_lookup", "invoice_details", "exception_list", "reconciliation_summary"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)

        # Validate recommendation_type against the enum.
        valid_rec_types = {rt.value for rt in RecommendationType}
        rec_type = data.get("recommendation_type")
        if rec_type is not None and rec_type not in valid_rec_types:
            data["recommendation_type"] = RecommendationType.SEND_TO_AP_REVIEW.value
            data["confidence"] = min(data.get("confidence") or 0.0, 0.6)

        # Clamp confidence to [0.0, 1.0].
        try:
            data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            data["confidence"] = 0.0

        return _to_agent_output(data, content)

    # ------------------------------------------------------------------
    # Overridden run() -- adds reviewer summary write after ReAct loop
    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext, review_assignment=None):
        """Execute the standard ReAct loop, then generate and persist a reviewer summary.

        Args:
            ctx: Agent context (same as BaseAgent).
            review_assignment: Optional ReviewAssignment to write the summary to.
        """
        agent_run = super().run(ctx)

        # Generate reviewer summary via a dedicated second LLM call.
        reviewer_data = self._generate_reviewer_summary(agent_run, ctx)
        if reviewer_data and review_assignment:
            review_assignment.reviewer_summary = reviewer_data.get("summary", "")
            review_assignment.reviewer_risk_level = reviewer_data.get("risk_level", "")
            review_assignment.reviewer_confidence = reviewer_data.get("confidence")
            review_assignment.reviewer_recommendation = reviewer_data.get("recommendation", "")
            review_assignment.reviewer_suggested_actions = reviewer_data.get("suggested_actions", [])
            review_assignment.reviewer_summary_generated_at = timezone.now()
            review_assignment.save(update_fields=[
                "reviewer_summary", "reviewer_risk_level", "reviewer_confidence",
                "reviewer_recommendation", "reviewer_suggested_actions",
                "reviewer_summary_generated_at",
            ])
            # Record as a second decision log entry for audit trail
            from apps.agents.services.agent_trace_service import AgentTraceService
            AgentTraceService.log_agent_decision(
                agent_run_id=agent_run.pk,
                decision_type="REVIEWER_SUMMARY",
                summary=reviewer_data.get("summary", ""),
                confidence=reviewer_data.get("confidence"),
                evidence=reviewer_data,
            )
        elif not reviewer_data and review_assignment:
            logger.warning(
                "ExceptionAnalysisAgent run %s did not produce a reviewer summary",
                agent_run.pk,
            )

        return agent_run

    def _generate_reviewer_summary(self, agent_run, ctx: AgentContext) -> Optional[dict]:
        """Make a dedicated second LLM call to generate the reviewer summary.

        Failure is non-fatal -- returns None on any exception so the main
        agent_run result is never affected.
        """
        from apps.agents.services.llm_client import LLMMessage
        try:
            output_payload = agent_run.output_payload or {}
            recommendation_type = output_payload.get("recommendation_type", "")
            confidence = output_payload.get("confidence", "")
            evidence = output_payload.get("evidence") or {}
            summarized_reasoning = agent_run.summarized_reasoning or ""

            user_msg = (
                "Reconciliation analysis result:\n"
                f"Recommendation: {recommendation_type}\n"
                f"Confidence: {confidence}\n"
                f"Reasoning: {summarized_reasoning[:600]}\n"
                f"Key evidence: {json.dumps(evidence, default=str)[:400]}\n\n"
                "Produce the reviewer summary JSON now."
            )

            self.llm._langfuse_metadata = {
                "agent_type": str(self.agent_type),
                "call_type": "reviewer_summary",
                "agent_run_id": agent_run.pk,
                "invoice_id": ctx.invoice_id,
                "po_number": ctx.po_number or "",
                "trace_id": ctx.trace_id or "",
                "user_id": ctx.actor_user_id or "",
                "session_id": f"invoice-{ctx.invoice_id}" if ctx.invoice_id else "",
            }
            try:
                response = self.llm.chat(
                    messages=[
                        LLMMessage(role="system", content=self._REVIEWER_SUMMARY_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=user_msg),
                    ],
                    tools=None,
                    response_format={"type": "json_object"},
                )
            finally:
                self.llm._langfuse_metadata = {}

            content = (response.content or "").strip()
            if not content:
                logger.warning("ExceptionAnalysisAgent reviewer summary call returned empty content for run %s", agent_run.pk)
                return None
            return json.loads(content)
        except Exception as exc:
            logger.warning("ExceptionAnalysisAgent reviewer summary call failed for run %s: %s", agent_run.pk, exc)
            return None


# ============================================================================
# 2. Invoice Extraction Agent
# ============================================================================
class InvoiceExtractionAgent(BaseAgent):
    """Extracts structured data from OCR text using GPT-4o.

    Runs right after Azure Document Intelligence OCR.  No tools — single-shot
    extraction with ``response_format=json_object`` and ``temperature=0``.
    Provides full agent traceability (AgentRun, AgentMessage, AgentStep).
    """

    agent_type = AgentType.INVOICE_EXTRACTION
    enforce_json_response = False  # Handles its own response_format in run()

    def __init__(self):
        from apps.agents.services.llm_client import LLMClient
        super().__init__()
        # Override LLM settings for deterministic extraction
        self.llm = LLMClient(temperature=0.0, max_tokens=4096)

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("extraction.invoice_system")

    def build_user_message(self, ctx: AgentContext) -> str:
        ocr_text = ctx.extra.get("ocr_text", "")
        return f"Extract invoice data from the following OCR text:\n\n{ocr_text}"

    @property
    def allowed_tools(self) -> List[str]:
        return []  # Single-shot extraction — no tools needed

    def _init_messages(self, ctx: AgentContext, agent_run) -> List[Dict[str, str]]:
        """Resolve the system prompt and record which source path was used.

        Precedence:
          1. composed_prompt  — from InvoicePromptComposer via ctx.extra (normal operation)
          2. system_prompt    — monolithic PromptRegistry.get("extraction.invoice_system") fallback
          3. (agent_default)  — only if system_prompt itself raises (recorded but never suppressed)

        The resolved source type is stored on self._prompt_source_type so that
        run() can persist it to AgentRun.input_payload for governance audit.
        """
        from apps.agents.services.llm_client import LLMMessage  # local to avoid circular import
        composed = ctx.extra.get("composed_prompt")
        if composed:
            sys_msg = composed
            self._prompt_source_type = "composed"
        else:
            sys_msg = self.system_prompt
            self._prompt_source_type = "monolithic_fallback"

        user_msg = self.build_user_message(ctx)
        self._save_message(agent_run, "system", sys_msg, 0)
        self._save_message(agent_run, "user", user_msg, 1)
        return [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        # Store the raw extracted JSON in evidence for downstream consumption
        return AgentOutput(
            reasoning=f"Extracted {len(data.get('line_items', []))} line items with confidence {data.get('confidence', 0)}",
            recommendation_type=None,
            confidence=float(data.get("confidence", 0.0)),
            evidence=data,  # The full extraction JSON
            decisions=[],
            raw_content=content,
        )

    def run(self, ctx: AgentContext):
        """Override to pass response_format=json_object for structured output."""
        from apps.agents.models import AgentDefinition, AgentRun
        from apps.agents.services.llm_client import LLMMessage

        agent_def = AgentDefinition.objects.filter(
            agent_type=self.agent_type, enabled=True
        ).first()

        agent_run = AgentRun.objects.create(
            agent_definition=agent_def,
            agent_type=self.agent_type,
            reconciliation_result=ctx.reconciliation_result,
            document_upload_id=ctx.document_upload_id,
            status=AgentRunStatus.RUNNING,
            input_payload=self._serialise_context(ctx),
            started_at=timezone.now(),
            llm_model_used=self.llm.model,
            # RBAC metadata from context
            actor_user_id=ctx.actor_user_id,
            actor_primary_role=ctx.actor_primary_role,
            actor_roles_snapshot_json=ctx.actor_roles_snapshot or None,
            permission_checked=ctx.permission_checked,
            permission_source=ctx.permission_source,
            access_granted=ctx.access_granted,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
        )

        import time as _time
        start = _time.monotonic()

        # Open a Langfuse trace + span for this extraction run.
        # OCR text is stored as span input so it can be copied into
        # the Langfuse playground for prompt testing.
        _lf_trace = getattr(ctx, "_langfuse_trace", None)
        _lf_span = None
        _own_trace = False
        ocr_text = ctx.extra.get("ocr_text", "")
        try:
            from apps.core.langfuse_client import start_trace, start_span, end_span, score_trace
            if _lf_trace is None:
                # Extraction runs standalone (no orchestration trace) — create root trace.
                import uuid
                _trace_id = ctx.trace_id or uuid.uuid4().hex
                _lf_trace = start_trace(
                    _trace_id,
                    "invoice_extraction",
                    invoice_id=ctx.invoice_id or None,
                    user_id=ctx.actor_user_id or None,
                    session_id=f"invoice-{ctx.invoice_id}" if ctx.invoice_id else None,
                    metadata={"agent_run_id": agent_run.pk},
                )
                _own_trace = True
            _lf_span = start_span(
                _lf_trace,
                name="INVOICE_EXTRACTION",
                metadata={
                    "agent_run_id": agent_run.pk,
                    "invoice_id": ctx.invoice_id,
                    "ocr_char_count": len(ocr_text),
                },
            )
            if _lf_span:
                _lf_span.update(input={"ocr_text": ocr_text})
        except Exception:
            _lf_span = None
        self.llm._langfuse_span = _lf_span
        # Build base metadata then merge prompt-composition metadata from ctx.extra
        _prompt_meta = ctx.extra.get("prompt_metadata", {})
        self.llm._langfuse_metadata = {
            "agent_type": str(self.agent_type),
            "invoice_id": ctx.invoice_id,
            "trace_id": ctx.trace_id or "",
            "agent_run_id": agent_run.pk,
            "ocr_char_count": len(ocr_text),
            # Prompt composition fields (populated by InvoiceExtractionAdapter)
            "invoice_category": _prompt_meta.get("invoice_category", ""),
            "invoice_category_confidence": _prompt_meta.get("invoice_category_confidence", 0.0),
            "base_prompt_key": _prompt_meta.get("base_prompt_key", ""),
            "base_prompt_version": _prompt_meta.get("base_prompt_version", ""),
            "category_prompt_key": _prompt_meta.get("category_prompt_key", ""),
            "category_prompt_version": _prompt_meta.get("category_prompt_version", ""),
            "country_prompt_key": _prompt_meta.get("country_prompt_key", ""),
            "country_prompt_version": _prompt_meta.get("country_prompt_version", ""),
            "prompt_hash": _prompt_meta.get("prompt_hash", ""),
            "schema_code": _prompt_meta.get("schema_code", ""),
        }

        try:
            messages = self._init_messages(ctx, agent_run)

            llm_resp = self.llm.chat(
                messages=[
                    LLMMessage(role=m["role"], content=m["content"])
                    for m in messages
                ],
                response_format={"type": "json_object"},
            )

            agent_run.prompt_tokens = llm_resp.prompt_tokens
            agent_run.completion_tokens = llm_resp.completion_tokens
            agent_run.total_tokens = llm_resp.total_tokens

            self._save_message(agent_run, "assistant", llm_resp.content or "", len(messages))

            output = self.interpret_response(llm_resp.content or "", ctx)
            self._finalise_run(agent_run, output, start, agent_def=agent_def)

            # Persist prompt composition metadata on AgentRun.input_payload so it
            # is queryable without Langfuse.  Uses the same post-creation update
            # pattern as the orchestrator (safe on existing input_payload dict).
            try:
                _pm = ctx.extra.get("prompt_metadata", {})
                _ph = _pm.get("prompt_hash", "")
                _src = getattr(self, "_prompt_source_type", "unknown")
                _prompt_persistence = {
                    "prompt_source_type": _src,
                    "prompt_hash": _ph,
                    "base_prompt_key": _pm.get("base_prompt_key", ""),
                    "base_prompt_version": _pm.get("base_prompt_version", ""),
                    "category_prompt_key": _pm.get("category_prompt_key", ""),
                    "category_prompt_version": _pm.get("category_prompt_version", ""),
                    "country_prompt_key": _pm.get("country_prompt_key", ""),
                    "country_prompt_version": _pm.get("country_prompt_version", ""),
                    "invoice_category": _pm.get("invoice_category", ""),
                    "components": _pm.get("components", {}),
                }
                agent_run.input_payload = agent_run.input_payload or {}
                agent_run.input_payload["_prompt_meta"] = _prompt_persistence
                # Also stamp on the dedicated prompt_version field (prompt hash, max 50 chars)
                agent_run.prompt_version = _ph[:50] if _ph else _src[:50]
                # invocation_reason records the source path for quick filtering
                agent_run.invocation_reason = f"extraction:{_src}"
                agent_run.save(update_fields=["input_payload", "prompt_version", "invocation_reason"])
            except Exception as _pe:
                logger.warning("InvoiceExtractionAgent: failed to persist prompt metadata: %s", _pe)

            # Close Langfuse span with extraction output + prompt metadata.
            if _lf_span is not None:
                try:
                    _prompt_meta = ctx.extra.get("prompt_metadata", {})
                    end_span(_lf_span, output={
                        "confidence": output.confidence,
                        "vendor_name": output.evidence.get("vendor_name", ""),
                        "invoice_number": output.evidence.get("invoice_number", ""),
                        "total_amount": output.evidence.get("total_amount", ""),
                        "line_items_count": len(output.evidence.get("line_items", [])),
                        # Prompt composition summary
                        "invoice_category": _prompt_meta.get("invoice_category", ""),
                        "prompt_hash": _prompt_meta.get("prompt_hash", ""),
                        "prompt_components_count": len(_prompt_meta.get("components", {})),
                    })
                    if _own_trace and _lf_trace:
                        score_trace(
                            getattr(_lf_trace, "trace_id", ""),
                            EXTRACTION_CONFIDENCE,
                            output.confidence,
                            span=_lf_trace,
                        )
                        end_span(_lf_trace, output={"confidence": output.confidence})
                except Exception:
                    pass

        except Exception as exc:
            logger.exception("InvoiceExtractionAgent failed")
            agent_run.status = AgentRunStatus.FAILED
            agent_run.error_message = str(exc)[:2000]
            agent_run.duration_ms = int((_time.monotonic() - start) * 1000)
            agent_run.completed_at = timezone.now()
            agent_run.save()
            if _lf_span is not None:
                try:
                    end_span(_lf_span, output={"error": str(exc)[:200]}, level="ERROR")
                    if _own_trace and _lf_trace:
                        end_span(_lf_trace, output={"error": str(exc)[:200]}, level="ERROR")
                except Exception:
                    pass
        finally:
            self.llm._langfuse_span = None
            self.llm._langfuse_metadata = {}

        return agent_run


# ============================================================================
# 3. Invoice Understanding Agent
# ============================================================================
class InvoiceUnderstandingAgent(BaseAgent):
    """Deep-dives into invoice data to resolve ambiguity or extraction issues."""

    agent_type = AgentType.INVOICE_UNDERSTANDING

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.invoice_understanding")

    def build_user_message(self, ctx: AgentContext) -> str:
        rr = ctx.reconciliation_result
        extraction_conf = (
            rr.extraction_confidence
            if rr
            else (ctx.memory.facts.get("extraction_confidence", "N/A") if ctx.memory else "N/A")
        )
        match_status = (
            rr.match_status
            if rr
            else (ctx.memory.facts.get("match_status", "PRE_RECONCILIATION") if ctx.memory else "PRE_RECONCILIATION")
        )
        lines = [
            f"Invoice ID: {ctx.invoice_id}",
            f"PO Number: {ctx.po_number or 'N/A'}",
            f"Extraction Confidence: {extraction_conf}",
            f"Match Status: {match_status}",
        ]
        validation_warnings = (
            ctx.memory.facts.get("validation_warnings") if ctx.memory else None
        ) or ctx.extra.get("validation_warnings")
        if validation_warnings:
            lines.append(f"Validation Warnings: {validation_warnings}")
        lines.append("\nRetrieve invoice details using the invoice_details tool, then analyse quality.")
        return "\n".join(lines)

    @property
    def allowed_tools(self) -> List[str]:
        return ["invoice_details", "po_lookup", "vendor_search"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 3. PO Retrieval Agent
# ============================================================================
class PORetrievalAgent(BaseAgent):
    """Attempts to find the correct PO when deterministic lookup failed."""

    agent_type = AgentType.PO_RETRIEVAL

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.po_retrieval")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            f"Invoice ID: {ctx.invoice_id}\n"
            f"Extracted PO Number (failed lookup): {ctx.po_number or 'MISSING'}\n"
            f"Vendor on invoice: {ctx.extra.get('vendor_name', 'unknown')}\n"
            f"Invoice total: {ctx.extra.get('total_amount', 'unknown')}\n"
            "\nTry to find the correct PO using the available tools."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["po_lookup", "vendor_search", "invoice_details"]

    _PO_EVIDENCE_FALLBACK_KEYS = ("po_number", "matched_po", "result", "found", "po")

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)

        # Normalise evidence so the orchestrator feedback loop always finds
        # the PO number under the canonical "found_po" key.
        evidence = data.get("evidence") or {}
        if "found_po" not in evidence:
            for key in self._PO_EVIDENCE_FALLBACK_KEYS:
                value = evidence.get(key)
                if isinstance(value, str) and value.strip():
                    evidence["found_po"] = value.strip()
                    break
            data["evidence"] = evidence

        return _to_agent_output(data, content)


# ============================================================================
# 4. GRN Retrieval Agent
# ============================================================================
class GRNRetrievalAgent(BaseAgent):
    """Investigates GRN data when GRN is missing or has receipt issues.

    NOTE: This agent is only invoked in 3-way mode. The PolicyEngine
    suppresses it when the reconciliation mode is TWO_WAY.
    """

    agent_type = AgentType.GRN_RETRIEVAL

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.grn_retrieval")

    def build_user_message(self, ctx: AgentContext) -> str:
        # Guard: this agent is only applicable in THREE_WAY mode.
        if ctx.reconciliation_mode == "TWO_WAY":
            return (
                '{"reasoning": "Agent not applicable in TWO_WAY mode", '
                '"recommendation_type": null, "confidence": 0.0, "decisions": [], "evidence": {}}'
            )
        grn_available = (
            ctx.memory.facts.get("grn_available", ctx.extra.get("grn_available", "unknown"))
            if ctx.memory else ctx.extra.get("grn_available", "unknown")
        )
        grn_fully_received = (
            ctx.memory.facts.get("grn_fully_received", ctx.extra.get("grn_fully_received", "unknown"))
            if ctx.memory else ctx.extra.get("grn_fully_received", "unknown")
        )
        return (
            _mode_context(ctx)
            + f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"GRN Available: {grn_available}\n"
            f"GRN Fully Received: {grn_fully_received}\n"
            "\nInvestigate GRN data for this PO."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["grn_lookup", "po_lookup", "invoice_details"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 5. Review Routing Agent
# ============================================================================
class ReviewRoutingAgent(BaseAgent):
    """Determines the best review queue/team and priority for a reconciliation case."""

    agent_type = AgentType.REVIEW_ROUTING

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.review_routing")

    def build_user_message(self, ctx: AgentContext) -> str:
        prior_lines = []
        if ctx.memory:
            for agent_type, summary in ctx.memory.agent_summaries.items():
                prior_lines.append(f"  {agent_type}: {summary}")
            if ctx.memory.current_recommendation:
                prior_lines.append(
                    f"  Current best recommendation: {ctx.memory.current_recommendation} "
                    f"(confidence {ctx.memory.current_confidence:.0%})"
                )
        prior_block = "\n".join(prior_lines) if prior_lines else "No prior agent findings."
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Exceptions: {json.dumps(ctx.exceptions, indent=2, default=str)}\n"
            f"Prior agent findings:\n{prior_block}\n"
            "\nDetermine the best routing for this case."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["reconciliation_summary", "exception_list"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 6. Case Summary Agent
# ============================================================================
class CaseSummaryAgent(BaseAgent):
    """Produces a human-readable case summary for reviewers."""

    agent_type = AgentType.CASE_SUMMARY

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.case_summary")

    def build_user_message(self, ctx: AgentContext) -> str:
        prior = ctx.memory.agent_summaries if ctx.memory else {}
        rec = (ctx.memory.current_recommendation or "N/A") if ctx.memory else "N/A"
        conf = (ctx.memory.current_confidence or 0.0) if ctx.memory else 0.0
        prior_text = " | ".join(f"{k}: {v[:100]}" for k, v in prior.items()) or "N/A"
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Prior analysis: {prior_text}\n"
            f"Recommendation: {rec} (confidence {conf:.0%})\n"
            "\nGather data using tools, then produce a clear case summary."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return [
            "invoice_details", "po_lookup", "grn_lookup",
            "reconciliation_summary", "exception_list",
        ]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 7. Reconciliation Assist Agent
# ============================================================================
class ReconciliationAssistAgent(BaseAgent):
    """General-purpose assistant that helps resolve partial matches."""

    agent_type = AgentType.RECONCILIATION_ASSIST

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.reconciliation_assist")

    def build_user_message(self, ctx: AgentContext) -> str:
        resolved_po_line = ""
        if ctx.memory and ctx.memory.resolved_po_number is not None:
            resolved_po_line = (
                f"Note: PO retrieval agent resolved PO number to: "
                f"{ctx.memory.resolved_po_number}\n"
            )
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            + resolved_po_line
            + f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Exceptions: {json.dumps(ctx.exceptions, indent=2, default=str)}\n"
            "\nInvestigate and advise."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return [
            "invoice_details", "po_lookup", "grn_lookup",
            "reconciliation_summary", "exception_list",
        ]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 8. Compliance Agent
# ============================================================================
class ComplianceAgent(BaseAgent):
    """Assesses invoice compliance: duplicate detection, tax validation, vendor approval,
    mandatory field completeness, fraud indicators, and amount-threshold policies.

    Runs AFTER ExceptionAnalysisAgent and BEFORE ReviewRoutingAgent so that
    compliance flags can influence routing priority and escalation decisions.

    Trigger conditions (set by PolicyEngine):
      - DUPLICATE_INVOICE exception present
      - TAX_MISMATCH exception present
      - VENDOR_MISMATCH exception present
      - MISSING_MANDATORY_FIELDS exception present
      - Invoice amount exceeds the high-value review threshold

    Output always includes ``compliance_status`` (PASS/FAIL/PARTIAL) and
    ``compliance_flags`` (list of triggered compliance rules) in ``evidence``.
    """

    agent_type = AgentType.COMPLIANCE_AGENT

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.compliance_check")

    def build_user_message(self, ctx: AgentContext) -> str:
        # Summarise any prior agent analysis from memory so the compliance
        # agent benefits from exception analysis context.
        prior_summary = ""
        if ctx.memory and ctx.memory.agent_summaries:
            prior_texts = [
                f"[{a}] {s}"
                for a, s in ctx.memory.agent_summaries.items()
                if s
            ]
            if prior_texts:
                prior_summary = "Prior agent analysis:\n" + "\n".join(prior_texts) + "\n\n"

        exc_summary = json.dumps(ctx.exceptions, indent=2, default=str) if ctx.exceptions else "None"
        return (
            _mode_context(ctx)
            + f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Extraction Confidence: {ctx.reconciliation_result.extraction_confidence}\n"
            f"Exceptions ({len(ctx.exceptions)}):\n{exc_summary}\n\n"
            + prior_summary
            + "Review this invoice for compliance violations. "
            "Use tools to retrieve invoice details, vendor status, and exception data. "
            "Produce a structured compliance assessment with compliance_status and "
            "compliance_flags in your evidence output."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return [
            "invoice_details",
            "vendor_search",
            "po_lookup",
            "exception_list",
            "reconciliation_summary",
        ]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)

        # Validate recommendation_type.
        valid_rec_types = {rt.value for rt in RecommendationType}
        rec_type = data.get("recommendation_type")
        if rec_type is not None and rec_type not in valid_rec_types:
            # Compliance failures default to escalation.
            data["recommendation_type"] = RecommendationType.ESCALATE_TO_MANAGER.value
            data["confidence"] = min(data.get("confidence") or 0.0, 0.6)

        # Clamp confidence to [0.0, 1.0].
        try:
            data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            data["confidence"] = 0.0

        # Ensure compliance_status is present in evidence (default to PARTIAL on missing).
        evidence = data.get("evidence") or {}
        if "compliance_status" not in evidence:
            evidence["compliance_status"] = "PARTIAL"
            data["evidence"] = evidence

        output = _to_agent_output(data, content)

        # Upgrade recommendation to ESCALATE_TO_MANAGER when compliance fails.
        compliance_status = (data.get("evidence") or {}).get("compliance_status", "")
        if compliance_status == "FAIL" and output.recommendation_type not in (
            RecommendationType.ESCALATE_TO_MANAGER,
        ):
            output.recommendation_type = RecommendationType.ESCALATE_TO_MANAGER.value
            output.confidence = max(output.confidence, 0.8)

        return output


# ============================================================================
# Agent class registry
# ============================================================================

# Lazy import system agent classes to avoid circular dependency
def _get_system_agent_classes():
    from apps.agents.services.system_agent_classes import (
        SystemBulkExtractionIntakeAgent,
        SystemCaseIntakeAgent,
        SystemCaseSummaryAgent,
        SystemPostingPreparationAgent,
        SystemReviewRoutingAgent,
    )
    return {
        AgentType.SYSTEM_REVIEW_ROUTING: SystemReviewRoutingAgent,
        AgentType.SYSTEM_CASE_SUMMARY: SystemCaseSummaryAgent,
        AgentType.SYSTEM_BULK_EXTRACTION_INTAKE: SystemBulkExtractionIntakeAgent,
        AgentType.SYSTEM_CASE_INTAKE: SystemCaseIntakeAgent,
        AgentType.SYSTEM_POSTING_PREPARATION: SystemPostingPreparationAgent,
    }


AGENT_CLASS_REGISTRY: Dict[str, type] = {
    AgentType.INVOICE_EXTRACTION: InvoiceExtractionAgent,
    AgentType.EXCEPTION_ANALYSIS: ExceptionAnalysisAgent,
    AgentType.INVOICE_UNDERSTANDING: InvoiceUnderstandingAgent,
    AgentType.PO_RETRIEVAL: PORetrievalAgent,
    AgentType.GRN_RETRIEVAL: GRNRetrievalAgent,
    AgentType.COMPLIANCE_AGENT: ComplianceAgent,
    AgentType.REVIEW_ROUTING: ReviewRoutingAgent,
    AgentType.CASE_SUMMARY: CaseSummaryAgent,
    AgentType.RECONCILIATION_ASSIST: ReconciliationAssistAgent,
}

# Merge system agent classes (done at import time)
try:
    AGENT_CLASS_REGISTRY.update(_get_system_agent_classes())
except Exception:  # pragma: no cover
    # Tolerate import errors during migrations / early app loading
    pass
