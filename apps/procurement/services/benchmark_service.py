"""BenchmarkService -- orchestrates should-cost benchmarking flow.

Phase 1 agentic bridge: BenchmarkAgent calls now route through
ProcurementAgentOrchestrator so every LLM call has standard audit, trace,
and execution records. Variance computation and risk classification remain
deterministic and are unchanged.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db import transaction

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import (
    AnalysisRunStatus,
    BenchmarkRiskLevel,
    ProcurementRequestStatus,
    VarianceStatus,
)
from apps.core.trace import TraceContext
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    BenchmarkResultLine,
    ProcurementRequest,
    QuotationLineItem,
    SupplierQuotation,
)
from apps.procurement.runtime import ProcurementAgentMemory, ProcurementAgentOrchestrator
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.request_service import ProcurementRequestService

logger = logging.getLogger(__name__)

# Variance thresholds for risk classification
RISK_THRESHOLDS = {
    "low": Decimal("5.0"),       # <=5% -> LOW
    "medium": Decimal("15.0"),   # <=15% -> MEDIUM
    "high": Decimal("30.0"),     # <=30% -> HIGH
    # >30% -> CRITICAL
}


class BenchmarkService:
    """Orchestrates should-cost benchmarking.

    Steps:
      1. Resolve benchmark references per line item
         - Deterministic source checked first (Phase 2: catalogue DB lookup)
         - AI fallback via ProcurementAgentOrchestrator if no deterministic data
      2. Compute variance
      3. Classify risk
      4. Persist BenchmarkResult + lines
      5. Update request status
    """

    @staticmethod
    @observed_service("procurement.benchmark.run", audit_event="BENCHMARK_RUN_STARTED")
    def run_benchmark(
        request: ProcurementRequest,
        run: AnalysisRun,
        quotation: SupplierQuotation,
        *,
        use_ai: bool = True,
        request_user: Any = None,
    ) -> BenchmarkResult:
        AnalysisRunService.start_run(run)

        # Shared memory for this run -- benchmark agents write findings here
        memory = ProcurementAgentMemory()

        try:
            line_items = list(quotation.line_items.all())
            if not line_items:
                raise ValueError("Quotation has no line items to benchmark.")

            # Step 1 & 2: Resolve benchmarks and compute variance
            line_results = []
            for item in line_items:
                benchmark_data = BenchmarkService._resolve_benchmark(
                    item,
                    run=run,
                    memory=memory,
                    use_ai=use_ai,
                    request_user=request_user,
                )
                variance = BenchmarkService._compute_variance(item, benchmark_data)
                line_results.append({
                    "item": item,
                    "benchmark": benchmark_data,
                    "variance": variance,
                })

            # Step 3: Aggregate and classify risk
            total_quoted = sum(item.total_amount for item in line_items)
            total_benchmark = sum(
                lr["benchmark"].get("avg", lr["item"].unit_rate) * lr["item"].quantity
                for lr in line_results
            )
            overall_variance_pct = (
                ((total_quoted - total_benchmark) / total_benchmark * 100)
                if total_benchmark else Decimal("0")
            )
            risk_level = BenchmarkService._classify_risk(overall_variance_pct)

            # Step 4: Persist results
            with transaction.atomic():
                benchmark_result = BenchmarkResult.objects.create(
                    run=run,
                    quotation=quotation,
                    total_quoted_amount=total_quoted,
                    total_benchmark_amount=total_benchmark,
                    variance_pct=overall_variance_pct,
                    risk_level=risk_level,
                    summary_json={
                        "line_count": len(line_items),
                        "total_quoted": str(total_quoted),
                        "total_benchmark": str(total_benchmark),
                        "variance_pct": str(overall_variance_pct),
                    },
                )

                benchmark_lines = []
                for lr in line_results:
                    bm = lr["benchmark"]
                    v = lr["variance"]
                    benchmark_lines.append(BenchmarkResultLine(
                        benchmark_result=benchmark_result,
                        quotation_line=lr["item"],
                        benchmark_min=bm.get("min"),
                        benchmark_avg=bm.get("avg"),
                        benchmark_max=bm.get("max"),
                        quoted_value=lr["item"].unit_rate,
                        variance_pct=v["pct"],
                        variance_status=v["status"],
                        remarks=v.get("remarks", ""),
                    ))
                BenchmarkResultLine.objects.bulk_create(benchmark_lines)

            # Step 5: Finalize
            AnalysisRunService.complete_run(
                run,
                output_summary=f"Benchmark complete: {risk_level} risk, {overall_variance_pct:.1f}% variance",
                confidence_score=0.8 if not use_ai else None,
            )

            new_status = (
                ProcurementRequestStatus.COMPLETED
                if risk_level in (BenchmarkRiskLevel.LOW, BenchmarkRiskLevel.MEDIUM)
                else ProcurementRequestStatus.REVIEW_REQUIRED
            )
            ProcurementRequestService.update_status(request, new_status, user=run.triggered_by)

            return benchmark_result

        except Exception as exc:
            AnalysisRunService.fail_run(run, str(exc))
            ProcurementRequestService.update_status(
                request, ProcurementRequestStatus.FAILED, user=run.triggered_by,
            )
            raise

    # ------------------------------------------------------------------
    # Phase 1: AI routing through the orchestrator bridge
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_benchmark(
        item: QuotationLineItem,
        *,
        run: Optional[AnalysisRun] = None,
        memory: Optional[ProcurementAgentMemory] = None,
        use_ai: bool = False,
        request_user: Any = None,
    ) -> Dict[str, Any]:
        """Resolve benchmark price range for a line item.

        Priority:
        1. Deterministic catalogue lookup (Phase 2: implement DB/ERP lookup here)
        2. AI estimate via ProcurementAgentOrchestrator (when use_ai=True)
        3. Fallback: no benchmark data

        Phase 1: deterministic source is a stub. AI path is routed through
        ProcurementAgentOrchestrator to gain standard audit + trace records.
        """
        # Phase 2 extension point: add deterministic catalogue lookup here
        # e.g.: result = BenchmarkCatalogueService.lookup(item)
        # if result: return result

        if use_ai and run is not None:
            try:
                from apps.procurement.agents.benchmark_agent import BenchmarkAgent

                orchestrator = ProcurementAgentOrchestrator()

                def _agent_fn(ctx):
                    return BenchmarkAgent.resolve_benchmark_for_item(item)

                orch_result = orchestrator.run(
                    run=run,
                    agent_type=f"benchmark_item_{item.pk}",
                    agent_fn=_agent_fn,
                    memory=memory,
                    extra_context={"line_item_pk": item.pk, "description": item.description},
                    request_user=request_user,
                )

                if orch_result.status == "completed" and orch_result.output:
                    bm = orch_result.output
                    # Store in memory for cross-agent visibility
                    if memory:
                        memory.benchmark_findings[item.description[:80]] = bm
                    return bm

            except Exception:
                logger.warning(
                    "BenchmarkService: AI benchmark resolution failed for line %s (non-blocking), using fallback.",
                    item.pk,
                    exc_info=True,
                )

        # Fallback: web search for indicative pricing
        geography = ""
        try:
            if run is not None:
                req = getattr(run, "request", None)
                if req is not None:
                    geography = (
                        getattr(req, "geography_country", "")
                        or str(getattr(req, "location", "") or "")
                    )
        except Exception:
            pass

        try:
            from apps.procurement.services.web_search_service import WebSearchService
            ws_result = WebSearchService.search_benchmark(
                description=item.description or "",
                geography=geography or "UAE",
                uom=str(item.uom or "") if hasattr(item, "uom") else "",
                currency=str(item.currency or "AED") if hasattr(item, "currency") else "AED",
            )
            if ws_result.get("avg") is not None:
                logger.info(
                    "BenchmarkService: web search found indicative pricing for line %s "
                    "(avg=%s, source=WEB_SEARCH).",
                    item.pk,
                    ws_result["avg"],
                )
                return ws_result
        except Exception:
            logger.warning(
                "BenchmarkService: web search fallback failed for line %s (non-blocking).",
                item.pk,
                exc_info=True,
            )

        # No benchmark data available from any source
        return {
            "min": None,
            "avg": None,
            "max": None,
            "source": "none",
        }

    @staticmethod
    def _compute_variance(
        item: QuotationLineItem,
        benchmark: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute variance between quoted value and benchmark average."""
        avg = benchmark.get("avg")
        if avg is None or avg == 0:
            return {
                "pct": None,
                "status": VarianceStatus.WITHIN_RANGE,
                "remarks": "No benchmark data available",
            }

        quoted = item.unit_rate
        pct = ((quoted - avg) / avg) * 100

        if pct > 30:
            status = VarianceStatus.SIGNIFICANTLY_ABOVE
        elif pct > 0:
            status = VarianceStatus.ABOVE_BENCHMARK
        elif pct < -30:
            status = VarianceStatus.BELOW_BENCHMARK
        else:
            status = VarianceStatus.WITHIN_RANGE

        return {
            "pct": pct,
            "status": status,
            "remarks": "",
        }

    @staticmethod
    def _classify_risk(variance_pct: Decimal) -> str:
        """Classify overall risk level from aggregate variance."""
        abs_var = abs(variance_pct)
        if abs_var <= RISK_THRESHOLDS["low"]:
            return BenchmarkRiskLevel.LOW
        elif abs_var <= RISK_THRESHOLDS["medium"]:
            return BenchmarkRiskLevel.MEDIUM
        elif abs_var <= RISK_THRESHOLDS["high"]:
            return BenchmarkRiskLevel.HIGH
        return BenchmarkRiskLevel.CRITICAL
