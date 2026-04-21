# 11 — Documentation Gap Assessment

**Generated**: 2026-04-09 | **Method**: Comparing existing docs against code-first findings  
**Existing docs reviewed**: README.md, docs/PROJECT.md, docs/AGENT_ARCHITECTURE.md, docs/EXTRACTION_AGENT.md, docs/LANGFUSE_INTEGRATION.md, docs/CELERY.md, docs/ERP_INTEGRATION.md, docs/DATABASE.md, docs/MULTI_TENANT.md, docs/EVAL_LEARNING.md, docs/RECON_AGENT.md, docs/POSTING_AGENT.md, docs/PROCUREMENT.md, docs/OBSERVABILITY_UPGRADE_SUMMARY.md, deploy/DEPLOYMENT.md, deploy/MONITORING_OPS.md

---

## 1. Documentation Sources Reviewed

| Source | Type | Notes |
|--------|------|-------|
| `README.md` | Top-level overview | Recent, mostly accurate |
| `docs/PROJECT.md` | Comprehensive architecture reference | Likely has some drift |
| `docs/AGENT_ARCHITECTURE.md` | Agent framework doc | Pre-dates system agents (5 deterministic agents) |
| `docs/EXTRACTION_AGENT.md` | Phase 2 extraction pipeline | Aligns with code |
| `docs/LANGFUSE_INTEGRATION.md` | Langfuse SDK integration | Check for Langfuse 4.x quirks |
| `docs/CELERY.md` | Celery task documentation | Beat schedule incomplete |
| `docs/ERP_INTEGRATION.md` | ERP connector framework | Aligns with code structure |
| `docs/DATABASE.md` | DB model reference | Likely drift on new fields |
| `docs/MULTI_TENANT.md` | Multi-tenancy design | Aligns with CompanyProfile FK pattern |
| `docs/EVAL_LEARNING.md` | core_eval framework | Active; likely current |
| `docs/RECON_AGENT.md` | Reconciliation agent | Needs verification against agent feedback loop |
| `docs/POSTING_AGENT.md` | Posting workflow | Needs verification |
| `docs/PROCUREMENT.md` | Procurement intelligence | Largely aligned for request intake, validation, recommendation, prefill, and market intelligence; benchmark runtime currently drifts from the full should-cost design |
| `docs/OBSERVABILITY_UPGRADE_SUMMARY.md` | Observability work | Likely current (recent work) |
| `deploy/DEPLOYMENT.md` | Production deployment guide | Likely valid |
| `deploy/MONITORING_OPS.md` | Monitoring and operations | Valid, needs Celery Beat section |
| `docs/debugging/extraction_ocr_debugging.md` | OCR debugging guide | Niche reference doc |

---

## 2. Valid Documentation (Still Accurate)

| Document | Valid Areas |
|----------|------------|
| `README.md` | Tech stack, architecture diagram, Quick Start, most feature descriptions, env var list |
| `docs/EXTRACTION_AGENT.md` | 11-stage pipeline, modular prompt system, response repair rules, credit system, auto-approval |
| `docs/ERP_INTEGRATION.md` | Connector types, CacheService L1/L2/L3, freshness TTLs |
| `docs/MULTI_TENANT.md` | CompanyProfile FK row-level isolation pattern |
| `docs/LANGFUSE_INTEGRATION.md` | Trace hierarchy, score names, prompt management via production label |
| `deploy/DEPLOYMENT.md` | Nginx, Gunicorn, Systemd — production deployment steps |
| `docs/OBSERVABILITY_UPGRADE_SUMMARY.md` | OpenTelemetry + Langfuse integration details |

---

## 3. Outdated or Partially Incorrect Documentation

### README.md — App Count
- **States**: "26 Django apps"
- **Code reality**: 21 in `INSTALLED_APPS` (22 including `reviews` stub)
- **Impact**: Minor — misleading but not functionally incorrect
- **Fix**: Update count to 21 (or 22 with stub)

### README.md — Celery Beat Status
- **States**: "Celery Beat (scheduled tasks) — Not started ⬜"
- **Code reality**: `config/celery.py` has `process_approved_learning_actions` on 30-min schedule
- **Impact**: Medium — operators may not know to run Celery Beat
- **Fix**: Update to "Partial — learning actions beat task implemented"

### `docs/AGENT_ARCHITECTURE.md` — System Agents
- **States**: 8 agents in the framework
- **Code reality**: 8 LLM agents + 5 deterministic system agents (13 total in `AGENT_CLASS_REGISTRY`)
- **Impact**: Medium — system agents handle all tail-position routing/summary without LLM
- **Fix**: Add system agents section

### `docs/CELERY.md` — Beat Schedule
- **States**: (likely documents no beat schedule or placeholder)
- **Code reality**: `process_approved_learning_actions` every 30 min
- **Fix**: Document the beat schedule and requirement to run `celery -A config beat`

### `docs/DATABASE.md` — Invoice Model Fields
- **Likely state**: Does not include Phase 2 fields
- **Code reality**: Invoice has `vendor_tax_id`, `buyer_name`, `due_date`, `tax_percentage`, `tax_breakdown` (Phase 2 additions)
- **Fix**: Add these fields to the Invoice model reference

---

## 4. Docs Contradicted by Code

| Doc Claim | Code Reality | Mismatch Type |
|-----------|-------------|--------------|
| README: "26 Django apps" | 21 in INSTALLED_APPS | Count incorrect |
| README: "Celery Beat not started" | Beat task implemented for learning actions | Status incorrect |
| AGENT_ARCHITECTURE.md: 8 agents | 13 in registry (8 LLM + 5 system) | Incomplete |
| (Inferred) PROJECT.md ReasoningPlanner | Available but disabled by default; not documented | Missing feature |

---

## 5. Undocumented Current Features / Behaviors

| Feature | Where to find in code | Notes |
|---------|----------------------|-------|
| System agents (5 deterministic) | `agents/services/system_agent_classes.py` | Not in any existing doc |
| ReasoningPlanner (LLM plan) | `agents/services/reasoning_planner.py` | Behind env flag; not documented |
| `AgentDefinition` DB record governance fields | `agents/models.py` (lifecycle_status, owner_team, prohibited_actions, etc.) | Not documented |
| `LLMCostRate` model | `agents/models.py` | Cost tracking system not documented |
| `AgentOrchestrationRun` model | `agents/models.py` | Top-level pipeline record not documented |
| Scope-restricted `UserRole` (scope_json) | `accounts/rbac_models.py` | Partial implementation, not documented |
| `MenuConfig` model | `accounts/rbac_models.py` | DB-driven menu visibility, not documented |
| 5 deterministic response repair rules | `extraction/services/response_repair_service.py` | Mentioned in README but not detailed in docs |
| `ERP_ENABLE_LIVE_REFRESH_ON_MISS/STALE` flags | `config/settings.py` | ERP live refresh policy not in ERP_INTEGRATION.md |
| `line_match_llm_fallback.py` | `reconciliation/services/` | Unknown if active; not documented |
| `copilot` app scope | `apps/copilot/` | Not documented beyond URL registration |
| `procurement` and `benchmarking` module detail | `apps/procurement/`, `apps/benchmarking/` | Split implementation exists; BENCHMARK runs currently route through a compatibility bridge in `apps.benchmarking` |
| `core_eval` beat task | `config/celery.py` | Beat schedule not in CELERY.md |
| `safe_retry()` utility | `core/utils.py` | Used in all tasks; not documented |
| `dispatch_task()` utility | `core/utils.py` | Task dispatch with tenant propagation; not documented |
| Extraction QR decoder | `extraction/services/qr_decoder_service.py` | Present but not in docs |
| Extraction recovery lane | `extraction/services/recovery_lane_service.py` | Present but not in docs |

---

## 6. Recommended Documentation Actions

### High Priority (correctness fixes)

1. **Update README.md app count** (21, not 26)
2. **Update README.md Celery Beat status** (partial, not "Not started")
3. **Update `docs/AGENT_ARCHITECTURE.md`** to add system agents section and correct agent count
4. **Update `docs/DATABASE.md`** with Phase 2 Invoice model fields

### Medium Priority (new capabilities)

5. **New: `docs/SYSTEM_AGENTS.md`** — Document all 5 system agents, their purpose, and how they differ from LLM agents
6. **New: `docs/COST_TRACKING.md`** — LLMCostRate model, token tracking on AgentRun, cost reporting
7. **Update `docs/CELERY.md`** — Add beat task, task chain diagram, `safe_retry` / `dispatch_task` utilities
8. **Update `docs/ERP_INTEGRATION.md`** — Add live refresh policy settings documentation

### Low Priority (nice-to-have)

9. **`docs/COPILOT.md`** — Document the copilot feature scope
10. **`docs/PROCUREMENT.md`** — Align benchmark sections with the current compatibility-bridge runtime and the split between `apps/procurement` and `apps/benchmarking`
11. **Update `docs/LANGFUSE_INTEGRATION.md`** — Document Langfuse 4.x SDK specifics and score key catalog
12. **Operations runbook** — Document how to run Celery Beat, handle task failures, ERP cache invalidation

---

## 7. Cleanup Recommendations

| Action | Target |
|--------|--------|
| Mark `reviews` app as deprecated/stub | In README app list and INSTALLED_APPS comment — already done; add to PROJECT.md |
| Remove references to `extraction_documents` app | Cleanup historical migration references in docs |
| Consolidate agent count to "13 agents (8 LLM + 5 system)" | Throughout all agent documentation |
| Add `ReasoningPlanner` section with "experimental/disabled" label | AGENT_ARCHITECTURE.md |
| Document `AGENT_REASONING_ENGINE_ENABLED` env var | README env var section |
