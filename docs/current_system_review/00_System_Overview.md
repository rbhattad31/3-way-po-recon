# 00 — System Overview

**Generated**: 2026-04-09 | **Method**: Code-first inspection | **Confidence**: High

---

## 1. Platform Purpose

The **3-Way PO Reconciliation Platform** is an enterprise Django application that automates Accounts Payable invoice processing. It ingests invoice PDFs, extracts structured data via AI, matches invoices against Purchase Orders and Goods Receipt Notes, routes exceptions to LLM-powered agents, and supports human review and ERP posting — all within a multi-tenant, role-governed architecture.

---

## 2. Business Domain

**Accounts Payable (AP) Automation** — specifically:

- **2-way matching**: Invoice vs Purchase Order (price, quantity, amount tolerance)
- **3-way matching**: Invoice vs Purchase Order vs Goods Receipt Note
- **Non-PO invoices**: Service invoices without a PO reference
- **Exception management**: Flagging, routing, human escalation
- **ERP integration**: Posting approved invoices into connected ERP systems

The platform supports **configurable reconciliation mode selection** (2-way vs 3-way) based on invoice type, vendor policy, and business unit rules — rather than requiring a fixed approach for all invoices.

---

## 3. Main Personas / Users

| Role | Code | Description |
|------|------|-------------|
| Administrator | `ADMIN` | Full platform access; manages users, roles, config |
| AP Processor | `AP_PROCESSOR` | Uploads invoices, monitors extraction, triggers reconciliation |
| Reviewer | `REVIEWER` | Reviews exceptions, makes approve/reject decisions |
| Finance Manager | `FINANCE_MANAGER` | Approves escalated cases; views governance metrics |
| Auditor | `AUDITOR` | Read-only access to audit logs and governance reports |
| System Agent | `SYSTEM_AGENT` | Internal identity for autonomous pipeline operations |

**Evidence**: `apps/accounts/rbac_models.py`, `apps/agents/services/guardrails_service.py`

---

## 4. Key Capabilities

| Capability | Implementation Status |
|-----------|----------------------|
| Invoice PDF ingestion (web + bulk) | Implemented |
| Azure Document Intelligence OCR | Implemented |
| AI invoice extraction (GPT-4o, modular prompts) | Implemented |
| Category classification (goods/service/travel) | Implemented |
| Deterministic response repair (5 rules) | Implemented |
| 2-way PO matching | Implemented |
| 3-way PO+GRN matching | Implemented |
| Non-PO invoice validation | Implemented |
| Configurable tolerance bands (strict + auto-close) | Implemented |
| LLM agent analysis pipeline (8 agents) | Implemented |
| Supervisor agent (full lifecycle, 5 skills, 24 tools) | Implemented |
| Agent feedback loop (PO/GRN re-reconciliation) | Implemented |
| Human review workflow with auto-assignment | Implemented |
| Case management (11-stage state machine) | Implemented |
| ERP posting workflow | Implemented |
| ERP integration (6 connector types) | Implemented |
| Multi-tenant row-level isolation | Implemented |
| RBAC with 6 roles, 40+ permissions | Implemented |
| Agent RBAC guardrails | Implemented |
| Compliance audit trail | Implemented |
| Langfuse LLM observability | Implemented |
| Dashboard analytics (7 endpoints) | Implemented |
| Report exports (Excel/PDF) | Stub only |
| Email notifications | Not implemented |
| Celery Beat for scheduled reconciliation | Not implemented |
| Docker / CI-CD | Not implemented |

---

## 5. High-Level Workflow

```
[User uploads PDF]
       │
       ▼
[DocumentUpload created] → Azure Blob Storage
       │
       ▼
[Extraction Pipeline Task] (Celery)
   ├── Azure Document Intelligence OCR
   ├── Category Classification (goods / service / travel)
   ├── Modular Prompt Composition (base + category + country overlays)
   ├── GPT-4o LLM Extraction (temperature=0, JSON response)
   ├── Deterministic Response Repair (5 rules)
   ├── Parse + Normalize + Validate
   ├── Duplicate Detection
   ├── Persist (Invoice + InvoiceLineItem)
   └── Extraction Approval Gate
          ├── Auto-approve (confidence ≥ threshold, default 0.85)
          └── Manual review (confidence < threshold)
                     │ (on approval)
                     ▼
[APCase created] ← [CaseOrchestrator] (Celery)
       │
       ▼
[Path Resolution]
   ├── TWO_WAY (service invoices, policy)
   ├── THREE_WAY (goods/stock, GRN required)
   └── NON_PO (no PO reference)
       │
       ▼
[Reconciliation Engine] (14 services)
   ├── PO Lookup → Header Match → Line Match → GRN Match (3-way)
   ├── Tolerance Classification (strict band: 2%/1%/1%)
   ├── Auto-close (auto-close band: 5%/3%/3%)
   └── Exception Building
       │                    │
       │ (MATCHED)          │ (PARTIAL/UNMATCHED/exceptions)
       ▼                    ▼
[Case CLOSED]     [Agent Pipeline Task] (Celery)
                     ├── PolicyEngine selects agent sequence
                     ├── Agents run in sequence (ReAct loops + tools)
                     │    ├── InvoiceUnderstanding / PORetrieval / GRNRetrieval
                     │    ├── ExceptionAnalysis + Reviewer Summary
                     │    ├── ReviewRouting
                     │    └── CaseSummary
                     ├── Agent Feedback Loop (if PO recovered → re-reconcile)
                     │
                     └── [Alt: SupervisorAgent] (single-agent lifecycle)
                          ├── 5-phase non-linear: UNDERSTAND→VALIDATE→MATCH→INVESTIGATE→DECIDE
                          ├── 30 tools (24 supervisor + 6 base), skill-based prompt composition
                          └── submit_recommendation → route / auto-close / escalate
                              │
                              ▼
                     [Human Review Queue]
                     ├── Auto-assigned to REVIEWER
                     ├── Reviewer sees: exceptions, agent summary, actions
                     └── Decision: Approve / Reject / Escalate
                              │
                              ▼
                     [ERP Posting Workflow]
                     PROPOSED → REVIEW_REQUIRED → READY_TO_SUBMIT → SUBMITTED
```

---

## 6. Architecture Style

- **Django multi-app monolith** with clear domain boundaries
- **Shared-database multi-tenancy** via `CompanyProfile` FK (`tenant`) on all major models
- **Service-layer pattern**: business logic in `services/` modules, not in views or models
- **Celery async tasks** for all long-running operations (extraction, reconciliation, agents)
- **ReAct agent loop** for LLM agents (tool-calling, iterative reasoning)
- **Policy-driven orchestration**: deterministic `PolicyEngine` selects agent sequence; optional LLM `ReasoningPlanner`
- **Fail-closed guardrails**: RBAC checked at orchestrator, agent, tool, and recommendation levels

---

## 7. Technical Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Web framework | Django | 5.0.14 |
| API framework | Django REST Framework | 3.16.1 |
| Task queue | Celery + Redis | 5.6.2 + 6.4.0 |
| Result backend | django-celery-results | 2.6.0 |
| Database | MySQL (utf8mb4) | — |
| LLM | Azure OpenAI (GPT-4o) | openai 2.30 |
| OCR | Azure Document Intelligence | azure-ai-formrecognizer 3.3.2 |
| Blob storage | Azure Blob Storage | azure-storage-blob 12.20 |
| LLM observability | Langfuse | 4.0.1 |
| Distributed tracing | OpenTelemetry | 1.40.0 |
| String matching | thefuzz + RapidFuzz | 0.22 + 3.14 |
| Frontend | Django Templates + Bootstrap 5 | — |
| Testing | pytest + pytest-django + factory-boy | — |

---

## 8. Maturity Summary

| Area | Maturity |
|------|---------|
| Extraction pipeline | Production-ready (11-stage, 25 repair tests, Langfuse traced) |
| Reconciliation engine | Production-ready (14 services, tiered tolerance, 73+ tests) |
| Agent pipeline | Production-ready (8 LLM + 5 system + 1 supervisor agent, RBAC guardrails, feedback loop) |
| RBAC / governance | Production-ready (6 roles, 40 permissions, per-user overrides, audit trail) |
| Case management | Production-ready (state machine, orchestrators, review workflow) |
| ERP integration | Framework complete; connector maturity depends on specific ERP |
| Observability | Strong (Langfuse, AuditEvent, DecisionLog, ProcessingLog, OpenTelemetry) |
| Operations | Partial (no email, no scheduled ERP sync, no Docker/CI-CD) |
| Report exports | Stub only |
| Procurement intelligence | Implemented; request/recommendation/validation/prefill flows verified, benchmark execution currently uses a compatibility bridge |

---

## 9. Top Ambiguities

1. **Extraction_documents app**: Referenced in migrations and settings comment but not in `INSTALLED_APPS` — may be a migration artifact
2. **Integrations app**: Registered in settings but not deeply inspected — purpose unclear vs `erp_integration`
3. **Copilot**: Registered with `api/v1/copilot/` and `copilot/` URLs — full feature scope not inspected
4. **Celery Beat**: Only one beat task (`process_approved_learning_actions`) — scheduled reconciliation is triggered on-demand only
5. **ReasoningPlanner**: Available behind `AGENT_REASONING_ENGINE_ENABLED` env flag — not tested in production per README silence
6. **Procurement benchmarking depth**: Procurement request, validation, prefill, and recommendation flows are implemented and verified, but the active BENCHMARK execution path currently routes through `apps.benchmarking.services.procurement_cost_service` as a compatibility bridge rather than a full should-cost corridor engine
