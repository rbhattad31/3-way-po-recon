"""
Write the new Procurement Intelligence Platform Reference page.
Replaces templates/agents/reference.html (which was the old 3-Way PO Reconciliation reference).
"""
import os

TARGET = os.path.join(
    os.path.dirname(__file__), "..", "templates", "agents", "reference.html"
)

HTML = """\
{{% extends "base.html" %}}
{{% block title %}}Procurement Intelligence Platform -- Reference{{% endblock %}}

{{% block extra_css %}}
<style>
.diagram-zoom-wrap {{
  position: relative; overflow: hidden; cursor: grab;
  background: #fafafa; border-radius: .375rem; min-height: 220px; height: 460px;
}}
.diagram-zoom-wrap.is-grabbing {{ cursor: grabbing; }}
.diagram-zoom-wrap .mermaid {{ display: inline-block; transform-origin: top left; transition: none; user-select: none; }}
.diagram-zoom-wrap svg {{ display: block; max-width: none !important; height: auto; }}
.diagram-zoom-toolbar {{
  display: flex; align-items: center; gap: .35rem; padding: .35rem .5rem;
  border-bottom: 1px solid #dee2e6; background: #fff; border-radius: .375rem .375rem 0 0;
}}
.diagram-zoom-toolbar .zoom-level {{ font-size: .7rem; color: #6c757d; min-width: 3rem; text-align: center; }}
.diagram-fs-overlay {{
  position: fixed; inset: 0; z-index: 1055; background: #fff; display: flex; flex-direction: column;
}}
.diagram-fs-overlay .diagram-zoom-toolbar {{ flex: 0 0 auto; border-radius: 0; padding: .5rem .75rem; }}
.diagram-fs-overlay .diagram-zoom-wrap {{
  flex: 1 1 0%; min-height: 0; height: auto !important; max-height: none !important;
  overflow: auto; border-radius: 0; background: #fff; cursor: default;
}}
.docs-toc {{
  position: sticky; top: 70px; max-height: calc(100vh - 90px); overflow-y: auto;
  font-size: .8rem; padding-bottom: 2rem;
}}
.docs-toc .toc-group-label {{
  font-size: .65rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em;
  color: #adb5bd; padding: .6rem .75rem .15rem; margin-top: .25rem;
}}
.docs-toc .nav-link {{ padding: .2rem .75rem; color: #6c757d; border-left: 2px solid transparent; border-radius: 0; line-height: 1.4; }}
.docs-toc .nav-link:hover  {{ color: #0d6efd; border-left-color: #cfe2ff; background: none; }}
.docs-toc .nav-link.active {{ color: #0d6efd; font-weight: 600; border-left-color: #0d6efd; background: none; }}
.docs-toc .toc-child {{ padding-left: 1.5rem; font-size: .76rem; }}
.docs-section {{ scroll-margin-top: 70px; padding-top: 2.5rem; }}
.docs-section + .docs-section {{ border-top: 1px solid #e9ecef; margin-top: 1rem; }}
.docs-section-title {{ font-size: 1.4rem; font-weight: 700; color: #111827; margin-bottom: 1rem; }}
.docs-h3 {{ font-size: 1rem; font-weight: 600; color: #374151; margin-top: 1.75rem; margin-bottom: .75rem; padding-bottom: .35rem; border-bottom: 1px solid #f3f4f6; }}
.stage-row {{ display: flex; gap: .85rem; align-items: flex-start; padding: .75rem 0; border-bottom: 1px solid #f3f4f6; }}
.stage-row:last-child {{ border-bottom: none; }}
.stage-num {{ width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: .82rem; flex-shrink: 0; color: #fff; }}
.stage-num-secondary {{ background: #6c757d; }}
.stage-num-info      {{ background: #0dcaf0; color: #000; }}
.stage-num-primary   {{ background: #0d6efd; }}
.stage-num-warning   {{ background: #ffc107; color: #000; }}
.stage-num-success   {{ background: #198754; }}
.stage-num-danger    {{ background: #dc3545; }}
code {{ background: #f3f4f6; border-radius: 4px; padding: .05rem .35rem; font-size: .83em; color: #dc3545; }}
pre code {{ background: none; padding: 0; color: inherit; font-size: inherit; }}
.flow-arrow {{ font-size: 1.1rem; color: #adb5bd; align-self: center; }}
.status-badge {{ display: inline-block; padding: .2rem .6rem; border-radius: 20px; font-size: .76rem; font-weight: 600; }}
</style>
{{% endblock %}}

{{% block breadcrumb %}}
<nav aria-label="breadcrumb">
  <ol class="breadcrumb mb-0">
    <li class="breadcrumb-item"><a href="{{% url 'dashboard:index' %}}">Dashboard</a></li>
    <li class="breadcrumb-item active">Platform Reference</li>
  </ol>
</nav>
{{% endblock %}}

{{% block content %}}
<div class="row gx-4 mt-2">

  <!-- MAIN CONTENT -->
  <div class="col-xl-9 col-lg-8" id="docs-content">

    <!-- OVERVIEW -->
    <div id="overview" class="docs-section" style="padding-top:.25rem;border-top:none">
      <h1 class="docs-section-title">
        <i class="bi bi-building-fill-gear me-2 text-warning"></i>Procurement Intelligence Platform
        <span style="font-size:.65rem;font-weight:700;background:#fef3c7;color:#92400e;padding:.2rem .5rem;border-radius:4px;vertical-align:middle;margin-left:.5rem;">Bradsol Group</span>
      </h1>
      <p class="text-muted mb-4" style="max-width:720px">
        End-to-end procurement intelligence covering request management, AI-powered quotation extraction,
        HVAC system recommendation, GCC market benchmarking, compliance validation, and agentic analysis pipeline.
        GCC-calibrated with AED market benchmarks across UAE, KSA, Oman, Qatar, Kuwait, and Bahrain.
      </p>

      <div class="card mb-3">
        <div class="card-header bg-white pb-0">
          <ul class="nav nav-tabs card-header-tabs" id="diagramTabs" role="tablist">
            <li class="nav-item" role="presentation">
              <button class="nav-link active fw-semibold small" data-bs-toggle="tab" data-bs-target="#tab-platform" type="button">
                <i class="bi bi-diagram-3 me-1"></i>Platform Overview
              </button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link fw-semibold small" data-bs-toggle="tab" data-bs-target="#tab-lifecycle" type="button">
                <i class="bi bi-arrow-repeat me-1"></i>Request Lifecycle
              </button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link fw-semibold small" data-bs-toggle="tab" data-bs-target="#tab-extraction" type="button">
                <i class="bi bi-file-earmark-text me-1"></i>Quotation Extraction
              </button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link fw-semibold small" data-bs-toggle="tab" data-bs-target="#tab-analysis" type="button">
                <i class="bi bi-cpu me-1"></i>Analysis Engine
              </button>
            </li>
          </ul>
        </div>
        <div class="tab-content">

          <!-- Tab 1: Platform Overview -->
          <div class="tab-pane fade show active" id="tab-platform" role="tabpanel">
            <div class="diagram-zoom-toolbar">
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-in"><i class="bi bi-zoom-in"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-out"><i class="bi bi-zoom-out"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-reset"><i class="bi bi-aspect-ratio"></i></button>
              <span class="zoom-level">100%</span>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 ms-auto zoom-fullscreen"><i class="bi bi-fullscreen"></i></button>
            </div>
            <div class="diagram-zoom-wrap p-3">
              <div class="mermaid">
flowchart LR
    A["Request Created\\nProcurement manager defines\\nproject scope, site parameters,\\nbudget, and GCC country"]
    B["Quotation Upload\\nSupplier PDFs uploaded.\\nOCR reads structure.\\nLLM extracts line items,\\nvendor info, amounts"]
    C["Prefill Review\\nExtracted fields shown\\nfor human confirmation.\\nManual corrections tracked\\nper field"]
    D["AI Analysis Engine\\nRecommendation: optimal\\nsystem type and brand.\\nBenchmark: market price\\ncomparison vs GCC rates.\\nCompliance: spec validation"]
    E["Review and Approval\\nREVIEW_REQUIRED cases\\nassigned to Procurement Manager.\\nApprove, reject, or re-run analysis"]
    F["Completed\\nFull audit trail stored.\\nLangfuse traces per run.\\nERP reference data available"]

    A --> B
    B -->|"PDF uploaded"| C
    B -->|"No quotation yet"| D
    C -->|"Fields confirmed"| D
    D -->|"High confidence\\nautomated result"| F
    D -->|"Low confidence or\\ncompliance issues"| E
    E --> F

    QEA(["Quotation Extraction Agent\\nAzure Document Intelligence OCR\\nGPT-4o field extraction\\n60K char text limit"])
    B --- QEA

    ATTR(["Attribute Collection\\nDynamic HVAC site parameters\\nStore type, area, zones,\\nambient temp, CW backbone"])
    A --- ATTR

    RA(["Recommendation Agent\\nSelects optimal HVAC system\\nbased on site parameters\\nand GCC market data"])
    BA(["Benchmark Agent\\nCompares vendor quotation\\nagainst HVAC reference\\ncatalogue AED prices"])
    CA(["Compliance Agent\\nValidates specs against\\nGCC standards and\\nlandlord requirements"])
    D --- RA
    D --- BA
    D --- CA

    classDef step fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a,font-weight:bold
    classDef ai   fill:#d1fae5,stroke:#059669,color:#065f46,font-weight:bold
    classDef agent fill:#f0fdf4,stroke:#10b981,color:#064e3b,font-size:12px
    classDef ok   fill:#fef3c7,stroke:#d97706,color:#78350f,font-weight:bold
    class A,C,E step
    class B,D ai
    class F ok
    class QEA,ATTR,RA,BA,CA agent
              </div>
            </div>
            <p class="small text-muted border-top pt-2 px-3 pb-2 mb-0">
              <i class="bi bi-info-circle me-1"></i>Green rounded boxes are AI agent components. Blue is human-in-the-loop. Amber is completed state.
            </p>
          </div>

          <!-- Tab 2: Request Lifecycle -->
          <div class="tab-pane fade" id="tab-lifecycle" role="tabpanel">
            <div class="diagram-zoom-toolbar">
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-in"><i class="bi bi-zoom-in"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-out"><i class="bi bi-zoom-out"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-reset"><i class="bi bi-aspect-ratio"></i></button>
              <span class="zoom-level">100%</span>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 ms-auto zoom-fullscreen"><i class="bi bi-fullscreen"></i></button>
            </div>
            <div class="diagram-zoom-wrap p-3">
              <div class="mermaid">
stateDiagram-v2
    [*] --> DRAFT : Request created
    DRAFT --> READY : Mark Ready action
    DRAFT --> CANCELLED : Cancelled by user
    READY --> PROCESSING : Trigger Analysis
    PROCESSING --> COMPLETED : Analysis succeeded
    PROCESSING --> REVIEW_REQUIRED : Low confidence or issues
    PROCESSING --> FAILED : Pipeline error
    REVIEW_REQUIRED --> PROCESSING : Re-run Analysis
    REVIEW_REQUIRED --> COMPLETED : Manager approves
    REVIEW_REQUIRED --> CANCELLED : Request cancelled
    COMPLETED --> [*]
    FAILED --> READY : Reset after fix
    CANCELLED --> [*]
              </div>
            </div>
            <p class="small text-muted border-top pt-2 px-3 pb-2 mb-0">
              <i class="bi bi-info-circle me-1"></i>Status stored on <code>ProcurementRequest.status</code>. Each <code>AnalysisRun</code> tracks its own QUEUED/RUNNING/COMPLETED/FAILED status independently.
            </p>
          </div>

          <!-- Tab 3: Quotation Extraction -->
          <div class="tab-pane fade" id="tab-extraction" role="tabpanel">
            <div class="diagram-zoom-toolbar">
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-in"><i class="bi bi-zoom-in"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-out"><i class="bi bi-zoom-out"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-reset"><i class="bi bi-aspect-ratio"></i></button>
              <span class="zoom-level">100%</span>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 ms-auto zoom-fullscreen"><i class="bi bi-fullscreen"></i></button>
            </div>
            <div class="diagram-zoom-wrap p-3">
              <div class="mermaid">
flowchart TD
    UP(["Quotation PDF or Image\\nUploaded by procurement user"]) --> OCR["Azure Document Intelligence\\nLayout-aware OCR\\nText capped at 60,000 characters"]
    OCR --> QEA["Quotation Extraction Agent\\nGPT-4o reads OCR text\\nOutputs structured JSON:\\nvendor, date, line items,\\namounts, HVAC specs, brand, model"]
    QEA --> PARSE["Parse and Normalise\\nLine items validated,\\namounts cross-checked,\\nHVAC attributes mapped\\nvia synonym mapping"]
    PARSE --> CONF{{"Confidence\\nthreshold met?"}}
    CONF -->|"High confidence"| STORE["Stored in prefill_payload_json\\nNot yet persisted to DB.\\nUser sees review screen"]
    CONF -->|"Low confidence"| REVIEW["Prefill Review Screen\\nUser confirms or corrects\\neach extracted field"]
    STORE --> REVIEW
    REVIEW --> CONFIRM["User Confirms\\nPrefillReviewService.confirm()\\nCreates SupplierQuotation record\\nCreates QuotationLineItem records\\nMarks prefill_status = COMPLETED"]
    CONFIRM --> READY(["Quotation ready\\nfor Analysis Engine"])
    QEA --> FAIL(["Extraction Failed\\nprefill_status = FAILED\\nUser can retry or enter manually"])

    classDef ai     fill:#d1fae5,stroke:#059669,color:#065f46
    classDef gate   fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef bad    fill:#fee2e2,stroke:#ef4444,color:#7f1d1d
    classDef neutral fill:#f3f4f6,stroke:#9ca3af,color:#374151
    classDef good   fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a
    class OCR,QEA,PARSE ai
    class CONF gate
    class REVIEW,STORE,CONFIRM good
    class UP,READY neutral
    class FAIL bad
              </div>
            </div>
            <p class="small text-muted border-top pt-2 px-3 pb-2 mb-0">
              <i class="bi bi-info-circle me-1"></i>Line items are <strong>not</strong> persisted during extraction -- only in <code>prefill_payload_json</code> until the user confirms on the Prefill Review screen.
            </p>
          </div>

          <!-- Tab 4: Analysis Engine -->
          <div class="tab-pane fade" id="tab-analysis" role="tabpanel">
            <div class="diagram-zoom-toolbar">
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-in"><i class="bi bi-zoom-in"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-out"><i class="bi bi-zoom-out"></i></button>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 zoom-reset"><i class="bi bi-aspect-ratio"></i></button>
              <span class="zoom-level">100%</span>
              <button class="btn btn-sm btn-outline-secondary py-0 px-2 ms-auto zoom-fullscreen"><i class="bi bi-fullscreen"></i></button>
            </div>
            <div class="diagram-zoom-wrap p-3">
              <div class="mermaid">
flowchart TD
    START(["Trigger Analysis\\nRun type: RECOMMENDATION\\nBENCHMARK, VALIDATION, or COMPLIANCE"]) --> SNAP["Build Input Snapshot\\nAll attributes, quotation line items,\\nsite params saved to input_snapshot_json"]
    SNAP --> ROUTE{{"Route to\\nanalysis type"}}

    ROUTE -->|"RECOMMENDATION"| REC["Recommendation Engine\\nSite parameters mapped to HVAC criteria.\\nGCC climate calibration applied.\\nOptimal system type and brand selected"]
    REC --> RRES["RecommendationResult saved\\nSystem type, brand, capacity,\\nconfidence score, rationale,\\nGCC-specific warnings"]

    ROUTE -->|"BENCHMARK"| BENCH["Benchmark Engine\\nEach quotation line item\\nmatched to reference catalogue.\\nUnit rates compared vs AED market bands"]
    BENCH --> BRES["BenchmarkResult + Lines saved\\nVariance per cent per line,\\nrisk level LOW/MEDIUM/HIGH,\\nmarket position rating"]

    ROUTE -->|"VALIDATION"| VAL["Validation Engine\\nSpec compliance rules applied.\\nGCC standards enforced per attribute.\\nCritical failures flagged"]
    VAL --> VRES["ComplianceResult saved\\nPass/Fail per rule,\\noverall compliance status"]

    RRES --> CONF{{"Confidence and\\ncompliance acceptable?"}}
    BRES --> CONF
    VRES --> CONF

    CONF -->|"Pass"| DONE(["AnalysisRun COMPLETED\\nRequest status -> COMPLETED"])
    CONF -->|"Review needed"| RREQ(["AnalysisRun COMPLETED\\nRequest status -> REVIEW_REQUIRED\\nAssigned to Procurement Manager"])

    classDef step fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a,font-weight:bold
    classDef ai   fill:#d1fae5,stroke:#059669,color:#065f46,font-weight:bold
    classDef gate fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef ok   fill:#d1fae5,stroke:#059669,color:#065f46
    classDef warn fill:#fef3c7,stroke:#d97706,color:#78350f
    class START,SNAP step
    class REC,BENCH,VAL ai
    class RRES,BRES,VRES step
    class CONF gate
    class DONE ok
    class RREQ warn
              </div>
            </div>
            <p class="small text-muted border-top pt-2 px-3 pb-2 mb-0">
              <i class="bi bi-info-circle me-1"></i>Each <code>AnalysisRun</code> is independent. Re-triggering creates a new run -- all historical runs are preserved.
            </p>
          </div>
        </div>
      </div>
    </div>

    <!-- DATA MODEL -->
    <div id="data-model" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-diagram-2 me-2 text-primary"></i>Data Model</h2>
      <p class="text-muted mb-3">All procurement data lives in <code>apps/procurement/</code>. Core model hierarchy:</p>

      <div class="card mb-3">
        <div class="card-body" style="overflow-x:auto">
          <pre class="mb-0" style="font-size:.79rem;color:#374151">ProcurementRequest   (db: procurement_request)
  |- request_id        UUID unique indexed
  |- title             CharField(300)
  |- domain_code       "HVAC" | "IT" | "FACILITIES" | ...
  |- schema_code       Attribute schema identifier
  |- request_type      RECOMMENDATION | BENCHMARK | BOTH
  |- status            DRAFT | READY | PROCESSING | COMPLETED | REVIEW_REQUIRED | FAILED | CANCELLED
  |- priority          LOW | MEDIUM | HIGH | CRITICAL
  |- geography_country UAE | KSA | OMAN | QATAR | KUWAIT | BAHRAIN
  |- geography_city    Free text
  |- currency          AED | SAR | OMR | QAR | KWD | BHD | USD
  |- prefill_status    NOT_STARTED | IN_PROGRESS | REVIEW_PENDING | COMPLETED | FAILED
  |- prefill_confidence  Float 0.0-1.0
  |- prefill_payload_json  JSON -- raw extracted fields not yet confirmed
  |- uploaded_document FK docs.DocumentUpload
  |
  +--&lt; ProcurementRequestAttribute  (db: procurement_request_attribute)
  |      |- attribute_code   e.g. "store_type" "area_sqm" "zone_count"
  |      |- data_type        TEXT | NUMBER | SELECT | BOOLEAN | JSON
  |      |- value_text / value_number / value_json
  |      |- extraction_source  MANUAL | DOCUMENT | AI
  |      |- confidence_score   Float
  |
  +--&lt; SupplierQuotation  (db: procurement_supplier_quotation)
  |      |- vendor_name / quotation_number / quotation_date
  |      |- total_amount / currency
  |      |- extraction_status  PENDING | IN_PROGRESS | COMPLETED | FAILED
  |      |- prefill_payload_json  JSON -- raw payload before confirmation
  |      |
  |      +--&lt; QuotationLineItem  (db: procurement_quotation_line_item)
  |             |- line_number / description / category_code
  |             |- quantity / unit / unit_rate / total_amount
  |             |- brand / model / extraction_confidence
  |
  +--&lt; AnalysisRun  (db: procurement_analysis_run)
         |- run_id     UUID
         |- run_type   RECOMMENDATION | BENCHMARK | VALIDATION | COMPLIANCE
         |- status     QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED
         |- confidence_score   Float
         |- input_snapshot_json  Full snapshot at time of run
         |- output_summary       Plain-text AI summary
         |- trace_id             Langfuse trace propagation
         |
         +-- RecommendationResult    1:1 with RECOMMENDATION run
         +-- BenchmarkResult         1:1 with BENCHMARK run
         |    +--&lt; BenchmarkResultLine  one per quotation line
         +-- ComplianceResult        1:1 with COMPLIANCE/VALIDATION run</pre>
        </div>
      </div>

      <h3 class="docs-h3">Key Design Decisions</h3>
      <ul class="small text-muted">
        <li><strong>Soft delete</strong> -- all models inherit <code>BaseModel</code> (soft delete via <code>is_active</code>) + <code>TimestampMixin</code> (created_at, updated_at) + <code>AuditMixin</code> (created_by, updated_by).</li>
        <li><strong>Dynamic attributes</strong> -- HVAC-specific fields (area, zones, ambient temp, etc.) are stored as <code>ProcurementRequestAttribute</code> rows, not fixed columns. New domains (IT, FACILITIES) define their own schemas without migrations.</li>
        <li><strong>Prefill separation</strong> -- extracted data lives in <code>prefill_payload_json</code> until the user explicitly confirms. Persistence to <code>SupplierQuotation</code> + <code>QuotationLineItem</code> only happens on confirmation via <code>PrefillReviewService.confirm_quotation_prefill()</code>.</li>
        <li><strong>Multiple analysis runs</strong> -- re-triggering creates a new <code>AnalysisRun</code>. All prior runs are preserved. Each run captures a full <code>input_snapshot_json</code> for comparison across runs.</li>
        <li><strong>Langfuse tracing</strong> -- <code>AnalysisRun.trace_id</code> is propagated from the Celery task into the pipeline stages so every LLM call appears nested in Langfuse under the same root trace.</li>
      </ul>
    </div>

    <!-- REQUEST LIFECYCLE -->
    <div id="request-lifecycle" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-arrow-repeat me-2 text-info"></i>Request Lifecycle</h2>

      <h3 class="docs-h3">ProcurementRequest Status Machine</h3>
      <div class="stage-row">
        <div class="stage-num stage-num-secondary">D</div>
        <div>
          <strong>DRAFT</strong>
          <div class="text-muted small">Initial state after creation. Required fields may be incomplete. User fills in site parameters on the HVAC Create form, optionally uploads a quotation PDF, sets priority and geography. The <em>Mark Ready</em> button activates when all mandatory attributes are present.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-info">R</div>
        <div>
          <strong>READY</strong>
          <div class="text-muted small">All mandatory fields are present. Eligible for analysis. User selects run type (RECOMMENDATION, BENCHMARK, VALIDATION) and clicks <em>Run Analysis</em>. A new <code>AnalysisRun</code> record is created and a Celery task is enqueued. Request transitions immediately to PROCESSING.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-primary">P</div>
        <div>
          <strong>PROCESSING</strong>
          <div class="text-muted small">Celery task is executing the pipeline. <code>AnalysisRun.status = RUNNING</code>. The workspace shows a live progress spinner. On pipeline completion, transitions to either COMPLETED (all checks pass) or REVIEW_REQUIRED (issues found).</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-success">C</div>
        <div>
          <strong>COMPLETED</strong>
          <div class="text-muted small">All analysis runs succeeded with acceptable confidence and no critical compliance issues. Results are available under the <em>Analysis Results</em> tab. Full audit trail and Langfuse traces are stored indefinitely.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-warning">RR</div>
        <div>
          <strong>REVIEW_REQUIRED</strong>
          <div class="text-muted small">Analysis completed but confidence is below threshold, compliance issues found, or significant benchmark variance (HIGH risk) detected. The Procurement Manager must review and either manually approve (-> COMPLETED) or re-trigger analysis (-> PROCESSING).</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-danger">F</div>
        <div>
          <strong>FAILED</strong>
          <div class="text-muted small">Unhandled pipeline error. Check <code>AnalysisRun.error_message</code>. Common causes: LLM timeout, missing required attributes, HVAC catalogue not seeded. Reset to READY after fixing the underlying issue.</div>
        </div>
      </div>

      <h3 class="docs-h3">AnalysisRun Status</h3>
      <p class="text-muted small">Independent from request status. Multiple runs can exist per request.</p>
      <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
        <span class="status-badge" style="background:#f3f4f6;color:#374151">QUEUED</span>
        <span class="flow-arrow">&#8594;</span>
        <span class="status-badge" style="background:#dbeafe;color:#1e3a8a">RUNNING</span>
        <span class="flow-arrow">&#8594;</span>
        <span class="status-badge" style="background:#d1fae5;color:#065f46">COMPLETED</span>
        <span style="color:#adb5bd;margin:0 .25rem">|</span>
        <span class="status-badge" style="background:#fee2e2;color:#7f1d1d">FAILED</span>
        <span style="color:#adb5bd;margin:0 .25rem">|</span>
        <span class="status-badge" style="background:#e5e7eb;color:#6b7280">CANCELLED</span>
      </div>
    </div>

    <!-- QUOTATION EXTRACTION -->
    <div id="quotation-extraction" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-file-earmark-text me-2 text-primary"></i>Quotation Extraction Pipeline</h2>
      <p class="text-muted mb-3">
        Converts supplier PDF quotations into structured data using Azure Document Intelligence (OCR) + GPT-4o (field extraction).
        Key files: <code>apps/procurement/services/prefill/quotation_prefill_service.py</code>,
        <code>apps/procurement/agents/quotation_extraction_agent.py</code>,
        <code>apps/procurement/services/prefill/attribute_mapping_service.py</code>.
      </p>

      <h3 class="docs-h3">Extraction Stages</h3>
      <div class="stage-row">
        <div class="stage-num stage-num-secondary">1</div>
        <div>
          <strong>OCR -- Azure Document Intelligence</strong>
          <div class="text-muted small">Reads uploaded PDF or image. Layout-aware extraction preserves table structure for line items. Raw text is capped at <strong>60,000 characters</strong>. Stored temporarily for LLM processing.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-primary">2</div>
        <div>
          <strong>Quotation Extraction Agent (GPT-4o)</strong>
          <div class="text-muted small">LLM receives OCR text + system prompt. Outputs structured JSON with: <code>vendor_name</code>, <code>quotation_number</code>, <code>quotation_date</code>, <code>total_amount</code>, <code>currency</code>, and <code>line_items[]</code> (description, qty, unit, unit_rate, brand, model). Max tokens: <strong>8,192</strong>.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-info">3</div>
        <div>
          <strong>Field Synonym Mapping (AttributeMappingService)</strong>
          <div class="text-muted small">Normalises extracted field names using <code>_QUOTATION_FIELD_SYNONYMS</code>. E.g. "unit price" maps to <code>unit_rate</code>, "supplier" to <code>vendor_name</code>, "cooling capacity" to <code>capacity_tr</code>, "refrigerant type" to <code>refrigerant</code>.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-warning">4</div>
        <div>
          <strong>Prefill Review Screen</strong>
          <div class="text-muted small">User sees the review screen at <code>/procurement/prefill/review/</code>. Each extracted field shows its value and confidence score. Fields below confidence threshold are highlighted for review. Every field edit is tracked individually.</div>
        </div>
      </div>
      <div class="stage-row">
        <div class="stage-num stage-num-success">5</div>
        <div>
          <strong>Confirmation -- PrefillReviewService.confirm_quotation_prefill()</strong>
          <div class="text-muted small">On user confirmation: creates <code>SupplierQuotation</code> + <code>QuotationLineItem</code> records. Sets <code>prefill_status = COMPLETED</code>. Line items only exist in the DB from this point forward.</div>
        </div>
      </div>

      <h3 class="docs-h3">Constraints and Edge Cases</h3>
      <ul class="small text-muted">
        <li>OCR text hard limit: <strong>60,000 characters</strong></li>
        <li>LLM extraction max tokens: <strong>8,192</strong></li>
        <li>Line items are <strong>not</strong> persisted until user confirmation (they live in <code>prefill_payload_json</code>)</li>
        <li>A request can have multiple <code>SupplierQuotation</code> records (one per supplier)</li>
        <li>If OCR or LLM extraction fails, <code>prefill_status = FAILED</code> is set -- user can still enter line items manually via the workspace quotation tab</li>
        <li>The quotation upload on the HVAC Create form creates both the <code>ProcurementRequest</code> and a <code>SupplierQuotation</code> in one POST -- the extraction pipeline runs as a Celery task after the response returns</li>
      </ul>
    </div>

    <!-- ANALYSIS ENGINE -->
    <div id="analysis-engine" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-cpu me-2 text-success"></i>Analysis Engine</h2>
      <p class="text-muted mb-3">Three primary analysis types run per request. Each creates an independent <code>AnalysisRun</code> so results are fully versioned.</p>

      <h3 class="docs-h3">1. RECOMMENDATION</h3>
      <p class="text-muted small">Selects optimal HVAC system type and brand using site parameters from <code>ProcurementRequestAttribute</code>. GCC climate rules are applied. Output stored in <code>RecommendationResult</code>.</p>
      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Input Attribute</th><th>Code</th><th>Selection Impact</th></tr></thead>
          <tbody>
            <tr><td>Facility Type</td><td><code>store_type</code></td><td>Mall = FCU/CW preferred. Warehouse = packaged. Office = VRF.</td></tr>
            <tr><td>Conditioned Area (m&sup2;)</td><td><code>area_sqm</code></td><td>Cooling load estimate. GCC retail: 130 W/m&sup2;. Data centre: 300+ W/m&sup2;.</td></tr>
            <tr><td>Number of Zones</td><td><code>zone_count</code></td><td>Zones &gt; 8 favours VRF/VRV over multiple splits.</td></tr>
            <tr><td>Max Ambient Temp (&deg;C)</td><td><code>ambient_temp_max</code></td><td>GCC typical: 46-52&deg;C. Affects unit nameplate rating selection.</td></tr>
            <tr><td>Chilled Water Available</td><td><code>chilled_water_available</code></td><td>YES = FCU/AHU. NO = direct expansion (split or VRF).</td></tr>
            <tr><td>Outdoor Unit Restriction</td><td><code>outdoor_unit_restriction</code></td><td>YES = chiller or FCU on CW only. Eliminates split and VRF.</td></tr>
            <tr><td>Noise Sensitivity</td><td><code>noise_sensitivity</code></td><td>HIGH = cassette or FCU only. No packaged rooftop units.</td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="docs-h3">2. BENCHMARK</h3>
      <p class="text-muted small">Compares each <code>QuotationLineItem.unit_rate</code> against the HVAC reference catalogue AED market bands. Calculates variance % per line. Output: <code>BenchmarkResult</code> + <code>BenchmarkResultLine</code>.</p>
      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Variance vs Market</th><th>Risk Level</th><th>Effect on Request Status</th></tr></thead>
          <tbody>
            <tr><td>&lt; &plusmn;5%</td><td><span class="badge bg-success">LOW</span></td><td>COMPLETED</td></tr>
            <tr><td>&plusmn;5% to &plusmn;15%</td><td><span class="badge bg-warning text-dark">MEDIUM</span></td><td>COMPLETED with warning</td></tr>
            <tr><td>&gt; &plusmn;15%</td><td><span class="badge bg-danger">HIGH</span></td><td>REVIEW_REQUIRED</td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="docs-h3">3. VALIDATION / COMPLIANCE</h3>
      <p class="text-muted small">
        Validates request attributes against domain-specific rules. HVAC checks include: ambient temperature within GCC rated range, refrigerant type compliant with GCC regulations (R32/R410A preferred, R22 blocked), zone-to-area ratio validation, landlord outdoor unit restrictions, and mandatory site parameter completeness.
        Each rule is evaluated independently. Critical failures (severity = CRITICAL or HIGH) set <code>REVIEW_REQUIRED</code>.
        Output stored in <code>ComplianceResult</code>.
      </p>
    </div>

    <!-- HVAC INTELLIGENCE -->
    <div id="hvac-intelligence" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-wind me-2 text-info"></i>HVAC Intelligence</h2>
      <p class="text-muted mb-3">
        HVAC is the primary domain for Bradsol Group. The platform contains GCC-calibrated selection logic,
        2025 AED market benchmarks, and site-specific engineering rules for all six GCC countries.
      </p>

      <h3 class="docs-h3">System Type Selection Matrix</h3>
      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>System Type</th><th>Code</th><th>Typical Use Case</th><th>CW Required</th><th>Typical Capacity</th></tr></thead>
          <tbody>
            <tr><td>Split AC</td><td><code>SPLIT_AC</code></td><td>Small retail, kiosks, offices</td><td>No</td><td>1-5 TR</td></tr>
            <tr><td>Cassette Split AC</td><td><code>CASSETTE_AC</code></td><td>Medium retail with false ceiling &ge;250mm</td><td>No</td><td>1.5-6 TR</td></tr>
            <tr><td>VRF / VRV System</td><td><code>VRF_VRV</code></td><td>Multi-zone retail &gt;200m&sup2;, 3-64 indoor units per ODU</td><td>No</td><td>8-48 TR</td></tr>
            <tr><td>Fan Coil Unit (FCU)</td><td><code>FCU_CW</code></td><td>Mall stores with chilled water backbone</td><td>Yes</td><td>0.5-5 TR each</td></tr>
            <tr><td>Air Handling Unit</td><td><code>AHU</code></td><td>Large open spaces, warehouses, hypermarkets</td><td>Yes</td><td>10-100+ TR</td></tr>
            <tr><td>Packaged Unit</td><td><code>PACKAGED_UNIT</code></td><td>Standalone warehouses, logistics hubs</td><td>No</td><td>5-50 TR</td></tr>
            <tr><td>Chiller Plant</td><td><code>CHILLER</code></td><td>Anchor stores, data centres, loads &gt;100 TR</td><td>Generates CW</td><td>100-1000+ TR</td></tr>
            <tr><td>Ventilation / ERV</td><td><code>VENTILATION</code></td><td>Restaurant kitchens, high-occupancy spaces, F&amp;B</td><td>No</td><td>Per application</td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="docs-h3">Cooling Load Estimation</h3>
      <div class="card bg-light border-0 p-3 mb-3" style="font-size:.85rem">
        <strong>Formula:</strong> <code>Cooling Load (TR) = Area (m&sup2;) &times; Design Density (W/m&sup2;) &divide; 3,517 (W/TR)</code><br>
        <div class="mt-2 text-muted">
          Default density by facility type:
          <ul class="mb-0 mt-1">
            <li><strong>GCC Retail</strong> -- 130 W/m&sup2;</li>
            <li><strong>Office / F&amp;B</strong> -- 150-180 W/m&sup2;</li>
            <li><strong>Warehouse / Logistics</strong> -- 80-100 W/m&sup2;</li>
            <li><strong>Data Centre</strong> -- 250-400 W/m&sup2; (varies by server density)</li>
          </ul>
        </div>
      </div>

      <h3 class="docs-h3">GCC Climate Zones</h3>
      <div class="table-responsive">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Zone</th><th>Max Ambient (&deg;C)</th><th>Humidity</th><th>Dust</th><th>Key Engineering Constraint</th></tr></thead>
          <tbody>
            <tr><td>UAE Coastal (Dubai, Sharjah, Abu Dhabi)</td><td>48</td><td>HIGH</td><td>MEDIUM</td><td>Marine-grade epoxy coating on ODU coils</td></tr>
            <tr><td>UAE Inland (Al Ain, Abu Dhabi inland)</td><td>50</td><td>LOW</td><td>HIGH</td><td>Sand guards + high-ambient rated compressors</td></tr>
            <tr><td>KSA Coastal (Jeddah, Dammam)</td><td>48</td><td>HIGH</td><td>MEDIUM</td><td>Sea salt corrosion protection class C4/C5</td></tr>
            <tr><td>KSA Inland (Riyadh)</td><td>52</td><td>LOW</td><td>HIGH</td><td>52&deg;C rated units mandatory. Dual filter banks.</td></tr>
            <tr><td>Qatar (Doha, Lusail)</td><td>49</td><td>HIGH</td><td>MEDIUM</td><td>Coastal + Shamal sandstorm exposure</td></tr>
            <tr><td>Kuwait</td><td>50</td><td>MEDIUM</td><td>HIGH</td><td>High ambient, dust-proof ODU enclosures required</td></tr>
            <tr><td>Oman (Muscat, Musandam)</td><td>48</td><td>HIGH</td><td>MEDIUM</td><td>Coastal salt exposure standard</td></tr>
            <tr><td>Bahrain</td><td>47</td><td>HIGH</td><td>LOW</td><td>Island humidity -- anti-microbial coatings recommended</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- AGENT ARCHITECTURE -->
    <div id="agent-architecture" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-robot me-2 text-success"></i>Agent Architecture</h2>
      <p class="text-muted mb-3">
        The procurement module has its own agentic layer at <code>apps/procurement/agents/</code>, separate from the reconciliation agent system at <code>apps/agents/</code>.
      </p>

      <h3 class="docs-h3">Quotation Extraction Agent</h3>
      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Property</th><th>Value</th></tr></thead>
          <tbody>
            <tr><td>File</td><td><code>apps/procurement/agents/quotation_extraction_agent.py</code></td></tr>
            <tr><td>LLM</td><td>GPT-4o (Azure OpenAI). Falls back to standard OpenAI if Azure endpoint not set.</td></tr>
            <tr><td>Input</td><td>Raw OCR text from Azure Document Intelligence (hard cap: 60,000 chars)</td></tr>
            <tr><td>Output</td><td>Structured JSON: vendor, date, currency, line_items[] with brand/model/qty/rate</td></tr>
            <tr><td>Max tokens</td><td>8,192 (response side)</td></tr>
            <tr><td>Triggered by</td><td><code>QuotationDocumentPrefillService</code> on quotation file upload</td></tr>
            <tr><td>Failure mode</td><td>Sets <code>prefill_status = FAILED</code>. Logs error. User proceeds manually.</td></tr>
            <tr><td>Langfuse tracing</td><td>Each extraction creates a span under the request's root trace</td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="docs-h3">Analysis Agents (per run type)</h3>
      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Run Type</th><th>Service Location</th><th>Output Model</th><th>LLM?</th></tr></thead>
          <tbody>
            <tr><td>RECOMMENDATION</td><td><code>apps/procurement/services/recommendation/</code></td><td><code>RecommendationResult</code></td><td>Yes -- rationale text generation</td></tr>
            <tr><td>BENCHMARK</td><td><code>apps/procurement/services/benchmark/</code></td><td><code>BenchmarkResult</code> + <code>BenchmarkResultLine</code> (one per line)</td><td>Partial -- category classification</td></tr>
            <tr><td>VALIDATION</td><td><code>apps/procurement/services/validation/</code></td><td><code>ComplianceResult</code></td><td>No -- rule-based engine</td></tr>
            <tr><td>COMPLIANCE</td><td><code>apps/procurement/services/compliance/</code></td><td><code>ComplianceResult</code></td><td>No -- rule-based engine</td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="docs-h3">HVAC-specific Services</h3>
      <div class="table-responsive">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Service</th><th>Location</th><th>Purpose</th></tr></thead>
          <tbody>
            <tr><td>HVAC Selection Engine</td><td><code>apps/procurement/hvac/</code></td><td>Core system type selection and load calculation per GCC zone</td></tr>
            <tr><td>Prefill Orchestrator</td><td><code>apps/procurement/services/prefill/quotation_prefill_service.py</code></td><td>Manages OCR + LLM extraction pipeline end-to-end</td></tr>
            <tr><td>Attribute Mapping Service</td><td><code>apps/procurement/services/prefill/attribute_mapping_service.py</code></td><td>Synonym normalisation via <code>_QUOTATION_FIELD_SYNONYMS</code> dict</td></tr>
            <tr><td>Prefill Review Service</td><td><code>apps/procurement/services/prefill/</code></td><td>Confirms reviewed payload to DB -- creates Quotation + LineItems</td></tr>
            <tr><td>Analysis Orchestrator</td><td><code>apps/procurement/services/</code></td><td>Routes <code>AnalysisRun</code> to correct engine based on <code>run_type</code></td></tr>
            <tr><td>Domain Runtime</td><td><code>apps/procurement/runtime/</code></td><td>Domain-specific runtime config and schema definitions</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- NAVIGATION AND UI -->
    <div id="navigation" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-layout-sidebar me-2 text-secondary"></i>Navigation &amp; UI Pages</h2>

      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Page</th><th>URL Name</th><th>What it shows</th></tr></thead>
          <tbody>
            <tr><td>Procurement Dashboard</td><td><code>procurement:procurement_dashboard</code></td><td>KPI cards (total, by status, HVAC count), status doughnut chart, domain bar chart, recent requests table</td></tr>
            <tr><td>All Requests</td><td><code>procurement:request_list</code></td><td>Filterable list with status badges, domain tags, priority, quick Open action</td></tr>
            <tr><td>New HVAC Request</td><td><code>procurement:hvac_create</code></td><td>Comprehensive HVAC site parameters form. Bradsol Group dark-theme. JS converts f_* fields to attr_code[]/attr_value[] arrays on submit. Optional quotation upload triggers extraction pipeline.</td></tr>
            <tr><td>New Request (Generic)</td><td><code>procurement:request_create</code></td><td>Domain-agnostic request form for non-HVAC use cases</td></tr>
            <tr><td>Request Workspace</td><td><code>procurement:request_workspace pk</code></td><td>Deep-dive per request. Tabs: Overview, Attributes, Quotations, Analysis Results, Reference Catalogue. Run Analysis + Mark Ready actions.</td></tr>
            <tr><td>Prefill Review</td><td><code>quotation_prefill_review</code></td><td>Field-by-field review of LLM-extracted quotation data before DB persistence</td></tr>
            <tr><td>Analysis Run Detail</td><td><code>procurement:run_detail pk</code></td><td>Full output of a single AnalysisRun: recommendation text, benchmark variance table, compliance rule results</td></tr>
          </tbody>
        </table>
      </div>

      <h3 class="docs-h3">Workspace Tabs</h3>
      <div class="table-responsive">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Tab</th><th>Content</th></tr></thead>
          <tbody>
            <tr><td><i class="bi bi-info-circle me-1"></i>Overview</td><td>Request metadata: status badge, priority, geography, currency, prefill status with confidence %, source document type, trace ID link</td></tr>
            <tr><td><i class="bi bi-sliders me-1"></i>Attributes</td><td>All <code>ProcurementRequestAttribute</code> rows grouped by data type. Shows attribute_code, label, value, extraction_source, confidence score.</td></tr>
            <tr><td><i class="bi bi-file-earmark-text me-1"></i>Quotations</td><td>Uploaded supplier quotations with line item tables. Shows extraction status, vendor name, totals, prefill confidence. Upload new quotation button.</td></tr>
            <tr><td><i class="bi bi-cpu me-1"></i>Analysis Results</td><td>All <code>AnalysisRun</code> records. For each: run type badge, status, duration, confidence score bar, output summary text, link to full detail view.</td></tr>
            <tr><td><i class="bi bi-book me-1"></i>Reference Catalogue</td><td>Searchable HVAC product catalogue (sourced from imported master data). Browse by system type and brand. Copy product codes into the HVAC Create form for precise benchmarking.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- API REFERENCE -->
    <div id="api-reference" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-code-slash me-2 text-secondary"></i>API Reference</h2>
      <p class="text-muted small mb-3">All endpoints under <code>/api/v1/procurement/</code>. Auth required on all endpoints. Default pagination: 25/page.</p>

      <div class="table-responsive">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td><span class="badge bg-primary">GET</span></td><td><code>/api/v1/procurement/requests/</code></td><td>List requests. Filter: <code>status</code>, <code>domain_code</code>, <code>request_type</code>, <code>priority</code>.</td></tr>
            <tr><td><span class="badge bg-success">POST</span></td><td><code>/api/v1/procurement/requests/</code></td><td>Create a new procurement request.</td></tr>
            <tr><td><span class="badge bg-primary">GET</span></td><td><code>/api/v1/procurement/requests/{id}/</code></td><td>Request detail with attributes and quotation summaries.</td></tr>
            <tr><td><span class="badge bg-warning text-dark">PATCH</span></td><td><code>/api/v1/procurement/requests/{id}/</code></td><td>Update request. Status transitions are validated server-side.</td></tr>
            <tr><td><span class="badge bg-primary">GET</span></td><td><code>/api/v1/procurement/attributes/</code></td><td>List attributes. Filter: <code>request</code>, <code>attribute_code</code>.</td></tr>
            <tr><td><span class="badge bg-primary">GET</span></td><td><code>/api/v1/procurement/quotations/</code></td><td>List quotations. Filter: <code>request</code>, <code>vendor_name</code>, <code>extraction_status</code>.</td></tr>
            <tr><td><span class="badge bg-success">POST</span></td><td><code>/api/v1/procurement/quotations/</code></td><td>Upload a new quotation. Triggers extraction pipeline as Celery task.</td></tr>
            <tr><td><span class="badge bg-primary">GET</span></td><td><code>/api/v1/procurement/analysis-runs/</code></td><td>List analysis runs. Filter: <code>request</code>, <code>run_type</code>, <code>status</code>.</td></tr>
            <tr><td><span class="badge bg-success">POST</span></td><td><code>/api/v1/procurement/analysis-runs/</code></td><td>Trigger analysis. Body: <code>{{"request": id, "run_type": "RECOMMENDATION"}}</code></td></tr>
            <tr><td><span class="badge bg-primary">GET</span></td><td><code>/api/v1/procurement/analysis-runs/{id}/</code></td><td>Full run detail including nested RecommendationResult, BenchmarkResultLines, ComplianceResult.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- PERMISSIONS -->
    <div id="permissions" class="docs-section">
      <h2 class="docs-section-title"><i class="bi bi-shield-check me-2 text-warning"></i>Permissions &amp; RBAC</h2>

      <div class="table-responsive mb-3">
        <table class="table table-sm table-bordered" style="font-size:.82rem">
          <thead class="table-light"><tr><th>Permission Code</th><th>Gates</th><th>Default Roles</th></tr></thead>
          <tbody>
            <tr><td><code>procurement.view</code></td><td>Dashboard, request list, workspace, all read-only pages</td><td>All roles</td></tr>
            <tr><td><code>procurement.create</code></td><td>HVAC Create form, generic request create</td><td>AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, ADMIN</td></tr>
            <tr><td><code>procurement.edit</code></td><td>Edit request fields, update attributes, upload quotation</td><td>AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, ADMIN</td></tr>
            <tr><td><code>procurement.run_analysis</code></td><td>Trigger AnalysisRun from workspace</td><td>REVIEWER, FINANCE_MANAGER, ADMIN</td></tr>
            <tr><td><code>procurement.approve</code></td><td>Manually advance REVIEW_REQUIRED to COMPLETED</td><td>FINANCE_MANAGER, ADMIN</td></tr>
            <tr><td><code>procurement.delete</code></td><td>Cancel or soft-delete requests</td><td>FINANCE_MANAGER, ADMIN</td></tr>
          </tbody>
        </table>
      </div>
      <p class="text-muted small">
        Template views use <code>@permission_required_code("procurement.view")</code>.
        DRF API views use <code>HasPermissionCode("procurement.view")</code>.
        The platform-wide <code>AgentGuardrailsService</code> enforces RBAC for all agentic operations.
        Permissions are seeded via <code>python manage.py seed_rbac</code>.
      </p>
    </div>

  </div>

  <!-- RIGHT SIDEBAR TOC -->
  <div class="col-xl-3 col-lg-4 d-none d-lg-block">
    <nav class="docs-toc" id="docs-toc">
      <div class="toc-group-label">Contents</div>
      <ul class="nav flex-column">
        <li class="nav-item"><a class="nav-link active" href="#overview">Platform Overview</a></li>
        <li class="nav-item"><a class="nav-link" href="#data-model">Data Model</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#data-model">ProcurementRequest</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#data-model">SupplierQuotation</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#data-model">AnalysisRun</a></li>
        <div class="toc-group-label">Lifecycle</div>
        <li class="nav-item"><a class="nav-link" href="#request-lifecycle">Request Lifecycle</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#request-lifecycle">Status Machine</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#request-lifecycle">AnalysisRun Status</a></li>
        <div class="toc-group-label">Pipelines</div>
        <li class="nav-item"><a class="nav-link" href="#quotation-extraction">Quotation Extraction</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#quotation-extraction">Extraction Stages</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#quotation-extraction">Constraints</a></li>
        <li class="nav-item"><a class="nav-link" href="#analysis-engine">Analysis Engine</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#analysis-engine">Recommendation</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#analysis-engine">Benchmark</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#analysis-engine">Validation</a></li>
        <div class="toc-group-label">Domain</div>
        <li class="nav-item"><a class="nav-link" href="#hvac-intelligence">HVAC Intelligence</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#hvac-intelligence">System Types</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#hvac-intelligence">Cooling Load</a></li>
        <li class="nav-item toc-child"><a class="nav-link" href="#hvac-intelligence">GCC Climate Zones</a></li>
        <div class="toc-group-label">Technical</div>
        <li class="nav-item"><a class="nav-link" href="#agent-architecture">Agent Architecture</a></li>
        <li class="nav-item"><a class="nav-link" href="#navigation">Navigation &amp; UI</a></li>
        <li class="nav-item"><a class="nav-link" href="#api-reference">API Reference</a></li>
        <li class="nav-item"><a class="nav-link" href="#permissions">Permissions</a></li>
      </ul>
    </nav>
  </div>

</div>
{{% endblock %}}

{{% block extra_js %}}
{{{{ block.super }}}}
<script>
(function () {
  // Diagram zoom / pan / fullscreen
  function initDiagramZoom(wrap) {
    var scale = 1, tx = 0, ty = 0, startX, startY, dragging = false;
    var diagram = wrap.querySelector('.mermaid');
    var toolbar = wrap.previousElementSibling;
    if (!toolbar || !toolbar.classList.contains('diagram-zoom-toolbar')) return;

    function applyTransform() {{
      diagram.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
      var zlabel = toolbar.querySelector('.zoom-level');
      if (zlabel) zlabel.textContent = Math.round(scale * 100) + '%';
    }}

    toolbar.querySelector('.zoom-in').addEventListener('click', function () {{ scale = Math.min(scale * 1.25, 5); applyTransform(); }});
    toolbar.querySelector('.zoom-out').addEventListener('click', function () {{ scale = Math.max(scale * 0.8, 0.2); applyTransform(); }});
    toolbar.querySelector('.zoom-reset').addEventListener('click', function () {{ scale = 1; tx = 0; ty = 0; applyTransform(); }});

    var fsBtn = toolbar.querySelector('.zoom-fullscreen');
    if (fsBtn) {{
      fsBtn.addEventListener('click', function () {{
        var overlay = document.createElement('div');
        overlay.className = 'diagram-fs-overlay';
        var newToolbar = toolbar.cloneNode(true);
        var newWrap = wrap.cloneNode(true);
        newWrap.style.height = '';
        overlay.appendChild(newToolbar);
        overlay.appendChild(newWrap);
        document.body.appendChild(overlay);
        mermaid.run({{ nodes: newWrap.querySelectorAll('.mermaid') }});
        initDiagramZoom(newWrap);
        newToolbar.querySelector('.zoom-fullscreen').addEventListener('click', function () {{
          document.body.removeChild(overlay);
        }});
      }});
    }}

    wrap.addEventListener('wheel', function (e) {{
      e.preventDefault();
      scale = e.deltaY < 0 ? Math.min(scale * 1.15, 5) : Math.max(scale * 0.87, 0.2);
      applyTransform();
    }}, {{ passive: false }});

    wrap.addEventListener('mousedown', function (e) {{ dragging = true; startX = e.clientX - tx; startY = e.clientY - ty; wrap.classList.add('is-grabbing'); }});
    document.addEventListener('mousemove', function (e) {{ if (!dragging) return; tx = e.clientX - startX; ty = e.clientY - startY; applyTransform(); }});
    document.addEventListener('mouseup', function () {{ dragging = false; wrap.classList.remove('is-grabbing'); }});
  }

  document.querySelectorAll('.diagram-zoom-wrap').forEach(function (w) {{ initDiagramZoom(w); }});

  // TOC active link on scroll
  var sections = document.querySelectorAll('.docs-section');
  var tocLinks = document.querySelectorAll('.docs-toc .nav-link');
  window.addEventListener('scroll', function () {{
    var scrollY = window.scrollY + 100;
    var current = '';
    sections.forEach(function (s) {{ if (scrollY >= s.offsetTop) current = s.id; }});
    tocLinks.forEach(function (a) {{
      a.classList.toggle('active', a.getAttribute('href') === '#' + current);
    }});
  }});
}());
</script>
{{% endblock %}}
"""

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(HTML)
print(f"Written {len(HTML):,} chars to {TARGET}")
