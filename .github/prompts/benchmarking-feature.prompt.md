---
mode: agent
description: "Add or modify should-cost benchmarking features (benchmark resolution, variance computation, risk classification, cross-vendor comparison)"
---

# Should-Cost Benchmarking Feature

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/Should-Cost-Benchmarking.md` -- complete flow plan: RFQ generation, quotation ingestion, reference resolution (4-tier chain), variance bands, cross-vendor comparison, risk aggregation, API/template design, Langfuse spans
- `docs/PROCUREMENT.md` -- Section 5.8 (BenchmarkService), Section 6 (Agent System), Section 3.7-3.8 (BenchmarkResult/BenchmarkResultLine models)

### Source Files (read in this order)
1. `apps/procurement/services/benchmark_service.py` -- BenchmarkService.run_benchmark() -- 5-step orchestration: start run, per-line resolution + variance, aggregate, persist (atomic), finalize. Study `_resolve_benchmark()` (3-tier priority), `_compute_variance()`, `_classify_risk()`
2. `apps/procurement/services/web_search_service.py` -- WebSearchService.search_benchmark() (DuckDuckGo IA + Bing scrape fallback, regex price parsing, confidence capped at 0.35) and search_product_info() for recommendation pipeline
3. `apps/procurement/runtime/procurement_agent_orchestrator.py` -- ProcurementAgentOrchestrator.run() wraps per-line BenchmarkAgent calls (one execution record per line item)
4. `apps/procurement/models.py` -- BenchmarkResult (header: total_quoted, total_benchmark, variance_pct, risk_level, summary_json), BenchmarkResultLine (per-line: benchmark_min/avg/max, quoted_value, variance_pct, variance_status, remarks)
5. `apps/procurement/services/quotation_service.py` -- QuotationService (create_quotation, add_line_items) + LineItemNormalizationService (normalize_line_items)
6. `apps/core/enums.py` -- VarianceStatus (WITHIN_RANGE, ABOVE_BENCHMARK, BELOW_BENCHMARK, SIGNIFICANTLY_ABOVE), BenchmarkRiskLevel (LOW, MEDIUM, HIGH, CRITICAL)

### Comprehension Check
1. Benchmark resolution has a 3-tier priority chain (Phase 1): (1) internal catalogue DB stub, (2) BenchmarkAgent via ProcurementAgentOrchestrator (LLM), (3) WebSearchService (DuckDuckGo/Bing, confidence 0.35)
2. Variance computation: `pct = (quoted - avg) / avg * 100`. Status bands: WITHIN_RANGE (-5% to +5%), ABOVE_BENCHMARK (>0%), SIGNIFICANTLY_ABOVE (>30%), BELOW_BENCHMARK (<-30%)
3. Risk classification: LOW (<=5%), MEDIUM (<=15%), HIGH (<=30%), CRITICAL (>30%) on absolute value of aggregate variance
4. ProcurementAgentOrchestrator creates a unique agent_type per line (`"benchmark_item_{pk}"`) and shares ProcurementAgentMemory across all lines in a single run
5. Request status after benchmark: COMPLETED when risk in (LOW, MEDIUM), REVIEW_REQUIRED when risk in (HIGH, CRITICAL)
6. Phase 2 TODOs include: BenchmarkCatalogueEntry model (Tier 1 DB lookup), cross-vendor comparison fields, expanded summary_json scorecard, historical quotation lookups (Tier 4)

---

## When Adding a New Benchmark Resolution Tier

The `_resolve_benchmark()` method uses a priority chain that falls through on failure. To add a new tier:

1. Define a new service method or class that returns the standard corridor dict:
   ```python
   {"min": Decimal, "avg": Decimal, "max": Decimal, "source": "tier_label", "confidence": float}
   ```
2. Insert the call at the correct priority position in `_resolve_benchmark()` (after the stub and before WebSearchService)
3. Wrap in `try/except` -- all tiers must be non-blocking (fall through on failure)
4. If the tier uses a new data model (like `BenchmarkCatalogueEntry`), create the model inheriting from `TimestampMixin` and add `geography`, `currency`, `category_code` fields for scoped matching
5. Add source label to the `remarks` field on `BenchmarkResultLine` when this tier provides the data
6. Consider adding a `source_tier` field to `BenchmarkResultLine` for provenance tracking (see `docs/Should-Cost-Benchmarking.md` Stage 4)

## When Adding Cross-Vendor Comparison

Per `docs/Should-Cost-Benchmarking.md` Stage 4:

1. Add fields to `BenchmarkResultLine`: `cross_vendor_min`, `cross_vendor_avg`, `cross_vendor_rank`, `is_outlier`, `source_tier`, `source_label`, `citations_json`
2. Create a new service method in `BenchmarkService` (e.g. `_cross_vendor_compare(request, run)`) that:
   - Groups all `BenchmarkResultLine` records by `normalized_description`
   - Computes min/avg/max across vendors for each line
   - Flags outliers (> 2 std-dev from cross_vendor_avg)
   - Sets `cross_vendor_rank` (1 = cheapest)
3. Call this method after the per-quotation loop in `run_benchmark()` -- it operates on already-persisted `BenchmarkResultLine` records
4. Create migration for the new fields

## When Modifying Variance Bands

1. Current thresholds are in `_compute_variance()` and `_classify_risk()` in `apps/procurement/services/benchmark_service.py`
2. Variance status bands: WITHIN_RANGE, ABOVE_BENCHMARK, BELOW_BENCHMARK, SIGNIFICANTLY_ABOVE -- see `VarianceStatus` enum in `apps/core/enums.py`
3. Risk level thresholds: LOW (<=5%), MEDIUM (<=15%), HIGH (<=30%), CRITICAL (>30%) -- see `BenchmarkRiskLevel` enum
4. If adding new status values, add to the enum in `apps/core/enums.py` first
5. Update `templates/procurement/request_workspace.html` benchmark section for any new badge colors
6. Update the `summary_json` structure if adding new aggregate metrics

## When Adding Benchmark Langfuse Tracing

Per `docs/Should-Cost-Benchmarking.md` Stage 8:

1. Open a root trace `"benchmark_run"` at the top of `BenchmarkService.run_benchmark()`
2. Create per-line spans: `"resolve_references"` (one per QuotationLineItem), `"compute_variance"` (one per vendor x line)
3. Create aggregate spans: `"cross_vendor_compare"`, `"aggregate_scores"`
4. Emit scores: `benchmark_lines_with_data` (ratio), `benchmark_overall_variance_pct` (raw float), `benchmark_risk_level` (LOW=1.0, MEDIUM=0.7, HIGH=0.4, CRITICAL=0.0)
5. All Langfuse calls wrapped in `try/except Exception: pass` -- never let tracing errors propagate
6. Pass `span=` to `score_trace()` for proper OTel trace_id linkage

## Coding Rules

- **Decimal precision**: Use `Decimal` for all financial values. Never use `float` for monetary calculations.
- **Non-blocking fallback**: Every resolution tier must be wrapped in `try/except` so failures fall through to the next tier.
- **ProcurementAgentMemory**: When calling BenchmarkAgent via the orchestrator, store results in `memory.benchmark_findings` keyed by description or line PK for cross-line visibility.
- **Web search confidence cap**: WebSearchService results always have `confidence=0.35` -- treat as indicative only.
- **ASCII only**: Sanitize all LLM-generated text in benchmark reasoning/remarks before persisting.
- **Tenant scoping**: BenchmarkResult and BenchmarkResultLine inherit tenant scoping from their parent AnalysisRun -> ProcurementRequest chain. No direct tenant FK needed on result lines.
