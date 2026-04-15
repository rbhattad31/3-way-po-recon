"""
Benchmark orchestration service.

Orchestrates the full benchmarking pipeline following documented flow:
  1. Azure DI Extractor (document extraction)
  2. Market Data Analyzer (market intelligence)
  3. Benchmarking Analyst (analysis synthesis)
  4. Compliance Agent (compliance validation)
  5. Decision Maker (source selection per line)
  6. Vendor Recommendation (vendor ranking)

Each agent is invoked sequentially with prior stage context.
"""
import logging
import json
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.auditlog.services import AuditService
from apps.core.trace import TraceContext

from apps.benchmarking.models import (
    BenchmarkLineItem,
    BenchmarkRequest,
    BenchmarkResult,
    VarianceStatus,
)

logger = logging.getLogger(__name__)


class BenchmarkEngine:
    """Orchestrates benchmarking pipeline via agent coordination."""

    @staticmethod
    def _json_safe(value):
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return value

    @staticmethod
    def _configured_llm_model() -> str:
        deployment = str(getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or "").strip()
        if deployment:
            return deployment
        model = str(getattr(settings, "LLM_MODEL_NAME", "") or "").strip()
        return model or "unknown"

    @staticmethod
    def _start_agent_run(
        bench_request: BenchmarkRequest,
        user=None,
        trace_id: str = "",
        invocation_reason: str = "",
        parent_run_id: Optional[int] = None,
        input_payload_extra: Optional[dict] = None,
        llm_model_used: str = "unknown",
    ) -> Optional[int]:
        """Create AgentRun record for benchmarking stage."""
        try:
            from apps.agents.models import AgentDefinition, AgentRun
            from apps.core.enums import AgentRunStatus, AgentType

            agent_def = AgentDefinition.objects.filter(
                agent_type=AgentType.PROCUREMENT_BENCHMARK,
                enabled=True,
            ).first()

            agent_run = AgentRun.objects.create(
                agent_definition=agent_def,
                tenant=getattr(bench_request, "tenant", None),
                agent_type=AgentType.PROCUREMENT_BENCHMARK,
                status=AgentRunStatus.RUNNING,
                confidence=0.0,
                llm_model_used=llm_model_used or "unknown",
                input_payload={
                    "benchmark_request_pk": bench_request.pk,
                    "geography": bench_request.geography,
                    "scope_type": bench_request.scope_type,
                    **(input_payload_extra or {}),
                },
                trace_id=trace_id or "",
                invocation_reason=invocation_reason or f"Benchmarking pipeline run for request {bench_request.pk}",
                actor_user_id=getattr(user, "pk", None) if user is not None else None,
                actor_primary_role=(getattr(user, "role", "") or "USER") if user is not None else "SYSTEM_AGENT",
                access_granted=True,
                started_at=timezone.now(),
                parent_run_id=parent_run_id,
            )
            return agent_run.pk
        except Exception:
            logger.debug("BenchmarkEngine: unable to create AgentRun mirror (non-fatal)", exc_info=True)
            return None

    @staticmethod
    def _complete_agent_run(agent_run_id: Optional[int], *, confidence: float, summary: str, output: dict) -> None:
        """Update AgentRun with completion status and output."""
        if not agent_run_id:
            return
        try:
            from apps.agents.models import AgentRun
            from apps.core.enums import AgentRunStatus
            from apps.agents.services.base_agent import BaseAgent

            completed_at = timezone.now()
            run = AgentRun.objects.filter(pk=agent_run_id).first()
            duration_ms = None
            if run and run.started_at:
                duration_ms = max(0, int((completed_at - run.started_at).total_seconds() * 1000))

            AgentRun.objects.filter(pk=agent_run_id).update(
                status=AgentRunStatus.COMPLETED,
                confidence=max(0.0, min(1.0, float(confidence))),
                summarized_reasoning=BaseAgent._sanitise_text(summary)[:2000],
                output_payload=BenchmarkEngine._json_safe(output),
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.debug("BenchmarkEngine: unable to complete AgentRun mirror (non-fatal)", exc_info=True)

    @staticmethod
    def _fail_agent_run(agent_run_id: Optional[int], *, error: str) -> None:
        """Mark AgentRun as failed."""
        if not agent_run_id:
            return
        try:
            from apps.agents.models import AgentRun
            from apps.core.enums import AgentRunStatus

            completed_at = timezone.now()
            run = AgentRun.objects.filter(pk=agent_run_id).first()
            duration_ms = None
            if run and run.started_at:
                duration_ms = max(0, int((completed_at - run.started_at).total_seconds() * 1000))

            AgentRun.objects.filter(pk=agent_run_id).update(
                status=AgentRunStatus.FAILED,
                confidence=0.0,
                error_message=str(error)[:2000],
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.debug("BenchmarkEngine: unable to fail AgentRun mirror (non-fatal)", exc_info=True)

    @classmethod
    def run(cls, request_pk: int, user=None, tenant=None) -> dict:
        """
        Orchestrate full benchmarking pipeline via documented agent flow.
        
        Flow:
          1. Extract quotation documents (Azure DI)
          2. Analyze market data
          3. Synthesize benchmarking analysis
          4. Validate compliance
          5. Make source/classification decisions
          6. Generate vendor recommendations
        """
        try:
            req_qs = BenchmarkRequest.objects
            if tenant is not None:
                req_qs = req_qs.filter(tenant=tenant)
            bench_request = req_qs.get(pk=request_pk)
        except BenchmarkRequest.DoesNotExist:
            return {"success": False, "error": f"BenchmarkRequest {request_pk} not found"}

        trace_ctx = TraceContext.get_current()
        bench_request.status = "PROCESSING"
        bench_request.error_message = ""
        bench_request.save(update_fields=["status", "error_message", "updated_at"])

        agent_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=getattr(trace_ctx, "trace_id", "") if trace_ctx else "",
        )

        try:
            # Run orchestration via agents (minimal implementation)
            # Each agent executes its deterministic logic; agents are responsible for their own tasks
            outputs = cls._run_agent_pipeline(
                bench_request=bench_request,
                user=user,
                trace_id=getattr(trace_ctx, "trace_id", "") if trace_ctx else "",
                parent_agent_run_id=agent_run_id,
            )

            bench_request.refresh_from_db()
            bench_request.status = "COMPLETED"
            bench_request.save(update_fields=["status", "updated_at"])

            try:
                from apps.benchmarking.services.eval_adapter import BenchmarkingEvalAdapter
                BenchmarkingEvalAdapter.sync_for_request(
                    bench_request,
                    trace_id=getattr(trace_ctx, "trace_id", "") if trace_ctx else "",
                )
            except Exception:
                logger.debug("BenchmarkEngine.run: eval adapter failed (non-fatal)", exc_info=True)

            cls._complete_agent_run(
                agent_run_id,
                confidence=0.8,
                summary=f"Benchmarking pipeline completed for request {bench_request.pk}",
                output={"request_pk": bench_request.pk, "status": bench_request.status, "agents_executed": len(outputs)},
            )

            return {"success": True, "error": None}

        except Exception as exc:
            logger.exception("BenchmarkEngine.run failed for request %s", request_pk)
            bench_request.status = "FAILED"
            bench_request.error_message = str(exc)
            bench_request.save(update_fields=["status", "error_message", "updated_at"])
            cls._fail_agent_run(agent_run_id, error=str(exc))
            return {"success": False, "error": str(exc)}

    @classmethod
    def _run_agent_pipeline(
        cls,
        *,
        bench_request: BenchmarkRequest,
        user,
        trace_id: str,
        parent_agent_run_id: Optional[int],
    ) -> dict:
        """Execute the documented agent flow sequentially.
        
        CORRECTED FLOW:
          1. Azure DI (extraction) - completed
          2. Decision Maker - classify & decide: MARKET_DATA vs DB_BENCHMARK per line
          3. Market Data Analyzer - fetch data based on Decision Maker guidance
          4. Benchmarking Analyst - analyze with collected data
          5. Compliance - validate outcomes
          6. Vendor Recommendation - rank vendors
        """
        from apps.benchmarking.agents import (
            BenchmarkComplianceAgentBM,
            BenchmarkDecisionMakerAgentBM,
            BenchmarkingAnalystAgentBM,
            BenchmarkMarketDataAnalyzerAgentBM,
            BenchmarkVendorRecommendationAgent,
        )

        outputs = {}
        line_items = list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            )
        )

        request_context = {
            "benchmark_request_pk": bench_request.pk,
            "geography": bench_request.geography,
            "scope_type": bench_request.scope_type,
            "line_item_count": len(line_items),
        }

        # Stage 1: Extract quotations (Azure DI) - assumed completed in _process_request
        logger.info("BenchmarkEngine: Stage 1 (Azure DI extraction) - assumed completed")

        # Stage 2: Decision Maker - RUNS SECOND, decides what data to fetch
        stage_name = "Decision_Maker"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
        )
        decision_output = {}
        try:
            decision_output = BenchmarkDecisionMakerAgentBM.decide_for_line_items(
                line_items=line_items,
                geography=bench_request.geography,
                scope_type=bench_request.scope_type,
            )
            outputs["decision"] = decision_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Decision making completed", output=decision_output)
            logger.info("Decision Maker: %s", decision_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Decision Maker failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 3: Market Data Analyzer - USES Decision Maker output to guide data fetching
        stage_name = "Market_Data_Analyzer"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
        )
        try:
            # Pass Decision Maker output to Market Data Analyzer so it knows what to fetch
            market_output = BenchmarkMarketDataAnalyzerAgentBM.analyze(
                line_items=line_items,
                vendor_cards=[],
                result=None,
                decision_guidance=decision_output,  # Uses decision output to guide data collection
            )
            outputs["market_data"] = market_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Market analysis completed", output=market_output)
            logger.info("Market Data Analyzer: %s", market_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Market Data Analyzer failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 3.5: Apply Benchmark Corridor Rules to Lines
        # (based on Decision Maker's routing)
        try:
            cls._apply_benchmark_corridors(
                line_items=line_items,
                decision_output=decision_output,
                geography=bench_request.geography,
                scope_type=bench_request.scope_type,
            )
            logger.info("Corridor rates applied to line items based on Decision Maker routing")
        except Exception as exc:
            logger.exception("Corridor application failed (non-fatal): %s", exc)

        # Stage 4: Benchmarking Analyst
        stage_name = "Benchmarking_Analyst"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
        )
        try:
            analyst_output = BenchmarkingAnalystAgentBM.summarize(
                result=None,
                market_analysis=outputs.get("market_data", {}),
                compliance_assessment={},
                vendor_recommendation={},
                vendor_cards=[],
            )
            outputs["analyst"] = analyst_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Analyst synthesis completed", output=analyst_output)
            logger.info("Benchmarking Analyst: %s", analyst_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Benchmarking Analyst failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 5: Compliance Agent
        stage_name = "Compliance_Agent"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
        )
        try:
            compliance_output = BenchmarkComplianceAgentBM.evaluate(
                result=None,
                line_items=line_items,
            )
            outputs["compliance"] = compliance_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Compliance check completed", output=compliance_output)
            logger.info("Compliance Agent: %s", compliance_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Compliance Agent failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 6: Vendor Recommendation
        stage_name = "Vendor_Recommendation"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
        )
        try:
            vendor_output = BenchmarkVendorRecommendationAgent.recommend(vendor_cards=[])
            outputs["vendor_recommendation"] = vendor_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Vendor recommendation completed", output=vendor_output)
            logger.info("Vendor Recommendation: %s", vendor_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Vendor Recommendation failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        logger.info("BenchmarkEngine: Pipeline completed with %d agent stages", len(outputs))
        return outputs

    @classmethod
    def _apply_benchmark_corridors(
        cls,
        *,
        line_items: list,
        decision_output: dict,
        geography: str = "UAE",
        scope_type: str = "SITC",
    ) -> None:
        """Apply benchmark corridor rules to line items based on Decision Maker's routing."""
        from apps.benchmarking.models import BenchmarkCorridorRule
        
        # Extract Decision Maker's routing per line
        line_decisions = decision_output.get("line_decisions", [])
        decisions_by_line_num = {}
        line_num_counts = {}
        for decision in line_decisions:
            line_num = decision.get("line_number")
            line_num_counts[line_num] = line_num_counts.get(line_num, 0) + 1
            if line_num not in decisions_by_line_num:
                decisions_by_line_num[line_num] = decision

        can_use_line_number_lookup = bool(line_decisions) and all(
            line_num not in (None, 0) and count == 1
            for line_num, count in line_num_counts.items()
        )
        
        # For each line, find and apply matching corridor rule
        for idx, line_item in enumerate(line_items):
            if can_use_line_number_lookup:
                decision = decisions_by_line_num.get(line_item.line_number, {})
            else:
                decision = line_decisions[idx] if idx < len(line_decisions) else {}

            source = decision.get("source", "NEEDS_REVIEW")
            category = decision.get("category", "UNCATEGORIZED")
            
            # Only apply corridors for lines routed to DB_BENCHMARK
            if source != "DB_BENCHMARK" or category == "UNCATEGORIZED":
                fields_to_reset = []
                if line_item.benchmark_min is not None:
                    line_item.benchmark_min = None
                    fields_to_reset.append("benchmark_min")
                if line_item.benchmark_mid is not None:
                    line_item.benchmark_mid = None
                    fields_to_reset.append("benchmark_mid")
                if line_item.benchmark_max is not None:
                    line_item.benchmark_max = None
                    fields_to_reset.append("benchmark_max")
                if line_item.corridor_rule_code:
                    line_item.corridor_rule_code = ""
                    fields_to_reset.append("corridor_rule_code")
                if line_item.benchmark_source == "CORRIDOR_DB":
                    line_item.benchmark_source = "NONE"
                    fields_to_reset.append("benchmark_source")
                if line_item.variance_pct is not None:
                    line_item.variance_pct = None
                    fields_to_reset.append("variance_pct")
                if line_item.variance_status != "NEEDS_REVIEW":
                    line_item.variance_status = "NEEDS_REVIEW"
                    fields_to_reset.append("variance_status")

                if fields_to_reset:
                    line_item.save(update_fields=fields_to_reset + ["updated_at"])
                continue
            
            # Find matching corridor rule by category + geography + scope
            corridor = BenchmarkCorridorRule.objects.filter(
                is_active=True,
                category=category,
                scope_type__in=[scope_type, "ALL"],
                geography__in=[geography, "ALL"],
            ).order_by("geography", "scope_type", "-priority").first()
            
            # Apply benchmark rates if found
            if corridor:
                line_item.benchmark_min = corridor.min_rate
                line_item.benchmark_mid = corridor.mid_rate
                line_item.benchmark_max = corridor.max_rate
                line_item.corridor_rule_code = corridor.rule_code
                line_item.benchmark_source = "CORRIDOR_DB"
                
                # Calculate variance
                if line_item.quoted_unit_rate and corridor.mid_rate:
                    variance_pct = (
                        (float(line_item.quoted_unit_rate) - float(corridor.mid_rate))
                        / float(corridor.mid_rate)
                        * 100.0
                    )
                    line_item.variance_pct = round(variance_pct, 2)
                    
                    # Determine variance status
                    abs_variance = abs(variance_pct)
                    if abs_variance <= 5.0:
                        line_item.variance_status = "WITHIN_RANGE"
                    elif abs_variance <= 15.0:
                        line_item.variance_status = "MODERATE"
                    else:
                        line_item.variance_status = "HIGH"
                
                line_item.save(update_fields=[
                    "benchmark_min", "benchmark_mid", "benchmark_max",
                    "corridor_rule_code", "benchmark_source",
                    "variance_pct", "variance_status", "updated_at"
                ])
                logger.info(
                    f"Applied corridor {corridor.rule_code} to line {line_item.line_number}: "
                    f"min={corridor.min_rate}, mid={corridor.mid_rate}, max={corridor.max_rate}"
                )

    @classmethod
    def run_live_enrichment(cls, request_pk: int, user=None, tenant=None) -> dict:
        """
        Enrich benchmarking with live market data.
        
        Returns: {"success": True/False, "enriched": int, "error": str|None}
        """
        try:
            req_qs = BenchmarkRequest.objects
            if tenant is not None:
                req_qs = req_qs.filter(tenant=tenant)
            bench_request = req_qs.get(pk=request_pk)
        except BenchmarkRequest.DoesNotExist:
            return {"success": False, "error": f"BenchmarkRequest {request_pk} not found"}

        try:
            # Placeholder for live enrichment logic
            # In Phase 2, integrate with Perplexity or market data services
            logger.info("BenchmarkEngine: Live enrichment placeholder for request %s", request_pk)
            return {"success": True, "enriched": 0, "error": None}
        except Exception as exc:
            logger.exception("BenchmarkEngine.run_live_enrichment failed")
            return {"success": False, "error": str(exc)}
