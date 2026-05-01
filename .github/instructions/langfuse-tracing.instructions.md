---
description: "Use when adding Langfuse tracing, spans, LLM generation logging, or quality scores to any pipeline, service, or task. Enforces the fail-silent guard pattern, span= argument for score_trace, score naming conventions, trace_id patterns, and the guard-ALL-calls rule."
applyTo: "apps/**/*.py"
---
# Langfuse Observability Conventions

## Import Path
```python
from apps.core.langfuse_client import start_trace, start_span, end_span, log_generation, score_trace
```
For ERP-specific tracing: use `apps.erp_integration.services.langfuse_helpers` instead.

## Fail-Silent Rule (Non-Negotiable)
EVERY Langfuse call MUST be wrapped in `try/except Exception: pass`.
Langfuse errors MUST NEVER propagate to callers. If `LANGFUSE_PUBLIC_KEY` is not set, all functions return `None`.

## Guard Pattern
```python
try:
    _lf_span = start_span(_lf_trace, name="stage_name", metadata={...})
except Exception:
    _lf_span = None
# ... do work ...
try:
    if _lf_span:
        end_span(_lf_span, output={...}, level="ERROR" if failed else "DEFAULT")
except Exception:
    pass
```

## score_trace — ALWAYS pass span=
```python
score_trace(_trace_id, "score_name", float_value, comment="...", span=_lf_trace)
```
Without `span=`, scores are orphaned in Langfuse SDK v4 (real OTel trace_id differs from app-level string).

## Trace ID Conventions
| Context | Trace ID pattern |
|---------|-----------------|
| Extraction task | `uuid4().hex` |
| Agent pipeline | `trace_ctx.trace_id` |
| Reconciliation run | `run.trace_id` or `str(run.pk)` |
| Posting run | `posting_run.trace_id` or `str(posting_run.pk)` |
| ERP submission | `f"erp-{posting_run_id}"` |
| Approval | `f"approval-{approval.pk}"` |
| Review | `f"review-{assignment.pk}"` |
| Case task | `f"case-{case.case_number}"` |

## Standard Score Names (use exact strings)
`reconciliation_match`, `posting_confidence`, `extraction_confidence`, `extraction_success`,
`agent_pipeline_final_confidence`, `agent_confidence`, `agent_tool_success_rate`,
`review_decision`, `review_priority`, `rbac_guardrail`, `erp_resolution_success`,
`erp_cache_hit`, `erp_submission_success`, `case_processing_success`

New score names must be added to the conventions table in copilot-instructions.md.

## Score Value Conventions
- Qualitative outcomes: MATCHED/SUCCESS=1.0, PARTIAL=0.5, REVIEW=0.3, FAILED/UNMATCHED=0.0
- Boolean outcomes: 1.0 or 0.0
- Counts: raw integer cast to float
- Rates/confidence: 0.0 to 1.0

## end_span in finally (Required)
```python
_lf_span = None
try:
    _lf_span = start_span(_lf_trace, "stage")
    result = do_work()
finally:
    try:
        if _lf_span:
            end_span(_lf_span, output={"result": result})
    except Exception:
        pass
```
