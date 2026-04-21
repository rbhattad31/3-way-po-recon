# 12 — Open Questions and Validation Points

**Generated**: 2026-04-09 | **Purpose**: Items requiring product owner / architect / business team confirmation  
**Format**: Grouped by domain, with evidence of what the code implies and what remains unknown

---

## A. Business Domain Questions

### A1. Primary Geographic Market
**Code implies**: India (Asia/Kolkata timezone, India GST tax_breakdown with cgst/sgst/igst fields, country overlay `extraction.country.india_gst`)  
**Question**: Is India the primary market, or is this a multi-country deployment? What countries are currently active?

### A2. McDonald's Saudi Arabia Seed Data
**Code**: Seed data referenced "Saudi McD" (25 POs, 30 GRNs, 40 test scenarios)  
**Question**: Is this a real customer deployment or purely a demo dataset? If real, is there additional Saudi-specific business logic needed (VAT, Arabic character handling)?

### A3. Credit System Business Model
**Code**: Per-user credit accounts with reserve → consume → refund lifecycle for extraction  
**Question**: Is this SaaS billing (customers pre-buy credits) or internal cost allocation (departments charged for LLM usage)?

### A4. Non-PO Invoice Approval Authority
**Code**: `NON_PO_VALIDATION_IN_PROGRESS` case stage exists; non-PO validation service present  
**Question**: Who approves non-PO invoices? What are the approval thresholds and rules?

### A5. ERP Posting Review Conditions
**Code**: Posting workflow has `REVIEW_REQUIRED` state before `READY_TO_SUBMIT`  
**Question**: What conditions trigger `REVIEW_REQUIRED`? Is this amount-based, exception-based, or manual?

---

## B. Architecture and Technical Questions

### B1. `integrations` App Purpose
**Code**: `apps.integrations` is in `INSTALLED_APPS`; URL path not registered in `config/urls.py`  
**Question**: What is this app for? Is it a shared integration utilities module, a legacy app, or planned future work?

### B2. Line Match LLM Fallback
**Code**: `reconciliation/services/line_match_llm_fallback.py` exists  
**Question**: Is this currently active in the production matching path? Under what conditions does it trigger? Is it tested?

### B3. `copilot` App Scope
**Code**: URL paths `api/v1/copilot/` and `copilot/` registered; `CopilotService` and views present  
**Question**: What does the Copilot feature do exactly? Is it a Q&A interface for AP processors? Is it production-ready or experimental?

### B4. `ReasoningPlanner` Activation Plans
**Code**: Available via `AGENT_REASONING_ENGINE_ENABLED=true`; 17 tests in `test_reasoning_planner.py`; eval tracking wired via `AgentEvalAdapter` (plan_source, plan_confidence, plan_adherence metrics).  
**Status**: RESOLVED -- flag is wired into `AgentOrchestrator.__init__()`, tests cover LLM plan, fallback, validation, and orchestrator flag wiring. See `docs/REASONING_PLANNER.md` for full architecture + LLM-only upgrade path.  
**Question**: Is there a plan to enable this in production? What would be the trigger criteria? Are there eval baselines for it?

### B5. Single Celery Queue
**Code**: All tasks use `CELERY_TASK_DEFAULT_QUEUE = "default"` — no queue prioritization  
**Question**: Is there a plan to separate high-priority (extraction, case tasks) from lower-priority (agent analysis) queues? Under high load, extraction could be starved by agent tasks.

### B6. OTLP Exporter Configuration
**Code**: `opentelemetry-exporter-otlp-proto-http` installed; no OTEL endpoint in settings.py  
**Question**: Is OpenTelemetry actually shipping spans? Where is the OTLP endpoint configured? Is there a separate APM tool receiving this data?

---

## C. Security and Governance Questions

### C1. Scope Restrictions in `UserRole.scope_json`
**Code**: `scope_json` supports `allowed_business_units` and `allowed_vendor_ids`; other fields commented as "pending"  
**Question**: Is scope restriction actively used? Are there users with scoped roles in production? What is the plan for country/legal_entity scope restrictions?

### C2. `prohibited_actions` on `AgentDefinition`
**Code**: `AgentDefinition.prohibited_actions` JSON field exists but enforcement not verified  
**Question**: Is this field actually enforced anywhere? If not, should it be implemented or removed as misleading?

### C3. `RBACMiddleware` Behavior
**Code**: `RBACMiddleware` is in the middleware stack but its internals were not read  
**Question**: Does this middleware perform view-level permission checks, or is it purely for context injection? Are there views that should be protected that rely on this middleware vs the `AgentGuardrailsService`?

### C4. Database Password Validation
**Code**: `DB_PASSWORD` is read from env but no `ImproperlyConfigured` raise if missing (unlike `DJANGO_SECRET_KEY`)  
**Question**: Is this an intentional design choice? In production, an empty DB_PASSWORD would silently fail at connection time rather than startup.

---

## D. Operations and Deployment Questions

### D1. Celery Beat Worker Deployment
**Code**: Beat schedule exists (`process_approved_learning_actions` every 30 min)  
**Question**: Is the Celery Beat worker actually running in production? The README doesn't mention starting it. What happens if it's not running — do learning actions queue up indefinitely?

### D2. Scheduled Reconciliation Policy
**Code**: `run_reconciliation_task` only triggered on-demand (no beat schedule)  
**Question**: Is on-demand reconciliation intentional? Is there a business SLA for how quickly invoices should be processed after upload? Should auto-triggered reconciliation be added?

### D3. ERP Live Refresh Policy
**Code**: `ERP_ENABLE_LIVE_REFRESH_ON_MISS=false` and `ERP_ENABLE_LIVE_REFRESH_ON_STALE=false` by default  
**Question**: In production, is this deliberately disabled? How are mirror tables kept up-to-date if live refresh is off? Is there a separate ERP sync job not in this codebase?

### D4. Redis Production Configuration
**Code**: Settings comment explicitly warns: "MUST be overridden via CELERY_BROKER_URL env var in every non-development environment"  
**Question**: Is this enforced in production deployment scripts? Is there a checklist preventing deployment with default dev Redis config?

### D5. Multi-Worker Celery Concurrency
**Code**: No worker concurrency settings specified; single `default` queue  
**Question**: How many Celery workers and with what concurrency are deployed? Given LLM calls can take 30-120s, what is the expected throughput (invoices/hour)?

---

## E. Data and Model Questions

### E1. Invoice Deduplication Exact Rules
**Code**: `DuplicateDetectionService` exists; `Invoice.is_duplicate` flag set  
**Question**: What is the exact deduplication logic? Is it invoice_number + vendor + amount? Does it span tenants?

### E2. PO Balance Tracking
**Code**: `reconciliation/services/po_balance_service.py` exists  
**Question**: Does the platform track how much of a PO has been invoiced (partially invoiced POs)? How does it handle multiple invoices against the same PO?

### E3. Multi-Invoice PO Scenarios
**Code**: `partial_invoice_threshold_pct = 95.0` config field suggests milestone invoicing is supported  
**Question**: Is there a use case where multiple invoices reference the same PO (progress billing, partial delivery)? How are these reconciled?

### E4. `procurement` Module Scope
**Code**: Standalone procurement stack with `ProcurementRequest`, quotation prefill, validation, recommendation, market-intelligence flows, plus BENCHMARK dispatch via `apps.benchmarking.services.procurement_cost_service`  
**Current finding**: Procurement is currently a standalone feature area, not part of the reconciliation or PO-validation feedback loop. It has its own request/run/result hierarchy and its own APIs/tasks. The active BENCHMARK runtime is a compatibility bridge rather than a full should-cost engine.

### E5. `core_eval` Learning Actions
**Code**: `LearningAction` model, `process_approved_learning_actions` beat task  
**Question**: What do approved learning actions actually do? Do they modify prompt templates, tolerances, or routing rules? Who reviews and approves learning actions before they're applied?

---

## F. Assumptions Requiring Validation

| Assumption | Based On | Validation Needed |
|-----------|---------|------------------|
| India is primary market | Timezone + tax fields | Confirm geographic scope |
| Single Azure OpenAI deployment | Settings default | Confirm no fallback deployment |
| Local filesystem only for dev (not prod) | media/ directory | Confirm Azure Blob is always used in prod |
| Seed data is demo-only | "Saudi McD" references | Confirm no real customer data in seed scripts |
| `reviews` app is permanently deprecated | Settings comment | Confirm migrations won't break if app removed |
| `integrations` app has no active business logic | No URL registration | Inspect `apps/integrations/` to confirm |
| 124+ tests all pass on current codebase | README claim | Run test suite to verify |
| `prohibited_actions` is not enforced | Code not read in full | Search for enforcement code in guardrails |
