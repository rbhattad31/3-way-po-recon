# 08 — Audit, Traceability, and Observability

**Generated**: 2026-04-09 | **Method**: Code-first inspection  
**Evidence files**: `auditlog/models.py`, `agents/models.py` (DecisionLog), `core/evaluation_constants.py`, `core/observability_helpers.py`, `agents/tasks.py`, `reconciliation/tasks.py`, `cases/tasks.py`

---

## 1. Audit Event Model (`AuditEvent`)

**Table**: `auditlog_audit_event`  
**Purpose**: Compliance-grade, business-significant event history only.

### Design Principles (from model docstring)
- Captures: invoice uploaded, extraction completed, duplicate flagged, reconciliation triggered/completed, mode resolved, review assigned/approved/rejected, field corrected, override applied, reprocess requested, case rerouted/closed, role/permission changes, access denied
- Does NOT capture operational noise (use `ProcessingLog` for that)
- 38+ audit event types (from README)

### Key Fields
| Category | Fields |
|----------|--------|
| Entity | entity_type (e.g. "Invoice"), entity_id (BigInt) |
| Action | action, event_type, event_description |
| Actor | performed_by (FK User), performed_by_agent (agent name for system actions) |
| Before/After | old_values (JSON), new_values (JSON), status_before, status_after |
| RBAC Snapshot | actor_email, actor_primary_role, actor_roles_snapshot_json, permission_checked, permission_source, access_granted |
| Trace | trace_id, span_id, parent_span_id |
| Cross-refs | invoice_id, case_id, reconciliation_result_id, review_assignment_id, agent_run_id |
| Payload | input_snapshot_json, output_snapshot_json (redacted) |
| Meta | ip_address, user_agent, duration_ms, error_code, is_redacted |

### Indexed Fields
`entity_type+entity_id`, `action`, `event_type`, `trace_id`, `invoice_id`, `case_id`, `actor_primary_role`, `permission_checked`, `access_granted`

---

## 2. Processing Log (`ProcessingLog`)

**Table**: `auditlog_processing_log`  
**Purpose**: Operational observability — pipeline durations, retries, failures, queue health. Not for compliance.

### Key Fields
| Category | Fields |
|----------|--------|
| Routing | source (module name), event (event identifier) |
| Status | level (INFO/WARNING/ERROR), success (bool), retry_count |
| Performance | duration_ms, task_name, task_id |
| Error | exception_class, error_code |
| Cross-refs | invoice_id, case_id, reconciliation_result_id, review_assignment_id, agent_run_id |
| Trace | trace_id, span_id |
| RBAC (sensitive ops) | actor_primary_role, permission_checked, access_granted |

---

## 3. Decision Log (`DecisionLog`)

**Table**: `agents_decision_log`  
**Purpose**: Every key decision — agent, deterministic, policy, or human — with full rationale.

### Coverage
- Agent decisions (recommendation type, confidence, evidence)
- Deterministic decisions (mode resolution, auto-close, path selection)
- Policy decisions (policy_code, policy_version)
- Human decisions (actor_user_id, actor_primary_role)

### Determinism Flag
`deterministic_flag = True` → rule-based decision  
`deterministic_flag = False` → LLM-generated decision

### Traceability Chain
```
DecisionLog
  ├── agent_run_id → AgentRun
  ├── invoice_id → Invoice
  ├── case_id → APCase
  ├── reconciliation_result_id → ReconciliationResult
  ├── trace_id → Langfuse trace
  ├── rule_name + rule_version → deterministic rule provenance
  ├── policy_code + policy_version → policy provenance
  └── prompt_template_id + prompt_version → LLM provenance
```

---

## 4. Agent Run Traceability

`AgentRun` is the primary execution record:

| Traceability Concern | Fields |
|---------------------|--------|
| What ran | agent_type, agent_definition, llm_model_used |
| When and how long | started_at, completed_at, duration_ms |
| Input/output | input_payload, output_payload, summarized_reasoning |
| Prompt | prompt_version (hash), input_payload._prompt_meta (full composition) |
| Quality | confidence, prompt_tokens, completion_tokens, total_tokens |
| Cost | actual_cost_usd, cost_estimate, cost_currency |
| Actor | actor_user_id, actor_primary_role, actor_roles_snapshot_json, permission_source, access_granted |
| Distributed trace | trace_id, span_id |
| Handoff | handed_off_to (FK to self) |

`AgentMessage` records all messages (system, user, assistant, tool) for full conversation replay.  
`AgentStep` records each tool invocation with input/output data and duration.

---

## 5. Langfuse Observability

### Trace Hierarchy

```
[Celery Task] root trace (task_id.replace("-","") as 32-char hex trace_id)
  └── Pipeline span (e.g. "invoice_extraction", "reconciliation_run", "agent_pipeline")
       ├── Agent span (e.g. "INVOICE_EXTRACTION", "EXCEPTION_ANALYSIS")
       │    └── LLM generation (via LangChain-Langfuse callback)
       │    └── Tool call spans
       └── Per-invoice spans (for reconciliation runner)
```

### Langfuse Session Linkage
`session_id = derive_session_id(case_number, invoice_id, case_id)`  
All traces for the same case/invoice share a `session_id`, enabling full session replay in Langfuse.

### Trace Metadata (`build_observability_context`)
```python
{
    "tenant_id": ...,
    "invoice_id": ...,
    "reconciliation_result_id": ...,
    "actor_user_id": ...,
    "match_status": ...,
    "reconciliation_mode": ...,
    "trigger": "manual" | "auto",
    "source": "deterministic" | "agentic" | "mixed",
    "task_id": ...,
}
```

### Langfuse Scores

All score keys defined in `core/evaluation_constants.py`:

| Score Key Constant | Typical Value | When Scored |
|-------------------|--------------|------------|
| `EXTRACTION_CONFIDENCE` | float [0.0, 1.0] | After extraction LLM call |
| `EXTRACTION_IS_VALID` | 0.0 / 1.0 | After validation |
| `EXTRACTION_IS_DUPLICATE` | 0.0 / 1.0 | After duplicate detection |
| `EXTRACTION_REQUIRES_REVIEW` | 0.0 / 1.0 | After approval gate |
| `EXTRACTION_RESPONSE_REPAIRED` | 0.0 / 1.0 | After response repair |
| `EXTRACTION_OCR_CHAR_COUNT` | int | After OCR |
| `EXTRACTION_SUCCESS` | 0.0 / 1.0 | Pipeline completion |
| `EXTRACTION_APPROVAL_CONFIDENCE` | float | At approval |
| `CASE_PROCESSING_SUCCESS` | 0.0 / 1.0 | After CaseOrchestrator |
| `CASE_REPROCESSED` | 1.0 | If reprocess task ran |
| `RECON_FINAL_SUCCESS` | 1.0 | After reconciliation run |
| `RECON_ROUTED_TO_AGENTS` | 0.0 / 1.0 | If non-MATCHED dispatched |
| `AGENT_PIPELINE_FINAL_CONFIDENCE` | float | After orchestration |
| `AGENT_PIPELINE_AGENTS_EXECUTED_COUNT` | int | After orchestration |
| `AGENT_PIPELINE_AUTO_CLOSE_CANDIDATE` | 0.0 / 1.0 | If auto-close eligible |
| `AGENT_PIPELINE_ESCALATION_TRIGGERED` | 0.0 / 1.0 | If escalation triggered |
| `RBAC_GUARDRAIL` | 0.0 / 1.0 | RBAC enforcement events |
| `RBAC_DATA_SCOPE` | 0.0 / 1.0 | Data scope restriction events |

---

## 6. OpenTelemetry Integration

From requirements.txt:
- `opentelemetry-api==1.40.0`
- `opentelemetry-sdk==1.40.0`
- `opentelemetry-exporter-otlp-proto-http==1.40.0`
- `openinference-instrumentation==0.1.46`
- `openinference-instrumentation-openai==0.1.43`

**OTLP exporter** configured (likely pointing to Langfuse or a self-hosted collector).  
`openinference-instrumentation-openai` instruments all OpenAI API calls automatically for tracing.

---

## 7. Logging Infrastructure

### Log Channels
| Channel | Handler Class | Format | When Active |
|---------|--------------|--------|------------|
| Console | `logging.StreamHandler` | `dev_traced` (debug) / JSON (prod) | Always |
| File | `SafeRotatingFileHandler` | JSON | Always; rotates at 10MB, 5 backups |
| Loki | `SilentLokiHandler` | JSON | Only when `LOKI_ENABLED=true` |

### Log Namespaces
- `apps` — all application code (level=DEBUG)
- `apps.observed` — service observability (level=INFO)
- `apps.action` — user/system action logs (level=INFO)
- `apps.task` — Celery task logs (level=INFO)
- `django` — framework logs (level=INFO)

### DevLogFormatter / JSONLogFormatter
Custom formatters in `core/logging_utils.py`. `BrokenPipeFilter` suppresses common `django.server` broken pipe errors.

---

## 8. Timeline Service

`auditlog/timeline_service.py` builds a unified case timeline by aggregating:
- AuditEvent records for a case
- AgentRun records
- ReviewAssignment actions
- APCaseStage transitions

Used by the Case Console UI to show a unified activity feed.

---

## 9. Governance API (`/api/v1/governance/`)

9 endpoints (from README: "9 governance API endpoints"):
- Agent RBAC compliance metrics
- Audit event queries
- Decision log access
- Agent run summaries
- (specific endpoints not read — inferred from `auditlog/api_urls.py`)

---

## 10. Observability Gaps

| Gap | Risk | Notes |
|-----|------|-------|
| AuditEvent writer call sites not inspected | Medium | Whether all 38+ event types are actually called cannot be confirmed without reading all services |
| No alerting on audit_event with access_granted=False | Medium | Data is captured; no automatic alerting confirmed |
| Loki disabled by default | Low | Centralized log aggregation only available when `LOKI_ENABLED=true` |
| Celery worker health not monitored | Medium | No Beat task for worker health checks; Flower or similar not confirmed |
| OTLP exporter endpoint config not verified | Unknown | `opentelemetry-exporter-otlp-proto-http` installed; OTLP endpoint env var not found in settings.py |
| Cost tracking requires `LLMCostRate` seed data | Medium | `actual_cost_usd` is only computed if rates exist; no fallback |
