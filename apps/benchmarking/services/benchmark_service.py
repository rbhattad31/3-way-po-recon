"""
Benchmark orchestration service.

Orchestrates the full benchmarking pipeline following documented flow:
  1. Azure DI Extractor (document extraction)
    2. Line Item Understanding Agent (LLM normalization and cleanup)
    3. Market Data Analyzer (market intelligence)
    4. Benchmarking Analyst (analysis synthesis)
    5. Compliance Agent (compliance validation)
    6. Decision Maker (source selection per line)
    7. Vendor Recommendation (vendor ranking)

Each agent is invoked sequentially with prior stage context.
"""
import logging
import json
from decimal import Decimal
from collections import defaultdict
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.auditlog.services import AuditService
from apps.core.trace import TraceContext

from apps.benchmarking.models import (
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    BenchmarkResult,
    VarianceThresholdConfig,
    VarianceStatus,
)

logger = logging.getLogger(__name__)


class BenchmarkEngine:
    """Orchestrates benchmarking pipeline via agent coordination."""

    @staticmethod
    def _to_decimal(value) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @classmethod
    def _is_market_benchmark_plausible(
        cls,
        *,
        line_item,
        benchmark_min: Optional[Decimal],
        benchmark_mid: Decimal,
        benchmark_max: Optional[Decimal],
    ) -> bool:
        if benchmark_mid <= 0:
            return False

        max_unit_price = cls._to_decimal(getattr(settings, "BENCHMARKING_MARKET_MAX_UNIT_PRICE", "1000000")) or Decimal("1000000")
        if benchmark_mid > max_unit_price:
            return False

        if benchmark_min is not None and benchmark_min <= 0:
            return False
        if benchmark_max is not None and benchmark_max <= 0:
            return False
        if benchmark_min is not None and benchmark_max is not None and benchmark_max < benchmark_min:
            return False

        max_range_ratio = cls._to_decimal(getattr(settings, "BENCHMARKING_MARKET_MAX_RANGE_RATIO", "25")) or Decimal("25")
        if benchmark_min is not None and benchmark_max is not None and benchmark_min > 0:
            try:
                if (benchmark_max / benchmark_min) > max_range_ratio:
                    return False
            except Exception:
                return False

        anchors = []
        quoted_rate = cls._to_decimal(getattr(line_item, "quoted_unit_rate", None))
        if quoted_rate is not None and quoted_rate > 0:
            anchors.append(quoted_rate)

        existing_benchmark = cls._to_decimal(getattr(line_item, "benchmark_mid", None))
        if existing_benchmark is not None and existing_benchmark > 0:
            anchors.append(existing_benchmark)

        line_amount = cls._to_decimal(getattr(line_item, "line_amount", None))
        quantity = cls._to_decimal(getattr(line_item, "quantity", None))
        if line_amount is not None and quantity is not None and quantity > 0:
            implied_rate = line_amount / quantity
            if implied_rate > 0:
                anchors.append(implied_rate)

        max_anchor_ratio = cls._to_decimal(getattr(settings, "BENCHMARKING_MARKET_MAX_ANCHOR_RATIO", "50")) or Decimal("50")
        min_anchor_ratio = cls._to_decimal(getattr(settings, "BENCHMARKING_MARKET_MIN_ANCHOR_RATIO", "0.02")) or Decimal("0.02")

        for anchor in anchors:
            if anchor <= 0:
                continue
            try:
                ratio = benchmark_mid / anchor
            except Exception:
                return False
            if ratio > max_anchor_ratio or ratio < min_anchor_ratio:
                return False

        return True

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
    def _get_cached_stage_output(*, bench_request: BenchmarkRequest, stage_name: str) -> Optional[dict]:
        try:
            from apps.agents.models import AgentRun

            run = (
                AgentRun.objects.filter(
                    input_payload__benchmark_request_pk=bench_request.pk,
                    input_payload__agent_stage=stage_name,
                    status="COMPLETED",
                )
                .order_by("-started_at", "-pk")
                .first()
            )
            if not run:
                return None
            payload = run.output_payload or {}
            return payload if isinstance(payload, dict) else None
        except Exception:
            logger.debug("BenchmarkEngine: unable to read cached stage output", exc_info=True)
            return None

    @classmethod
    def _resolve_variance_thresholds(cls, category: Optional[str] = None, geography: Optional[str] = None) -> tuple[float, float]:
        """Resolve variance thresholds from DB.

        Priority:
          1. category + geography
          2. category + ALL geography
          3. ALL + ALL global rule

        Falls back to the client-approved global baseline from the configuration
        PDF when DB records are unavailable.
        """
        category = str(category or "ALL").strip() or "ALL"
        geography = str(geography or "ALL").strip().upper() or "ALL"

        candidates = [
            {"category__iexact": category, "geography": geography, "is_active": True},
            {"category__iexact": category, "geography": "ALL", "is_active": True},
            {"category": "ALL", "geography": "ALL", "is_active": True},
        ]
        try:
            for filters in candidates:
                rows = list(
                    VarianceThresholdConfig.objects
                    .filter(**filters)
                    .order_by("pk")
                )
                if not rows:
                    continue

                optimal_row = next((row for row in rows if row.variance_status == VarianceStatus.WITHIN_RANGE), None)
                moderate_row = next((row for row in rows if row.variance_status == VarianceStatus.MODERATE), None)
                if optimal_row and moderate_row:
                    return float(optimal_row.moderate_max_pct), float(moderate_row.moderate_max_pct)

                legacy_global = next((row for row in rows if row.category == "ALL" and row.geography == "ALL"), None)
                if legacy_global:
                    return float(legacy_global.within_range_max_pct), float(legacy_global.moderate_max_pct)
        except Exception:
            logger.exception("BenchmarkEngine: failed to resolve variance thresholds from DB")

        return 5.0, 15.0

    @classmethod
    def _persist_result_snapshot(cls, *, bench_request: BenchmarkRequest) -> Optional[BenchmarkResult]:
        line_items = list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            ).select_related("quotation")
        )
        if not line_items:
            return None

        totals_by_category = defaultdict(lambda: {"quoted": 0.0, "benchmark": 0.0, "line_count": 0})
        total_quoted = 0.0
        total_quoted_benchmark_covered = 0.0
        total_benchmark_mid = 0.0
        counts = {
            "WITHIN_RANGE": 0,
            "MODERATE": 0,
            "HIGH": 0,
            "NEEDS_REVIEW": 0,
        }

        for item in line_items:
            quoted_amount = 0.0
            if item.line_amount is not None:
                quoted_amount = float(item.line_amount)
            elif item.quoted_unit_rate is not None and item.quantity is not None:
                quoted_amount = float(item.quoted_unit_rate) * float(item.quantity)

            benchmark_amount = 0.0
            has_benchmark = item.benchmark_mid is not None
            if has_benchmark and item.quantity is not None:
                benchmark_amount = float(item.benchmark_mid) * float(item.quantity)
            elif has_benchmark:
                benchmark_amount = float(item.benchmark_mid)

            total_quoted += quoted_amount
            if has_benchmark:
                total_quoted_benchmark_covered += quoted_amount
                total_benchmark_mid += benchmark_amount

            status_key = (item.variance_status or "NEEDS_REVIEW").upper()
            if status_key not in counts:
                status_key = "NEEDS_REVIEW"
            counts[status_key] += 1

            category_key = (item.category or "UNCATEGORIZED").strip() or "UNCATEGORIZED"
            totals_by_category[category_key]["quoted"] += quoted_amount
            totals_by_category[category_key]["benchmark"] += benchmark_amount
            totals_by_category[category_key]["line_count"] += 1

        overall_deviation_pct = None
        if total_benchmark_mid > 0:
            overall_deviation_pct = round(
                ((total_quoted_benchmark_covered - total_benchmark_mid) / total_benchmark_mid) * 100.0,
                2,
            )

        if counts["HIGH"] > 0:
            overall_status = VarianceStatus.HIGH
        elif counts["MODERATE"] > 0:
            overall_status = VarianceStatus.MODERATE
        elif counts["WITHIN_RANGE"] > 0 and counts["NEEDS_REVIEW"] == 0:
            overall_status = VarianceStatus.WITHIN_RANGE
        else:
            overall_status = VarianceStatus.NEEDS_REVIEW

        category_summary = {}
        for category, bucket in totals_by_category.items():
            cat_quoted = bucket["quoted"]
            cat_benchmark = bucket["benchmark"]
            cat_variance_pct = None
            if cat_benchmark > 0:
                cat_variance_pct = round(((cat_quoted - cat_benchmark) / cat_benchmark) * 100.0, 2)
            category_summary[category] = {
                "quoted": round(cat_quoted, 2),
                "benchmark": round(cat_benchmark, 2),
                "variance_pct": cat_variance_pct,
                "line_count": int(bucket["line_count"]),
            }

        existing_result = BenchmarkResult.objects.filter(request=bench_request).first()
        negotiation_notes = []
        if existing_result and isinstance(existing_result.negotiation_notes_json, list):
            negotiation_notes = [
                str(item).strip()
                for item in existing_result.negotiation_notes_json
                if str(item).strip()
            ][:20]

        result, _ = BenchmarkResult.objects.update_or_create(
            request=bench_request,
            defaults={
                "tenant": getattr(bench_request, "tenant", None),
                "total_quoted": Decimal(str(round(total_quoted, 2))),
                "total_benchmark_mid": Decimal(str(round(total_benchmark_mid, 2))) if total_benchmark_mid > 0 else None,
                "overall_deviation_pct": overall_deviation_pct,
                "overall_status": overall_status,
                "category_summary_json": category_summary,
                "negotiation_notes_json": negotiation_notes,
                "lines_within_range": counts["WITHIN_RANGE"],
                "lines_moderate": counts["MODERATE"],
                "lines_high": counts["HIGH"],
                "lines_needs_review": counts["NEEDS_REVIEW"],
                "is_active": True,
            },
        )
        return result

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
    def run(cls, request_pk: int, user=None, tenant=None, force_reextract: bool = False) -> dict:
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
                force_reextract=force_reextract,
            )

            cls._persist_benchmark_result(
                bench_request=bench_request,
                outputs=outputs,
            )

            final_line_count = int(outputs.get("line_item_count", 0) or 0)
            if final_line_count <= 0:
                raise ValueError(
                    "Benchmarking extraction produced zero line items. "
                    "Please verify quotation quality or Azure DI configuration and reprocess."
                )

            cls._persist_result_snapshot(bench_request=bench_request)

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
                output={
                    "request_pk": bench_request.pk,
                    "status": bench_request.status,
                    "agents_executed": len(outputs),
                    "line_item_count": final_line_count,
                },
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
        force_reextract: bool = False,
    ) -> dict:
        """Execute the documented agent flow sequentially.
        
        CORRECTED FLOW:
          1. Azure DI (extraction) - completed
                    2. Line Item Understanding - normalize/filter DI rows and infer supplier
                    3. Decision Maker - classify & decide: MARKET_DATA vs DB_BENCHMARK per line
                    4. Market Data Analyzer - fetch data based on Decision Maker guidance
                    5. Benchmarking Analyst - analyze with collected data
                    6. Compliance - validate outcomes
                    7. Vendor Recommendation - rank vendors
        """
        from apps.benchmarking.agents import (
            BenchmarkAIAnalyzerAgentBM,
            BenchmarkComplianceAgentBM,
            BenchmarkDecisionMakerAgentBM,
            BenchmarkingAnalystAgentBM,
            BenchmarkLineItemUnderstandingAgentBM,
            BenchmarkMarketDataAnalyzerAgentBM,
            BenchmarkNegotiationTalkingPointsAgentBM,
            BenchmarkVendorRecommendationAgent,
        )

        outputs = {}

        # Stage 1: Extract quotations (Azure DI)
        extraction_output = cls._run_extraction_stage(
            bench_request=bench_request,
            user=user,
            trace_id=trace_id,
            parent_agent_run_id=parent_agent_run_id,
            force_reextract=force_reextract,
        )
        outputs["extraction"] = extraction_output

        # Stage 1.5: Line Item Understanding - cleans arbitrary document extraction output.
        stage_name = "Line_Item_Understanding"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
            llm_model_used=cls._configured_llm_model(),
        )
        try:
            quotations = list(
                BenchmarkQuotation.objects.filter(
                    request=bench_request,
                    is_active=True,
                )
            )
            understanding_output = BenchmarkLineItemUnderstandingAgentBM.understand_request(
                quotations=quotations,
            )
            outputs["line_item_understanding"] = understanding_output
            cls._complete_agent_run(
                stage_run_id,
                confidence=float(understanding_output.get("confidence", 0.8) or 0.8),
                summary=understanding_output.get("summary", "Line item understanding completed"),
                output=understanding_output,
            )
            logger.info("Line Item Understanding: %s", understanding_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Line Item Understanding failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        line_items = list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            )
        )
        vendor_cards = cls._build_vendor_cards(bench_request=bench_request)

        request_context = {
            "benchmark_request_pk": bench_request.pk,
            "geography": bench_request.geography,
            "scope_type": bench_request.scope_type,
            "line_item_count": len(line_items),
        }
        outputs["request_context"] = request_context

        if not line_items:
            logger.warning("BenchmarkEngine: no line items available after extraction stage")
            outputs["line_item_count"] = 0
            return outputs

        # Stage 2: Decision Maker - RUNS SECOND, decides what data to fetch
        stage_name = "Decision_Maker"
        decision_output = {}
        if not force_reextract:
            cached_decision = cls._get_cached_stage_output(bench_request=bench_request, stage_name=stage_name)
            if cached_decision:
                decision_output = cached_decision
                outputs["decision"] = decision_output
                logger.info("Decision Maker: reused cached output for request %s", bench_request.pk)
            else:
                cached_decision = None
        else:
            cached_decision = None

        if cached_decision is None:
            stage_run_id = cls._start_agent_run(
                bench_request,
                user=user,
                trace_id=trace_id,
                invocation_reason=f"{stage_name}:execute",
                parent_run_id=parent_agent_run_id,
                input_payload_extra={"agent_stage": stage_name},
            )
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
                vendor_cards=vendor_cards,
                result=None,
                decision_guidance=decision_output,  # Uses decision output to guide data collection
            )
            outputs["market_data"] = market_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Market analysis completed", output=market_output)
            logger.info("Market Data Analyzer: %s", market_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Market Data Analyzer failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 3.25: Apply market prices for lines routed to MARKET_DATA
        try:
            cls._apply_market_prices(
                line_items=line_items,
                decision_output=decision_output,
                market_output=outputs.get("market_data", {}),
            )
        except Exception as exc:
            logger.exception("Market price application failed (non-fatal): %s", exc)

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

        # Refresh line/vendor state after source routing + benchmark application so
        # downstream stages (analyst/compliance/recommendation/AI) run on final values.
        line_items = list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            )
        )
        vendor_cards = cls._build_vendor_cards(bench_request=bench_request)

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
                vendor_cards=vendor_cards,
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
            vendor_output = BenchmarkVendorRecommendationAgent.recommend(vendor_cards=vendor_cards)
            outputs["vendor_recommendation"] = vendor_output
            cls._complete_agent_run(stage_run_id, confidence=0.8, summary="Vendor recommendation completed", output=vendor_output)
            logger.info("Vendor Recommendation: %s", vendor_output.get("summary", "completed"))
        except Exception as exc:
            logger.exception("Vendor Recommendation failed: %s", exc)
            cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 7: AI Insights Analyzer
        stage_name = "AI_Insights_Analyzer"
        if not force_reextract:
            cached_ai = cls._get_cached_stage_output(bench_request=bench_request, stage_name=stage_name)
            if cached_ai:
                outputs["ai_insights"] = cached_ai
                logger.info("AI Insights Analyzer: reused cached output for request %s", bench_request.pk)
            else:
                cached_ai = None
        else:
            cached_ai = None

        if cached_ai is None:
            stage_run_id = cls._start_agent_run(
                bench_request,
                user=user,
                trace_id=trace_id,
                invocation_reason=f"{stage_name}:execute",
                parent_run_id=parent_agent_run_id,
                input_payload_extra={"agent_stage": stage_name},
                llm_model_used=cls._configured_llm_model(),
            )
            try:
                ai_output = BenchmarkAIAnalyzerAgentBM.analyze(
                    bench_request=bench_request,
                    line_items=line_items,
                    vendor_cards=vendor_cards,
                    decision_output=outputs.get("decision", {}),
                    market_output=outputs.get("market_data", {}),
                    analyst_output=outputs.get("analyst", {}),
                    compliance_output=outputs.get("compliance", {}),
                    vendor_output=outputs.get("vendor_recommendation", {}),
                )
                outputs["ai_insights"] = ai_output
                cls._complete_agent_run(
                    stage_run_id,
                    confidence=float(ai_output.get("confidence", 0.75) or 0.75),
                    summary=ai_output.get("summary", "AI insights generated"),
                    output=ai_output,
                )
                logger.info("AI Insights Analyzer: %s", ai_output.get("summary", "completed"))
            except Exception as exc:
                logger.exception("AI Insights Analyzer failed: %s", exc)
                cls._fail_agent_run(stage_run_id, error=str(exc))

        # Stage 8: Negotiation Talking Points
        stage_name = "Negotiation_Talking_Points"
        if not force_reextract:
            cached_negotiation = cls._get_cached_stage_output(bench_request=bench_request, stage_name=stage_name)
            if cached_negotiation:
                outputs["negotiation_talking_points"] = cached_negotiation
                logger.info("Negotiation Talking Points: reused cached output for request %s", bench_request.pk)
            else:
                cached_negotiation = None
        else:
            cached_negotiation = None

        if cached_negotiation is None:
            stage_run_id = cls._start_agent_run(
                bench_request,
                user=user,
                trace_id=trace_id,
                invocation_reason=f"{stage_name}:execute",
                parent_run_id=parent_agent_run_id,
                input_payload_extra={"agent_stage": stage_name},
                llm_model_used=cls._configured_llm_model(),
            )
            try:
                negotiation_output = BenchmarkNegotiationTalkingPointsAgentBM.generate(
                    bench_request=bench_request,
                    line_items=line_items,
                    vendor_cards=vendor_cards,
                    ai_output=outputs.get("ai_insights", {}),
                    compliance_output=outputs.get("compliance", {}),
                    vendor_output=outputs.get("vendor_recommendation", {}),
                )
                outputs["negotiation_talking_points"] = negotiation_output
                cls._complete_agent_run(
                    stage_run_id,
                    confidence=float(negotiation_output.get("confidence", 0.75) or 0.75),
                    summary=negotiation_output.get("summary", "Negotiation talking points generated"),
                    output=negotiation_output,
                )
                logger.info("Negotiation Talking Points: %s", negotiation_output.get("summary", "completed"))
            except Exception as exc:
                logger.exception("Negotiation Talking Points failed: %s", exc)
                cls._fail_agent_run(stage_run_id, error=str(exc))

        outputs["line_item_count"] = len(line_items)
        logger.info("BenchmarkEngine: Pipeline completed with %d agent stages", len(outputs))
        return outputs

    @classmethod
    def _sanitise_insight_text(cls, value: str) -> str:
        text = str(value or "")
        text = text.encode("ascii", "ignore").decode("ascii")
        return text.strip()

    @classmethod
    def _derive_variance_status(cls, deviation_pct: Optional[float], benchmark_total: Decimal) -> str:
        if benchmark_total <= 0 or deviation_pct is None:
            return "NEEDS_REVIEW"
        abs_dev = abs(float(deviation_pct))
        within_range_max_pct, moderate_max_pct = cls._resolve_variance_thresholds("ALL", "ALL")
        if abs_dev <= within_range_max_pct:
            return "WITHIN_RANGE"
        if abs_dev <= moderate_max_pct:
            return "MODERATE"
        return "HIGH"

    @classmethod
    def _build_category_summary(cls, line_items: list) -> dict:
        category_totals = {}
        for line_item in line_items:
            category = (line_item.category or "UNCATEGORIZED").strip() or "UNCATEGORIZED"
            bucket = category_totals.setdefault(
                category,
                {
                    "quoted": Decimal("0"),
                    "benchmark": Decimal("0"),
                    "line_count": 0,
                    "benchmarked_line_count": 0,
                },
            )
            quoted = Decimal(str(line_item.line_amount or "0"))
            benchmark_mid = line_item.benchmark_mid
            qty = line_item.quantity

            bucket["quoted"] += quoted
            bucket["line_count"] += 1

            if benchmark_mid is not None:
                benchmark_component = Decimal(str(benchmark_mid))
                if qty is not None:
                    benchmark_component = benchmark_component * Decimal(str(qty))
                bucket["benchmark"] += benchmark_component
                bucket["benchmarked_line_count"] += 1

        summary = {}
        for category, bucket in category_totals.items():
            quoted = bucket["quoted"]
            benchmark = bucket["benchmark"]
            variance_pct = None
            if benchmark > 0:
                variance_pct = round(((quoted - benchmark) / benchmark) * Decimal("100"), 2)
            summary[category] = {
                "quoted": float(round(quoted, 2)),
                "benchmark": float(round(benchmark, 2)),
                "variance_pct": float(variance_pct) if variance_pct is not None else None,
                "line_count": bucket["line_count"],
                "benchmarked_line_count": bucket["benchmarked_line_count"],
            }
        return summary

    @classmethod
    def _persist_benchmark_result(cls, *, bench_request: BenchmarkRequest, outputs: dict) -> Optional[BenchmarkResult]:
        line_items = list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            )
        )
        if not line_items:
            return None

        total_quoted = Decimal("0")
        total_benchmark_mid = Decimal("0")
        lines_within_range = 0
        lines_moderate = 0
        lines_high = 0
        lines_needs_review = 0

        for line_item in line_items:
            line_amount = Decimal(str(line_item.line_amount or "0"))
            total_quoted += line_amount

            benchmark_mid = line_item.benchmark_mid
            qty = line_item.quantity
            if benchmark_mid is not None:
                benchmark_component = Decimal(str(benchmark_mid))
                if qty is not None:
                    benchmark_component = benchmark_component * Decimal(str(qty))
                total_benchmark_mid += benchmark_component

            status = (line_item.variance_status or "NEEDS_REVIEW").strip().upper()
            if status == "WITHIN_RANGE":
                lines_within_range += 1
            elif status == "MODERATE":
                lines_moderate += 1
            elif status == "HIGH":
                lines_high += 1
            else:
                lines_needs_review += 1

        overall_deviation_pct = None
        if total_benchmark_mid > 0:
            overall_deviation_pct = float(
                round(((total_quoted - total_benchmark_mid) / total_benchmark_mid) * Decimal("100"), 2)
            )

        negotiation_payload = outputs.get("negotiation_talking_points") or {}
        raw_insights = negotiation_payload.get("talking_points") or []
        if not raw_insights:
            fallback_summary = (
                negotiation_payload.get("summary")
                or outputs.get("vendor_recommendation", {}).get("summary")
                or outputs.get("analyst", {}).get("summary")
                or ""
            )
            if fallback_summary:
                raw_insights = [fallback_summary]
        negotiation_notes = [
            cls._sanitise_insight_text(item)
            for item in raw_insights
            if cls._sanitise_insight_text(item)
        ][:20]

        category_summary = cls._build_category_summary(line_items)

        defaults = {
            "tenant": getattr(bench_request, "tenant", None),
            "total_quoted": round(total_quoted, 2),
            "total_benchmark_mid": round(total_benchmark_mid, 2),
            "overall_deviation_pct": overall_deviation_pct,
            "overall_status": cls._derive_variance_status(overall_deviation_pct, total_benchmark_mid),
            "category_summary_json": category_summary,
            "negotiation_notes_json": negotiation_notes,
            "lines_within_range": lines_within_range,
            "lines_moderate": lines_moderate,
            "lines_high": lines_high,
            "lines_needs_review": lines_needs_review,
            "is_active": True,
        }

        result, _ = BenchmarkResult.objects.update_or_create(
            request=bench_request,
            defaults=defaults,
        )
        return result

    @classmethod
    def _run_extraction_stage(
        cls,
        *,
        bench_request: BenchmarkRequest,
        user,
        trace_id: str,
        parent_agent_run_id: Optional[int],
        force_reextract: bool = False,
    ) -> dict:
        """Run Azure DI extraction for all active quotations in a request."""
        stage_name = "Azure_DI_Extraction"
        stage_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=trace_id,
            invocation_reason=f"{stage_name}:execute",
            parent_run_id=parent_agent_run_id,
            input_payload_extra={"agent_stage": stage_name},
        )

        try:
            from apps.benchmarking.agents.Azure_Document_Intelligence_Agent_BM import (
                AzureDocumentIntelligenceAgentBM,
            )

            quotations = list(
                BenchmarkQuotation.objects.filter(
                    request=bench_request,
                    is_active=True,
                )
            )

            outputs = []
            total_lines = 0
            success_count = 0
            for quotation in quotations:
                # Re-extract when status is not DONE or when line items are missing.
                active_line_count = quotation.line_items.filter(is_active=True).count()
                if (not force_reextract) and quotation.extraction_status == "DONE" and active_line_count > 0:
                    outputs.append(
                        {
                            "quotation_id": quotation.pk,
                            "skipped": True,
                            "line_count": active_line_count,
                            "status": "DONE",
                        }
                    )
                    total_lines += active_line_count
                    success_count += 1
                    continue

                result = AzureDocumentIntelligenceAgentBM.extract_quotation(quotation=quotation)
                line_count = int(result.get("line_count", 0) or 0)
                total_lines += line_count
                if result.get("success"):
                    success_count += 1
                outputs.append(
                    {
                        "quotation_id": quotation.pk,
                        "skipped": False,
                        "line_count": line_count,
                        "status": quotation.extraction_status,
                        "error": result.get("error", ""),
                    }
                )

            stage_output = {
                "total_quotations": len(quotations),
                "successful_quotations": success_count,
                "line_item_count": total_lines,
                "details": outputs,
            }

            confidence = 0.9 if total_lines > 0 else 0.3
            summary = (
                f"Azure DI extraction processed {len(quotations)} quotation(s); "
                f"success={success_count}, lines={total_lines}."
            )
            cls._complete_agent_run(
                stage_run_id,
                confidence=confidence,
                summary=summary,
                output=stage_output,
            )
            return stage_output
        except Exception as exc:
            cls._fail_agent_run(stage_run_id, error=str(exc))
            logger.exception("BenchmarkEngine extraction stage failed: %s", exc)
            return {
                "total_quotations": 0,
                "successful_quotations": 0,
                "line_item_count": 0,
                "details": [],
                "error": str(exc),
            }

    @classmethod
    def _build_vendor_cards(cls, *, bench_request: BenchmarkRequest) -> list:
        """Build vendor cards from active quotation line items."""
        vendor_cards = []
        quotations = list(
            BenchmarkQuotation.objects.filter(
                request=bench_request,
                is_active=True,
            ).prefetch_related("line_items")
        )

        for quotation in quotations:
            q_items = list(quotation.line_items.filter(is_active=True))
            q_total = 0.0
            q_bench = 0.0
            q_total_bench_covered = 0.0
            benchmarked_line_count = 0
            status_counts = {
                "WITHIN_RANGE": 0,
                "MODERATE": 0,
                "HIGH": 0,
                "NEEDS_REVIEW": 0,
            }
            live_reference_count = 0

            for line in q_items:
                q_total += float(line.line_amount or 0)
                if line.benchmark_mid is not None and line.quantity is not None:
                    q_bench += float(line.benchmark_mid) * float(line.quantity)
                    q_total_bench_covered += float(line.line_amount or 0)
                    benchmarked_line_count += 1
                elif line.benchmark_mid is not None:
                    q_bench += float(line.benchmark_mid)
                    q_total_bench_covered += float(line.line_amount or 0)
                    benchmarked_line_count += 1
                status_counts[line.variance_status] = status_counts.get(line.variance_status, 0) + 1
                live_reference_count += len((line.live_price_json or {}).get("citations", []) or [])

            q_dev = None
            if q_bench > 0:
                q_dev = ((q_total_bench_covered - q_bench) / q_bench) * 100

            q_status = "NEEDS_REVIEW"
            if q_dev is not None:
                if abs(q_dev) < 5:
                    q_status = "WITHIN_RANGE"
                elif abs(q_dev) < 15:
                    q_status = "MODERATE"
                else:
                    q_status = "HIGH"

            vendor_cards.append(
                {
                    "quotation_id": quotation.pk,
                    "supplier_name": quotation.supplier_name or "Unnamed Vendor",
                    "quotation_ref": quotation.quotation_ref,
                    "line_items": q_items,
                    "line_count": len(q_items),
                    "benchmarked_line_count": benchmarked_line_count,
                    "total_quoted": q_total,
                    "total_benchmark": q_bench if q_bench > 0 else None,
                    "total_quoted_benchmark_covered": q_total_bench_covered if q_bench > 0 else None,
                    "deviation_pct": q_dev,
                    "status": q_status,
                    "status_counts": status_counts,
                    "live_reference_count": live_reference_count,
                }
            )

        return vendor_cards

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
            classification_confidence = float(decision.get("classification_confidence", 0.0) or 0.0)

            classification_fields_to_update = []
            if category and category != line_item.category:
                line_item.category = category
                classification_fields_to_update.append("category")
            if classification_confidence and line_item.classification_confidence != classification_confidence:
                line_item.classification_confidence = classification_confidence
                classification_fields_to_update.append("classification_confidence")
            if line_item.classification_source != "KEYWORD":
                line_item.classification_source = "KEYWORD"
                classification_fields_to_update.append("classification_source")

            if source == "MARKET_DATA":
                if line_item.benchmark_source == "PERPLEXITY_LIVE":
                    if classification_fields_to_update:
                        line_item.save(update_fields=classification_fields_to_update + ["updated_at"])
                    continue

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
                if line_item.benchmark_source != "NONE":
                    line_item.benchmark_source = "NONE"
                    fields_to_reset.append("benchmark_source")
                if line_item.variance_pct is not None:
                    line_item.variance_pct = None
                    fields_to_reset.append("variance_pct")
                if line_item.variance_status != "NEEDS_REVIEW":
                    line_item.variance_status = "NEEDS_REVIEW"
                    fields_to_reset.append("variance_status")
                fields_to_reset.extend(classification_fields_to_update)
                if fields_to_reset:
                    line_item.save(update_fields=fields_to_reset + ["updated_at"])
                continue
            
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
                fields_to_reset.extend(classification_fields_to_update)

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
                    within_range_max_pct, moderate_max_pct = cls._resolve_variance_thresholds(
                        line_item.category,
                        getattr(line_item.quotation.request, "geography", "ALL"),
                    )
                    if abs_variance <= within_range_max_pct:
                        line_item.variance_status = "WITHIN_RANGE"
                    elif abs_variance <= moderate_max_pct:
                        line_item.variance_status = "MODERATE"
                    else:
                        line_item.variance_status = "HIGH"
                
                line_item.save(update_fields=[
                    "benchmark_min", "benchmark_mid", "benchmark_max",
                    "corridor_rule_code", "benchmark_source",
                    "variance_pct", "variance_status",
                    "category", "classification_confidence", "classification_source",
                    "updated_at"
                ])
                logger.info(
                    f"Applied corridor {corridor.rule_code} to line {line_item.line_number}: "
                    f"min={corridor.min_rate}, mid={corridor.mid_rate}, max={corridor.max_rate}"
                )
            elif classification_fields_to_update:
                line_item.save(update_fields=classification_fields_to_update + ["updated_at"])

    @classmethod
    def _apply_market_prices(
        cls,
        *,
        line_items: list,
        decision_output: dict,
        market_output: dict,
    ) -> None:
        line_decisions = decision_output.get("line_decisions", [])
        decisions_by_line_pk = {
            int(d.get("line_pk")): d
            for d in line_decisions
            if d.get("line_pk") is not None
        }
        decisions_by_line_number = {
            int(d.get("line_number")): d
            for d in line_decisions
            if d.get("line_number") is not None
        }
        market_line_ids = {
            int(d.get("line_pk"))
            for d in line_decisions
            if (
                d.get("line_pk") is not None
                and (
                    d.get("source") == "MARKET_DATA"
                    or bool(d.get("hybrid_use_market", False))
                    or str(d.get("pricing_type") or "").strip().upper() == "HYBRID"
                )
            )
        }

        updates_by_id = {}
        for payload in market_output.get("market_price_updates", []) or []:
            line_pk = payload.get("line_pk")
            if line_pk is None:
                continue
            updates_by_id[int(line_pk)] = payload

        for line_item in line_items:
            if market_line_ids and int(line_item.pk) not in market_line_ids:
                continue

            decision = (
                decisions_by_line_pk.get(int(line_item.pk))
                or decisions_by_line_number.get(int(getattr(line_item, "line_number", 0) or 0))
                or {}
            )
            is_hybrid = bool(decision.get("hybrid_use_market", False)) and str(
                decision.get("pricing_type") or ""
            ).strip().upper() == "HYBRID"

            payload = updates_by_id.get(int(line_item.pk))
            if not payload:
                continue

            benchmark_mid = Decimal(str(payload.get("benchmark_mid") or "0"))
            if benchmark_mid <= 0:
                continue

            benchmark_min = Decimal(str(payload.get("benchmark_min") or benchmark_mid))
            benchmark_max = Decimal(str(payload.get("benchmark_max") or benchmark_mid))

            if not cls._is_market_benchmark_plausible(
                line_item=line_item,
                benchmark_min=benchmark_min,
                benchmark_mid=benchmark_mid,
                benchmark_max=benchmark_max,
            ):
                logger.warning(
                    "BenchmarkEngine: rejected implausible market benchmark for line_item=%s (mid=%s, min=%s, max=%s)",
                    line_item.pk,
                    benchmark_mid,
                    benchmark_min,
                    benchmark_max,
                )
                continue

            corridor_min = line_item.benchmark_min
            corridor_mid = line_item.benchmark_mid
            corridor_max = line_item.benchmark_max
            corridor_rule_code = line_item.corridor_rule_code or ""

            if is_hybrid and corridor_mid is not None:
                corridor_mid_d = Decimal(str(corridor_mid))
                corridor_min_d = Decimal(str(corridor_min if corridor_min is not None else corridor_mid_d))
                corridor_max_d = Decimal(str(corridor_max if corridor_max is not None else corridor_mid_d))

                benchmark_mid = (corridor_mid_d + benchmark_mid) / Decimal("2")
                benchmark_min = min(corridor_min_d, benchmark_min)
                benchmark_max = max(corridor_max_d, benchmark_max)

            line_item.benchmark_min = benchmark_min
            line_item.benchmark_mid = benchmark_mid
            line_item.benchmark_max = benchmark_max
            if is_hybrid and corridor_rule_code:
                line_item.corridor_rule_code = corridor_rule_code
            else:
                line_item.corridor_rule_code = ""
            line_item.benchmark_source = "PERPLEXITY_LIVE"

            if line_item.quoted_unit_rate:
                variance_pct = (
                    (float(line_item.quoted_unit_rate) - float(benchmark_mid))
                    / float(benchmark_mid)
                    * 100.0
                )
                line_item.variance_pct = round(variance_pct, 2)
                abs_variance = abs(variance_pct)
                within_range_max_pct, moderate_max_pct = cls._resolve_variance_thresholds(
                    line_item.category,
                    getattr(line_item.quotation.request, "geography", "ALL"),
                )
                if abs_variance <= within_range_max_pct:
                    line_item.variance_status = "WITHIN_RANGE"
                elif abs_variance <= moderate_max_pct:
                    line_item.variance_status = "MODERATE"
                else:
                    line_item.variance_status = "HIGH"
            else:
                line_item.variance_pct = None
                line_item.variance_status = "NEEDS_REVIEW"

            existing_payload = line_item.live_price_json or {}
            citations = payload.get("citations") or []
            merged_payload = {
                **existing_payload,
                "benchmark_min": float(benchmark_min),
                "benchmark_mid": float(benchmark_mid),
                "benchmark_max": float(benchmark_max),
                "currency": payload.get("currency") or existing_payload.get("currency") or "AED",
                "confidence": payload.get("confidence"),
                "source_note": payload.get("source_note") or existing_payload.get("source_note") or "perplexity_live",
                "citations": citations,
            }
            if is_hybrid:
                merged_payload["hybrid_mode"] = True
                merged_payload["hybrid_components"] = {
                    "corridor": {
                        "benchmark_min": float(corridor_min) if corridor_min is not None else None,
                        "benchmark_mid": float(corridor_mid) if corridor_mid is not None else None,
                        "benchmark_max": float(corridor_max) if corridor_max is not None else None,
                        "corridor_rule_code": corridor_rule_code,
                    },
                    "market": {
                        "benchmark_min": float(payload.get("benchmark_min") or 0.0),
                        "benchmark_mid": float(payload.get("benchmark_mid") or 0.0),
                        "benchmark_max": float(payload.get("benchmark_max") or 0.0),
                        "confidence": float(payload.get("confidence") or 0.0),
                        "citations": citations,
                    },
                    "combined": {
                        "benchmark_min": float(benchmark_min),
                        "benchmark_mid": float(benchmark_mid),
                        "benchmark_max": float(benchmark_max),
                    },
                }
            line_item.live_price_json = merged_payload

            line_item.save(update_fields=[
                "benchmark_min",
                "benchmark_mid",
                "benchmark_max",
                "corridor_rule_code",
                "benchmark_source",
                "variance_pct",
                "variance_status",
                "live_price_json",
                "updated_at",
            ])

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
