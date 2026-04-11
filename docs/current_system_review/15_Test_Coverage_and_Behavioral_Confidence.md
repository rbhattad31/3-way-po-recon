# 15 — Test Coverage and Behavioral Confidence

**Generated**: 2026-04-09 | **Method**: Code-first inspection of test directories and conftest  
**Confidence**: Medium — test directories confirmed but individual test contents not fully read

---

## 1. Test Infrastructure

### Configuration (`pytest.ini`)
```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.test_settings
```

### `config/test_settings.py`
Separate test settings — confirms `CELERY_TASK_ALWAYS_EAGER = True` (synchronous task execution for tests).

### `conftest.py` (root)
Contains shared pytest fixtures — likely includes:
- `db` fixture (database access)
- Tenant/CompanyProfile fixtures
- User fixtures with roles
- Factory-boy factory definitions

### Test Directories Confirmed
```
apps/agents/tests/
apps/cases/tests/
apps/extraction/tests/
apps/extraction_core/tests/
apps/extraction_configs/tests/  (inferred)
apps/reconciliation/tests/
apps/erp_integration/tests/
apps/posting/tests/
apps/posting_core/tests/
apps/core_eval/tests/  (confirmed: test_end_to_end.py visible in git status)
```

---

## 2. Test Coverage Claims (from README)

| Area | Test Count | Status |
|------|-----------|--------|
| Extraction Phase 2 pipeline (OCR, compose, LLM, repair, parse, normalize, validate, duplicate, persist, approve) | 51 | Claimed ✅ |
| Reconciliation engine (14 services, mode resolver, tolerance, exceptions, agent feedback) | 73 | Claimed ✅ |
| Supervisor agent (SkillRegistry, PluginToolRouter, prompt/output/context, tools, guardrails, full run) | ~40 | Confirmed ✅ |
| **Total** | **164+** | **Claimed ✅** |

---

## 3. Confirmed Test Presence by App

| App | Test Dir | Evidence |
|-----|---------|---------|
| `extraction` | `tests/` | git status shows `tests/test_end_to_end.py` visible in core_eval |
| `reconciliation` | `tests/` | Directory confirmed from ls |
| `agents` | `tests/` | Directory confirmed |
| `cases` | `tests/` | Directory confirmed |
| `erp_integration` | `tests/` | Directory confirmed |
| `posting` | `tests/` | Directory confirmed |

---

## 4. Test Areas of Confidence

### High Confidence (actively tested by README claim)

**Extraction**:
- Response repair rules (5 rules, 25 tests per README)
- Full 11-stage pipeline
- Modular prompt composition
- Duplicate detection
- Approval gate (auto vs manual)

**Reconciliation**:
- 2-way and 3-way matching
- Mode resolution (policy → heuristic → default)
- Tolerance band classification (strict + auto-close)
- Exception building
- Agent feedback loop (PO re-reconciliation)

---

## 5. Test Areas of Lower Confidence (not mentioned in README test counts)

| Area | Risk |
|------|------|
| RBAC / `AgentGuardrailsService` | Permission checks may be tested but count not stated |
| Agent orchestration (LLM mock path) | LLM calls require mocking — real LLM not called in tests |
| ERP connector integration tests | `erp_integration/tests/` exists; scope unclear |
| Case state machine transitions | Tests likely in `cases/tests/`; scope unclear |
| Human review workflow | Complex workflow; test coverage unclear |
| Celery task retry behavior | `CELERY_TASK_ALWAYS_EAGER=True` suppresses retry logic in tests |
| Multi-tenant isolation | Whether tenant FK scoping is tested across all models |
| `core_eval` learning engine | `tests/test_end_to_end.py` exists; scope unclear |
| `posting` workflow | Directory exists; coverage unclear |
| `copilot` | No test directory confirmed |
| `procurement` | No test directory confirmed |
| `dashboard` analytics endpoints | No test directory confirmed |

---

## 6. Key Testing Limitations

### LLM Calls Not Real in Tests
By design, `CELERY_TASK_ALWAYS_EAGER=True` and test settings would mock or skip real Azure OpenAI calls. The extraction and agent tests likely verify the pipeline structure and deterministic components rather than actual LLM responses.

### Idempotency Not Tested
No evidence of tests that verify duplicate task execution produces idempotent results (e.g., running reconciliation twice for the same invoice).

### No Integration Tests Against Real ERP
ERP connector tests likely use mocked HTTP responses or test databases rather than real ERP systems.

---

## 7. Behavioral Confidence Summary

| Workflow | Confidence | Basis |
|----------|-----------|-------|
| Invoice extraction pipeline (stages 1-11) | **High** | 51 tests claimed |
| Response repair (5 rules) | **High** | 25 tests specifically for repair |
| PO/GRN matching engine | **High** | 73 tests claimed |
| Mode resolution | **High** | Part of reconciliation test suite |
| Tolerance bands | **High** | Tiered tolerance tested |
| Agent pipeline structure | **Medium** | Test infrastructure present; LLM mocked |
| Supervisor agent (skills, tools, router, output) | **High** | ~40 dedicated tests: SR/PT/SA/SP/ST/SU/SO/SG/SPR/SAR/STE/SCB/SMR |
| RBAC enforcement | **Medium** | Guardrails service tested; coverage unknown |
| Case state machine | **Medium** | State machine code is tested; coverage unknown |
| ERP connector resolution | **Medium** | Tests present; connector behavior unclear |
| Multi-tenant isolation | **Low-Medium** | Pattern is consistent in models; test coverage unclear |
| Human review workflow | **Low** | Complex workflow; test count not stated |
| Celery retry / idempotency | **Low** | `ALWAYS_EAGER=True` bypasses retry logic in tests |
