"""
Benchmark engine service.
Orchestrates the full should-cost pipeline:
  Upload -> Extract text (Azure DI) -> Parse line items -> Classify (AI / keywords)
  -> Corridor lookup -> Variance calculation -> Result aggregation -> Negotiation notes

BenchmarkDocumentExtractorAgent handles Stages 1-5 (Blob upload, Azure DI extraction,
OpenAI line-item classification, VarianceThresholdConfig application, DB persistence).
BenchmarkEngine._build_result then aggregates everything into a BenchmarkResult.
"""
import logging
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    VarianceStatus,
)
# ClassificationService and ExtractionService are now used exclusively inside
# BenchmarkDocumentExtractorAgent; no direct import needed here.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Variance classification thresholds
# ---------------------------------------------------------------------------
WITHIN_RANGE_MAX = 5.0    # |variance%| < 5 => WITHIN_RANGE
MODERATE_MAX = 15.0       # 5 <= |variance%| < 15 => MODERATE
                           # |variance%| >= 15 => HIGH


def classify_variance(variance_pct):
    """Classify a numeric variance percentage into a VarianceStatus string."""
    if variance_pct is None:
        return VarianceStatus.NEEDS_REVIEW
    abs_v = abs(variance_pct)
    if abs_v < WITHIN_RANGE_MAX:
        return VarianceStatus.WITHIN_RANGE
    if abs_v < MODERATE_MAX:
        return VarianceStatus.MODERATE
    return VarianceStatus.HIGH


# ---------------------------------------------------------------------------
# Corridor lookup
# ---------------------------------------------------------------------------

class CorridorLookupService:
    """Look up the appropriate BenchmarkCorridorRule for a line item."""

    @classmethod
    def find_corridor(cls, category: str, geography: str, scope_type: str, description: str = ""):
        """
        Priority order:
          1. Exact: category + geography + scope_type
          2. category + geography + ALL scope
          3. category + ALL geography + scope_type
          4. category + ALL + ALL
          5. None (no corridor)
        Also checks keywords against description for finer matching.
        """
        from apps.benchmarking.models import BenchmarkCorridorRule

        candidates = list(
            BenchmarkCorridorRule.objects.filter(
                is_active=True,
                category=category,
            ).order_by("priority")
        )

        if not candidates:
            return None

        desc_lower = (description or "").lower()

        # Score each candidate: higher = better match
        def score(rule):
            s = 0
            s += 10 if rule.geography == geography else (5 if rule.geography == "ALL" else -100)
            s += 5 if rule.scope_type == scope_type else (2 if rule.scope_type == "ALL" else -100)
            # Keyword match bonus
            kw_list = rule.keyword_list()
            if kw_list and desc_lower:
                if any(kw in desc_lower for kw in kw_list):
                    s += 3
            return s

        scored = [(score(r), r) for r in candidates]
        scored.sort(key=lambda x: (-x[0], x[1].priority))

        best_score, best_rule = scored[0]
        if best_score < 0:
            return None
        return best_rule


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

class BenchmarkEngine:
    """Orchestrates the full benchmarking pipeline for a BenchmarkRequest."""

    DEFAULT_PARALLEL_WORKERS = 4

    @staticmethod
    def _start_agent_run(bench_request: BenchmarkRequest, user=None, trace_id: str = "") -> Optional[int]:
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
                input_payload={
                    "benchmark_request_pk": bench_request.pk,
                    "geography": bench_request.geography,
                    "scope_type": bench_request.scope_type,
                },
                trace_id=trace_id or "",
                invocation_reason=f"Benchmarking pipeline run for request {bench_request.pk}",
                actor_user_id=getattr(user, "pk", None) if user is not None else None,
                actor_primary_role=getattr(user, "role", "") if user is not None else "SYSTEM_AGENT",
                access_granted=True,
                started_at=timezone.now(),
            )
            return agent_run.pk
        except Exception:
            logger.debug("BenchmarkEngine: unable to create AgentRun mirror (non-fatal)", exc_info=True)
            return None

    @staticmethod
    def _complete_agent_run(agent_run_id: Optional[int], *, confidence: float, summary: str, output: dict) -> None:
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
                output_payload=output,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
            try:
                from apps.agents.services.eval_adapter import AgentEvalAdapter
                run_after = AgentRun.objects.filter(pk=agent_run_id).first()
                if run_after is not None:
                    AgentEvalAdapter.sync_for_agent_run(run_after)
            except Exception:
                logger.debug("BenchmarkEngine: AgentEvalAdapter sync failed (non-fatal)", exc_info=True)
        except Exception:
            logger.debug("BenchmarkEngine: unable to complete AgentRun mirror (non-fatal)", exc_info=True)

    @staticmethod
    def _fail_agent_run(agent_run_id: Optional[int], *, error: str) -> None:
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
                error_message=str(error)[:2000],
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
            try:
                from apps.agents.services.eval_adapter import AgentEvalAdapter
                run_after = AgentRun.objects.filter(pk=agent_run_id).first()
                if run_after is not None:
                    AgentEvalAdapter.sync_for_agent_run(run_after)
            except Exception:
                logger.debug("BenchmarkEngine: AgentEvalAdapter sync failed after failure", exc_info=True)
        except Exception:
            logger.debug("BenchmarkEngine: unable to fail AgentRun mirror (non-fatal)", exc_info=True)

    @classmethod
    def run(cls, request_pk: int, user=None, tenant=None) -> dict:
        """
        Run the full should-cost pipeline for BenchmarkRequest(pk=request_pk).
        Returns {"success": bool, "error": str or None}
        """
        try:
            req_qs = BenchmarkRequest.objects
            if tenant is not None:
                req_qs = req_qs.filter(tenant=tenant)
            bench_request = req_qs.get(pk=request_pk)
        except BenchmarkRequest.DoesNotExist:
            return {"success": False, "error": f"BenchmarkRequest {request_pk} not found"}

        trace_ctx = TraceContext.get_current()
        try:
            AuditService.log_event(
                entity_type="BenchmarkRequest",
                entity_id=bench_request.pk,
                event_type="BENCHMARKING_RUN_STARTED",
                description=f"Benchmark run started for request {bench_request.pk}",
                user=user,
                trace_ctx=trace_ctx,
                status_before=bench_request.status,
                status_after="PROCESSING",
            )
        except Exception:
            logger.debug("BenchmarkEngine.run: start audit failed (non-fatal)", exc_info=True)

        bench_request.status = "PROCESSING"
        bench_request.error_message = ""
        bench_request.save(update_fields=["status", "error_message", "updated_at"])

        agent_run_id = cls._start_agent_run(
            bench_request,
            user=user,
            trace_id=getattr(trace_ctx, "trace_id", "") if trace_ctx else "",
        )

        try:
            cls._process_request(bench_request, user)

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

            try:
                result_obj = getattr(bench_request, "result", None)
                overall_dev = getattr(result_obj, "overall_deviation_pct", None) if result_obj else None
                confidence = 1.0
                if overall_dev is not None:
                    try:
                        abs_dev = abs(float(overall_dev))
                        confidence = max(0.0, min(1.0, 1.0 - (abs_dev / 100.0)))
                    except (TypeError, ValueError):
                        confidence = 0.7
                cls._complete_agent_run(
                    agent_run_id,
                    confidence=confidence,
                    summary=f"Benchmark completed for request {bench_request.pk}",
                    output={
                        "request_pk": bench_request.pk,
                        "status": bench_request.status,
                        "overall_deviation_pct": overall_dev,
                        "overall_status": getattr(result_obj, "overall_status", None) if result_obj else None,
                    },
                )
            except Exception:
                logger.debug("BenchmarkEngine.run: AgentRun completion failed (non-fatal)", exc_info=True)

            try:
                AuditService.log_event(
                    entity_type="BenchmarkRequest",
                    entity_id=bench_request.pk,
                    event_type="BENCHMARKING_RUN_COMPLETED",
                    description=f"Benchmark run completed for request {bench_request.pk}",
                    user=user,
                    trace_ctx=trace_ctx,
                    status_before="PROCESSING",
                    status_after="COMPLETED",
                    output_snapshot={
                        "request_pk": bench_request.pk,
                        "status": bench_request.status,
                    },
                )
            except Exception:
                logger.debug("BenchmarkEngine.run: complete audit failed (non-fatal)", exc_info=True)
            return {"success": True, "error": None}

        except Exception as exc:
            logger.exception("BenchmarkEngine.run failed for request %s", request_pk)
            bench_request.status = "FAILED"
            bench_request.error_message = str(exc)
            bench_request.save(update_fields=["status", "error_message", "updated_at"])

            cls._fail_agent_run(agent_run_id, error=str(exc))

            try:
                from apps.benchmarking.services.eval_adapter import BenchmarkingEvalAdapter
                BenchmarkingEvalAdapter.sync_for_request(
                    bench_request,
                    error_message=str(exc),
                    trace_id=getattr(trace_ctx, "trace_id", "") if trace_ctx else "",
                )
            except Exception:
                logger.debug("BenchmarkEngine.run: eval adapter failed on error (non-fatal)", exc_info=True)

            try:
                AuditService.log_event(
                    entity_type="BenchmarkRequest",
                    entity_id=bench_request.pk,
                    event_type="BENCHMARKING_RUN_FAILED",
                    description=f"Benchmark run failed for request {bench_request.pk}: {str(exc)[:200]}",
                    user=user,
                    trace_ctx=trace_ctx,
                    status_before="PROCESSING",
                    status_after="FAILED",
                    error_code="BENCHMARKING_RUN_FAILED",
                    output_snapshot={"error": str(exc)[:1000]},
                )
            except Exception:
                logger.debug("BenchmarkEngine.run: failure audit failed (non-fatal)", exc_info=True)
            return {"success": False, "error": str(exc)}

    @classmethod
    def _process_request(cls, bench_request: BenchmarkRequest, user):
        """Inner pipeline (runs inside a transaction).

        Delegates per-quotation extraction + classification to BenchmarkDocumentExtractorAgent
        (Azure DI + OpenAI), then aggregates all line items into a BenchmarkResult.
        """
        all_line_items = []
        quotation_ids = list(
            bench_request.quotations.filter(is_active=True).values_list("pk", flat=True)
        )

        if not quotation_ids:
            cls._build_result(bench_request, all_line_items, user)
            return

        max_workers = int(
            getattr(settings, "BENCHMARK_PARALLEL_WORKERS", cls.DEFAULT_PARALLEL_WORKERS)
        )
        worker_count = max(1, min(max_workers, len(quotation_ids)))

        if worker_count == 1:
            for quotation_id in quotation_ids:
                items = cls._process_quotation_by_pk(quotation_id, bench_request.pk, user)
                all_line_items.extend(items)
        else:
            logger.info(
                "BenchmarkEngine: processing %d quotation(s) with %d parallel workers for request %s",
                len(quotation_ids),
                worker_count,
                bench_request.pk,
            )
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(
                        cls._process_quotation_by_pk,
                        quotation_id,
                        bench_request.pk,
                        user,
                    ): quotation_id
                    for quotation_id in quotation_ids
                }
                for future in as_completed(futures):
                    quotation_id = futures[future]
                    try:
                        items = future.result()
                        all_line_items.extend(items)
                    except Exception as exc:
                        logger.exception(
                            "BenchmarkEngine: quotation processing failed for quotation %s in request %s: %s",
                            quotation_id,
                            bench_request.pk,
                            exc,
                        )

        # Aggregate into BenchmarkResult
        cls._build_result(bench_request, all_line_items, user)

    @classmethod
    def _process_quotation_by_pk(cls, quotation_pk: int, bench_request_pk: int, user) -> list:
        """Resolve records by PK and process one quotation."""
        try:
            quotation = BenchmarkQuotation.objects.select_related("request").get(pk=quotation_pk)
            bench_request = BenchmarkRequest.objects.get(pk=bench_request_pk)
        except Exception as exc:
            logger.exception(
                "BenchmarkEngine._process_quotation_by_pk failed to load quotation=%s request=%s: %s",
                quotation_pk,
                bench_request_pk,
                exc,
            )
            return []
        return cls._process_quotation(quotation, bench_request, user)

    @classmethod
    def _process_quotation(cls, quotation: BenchmarkQuotation, bench_request: BenchmarkRequest, user) -> list:
        """
        Run the BenchmarkDocumentExtractorAgent pipeline for one quotation.

        The agent handles:
          1. Azure Blob upload (fail-silent)
          2. Azure DI text/table extraction (falls back to pdfplumber)
          3. OpenAI batch classification via CategoryMaster (falls back to keywords)
          4. VarianceThresholdConfig application
          5. BenchmarkLineItem persistence

        Returns the newly persisted BenchmarkLineItem instances for aggregation.
        """
        from apps.benchmarking.services.document_extractor_agent import BenchmarkDocumentExtractorAgent

        agent = BenchmarkDocumentExtractorAgent()
        agent_result = agent.run(
            quotation_pk=quotation.pk,
            bench_request_pk=bench_request.pk,
            user=user,
        )

        if not agent_result["success"]:
            logger.warning(
                "BenchmarkEngine._process_quotation: agent failed for quotation %d: %s",
                quotation.pk,
                agent_result.get("error"),
            )
            return []

        # Re-fetch the saved BenchmarkLineItem instances for aggregation
        from apps.benchmarking.models import BenchmarkLineItem
        return list(
            BenchmarkLineItem.objects.filter(
                quotation=quotation,
                is_active=True,
            )
        )

    @classmethod
    def _build_result(cls, bench_request: BenchmarkRequest, line_items: list, user):
        """Aggregate line items into a BenchmarkResult."""
        if not line_items:
            result, _ = BenchmarkResult.objects.update_or_create(
                request=bench_request,
                defaults={
                    "tenant": bench_request.tenant,
                    "total_quoted": None,
                    "total_benchmark_mid": None,
                    "overall_deviation_pct": None,
                    "overall_status": VarianceStatus.NEEDS_REVIEW,
                    "category_summary_json": {},
                    "negotiation_notes_json": [],
                    "lines_within_range": 0,
                    "lines_moderate": 0,
                    "lines_high": 0,
                    "lines_needs_review": 0,
                },
            )
            return result

        # Counters
        total_quoted = Decimal("0")
        total_bench_mid = Decimal("0")
        counts = {
            VarianceStatus.WITHIN_RANGE: 0,
            VarianceStatus.MODERATE: 0,
            VarianceStatus.HIGH: 0,
            VarianceStatus.NEEDS_REVIEW: 0,
        }

        # Per-category accumulators
        cat_data = {}

        for item in line_items:
            amt = item.line_amount or (
                (item.quoted_unit_rate or Decimal("0")) * (item.quantity or Decimal("1"))
            )
            total_quoted += amt

            # Benchmark mid * qty for total bench
            if item.benchmark_mid is not None and item.quantity is not None:
                total_bench_mid += item.benchmark_mid * item.quantity
            elif item.benchmark_mid is not None:
                total_bench_mid += item.benchmark_mid

            counts[item.variance_status] = counts.get(item.variance_status, 0) + 1

            cat = item.category
            if cat not in cat_data:
                cat_data[cat] = {"quoted": Decimal("0"), "benchmark": Decimal("0"), "count": 0}
            cat_data[cat]["quoted"] += amt
            if item.benchmark_mid and item.quantity:
                cat_data[cat]["benchmark"] += item.benchmark_mid * item.quantity
            cat_data[cat]["count"] += 1

        # Overall deviation
        overall_deviation = None
        if total_bench_mid > 0:
            overall_deviation = float((total_quoted - total_bench_mid) / total_bench_mid * 100)

        overall_status = classify_variance(overall_deviation)

        # Category summary JSON
        category_summary = {}
        for cat, dat in cat_data.items():
            q = float(dat["quoted"])
            b = float(dat["benchmark"]) if dat["benchmark"] else None
            dev = None
            if b and b > 0:
                dev = (q - b) / b * 100
            category_summary[cat] = {
                "quoted": q,
                "benchmark_mid": b,
                "deviation_pct": dev,
                "count": dat["count"],
                "status": classify_variance(dev),
            }

        # Negotiation notes
        notes = cls._generate_negotiation_notes(line_items, category_summary, overall_deviation)

        result, _ = BenchmarkResult.objects.update_or_create(
            request=bench_request,
            defaults={
                "tenant": bench_request.tenant,
                "total_quoted": total_quoted,
                "total_benchmark_mid": total_bench_mid if total_bench_mid > 0 else None,
                "overall_deviation_pct": overall_deviation,
                "overall_status": overall_status,
                "category_summary_json": category_summary,
                "negotiation_notes_json": notes,
                "lines_within_range": counts.get(VarianceStatus.WITHIN_RANGE, 0),
                "lines_moderate": counts.get(VarianceStatus.MODERATE, 0),
                "lines_high": counts.get(VarianceStatus.HIGH, 0),
                "lines_needs_review": counts.get(VarianceStatus.NEEDS_REVIEW, 0),
            },
        )
        if user and not result.created_by:
            result.created_by = user
            result.save(update_fields=["created_by"])

        return result

    @classmethod
    def _generate_negotiation_notes(cls, line_items: list, category_summary: dict, overall_deviation) -> list:
        """Build plain-text negotiation talking points."""
        notes = []

        if overall_deviation is not None and overall_deviation > 15:
            notes.append(
                f"Overall quotation is {overall_deviation:.1f}% above benchmark. "
                "Request a revised commercial proposal from the supplier."
            )
        elif overall_deviation is not None and 5 <= overall_deviation <= 15:
            notes.append(
                f"Overall quotation is {overall_deviation:.1f}% above benchmark -- "
                "within negotiation range. Target at least 5-8% reduction."
            )

        for cat, dat in category_summary.items():
            dev = dat.get("deviation_pct")
            if dev is not None and dev > 15:
                notes.append(
                    f"{cat.capitalize()} items are {dev:.1f}% above benchmark. "
                    "Challenge unit rates in this category specifically."
                )

        # Top overpriced lines
        high_lines = [i for i in line_items if i.variance_status == "HIGH" and i.variance_pct is not None]
        high_lines.sort(key=lambda x: abs(x.variance_pct), reverse=True)
        for item in high_lines[:3]:
            notes.append(
                f"Line {item.line_number} '{item.description[:60]}': "
                f"quoted {float(item.quoted_unit_rate or 0):.2f} vs benchmark "
                f"{float(item.benchmark_mid or 0):.2f} "
                f"({item.variance_pct:+.1f}%). Negotiate this line."
            )

        if not notes:
            notes.append("Quotation is within acceptable benchmark range. Proceed with standard approval process.")

        return notes


# ---------------------------------------------------------------------------
# Live-pricing enrichment (Perplexity)
# ---------------------------------------------------------------------------

    @classmethod
    def run_live_enrichment(cls, request_pk: int, user=None, tenant=None) -> dict:
        """
        Enrich an existing BenchmarkRequest with live market pricing from Perplexity.

        Steps:
          1. Call PerplexityBenchmarkService to get live price corridors.
          2. Update BenchmarkLineItem benchmark_min/mid/max and benchmark_source.
          3. Re-aggregate BenchmarkResult with the updated line items.
          4. Stamp BenchmarkResult.live_enriched_at + live_enrichment_json.

        Returns:
          {"success": True/False, "enriched": int, "total": int, "error": str|None}
        """
        from django.utils import timezone
        from decimal import Decimal as D
        from apps.benchmarking.services.perplexity_benchmark_service import (
            PerplexityBenchmarkService,
        )

        try:
            req_qs = BenchmarkRequest.objects
            if tenant is not None:
                req_qs = req_qs.filter(tenant=tenant)
            bench_request = req_qs.get(pk=request_pk)
        except BenchmarkRequest.DoesNotExist:
            return {"success": False, "enriched": 0, "total": 0, "error": f"Request {request_pk} not found"}

        try:
            # --- Step 1: fetch live prices ---
            price_map = PerplexityBenchmarkService.fetch_prices_for_request(bench_request)
            if not price_map:
                return {
                    "success": False,
                    "enriched": 0,
                    "total": 0,
                    "error": "Perplexity returned no pricing data. Check API key and request line items.",
                }

            # --- Step 2: update each line item ---
            enriched_count = 0
            all_line_items = list(
                BenchmarkLineItem.objects.filter(
                    quotation__request=bench_request,
                    quotation__is_active=True,
                    is_active=True,
                )
            )

            with transaction.atomic():
                for item in all_line_items:
                    entry = price_map.get(str(item.pk))
                    if not entry:
                        continue

                    item.benchmark_min = D(str(entry["min_rate"]))
                    item.benchmark_mid = D(str(entry["mid_rate"]))
                    item.benchmark_max = D(str(entry["max_rate"]))
                    item.benchmark_source = BenchmarkLineItem.BENCHMARK_SOURCE_PERPLEXITY
                    item.live_price_json = entry
                    item.corridor_rule_code = "PPLX_LIVE"

                    # Recalculate variance
                    if item.quoted_unit_rate is not None and item.benchmark_mid:
                        try:
                            mid = float(item.benchmark_mid)
                            rate = float(item.quoted_unit_rate)
                            if mid > 0:
                                item.variance_pct = ((rate - mid) / mid) * 100
                                item.variance_status = classify_variance(item.variance_pct)
                                note_parts = [
                                    f"Live market (Perplexity): {entry.get('min_rate', 0):.0f} - "
                                    f"{entry.get('max_rate', 0):.0f} {entry.get('currency', 'AED')}. "
                                    f"{entry.get('source_note', '')}"
                                ]
                                if abs(item.variance_pct) >= 15:
                                    note_parts.append(
                                        f"Quoted {rate:.2f} is {item.variance_pct:+.1f}% vs live mid {mid:.2f}. "
                                        "Negotiate this line."
                                    )
                                item.variance_note = " ".join(note_parts)
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
                    else:
                        item.variance_status = VarianceStatus.NEEDS_REVIEW

                    item.save(update_fields=[
                        "benchmark_min", "benchmark_mid", "benchmark_max",
                        "benchmark_source", "live_price_json", "corridor_rule_code",
                        "variance_pct", "variance_status", "variance_note", "updated_at",
                    ])
                    enriched_count += 1

                # --- Step 3: re-aggregate result ---
                cls._build_result(bench_request, all_line_items, user)

                # --- Step 4: stamp live enrichment metadata ---
                citations_all = []
                confidences = []
                for entry in price_map.values():
                    citations_all.extend(entry.get("citations", []))
                    confidences.append(entry.get("confidence", 0.7))

                avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
                enrichment_meta = {
                    "enriched_items": enriched_count,
                    "total_items": len(all_line_items),
                    "avg_confidence": round(avg_conf, 3),
                    "unique_citations": list(set(citations_all))[:20],
                    "geography": bench_request.geography,
                    "scope_type": bench_request.scope_type,
                }

                BenchmarkResult.objects.filter(request=bench_request).update(
                    live_enriched_at=timezone.now(),
                    live_enrichment_json=enrichment_meta,
                )

            # Update request status
            bench_request.status = "COMPLETED"
            bench_request.save(update_fields=["status", "updated_at"])

            try:
                from apps.benchmarking.services.eval_adapter import BenchmarkingEvalAdapter
                BenchmarkingEvalAdapter.sync_for_request(bench_request)
            except Exception:
                logger.debug("BenchmarkEngine.run_live_enrichment: eval adapter failed (non-fatal)", exc_info=True)

            return {
                "success": True,
                "enriched": enriched_count,
                "total": len(all_line_items),
                "error": None,
            }

        except Exception as exc:
            logger.exception("BenchmarkEngine.run_live_enrichment failed for request %s", request_pk)
            return {"success": False, "enriched": 0, "total": 0, "error": str(exc)}
