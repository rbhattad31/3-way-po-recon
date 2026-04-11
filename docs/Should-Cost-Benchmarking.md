# Should-Cost Benchmarking -- Complete Flow Plan

## Architecture Overview

```
ProcurementRequest
    |
    +-- GeneratedRFQ  --------------------------------- sent to vendors
    |                                                       |
    |                                         SupplierQuotation (one per vendor)
    |                                                       |
    |                                         QuotationLineItem (N lines)
    |                                                       |
    +-- AnalysisRun (run_type=BENCHMARK)                    |
    |       |                                               |
    |       +-- BenchmarkResult  <------------ matches -----+
    |               |
    |               +-- BenchmarkResultLine (one per QuotationLineItem)
    |                       +-- benchmark_min / avg / max  (should-cost band)
    |                       +-- quoted_value
    |                       +-- variance_pct
    |                       +-- variance_status
    |
    +-- request status --> COMPLETED | REVIEW_REQUIRED | FAILED
```

---

## Stage 1 -- RFQ Generation

### What already exists

- `GeneratedRFQ` model (`apps/procurement/models.py`) stores `rfq_ref`, `system_code`, `scope_json`, `line_items_json`
- `generate_rfq` view (template_views.py) builds Excel + PDF
- Scope rows come from `HVACServiceScope` or JS fallback table (category, description, unit, qty)

### What the RFQ must carry for benchmarking

| RFQ Field | Purpose |
|---|---|
| `rfq_ref` | Ties quotations back to a specific RFQ scope |
| `line_items_json` | Canonical scope rows that all vendors must price against |
| `system_code` | VRF / SPLIT_AC / CASSETTE etc. for reference lookup routing |
| `currency` | AED / SAR / OMR -- normalises all vendor prices to one currency |
| `geography_country` | Routes Perplexity / web-search market data to correct region |

### Gap to Fill -- Phase 2 Hook

Add a `scope_version` field to `GeneratedRFQ` so that re-issued RFQs do not pollute
the benchmark of an earlier version. When a vendor quotes against `rfq_ref`, their
`SupplierQuotation` links to that specific `GeneratedRFQ` (FK not yet on the model).

---

## Stage 2 -- Quotation Ingestion

### Two ingestion paths (both exist)

**Path A -- PDF upload -> LLM extraction**

```
DocumentUpload (PDF)
    -> QuotationDocumentPrefillService._extract_quotation_data()
    -> prefill_payload_json stored on SupplierQuotation
    -> PrefillReviewService.confirm_quotation_prefill()
    -> QuotationLineItem rows created
```

**Path B -- Manual entry**

```
POST /api/v1/procurement/quotations/
    -> QuotationService.create_quotation()
    -> QuotationService.add_line_items()
    -> QuotationLineItem rows created
```

### Line Item Fields Critical for Benchmarking

```python
QuotationLineItem:
    description              # "VRF Outdoor Unit 10HP"
    normalized_description   # canonical form used for matching
    category_code            # "Equipment" | "Piping" | "Electrical" etc.
    quantity                 # 2
    unit                     # "Nos" | "RM" | "LS"
    unit_rate                # 45000.00  <-- compared against should-cost band
    total_amount             # unit_rate x qty
    brand / model            # used to route to brand-specific pricing data
```

---

## Stage 3 -- Should-Cost Reference Resolution

This is the core intelligence step. `BenchmarkService._resolve_benchmark()` already has
a 3-tier priority chain. Here is the full expanded design:

### Resolution Chain (per line item)

```
Tier 1 -- Deterministic DB lookup
|    BenchmarkCatalogueEntry model (new)
|    Match by: normalized_description + category_code + unit
|    Returns: {min, avg, max, source: "catalogue", freshness_days}
|
Tier 2 -- MarketIntelligenceSuggestion (already seeded via Perplexity)
|    Match by: system_type -> product suggestions with price_range_low/high
|    Returns: {min, avg, max, source: "market_intelligence", confidence}
|
Tier 3 -- Perplexity live web search (WebSearchService.search_benchmark)
|    Query: "{description} unit price {geography} {currency} HVAC"
|    Returns: {min, avg, max, source: "web_search", citations}
|
Tier 4 -- Historical quotations (cross-request lookup)
|    SELECT avg(unit_rate) FROM QuotationLineItem
|      WHERE normalized_description ILIKE '%{keyword}%'
|        AND quotation__request__geography_country = '{country}'
|    Returns: {min, avg, max, source: "historical", sample_count}
|
Fallback -- No data -> variance_status = "NO_DATA"
```

### New Model: `BenchmarkCatalogueEntry`

```python
# apps/procurement/models.py (new)
class BenchmarkCatalogueEntry(TimestampMixin):
    category_code        = models.CharField(max_length=100, db_index=True)
    description_keywords = models.TextField()   # comma-separated
    unit                 = models.CharField(max_length=50)
    geography            = models.CharField(max_length=100, db_index=True)  # "UAE", "KSA"
    currency             = models.CharField(max_length=3)
    price_min            = models.DecimalField(max_digits=18, decimal_places=4)
    price_avg            = models.DecimalField(max_digits=18, decimal_places=4)
    price_max            = models.DecimalField(max_digits=18, decimal_places=4)
    source_label         = models.CharField(max_length=200)  # "Daikin UAE pricelist Q1 2026"
    valid_from           = models.DateField()
    valid_until          = models.DateField(null=True, blank=True)
    is_active            = models.BooleanField(default=True)
```

---

## Stage 4 -- Line-by-Line Comparison Logic

`BenchmarkService._compute_variance()` already exists. Expand it to:

### Variance Bands (per line)

```
quoted_value vs benchmark_avg:

  < -30%          -->  BELOW_BENCHMARK   (suspiciously cheap, quality risk)
  -30% to -5%     -->  BELOW_BENCHMARK   (favourable but verify scope)
  -5%  to  +5%    -->  WITHIN_RANGE      (GREEN -- acceptable)
  +5%  to +15%    -->  ABOVE_BENCHMARK   (AMBER -- negotiate)
  +15% to +30%    -->  HIGH              (RED -- escalate or reject)
  > +30%          -->  CRITICAL          (BLOCK -- likely scope mismatch)
```

### Cross-Vendor Comparison (multi-quotation)

When more than one `SupplierQuotation` exists for the same `ProcurementRequest`,
run a secondary comparison layer:

```
For each normalized_description (line descriptor):
    collect [vendor_A.unit_rate, vendor_B.unit_rate, vendor_C.unit_rate]
    compute: cross_vendor_min, cross_vendor_avg, cross_vendor_max
    flag: outlier vendors whose rate deviates > 2 std-dev from cross_vendor_avg
```

### New Fields to Add to `BenchmarkResultLine`

```python
cross_vendor_min      # lowest price across all vendors for this line
cross_vendor_avg      # mean across vendors
cross_vendor_rank     # 1 = cheapest vendor for this line
is_outlier            # True if > 2 std-dev from cross_vendor_avg
source_tier           # "catalogue" | "market_intelligence" | "web_search" | "historical" | "none"
source_label          # human-readable: "Perplexity UAE Q1 2026"
citations_json        # [{url, snippet}] from web search
```

---

## Stage 5 -- Risk Aggregation & Header Score

Current logic in `BenchmarkService.run_benchmark()` computes a single
`overall_variance_pct`. Expand to a full scorecard:

### `BenchmarkResult.summary_json` (expanded)

```json
{
  "line_count": 12,
  "lines_with_data": 10,
  "lines_no_data": 2,
  "lines_within_range": 7,
  "lines_above_benchmark": 2,
  "lines_critical": 1,
  "total_quoted": "480000.00",
  "total_benchmark": "420000.00",
  "variance_pct": "14.3",
  "risk_level": "MEDIUM",
  "negotiation_potential_aed": "60000.00",
  "vendor_count": 3,
  "cheapest_vendor": "Carrier UAE",
  "highest_risk_line": "VRF Outdoor Unit -- 38% above benchmark",
  "recommendation": "Negotiate line 3 and line 7 before award"
}
```

### Risk Level Mapping

| Condition | Risk Level |
|---|---|
| All lines WITHIN_RANGE | LOW |
| 1-2 lines ABOVE_BENCHMARK, none CRITICAL | MEDIUM |
| Any line HIGH or overall variance > 15% | HIGH |
| Any line CRITICAL or variance > 30% | CRITICAL |

---

## Stage 6 -- Fetching & Displaying Results

### API Endpoint

```
GET /api/v1/procurement/requests/{pk}/benchmark/
```

### Response Structure

```json
{
  "request_id": 42,
  "rfq_ref": "RFQ-2026-0001",
  "benchmark_results": [
    {
      "vendor": "Carrier UAE",
      "total_quoted": "480000.00",
      "total_benchmark": "420000.00",
      "variance_pct": "14.3",
      "risk_level": "MEDIUM",
      "lines": [
        {
          "line_number": 1,
          "description": "VRF Outdoor Unit 10HP",
          "category": "Equipment",
          "qty": 2,
          "unit": "Nos",
          "quoted_unit_rate": "45000.00",
          "benchmark_min": "38000.00",
          "benchmark_avg": "41000.00",
          "benchmark_max": "46000.00",
          "variance_pct": "9.7",
          "variance_status": "ABOVE_BENCHMARK",
          "source_tier": "market_intelligence",
          "source_label": "Perplexity UAE Q1 2026",
          "cross_vendor_rank": 1,
          "is_outlier": false,
          "citations_json": []
        }
      ]
    }
  ]
}
```

### Template View -- Benchmark Dashboard

**URL:** `/procurement/requests/{pk}/benchmark/`
**Template:** `templates/procurement/benchmark_dashboard.html`

**Sections:**

1. **Header KPI cards**
   - Total quoted vs total benchmark
   - Overall variance % with RAG badge
   - Negotiation potential (AED)
   - Risk level badge (LOW / MEDIUM / HIGH / CRITICAL)

2. **Vendor comparison table** (one column per vendor)
   - Rows = line items
   - Cells = unit_rate with colour coding by variance_status
   - Footer = totals + overall variance per vendor
   - Cheapest cell highlighted in green per row

3. **Line-item detail accordion**
   - Benchmark band bar (min--avg--max) with quoted price marker
   - Source tier badge + source label
   - Citations list (for web_search tier)
   - Cross-vendor rank badge

4. **Negotiation summary panel**
   - Lines sorted by descending variance_pct
   - Recommended negotiation focus items (CRITICAL + HIGH)
   - Estimated saving if negotiated to benchmark_avg

---

## Stage 7 -- Celery Task Integration

```python
# apps/procurement/tasks.py

@shared_task(bind=True, max_retries=3, default_retry_delay=60, acks_late=True)
def run_benchmark_task(self, request_id: int, quotation_ids: list):
    """
    Triggered after all quotations for a request are marked SUBMITTED.
    1. Resolve benchmark reference per line (4-tier chain)
    2. Compute variance per line per vendor
    3. Run cross-vendor comparison
    4. Persist BenchmarkResult + BenchmarkResultLine
    5. Update ProcurementRequest.status
    6. Emit AuditEvent + Langfuse trace
    """
    from apps.procurement.services.benchmark_service import BenchmarkService
    BenchmarkService.run_benchmark(request_id=request_id, quotation_ids=quotation_ids)
```

**Trigger point:** `QuotationService.mark_submitted()` -- when the last expected
quotation for an RFQ is marked SUBMITTED, enqueue `run_benchmark_task`.

---

## Stage 8 -- Langfuse Observability

| Score | Value | When |
|---|---|---|
| `benchmark_lines_with_data` | 0.0-1.0 (ratio) | After resolution |
| `benchmark_overall_variance_pct` | raw float | After aggregation |
| `benchmark_risk_level` | LOW=1.0, MEDIUM=0.7, HIGH=0.4, CRITICAL=0.0 | After aggregation |
| `benchmark_source_tier_hit` | 1=catalogue, 2=MI, 3=web, 4=historical, 0=none (avg) | After resolution |
| `benchmark_negotiation_potential` | raw AED float | After aggregation |

Span hierarchy:
```
trace: "benchmark_run" (root)
    +-- span: "resolve_references"   (one per line item)
    +-- span: "compute_variance"     (one per vendor x line)
    +-- span: "cross_vendor_compare" (one per request)
    +-- span: "aggregate_scores"
```

---

## Implementation Checklist

| # | Task | File(s) | Status |
|---|---|---|---|
| 1 | Add `BenchmarkCatalogueEntry` model | `apps/procurement/models.py` | TODO |
| 2 | Add `scope_version` FK on `GeneratedRFQ` | `apps/procurement/models.py` | TODO |
| 3 | Add cross-vendor fields to `BenchmarkResultLine` | `apps/procurement/models.py` | TODO |
| 4 | Expand `_resolve_benchmark()` to 4-tier chain | `apps/procurement/services/benchmark_service.py` | TODO |
| 5 | Expand `_compute_variance()` with full band logic | `apps/procurement/services/benchmark_service.py` | TODO |
| 6 | Add cross-vendor comparison method | `apps/procurement/services/benchmark_service.py` | TODO |
| 7 | Expand `summary_json` scorecard | `apps/procurement/services/benchmark_service.py` | TODO |
| 8 | `run_benchmark_task` Celery task | `apps/procurement/tasks.py` | TODO |
| 9 | `GET /api/v1/procurement/requests/{pk}/benchmark/` | `apps/procurement/views.py` | TODO |
| 10 | Benchmark serializer | `apps/procurement/serializers.py` | TODO |
| 11 | `benchmark_dashboard.html` template | `templates/procurement/` | TODO |
| 12 | Benchmark dashboard view + URL | `apps/procurement/template_views.py` | TODO |
| 13 | Langfuse tracing in `run_benchmark()` | `apps/procurement/services/benchmark_service.py` | TODO |
| 14 | Migration for new fields/models | `apps/procurement/migrations/` | TODO |
| 15 | `benchmark.view` + `benchmark.manage` permissions | `seed_rbac.py` | TODO |
