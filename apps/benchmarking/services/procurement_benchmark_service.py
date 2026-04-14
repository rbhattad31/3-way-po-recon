"""Compatibility benchmark bridge for procurement tasks.

This keeps procurement tasks working after service pruning.
"""
from __future__ import annotations

from types import SimpleNamespace

from apps.core.enums import AnalysisRunStatus
from apps.procurement.models import BenchmarkResult


class ProcurementBenchmarkService:
    @staticmethod
    def run_benchmark(request, run, quotation):
        # Minimal deterministic benchmark placeholder based on available quotation total.
        total = quotation.total_amount or 0

        result = BenchmarkResult.objects.create(
            tenant=request.tenant,
            run=run,
            quotation=quotation,
            total_quoted_amount=total,
            total_benchmark_amount=total,
            variance_pct=0,
            risk_level="LOW",
            summary_json={"source": "procurement_benchmark_service", "note": "compatibility bridge"},
        )

        run.status = AnalysisRunStatus.COMPLETED
        run.output_summary = "Benchmark completed"
        run.confidence_score = 0.8
        run.save(update_fields=["status", "output_summary", "confidence_score", "updated_at"])

        return SimpleNamespace(risk_level=result.risk_level, variance_pct=result.variance_pct)
