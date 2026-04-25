# Test Documentation -- 3-Way PO Reconciliation Platform

> **Generated**: 2026-04-25
> **Test Framework**: pytest 8.x + pytest-django 4.12 + factory-boy 3.3
> **Total Tests**: ~788 tests across 55 test files (651 automated + 137 eval/learning; manual functional test cases in §10)
> **Coverage Domains**: 9 of 21 Django apps have automated test suites

---

## Table of Contents

1. [Test Infrastructure](#1-test-infrastructure)
2. [Test Execution Guide](#2-test-execution-guide)
3. [Test Strategy Overview](#3-test-strategy-overview)
4. [Test Inventory by App](#4-test-inventory-by-app)
   - 4.1 [Core (`apps/core/`)](#41-core-appscore)
   - 4.2 [Accounts (`apps/accounts/`)](#42-accounts-appsaccounts)
   - 4.3 [Reconciliation (`apps/reconciliation/`)](#43-reconciliation-appsreconciliation)
   - 4.4 [Agents (`apps/agents/`)](#44-agents-appsagents)
   - 4.5 [Extraction (`apps/extraction/`)](#45-extraction-appsextraction)
   - 4.6 [Cases (`apps/cases/`)](#46-cases-appscases)
   - 4.7 [Reviews (`apps/cases/` -- merged from `apps/reviews`)](#47-reviews-appscases----merged-from-appsreviews)
   - 4.8 [Posting (`apps/posting/`)](#48-posting-appsposting)
   - 4.9 [Posting Core (`apps/posting_core/`)](#49-posting-core-appsposting_core)
   - 4.10 [ERP Integration (`apps/erp_integration/`)](#410-erp-integration-appserp_integration)
   - 4.11 [Eval & Learning (`apps/core_eval/`)](#411-eval--learning-appscore_eval)
5. [Factory & Fixture Catalog](#5-factory--fixture-catalog)
6. [Mocking Strategy Reference](#6-mocking-strategy-reference)
7. [Test Categorisation Matrix](#7-test-categorisation-matrix)
8. [Scenario Coverage Map](#8-scenario-coverage-map)
9. [Gaps & Recommendations](#9-gaps--recommendations)
10. [AP Finance Agents — Manual Functional Test Guide](#10-ap-finance-agents--manual-functional-test-guide)

---

## 1. Test Infrastructure

### 1.1 Configuration Files

| File | Purpose |
|------|---------|
| `pytest.ini` | Root config: `DJANGO_SETTINGS_MODULE = config.test_settings`, verbose/short-traceback defaults |
| `config/test_settings.py` | Imports production settings, overrides `DATABASES` to SQLite `:memory:`, stubs `MySQLdb`, forces `CELERY_TASK_ALWAYS_EAGER = True` |
| `conftest.py` | Root conftest: installs a meta-path finder (`_SQLiteSettingsPatcher`) that intercepts `config.settings` import and rewires `DATABASES` to SQLite before Django initialises. This guarantees MySQL is never contacted during tests. |
| `requirements-test.txt` | Test dependencies: `pytest>=8.0`, `pytest-django>=4.8`, `factory-boy>=3.3`, `pytest-cov>=5.0`, plus full production deps |

### 1.2 Database Strategy

All DB-backed tests use **SQLite in-memory** via a custom import hook in the root `conftest.py`. This avoids requiring a MySQL server for local development and CI. The hook:

1. Fires in `pytest_configure` (before Django setup).
2. Installs a `MetaPathFinder` that intercepts the `config.settings` module load.
3. Replaces the MySQL `DATABASES` dict with an SQLite `:memory:` configuration.
4. Patching happens at import-time, before Django's `setup()` call.

### 1.3 Celery Behaviour

`CELERY_TASK_ALWAYS_EAGER = True` in test settings ensures all Celery tasks execute synchronously in-process. No Redis or message broker is needed for tests.

### 1.4 Langfuse Handling

Tests never contact a Langfuse server. The `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` env vars are unset in test environments, causing `get_client()` to return `None`. Tests that exercise Langfuse-instrumented code either:
- Rely on the fail-silent pattern (all Langfuse calls are no-ops when the client is `None`).
- Explicitly patch `apps.core.langfuse_client` functions to verify call patterns or inject errors.

---

## 2. Test Execution Guide

### Run the entire suite

```bash
python -m pytest
```

### Run a specific app's tests

```bash
python -m pytest apps/reconciliation/tests/ -v
python -m pytest apps/core/tests/ -v
```

### Run a single test file

```bash
python -m pytest apps/reconciliation/tests/test_tolerance_engine.py -v
```

### Run a single test class or method

```bash
python -m pytest apps/agents/tests/test_policy_engine.py::TestRule1MatchedSkip -v
python -m pytest apps/extraction/tests/test_credit_service.py::TestReserve::test_reserve_success -v
```

### Run only pure unit tests (no DB)

```bash
python -m pytest -m "not django_db"
```

### Run only DB-backed tests

```bash
python -m pytest -m "django_db"
```

### Run with coverage

```bash
python -m pytest --cov=apps --cov-report=html
```

---

## 3. Test Strategy Overview

### 3.1 Testing Pyramid

The project follows a **bottom-up testing pyramid**:

```
                    +-------------------+
                    |    Integration    |   ~55 DB-backed test classes
                    |   (DB + Service)  |   Real models, factories, patches
                    +-------------------+
                   /                     \
          +-------------------------------+
          |        Unit Tests             |   ~100 pure test classes
          | (Services, Engines, Helpers)  |   MagicMock, no DB
          +-------------------------------+
```

- **Pure unit tests** (~65%): No database. Service logic, engines, helpers, and validators are tested with `MagicMock` inputs. Fast (~2s for 118 tests).
- **DB-backed integration tests** (~35%): Use `@pytest.mark.django_db` and factory-boy to create real model instances in SQLite. Verify service + ORM interactions. Slower (~60s for 15 complex runner tests).

### 3.2 Naming & ID Conventions

Many test classes assign stable IDs to test methods for traceability:

| Convention | Example | Used in |
|------------|---------|---------|
| `TE-01` | Tolerance Engine Test 01 | `test_tolerance_engine.py` |
| `HM-01` | Header Match Test 01 | `test_header_match_service.py` |
| `LM-01` | Line Match Test 01 | `test_line_match_service.py` |
| `GM-01` | GRN Match Test 01 | `test_grn_match_service.py` |
| `EB-01` | Exception Builder Test 01 | `test_exception_builder.py` |
| `CS-01` | Classification Service Test 01 | `test_classification_service.py` |
| `MR-01` | Mode Resolver Test 01 | `test_mode_resolver.py` |

### 3.3 Key Design Principles

1. **Fail-silent contract**: Any service that wraps external calls (Langfuse, LLM, ERP) must be tested for fail-silent behaviour -- injected exceptions must never propagate to callers.
2. **Mock at boundaries**: External services (Langfuse SDK, OpenAI, Azure DI, AuditService) are patched; internal logic is tested through the real code path.
3. **Factory-style fixtures**: `factory_boy` factories build complex model graphs for DB tests. Pure tests use lightweight `MagicMock` helpers.
4. **Parametrize for combinatorics**: `@pytest.mark.parametrize` is used extensively for permission matrices, status transitions, and score mappings.

---

## 4. Test Inventory by App

### 4.1 Core (`apps/core/`)

**6 test files, ~91 tests. Mix of pure unit and DB-backed.**

The core module tests cover shared utilities, the Langfuse client wrapper, the observability helpers library, the evaluation constants taxonomy, Django middleware, and Celery task integration.

#### 4.1.1 `test_utils.py` -- Utility Functions (76 tests)

Tests the foundational text normalisation, numeric parsing, and tolerance comparison functions used throughout the platform.

| Test Class | Tests | Functionality Covered |
|------------|-------|-----------------------|
| `TestNormalizeString` | 8 | Whitespace collapse, Unicode stripping, case folding, None/empty handling |
| `TestNormalizePONumber` | 8 | PO prefix stripping (`PO-`, `P.O.`), leading zeros, None, already-clean values |
| `TestNormalizeInvoiceNumber` | 6 | Invoice prefix stripping (`INV-`, `#`), hyphen normalisation, None |
| `TestParseDate` | 8 | ISO-8601, `DD/MM/YYYY`, `MM-DD-YYYY`, None, invalid string, timezone awareness |
| `TestToDecimal` | 12 | Integer/float/string/Decimal input, comma formatting (`1,000.50`), None, empty, negative, `Decimal("NaN")` |
| `TestParsePercentage` | 6 | `15%`, `0.15`, `15`, negative, None, empty |
| `TestCalculateTaxPercentage` | 5 | Standard calc, zero subtotal, None inputs, floating-point rounding |
| `TestResolveTaxPercentage` | 3 | Explicit value priority over calculation, fallback chain |
| `TestPctDifference` | 6 | Standard diff, zero base, same value, None handling, asymmetric tolerance |
| `TestWithinTolerance` | 9 | Within/exceeded/boundary, zero tolerance, None inputs, negative values |
| `TestNormalizeCategory` | 5 | Goods/services/travel/mixed classification, empty input |

**Why it matters**: These functions underpin every matching and extraction comparison. Regressions here would cascade across the entire reconciliation and extraction pipeline.

---

#### 4.1.2 `test_langfuse_client.py` -- Langfuse SDK Wrapper (38 tests)

Tests the fail-silent Langfuse wrapper that all pipeline code depends on. Core contract: **every function must be a no-op when Langfuse is not configured, and must never raise regardless of input**.

| Test Class | Tests | Functionality Covered |
|------------|-------|-----------------------|
| `TestGetClient` | 4 | Returns `None` without env vars, caches result, returns client when configured, handles SDK init crash |
| `TestStartTrace` | 4 | No-op when disabled, accepts all kwargs without raising, returns span when active, handles creation failure |
| `TestStartSpan` | 4 | No-op with `None` parent, creates child span, handles parent crash |
| `TestEndSpan` | 5 | No-op with `None`, calls `update(output=...)` + `end()`, skips update without output, swallows `end()` exceptions |
| `TestScoreTrace` | 4 | No-op when disabled, calls `create_score` with correct positional args (uses `RECON_RECONCILIATION_MATCH` constant), empty comment becomes `None`, swallows API errors |
| `TestSlugConversion` | 4 | Dot-to-dash conversion, round-trip fidelity for single-dot names |
| `TestFlush` | 3 | No-op when disabled, calls `client.flush()`, swallows errors |
| `TestPushPrompt` | 3 | Returns `False` when disabled, `True` on success, `False` on SDK error |
| `TestGetPrompt` | 7 | Returns `None` when disabled/on error, extracts system message from prompt list, falls back to first message, handles empty list |

**Test technique**: Uses `monkeypatch` to reset the module-level `_client` singleton between tests. `disabled_client` fixture strips Langfuse env vars. `mock_lf_client` fixture provides a `MagicMock` standing in for the real SDK.

---

#### 4.1.3 `test_evaluation_constants.py` -- Score Name Taxonomy (29 tests)

Validates the integrity of the centralised `evaluation_constants.py` module (151+ score name constants).

| Test Class | Tests | Functionality Covered |
|------------|-------|-----------------------|
| `TestNoDuplicateValues` | 1 | Every score constant maps to a unique string value (excludes known backward-compat aliases like `POSTING_REQUIRES_REVIEW` / `POSTING_FINAL_REQUIRES_REVIEW`) |
| `TestValueFormat` | 2 | All values are non-empty strings matching `^[a-z][a-z0-9_]*$` (lowercase underscore convention) |
| `TestDomainCoverage` | 7 | Each domain prefix (`EXTRACTION_`, `RECON_`, `AGENT_`, `CASE_`, `REVIEW_`, `POSTING_`, `ERP_`) has at least one constant (parametrized) |
| `TestCrossCuttingConstants` | 5 | Spot-checks `RBAC_GUARDRAIL`, `RBAC_DATA_SCOPE`, `COPILOT_SESSION_LENGTH`, `LATENCY_OK`, `FALLBACK_USED` |
| `TestLatencyThresholds` | 5 | All thresholds are positive; verifies ERP=5000ms, LLM=20000ms, OCR=30000ms, DB=2000ms |
| `TestWellKnownValues` | 8 | Pin-checks critical values that external dashboards/evals depend on (`reconciliation_match`, `extraction_confidence`, `agent_confidence`, `posting_confidence`, `review_decision`, `erp_resolution_success`, `erp_submission_success`, `case_processing_success`) |
| `TestRootTraceNames` | 1 | Verifies >= 1 `TRACE_*` root trace name constant exists |

**Why it matters**: Langfuse evaluation dashboards query by score name. A typo or duplicate would silently break evaluation pipelines. These tests act as a schema contract.

---

#### 4.1.4 `test_observability_helpers.py` -- Cross-Flow Correlation (50 tests)

Tests the cross-flow observability helpers that provide consistent session attribution, metadata sanitisation, and latency scoring across all pipelines.

| Test Class | Tests | Functionality Covered |
|------------|-------|-----------------------|
| `TestDeriveSessionId` | 5 | Priority chain: `invoice-{id}` > `upload-{id}` > `case-{id}` > `None`; falsy `invoice_id=0` falls through |
| `TestBuildObservabilityContext` | 5 | Includes populated fields, excludes `None`/empty-string, returns `{}` when nothing provided, includes all 19 fields when fully populated |
| `TestMergeTraceMetadata` | 5 | Left-to-right merge, later-wins on conflict, filters `None` values, handles `None` base, handles no extras |
| `TestSanitizeLangfuseMetadata` | 9 | Strips sensitive keys (`api_key`, `password`), truncates large text fields (`ocr_text`), truncates long strings (>2000 chars), truncates large lists (>50 items), sanitises nested dicts, handles `None`/empty/broken input |
| `TestSanitizeSummaryText` | 5 | Strips non-ASCII (Unicode arrows, fancy quotes), truncates to `max_length`, returns empty for `None`/empty, preserves plain ASCII |
| `TestLatencyOk` | 6 | Within/at/over threshold, zero latency, float inputs, invalid input returns `0.0` |
| `TestScoreLatency` | 3 | No-op with `None` observation, calls `score_observation_safe`, never raises on error |
| `TestBuildExtractionEvalMetadata` | 2 | Includes provided fields, excludes `None` |
| `TestBuildReconEvalMetadata` | 2 | Includes provided fields, default `po_found=False` / `exception_count=0` |
| `TestBuildAgentEvalMetadata` | 1 | Includes `planned_agents`/`executed_agents` lists |
| `TestBuildCaseEvalMetadata` | 1 | Includes `case_id`, `review_required` |
| `TestBuildPostingEvalMetadata` | 1 | Includes `is_touchless`, `issue_count` |
| `TestBuildErpSpanMetadata` | 2 | Includes ERP fields, excludes `None` |
| `TestErpConstants` | 3 | Validates ERP error constants (`timeout`, `unauthorized`, `rate_limited`, `unknown_error`), source constants (`CACHE`, `API`, `MIRROR_DB`, `DB_FALLBACK`, `NONE`), freshness constants (`fresh`, `stale`, `unknown`) |

---

#### 4.1.5 `test_middleware.py` -- Django Middleware (14 tests, DB-backed)

Tests the three custom middleware classes that handle authentication, RBAC, and distributed tracing.

| Test Class | Tests | Functionality Covered |
|------------|-------|-----------------------|
| `TestLoginRequiredMiddleware` | 5 | Anonymous redirect to login, exempt paths (`/admin/`, `/accounts/`, `/api/`, `/health/`), nested exempt paths, authenticated passthrough |
| `TestRBACMiddleware` | 3 | RBAC permission cache warming for authenticated users, no-op for anonymous, graceful handling of users without RBAC methods |
| `TestRequestTraceMiddleware` | 7 | TraceContext creation, `X-Trace-ID`/`X-Request-ID` response headers, incoming request ID respected, UI/API source layer detection, thread-local cleanup, RBAC enrichment for authenticated users |

---

#### 4.1.6 `test_celery_tasks.py` -- Celery Task Integration (5 tests, DB-backed)

Tests the core Celery task entry points for reconciliation, agent orchestration, and extraction.

| Test Class | Tests | Functionality Covered |
|------------|-------|-----------------------|
| `TestRunReconciliationTask` | 2 | Successful run returns summary dict with `status`, `run_id`, `matched`, `agent_tasks_dispatched`; empty invoice list returns error |
| `TestRunAgentPipelineTask` | 2 | Non-existent ReconciliationResult returns error; successful execution returns outcome dict |
| `TestProcessInvoiceUploadTask` | 1 | Non-existent upload returns error dict |

---

### 4.2 Accounts (`apps/accounts/`)

**2 test files, ~21 tests. All DB-backed.**

Tests the enterprise RBAC permission engine and data-scope authorisation.

#### 4.2.1 `test_rbac_has_permission.py` -- Permission Resolution Engine (21 tests)

Tests the `has_permission()` logic that resolves whether a user has a specific permission code, following the resolution chain: Admin bypass -> DENY override -> ALLOW override -> Role-Permission matrix -> Default DENY.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestAdminBypass` | 3 | Superuser + staff bypasses all checks, including non-existent permissions |
| `TestDenyOverride` | 3 | `UserPermissionOverride(override_type="DENY")` overrides role grants, even for admin-like roles |
| `TestAllowOverride` | 2 | `UserPermissionOverride(override_type="ALLOW")` grants permission even without any role assignment |
| `TestRoleLevelPermission` | 8 | Role-Permission matrix: granted via `RolePermission`, denied when perm not in role, multi-role union (user with 2 roles), expired `UserRole` exclusion (date-based expiry), inactive role exclusion |
| `TestDefaultDeny` | 2 | No role and no override = denied; user with role but no matching permission = denied |
| `TestPermissionCache` | 3 | `_perm_cache` populated after first check, cache reused on second check, cache invalidation on role change |

**Helper**: `make_user_with_role_and_perm(perm_code)` -- creates a full User -> UserRole -> Role -> RolePermission -> Permission chain in one call.

---

#### 4.2.2 `test_rbac_scope.py` -- Data-Scope Authorisation (19 tests)

Tests the `UserRole.scope_json`-based data-scope restriction system that limits what business units and vendor IDs a user can access.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestGetActorScope` | 8 | Extracts `allowed_business_units`/`allowed_vendor_ids` from `scope_json`, ADMIN/SYSTEM_AGENT bypass (returns unrestricted), multi-role union of scopes, expired role exclusion, `None` scope_json = unrestricted |
| `TestScopeValueAllowed` | 5 | Single-value checks: value in allowed list, value not in list, empty list = denied, `None` list = unrestricted |
| `TestAuthorizeDataScope` | 6 | Full `authorize_data_scope()` integration: filters `ReconciliationResult` queryset by vendor/BU scope, ADMIN sees everything, AP_PROCESSOR restricted to allowed vendors, combined BU + vendor restriction |

---

### 4.3 Reconciliation (`apps/reconciliation/`)

**10 test files, ~99 tests. Mix of pure unit and DB-backed.**

The reconciliation tests are the most comprehensive module, covering the complete 3-way matching pipeline from PO lookup through classification.

#### 4.3.1 `test_tolerance_engine.py` -- Tolerance Comparison Engine (18 tests, pure unit)

Tests the core numeric comparison engine used for quantity, price, and amount tolerance checks.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestQuantityComparisons` | 6 | Within 2% tolerance, boundary (exactly 2%), exceeded, large delta, zero quantity, negative values |
| `TestPriceComparisons` | 2 | Within 1% tolerance, exceeded |
| `TestNoneHandling` | 3 | `None` invoice value, `None` PO value, both `None` |
| `TestZeroBase` | 2 | Zero PO value (division-by-zero guard), zero invoice value |
| `TestCustomThresholds` | 2 | Custom 5%/3%/3% thresholds (auto-close band), custom tight 0.5%/0.1%/0.1% |
| `TestFieldComparisonData` | 3 | Output data class structure: `passed`, `deviation_pct`, `base_value`, `compare_value` |

---

#### 4.3.2 `test_po_lookup_service.py` -- Purchase Order Lookup (13 tests, DB-backed)

Tests the 3-strategy PO lookup chain: exact match -> normalised match -> vendor+amount discovery.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestExactMatch` | 3 | Exact PO number hit, case-insensitive match, prefix-stripped match |
| `TestNormalizedMatch` | 2 | Normalised lookup (`PO-001` matches `PO001`), no normalised match falls through |
| `TestVendorAmountDiscovery` | 7 | Vendor+amount within 1% tolerance, amount outside tolerance, wrong vendor, multiple POs (ambiguous = not found), closed PO excluded, `status=OPEN` filter, no vendor link on invoice |
| `TestNotFound` | 1 | No match across all strategies = `found=False` |

---

#### 4.3.3 `test_mode_resolver.py` -- 2-Way / 3-Way Mode Resolution (12 tests, mixed)

Tests the 3-tier mode resolution cascade: policy lookup -> heuristic -> config default.

| Test Class | Tests | DB? | Scenario |
|------------|-------|-----|----------|
| `TestModeResolverDisabled` | 1 | No | Resolver disabled via config = returns `None` |
| `TestModeResolverFallback` | 2 | No | No policy, no heuristic match = config default (`TWO_WAY` or `THREE_WAY`) |
| `TestPolicyResolution` | 5 | Yes | Policy match by vendor, by item category, priority ordering (lower number wins), expired policy excluded, inactive policy excluded |
| `TestHeuristicResolution` | 3 | No | Service keywords (`consulting`, `subscription`) -> `TWO_WAY`, stock keywords (`HSN`, `quantity`) -> `THREE_WAY`, ambiguous -> fallback |
| `TestNoInvoiceLines` | 1 | Yes | Invoice with no line items -> falls back to default |

---

#### 4.3.4 `test_header_match_service.py` -- Invoice/PO Header Matching (13 tests, pure unit)

Tests header-level field comparisons between Invoice and PO.

| ID | Test | Scenario |
|----|------|----------|
| HM-01 | Vendor match via FK | Both reference same `Vendor` object |
| HM-02 | Vendor match via name | Same `vendor_name` string, different FK |
| HM-03 | Vendor mismatch | Different vendor FK and name |
| HM-04 | Currency match (case-insensitive) | `SAR` vs `sar` |
| HM-05 | Currency mismatch | `SAR` vs `USD` |
| HM-06 | Amount within tolerance | `1000.00` vs `1010.00` (1% of 1000) |
| HM-07 | Amount exceeded | `1000.00` vs `1050.00` (5% > 1% threshold) |
| HM-08 | Tax match | Both `15%` |
| HM-09 | Tax mismatch | `15%` vs `10%` |
| HM-10 | Missing tax on invoice | Invoice tax `None`, PO tax `15%` |
| HM-11 | Missing tax on PO | Invoice tax `15%`, PO tax `None` |
| HM-12 | Trimmed whitespace vendor | `" Acme Corp "` matches `"Acme Corp"` |
| HM-13 | Both totals zero | Edge case: `0.00` vs `0.00` |

---

#### 4.3.5 `test_line_match_service.py` -- Invoice/PO Line Matching (v1 backward compat, 21 tests, DB-backed)

Tests the line-level matching algorithm backward compatibility. These tests were written against the original scoring system (v1) and verify that the v2 multi-signal scorer still produces correct `LineMatchPair`/`LineMatchResult` output for the core scenarios.

| ID | Test | Scenario |
|----|------|----------|
| LM-01 | Single line exact match | Perfect description + qty + price + amount |
| LM-02 | Multi-line all matched | 3 invoice lines paired to 3 PO lines |
| LM-03 | Line number bonus | Same `line_number` contributes to score |
| LM-04 | Fuzzy description match | Partial description similarity contributes to scoring |
| LM-05 | Poor description no match | Completely different items = no pairing |
| LM-06 | Unmatched invoice line | Extra invoice line with no PO counterpart |
| LM-07 | Unmatched PO line | Extra PO line with no invoice counterpart |
| LM-08 | Tolerance breach | Qty/price/amount exceeding 2%/1%/1% thresholds |
| LM-09 | PO line deduplication | Same PO line not matched to two invoice lines |
| LM-10 | Tax difference | Tax mismatch flagged separately from amount |
| LM-11 | Minimum score threshold | Low-scoring pairs rejected as non-match |

**Note**: Some v1 test assertions were adjusted for v2 scorer behavior. The v2 scorer uses 11 weighted signals (total weight 1.00) with a minimum match threshold of 0.50 (up from 0.30), so some edge-case scenarios that previously matched may now classify as AMBIGUOUS or UNRESOLVED.

---

#### 4.3.5a `test_line_match_v2.py` -- Line Match v2 Multi-Signal Scorer (36 tests, DB-backed)

Tests the deterministic multi-signal line matching scorer introduced in v2. Covers the full scoring pipeline: 11 weighted signals, 4 penalty types, ambiguity detection, confidence bands, LLM fallback hook, and backward compatibility.

| ID | Test | Scenario |
|----|------|----------|
| V2-01 | Exact match (all signals) | Perfect description + qty + price + amount -> MATCHED |
| V2-02 | Token overlap | Partial token overlap contributes deterministic score |
| V2-03 | Item code ranking | Item code exact match boosts score (when present) |
| V2-04 | Numeric mismatch | 50% qty deviation triggers qty contradiction penalty |
| V2-05 | Ambiguity detection | Two near-identical candidates -> AMBIGUOUS |
| V2-06 | Unresolved | Completely different descriptions -> UNRESOLVED |
| V2-07 | Service/stock penalty | Service vs stock contradiction applies -0.10 penalty |
| V2-08 | UOM equivalence | "kg" matches "kilograms" via equivalence map |
| V2-09 | Moderate match | Score in MODERATE band (0.62-0.75) -> MATCHED with sufficient gap |
| V2-10 | LLM fallback not configured | AMBIGUOUS status when no fallback service provided |
| V2-11 | LLM fallback configured | LLM resolves ambiguous case -> MATCHED via LLM_FALLBACK |
| V2-12 | LLM fallback error | Fallback exception handled gracefully, stays AMBIGUOUS |
| V2-13 | Backward compat | `LineMatchPair` fields populated, `FieldComparison` objects present |
| V2-14 | Decision metadata | `to_result_line_metadata()` serialization |
| V2-15 | Deduplication | Used PO lines excluded from subsequent scoring |
| V2-16 | Confidence bands | Thresholds: HIGH>=0.85, GOOD>=0.75, MODERATE>=0.62, LOW>=0.50, NONE<0.50 |
| V2-17 | Edge cases | Empty descriptions, zero quantities, None values |
| V2-18 | Penalty stacking | Multiple penalties accumulate correctly |

**Scoring rules validated**: 11 signals (item_code 0.30, desc_exact 0.20, token_sim 0.15, fuzzy 0.10, qty 0.10, price 0.07, amount 0.03, uom 0.02, category 0.01, service_stock 0.01, line_number 0.01), penalties (-0.10/-0.08/-0.08/-0.05), match threshold 0.50, strong match 0.75+gap>=0.10, moderate match 0.62+gap>=0.08, ambiguity gap <0.08.

---

#### 4.3.5b `test_line_match_helpers.py` -- Line Match Helper Functions (52 tests, pure unit)

Tests the reusable text normalization, similarity, and numeric proximity helper functions used by the v2 scorer.

| Group | Tests | Coverage |
|-------|-------|----------|
| `normalize_line_text` | 6 | Lowercase, punctuation strip, whitespace collapse, None handling |
| `extract_meaningful_tokens` | 6 | Tokenization, stopword removal, short word filtering |
| `token_similarity` | 6 | Jaccard coefficient, empty sets, partial/full overlap |
| `fuzzy_similarity` | 5 | RapidFuzz token_sort_ratio, identical/different/empty strings |
| `quantity_proximity` | 7 | Tiered scoring (exact/2%/5%/10%/beyond), zero/None handling |
| `price_proximity` | 6 | Tiered scoring (1%/3%/5%/beyond), zero/None handling |
| `amount_proximity` | 5 | Tiered scoring (1%/3%/5%/beyond) |
| `uom_compatibility` | 7 | Equivalence map, exact match, one-side missing, unknown UOMs |
| `category_compatibility` | 4 | Exact match, both missing, mismatch, one-side missing |
| `service_stock_compatibility` | 6 | Both service, both stock, contradiction, unknown flags |

**Key constraint tested**: `InvoiceLineItem` does NOT have `item_code` or `unit_of_measure` fields (only PO lines do). Max achievable score without item_code is ~0.70.

---

#### 4.3.6 `test_grn_match_service.py` -- GRN Receipt Matching (13 tests, pure unit)

Tests the goods receipt verification logic (only active in 3-way mode).

| ID | Test | Scenario |
|----|------|----------|
| GM-01 | GRN unavailable | No GRN data = `grn_available=False` |
| GM-02 | Exact receipt | Received qty matches PO qty |
| GM-03 | Over-receipt | Received > PO (flagged as exception) |
| GM-04 | Under-receipt | Received < PO (flagged as exception) |
| GM-05 | Invoice exceeds received | Invoice qty > received (blocking exception) |
| GM-06 | Delayed receipt | `receipt_date - po_date > 30 days` (30-day threshold) |
| GM-07 | Multi-GRN aggregation | Multiple GRN line items summed |
| GM-08 | Partial receipt | Some lines received, some not |
| GM-09 | Zero received | `received_qty = 0` |
| GM-10 | Within tolerance receipt | Small delta within acceptable range |

---

#### 4.3.7 `test_grn_lookup_service.py` -- GRN Data Lookup (11 tests, DB-backed)

Tests database lookup and aggregation of GRN records.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestNoGRNs` | 2 | No GRNs in DB, wrong PO reference |
| `TestSingleGRN` | 4 | Single GRN retrieval, qty field extraction, date handling, line item count |
| `TestMultipleGRNs` | 5 | Multi-GRN quantity summation, latest_receipt_date selection, cross-PO exclusion, status filtering, combined line items |

---

#### 4.3.8 `test_exception_builder.py` -- Reconciliation Exception Generation (19 tests, DB-backed)

Tests the exception builder that generates structured exception records from match results.

| ID | Test | Scenario |
|----|------|----------|
| EB-01 | PO not found | Missing PO -> `PO_NOT_FOUND` exception |
| EB-02 | Vendor mismatch | Different vendors -> `VENDOR_MISMATCH` |
| EB-03 | Currency mismatch | Different currencies -> `CURRENCY_MISMATCH` |
| EB-04 | Header amount mismatch | Total difference > threshold -> `AMOUNT_MISMATCH` |
| EB-05 | GRN not found | 3-way mode, no GRN -> `GRN_NOT_FOUND` |
| EB-06 | Over-receipt | Received > ordered -> `OVER_RECEIPT` |
| EB-07 | Invoice exceeds received | Invoice > received -> `INVOICE_EXCEEDS_RECEIVED` |
| EB-08 | Delayed receipt | 30-day tiers: MEDIUM severity (31-60 days), HIGH severity (>60 days) |
| EB-09 | Clean match | Perfect 3-way and 2-way matches generate zero exceptions |
| EB-10 | GRN exceptions skipped in 2-way | GRN-related exceptions suppressed when `reconciliation_mode=TWO_WAY` |

---

#### 4.3.9 `test_classification_service.py` -- Match Status Classification (13 tests, pure unit)

Tests the deterministic classification gate sequence that assigns a final `MatchStatus`.

Gate sequence (in priority order):
1. PO not found -> `UNMATCHED`
2. Duplicate invoice -> `UNMATCHED`
3. Low extraction confidence -> `REQUIRES_REVIEW`
4. All headers + all lines matched + within tolerance -> `MATCHED`
5. Headers OK but some lines mismatched -> `PARTIAL_MATCH`
6. GRN gates (3-way only): not found / over-receipt / under-receipt -> severity-based
7. Unmatched invoice lines remain -> `REQUIRES_REVIEW`
8. Default fallback -> `REQUIRES_REVIEW`

---

#### 4.3.10 `test_runner_langfuse.py` -- Runner + Langfuse Integration (15 tests, DB-backed)

Tests that the `ReconciliationRunnerService` works correctly with Langfuse both enabled and disabled, and that observability instrumentation does not affect reconciliation outcomes.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestRunnerLangfuseDisabled` | 4 | Runner completes with `get_client() = None`, correct match counts, `lf_trace=None` explicit pass, empty invoice list |
| `TestRunnerScoreTrace` | 3 | `score_trace_safe` emits correct `RECON_RECONCILIATION_MATCH` value per status (`MATCHED=1.0`, `PARTIAL=0.5`, `REVIEW=0.3`, `UNMATCHED=0.0`); exception in `score_trace` does not break runner; SDK error is swallowed |
| `TestLangfuseSpansDoNotAlterResults` | 2 | Active Langfuse spans do not change mode resolution result; each invoice gets its own score call (filtered by `RECON_RECONCILIATION_MATCH` constant) |
| `TestGuardrailsLangfuseScoring` | 3 | Guardrail score emission: `score_trace` failure is silent; granted=`1.0`, denied=`0.0` |

**Mocking depth**: Patches 8 sub-services (`POLookupService`, `ReconciliationModeResolver`, `ReconciliationExecutionRouter`, `ClassificationService`, `ExceptionBuilderService`, `ReconciliationResultService`, `AuditService`, `ReviewWorkflowService`) via `unittest.mock.patch`.

---

### 4.4 Agents (`apps/agents/`)

**7 test files, ~80 tests. Mix of pure unit and DB-backed.**

#### 4.4.1 `test_policy_engine.py` -- Agent Planning Rules (19 tests, mostly DB-backed)

Tests the deterministic `PolicyEngine` that maps exception types to agent plans.

| Test Class | Tests | Rule |
|------------|-------|------|
| `TestRule1MatchedSkip` | 2 | `MATCHED` result -> `skip_agents=True` |
| `TestRule1bAutoClose` | 3 | `PARTIAL_MATCH` within auto-close bands (qty 5%, price 3%, amount 3%) -> auto-close eligible |
| `TestRule2PORetrieval` | 2 | `PO_NOT_FOUND` exception -> `PO_RETRIEVAL` agent planned |
| `TestRule3GRNRetrieval` | 2 | `GRN_NOT_FOUND` exception -> `GRN_RETRIEVAL` agent planned |
| `TestRule4InvoiceUnderstanding` | 2 | `VENDOR_MISMATCH` / header issues -> `INVOICE_UNDERSTANDING` agent |
| `TestRule5ReconciliationAssist` | 1 | Multiple exception types -> `RECONCILIATION_ASSIST` agent |
| `TestFallbackRequiresReview` | 2 | No matching rule -> `REQUIRES_REVIEW` fallback recommendation |
| `TestPostRunChecks` | 5 | `should_auto_close()` and `should_escalate()` post-run decision methods (pure unit) |

---

#### 4.4.2 `test_guardrails_service.py` -- RBAC Enforcement for Agents (27 tests, DB-backed)

Tests the `AgentGuardrailsService` that enforces RBAC at every agent operation boundary.

| Test Class | Tests | Authorization Layer |
|------------|-------|---------------------|
| `TestAuthorizeOrchestration` | 3 | `agents.orchestrate` permission |
| `TestAuthorizeAgent` | 4 | Per-agent permissions (`agents.run_exception_analysis`, etc.) via parametrize over `AGENT_PERMISSIONS` dict |
| `TestAuthorizeTool` | 4 | Per-tool permissions (`purchase_orders.view`, etc.) via parametrize over `TOOL_PERMISSIONS` dict |
| `TestAuthorizeRecommendation` | 4 | Recommendation accept/reject permissions |
| `TestAuthorizeAction` | 4 | Post-policy action permissions (auto-close, escalation) |
| `TestEnsurePermission` | 3 | Low-level `ensure_permission()` helper |
| `TestResolveActor` | 3 | Request -> User resolution (anonymous, authenticated, missing) |
| `TestGetSystemAgentUser` | 2 | `SYSTEM_AGENT` service account creation and idempotency |
| `TestBuildRBACSnapshot` | 4 | JSON snapshot generation for audit trail |

---

#### 4.4.3 `test_deterministic_resolver.py` -- Rule-Based Recommendation (20 tests, pure unit)

Tests the `DeterministicResolver` that maps exception patterns to agent recommendations without LLM calls.

| Test Class | Tests | Rule |
|------------|-------|------|
| `TestPriorAutoClose` | 4 | PARTIAL_MATCH within auto-close bands -> `AUTO_CLOSE` priority recommendation |
| `TestExtractionLowConfidence` | 2 | Low confidence -> `EXTRACTION` recommendation |
| `TestVendorMismatch` | 2 | `VENDOR_MISMATCH` exceptions -> vendor resolution |
| `TestGRNReceiptIssues` | 4 | GRN-related exceptions -> receipt issue recommendations |
| `TestEscalation` | 4 | High-severity multi-exception -> `ESCALATION` recommendation |
| `TestDefaultFallback` | 2 | No matching rule -> `SEND_TO_AP_REVIEW` |
| `TestOutputShape` | 2 | Output dict has correct structure (`recommendation_type`, `confidence`, `reasoning`) |

---

#### 4.4.4 `test_agent_memory.py` -- Agent Working Memory (21 tests, pure unit)

Tests the `AgentMemory` data structure that agents use to accumulate reasoning during ReAct loops.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestInitialState` | 1 | Empty defaults (no reasoning, no recommendation, empty facts) |
| `TestReasoningSummary` | 5 | Append reasoning, 500-character truncation, unicode handling |
| `TestRecommendationPromotion` | 5 | Confidence-based promotion (new recommendation accepted only when confidence strictly exceeds current), tie-break behaviour |
| `TestResolvedPONumber` | 7 | `found_po` extraction from tool output JSON (exact key, nested key, `None` output, empty output, multiple tools) |
| `TestFacts` | 3 | Facts dict get/set/overwrite |

---

#### 4.4.5 `test_orchestrator.py` -- Agent Orchestrator Pipeline (5 tests, DB-backed)

Tests the `AgentOrchestrator.execute()` pipeline including RBAC checks, duplicate-run guards, and plan execution.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestOrchestratorExecute` | 5 | MATCHED result skipped, RBAC denied returns skip, auto-close from policy engine, duplicate run guard prevents re-entry, empty agent plan completes with no agents |

**Mocking depth**: Patches `AgentGuardrailsService` (class-level methods), `ReasoningPlanner`, `PolicyEngine`, and individual agent classes. Uses real `TraceContext.new_root()` for trace propagation.

---

#### 4.4.6 `test_base_agent.py` -- BaseAgent ReAct Loop (11 tests, mixed)

Tests the core `BaseAgent.run()` ReAct loop, composite confidence calculation, and text sanitisation.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestBaseAgentRun` | 3 | Single-round no tools (BA-01), tool-call round with mock tool registry (BA-02), LLM exception marks run FAILED (BA-03) |
| `TestCompositeConfidence` | 5 | Perfect scores (CC-01), no tools -> tool_score=1.0 (CC-02), all tools failed -> lower composite (CC-03), no evidence penalty (CC-04), clamped to [0,1] (CC-05) |
| `TestSanitiseText` | 3 | ASCII passthrough (ST-01), Unicode arrows replaced (ST-02), fancy quotes stripped (ST-03) |

**Composite confidence formula validated**: `composite = llm*0.6 + tool*0.25 + evidence*0.15`, where `tool_score = (total - failed) / total` (1.0 if no tools), `evidence_score = 0.5` if empty/only `_provenance`, else `1.0`.

---

#### 4.4.7 `test_reasoning_planner.py` -- LLM-Enhanced Agent Planner (17 tests, mixed)

Tests the `ReasoningPlanner` class that uses LLM reasoning to produce agent execution plans, with deterministic fallback when the LLM is unavailable or the reasoning engine is disabled.

| Coverage Area | Tests | Scenarios |
|---|---|---|
| LLM plan generation | ~6 | Valid LLM plan returned, plan includes correct agent sequence, plan metadata captured |
| Fallback behaviour | ~5 | LLM unavailable falls back to `PolicyEngine`, reasoning engine disabled flag bypasses LLM, LLM returns invalid plan falls back |
| Plan validation | ~3 | Plan schema validation, unknown agent types rejected, empty plan handled |
| Orchestrator flag wiring | ~3 | `AGENT_REASONING_ENGINE_ENABLED` setting respected, planner selection logged, eval adapter captures plan source comparison |

**Key setting tested**: `AGENT_REASONING_ENGINE_ENABLED` in `config/settings.py` — when `False`, `PolicyEngine` is used directly without any LLM call.

---

### 4.5 Extraction (`apps/extraction/`)

**14 test files, ~234 tests. Mostly pure unit, 3 DB-backed files.**

The extraction module has the largest test suite, covering the full document intelligence pipeline from OCR output through structured data extraction, validation, and credit accounting.

#### 4.5.1 `test_response_repair_service.py` -- LLM Response Repair (25 tests, pure)

Tests the post-processing repair rules applied to raw LLM extraction output before persistence.

| Test Class | Tests | Repair Rule |
|------------|-------|-------------|
| `TestInvoiceNumberExclusion` | 6 | Strips non-invoice references (PO numbers, GRN references) that the LLM incorrectly placed in `invoice_number` |
| `TestTaxPercentageRecomputation` | 5 | Recomputes tax percentage from `subtotal`/`tax_amount` when LLM output is implausible |
| `TestSubtotalReconciliation` | 4 | Verifies/repairs `subtotal = total_amount - tax_amount` |
| `TestLineTaxAllocation` | 3 | Allocates header tax proportionally across line items |
| `TestTravelLineConsolidation` | 3 | Merges multi-row travel expense lines (hotel + airfare) into consolidated format |
| `TestRepairServiceSafety` | 4 | Backward compatibility with pre-repair payloads, fail-silent on broken input |

---

#### 4.5.2 `test_recovery_lane.py` -- Extraction Recovery Pipeline (24 tests, pure)

Tests the recovery lane that re-extracts data when initial extraction quality is below threshold.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestEvaluatePolicy` | 14 | Recovery trigger evaluation: confidence < threshold, specific field failures (vendor_name missing, total_amount implausible), multiple triggers, already-recovered (no re-trigger), configurable thresholds |
| `TestInvokeNotTriggered` | 1 | No invocation when policy says not to trigger |
| `TestInvokeWithAgent` | 7 | Agent-backed recovery: success, failure (fail-silent), partial recovery, agent timeout, merged results, priority ordering, original preserved when agent fails |
| `TestRecoveryResultSerializable` | 2 | Result can be JSON-serialised for persistence and logging |

---

#### 4.5.3 `test_reconciliation_validator.py` -- Pre-Reconciliation Validation (15 tests, pure)

Tests the validation service that checks extracted data quality before reconciliation.

| Test | Scenario |
|------|----------|
| Clean invoice with all fields | No validation issues |
| Total amount mismatch | `subtotal + tax != total` (critical failure) |
| Within tolerance | Small rounding difference accepted |
| Line item sum mismatch | Sum of line amounts != subtotal |
| Line math error | `quantity * unit_price != line_amount` |
| Tax breakdown mismatch | Tax lines don't sum to header tax |
| Missing critical fields | `vendor_name`, `invoice_number`, `total_amount`, `invoice_date` each tested |
| Fail-silent on None | `None` input -> empty result |
| Serialisation | Result converts to JSON for persistence |

---

#### 4.5.4 `test_qr_decoder_service.py` -- E-Invoice QR Code Processing (37 tests, pure)

Tests the Indian e-invoice (GST) QR code decoding and data extraction service.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestQRInvoiceData` | 8 | Data model: required fields, optional fields, seller/buyer GSTIN, IRN, date formatting |
| `TestParseEInvoiceJson` | 13 | JSON payload parsing: complete payload, missing optional fields, nested `ValDtls`/`DocDtls`/`SellerDtls`/`BuyerDtls` structures, invalid JSON |
| `TestDecodeFromTexts` | 10 | Three decode strategies tested: direct JSON, base64-encoded, URL-encoded; priority ordering; partial decode |
| `TestDecodeFromOcrText` | 6 | Extraction of QR payload from raw OCR text, regex patterns, multi-QR handling, no QR present |

---

#### 4.5.5 `test_prompt_source.py` -- Prompt Source Tracking (12 tests, pure)

Tests the prompt source metadata tracking that records which prompt template was used for each extraction.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestInitMessagesSourceRecording` | 5 | `composed` vs `monolithic_fallback` source type tracking, Langfuse vs DB source, prompt hash recording |
| `TestPromptMetaPersistence` | 5 | Metadata flows through to extraction result, `prompt_version` stamp, source preserved across re-extraction |
| `TestDecisionCodesFromPromptSource` | 2 | Prompt source type -> decision code mapping (`PROMPT_FALLBACK` when monolithic used) |

---

#### 4.5.6 `test_normalization_service.py` -- Data Normalization (34 tests, pure)

Tests the normalisation layer that cleans LLM-extracted values into canonical formats.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestVendorNormalization` | 3 | Whitespace, case, suffix removal ("Inc.", "LLC") |
| `TestInvoiceNumberNormalization` | 3 | Prefix stripping, leading zeros |
| `TestPONormalization` | 3 | `PO-`, `P.O.` prefix handling |
| `TestCurrencyNormalization` | 5 | ISO codes, symbols (`$`->`USD`, `SAR`), `None`, empty |
| `TestAmountNormalization` | 6 | Comma formatting, currency prefix removal, negative, `None`, empty, string |
| `TestDateNormalization` | 3 | Multiple formats -> ISO, `None`, invalid |
| `TestLineItemNormalization` | 9 | Per-field normalisation within line items, missing fields, empty list, partial lines |
| `TestTaxBreakdownNormalization` | 2 | Tax breakdown structure normalisation, empty breakdown |

---

#### 4.5.7 `test_invoice_prompt_composer.py` -- Modular Prompt Assembly (13 tests, pure)

Tests the prompt composition system that builds extraction prompts from modular overlays.

| Test | Scenario |
|------|----------|
| Base prompt + travel category overlay | Travel-specific fields added |
| Base prompt + goods category overlay | Goods-specific fields (HSN, qty) added |
| Base prompt + service category overlay | Service-specific fields added |
| Country overlay (IN / GST) | Indian GST fields injected |
| Prompt hash determinism | Same inputs -> same hash |
| Different categories -> different hashes | Category switch changes hash |
| Components tracking | Metadata records which overlays were applied |
| Fallback on missing base | Missing base prompt -> fallback to monolithic |

---

#### 4.5.8 `test_invoice_category_classifier.py` -- Document Category Classification (13 tests, pure)

Tests the rule-based classifier that determines invoice category (travel, goods, service) from OCR text and extracted data.

| Test | Scenario |
|------|----------|
| Travel classification | Hotel keywords, airfare terms, CART references |
| Goods classification | HSN codes, quantity + rate presence |
| Service classification | Consulting, subscription keywords |
| Ambiguity handling | Mixed signals -> ambiguity flag |
| Confidence range | Classification confidence bounded to [0.0, 1.0] |
| Signal cap | Maximum number of contributing signals |

---

#### 4.5.9 `test_field_confidence_service.py` -- Per-Field Confidence Scoring (22 tests, pure)

Tests the field-level confidence scoring that assigns `0.0-1.0` scores to each extracted field.

| Test Group | Scenarios |
|------------|-----------|
| `invoice_number` scoring | Clean=1.0, missing=0.0, recovered=0.65, excluded_reference=0.78, stripped-by-normalization<0.3 |
| `tax_percentage` scoring | Recomputed=0.55, clean=1.0 |
| Critical field missing | `vendor_name=0.0`, `total_amount=0.0`, `invoice_date=0.0` |
| `low_confidence_fields` | Populated with fields below threshold |
| `weakest_critical_field` | Correctly identifies minimum-confidence critical field |
| Line-level scoring | Clean line=1.0, large discrepancy<0.5, missing qty=0.7 |
| Fail-silent | `None` input -> empty result |
| Serialisation | `to_serializable()` output structure |

---

#### 4.5.10 `test_evidence_confidence.py` -- Evidence-Based Confidence Adjustment (39 tests, pure)

Tests the evidence-based confidence boosting/capping system that uses OCR text, extraction method, QR data, and evidence snippets to adjust per-field confidence.

| Test Class | Tests | Evidence Type |
|------------|-------|---------------|
| `TestBackwardCompatibility` | 3 | Handles pre-evidence payloads gracefully |
| `TestOCRSubstringConfirmation` | 6 | OCR text confirms extracted value -> boost (cap <= 0.95), short values skipped, no-match no-boost |
| `TestExtractionMethodSignal` | 7 | Method-based caps: `explicit=none`, `repaired<=0.78`, `recovered<=0.65`, `derived<=0.55`, `unknown=none` |
| `TestEvidenceSnippets` | 5 | Snippet text confirms value -> boost (cap <= 0.90), low-confidence nudge, short snippets skipped |
| `TestCombinedEvidenceAndOCR` | 3 | Combined `repaired + OCR` cap interaction, fail-silent on bad context, non-string OCR text |
| `TestQRVerifiedGroundTruth` | 15 | QR-confirmed `=0.99`, separator stripping, mismatch `<=0.40`, vendor_tax_id/total_amount/date format-aware comparisons, empty extracted value handling |

---

#### 4.5.11 `test_decision_codes.py` -- Decision Code Derivation (46 tests, pure)

Tests the system that derives routing decision codes from validation, reconciliation, field confidence, prompt source, and QR data.

| Test Class | Tests | Source |
|------------|-------|--------|
| `TestConstants` | 9 | Code format validation (UPPERCASE_SNAKE_CASE), routing map non-empty, hard-review codes subset |
| `TestDeriveFromValidation` | 5 | Validation failures -> decision codes |
| `TestDeriveFromRecon` | 6 | Reconciliation issues -> decision codes |
| `TestDeriveFromFieldConf` | 6 | Field confidence thresholds -> codes (vendor score < 0.5, line table incomplete) |
| `TestDeriveFromPromptSource` | 4 | Prompt source type -> fallback code |
| `TestDeduplication` | 2 | No duplicate codes, order preserved |
| `TestFailSilent` | 2 | Bad input -> empty list |
| `TestDeriveFromQRData` | 11 | QR IRN present, verified, mismatch codes, hard review routing, routing map entries |

---

#### 4.5.12 `test_duplicate_detection_service.py` -- Duplicate Invoice Detection (11 tests, DB-backed)

Tests the duplicate detection service that prevents re-processing of identical invoices.

| Test Class | Tests | Check |
|------------|-------|-------|
| `TestSameInvoiceNumberAndVendor` | 3 | Same `invoice_number` + `vendor` -> duplicate |
| `TestSameInvoiceNumberAndAmount` | 2 | Same `invoice_number` + `total_amount` -> duplicate |
| `TestEmptyInvoiceNumber` | 2 | Empty/None number skips all checks |
| `TestExcludeSelf` | 2 | `exclude_invoice_id` parameter prevents self-match |
| `TestAlreadyFlaggedDuplicatesExcluded` | 1 | `is_duplicate=True` records excluded from comparison |
| `TestNoExistingInvoices` | 1 | Empty DB -> no duplicate |

---

#### 4.5.13 `test_credit_service.py` -- Credit Accounting (40 tests, DB-backed)

Tests the credit/usage accounting system that tracks extraction usage per user.

| Test Class | Tests | Operation |
|------------|-------|-----------|
| `TestGetOrCreateAccount` | 2 | Auto-creation, idempotent |
| `TestCheckCanReserve` | 7 | Balance check, inactive block, monthly limit, reserved counted, unlimited |
| `TestReserve` | 5 | Success, transaction creation, insufficient/inactive block, multiple reserves |
| `TestConsume` | 5 | Consume success, negative transaction, fail without reservation, over-reserved fail, accepts account instance |
| `TestRefund` | 3 | Refund success, transaction creation, fail without reservation |
| `TestAllocate` | 4 | Positive allocation, reject zero/negative, transaction creation |
| `TestAdjust` | 4 | Positive/negative adjust, block negative balance, block below reserved |
| `TestMonthlyReset` | 3 | Reset clears `monthly_used`, no reset within month, transaction creation |
| `TestGetUsageSummary` | 4 | Response structure, values, unlimited vs limited monthly display |
| `TestFullLifecycle` | 3 | Reserve -> consume, reserve -> refund, ledger integrity |

---

#### 4.5.14 `test_credit_views.py` -- Credit Admin Views (13 tests, DB-backed)

Tests the admin credit management UI views.

| Test Class | Tests | View |
|------------|-------|------|
| `TestCreditAccountListView` | 3 | Anonymous redirect, admin access, search filter |
| `TestCreditAccountDetailView` | 2 | Detail render, balance display |
| `TestCreditAccountAdjustView` | 7 | Add/subtract credits, block negative, set monthly limit, toggle active, GET redirect, invalid form |
| `TestWorkbenchCreditContext` | 1 | Workbench template includes `credit_summary` in context |

---

### 4.6 Cases (`apps/cases/`)

**1 test file, ~28 tests. All pure unit.**

#### 4.6.1 `test_case_state_machine.py` -- Case State Transitions (28 tests, pure)

Tests the deterministic state machine that governs case lifecycle.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestCanTransition` | 13 | Valid transitions (NEW->OPEN, OPEN->IN_PROGRESS, etc.), invalid transitions rejected, self-transition blocked |
| `TestGetAllowedTransitions` | 6 | Per-state allowed target list |
| `TestIsTerminal` | 6 | CLOSED and REJECTED are terminal; NEW, OPEN, IN_PROGRESS, FAILED are not |
| `TestTransition` | 3 | Trigger type enforcement (`manual` vs `automatic`), recovery path (FAILED -> NEW) |

---

### 4.7 Reviews (`apps/cases/` -- merged from `apps/reviews`)

**1 test file, ~12 tests. All DB-backed.**

#### 4.7.1 `test_review_workflow_service.py` -- Review Lifecycle (12 tests, DB-backed)

> **Note:** Tests moved from `apps/reviews/tests/` to `apps/cases/tests/` during the reviews-to-cases merge.

Tests the complete review assignment lifecycle from creation through approval/rejection.

| Test Class | Tests | Status Transition |
|------------|-------|-------------------|
| `TestCreateAssignment` | 5 | Creates PENDING (no user) or ASSIGNED (with user), sets `requires_review=True`, stores priority, logs audit event |
| `TestAssignReviewer` | 2 | PENDING -> ASSIGNED, reviewer persisted |
| `TestStartReview` | 1 | ASSIGNED -> IN_REVIEW |
| `TestAddComment` | 2 | Comment creation, link to assignment |
| `TestApprove` | 1 | IN_REVIEW -> APPROVED, creates `ReviewDecision` |
| `TestReject` | 1 | IN_REVIEW -> REJECTED, creates `ReviewDecision` with reason |

---

### 4.8 Posting (`apps/posting/`)

**2 test files, ~19 tests. All DB-backed.**

Tests the posting business layer: eligibility checking and posting action lifecycle (approve/reject/submit/retry).

#### 4.8.1 `test_eligibility_service.py` -- Posting Eligibility Checks (10 tests, DB-backed)

Tests the `PostingEligibilityService.check()` pre-condition validation that gates whether an invoice can enter the posting pipeline.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestEligibilityChecks` | 10 | Invoice not found, eligible reconciled invoice (with approved extraction), wrong status (not RECONCILED), duplicate invoice, missing invoice_number, missing vendor info, no extraction approval, already posted, active running PostingRun blocks, multiple failures accumulated |

---

#### 4.8.2 `test_posting_action_service.py` -- Posting Action Lifecycle (9 tests, DB-backed)

Tests the `PostingActionService` approve/reject/submit/retry state transitions.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestApprovePosting` | 3 | Approve from MAPPING_REVIEW_REQUIRED -> READY_TO_SUBMIT (AP-01), cannot approve POSTED (AP-02), corrections applied during approval with proper `fields` format (AP-03) |
| `TestRejectPosting` | 2 | Reject from MAPPING_REVIEW_REQUIRED -> REJECTED (RJ-01), cannot reject POSTED (RJ-02) |
| `TestSubmitPosting` | 2 | Submit from READY_TO_SUBMIT -> POSTED with mock doc number (SB-01), cannot submit from MAPPING_REVIEW_REQUIRED (SB-02) |
| `TestRetryPosting` | 2 | Retry from POST_FAILED re-triggers PostingOrchestrator (RT-01), cannot retry from POSTED (RT-02) |

---

### 4.9 Posting Core (`apps/posting_core/`)

**2 test files, ~33 tests. Mix of pure unit and DB-backed.**

Tests the posting platform layer: field validation rules and multi-dimensional confidence scoring.

#### 4.9.1 `test_posting_validation.py` -- Posting Field Validation (18 tests, pure unit)

Tests the `PostingValidationService` field-level and cross-field validation rules.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestFieldLevelRules` | 12 | Vendor code present/missing, valid/invalid GL account format, negative amount flagged, valid/missing tax code, missing PO reference, missing cost center, missing currency, valid date format, empty date no error, missing company code |
| `TestCrossFieldRules` | 3 | Tax amount exceeds total, clean data no issues, zero tax valid |
| `TestSafetyAndEdgeCases` | 3 | None input returns empty, missing fields treated as empty, pure-whitespace treated as empty |

---

#### 4.9.2 `test_posting_confidence.py` -- Posting Confidence Scoring (15 tests, pure unit)

Tests the `PostingConfidenceService` 5-dimensional weighted confidence scoring.

| Test Class | Tests | Scenario |
|------------|-------|----------|
| `TestCompositeScore` | 5 | Perfect score (all fields complete) >= 0.85, low vendor confidence pulls score down, many PostingIssues reduce score, zero line count edge case, touchless threshold boundary |
| `TestTouchlessDecision` | 4 | No mapping review needed + high scores = touchless, any review queue = not touchless, missing vendor mapping = not touchless, low confidence = not touchless |
| `TestDimensions` | 6 | Header completeness (partial fields), vendor mapping passthrough, line mapping average, tax completeness ratio, reference freshness default, reference freshness degraded |

**Confidence formula validated**: 5 dimensions weighted: header completeness 15%, vendor mapping 25%, line mapping 30%, tax completeness 15%, reference freshness 15%.

---

### 4.10 ERP Integration (`apps/erp_integration/`)

**3 test files, ~37 tests. Mix of pure unit and DB-backed.**

Tests the ERP connectivity layer: connector data classes, connector factory, resolution chain, and cache service.

#### 4.10.1 `test_base_connector.py` -- ERP Connector Data Classes (14 tests, pure unit)

Tests the `ERPResolutionResult`, `ERPSubmissionResult` data classes and `BaseERPConnector` default implementations.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestERPResolutionResult` | 6 | Default unresolved (RR-01), resolved with value (RR-02), to_provenance_dict keys (RR-03), fallback_used flag (RR-04), warnings accumulation (RR-05), metadata dict (RR-06) |
| `TestERPSubmissionResult` | 3 | Success result (SR-01), failure result with error code (SR-02), safe defaults (SR-03) |
| `TestBaseConnectorDefaults` | 5 | All capabilities false by default (BC-01), default lookup returns unresolved (BC-02), default create_invoice returns failed (BC-03), connector_name accessible (BC-04), config dict stored on instance (BC-05) |

---

#### 4.10.2 `test_connector_factory.py` -- Connector Factory (10 tests, DB-backed)

Tests the `ConnectorFactory` that instantiates ERP connectors from `ERPConnection` database records.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestCreateFromConfig` | 2 | Valid config creates correct connector type with config stored, invalid type raises ValueError |
| `TestGetDefaultConnector` | 3 | Returns None when no ERPConnection exists, returns connector for active default, ignores inactive connections |
| `TestGetConnectorByName` | 3 | Returns connector by name, returns None for non-existent name, ignores inactive connections |
| `TestConcurrentDefault` | 2 | Only one default allowed, latest default wins when multiple are created |

---

#### 4.10.3 `test_base_resolver.py` -- Resolution Chain (10 tests, DB-backed)

Tests the `BaseResolver` resolution chain: cache -> ERP API -> DB fallback.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestResolutionChain` | 7 | Cache hit returns cached result, cache miss falls through to API, API failure falls through to DB fallback, DB fallback failure returns not-resolved, creates ERPResolutionLog audit record, populated result carries source metadata, no connector skips API stage |
| `TestBuildLookupKey` | 3 | Key includes params, key without params, empty values excluded |

---

#### 4.10.4 `test_cache_service.py` -- ERP Cache Service (13 tests, DB-backed)

Tests the `ERPCacheService` TTL-based database cache for ERP resolution results.

| Test Class | Tests | Functionality |
|------------|-------|---------------|
| `TestCacheGet` | 4 | Returns None for missing key, returns cached value within TTL, returns None for expired entry, handles JSON decode errors |
| `TestCacheSet` | 4 | Creates new cache entry, updates existing entry, stores all fields correctly, handles missing optional fields |
| `TestCacheInvalidation` | 3 | Invalidate by key deletes entry, invalidate by resolution type, invalidate all entries |
| `TestCacheTTL` | 2 | Configurable TTL respected, default TTL (3600s) used when not configured |

---

### 4.11 Eval & Learning (`apps/core_eval/`)

**6 test files, ~120 tests. Mix of pure unit, integration, and DB-backed.**

Tests the evaluation pipeline and learning engine that track extraction/reconciliation quality signals, propose learning actions, and provide RBAC-gated views.

> Note: `apps/core/tests/test_evaluation_constants.py` (29 tests) is documented under §4.1.3.

#### 4.11.1 `test_learning_engine.py` -- Learning Engine Rules (22 tests, pure unit)

Tests the `LearningEngine` deterministic rule engine that aggregates `LearningSignal` records and proposes `LearningAction` items.

| Coverage Area | Tests | Scenarios |
|---|---|---|
| Signal aggregation | ~6 | Signals grouped by module + field, threshold detection, time-window filtering |
| Rule 1 — field normalisation | ~3 | Enough field-correction signals trigger `field_normalization_candidate` action |
| Rule 2 — prompt review | ~3 | Pattern of low-confidence signals triggers `prompt_review` action |
| Rule 3 — threshold tuning | ~3 | Accuracy-band signals trigger `threshold_tune` proposal |
| Safety controls | ~4 | Deduplication prevents re-proposing same action, cooldown period enforced |
| Management command | ~3 | `run_learning_engine` runs full cycle, reports proposed/skipped counts |

---

#### 4.11.2 `test_eval_adapter.py` -- Extraction Eval Adapter (10 tests, pure unit)

Tests `ExtractionEvalAdapter.sync_for_extraction_result()` — the bridge that converts extraction outcomes into `EvalRun`, `EvalMetric`, and `EvalFieldOutcome` records.

| Coverage Area | Tests | Scenarios |
|---|---|---|
| `sync_for_extraction_result` | 10 | EvalRun created with correct module/entity, metrics populated, field outcomes written, idempotency (re-sync is no-op), fail-silent on error |

---

#### 4.11.3 `test_approval_integration.py` -- Approval → Learning Signal (25 tests, integration)

Integration tests for the approval workflow creating `LearningSignal` records when invoices are approved, rejected, or auto-approved.

| Coverage Area | Tests | Scenarios |
|---|---|---|
| Approve path | ~6 | Approval creates signal with correct signal_type and field metadata |
| Reject path | ~5 | Rejection creates signal; rejection reason captured in signal payload |
| Auto-approve path | ~4 | Auto-close creates signal with auto-approve signal_type |
| Field corrections | ~6 | Reviewer field edits emit per-field correction signals with old/new values |
| Review overrides | ~4 | Reviewer overriding agent recommendation creates override signal |

---

#### 4.11.4 `test_end_to_end.py` -- Full Eval → Learning Cycle (13 tests, integration)

End-to-end integration tests covering the full path: `ExtractionEvalAdapter` creates signals → `LearningEngine` detects patterns → `LearningAction` records proposed.

| Coverage Area | Tests | Scenarios |
|---|---|---|
| Full cycle | ~8 | Actions proposed after enough signals, correct action types match signal patterns |
| Deduplication | ~3 | Engine run twice does not double-propose same action |
| Empty state | ~2 | Engine with no signals produces no actions |

---

#### 4.11.5 `test_views.py` -- Eval & Learning RBAC Views (29 tests, DB-backed)

Tests the five template views at `/eval/` with RBAC permission enforcement.

| Test Class | Tests | Scenarios |
|---|---|---|
| Anonymous redirect | ~5 | All 5 views redirect to login when unauthenticated |
| Permission denied | ~5 | User without `eval.view` gets 403 for all 5 views |
| Authorized access | ~5 | User with `eval.view` gets 200 for all 5 views |
| Filter parameters | ~9 | List views accept and apply filters (module, status, entity_type, signal_type, field_name) |
| 404 handling | ~5 | Detail views return 404 for non-existent PKs |

---

#### 4.11.6 `test_recon_eval_adapter.py` -- Reconciliation Eval Adapter (21 tests, DB-backed)

Tests `ReconciliationEvalAdapter` methods that bridge reconciliation outcomes into the eval/learning pipeline.

| Coverage Area | Tests | Scenarios |
|---|---|---|
| `sync_for_result` | ~8 | EvalRun and metrics created on reconciliation completion, correct module/entity, idempotency |
| `sync_for_review_outcome` | ~6 | Review decision (approve/reject/escalate) creates EvalMetric and LearningSignal |
| Idempotency | ~4 | Syncing same result twice does not duplicate records |
| Fail-safety | ~2 | Adapter exceptions do not propagate to caller (fail-silent) |
| Review assignment | ~1 | Reviewer user captured in eval metadata |

```bash
# Run all eval/learning tests (120 total)
python -m pytest apps/core_eval/ apps/extraction/tests/test_eval_adapter.py apps/extraction/tests/test_approval_integration.py apps/reconciliation/tests/test_recon_eval_adapter.py -v

# Just the engine
python -m pytest apps/core_eval/tests/test_learning_engine.py -v

# Just end-to-end
python -m pytest apps/core_eval/tests/test_end_to_end.py -v

# Just reconciliation eval adapter
python -m pytest apps/reconciliation/tests/test_recon_eval_adapter.py -v
```

---

## 5. Factory & Fixture Catalog

### 5.1 Model Factories

**`apps/accounts/tests/factories.py`** -- 6 factories

| Factory | Model | Key Defaults |
|---------|-------|-------------|
| `UserFactory` | `accounts.User` | `email=Sequence("user{n}@test.com")`, `is_active=True` |
| `RoleFactory` | `accounts.Role` | `code=Sequence("role_{n}")`, `rank=50` |
| `PermissionFactory` | `accounts.Permission` | `code=Sequence("perm_{n}")`, `module=Sequence`, `action=Sequence` |
| `RolePermissionFactory` | `accounts.RolePermission` | Role + Permission via SubFactory |
| `UserRoleFactory` | `accounts.UserRole` | User + Role via SubFactory |
| `UserPermissionOverrideFactory` | `accounts.UserPermissionOverride` | `override_type="ALLOW"` |

**`apps/reconciliation/tests/factories.py`** -- 7 factories

| Factory | Model | Key Defaults |
|---------|-------|-------------|
| `VendorFactory` | `vendors.Vendor` | `code=Sequence("V{n}")`, `normalized_name=Faker` |
| `ReconConfigFactory` | `reconciliation.ReconciliationConfig` | Tolerances: qty=2%, price=1%, amount=1%; auto-close: qty=5%, price=3%, amount=3% |
| `ReconPolicyFactory` | `reconciliation.ReconciliationPolicy` | `reconciliation_mode=THREE_WAY`, `priority=10` |
| `InvoiceFactory` | `documents.Invoice` | `invoice_number=Sequence("INV-{n}")`, `total_amount=1000`, `currency="SAR"` |
| `POFactory` | `documents.PurchaseOrder` | `po_number=Sequence("PO-{n}")`, `total_amount=1000`, `status="OPEN"` |
| `InvoiceLineItemFactory` | `documents.InvoiceLineItem` | `line_number=1`, `quantity=10`, `unit_price=100` |
| `POLineItemFactory` | `documents.PurchaseOrderLineItem` | `line_number=1`, `quantity=10`, `unit_price=100` |

### 5.2 App-Level Conftest Fixtures

**`apps/extraction/tests/conftest.py`**

| Fixture | Creates | Used By |
|---------|---------|---------|
| `user` | `User(email="testuser@example.com")` | Credit tests |
| `admin_user` | `User(is_staff=True, is_superuser=True, role="ADMIN")` | Credit view tests |
| `credit_account(user)` | `CreditService.get_or_create_account(user)` + 10 credits | Credit tests |
| `limited_account(user)` | Same + `monthly_limit=5` | Monthly limit tests |

**`apps/reconciliation/tests/conftest.py`**

| Fixture | Creates | Used By |
|---------|---------|---------|
| `default_tolerance_engine` | `ToleranceEngine(qty=2, price=1, amount=1)` | Tolerance tests |
| `wide_tolerance_engine` | `ToleranceEngine(qty=5, price=3, amount=3)` | Auto-close band tests |
| `recon_config` | `ReconConfigFactory()` | Runner, policy engine tests |
| `vendor` | `VendorFactory()` | PO/GRN lookup, exception builder tests |
| `invoice` / `purchase_order` | Via factories | All recon DB tests |
| `invoice_with_vendor` / `po_with_vendor` | Factory instances linked to same vendor | Header match tests |
| `invoice_line` / `po_line` | Via line item factories | Line match tests |

---

## 6. Mocking Strategy Reference

### 6.1 Core Patterns

| Pattern | When Used | Example |
|---------|-----------|---------|
| **`MagicMock` instances** | Lightweight object substitution for pure unit tests | `make_mock_invoice()` in runner tests |
| **`unittest.mock.patch`** | Replace module-level imports at call sites | `patch("apps.core.langfuse_client.score_trace")` |
| **`unittest.mock.patch.object`** | Replace methods on specific class instances | `patch.object(AgentGuardrailsService, "build_rbac_snapshot")` |
| **`monkeypatch` (pytest)** | Environment variable manipulation, singleton reset | Langfuse client singleton reset |
| **`@pytest.mark.parametrize`** | Combinatorial test generation | Permission matrices, score mappings |
| **Factory Boy** | Complex DB model graph construction | `InvoiceFactory`, `UserRoleFactory` |
| **Raw `Model.objects.create()`** | Simple one-off DB records | PO lookup, duplicate detection |

### 6.2 Commonly Patched Targets

| Target | Why |
|--------|-----|
| `apps.core.langfuse_client.get_client` | Prevent Langfuse SDK initialisation |
| `apps.core.langfuse_client.score_trace` | Verify score emission or inject failures |
| `apps.core.langfuse_client.start_trace` | Suppress trace creation |
| `apps.core.langfuse_client.start_span` | Suppress span creation |
| `apps.core.langfuse_client.end_span` | Suppress span closure |
| `apps.core.langfuse_client.score_observation` | Suppress observation scores |
| `apps.auditlog.services.AuditService.log_event` | Prevent audit event DB writes in non-audit tests |
| `apps.cases.services.review_workflow_service.ReviewWorkflowService.create_assignment` | Prevent review creation side effects |
| `apps.agents.services.agent_classes.InvoiceUnderstandingAgent` | Mock agent invocation in recovery lane tests |

---

## 7. Test Categorisation Matrix

### 7.1 By Test Type

| Type | Count | Percentage | Description |
|------|-------|-----------|-------------|
| Pure Unit | ~383 | ~59% | No database, no external services. MagicMock inputs. |
| DB Integration | ~268 | ~41% | SQLite in-memory. Real models and ORM queries. |
| View / HTTP | ~13 | ~2% | Django test client hitting actual views (credit views only). |

### 7.2 By Domain

| Domain | Files | Tests | Key Services Covered |
|--------|-------|-------|---------------------|
| Extraction Pipeline | 14 | ~234 | LLM response repair, recovery lane, field confidence, evidence scoring, QR decoding, prompt composition, normalisation, validation, duplicate detection, decision codes, credit accounting |
| Reconciliation Engine | 10 | ~99 | Tolerance comparison, PO lookup (3-strategy), mode resolution (3-tier), header match (13 checks), line match (weighted scoring), GRN match, GRN lookup, exception builder (10 types), classification (8-gate), runner Langfuse integration |
| Agent System | 6 | ~63 | Policy engine rules, deterministic resolver, RBAC guardrails (5 authorization layers), agent memory, **orchestrator pipeline, BaseAgent ReAct loop** |
| RBAC / Accounts | 2 | ~21 | Permission resolution chain, data-scope authorisation, role expiry, override precedence, cache behaviour |
| Observability | 5 | ~98 | Langfuse client fail-silent contract, evaluation constants integrity, cross-flow helpers (session ID, metadata, sanitisation, latency), **middleware tracing, Celery task integration** |
| Cases | 1 | ~28 | State machine transitions, terminal states, trigger types |
| Reviews | 1 | ~12 | Assignment lifecycle, reviewer assignment, approval/rejection decisions |
| **Posting Pipeline** | **4** | **~52** | **Eligibility checks, action lifecycle (approve/reject/submit/retry), field validation, 5-dimensional confidence scoring** |
| **ERP Integration** | **4** | **~47** | **Connector data classes, connector factory, resolution chain (cache->API->DB), cache TTL management** |

### 7.3 By Risk Level

| Risk | Tests | Rationale |
|------|-------|-----------|
| **Critical Path** | ~212 | Matching, classification, tolerance, **posting pipeline, ERP resolution** -- directly affects reconciliation and posting accuracy |
| **Data Integrity** | ~82 | Duplicate detection, credit accounting, review decisions, **posting action lifecycle** -- affects financial data correctness |
| **Security / RBAC** | ~48 | Permission resolution, data scoping, guardrails -- affects access control correctness |
| **Observability** | ~98 | Langfuse fail-silent, constants integrity, **middleware tracing, Celery task integration** -- ensures monitoring never breaks business logic |
| **Extraction Quality** | ~163 | Field confidence, evidence scoring, QR verification -- affects data quality entering the pipeline |
| **Agent Intelligence** | ~63 | Policy engine, deterministic resolver, **ReAct loop, orchestrator** -- affects agent decision quality |

---

## 8. Scenario Coverage Map

### 8.1 Reconciliation Pipeline End-to-End Scenarios

| Scenario | PO Lookup | Mode | Header Match | Line Match | GRN Match | Classification | Exceptions | Tests |
|----------|-----------|------|-------------|------------|-----------|----------------|------------|-------|
| Perfect 2-way match | Exact | TWO_WAY | All pass | All paired | N/A | MATCHED | 0 | TE-*, HM-01, LM-01, CS-01, EB-09 |
| Perfect 3-way match | Exact | THREE_WAY | All pass | All paired | Exact receipt | MATCHED | 0 | GM-02, EB-09 |
| PO not found | Not found | -- | N/A | N/A | N/A | UNMATCHED | PO_NOT_FOUND | EB-01 |
| Vendor mismatch | Found | TWO_WAY | Vendor fail | -- | N/A | PARTIAL | VENDOR_MISMATCH | HM-03, EB-02 |
| Amount over tolerance | Found | TWO_WAY | Amount fail | -- | N/A | PARTIAL | AMOUNT_MISMATCH | HM-07, EB-04 |
| Line qty mismatch | Found | TWO_WAY | Pass | Tolerance breach | N/A | PARTIAL | LINE_QTY_MISMATCH | LM-08 |
| GRN not found (3-way) | Found | THREE_WAY | Pass | Pass | Unavailable | REQUIRES_REVIEW | GRN_NOT_FOUND | GM-01, EB-05 |
| Over-receipt | Found | THREE_WAY | Pass | Pass | Over | PARTIAL | OVER_RECEIPT | GM-03, EB-06 |
| Invoice exceeds received | Found | THREE_WAY | Pass | Pass | Under | REQUIRES_REVIEW | INVOICE_EXCEEDS | GM-05, EB-07 |
| Delayed receipt | Found | THREE_WAY | Pass | Pass | Late | PARTIAL | DELAYED_RECEIPT | GM-06, EB-08 |
| Auto-close band | Found | -- | Minor diffs | Minor diffs | -- | PARTIAL (closeable) | Small diffs | Rule1b |
| GRN suppressed in 2-way | Found | TWO_WAY | Pass | Pass | N/A | MATCHED | 0 (GRN exc suppressed) | EB-10 |

### 8.2 Agent Decision Scenarios

| Scenario | Exception Pattern | Agent Planned | Recommendation | Tests |
|----------|-------------------|---------------|----------------|-------|
| Matched result | None | None (skipped) | N/A | Rule1 |
| Partial within bands | Small deviations | None | AUTO_CLOSE | Rule1b |
| PO missing | PO_NOT_FOUND | PO_RETRIEVAL | -- | Rule2 |
| GRN missing | GRN_NOT_FOUND | GRN_RETRIEVAL | -- | Rule3 |
| Vendor issues | VENDOR_MISMATCH | INVOICE_UNDERSTANDING | -- | Rule4 |
| Multiple exceptions | Mixed | RECONCILIATION_ASSIST | -- | Rule5 |
| No rule match | Unusual pattern | None | SEND_TO_AP_REVIEW | Fallback |
| Low confidence | Low extraction score | -- | EXTRACTION | DeterministicResolver |
| High severity multi | Severe exceptions | -- | ESCALATION | DeterministicResolver |

### 8.3 RBAC Permission Scenarios

| Scenario | Resolution Path | Result | Tests |
|----------|----------------|--------|-------|
| Admin superuser | Admin bypass | Granted | TestAdminBypass |
| DENY override exists | Override check | Denied (overrides role grant) | TestDenyOverride |
| ALLOW override exists | Override check | Granted (no role needed) | TestAllowOverride |
| Role has permission | Role-Permission matrix | Granted | TestRoleLevelPermission |
| Expired role | Date check | Denied (expired ignored) | TestRoleLevelPermission |
| No role, no override | Default deny | Denied | TestDefaultDeny |
| Scoped user, in scope | Data-scope check | Granted (filtered queryset) | TestAuthorizeDataScope |
| Scoped user, out of scope | Data-scope check | Denied | TestAuthorizeDataScope |
| SYSTEM_AGENT | System bypass | Granted (unrestricted) | TestGetActorScope |

### 8.4 Extraction Quality Scenarios

| Scenario | Stage | Tests |
|----------|-------|-------|
| Clean extraction, all fields present | Field confidence | 22 tests |
| LLM extracted wrong invoice number | Response repair | TestInvoiceNumberExclusion |
| Tax percentage implausible | Response repair | TestTaxPercentageRecomputation |
| OCR text confirms extracted value | Evidence confidence | TestOCRSubstringConfirmation |
| Extraction method = "repaired" | Evidence confidence | TestExtractionMethodSignal (cap <=0.78) |
| QR code verifies field | Evidence confidence | TestQRVerifiedGroundTruth (boost to 0.99) |
| QR data mismatches extracted | Evidence confidence | TestQRVerifiedGroundTruth (cap <=0.40) |
| Low confidence triggers recovery | Recovery lane | TestEvaluatePolicy (14 policies) |
| Agent recovery succeeds | Recovery lane | TestInvokeWithAgent (7 scenarios) |
| Duplicate invoice detected | Duplicate detection | 11 tests |
| Travel invoice categorised | Category classifier | 13 tests |
| Indian GST QR decoded | QR decoder | 37 tests |

---

## 9. Gaps & Recommendations

### 9.1 Apps Without Test Coverage

The following apps have **zero test files**:

| App | Priority | Reason |
|-----|----------|--------|
| ~~`posting` / `posting_core`~~ | ~~**High**~~ | **CLOSED** -- 4 test files added: eligibility, action service, validation, confidence scoring (52 tests) |
| ~~`erp_integration`~~ | ~~**High**~~ | **CLOSED** -- 4 test files added: base connector, connector factory, base resolver, cache service (47 tests) |
| `documents` | Medium | Invoice/PO/GRN models -- mostly CRUD, but model methods and managers untested |
| `procurement` | Medium | Quotation extraction agent, prefill service, attribute mapping -- LLM-dependent pipeline |
| `copilot` | Low | Chat-style Q&A service -- mostly LLM wrapper |
| `dashboard` | Low | Analytics queries -- read-only aggregation |
| `vendors` | Low | Vendor list/detail views with RBAC scoping -- tested indirectly via recon tests |

### 9.2 Functional Gaps in Tested Apps

| Gap | App | Status |
|-----|-----|--------|
| No API endpoint tests | All apps | Open -- DRF ViewSets, serializers, and URL routing are untested |
| No template view tests (except credit) | All except extraction | Open -- Template rendering, context data, permission-gated views untested |
| ~~No Celery task unit tests~~ | ~~extraction, reconciliation, agents~~ | **CLOSED** -- `test_celery_tasks.py` covers 3 core tasks (5 tests) |
| ~~No agent integration tests~~ | ~~agents~~ | **CLOSED** -- `test_base_agent.py` tests ReAct loop with mocked LLM + tool calls (11 tests) |
| ~~No orchestrator tests~~ | ~~agents~~ | **CLOSED** -- `test_orchestrator.py` tests full pipeline (5 tests) |
| ~~No middleware tests~~ | ~~core~~ | **CLOSED** -- `test_middleware.py` covers all 3 middleware classes (14 tests) |
| No serializer tests | All apps | Open -- DRF serializer validation, field mapping, nested serialization untested |
| No migration tests | All apps | Open -- No verification that migrations apply cleanly on MySQL |

### 9.3 Recommended Next Steps

1. ~~**Posting Pipeline Tests**~~ -- **DONE**: `PostingActionService` (approve/reject/submit/retry), `PostingValidationService`, `PostingConfidenceService`, `PostingEligibilityService`. Remaining: `PostingMappingEngine` (vendor/item/tax resolution strategies), `PostingPipeline` (9-stage sequence end-to-end).
2. ~~**ERP Integration Tests**~~ -- **DONE**: `ConnectorFactory`, `BaseResolver` resolution chain, `ERPCacheService` TTL behaviour, `BaseERPConnector` data classes. Remaining: DB fallback adapters, individual connector implementations.
3. **API Endpoint Tests** -- Add `APIClient`-based tests for the most critical endpoints: reconciliation start, posting actions, governance queries.
4. ~~**Celery Task Tests**~~ -- **DONE**: `process_invoice_upload_task`, `run_reconciliation_task`, `run_agent_pipeline_task` tested with mocked services (5 tests).
5. ~~**Agent ReAct Loop Test**~~ -- **DONE**: `BaseAgent.run()` tested with mocked LLM + tool registry (3 tests), composite confidence (5 tests), text sanitisation (3 tests).
6. **PostingMappingEngine Tests** -- Test vendor/item/tax/cost-center/PO resolution strategy chains (exact code -> alias -> name -> fuzzy). High priority remaining gap.
7. **DB Fallback Adapter Tests** -- Test individual ERP DB fallback adapters (vendor, item, tax, cost center, PO dual-tier, GRN).
8. **Serializer Tests** -- DRF serializer validation for critical models (Invoice, ReconciliationResult, PostingRun).
9. **Multi-Tenant Isolation Tests** -- Verify tenant scoping on ViewSets, template views, agent tools, and Celery tasks. Test that users in Tenant A cannot access Tenant B data. Test platform admin cross-tenant access. See [MULTI_TENANT.md](MULTI_TENANT.md).

---

## Appendix: Full Test Count by File

| # | File | Tests |
|---|------|-------|
| 1 | `apps/core/tests/test_utils.py` | ~76 |
| 2 | `apps/core/tests/test_langfuse_client.py` | 38 |
| 3 | `apps/core/tests/test_evaluation_constants.py` | 29 |
| 4 | `apps/core/tests/test_observability_helpers.py` | 50 |
| 5 | `apps/accounts/tests/test_rbac_has_permission.py` | 21 |
| 6 | `apps/accounts/tests/test_rbac_scope.py` | 19 |
| 7 | `apps/reconciliation/tests/test_tolerance_engine.py` | 18 |
| 8 | `apps/reconciliation/tests/test_po_lookup_service.py` | 13 |
| 9 | `apps/reconciliation/tests/test_mode_resolver.py` | 12 |
| 10 | `apps/reconciliation/tests/test_header_match_service.py` | 13 |
| 11 | `apps/reconciliation/tests/test_line_match_service.py` | 21 |
| 12 | `apps/reconciliation/tests/test_grn_match_service.py` | 13 |
| 13 | `apps/reconciliation/tests/test_grn_lookup_service.py` | 11 |
| 14 | `apps/reconciliation/tests/test_exception_builder.py` | 19 |
| 15 | `apps/reconciliation/tests/test_classification_service.py` | 13 |
| 16 | `apps/reconciliation/tests/test_runner_langfuse.py` | 15 |
| 17 | `apps/agents/tests/test_policy_engine.py` | 19 |
| 18 | `apps/agents/tests/test_guardrails_service.py` | 27 |
| 19 | `apps/agents/tests/test_deterministic_resolver.py` | 20 |
| 20 | `apps/agents/tests/test_agent_memory.py` | 21 |
| 21 | `apps/extraction/tests/test_response_repair_service.py` | 25 |
| 22 | `apps/extraction/tests/test_recovery_lane.py` | 24 |
| 23 | `apps/extraction/tests/test_reconciliation_validator.py` | 15 |
| 24 | `apps/extraction/tests/test_qr_decoder_service.py` | 37 |
| 25 | `apps/extraction/tests/test_prompt_source.py` | 12 |
| 26 | `apps/extraction/tests/test_normalization_service.py` | 34 |
| 27 | `apps/extraction/tests/test_invoice_prompt_composer.py` | 13 |
| 28 | `apps/extraction/tests/test_invoice_category_classifier.py` | 13 |
| 29 | `apps/extraction/tests/test_field_confidence_service.py` | 22 |
| 30 | `apps/extraction/tests/test_evidence_confidence.py` | 39 |
| 31 | `apps/extraction/tests/test_decision_codes.py` | 46 |
| 32 | `apps/extraction/tests/test_duplicate_detection_service.py` | 11 |
| 33 | `apps/extraction/tests/test_credit_service.py` | 40 |
| 34 | `apps/extraction/tests/test_credit_views.py` | 13 |
| 35 | `apps/cases/tests/test_case_state_machine.py` | 28 |
| 36 | `apps/cases/tests/test_review_workflow_service.py` | 12 |
| 37 | `apps/core/tests/test_middleware.py` | 14 |
| 38 | `apps/core/tests/test_celery_tasks.py` | 5 |
| 39 | `apps/agents/tests/test_orchestrator.py` | 5 |
| 40 | `apps/agents/tests/test_base_agent.py` | 11 |
| 41 | `apps/posting/tests/test_eligibility_service.py` | 10 |
| 42 | `apps/posting/tests/test_posting_action_service.py` | 9 |
| 43 | `apps/posting_core/tests/test_posting_validation.py` | 18 |
| 44 | `apps/posting_core/tests/test_posting_confidence.py` | 15 |
| 45 | `apps/erp_integration/tests/test_base_connector.py` | 14 |
| 46 | `apps/erp_integration/tests/test_connector_factory.py` | 10 |
| 47 | `apps/erp_integration/tests/test_base_resolver.py` | 10 |
| 48 | `apps/erp_integration/tests/test_cache_service.py` | 13 |
| 49 | `apps/agents/tests/test_reasoning_planner.py` | 17 |
| 50 | `apps/core_eval/tests/test_learning_engine.py` | 22 |
| 51 | `apps/core_eval/tests/test_end_to_end.py` | 13 |
| 52 | `apps/core_eval/tests/test_views.py` | 29 |
| 53 | `apps/extraction/tests/test_eval_adapter.py` | 10 |
| 54 | `apps/extraction/tests/test_approval_integration.py` | 25 |
| 55 | `apps/reconciliation/tests/test_recon_eval_adapter.py` | 21 |
| | **TOTAL** | **~788** |

---

## 10. AP Finance Agents — Manual Functional Test Guide

> Tester-ready functional guide for AP finance agent flows (reconciliation + agent pipeline). Excludes procurement and posting module deep tests.

**Version**: 1.0 · Last Updated: 2026-04-21
**Scope**: AP finance agent flows only (reconciliation + agent pipeline)

---

### 10.1 Purpose

This section is a tester-ready functional guide for validating AP finance agent behaviour in the 3-way PO reconciliation platform.

It includes:
- End-to-end functional scenarios
- Agent-specific test cases
- Sample test data (without PDF file creation)
- Exact match, fuzzy match, and LLM-assisted behaviour expectations

It excludes:
- Procurement module scenarios
- Posting module deep tests
- UI pixel-level testing

---

### 10.2 In-Scope Components

Primary apps/services covered:
- `apps/reconciliation/` (deterministic matching engine)
- `apps/agents/` (LLM + deterministic system agents)
- `apps/tools/registry/tools.py` (PO/GRN/vendor/invoice tools)
- `apps/cases/` and review routing touchpoints

Primary AP agent types covered:
- `INVOICE_UNDERSTANDING`
- `PO_RETRIEVAL`
- `GRN_RETRIEVAL`
- `RECONCILIATION_ASSIST`
- `EXCEPTION_ANALYSIS`
- `COMPLIANCE_AGENT`
- `REVIEW_ROUTING`
- `CASE_SUMMARY`
- System tail agents where applicable (`SYSTEM_REVIEW_ROUTING`, `SYSTEM_CASE_SUMMARY`)

---

### 10.3 Test Preconditions

1. At least one tenant exists.
2. Test users exist for these roles: `ADMIN`, `AP_PROCESSOR`, `REVIEWER`, `FINANCE_MANAGER`.
3. Core master data is loaded for vendors, POs, and GRNs.
4. Reconciliation config exists with default tolerances:
   - Quantity tolerance: 2%
   - Price tolerance: 1%
   - Amount tolerance: 1%
   - Auto-close quantity tolerance: 5%
   - Auto-close price tolerance: 3%
   - Auto-close amount tolerance: 3%
5. Agent definitions and tool definitions are seeded.
6. For async validation, Celery worker is running. For local deterministic validation, eager mode is acceptable.

Optional precondition for LLM-assisted line matching test:
- A custom `LineMatchLLMFallbackService` implementation is plugged in. By default, fallback returns `None` and no LLM line-resolution occurs.

---

### 10.4 Sample Master Test Data

Use this as a reusable test pack. You can load via admin/API/fixtures.

#### 10.4.1 Vendors

| Vendor ID | Code | Name | Alias |
|---|---|---|---|
| 101 | VEND-ALPHA | Alpha Cooling Solutions Pvt Ltd | Alpha Cooling |
| 102 | VEND-BETA | Beta Facility Services LLP | Beta Services |
| 103 | VEND-GAMMA | Gamma Logistics and Supply | Gamma Supply |

#### 10.4.2 Purchase Orders

| PO Number | Vendor ID | Currency | PO Date | Total Amount | Notes |
|---|---|---|---|---:|---|
| PO-1001 | 101 | INR | 2026-04-10 | 500000.00 | Stock/goods style PO, should route 3-way by policy/heuristic |
| PO-1002 | 102 | INR | 2026-04-11 | 120000.00 | Service style PO, should route 2-way by policy/heuristic |
| PO-1003 | 103 | INR | 2026-04-12 | 250000.00 | Used for mismatch and unresolved cases |

#### 10.4.3 PO Line Items

| PO Number | Line | Item Code | Description | Qty | UOM | Unit Price | Line Amount |
|---|---:|---|---|---:|---|---:|---:|
| PO-1001 | 1 | AC-ODU-01 | VRF Outdoor Unit 10HP | 2 | NOS | 120000.00 | 240000.00 |
| PO-1001 | 2 | AC-IDU-01 | VRF Indoor Cassette Unit 2TR | 6 | NOS | 30000.00 | 180000.00 |
| PO-1001 | 3 | AC-INST-01 | Installation and Commissioning | 1 | LOT | 80000.00 | 80000.00 |
| PO-1002 | 1 | SV-MAINT-01 | Quarterly HVAC Preventive Maintenance | 4 | QTR | 30000.00 | 120000.00 |
| PO-1003 | 1 | LOG-COLD-01 | Cold Chain Logistics Service | 10 | TRIP | 25000.00 | 250000.00 |

#### 10.4.4 GRNs (for 3-way tests)

| GRN Number | PO Number | Receipt Date | Status |
|---|---|---|---|
| GRN-1001-A | PO-1001 | 2026-04-14 | RECEIVED |

#### 10.4.5 GRN Line Items

| GRN Number | Line | Item Code | Qty Received | Qty Accepted | Qty Rejected |
|---|---:|---|---:|---:|---:|
| GRN-1001-A | 1 | AC-ODU-01 | 2 | 2 | 0 |
| GRN-1001-A | 2 | AC-IDU-01 | 6 | 6 | 0 |
| GRN-1001-A | 3 | AC-INST-01 | 1 | 1 | 0 |

---

### 10.5 Sample Invoice Payloads (No PDF Required)

You can create invoice records directly via API/admin with these fields.

#### INV-EXACT-001 (Exact Match Candidate)

Header: `invoice_number=INV-EXACT-001`, vendor=Alpha Cooling Solutions Pvt Ltd (101), `po_number=PO-1001`, currency=INR, subtotal=500000.00, tax_amount=90000.00, total_amount=590000.00, extraction_confidence=0.95

Lines: (1) AC-ODU-01, VRF Outdoor Unit 10HP, qty 2, price 120000.00 · (2) AC-IDU-01, VRF Indoor Cassette Unit 2TR, qty 6, price 30000.00 · (3) AC-INST-01, Installation and Commissioning, qty 1, price 80000.00

#### INV-FUZZY-001 (Deterministic Fuzzy Match Candidate)

Header: `invoice_number=INV-FUZZY-001`, vendor=`Alpha Cooling` (alias of 101), `po_number=PO-1001`, currency=INR, subtotal=500500.00, tax_amount=90090.00, total_amount=590590.00, extraction_confidence=0.93

Lines (intentional text variation): (1) item_code blank, description `VRF Outdor Unit 10 HP` (typo), qty 2, price 120100.00 · (2) item_code blank, `VRF Indoor Casette Unit 2TR` (typo), qty 6, price 29980.00 · (3) item_code blank, `Install & Commissioning`, qty 1, price 80400.00

#### INV-LOWCONF-001 (Low Extraction Confidence)

Header: vendor=Beta Facility Services LLP, `po_number=PO-1002`, total_amount=120000.00, extraction_confidence=**0.55**

#### INV-PO-MISSING-001 (PO Not Found)

Header: vendor=Gamma Logistics and Supply, `po_number=PO-9999` (does not exist), total_amount=250000.00

#### INV-GRN-MISSING-001 (3-way GRN Missing)

Same lines as INV-EXACT-001 but precondition: remove/disable GRN-1001-A before running.

#### INV-UNRESOLVED-LINE-001 (Ambiguous Lines)

Lines: (1) description `Cooling Unit Package` (generic), qty 2, price 120000.00 · (2) description `Cooling Unit Package` (same generic), qty 6, price 30000.00 — creates deterministic line scoring ambiguity.

---

### 10.6 Functional Test Cases

**Legend**: Match Status values: `MATCHED`, `PARTIAL_MATCH`, `UNMATCHED`, `REQUIRES_REVIEW`. Agent pipeline triggers only when deterministic outcome is not cleanly MATCHED/auto-closed.

#### TC-AP-001 Exact Header + Line Match (Deterministic Exact)

**Objective**: Validate exact deterministic match without agent intervention.
**Input**: INV-EXACT-001. Trigger reconciliation from `READY_FOR_RECON` state.
**Expected**: PO-1001 found, mode=THREE_WAY, all lines MATCHED at high confidence, GRN checks pass, final status=`MATCHED`, no agent run created.

---

#### TC-AP-002 Deterministic Fuzzy Line Match

**Objective**: Validate typo/variant descriptions still match via deterministic scoring.
**Input**: INV-FUZZY-001.
**Expected**: Vendor alias `Alpha Cooling` → Vendor 101 resolved. Deterministic line matching selects correct PO lines. Result: `MATCHED` (strict tolerance) or `PARTIAL_MATCH` (minor deltas within auto-close band). Not an LLM line match — uses `LineMatchService` weighted scoring.

---

#### TC-AP-003 Auto-Close for Partial Within Wider Band

**Objective**: Validate policy auto-close for partial discrepancies within wider thresholds.
**Input**: INV-FUZZY-001 with deltas within 5% qty / 3% price / 3% amount.
**Expected**: Deterministic status initially `PARTIAL_MATCH` → policy engine flags auto-close → agent plan sets `skip_agents=True`, `auto_close=True` → no manual review.

---

#### TC-AP-004 Partial Outside Auto-Close → Agent Pipeline

**Objective**: Validate escalation from deterministic to agentic flow.
**Input**: Copy of INV-FUZZY-001 with one line price increased by +7%.
**Expected**: `PARTIAL_MATCH` outside auto-close band → agent pipeline executes with `RECONCILIATION_ASSIST`, `EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, `CASE_SUMMARY` → recommendation created for AP review.

---

#### TC-AP-005 PO Not Found → PO Retrieval Agent

**Objective**: Validate PO missing path and PO retrieval attempt.
**Input**: INV-PO-MISSING-001.
**Expected**: Deterministic: `UNMATCHED` with `PO_NOT_FOUND` exception. Agent plan includes `PO_RETRIEVAL`. `po_lookup` tool call appears in `ToolCall` records. If no PO recovered: routed to AP review with recommendation.

---

#### TC-AP-006 3-Way GRN Missing → GRN Retrieval Agent

**Objective**: Validate GRN retrieval in 3-way mode.
**Input**: INV-GRN-MISSING-001 with missing GRN precondition.
**Expected**: Agent plan includes `GRN_RETRIEVAL` (3-way mode only). `grn_lookup` tool call appears. If GRN still missing: recommendation routes to AP review or escalation.

---

#### TC-AP-007 Two-Way Mode Must Ignore GRN

**Objective**: Validate mode-aware suppression of GRN checks.
**Input**: Service PO PO-1002 and matching service invoice.
**Expected**: Mode=`TWO_WAY`. No GRN exceptions. `GRN_RETRIEVAL` not planned. Reconciliation depends only on invoice vs PO fields.

---

#### TC-AP-008 Low Extraction Confidence → Invoice Understanding Agent

**Objective**: Validate low-confidence routing behaviour.
**Input**: INV-LOWCONF-001 (extraction_confidence=0.55).
**Expected**: Deterministic: `REQUIRES_REVIEW`. Agent plan includes `INVOICE_UNDERSTANDING` before analysis/routing tail. Reviewer summary generated from `EXCEPTION_ANALYSIS` path.

---

#### TC-AP-009 Duplicate Invoice Handling

**Objective**: Validate duplicate invoice protection.
**Steps**: (1) Create and reconcile INV-EXACT-001. (2) Create second invoice with same invoice_number, vendor, and amount.
**Expected**: Duplicate flag set. Classification routes to `REQUIRES_REVIEW`. Agent recommendation should not auto-close duplicate-risk cases.

---

#### TC-AP-010 Currency Mismatch Exception

**Objective**: Validate currency mismatch exception creation.
**Input**: Invoice against PO-1001 but with `currency=USD` instead of INR.
**Expected**: `CURRENCY_MISMATCH` exception generated. Final status not clean `MATCHED`. Agent analysis includes exception rationale and reviewer action.

---

#### TC-AP-011 Tax Mismatch Exception

**Objective**: Validate tax variance exception path.
**Input**: INV-EXACT-001 baseline but with materially different tax_amount.
**Expected**: `TAX_MISMATCH` exception generated. Agent plan may include `COMPLIANCE_AGENT` depending on policy/exception set.

---

#### TC-AP-012 LLM-Assisted Line Match (Optional Feature Test)

**Objective**: Validate optional LLM fallback for ambiguous line matching.
**Input**: INV-UNRESOLVED-LINE-001.
**Default system**: Deterministic scorer marks line as `AMBIGUOUS`/`UNRESOLVED`. No LLM resolution. Exception path proceeds to review/agents.
**With custom fallback enabled only**: LLM fallback returns selected PO line. Match method includes `LLM_FALLBACK` in line metadata. Confidence and rationale captured.
**Tester note**: Record whether fallback service is enabled before executing. Pass/fail depends on environment configuration.

---

#### TC-AP-013 Tool Authorization Guardrail

**Objective**: Validate RBAC enforcement for agent tool calls.
**Input**: Trigger agent flow using a user lacking `purchase_orders.view` or `grns.view`.
**Expected**: Tool authorization denied by guardrail. Denial captured in audit events. Agent should not fabricate evidence; returns uncertainty/routing recommendation.

---

#### TC-AP-014 Recommendation Routing Quality

**Objective**: Validate recommendation types map correctly to outcomes.
**Input**: Run scenarios from TC-AP-004, 005, 006, 008.
**Expected recommendation types**: `AUTO_CLOSE`, `SEND_TO_AP_REVIEW`, `SEND_TO_VENDOR_CLARIFICATION`, `ESCALATE_TO_MANAGER`, `REPROCESS_EXTRACTION`. `DecisionLog` includes rationale and confidence.

---

#### TC-AP-015 End-to-End Case Timeline Completeness

**Objective**: Validate audit and trace completeness across deterministic + agentic path.
**Input**: Execute TC-AP-005 or TC-AP-006 end-to-end.
**Expected**: `AuditEvent` has key events for reconciliation start/end and agent actions. `AgentRun`, `AgentStep`, and `ToolCall` records exist. Governance timeline is chronologically complete and role-aware.

---

### 10.7 Exact vs Fuzzy vs LLM Matching — Tester Cheat Sheet

| Match Type | When It Applies | Output Target |
|---|---|---|
| **Exact** | Item code matches exactly; description near-exact; qty/price/amount within strict tolerance | `MATCHED` without agent pipeline |
| **Fuzzy (Deterministic)** | Item code missing/different; description has typos or synonym-like shifts; token/fuzzy similarity strong enough for deterministic selection | Deterministic line selection succeeds |
| **LLM Fallback** | Only if custom `LineMatchLLMFallbackService` enabled; deterministic candidate ranking ambiguous; fallback returns candidate with rationale | Line metadata marks method `LLM_FALLBACK` |

Default product note: Base fallback is a no-op — LLM line matching is not active out of the box.

---

### 10.8 Suggested Execution Order for Testers

1. TC-AP-001 (baseline exact)
2. TC-AP-002 (fuzzy deterministic)
3. TC-AP-003 (partial auto-close)
4. TC-AP-004 (partial agentic)
5. TC-AP-005 (PO missing)
6. TC-AP-006 (GRN missing in 3-way)
7. TC-AP-007 (2-way suppression of GRN)
8. TC-AP-008 (low extraction confidence)
9. TC-AP-009 to 011 (risk/compliance paths)
10. TC-AP-012 (optional LLM fallback)
11. TC-AP-013 to 015 (guardrail + governance completeness)

---

### 10.9 Test Evidence Checklist

For each executed test case, capture:
- Invoice ID and ReconciliationResult ID
- Match status and reconciliation mode
- Exception list with severity
- Agent plan and actual agents executed
- Tool calls made (`po_lookup`, `grn_lookup`, etc.)
- Final recommendation type and confidence
- Review assignment created (yes/no)
- Relevant audit events present (yes/no)

---

### 10.10 Exit Criteria for AP Finance Agent Functional Sign-Off

Minimum pass criteria:
1. Baseline exact and fuzzy deterministic scenarios pass.
2. PO/GRN missing scenarios trigger expected agent + tool behaviour.
3. Low-confidence and duplicate/risk scenarios route to review correctly.
4. RBAC guardrail denial behaviour is fail-closed.
5. Governance evidence (`AgentRun`/`ToolCall`/`AuditEvent`/`DecisionLog`) is complete for at least one complex case.

Optional criteria:
- LLM fallback line matching validated only in environments where fallback service is explicitly enabled.
