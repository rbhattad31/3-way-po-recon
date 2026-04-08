# Procurement Intelligence Platform — Project Documentation

> **Version**: 1.0 · **Last Updated**: March 2026  
> **Stack**: Django 4.2 · MySQL · Celery + Redis · Azure OpenAI · Bootstrap 5  
> **App**: `apps.procurement`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Models](#3-data-models)
4. [Business Enumerations](#4-business-enumerations)
5. [Service Layer](#5-service-layer)
6. [Agent System](#6-agent-system)
7. [API Reference](#7-api-reference)
8. [Template Views & UI](#8-template-views--ui)
9. [Celery Tasks](#9-celery-tasks)
10. [Governance & Audit Integration](#10-governance--audit-integration)
11. [Observability Integration](#11-observability-integration)
12. [RBAC & Permissions](#12-rbac--permissions)
13. [File Organization](#13-file-organization)
14. [Status Transitions](#14-status-transitions)
15. [Flow Walkthroughs](#15-flow-walkthroughs)
16. [Configuration & Extension Points](#16-configuration--extension-points)

---

## 1. Executive Summary

The **Procurement Intelligence Platform** is a generic, domain-agnostic module built on top of the existing Django enterprise stack. All procurement models (`ProcurementRequest`, `ProcurementRecommendation`, quotation-related models) are tenant-scoped via the `CompanyProfile` FK (see [MULTI_TENANT.md](MULTI_TENANT.md)). It supports three primary analysis flows:

| Flow | Description |
|---|---|
| **Product / Solution Recommendation** | Given a set of requirements (attributes), apply deterministic rules and optionally invoke AI to recommend the best product or solution |
| **Should-Cost Benchmarking** | Given supplier quotations with line items, resolve market benchmark prices, compute variance, classify risk, and flag outliers |
| **Validation** | Given a procurement request with attributes/documents/quotations, run 6 deterministic validation dimensions (attribute completeness, document completeness, scope coverage, ambiguity detection, commercial completeness, compliance readiness) with optional AI augmentation for ambiguity resolution |

### Core Design Principles

- **Request-centric** — Uses `ProcurementRequest` + `AnalysisRun` hierarchy (NOT the existing AP case model)
- **Deterministic first** — Rule-based logic runs before any LLM invocation; AI is only called when rules are insufficient
- **Domain-agnostic** — `domain_code` and `schema_code` fields allow any business domain (HVAC, IT, Facilities, etc.) without hardcoded logic
- **Re-uses existing governance** — All audit logging, traceability, and observability use the existing platform services (no new governance modules)
- **Stateless services** — All business logic lives in service classes with static/class methods

### Business Flow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│  RECOMMENDATION FLOW                                            │
│                                                                 │
│  Create Request → Define Attributes → Mark Ready                │
│    → Create AnalysisRun(RECOMMENDATION)                         │
│    → Validate Attributes → Apply Rules → [Invoke AI if needed]  │
│    → Compliance Check → Save RecommendationResult               │
│    → Update Request Status                                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  BENCHMARK FLOW                                                 │
│                                                                 │
│  Create Request → Upload Quotation(s) → Add Line Items          │
│    → Normalize Line Items                                       │
│    → Create AnalysisRun(BENCHMARK)                              │
│    → Resolve Benchmark Prices → Compute Variance → Classify Risk│
│    → Save BenchmarkResult + BenchmarkResultLines                │
│    → Update Request Status                                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  VALIDATION FLOW                                                │
│                                                                 │
│  Create Request → Define Attributes → Upload Quotations         │
│    → Create AnalysisRun(VALIDATION)                             │
│    → Resolve Validation Rules (domain/schema-specific)          │
│    → Run 6 Deterministic Validators                             │
│    → [Optional AI Augmentation for ambiguity resolution]        │
│    → Score & Classify → Save ValidationResult + Items           │
│    → Update Request Status                                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Overview

### Hierarchy

```
ProcurementRequest (top-level business entity)
  ├── ProcurementRequestAttribute  (dynamic key-value requirements)
  ├── SupplierQuotation            (vendor quote with document link)
  │     └── QuotationLineItem      (individual priced items)
  └── AnalysisRun                  (execution instance — can have many per request)
        ├── RecommendationResult   (1:1 with RECOMMENDATION run)
        ├── BenchmarkResult        (1:N with BENCHMARK run per quotation)
        │     └── BenchmarkResultLine  (per-line comparison)
        ├── ComplianceResult       (1:1 compliance check output)
        └── ValidationResult       (1:1 with VALIDATION run)
              └── ValidationResultItem  (individual findings)

ValidationRuleSet (reusable rule definitions, domain/schema-scoped)
  └── ValidationRule (individual rules within a set)
```

### Layered Architecture

```
┌───────────────────────────────────────────────┐
│               UI Layer (Bootstrap 5)          │
│  request_list · request_create · workspace ·  │
│  run_detail · validation_summary partial      │
├───────────────────────────────────────────────┤
│               API Layer (DRF)                 │
│  ProcurementRequestViewSet (CRUD + actions)   │
│  SupplierQuotationViewSet                     │
│  ValidationRuleSetViewSet (read-only)         │
│  AnalysisRunValidationView                    │
├───────────────────────────────────────────────┤
│             Celery Tasks                      │
│  run_analysis_task · run_validation_task      │
├───────────────────────────────────────────────┤
│             Service Layer                     │
│  ProcurementRequestService · AttributeService │
│  QuotationService · LineItemNormalizationSvc  │
│  RecommendationService · BenchmarkService     │
│  ComplianceService · AnalysisRunService       │
├───────────────────────────────────────────────┤
│             Agent Layer                       │
│  RecommendationAgent · BenchmarkAgent         │
│  ComplianceAgent                              │
├───────────────────────────────────────────────┤
│        Existing Platform Services (REUSED)    │
│  AuditService · TraceContext · MetricsService │
│  LLMClient · @observed_service/task           │
│  RBAC · ProcessingLog · AuditEvent            │
└───────────────────────────────────────────────┘
```

---

## 3. Data Models

All models are defined in `apps/procurement/models.py`.

### 3.1 ProcurementRequest

The top-level business entity representing a procurement need.

| Field | Type | Notes |
|---|---|---|
| `request_id` | UUID | Auto-generated, unique, indexed |
| `title` | CharField(300) | Human-readable title |
| `description` | TextField | Detailed description of the procurement need |
| `domain_code` | CharField(100) | Business domain (e.g. `HVAC`, `IT`, `FACILITIES`), indexed |
| `schema_code` | CharField(100) | Attribute schema identifier for dynamic forms |
| `request_type` | CharField(20) | `RECOMMENDATION` / `BENCHMARK` / `BOTH` |
| `status` | CharField(20) | `DRAFT` → `READY` → `PROCESSING` → `COMPLETED` / `REVIEW_REQUIRED` / `FAILED` |
| `priority` | CharField(10) | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `geography_country` | CharField(100) | Country context |
| `geography_city` | CharField(100) | City context |
| `currency` | CharField(3) | Default `USD` |
| `assigned_to` | FK → User | Optional assignee |
| `trace_id` | CharField(64) | Distributed tracing correlation |
| `created_by` | FK → User | Inherited from `BaseModel` (via `AuditMixin`) |
| `updated_by` | FK → User | Inherited from `BaseModel` (via `AuditMixin`) |
| `created_at` | DateTimeField | Inherited from `BaseModel` (via `TimestampMixin`) |
| `updated_at` | DateTimeField | Inherited from `BaseModel` (via `TimestampMixin`) |

**Indexes**: `(status, request_type)`, `(domain_code, status)`, `request_id` (unique), `domain_code`

**Inherits**: `BaseModel` → `TimestampMixin` + `AuditMixin`

### 3.2 ProcurementRequestAttribute

Dynamic key-value attributes allowing domain-specific requirements without schema changes.

| Field | Type | Notes |
|---|---|---|
| `request` | FK → ProcurementRequest | CASCADE delete |
| `attribute_code` | CharField(120) | Machine-readable key (e.g. `cooling_capacity`, `budget`) |
| `attribute_label` | CharField(200) | Human-readable label |
| `data_type` | CharField(20) | `TEXT` / `NUMBER` / `BOOLEAN` / `JSON` / `DATE` / `SELECT` |
| `value_text` | TextField | Text value storage |
| `value_number` | Decimal(18,4) | Numeric value storage |
| `value_json` | JSONField | Complex value storage |
| `is_required` | BooleanField | Whether this attribute must be filled before marking READY |
| `normalized_value` | TextField | Normalized/canonical form of the value |

**Unique constraint**: `(request, attribute_code)`

**Inherits**: `TimestampMixin` only (lightweight join table)

### 3.3 SupplierQuotation

Supplier quotation attached to a procurement request. Can link to an uploaded document for extraction.

| Field | Type | Notes |
|---|---|---|
| `request` | FK → ProcurementRequest | CASCADE delete |
| `vendor_name` | CharField(300) | Supplier name, indexed |
| `quotation_number` | CharField(100) | Vendor's quote reference |
| `quotation_date` | DateField | Date on the quotation |
| `total_amount` | Decimal(18,2) | Total quoted amount |
| `currency` | CharField(3) | Default `USD` |
| `uploaded_document` | FK → DocumentUpload | Links to existing document upload system |
| `extraction_status` | CharField(20) | `PENDING` / `IN_PROGRESS` / `COMPLETED` / `FAILED` |
| `extraction_confidence` | FloatField | 0.0–1.0 confidence from extraction |

**Inherits**: `BaseModel`

**Cross-reference**: Links to `apps.documents.DocumentUpload` from the existing document pipeline.

### 3.4 QuotationLineItem

Individual priced line item from a supplier quotation.

| Field | Type | Notes |
|---|---|---|
| `quotation` | FK → SupplierQuotation | CASCADE delete |
| `line_number` | PositiveIntegerField | Sequential line number |
| `description` | TextField | Raw description from quotation |
| `normalized_description` | TextField | Cleaned/normalized description |
| `category_code` | CharField(100) | Product/service category |
| `quantity` | Decimal(14,4) | Default 1 |
| `unit` | CharField(50) | Unit of measure (default `EA`) |
| `unit_rate` | Decimal(18,4) | Per-unit price |
| `total_amount` | Decimal(18,2) | `quantity × unit_rate` |
| `brand` | CharField(200) | Brand name if applicable |
| `model` | CharField(200) | Model number if applicable |
| `extraction_confidence` | FloatField | Per-line extraction confidence |

**Unique constraint**: `(quotation, line_number)`

**Inherits**: `TimestampMixin`

### 3.5 AnalysisRun

A single execution of an analysis. Each `ProcurementRequest` can have multiple runs (re-runs, different types).

| Field | Type | Notes |
|---|---|---|
| `run_id` | UUID | Auto-generated, unique, indexed |
| `request` | FK → ProcurementRequest | CASCADE delete |
| `run_type` | CharField(20) | `RECOMMENDATION` / `BENCHMARK` |
| `status` | CharField(20) | `QUEUED` → `RUNNING` → `COMPLETED` / `FAILED` |
| `started_at` | DateTimeField | When execution began |
| `completed_at` | DateTimeField | When execution finished |
| `triggered_by` | FK → User | Who initiated the run |
| `input_snapshot_json` | JSONField | Frozen copy of request attributes at run time |
| `output_summary` | TextField | Human-readable summary of results |
| `confidence_score` | FloatField | Overall confidence (0.0–1.0) |
| `trace_id` | CharField(64) | Distributed tracing correlation |
| `error_message` | TextField | Error details if FAILED |

**Computed property**: `duration_ms` — calculated from `started_at` / `completed_at`

**Index**: `(request, run_type, status)`

**Inherits**: `BaseModel`

### 3.6 RecommendationResult

Output of a recommendation analysis run (1:1 with AnalysisRun).

| Field | Type | Notes |
|---|---|---|
| `run` | OneToOne → AnalysisRun | CASCADE delete |
| `recommended_option` | CharField(500) | The recommended product/solution |
| `reasoning_summary` | TextField | Plain-text explanation |
| `reasoning_details_json` | JSONField | Structured reasoning (source, rules evaluated, etc.) |
| `confidence_score` | FloatField | Recommendation confidence |
| `constraints_json` | JSONField | Constraints considered |
| `compliance_status` | CharField(20) | `PASS` / `FAIL` / `PARTIAL` / `NOT_CHECKED` |
| `output_payload_json` | JSONField | Full structured output from rules + AI |

**Inherits**: `TimestampMixin`

### 3.7 BenchmarkResult

Header-level benchmark output per quotation (many per run if multiple quotations).

| Field | Type | Notes |
|---|---|---|
| `run` | FK → AnalysisRun | CASCADE delete |
| `quotation` | FK → SupplierQuotation | CASCADE delete |
| `total_quoted_amount` | Decimal(18,2) | Sum of quoted line items |
| `total_benchmark_amount` | Decimal(18,2) | Sum of benchmark averages |
| `variance_pct` | Decimal(8,2) | Overall variance percentage |
| `risk_level` | CharField(20) | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `summary_json` | JSONField | Aggregated summary data |

**Unique constraint**: `(run, quotation)`

**Inherits**: `TimestampMixin`

### 3.8 BenchmarkResultLine

Per-line-item benchmark comparison.

| Field | Type | Notes |
|---|---|---|
| `benchmark_result` | FK → BenchmarkResult | CASCADE delete |
| `quotation_line` | FK → QuotationLineItem | CASCADE delete |
| `benchmark_min` | Decimal(18,4) | Market minimum price |
| `benchmark_avg` | Decimal(18,4) | Market average price |
| `benchmark_max` | Decimal(18,4) | Market maximum price |
| `quoted_value` | Decimal(18,4) | Quoted unit rate |
| `variance_pct` | Decimal(8,2) | `(quoted - avg) / avg × 100` |
| `variance_status` | CharField(30) | `WITHIN_RANGE` / `ABOVE_BENCHMARK` / `BELOW_BENCHMARK` / `SIGNIFICANTLY_ABOVE` |
| `remarks` | TextField | Notes or explanations |

**Inherits**: `TimestampMixin`

### 3.9 ComplianceResult

Compliance check output attached to an analysis run (1:1).

| Field | Type | Notes |
|---|---|---|
| `run` | OneToOne → AnalysisRun | CASCADE delete |
| `compliance_status` | CharField(20) | `PASS` / `FAIL` / `PARTIAL` / `NOT_CHECKED` |
| `rules_checked_json` | JSONField | List of `{rule, description}` dicts |
| `violations_json` | JSONField | List of `{rule, detail}` dicts |
| `recommendations_json` | JSONField | List of remediation suggestions |

**Inherits**: `TimestampMixin`

### 3.10 ValidationRuleSet

Reusable set of validation rules scoped to a domain and/or schema.

| Field | Type | Notes |
|---|---|---|
| `domain_code` | CharField(100) | Business domain (blank = generic / all domains), indexed |
| `schema_code` | CharField(100) | Attribute schema identifier (blank = all schemas) |
| `rule_set_code` | CharField(120) | Unique identifier, indexed |
| `rule_set_name` | CharField(300) | Human-readable name |
| `description` | TextField | Optional description |
| `validation_type` | CharField(40) | `ATTRIBUTE_COMPLETENESS` / `DOCUMENT_COMPLETENESS` / `SCOPE_COVERAGE` / `AMBIGUITY_CHECK` / `COMMERCIAL_COMPLETENESS` / `COMPLIANCE_READINESS` |
| `is_active` | BooleanField | Default `True`, indexed |
| `priority` | PositiveIntegerField | Ordering priority (lower = higher priority), default 100 |
| `config_json` | JSONField | Domain-specific config (expected docs, categories, commercial terms) |

**Indexes**: `(domain_code, validation_type, is_active)`

**Inherits**: `BaseModel`

### 3.11 ValidationRule

Individual validation rule within a rule set.

| Field | Type | Notes |
|---|---|---|
| `rule_set` | FK → ValidationRuleSet | CASCADE delete |
| `rule_code` | CharField(120) | Code within its set, indexed |
| `rule_name` | CharField(300) | Human-readable name |
| `rule_type` | CharField(30) | `REQUIRED_ATTRIBUTE` / `REQUIRED_DOCUMENT` / `REQUIRED_CATEGORY` / `AMBIGUITY_PATTERN` / `COMMERCIAL_CHECK` / `COMPLIANCE_CHECK` |
| `severity` | CharField(20) | `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `is_active` | BooleanField | Default `True` |
| `evaluation_mode` | CharField(20) | `DETERMINISTIC` / `AGENT_ASSISTED` |
| `condition_json` | JSONField | Evaluation conditions (attribute_code, pattern, etc.) |
| `expected_value_json` | JSONField | Expected value or pattern for comparison |
| `failure_message` | CharField(500) | Message shown on rule failure |
| `remediation_hint` | CharField(500) | Suggested fix |
| `display_order` | PositiveIntegerField | Display ordering |

**Unique constraint**: `(rule_set, rule_code)`

**Inherits**: `TimestampMixin`

### 3.12 ValidationResult

Top-level output of a validation run (1:1 with AnalysisRun).

| Field | Type | Notes |
|---|---|---|
| `run` | OneToOne → AnalysisRun | CASCADE delete |
| `validation_type` | CharField(40) | Primary validation type (default: `ATTRIBUTE_COMPLETENESS` for combined runs) |
| `overall_status` | CharField(30) | `PASS` / `PASS_WITH_WARNINGS` / `REVIEW_REQUIRED` / `FAIL` |
| `completeness_score` | FloatField | 0–100 percentage |
| `summary_text` | TextField | Human-readable summary |
| `readiness_for_recommendation` | BooleanField | Whether request is ready for recommendation analysis |
| `readiness_for_benchmarking` | BooleanField | Whether request is ready for benchmark analysis |
| `recommended_next_action` | CharField(40) | `READY_FOR_RECOMMENDATION` / `READY_FOR_BENCHMARKING` / `REQUEST_REFINEMENT` / `NEEDS_TECHNICAL_REVIEW` / `NEEDS_COMMERCIAL_REVIEW` |
| `missing_items_json` | JSONField | List of `{item_code, item_label, severity, remarks}` |
| `warnings_json` | JSONField | List of `{item_code, item_label, severity, remarks}` |
| `ambiguous_items_json` | JSONField | List of `{item_code, item_label, remarks}` |
| `output_payload_json` | JSONField | Full structured output for API consumers |

**Inherits**: `TimestampMixin`

### 3.13 ValidationResultItem

Individual finding within a validation result.

| Field | Type | Notes |
|---|---|---|
| `validation_result` | FK → ValidationResult | CASCADE delete |
| `item_code` | CharField(120) | Finding identifier |
| `item_label` | CharField(300) | Human-readable label |
| `category` | CharField(40) | Which validation dimension (uses `ValidationType` choices) |
| `status` | CharField(20) | `PRESENT` / `MISSING` / `WARNING` / `AMBIGUOUS` / `FAILED` |
| `severity` | CharField(20) | `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `source_type` | CharField(20) | `ATTRIBUTE` / `DOCUMENT` / `LINE_ITEM` / `RULE` / `AGENT` |
| `source_reference` | CharField(200) | Rule code, attribute code, or document reference |
| `remarks` | TextField | Human-readable notes |
| `details_json` | JSONField | Structured details |

**Inherits**: `TimestampMixin`

### Entity Relationship Diagram

```
User (accounts.User)
  ├── creates ──> ProcurementRequest (created_by)
  ├── assigned ──> ProcurementRequest (assigned_to)
  └── triggers ──> AnalysisRun (triggered_by)

ProcurementRequest
  ├── ──< ProcurementRequestAttribute (attributes)
  ├── ──< SupplierQuotation (quotations)
  │         └── ──< QuotationLineItem (line_items)
  └── ──< AnalysisRun (analysis_runs)
            ├── ── RecommendationResult (1:1, recommendation_result)
            ├── ──< BenchmarkResult (benchmark_results)
            │         └── ──< BenchmarkResultLine (lines)
            ├── ── ComplianceResult (1:1, compliance_result)
            └── ── ValidationResult (1:1, validation_result)
                      └── ──< ValidationResultItem (items)

ValidationRuleSet
  └── ──< ValidationRule (rules)

SupplierQuotation ── FK ──> DocumentUpload (existing documents app)
```

---

## 4. Business Enumerations

All procurement enums are defined in `apps/core/enums.py` (following existing project convention).

### ProcurementRequestType
| Value | Label |
|---|---|
| `RECOMMENDATION` | Product / Solution Recommendation |
| `BENCHMARK` | Should-Cost Benchmarking |
| `BOTH` | Recommendation + Benchmarking |

### ProcurementRequestStatus
| Value | Label |
|---|---|
| `DRAFT` | Draft — initial creation, attributes being defined |
| `READY` | Ready — attributes validated, ready for analysis |
| `PROCESSING` | Processing — analysis run in progress |
| `COMPLETED` | Completed — analysis finished successfully |
| `REVIEW_REQUIRED` | Review Required — compliance failure or high-risk benchmark |
| `FAILED` | Failed — analysis run failed |

### AnalysisRunType
| Value | Label |
|---|---|
| `RECOMMENDATION` | Recommendation analysis |
| `BENCHMARK` | Benchmark analysis |
| `VALIDATION` | Validation analysis |

### AnalysisRunStatus
| Value | Label |
|---|---|
| `QUEUED` | Queued — waiting for execution |
| `RUNNING` | Running — currently executing |
| `COMPLETED` | Completed — finished successfully |
| `FAILED` | Failed — execution error |

### ExtractionStatus
| Value | Label |
|---|---|
| `PENDING` | Pending |
| `IN_PROGRESS` | In Progress |
| `COMPLETED` | Completed |
| `FAILED` | Failed |

### ValidationType
| Value | Label |
|---|---|
| `ATTRIBUTE_COMPLETENESS` | Attribute Completeness |
| `DOCUMENT_COMPLETENESS` | Document Completeness |
| `SCOPE_COVERAGE` | Scope Coverage |
| `AMBIGUITY_CHECK` | Ambiguity Check |
| `COMMERCIAL_COMPLETENESS` | Commercial Completeness |
| `COMPLIANCE_READINESS` | Compliance Readiness |

### ValidationOverallStatus
| Value | Label |
|---|---|
| `PASS` | Pass |
| `PASS_WITH_WARNINGS` | Pass with Warnings |
| `REVIEW_REQUIRED` | Review Required |
| `FAIL` | Fail |

### ValidationRuleType
| Value | Label |
|---|---|
| `REQUIRED_ATTRIBUTE` | Required Attribute |
| `REQUIRED_DOCUMENT` | Required Document |
| `REQUIRED_CATEGORY` | Required Category |
| `AMBIGUITY_PATTERN` | Ambiguity Pattern |
| `COMMERCIAL_CHECK` | Commercial Check |
| `COMPLIANCE_CHECK` | Compliance Check |

### ValidationSeverity
| Value | Label |
|---|---|
| `INFO` | Info |
| `WARNING` | Warning |
| `ERROR` | Error |
| `CRITICAL` | Critical |

### ValidationEvaluationMode
| Value | Label |
|---|---|
| `DETERMINISTIC` | Deterministic |
| `AGENT_ASSISTED` | Agent-Assisted |

### ValidationItemStatus
| Value | Label |
|---|---|
| `PRESENT` | Present |
| `MISSING` | Missing |
| `WARNING` | Warning |
| `AMBIGUOUS` | Ambiguous |
| `FAILED` | Failed |

### ValidationSourceType
| Value | Label |
|---|---|
| `ATTRIBUTE` | Attribute |
| `DOCUMENT` | Document |
| `LINE_ITEM` | Line Item |
| `RULE` | Rule |
| `AGENT` | Agent |

### ValidationNextAction
| Value | Label |
|---|---|
| `READY_FOR_RECOMMENDATION` | Ready for Recommendation |
| `READY_FOR_BENCHMARKING` | Ready for Benchmarking |
| `REQUEST_REFINEMENT` | Request Refinement |
| `NEEDS_TECHNICAL_REVIEW` | Needs Technical Review |
| `NEEDS_COMMERCIAL_REVIEW` | Needs Commercial Review |

### ComplianceStatus
| Value | Label |
|---|---|
| `PASS` | Pass — all rules satisfied |
| `FAIL` | Fail — critical violations |
| `PARTIAL` | Partial — some violations (non-critical) |
| `NOT_CHECKED` | Not Checked — compliance not evaluated |

### VarianceStatus
| Value | Label |
|---|---|
| `WITHIN_RANGE` | Within Range |
| `ABOVE_BENCHMARK` | Above Benchmark |
| `BELOW_BENCHMARK` | Below Benchmark |
| `SIGNIFICANTLY_ABOVE` | Significantly Above (>30%) |

### BenchmarkRiskLevel
| Value | Label |
|---|---|
| `LOW` | ≤5% variance |
| `MEDIUM` | 5–15% variance |
| `HIGH` | 15–30% variance |
| `CRITICAL` | >30% variance |

### AttributeDataType
| Value | Label |
|---|---|
| `TEXT` | Text |
| `NUMBER` | Number |
| `BOOLEAN` | Boolean |
| `JSON` | JSON |
| `DATE` | Date |
| `SELECT` | Select (dropdown) |

---

## 5. Service Layer

All services are in `apps/procurement/services/`. They follow existing project conventions:
- Stateless classes with static/class methods
- Called by views/tasks (never directly from serializers)
- Each service method logs via existing `AuditService`
- Entry-point methods decorated with `@observed_service` for tracing

### 5.1 ProcurementRequestService

**File**: `apps/procurement/services/request_service.py`

| Method | Description |
|---|---|
| `create_request(...)` | Creates a `ProcurementRequest` with optional attributes. Logs `PROCUREMENT_REQUEST_CREATED` audit event. Decorated with `@observed_service`. |
| `update_status(request, new_status, user)` | Transitions request status. Logs `PROCUREMENT_REQUEST_STATUS_CHANGED` audit event with `status_before` / `status_after`. |
| `mark_ready(request, user)` | Validates all `is_required` attributes have values, then transitions status to `READY`. Raises `ValueError` if validation fails. |
| `get_request(request_id)` | Fetches by PK or UUID. |

### 5.2 AttributeService

**File**: `apps/procurement/services/request_service.py` (same file)

| Method | Description |
|---|---|
| `bulk_set_attributes(request, attributes)` | Upserts attributes (update-or-create by `attribute_code`). |
| `get_attributes_dict(request)` | Returns attributes as `{code: value}` dict with type-aware extraction (number/json/text). |

### 5.3 QuotationService

**File**: `apps/procurement/services/quotation_service.py`

| Method | Description |
|---|---|
| `create_quotation(...)` | Creates a `SupplierQuotation` linked to a request. Logs `QUOTATION_UPLOADED` audit event. |
| `add_line_items(quotation, items)` | Bulk-creates `QuotationLineItem` records from a list of dicts. |
| `update_extraction_status(quotation, status, confidence)` | Updates extraction pipeline status on quotation. |

### 5.4 LineItemNormalizationService

**File**: `apps/procurement/services/quotation_service.py` (same file)

| Method | Description |
|---|---|
| `normalize_line_items(quotation)` | Normalizes all line item descriptions (lowercase, strip, collapse whitespace). Returns count of items normalized. |
| `_normalize_description(description)` | Internal: basic text normalization. Extension point for domain-specific normalization. |

### 5.5 Quotation Document Prefill Pipeline

The prefill pipeline extracts structured data from uploaded supplier proposals/quotation PDFs using OCR + LLM.

#### Architecture

```
Quotation Upload (API: quotation_prefill)
    │
    ├─ Create DocumentUpload + SupplierQuotation (PENDING)
    ├─ Queue run_quotation_prefill_task
    │
    ▼
QuotationDocumentPrefillService.run_prefill(quotation)
    │
    ├─ Step 1: OCR (Azure Document Intelligence via InvoiceExtractionAdapter)
    ├─ Step 2: LLM Extraction (GPT-4o, up to 60K chars of OCR text)
    │          └─ System prompt requires JSON: header fields + line_items[] + commercial terms
    ├─ Step 3: Field Mapping (AttributeMappingService.map_quotation_fields)
    │          ├─ Header: vendor_name, quotation_number, quotation_date, total_amount, currency, subtotal
    │          ├─ Commercial: warranty_terms, payment_terms, delivery_terms, lead_time, etc.
    │          └─ Line Items: description, category_code, quantity, unit, unit_rate, total_amount, brand, model
    ├─ Step 4: Confidence Classification (high/low per field)
    ├─ Step 5: Store prefill_payload_json on quotation (status → REVIEW_PENDING)
    │
    ▼
User reviews extracted data in UI
    │
    ▼
PrefillReviewService.confirm_quotation_prefill(quotation, reviewed_data)
    │
    ├─ Persist header fields on SupplierQuotation
    ├─ Bulk-create QuotationLineItem records from confirmed line items
    └─ Set prefill_status → COMPLETED
```

#### Key Services

**File**: `apps/procurement/services/prefill/quotation_prefill_service.py`

| Method | Description |
|---|---|
| `run_prefill(quotation)` | Full pipeline: OCR → LLM → mapping → payload storage. Accepts up to 60K chars of OCR text to handle long proposals. |
| `_ocr_document(file_path)` | Delegates to `InvoiceExtractionAdapter._ocr_document()` (Azure Document Intelligence). |
| `_extract_quotation_data(ocr_text)` | LLM extraction with `max_tokens=8192`. Strips markdown code fences from response. |

**File**: `apps/procurement/services/prefill/attribute_mapping_service.py`

| Method | Description |
|---|---|
| `map_quotation_fields(extracted)` | Maps LLM output to canonical header fields, commercial terms, and line items via synonym dictionaries. |
| `classify_confidence(fields)` | Separates fields into high_confidence (≥0.7) and low_confidence (<0.7) groups. |

**File**: `apps/procurement/services/prefill/prefill_review_service.py`

| Method | Description |
|---|---|
| `confirm_quotation_prefill(quotation, reviewed_data)` | Atomic: updates header fields + bulk-creates `QuotationLineItem` records from user-confirmed data. |

**File**: `apps/procurement/services/prefill/prefill_status_service.py`

| Method | Description |
|---|---|
| `mark_quotation_in_progress(quotation)` | Sets `prefill_status` → `IN_PROGRESS`. |
| `mark_quotation_completed(quotation, confidence, payload)` | Sets `prefill_status` → `REVIEW_PENDING`, stores `prefill_payload_json`. |
| `mark_quotation_failed(quotation)` | Sets `prefill_status` → `FAILED`. |

**File**: `apps/procurement/agents/quotation_extraction_agent.py`

| Method | Description |
|---|---|
| `extract(ocr_text)` | Single-shot LLM call with structured JSON prompt. Extracts header + line items from OCR text (up to 60K chars). |

#### Important Notes

- **OCR text limit**: 60,000 characters (sufficient for 40+ page proposals). Long technical proposals often have pricing/licensing tables deep in the document.
- **Two-phase persistence**: Extraction stores data as JSON in `prefill_payload_json` (phase 1). Line items are NOT persisted to `QuotationLineItem` table until the user confirms (phase 2). This allows human review before commitment.
- **Line item sources**: The LLM is instructed to find line items in pricing tables, BOQ sections, licensing tables, cost breakdowns, and commercial schedules anywhere in the document.

### 5.6 AnalysisRunService

**File**: `apps/procurement/services/analysis_run_service.py`

Manages the full lifecycle of an `AnalysisRun`.

| Method | Description |
|---|---|
| `create_run(request, run_type, triggered_by)` | Creates run with `QUEUED` status, captures `input_snapshot_json` (request attributes frozen at creation time). Logs `ANALYSIS_RUN_CREATED`. |
| `start_run(run)` | Sets status to `RUNNING`, records `started_at`. Logs `ANALYSIS_RUN_STARTED`. |
| `complete_run(run, output_summary, confidence_score)` | Sets status to `COMPLETED`, records `completed_at`, summary, confidence. Logs `ANALYSIS_RUN_COMPLETED` with output snapshot. |
| `fail_run(run, error_message)` | Sets status to `FAILED`, records error. Logs `ANALYSIS_RUN_FAILED`. |

### 5.7 RecommendationService

**File**: `apps/procurement/services/recommendation_service.py`

Orchestrates the full recommendation flow. Decorated with `@observed_service`.

**`run_recommendation(request, run, use_ai=True)`** — steps:

1. **Start run** — calls `AnalysisRunService.start_run()`
2. **Gather attributes** — calls `AttributeService.get_attributes_dict()`
3. **Apply deterministic rules** — calls `_apply_rules()` (returns `{recommended_option, reasoning_summary, confident, constraints}`)
4. **Invoke AI** — if `use_ai=True` AND rules returned `confident=False`, calls `RecommendationAgent.execute()`
5. **Compliance check** — calls `ComplianceService.check_recommendation()` to validate the recommendation
6. **Persist result** — creates `RecommendationResult` + `ComplianceResult` in a transaction
7. **Finalize** — calls `AnalysisRunService.complete_run()`, updates request status to `COMPLETED` or `REVIEW_REQUIRED` (if compliance fails)

**Error path**: On exception, calls `AnalysisRunService.fail_run()` and sets request to `FAILED`.

**Extension point**: `_apply_rules()` is a static method that can be extended per domain with deterministic recommendation logic.

### 5.8 BenchmarkService

**File**: `apps/procurement/services/benchmark_service.py`

Orchestrates the should-cost benchmarking flow. Decorated with `@observed_service`.

**`run_benchmark(request, run, quotation, use_ai=True)`** — steps:

1. **Start run** — calls `AnalysisRunService.start_run()`
2. **Iterate line items** — for each `QuotationLineItem`:
   - **Resolve benchmark** — calls `_resolve_benchmark()` (tries `BenchmarkAgent` if `use_ai=True`, falls back to empty data)
   - **Compute variance** — calls `_compute_variance()` (calculates `(quoted - avg) / avg × 100`)
3. **Aggregate** — computes `total_quoted`, `total_benchmark`, overall `variance_pct`
4. **Classify risk** — calls `_classify_risk()`:
   - ≤5% → LOW
   - ≤15% → MEDIUM
   - ≤30% → HIGH
   - >30% → CRITICAL
5. **Persist** — creates `BenchmarkResult` + bulk-creates `BenchmarkResultLine` records in a transaction
6. **Finalize** — completes run, updates request status to `COMPLETED` (LOW/MEDIUM risk) or `REVIEW_REQUIRED` (HIGH/CRITICAL)

**Risk thresholds** (configurable constants):

```python
RISK_THRESHOLDS = {
    "low": Decimal("5.0"),
    "medium": Decimal("15.0"),
    "high": Decimal("30.0"),
}
```

### 5.9 ComplianceService

**File**: `apps/procurement/services/compliance_service.py`

Stateless rule-based compliance checking.

| Method | Description |
|---|---|
| `check_recommendation(request, recommendation)` | Checks: (1) recommendation present, (2) confidence ≥ 0.5, (3) budget constraint if `budget` attribute exists. Returns `{status, rules_checked, violations, recommendations}`. |
| `check_benchmark(request, benchmark_summary)` | Checks: overall variance ≤ 30%. Returns same structure. |

**Compliance status logic**:
- 0 violations → `PASS`
- 1 violation → `PARTIAL`
- 2+ violations → `FAIL`

### 5.10 ValidationRuleResolverService

**File**: `apps/procurement/services/validation/rule_resolver_service.py`

Resolves applicable validation rules for a procurement request based on domain and schema.

| Method | Description |
|---|---|
| `resolve_rule_sets(domain_code, schema_code, validation_type)` | Fetches active `ValidationRuleSet` records matching domain/schema with specificity ordering (exact match → domain-only → generic). |
| `resolve_rules(domain_code, schema_code, validation_type)` | Returns flat list of `ValidationRule` records from resolved rule sets. |
| `resolve_rules_for_request(request)` | Resolves rules across all 6 validation types for a given request. |

### 5.11 AttributeCompletenessValidationService

**File**: `apps/procurement/services/validation/attribute_completeness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Checks `REQUIRED_ATTRIBUTE` rules against request attributes. Validates presence and type for each required attribute. Returns list of finding dicts. |

### 5.12 DocumentCompletenessValidationService

**File**: `apps/procurement/services/validation/document_completeness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Checks `REQUIRED_DOCUMENT` rules. Maps document types (`QUOTATION`, `BOQ`, `SPECIFICATION`, etc.) to presence checks via quotation data. Returns findings. |

### 5.13 ScopeCoverageValidationService

**File**: `apps/procurement/services/validation/scope_coverage_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Compares expected categories from `REQUIRED_CATEGORY` rules and `config_json` against detected `category_code` values from `QuotationLineItem` records. Returns findings. |

### 5.14 AmbiguityValidationService

**File**: `apps/procurement/services/validation/ambiguity_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Scans request description, line item descriptions, and attribute values against configurable regex patterns. 12 default patterns ("as required", "lumpsum", "complete system", etc.) plus rule-defined patterns from `AMBIGUITY_PATTERN` rules. Returns findings with `AMBIGUOUS` status. |

### 5.15 CommercialCompletenessValidationService

**File**: `apps/procurement/services/validation/commercial_completeness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Keyword-based search for 8 default commercial terms (`WARRANTY`, `DELIVERY`, `PAYMENT_TERMS`, `TAXES`, `INSTALLATION`, `SUPPORT`, `LEAD_TIME`, `TESTING`) plus rule-defined checks from `COMMERCIAL_CHECK` rules. Returns findings. |

### 5.16 ComplianceReadinessValidationService

**File**: `apps/procurement/services/validation/compliance_readiness_service.py`

| Method | Description |
|---|---|
| `validate(request, rules)` | Evaluates `COMPLIANCE_CHECK` rules with check_types: `attribute`, `keyword`, `geography`. Returns findings. |

### 5.17 ValidationOrchestratorService

**File**: `apps/procurement/services/validation/orchestrator_service.py`

Central orchestrator for the full validation pipeline. Decorated with `@observed_service`.

**`run_validation(request, run, agent_enabled=False)`** — steps:

1. **Resolve rules** — calls `ValidationRuleResolverService.resolve_rules_for_request()`
2. **Run all deterministic validators** — calls all 6 validators (attribute, document, scope, ambiguity, commercial, compliance)
3. **Optional agent augmentation** — if `agent_enabled=True` AND ambiguity count ≥ 3, calls `ValidationAgentService.augment_findings()`
4. **Score and classify** — computes completeness score (severity-weighted: CRITICAL=3×, ERROR=2×, WARNING=0.5×, INFO=0×)
5. **Determine status** — `_determine_overall_status()` maps score + findings to `PASS`/`PASS_WITH_WARNINGS`/`REVIEW_REQUIRED`/`FAIL`
6. **Determine readiness** — `_determine_readiness()` checks if request is ready for recommendation and/or benchmarking
7. **Persist** — creates `ValidationResult` + bulk-creates `ValidationResultItem` records in a transaction
8. **Complete run** — calls `AnalysisRunService.complete_run()`, logs `VALIDATION_COMPLETED` audit event

**Status classification**:
- Any CRITICAL missing → `FAIL`
- Score < 70 → `FAIL`
- Score < 90 with warnings → `REVIEW_REQUIRED`
- Score < 95 with warnings → `PASS_WITH_WARNINGS`
- Score ≥ 95 → `PASS`

---

## 6. Agent System

Three lightweight agents in `apps/procurement/agents/`. They follow a simple prompt → response pattern (no ReAct tool-calling loop needed for V1).

All agents use the existing `LLMClient` from `apps.agents.services.llm_client`.

### 6.1 RecommendationAgent

**File**: `apps/procurement/agents/recommendation_agent.py`

Called by `RecommendationService` when deterministic rules return `confident=False`.

**Input**: Domain code, title, description, geography, currency, attributes dict, rule engine result.

**Output**: JSON with `recommended_option`, `reasoning_summary`, `reasoning_details`, `confidence`, `constraints`, `confident`.

**System prompt**: Instructs the LLM to act as a procurement intelligence assistant and respond with structured JSON.

### 6.2 BenchmarkAgent

**File**: `apps/procurement/agents/benchmark_agent.py`

Called by `BenchmarkService._resolve_benchmark()` per line item when no deterministic benchmark data is available.

**Input**: Item description, normalized description, category, brand, model, quantity, unit, quoted rate, currency.

**Output**: JSON with `min`, `avg`, `max`, `source`, `reasoning`.

**System prompt**: Instructs the LLM to act as a procurement cost analyst and estimate market benchmark price ranges.

### 6.3 ComplianceAgent

**File**: `apps/procurement/agents/compliance_agent.py`

Extension point for AI-augmented compliance checking (e.g., checking domain-specific regulations).

**Input**: Domain code, geography, context dict (recommendation or benchmark data).

**Output**: JSON with `status`, `rules_checked`, `violations`, `recommendations`.

### Agent Design Principles

- **Deterministic first**: Agents are only called when rule-based logic cannot produce a confident answer
- **Fail-safe**: All agents catch exceptions and return graceful fallback responses
- **Logging**: Failures are logged via standard Python logging
- **Existing LLM infrastructure**: All agents use `LLMClient` which supports both Azure OpenAI and OpenAI (configured via `LLM_PROVIDER` setting)

### 6.4 ValidationAgentService

**File**: `apps/procurement/services/validation/validation_agent.py`

Lightweight LLM agent for ambiguity resolution. Only invoked when deterministic validation identifies 3+ ambiguous items.

**`augment_findings(request, run, findings)`** — steps:

1. Filters ambiguous items from findings
2. Creates `AgentRun` record for traceability
3. Sends ambiguous items to LLM with system prompt requesting JSON classification
4. Parses LLM response and applies resolutions back to findings (updates status, remarks, source_type)
5. Logs `AgentStep` record with resolution details
6. Falls back to deterministic results on any LLM error

**Design principles**:
- Does NOT replace deterministic checks — it augments them
- Creates `AgentRun`/`AgentStep` records for full auditability
- Graceful fallback on failure (original findings preserved)

---

## 7. API Reference

All APIs are mounted under `/api/v1/procurement/`.

### 7.1 ProcurementRequestViewSet

**Base URL**: `/api/v1/procurement/requests/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/requests/` | List all requests (paginated, filterable) |
| `POST` | `/requests/` | Create new request (with inline attributes) |
| `GET` | `/requests/{id}/` | Get request detail (with attributes, quotations, runs) |
| `PUT/PATCH` | `/requests/{id}/` | Update request |
| `DELETE` | `/requests/{id}/` | Delete request |
| `GET` | `/requests/{id}/attributes/` | List attributes for a request |
| `POST` | `/requests/{id}/attributes/` | Bulk set attributes |
| `GET` | `/requests/{id}/runs/` | List analysis runs |
| `POST` | `/requests/{id}/runs/` | Trigger new analysis run (`{"run_type": "RECOMMENDATION" or "BENCHMARK" or "VALIDATION"}`) |
| `GET` | `/requests/{id}/recommendation/` | Get latest recommendation result |
| `GET` | `/requests/{id}/benchmark/` | Get all benchmark results |
| `POST` | `/requests/{id}/validate/` | Trigger validation run (creates `AnalysisRun(VALIDATION)` and dispatches task) |
| `GET` | `/requests/{id}/validation/` | Get latest validation result with items |

**Filters** (via `DjangoFilterBackend`): `status`, `request_type`, `domain_code`, `priority`

**Search** (via `SearchFilter`): `title`, `description`, `domain_code`

**Ordering** (via `OrderingFilter`): `created_at`, `updated_at`, `priority`, `status`

**Serializers**:
- **List**: `ProcurementRequestListSerializer` — lightweight with counts (`attribute_count`, `quotation_count`, `run_count`)
- **Detail**: `ProcurementRequestDetailSerializer` — full with nested `attributes`, `quotations`, `analysis_runs`
- **Write**: `ProcurementRequestWriteSerializer` — accepts inline `attributes` array, calls `ProcurementRequestService.create_request()`

### 7.2 SupplierQuotationViewSet

**Base URL**: `/api/v1/procurement/quotations/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/quotations/` | List all quotations |
| `POST` | `/quotations/` | Create quotation |
| `GET` | `/quotations/{id}/` | Get quotation detail (with line items) |
| `PUT/PATCH` | `/quotations/{id}/` | Update quotation |
| `DELETE` | `/quotations/{id}/` | Delete quotation |

**Filters**: `extraction_status`, `currency`, `request`

**Serializers**:
- **List**: `SupplierQuotationListSerializer` — with `line_item_count`
- **Detail**: `SupplierQuotationDetailSerializer` — full with nested `line_items`

### 7.3 ValidationRuleSetViewSet

**Base URL**: `/api/v1/procurement/validation/rulesets/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/validation/rulesets/` | List all validation rule sets |
| `GET` | `/validation/rulesets/{id}/` | Get rule set detail with nested rules |

**Filters**: `domain_code`, `schema_code`, `validation_type`, `is_active`

**Search**: `rule_set_code`, `rule_set_name`

**Serializers**:
- **List**: `ValidationRuleSetListSerializer` — with `rule_count`
- **Detail**: `ValidationRuleSetSerializer` — full with nested `rules`

### 7.4 AnalysisRunValidationView

**URL**: `/api/v1/procurement/runs/{id}/validation/`

| Method | URL | Description |
|---|---|---|
| `GET` | `/runs/{id}/validation/` | Get validation result for a specific analysis run |

**Serializers**: `ValidationResultSerializer` with nested `ValidationResultItemSerializer`

### Authentication

All endpoints require authentication (`permissions.IsAuthenticated`). RBAC permission classes (`HasPermissionCode`) are available for finer-grained control — see [Section 12](#12-rbac--permissions).

---

## 8. Template Views & UI

All template views are in `apps/procurement/template_views.py`. URLs are in `apps/procurement/urls.py` with `app_name = "procurement"`.

### 8.1 Request List (`/procurement/`)

**View**: `request_list`  
**Template**: `templates/procurement/request_list.html`

Features:
- Paginated table (25 per page)
- Filter by: status, request type, domain code, search text
- Status badges (color-coded per status)
- Priority indicators (colored dots)
- Attribute, quotation, and run counts per request
- "New Request" button

### 8.2 Create Request (`/procurement/create/`)

**View**: `request_create`  
**Template**: `templates/procurement/request_create.html`

Features:
- Core fields: title, description, domain code, schema code, request type, priority
- Geography: country, city, currency
- Dynamic attribute form: add/remove attribute rows with code, label, type, value
- JavaScript for adding dynamic attribute rows
- Redirects to workspace on success

### 8.3 Request Workspace (`/procurement/{id}/`)

**View**: `request_workspace`  
**Template**: `templates/procurement/request_workspace.html`

The primary workspace for a procurement request. Sections:

| Section | Description |
|---|---|
| **Request Summary** | Title, description, status badge, type, domain, priority, geography, currency, trace ID. Action buttons: "Mark Ready" (if DRAFT), "Run Analysis" with type selector (if READY/COMPLETED/REVIEW_REQUIRED). |
| **Attributes** | Table of all `ProcurementRequestAttribute` records (code, label, type, value). |
| **Validation Summary** | Latest `ValidationResult`: overall status badge, completeness progress bar, summary text, readiness indicators (recommendation/benchmarking), next action recommendation, missing items accordion, warnings accordion, ambiguous items accordion, detailed findings table accordion, last-validated footer with trace ID. Included via `{% include "procurement/partials/validation_summary.html" %}`. |
| **Recommendation** | Latest `RecommendationResult`: recommended option, reasoning, confidence percentage, compliance badge. |
| **Benchmark Results** | All `BenchmarkResult` records: vendor name, risk badge, quoted/benchmark/variance summary, line-level comparison table. |
| **Compliance** | Latest `ComplianceResult`: status badge, violations list. |
| **Quotations** (right column) | List of quotations with vendor name, amount, extraction status. Collapsible form to add new quotation. |
| **Analysis Runs** (right column) | Linked list to `run_detail` view. Shows type icon (including VALIDATION → `bi-check2-square`), status badge, date, confidence. |
| **Activity Timeline** (right column) | Uses existing `AuditService.fetch_entity_history("ProcurementRequest", pk)` to show all governance events. |

### 8.4 Analysis Run Detail (`/procurement/run/{id}/`)

**View**: `run_detail`  
**Template**: `templates/procurement/run_detail.html`

Features:
- Run metadata: run ID, status, confidence, start/end time, duration, triggered by, trace ID
- Output summary / error message
- Input snapshot (pretty-printed JSON)
- Recommendation result (if RECOMMENDATION type): option, reasoning, confidence, compliance, reasoning details (collapsible)
- Benchmark results (if BENCHMARK type): vendor, quoted/benchmark/variance/risk, line-level table
- Validation result (if VALIDATION type): overall status, completeness score, summary, findings
- Compliance result: status, violations list
- Audit trail: events from `AuditService.fetch_entity_history("AnalysisRun", pk)`

### 8.5 Action Views

| URL | Method | View | Description |
|---|---|---|---|
| `/procurement/{id}/trigger/` | POST | `trigger_analysis` | Creates `AnalysisRun` and fires `run_analysis_task` Celery task |
| `/procurement/{id}/ready/` | POST | `mark_ready` | Validates required attributes and sets status to `READY` |
| `/procurement/{id}/quotation/` | POST | `upload_quotation` | Creates `SupplierQuotation` from form data |
| `/procurement/{id}/validate/` | POST | `trigger_validation` | Creates `AnalysisRun(VALIDATION)` and fires `run_validation_task` Celery task |

### Sidebar Navigation

A new "Procurement" section is added to the global sidebar (`templates/partials/sidebar.html`) between the copilot/dashboard entries and the Documents section:

```html
{# ── Procurement ── #}
<li class="nav-item">
  <a class="nav-link" href="{% url 'procurement:request_list' %}">
    <i class="bi bi-cart4 me-2"></i>Requests
  </a>
</li>
```

---

## 9. Celery Tasks

**File**: `apps/procurement/tasks.py`

### `run_analysis_task(run_id: int) → dict`

**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`  
**Observability**: `@observed_task("procurement.run_analysis", audit_event="ANALYSIS_RUN_STARTED", entity_type="AnalysisRun")`

**Behavior**:
1. Loads the `AnalysisRun` with its related `ProcurementRequest`
2. Sets request status to `PROCESSING`
3. Dispatches based on `run_type`:
   - `RECOMMENDATION` → `RecommendationService.run_recommendation(request, run)`
   - `BENCHMARK` → `BenchmarkService.run_benchmark(request, run, quotation)` (uses first quotation)
   - `VALIDATION` → `ValidationOrchestratorService.run_validation(request, run)`
4. Returns structured result dict with status, run_id, type-specific data

**Error handling**: Catches exceptions and returns `{"status": "failed", "error": "..."}`.

**Execution mode**: In development on Windows, runs synchronously via `CELERY_TASK_ALWAYS_EAGER=True` (existing setting). In production, runs asynchronously with Redis broker.

### `run_validation_task(run_id: int) → dict`

**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`  
**Observability**: `@observed_task("procurement.run_validation", audit_event="VALIDATION_RUN_STARTED", entity_type="AnalysisRun")`

**Behavior**:
1. Loads the `AnalysisRun` with its related `ProcurementRequest`
2. Calls `ValidationOrchestratorService.run_validation(request, run)`
3. Updates request status based on validation outcome:
   - `PASS` → `READY`
   - `FAIL` → `FAILED`
   - `REVIEW_REQUIRED` → `REVIEW_REQUIRED`
4. Returns structured result dict with status, completeness_score, overall_status

### `run_quotation_prefill_task(quotation_id: int) → dict`

**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`  
**Observability**: `@observed_task("procurement.quotation_prefill", audit_event="PREFILL_STARTED", entity_type="SupplierQuotation")`

**Behavior**:
1. Loads the `SupplierQuotation` with its related `uploaded_document` and `request`
2. Calls `QuotationDocumentPrefillService.run_prefill(quotation)`
3. Returns structured result dict with status, quotation_id, prefill_status, line_item_count

**Error handling**: Catches exceptions and returns `{"status": "failed", "error": "..."}`. Quotation `extraction_status` set to `FAILED`.

---

## 10. Governance & Audit Integration

The procurement platform **reuses existing governance infrastructure** — no new audit modules were created.

### Existing Services Used

| Service | Module | Usage in Procurement |
|---|---|---|
| **AuditService** | `apps.auditlog.services` | All business events are logged via `AuditService.log_event()` |
| **AuditEvent** | `apps.auditlog.models` | Events are stored as `AuditEvent` records with full RBAC snapshot |
| **ProcessingLog** | `apps.auditlog.models` | Operational logs written by `@observed_service` decorator |
| **TraceContext** | `apps.core.trace` | Distributed tracing with `trace_id` / `span_id` propagation |
| **AuditService.fetch_entity_history()** | `apps.auditlog.services` | Used by workspace and run detail views for activity timeline |

### Audit Events Emitted

Every significant action in the procurement flow logs an `AuditEvent`:

| Event Type | Entity Type | Triggered By |
|---|---|---|
| `PROCUREMENT_REQUEST_CREATED` | `ProcurementRequest` | `ProcurementRequestService.create_request()` |
| `PROCUREMENT_REQUEST_STATUS_CHANGED` | `ProcurementRequest` | `ProcurementRequestService.update_status()` |
| `QUOTATION_UPLOADED` | `SupplierQuotation` | `QuotationService.create_quotation()` |
| `ANALYSIS_RUN_CREATED` | `AnalysisRun` | `AnalysisRunService.create_run()` |
| `ANALYSIS_RUN_STARTED` | `AnalysisRun` | `AnalysisRunService.start_run()` |
| `ANALYSIS_RUN_COMPLETED` | `AnalysisRun` | `AnalysisRunService.complete_run()` |
| `ANALYSIS_RUN_FAILED` | `AnalysisRun` | `AnalysisRunService.fail_run()` |
| `VALIDATION_RUN_STARTED` | `AnalysisRun` | `run_validation_task` (via `@observed_task`) |
| `VALIDATION_COMPLETED` | `ProcurementRequest` | `ValidationOrchestratorService.run_validation()` |

### Audit Event Fields Populated

Each audit event includes:

- `entity_type` + `entity_id` — which object was affected
- `event_type` — what happened
- `description` — human-readable summary
- `user` — who performed the action (Django User)
- `trace_ctx` — TraceContext for distributed tracing correlation
- `status_before` / `status_after` — for state transitions
- `output_snapshot` — redacted payload snapshot (for completed runs)
- `error_code` — for failure events

### Activity Timeline in UI

Both the **Request Workspace** and **Run Detail** views display an activity timeline powered by `AuditService.fetch_entity_history()`:

```python
# In request_workspace view:
audit_events = AuditService.fetch_entity_history("ProcurementRequest", proc_request.pk)

# In run_detail view:
audit_events = AuditService.fetch_entity_history("AnalysisRun", run.pk)
```

This provides a chronological log of all governance events for each entity without any new governance UI or modules.

---

## 11. Observability Integration

The procurement platform uses the existing observability infrastructure.

### Decorators Applied

| Decorator | Applied To | Effect |
|---|---|---|
| `@observed_service(...)` | `create_request`, `create_quotation`, `run_recommendation`, `run_benchmark`, `create_run`, `run_validation` | Creates child trace spans, measures duration, writes `ProcessingLog` |
| `@observed_task(...)` | `run_analysis_task`, `run_validation_task` | Trace propagation via Celery headers, writes `ProcessingLog`, emits audit event |

### Trace Propagation

1. **Request** → `RequestTraceMiddleware` creates root `TraceContext`
2. **Service** → `@observed_service` creates child span
3. **Task** → `@observed_task` propagates trace via Celery headers
4. **Nested services** — child spans preserve parent `trace_id`
5. **Models** — `ProcurementRequest.trace_id` and `AnalysisRun.trace_id` store the trace ID for cross-referencing

---

## 12. RBAC & Permissions

The procurement platform has its own RBAC roles, permissions, and role-permission matrix — fully integrated with the existing platform RBAC system.

### Roles

Three procurement-specific roles were added (seeded via `python manage.py seed_rbac`):

| Role Code | Name | Rank | Description |
|---|---|---|---|
| `PROCUREMENT_MANAGER` | Procurement Manager | 25 | Supervise procurement operations, review high-risk results, full control including delete |
| `CATEGORY_MANAGER` | Category Manager | 35 | Domain expert — manage category-specific rules, benchmarks, review results within their domain |
| `PROCUREMENT_BUYER` | Procurement Buyer | 55 | Operational buyer — create requests, upload quotations, trigger analysis |

These are separate from AP roles (AP_PROCESSOR, REVIEWER) because procurement teams are typically different from accounts payable teams.

### Permissions

Eight procurement permissions (module: `procurement`):

| Permission Code | Name | Description |
|---|---|---|
| `procurement.view` | View Procurement Requests | View requests, attributes, and quotations |
| `procurement.create` | Create Procurement Requests | Create new procurement requests |
| `procurement.edit` | Edit Procurement Requests | Edit requests and manage attributes |
| `procurement.delete` | Delete Procurement Requests | Delete procurement requests |
| `procurement.run_analysis` | Run Procurement Analysis | Trigger recommendation and benchmark analysis runs |
| `procurement.manage_quotations` | Manage Quotations | Upload and manage supplier quotations |
| `procurement.view_results` | View Analysis Results | View recommendation, benchmark, compliance, and validation results |
| `procurement.validate` | Run Validation | Trigger validation analysis runs |

### Role-Permission Matrix

| Permission | ADMIN | PROC_MGR | CAT_MGR | PROC_BUYER | FIN_MGR | AUDITOR | REVIEWER | AP_PROC | SYS_AGENT |
|---|---|---|---|---|---|---|---|---|---|
| `procurement.view` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | ✅ |
| `procurement.create` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | — |
| `procurement.edit` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | — |
| `procurement.delete` | ✅ | ✅ | — | — | — | — | — | — | — |
| `procurement.run_analysis` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | ✅ |
| `procurement.manage_quotations` | ✅ | ✅ | — | ✅ | — | — | — | — | — |
| `procurement.view_results` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | ✅ |
| `procurement.validate` | ✅ | ✅ | ✅ | ✅ | — | — | — | — | ✅ |

**Rationale**:
- **ADMIN** — all permissions (auto-granted from PERMISSIONS list)
- **PROCUREMENT_MANAGER** — full control including delete (supervisory)
- **CATEGORY_MANAGER** — create/edit/run/view but no delete or quotation management
- **PROCUREMENT_BUYER** — operational: create, edit, run, manage quotations, but no delete
- **FINANCE_MANAGER** — cross-functional oversight: view + view results only
- **AUDITOR** — read-only: view + view results
- **SYSTEM_AGENT** — automated pipeline: view, run_analysis, view_results

### Enforcement

#### API Views (`apps/procurement/views.py`)

Both ViewSets use `get_permissions()` to route each action to the correct permission:

| ViewSet | Action | Permission |
|---|---|---|
| `ProcurementRequestViewSet` | `list`, `retrieve` | `procurement.view` |
| | `create` | `procurement.create` |
| | `update`, `partial_update` | `procurement.edit` |
| | `destroy` | `procurement.delete` |
| | `attributes` (GET) | `procurement.view` |
| | `attributes` (POST) | `procurement.edit` |
| | `runs` (GET) | `procurement.view` |
| | `runs` (POST) | `procurement.run_analysis` |
| | `recommendation` (GET) | `procurement.view_results` |
| | `benchmark` (GET) | `procurement.view_results` |
| | `validation` (GET) | `procurement.view_results` |
| | `validate` (POST) | `procurement.validate` |
| `SupplierQuotationViewSet` | `list`, `retrieve` | `procurement.view` |
| | `create`, `update`, `destroy` | `procurement.manage_quotations` |
| `ValidationRuleSetViewSet` | `list`, `retrieve` | `procurement.view` |
| `AnalysisRunValidationView` | `retrieve` | `procurement.view_results` |

#### Template Views (`apps/procurement/template_views.py`)

All views use `@login_required` + `@permission_required_code()`:

| View | Permission |
|---|---|
| `request_list` | `procurement.view` |
| `request_create` | `procurement.create` |
| `request_workspace` | `procurement.view` |
| `run_detail` | `procurement.view_results` |
| `trigger_analysis` | `procurement.run_analysis` |
| `mark_ready` | `procurement.edit` |
| `upload_quotation` | `procurement.manage_quotations` |
| `trigger_validation` | `procurement.validate` |

#### Sidebar Navigation (`templates/partials/sidebar.html`)

The Procurement sidebar section is gated with `{% has_permission "procurement.view" %}` — only visible to users with the `procurement.view` permission.

---

## 13. File Organization

```
apps/procurement/
├── __init__.py
├── apps.py                    # AppConfig: "Procurement Intelligence"
├── admin.py                   # Admin registration with inlines
├── models.py                  # 13 models (Request, Attribute, Quotation, LineItem,
│                              #   AnalysisRun, RecommendationResult, BenchmarkResult,
│                              #   BenchmarkResultLine, ComplianceResult,
│                              #   ValidationRuleSet, ValidationRule,
│                              #   ValidationResult, ValidationResultItem)
├── serializers.py             # 17 DRF serializers (list/detail/write per model)
├── views.py                   # 4 DRF ViewSets + nested actions
├── api_urls.py                # DRF router → /api/v1/procurement/
├── template_views.py          # 8 template views (list, create, workspace, detail, actions)
├── urls.py                    # Template URLs → /procurement/
├── tasks.py                   # Celery tasks: run_analysis_task, run_validation_task
├── agents/
│   ├── __init__.py
│   ├── recommendation_agent.py      # AI recommendation agent
│   ├── benchmark_agent.py           # AI benchmark resolution agent
│   ├── compliance_agent.py          # AI compliance check agent
│   ├── quotation_extraction_agent.py # AI quotation data extraction (OCR text → structured JSON)
│   └── request_extraction_agent.py   # AI request/SOW data extraction
├── services/
│   ├── __init__.py
│   ├── request_service.py      # ProcurementRequestService + AttributeService
│   ├── quotation_service.py    # QuotationService + LineItemNormalizationService
│   ├── analysis_run_service.py # AnalysisRunService (lifecycle)
│   ├── recommendation_service.py # RecommendationService (full flow)
│   ├── benchmark_service.py    # BenchmarkService (full flow)
│   ├── compliance_service.py   # ComplianceService (rule-based)
│   ├── prefill/                # Quotation Prefill Extraction Pipeline
│   │   ├── __init__.py
│   │   ├── quotation_prefill_service.py  # QuotationDocumentPrefillService (OCR → LLM → mapping → payload)
│   │   ├── attribute_mapping_service.py  # AttributeMappingService (field synonym resolution + line item mapping)
│   │   ├── prefill_status_service.py     # PrefillStatusService (status transitions + payload persistence)
│   │   ├── prefill_review_service.py     # PrefillReviewService (user confirmation → QuotationLineItem creation)
│   │   └── request_prefill_service.py    # RequestDocumentPrefillService (SOW/RFQ attribute extraction)
│   └── validation/             # Validation Framework services
│       ├── __init__.py
│       ├── rule_resolver_service.py        # Rule resolution by domain/schema
│       ├── attribute_completeness_service.py # REQUIRED_ATTRIBUTE checks
│       ├── document_completeness_service.py  # REQUIRED_DOCUMENT checks
│       ├── scope_coverage_service.py         # REQUIRED_CATEGORY scope checks
│       ├── ambiguity_service.py              # Ambiguity pattern detection
│       ├── commercial_completeness_service.py # Commercial term checks
│       ├── compliance_readiness_service.py   # Compliance readiness checks
│       ├── orchestrator_service.py           # ValidationOrchestratorService
│       └── validation_agent.py               # LLM augmentation for ambiguity
└── migrations/
    ├── __init__.py
    ├── 0001_initial.py                        # Initial migration (9 tables)
    └── 0002_add_validation_framework.py        # Validation models (4 tables)

templates/procurement/
├── request_list.html          # Filterable list with status badges
├── request_create.html        # Dynamic attribute form
├── request_workspace.html     # Full workspace (summary, results, timeline)
├── run_detail.html            # Analysis run detail (input/output/audit)
└── partials/
    └── validation_summary.html # Validation results partial (status, score, findings)
```

### Integration Points in Existing Files

| File | Change |
|---|---|
| `config/settings.py` | Added `"apps.procurement"` to `INSTALLED_APPS` |
| `config/urls.py` | Added `path("procurement/", ...)` and `path("api/v1/procurement/", ...)` |
| `apps/core/enums.py` | Added 8 base enum classes + 8 validation enum classes (17 total including `VALIDATION` in AnalysisRunType) |
| `templates/partials/sidebar.html` | Added "Procurement" nav section |

### Database Tables Created

| Table Name | Model |
|---|---|
| `procurement_request` | ProcurementRequest |
| `procurement_request_attribute` | ProcurementRequestAttribute |
| `procurement_supplier_quotation` | SupplierQuotation |
| `procurement_quotation_line_item` | QuotationLineItem |
| `procurement_analysis_run` | AnalysisRun |
| `procurement_recommendation_result` | RecommendationResult |
| `procurement_benchmark_result` | BenchmarkResult |
| `procurement_benchmark_result_line` | BenchmarkResultLine |
| `procurement_compliance_result` | ComplianceResult |
| `procurement_validation_rule_set` | ValidationRuleSet |
| `procurement_validation_rule` | ValidationRule |
| `procurement_validation_result` | ValidationResult |
| `procurement_validation_result_item` | ValidationResultItem |

---

## 14. Status Transitions

### ProcurementRequest Status Flow

```
DRAFT ──[mark_ready]──> READY ──[trigger_analysis]──> PROCESSING
                                                          │
                          ┌───────────────────────────────┤
                          │               │               │
                     COMPLETED    REVIEW_REQUIRED       FAILED
                          │               │
                          └───[re-run]────┘──> PROCESSING (re-analysis)
```

| Transition | Trigger | Condition |
|---|---|---|
| DRAFT → READY | `mark_ready()` | All required attributes have values |
| READY → PROCESSING | `run_analysis_task` | Task dispatched |
| PROCESSING → COMPLETED | Service completion | Risk ≤ MEDIUM, compliance not FAIL, or validation PASS |
| PROCESSING → REVIEW_REQUIRED | Service completion | Risk = HIGH/CRITICAL, or compliance = FAIL, or validation REVIEW_REQUIRED |
| PROCESSING → FAILED | Service failure | Exception during analysis, or validation FAIL |
| COMPLETED/REVIEW_REQUIRED → PROCESSING | Re-trigger analysis | User manually re-runs |

### AnalysisRun Status Flow

```
QUEUED ──[start_run]──> RUNNING ──[complete_run]──> COMPLETED
                                 ──[fail_run]────> FAILED
```

---

## 15. Flow Walkthroughs

### Flow 1: Product / Solution Recommendation

```
Step 1: User creates ProcurementRequest via UI or API
        → ProcurementRequestService.create_request()
        → Status: DRAFT
        → AuditEvent: PROCUREMENT_REQUEST_CREATED

Step 2: User defines attributes (requirements)
        → AttributeService.bulk_set_attributes()

Step 3: User clicks "Mark Ready"
        → ProcurementRequestService.mark_ready()
        → Validates required attributes
        → Status: READY
        → AuditEvent: PROCUREMENT_REQUEST_STATUS_CHANGED

Step 4: User clicks "Run Analysis" with type=RECOMMENDATION
        → AnalysisRunService.create_run(run_type="RECOMMENDATION")
        → run_analysis_task.delay(run.pk)
        → Status: PROCESSING

Step 5: Celery task executes:
        → AnalysisRunService.start_run()
        → AttributeService.get_attributes_dict()
        → RecommendationService._apply_rules()  ← Deterministic first
        │
        ├── If rules confident=True:
        │   → Use rule result directly
        │
        └── If rules confident=False and use_ai=True:
            → RecommendationAgent.execute()  ← LLM call
            → Returns structured recommendation JSON

Step 6: ComplianceService.check_recommendation()
        → Checks: recommendation present, confidence ≥ 0.5, budget
        → Returns compliance status

Step 7: Persist results in transaction:
        → RecommendationResult.objects.create()
        → ComplianceResult.objects.create()

Step 8: AnalysisRunService.complete_run()
        → AuditEvent: ANALYSIS_RUN_COMPLETED

Step 9: ProcurementRequestService.update_status()
        → If compliance PASS/PARTIAL → COMPLETED
        → If compliance FAIL → REVIEW_REQUIRED
        → AuditEvent: PROCUREMENT_REQUEST_STATUS_CHANGED
```

### Flow 2: Should-Cost Benchmarking

```
Step 1: User creates ProcurementRequest
        → Status: DRAFT

Step 2: User adds SupplierQuotation(s)
        → QuotationService.create_quotation()
        → AuditEvent: QUOTATION_UPLOADED

Step 3: Line items are added to quotation
        → QuotationService.add_line_items()
        → LineItemNormalizationService.normalize_line_items()

Step 4: User clicks "Run Analysis" with type=BENCHMARK
        → AnalysisRunService.create_run(run_type="BENCHMARK")
        → run_analysis_task.delay(run.pk)
        → Status: PROCESSING

Step 5: Celery task executes:
        → AnalysisRunService.start_run()
        → For each QuotationLineItem:
        │
        ├── BenchmarkService._resolve_benchmark(item)
        │   ├── Try BenchmarkAgent.resolve_benchmark_for_item()  ← LLM call
        │   └── Fallback: {min: null, avg: null, max: null}
        │
        └── BenchmarkService._compute_variance(item, benchmark)
            → (quoted - avg) / avg × 100

Step 6: Aggregate results:
        → total_quoted = sum(line.total_amount)
        → total_benchmark = sum(avg × qty)
        → overall_variance_pct

Step 7: BenchmarkService._classify_risk(variance_pct)
        → ≤5% = LOW, ≤15% = MEDIUM, ≤30% = HIGH, >30% = CRITICAL

Step 8: Persist in transaction:
        → BenchmarkResult.objects.create(header)
        → BenchmarkResultLine.objects.bulk_create(lines)

Step 9: AnalysisRunService.complete_run()
        → AuditEvent: ANALYSIS_RUN_COMPLETED

Step 10: ProcurementRequestService.update_status()
         → If risk LOW/MEDIUM → COMPLETED
         → If risk HIGH/CRITICAL → REVIEW_REQUIRED
```

### Flow 3: Validation

```
Step 1: User creates ProcurementRequest and defines attributes/quotations
        → Status: DRAFT or READY

Step 2: User clicks "Run Analysis" with type=VALIDATION
        → AnalysisRunService.create_run(run_type="VALIDATION")
        → run_validation_task.delay(run.pk) (or via validate action)
        → Status: PROCESSING

Step 3: Celery task executes:
        → ValidationOrchestratorService.run_validation(request, run)
        │
        ├── Step 3a: Resolve rules
        │   → ValidationRuleResolverService.resolve_rules_for_request()
        │   → Matches rules by domain_code + schema_code (specific → generic)
        │
        ├── Step 3b: Run 6 deterministic validators
        │   → AttributeCompletenessValidationService.validate()
        │   → DocumentCompletenessValidationService.validate()
        │   → ScopeCoverageValidationService.validate()
        │   → AmbiguityValidationService.validate()
        │   → CommercialCompletenessValidationService.validate()
        │   → ComplianceReadinessValidationService.validate()
        │
        ├── Step 3c: Optional agent augmentation
        │   → If agent_enabled AND ambiguous_count >= 3:
        │     → ValidationAgentService.augment_findings()  ← LLM call
        │
        └── Step 3d: Score and classify
            → _compute_completeness_score() (severity-weighted)
            → _determine_overall_status() (PASS/PASS_WITH_WARNINGS/REVIEW_REQUIRED/FAIL)
            → _determine_readiness() (recommendation/benchmarking readiness)
            → _determine_next_action()

Step 4: Persist in transaction:
        → ValidationResult.objects.create(overall_status, score, summary, ...)
        → ValidationResultItem.objects.bulk_create(all findings)

Step 5: AnalysisRunService.complete_run()
        → AuditEvent: ANALYSIS_RUN_COMPLETED

Step 6: AuditService.log_event(VALIDATION_COMPLETED)
        → AuditEvent with completeness_score, missing/warning/ambiguous counts

Step 7: run_validation_task updates request status:
        → If PASS → READY
        → If FAIL → FAILED
        → If REVIEW_REQUIRED → REVIEW_REQUIRED
```

---

## 16. Configuration & Extension Points

### Adding a New Domain

1. No code changes needed — create requests with a new `domain_code` (e.g. `"ELECTRICAL"`)
2. Optionally set `schema_code` to define domain-specific attribute templates

### Adding Deterministic Rules

Extend `RecommendationService._apply_rules()`:

```python
@staticmethod
def _apply_rules(request, attrs):
    if request.domain_code == "IT":
        if attrs.get("compute_type") == "GPU" and attrs.get("budget") > 50000:
            return {
                "recommended_option": "NVIDIA A100 Cluster",
                "reasoning_summary": "GPU compute with sufficient budget matches A100",
                "confident": True,
                "constraints": ["budget", "compute_type"],
            }
    # ... fallback to AI
    return {"confident": False, ...}
```

### Adding Benchmark Data Sources

Override `BenchmarkService._resolve_benchmark()` to query a price database:

```python
@staticmethod
def _resolve_benchmark(item, use_ai=False):
    # Try internal benchmark DB first
    from myapp.benchmarks import PriceCatalog
    catalog_hit = PriceCatalog.lookup(item.category_code, item.normalized_description)
    if catalog_hit:
        return {"min": catalog_hit.p10, "avg": catalog_hit.p50, "max": catalog_hit.p90, "source": "catalog"}
    # Fall back to AI
    if use_ai:
        return BenchmarkAgent.resolve_benchmark_for_item(item)
    return {"min": None, "avg": None, "max": None, "source": "none"}
```

### Adding New Compliance Rules

Add rules in `ComplianceService.check_recommendation()` or `check_benchmark()`:

```python
# Example: geography-based compliance
rules_checked.append({"rule": "geo_restriction", "description": "Vendor must be in approved countries"})
if request.geography_country in RESTRICTED_COUNTRIES:
    violations.append({"rule": "geo_restriction", "detail": f"{request.geography_country} is restricted"})
```

### Adding RBAC Permissions

```bash
# Via seed_rbac or Django admin, add permissions:
procurement.view       → All roles
procurement.create     → AP_PROCESSOR, ADMIN, FINANCE_MANAGER
procurement.manage     → ADMIN, FINANCE_MANAGER
procurement.run_analysis → AP_PROCESSOR, ADMIN, FINANCE_MANAGER
```

### Adding New Analysis Run Types

1. Add enum value to `AnalysisRunType` in `apps/core/enums.py`
2. Create new service in `apps/procurement/services/`
3. Add dispatch branch in `run_analysis_task`
4. Create result model if needed
5. Add UI section in workspace template

**Example**: The `VALIDATION` run type was added following this pattern — see `ValidationOrchestratorService`, `run_validation_task`, and `validation_summary.html`.

### Adding Validation Rules

Validation rules are data-driven via the `ValidationRuleSet` + `ValidationRule` models:

1. Create a `ValidationRuleSet` via Django admin or API with `domain_code`, `schema_code`, and `validation_type`
2. Add `ValidationRule` records with `rule_type` matching the validation dimension:
   - `REQUIRED_ATTRIBUTE` → checked by `AttributeCompletenessValidationService`
   - `REQUIRED_DOCUMENT` → checked by `DocumentCompletenessValidationService`
   - `REQUIRED_CATEGORY` → checked by `ScopeCoverageValidationService`
   - `AMBIGUITY_PATTERN` → additional patterns for `AmbiguityValidationService`
   - `COMMERCIAL_CHECK` → additional terms for `CommercialCompletenessValidationService`
   - `COMPLIANCE_CHECK` → compliance checks for `ComplianceReadinessValidationService`
3. Set `severity` (INFO/WARNING/ERROR/CRITICAL) to control scoring impact
4. Set `condition_json` for rule-specific parameters (e.g. `{"attribute_code": "budget"}` for REQUIRED_ATTRIBUTE)
5. Rules are automatically resolved for matching requests via `ValidationRuleResolverService`

### Integration with Existing Document Extraction

`SupplierQuotation.uploaded_document` links to `apps.documents.DocumentUpload`. The quotation extraction pipeline operates independently from the invoice extraction pipeline:

1. Upload quotation/proposal PDF via `quotation_prefill` API → creates `DocumentUpload` + `SupplierQuotation`
2. Async `run_quotation_prefill_task` triggers `QuotationDocumentPrefillService.run_prefill()`
3. OCR via Azure Document Intelligence (reuses `InvoiceExtractionAdapter._ocr_document()`)
4. LLM extraction via `QuotationDocumentPrefillService._extract_quotation_data()` (GPT-4o, up to 60K chars)
5. Field mapping via `AttributeMappingService.map_quotation_fields()` → stores `prefill_payload_json`
6. User reviews and confirms → `PrefillReviewService.confirm_quotation_prefill()` persists `QuotationLineItem` records
7. `extraction_status` and `extraction_confidence` updated at each stage

---

## Appendix: Existing Platform Dependencies

The procurement module depends on these existing platform services:

| Dependency | Module | Purpose |
|---|---|---|
| `BaseModel` | `apps.core.models` | Timestamp + audit field inheritance |
| `TimestampMixin` | `apps.core.models` | Lightweight timestamp inheritance |
| `AuditService` | `apps.auditlog.services` | Business event logging |
| `AuditEvent` | `apps.auditlog.models` | Audit record storage |
| `ProcessingLog` | `apps.auditlog.models` | Operational log storage |
| `TraceContext` | `apps.core.trace` | Distributed tracing |
| `@observed_service` | `apps.core.decorators` | Service method tracing |
| `@observed_task` | `apps.core.decorators` | Celery task tracing |
| `LLMClient` | `apps.agents.services.llm_client` | Azure OpenAI / OpenAI API client |
| `LLMMessage` | `apps.agents.services.llm_client` | Message format for LLM calls |
| `DocumentUpload` | `apps.documents.models` | Document storage (FK from SupplierQuotation) |
| `HasPermissionCode` | `apps.core.permissions` | DRF permission class |
| `@login_required` | `django.contrib.auth` | Template view authentication |
| `LoginRequiredMiddleware` | `apps.core.middleware` | Global authentication enforcement |
| `RBACMiddleware` | `apps.core.middleware` | Permission cache pre-loading |
| `RequestTraceMiddleware` | `apps.core.middleware` | Root TraceContext creation per request |
| `DjangoFilterBackend` | `django_filters` | API filtering |
| `Celery` | `config.celery` | Async task execution |
