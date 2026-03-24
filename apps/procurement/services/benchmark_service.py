"""BenchmarkService — orchestrates should-cost benchmarking flow."""
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
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.request_service import ProcurementRequestService

logger = logging.getLogger(__name__)

# Variance thresholds for risk classification
RISK_THRESHOLDS = {
    "low": Decimal("5.0"),       # ≤5% → LOW
    "medium": Decimal("15.0"),   # ≤15% → MEDIUM
    "high": Decimal("30.0"),     # ≤30% → HIGH
    # >30% → CRITICAL
}


class BenchmarkService:
    """Orchestrates should-cost benchmarking.

    Steps:
      1. Resolve benchmark references per line item
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
    ) -> BenchmarkResult:
        AnalysisRunService.start_run(run)

        try:
            line_items = list(quotation.line_items.all())
            if not line_items:
                raise ValueError("Quotation has no line items to benchmark.")

            # Step 1 & 2: Resolve benchmarks and compute variance
            line_results = []
            benchmarks_resolved = 0
            benchmarks_failed = 0
            for item in line_items:
                benchmark_data = BenchmarkService._resolve_benchmark(item, use_ai=use_ai)
                if benchmark_data.get("avg") is not None:
                    benchmarks_resolved += 1
                else:
                    benchmarks_failed += 1
                variance = BenchmarkService._compute_variance(item, benchmark_data)
                line_results.append({
                    "item": item,
                    "benchmark": benchmark_data,
                    "variance": variance,
                })

            # Step 3: Aggregate and classify risk
            total_quoted = sum(item.total_amount for item in line_items)
            # Only include items with actual benchmark data in the benchmark total
            total_benchmark = Decimal("0")
            total_quoted_benchmarked = Decimal("0")
            for lr in line_results:
                avg = lr["benchmark"].get("avg")
                if avg is not None:
                    total_benchmark += avg * lr["item"].quantity
                    total_quoted_benchmarked += lr["item"].total_amount

            if total_benchmark and total_quoted_benchmarked:
                overall_variance_pct = (
                    (total_quoted_benchmarked - total_benchmark) / total_benchmark * 100
                )
            else:
                overall_variance_pct = None

            risk_level = (
                BenchmarkService._classify_risk(overall_variance_pct)
                if overall_variance_pct is not None
                else BenchmarkRiskLevel.HIGH  # unknown = HIGH risk
            )

            # Step 4: Persist results
            with transaction.atomic():
                benchmark_result = BenchmarkResult.objects.create(
                    run=run,
                    quotation=quotation,
                    total_quoted_amount=total_quoted,
                    total_benchmark_amount=total_benchmark if total_benchmark else None,
                    variance_pct=overall_variance_pct,
                    risk_level=risk_level,
                    summary_json={
                        "line_count": len(line_items),
                        "benchmarks_resolved": benchmarks_resolved,
                        "benchmarks_failed": benchmarks_failed,
                        "total_quoted": str(total_quoted),
                        "total_benchmark": str(total_benchmark) if total_benchmark else None,
                        "variance_pct": str(overall_variance_pct) if overall_variance_pct is not None else None,
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
                        # Source tracking from ReAct agent
                        source_type=bm.get("source_type", "ai_estimate"),
                        source_urls=bm.get("source_urls", []),
                        source_confidence=bm.get("source_confidence"),
                        reasoning=bm.get("reasoning", ""),
                    ))
                BenchmarkResultLine.objects.bulk_create(benchmark_lines)

            # Step 5: Finalize
            variance_str = f"{overall_variance_pct:.1f}%" if overall_variance_pct is not None else "N/A"
            AnalysisRunService.complete_run(
                run,
                output_summary=f"Benchmark complete: {risk_level} risk, {variance_str} variance ({benchmarks_resolved}/{len(line_items)} resolved)",
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

    @staticmethod
    def _resolve_benchmark(
        item: QuotationLineItem,
        *,
        use_ai: bool = True,
    ) -> Dict[str, Any]:
        """Resolve benchmark price range for a line item via ReAct agent.

        The agent uses web-search tools to gather real market data before
        synthesising a benchmark range.  The quoted price is NOT passed to
        the agent to avoid anchoring bias.
        """
        if use_ai:
            try:
                from apps.procurement.agents.benchmark_agent import BenchmarkAgent
                agent = BenchmarkAgent()
                return agent.resolve_benchmark_for_item(item)
            except Exception:
                logger.warning("AI benchmark resolution failed for line %s, using fallback", item.pk)

        # Fallback: no benchmark data available
        return {
            "min": None,
            "avg": None,
            "max": None,
            "source_type": "none",
            "source_urls": [],
            "source_confidence": None,
            "reasoning": "",
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

        # Ensure Decimal arithmetic (avg may arrive as float from AI agent)
        avg = Decimal(str(avg))
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
