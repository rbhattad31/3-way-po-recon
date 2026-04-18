# 17 — Supervisor Agent Architecture

**Generated**: 2026-04-11 | **Method**: Code-first inspection | **Confidence**: High

---

## 1. Executive Summary

The **SupervisorAgent** is a full AP lifecycle orchestrator that extends the platform's existing `BaseAgent` with a larger tool budget, dynamic skill-based prompt assembly, and end-to-end invoice processing capability. Unlike the existing pipeline of 8 specialized LLM agents (each handling a single concern), the Supervisor owns the entire invoice lifecycle in a single ReAct loop -- from document ingestion to final recommendation.

The Supervisor also supports **smart query routing** -- incoming queries are classified as `CASE_ANALYSIS` (default invoice lifecycle), `AP_INSIGHTS` (system-wide analytics), or `HYBRID` (both). AP Insights queries use 12 dedicated analytics tools and dashboard-enriched context, bypassing the case analysis phases entirely.

**Key design principles:**
- **Skill-based composition** -- prompt and toolset assembled dynamically from 6 registered skills
- **Non-linear phase progression** -- five phases (UNDERSTAND -> VALIDATE -> MATCH -> INVESTIGATE -> DECIDE) with backtracking
- **Smart query routing** -- heuristic-based classification into CASE_ANALYSIS / AP_INSIGHTS / HYBRID modes
- **Dashboard-enriched context** -- pre-loaded KPIs for insights/hybrid queries reduce tool-call overhead
- **Deterministic tool delegation** -- LLM reasons over tool outputs; tools wrap existing deterministic services
- **ERP-aware routing** -- PluginToolRouter routes tools through ERP connectors when available
- **Guardrailed output** -- mandatory `submit_recommendation` call (relaxed for AP_INSIGHTS mode); fallback to safe defaults
- **Real-time progress streaming** -- SSE endpoint streams tool-call progress events for live UI updates

**Evidence**: `apps/agents/services/supervisor_agent.py`, `apps/agents/services/supervisor_query_router.py`, `apps/agents/skills/`, `apps/tools/registry/supervisor_tools.py`, `apps/tools/registry/ap_insights_tools.py`

---

## 2. Architecture Overview

```
                    +-------------------+
                    |   User Query /    |
                    |   Copilot Chat    |
                    +--------+----------+
                             |
                    +--------v----------+
                    |   QueryRouter     |
                    | (classify_query)  |
                    +--+----+----+------+
         CASE_ANALYSIS |    |    | AP_INSIGHTS
                       |  HYBRID |
                       v    v    v
+-------------------------------------------------------------+
|                    SupervisorAgent                           |
|                                                             |
|  +--------------+  +------------------+  +--------------+   |
|  | SkillRegistry|  | PromptBuilder    |  | ContextBuilder|  |
|  |  6 skills    |  | base + skills    |  | invoice facts|   |
|  |  36+ tools   |  | + hints + mode   |  | + dashboard  |   |
|  +------+-------+  +--------+---------+  +------+-------+   |
|         |                   |                    |           |
|         v                   v                    v           |
|  +------------------------------------------------------+   |
|  |              BaseAgent ReAct Loop                     |   |
|  |  (system_prompt + user_message -> LLM -> tool calls)  |   |
|  |  Max 15 tool rounds (vs 10 default)                   |   |
|  |  progress_callback for SSE streaming                  |   |
|  +------------------------+-----------------------------+   |
|                           |                                  |
|  +------------------------v-----------------------------+   |
|  |           OutputInterpreter (mode-aware)              |   |
|  |  JSON parse -> AgentOutputSchema -> guardrail checks  |   |
|  |  AP_INSIGHTS: relaxes submit_recommendation rule      |   |
|  +------------------------------------------------------+   |
+-------------------------------------------------------------+
         |
         v
+-------------------------------------------------------------+
|                    Tool Execution Layer                       |
|                                                              |
|  +------------------+       +---------------------------+    |
|  | PluginToolRouter |------>| ERP Connector (if active) |    |
|  | 5 ERP-routable   |       +---------------------------+    |
|  +--------+---------+                                        |
|           | fallback                                         |
|           v                                                  |
|  +------------------+  +--------------------+                |
|  |  ToolRegistry    |  | AP Insights Tools  |                |
|  |  24 supervisor   |  | 12 analytics tools |                |
|  |  + 6 base tools  |  | (dashboard.view)   |                |
|  +------------------+  +--------------------+                |
+-------------------------------------------------------------+
```

---

## 3. Core Components

### 3.1 SupervisorAgent Class

**File**: `apps/agents/services/supervisor_agent.py`

```python
class SupervisorAgent(BaseAgent):
    agent_type = AgentType.SUPERVISOR
    enforce_json_response = True
```

**Constructor:**

```python
def __init__(self, skill_names=None, query_mode=None):
```

- `skill_names`: Override default skills (defaults to 6 DEFAULT_SKILLS). `ap_insights` is always force-included.
- `query_mode`: One of `CASE_ANALYSIS`, `AP_INSIGHTS`, `HYBRID` (set by `route_and_run()` or manually).

**Key overrides from BaseAgent:**

| Method / Property | Purpose |
|---|---|
| `system_prompt` | Lazy-built from `build_supervisor_prompt()` with skill composition |
| `allowed_tools` | Merged from all active skills (incl. `ap_insights`) + 6 existing base tools |
| `build_user_message()` | Mode-aware: rich context with recon mode, invoice facts, exceptions; AP_INSIGHTS/HYBRID adds pre-loaded dashboard data |
| `interpret_response()` | Mode-aware: delegates to `interpret_supervisor_output()`; AP_INSIGHTS relaxes `submit_recommendation` requirement |
| `run(ctx, progress_callback=None)` | Temporarily patches `MAX_TOOL_ROUNDS` from 10 -> 15; passes `progress_callback` for SSE streaming |

**Class method:**

| Method | Purpose |
|---|---|
| `route_and_run(ctx, *, user_query, user, progress_callback)` | Primary entry point: classifies query via `classify_query()`, enriches context with dashboard data for AP_INSIGHTS/HYBRID, creates agent with determined mode, then runs |

**Registration points** (confirmed in code and tests):
- `AgentType.SUPERVISOR` enum value in `apps/core/enums.py`
- `AGENT_CLASS_REGISTRY[AgentType.SUPERVISOR] = SupervisorAgent`
- `AGENT_PERMISSIONS["SUPERVISOR"] = "agents.run_supervisor"`
- `_AGENT_TYPE_TO_PROMPT_KEY["SUPERVISOR"] = "agent.supervisor_ap_lifecycle"`

### 3.2 Skill-Based Composition System

**File**: `apps/agents/skills/base.py`

The Supervisor uses a **Skill** abstraction to dynamically compose its prompt and toolset at runtime. Skills are code-only (no DB models) and registered in a singleton `SkillRegistry`.

#### Skill Dataclass

```python
@dataclass(frozen=True)
class Skill:
    name: str                          # Unique identifier
    description: str                   # Human-readable description
    prompt_extension: str              # Appended to system prompt
    tools: List[str]                   # Tool names this skill needs
    decision_hints: List[str]          # LLM decision guidance
```

#### SkillRegistry API

| Method | Purpose |
|---|---|
| `register(skill)` | Add a skill to the registry |
| `get(name)` | Retrieve a single skill |
| `get_all()` | Return all registered skills |
| `get_by_names(names)` | Return skills in requested order |
| `all_tools(skill_names)` | Merged, deduplicated tool list |
| `compose_prompt(skill_names)` | Concatenated prompt extensions |
| `compose_hints(skill_names)` | Aggregated decision hints |
| `clear()` | Reset registry (test utility) |

#### Loading Mechanism

Skills are loaded via `_ensure_skills_loaded()` in `supervisor_agent.py`:
1. Imports all 6 skill modules (triggers `register_skill()` at module level), including `ap_insights`
2. If SkillRegistry is empty (e.g., after test `clear()`), reloads modules
3. Also imports `supervisor_tools` and `ap_insights_tools` to ensure tool registration

### 3.3 Query Router

**File**: `apps/agents/services/supervisor_query_router.py`

The query router classifies incoming queries to determine whether the supervisor should run case-specific analysis, system-wide analytics, or both.

#### QueryMode Enum

```python
class QueryMode(str, Enum):
    CASE_ANALYSIS = "CASE_ANALYSIS"   # Default: full invoice lifecycle
    AP_INSIGHTS = "AP_INSIGHTS"       # System-wide analytics questions
    HYBRID = "HYBRID"                 # Both case + system context
```

#### RoutingDecision Dataclass

```python
@dataclass
class RoutingDecision:
    mode: QueryMode
    confidence: float      # 0.0-1.0
    reason: str
    has_case_context: bool  # Whether invoice/case was referenced
```

#### Classification Heuristic (`classify_query()`)

Uses two sets of regex patterns:

| Pattern Set | Count | Examples |
|---|---|---|
| `_INSIGHTS_PATTERNS` | 17 | dashboard, KPI, how many, total, trend, performance, token usage, review queue, touchless, comparison, this week |
| `_CASE_PATTERNS` | 11 | invoice #123, PO #..., this invoice, investigate, validate, vendor verify, duplicate, approve, reject |

**Scoring logic:**
1. Count keyword matches for both insight and case patterns
2. If case context exists (invoice_id, reconciliation_result, case_id), boost case score by +3
3. Both signals >= 2 -> `HYBRID`
4. Stronger insights signal without case context -> `AP_INSIGHTS`
5. Insights signal with case context -> `HYBRID`
6. Default: `CASE_ANALYSIS` if case context present, `AP_INSIGHTS` otherwise

Confidence is capped at 0.95, scaled by keyword count.

---

## 4. Five-Phase Lifecycle

The Supervisor operates through five non-linear phases for **CASE_ANALYSIS** mode. Unlike a fixed pipeline, the agent can **backtrack** between phases based on findings.

For **AP_INSIGHTS** mode, the agent skips all five phases and uses analytics tools directly. For **HYBRID** mode, the agent addresses both system-wide questions (using AP insights tools) and case-specific analysis (using the phase-based lifecycle).

### Phase Diagram

```
                    ┌──────────────┐
                    │  UNDERSTAND  │ ◄── Entry point
                    │  (Extract)   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   VALIDATE   │
                    │ (Check data) │
                    └──────┬───────┘
                           │
            ┌──────────────▼──────────────┐
            │           MATCH             │
            │  (PO/line/GRN matching)     │
            └──────┬──────────────┬───────┘
                   │              │
          success  │              │ failure
                   │       ┌──────▼───────┐
                   │       │ INVESTIGATE  │──── re-extract ──►┐
                   │       │  (Recover)   │                   │
                   │       └──────┬───────┘                   │
                   │              │                    ┌──────▼───────┐
                   │              │ recovered          │  UNDERSTAND  │
                   │              ├────────────────────│  (re-entry)  │
                   │              │                    └──────────────┘
            ┌──────▼──────────────▼───────┐
            │          DECIDE             │
            │  (Route / close / escalate) │
            └─────────────────────────────┘
```

### Phase Details

| Phase | Skill | Tools | Purpose |
|-------|-------|-------|---------|
| **UNDERSTAND** | `invoice_extraction` | `get_ocr_text`, `classify_document`, `extract_invoice_fields`, `re_extract_field` | Get structured data from the document |
| **VALIDATE** | `ap_validation` | `validate_extraction`, `repair_extraction`, `check_duplicate`, `verify_vendor`, `verify_tax_computation` | Check data quality, detect duplicates, verify vendor |
| **MATCH** | `ap_3way_matching` | `po_lookup`, `run_header_match`, `run_line_match`, `grn_lookup`, `run_grn_match`, `get_tolerance_config` | Deterministic matching against PO/GRN |
| **INVESTIGATE** | `ap_investigation` | `re_extract_field`, `invoke_po_retrieval_agent`, `invoke_grn_retrieval_agent`, `get_vendor_history`, `get_case_history`, `invoice_details` | Recovery actions when matching fails |
| **DECIDE** | `ap_review_routing` | `persist_invoice`, `create_case`, `submit_recommendation`, `assign_reviewer`, `generate_case_summary`, `auto_close_case`, `escalate_case`, `exception_list`, `reconciliation_summary` | Final decision, routing, and case management |

### Backtracking Scenarios (from prompt)

- **PO not found** → INVESTIGATE → re-extract PO number → MATCH (retry)
- **Low extraction confidence** → re-extract specific fields → VALIDATE again
- **Duplicate detected** → skip MATCH → go directly to DECIDE

---

## 5. Tool Inventory (42 Total)

### 5.1 Supervisor-Specific Tools (24)

All defined in `apps/tools/registry/supervisor_tools.py`. Each extends `BaseTool`, uses `@register_tool` decorator, and inherits `_scoped()` for tenant isolation.

#### UNDERSTAND Phase (4 tools)

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_ocr_text` | `invoices.view` | Retrieve raw OCR text from DocumentUpload (truncated to 10K chars for LLM context) |
| `classify_document` | `invoices.view` | Classify document type (invoice, credit note, etc.) |
| `extract_invoice_fields` | `extraction.run` | Extract structured header + line items with confidence scores |
| `re_extract_field` | `extraction.run` | Re-extract a specific field (shadow mode — returns current value for supervisor reasoning) |

#### VALIDATE Phase (5 tools)

| Tool | Permission | Description |
|------|-----------|-------------|
| `validate_extraction` | `extraction.run` | Run validation rules via `ValidationService` (mandatory fields, format, cross-field consistency) |
| `repair_extraction` | `extraction.run` | Auto-fix extraction issues via `NormalizationService` |
| `check_duplicate` | `invoices.view` | Duplicate detection via `DuplicateDetectionService` |
| `verify_vendor` | `vendors.view` | Vendor verification by tax ID (primary) or name (fallback with warning) |
| `verify_tax_computation` | `invoices.view` | Verify tax amounts: subtotal + tax = total, line sum = subtotal |

#### MATCH Phase (6 tools)

| Tool | Permission | Description |
|------|-----------|-------------|
| `run_header_match` | `reconciliation.run` | Header-level matching via `HeaderMatchService` (vendor, currency, total amount) |
| `run_line_match` | `reconciliation.run` | Line-level matching via `LineMatchService` (11 weighted signals) |
| `run_grn_match` | `reconciliation.run` | GRN receipt matching (received vs ordered quantities). 3-WAY only |
| `get_tolerance_config` | `reconciliation.view` | Retrieve strict and auto-close tolerance thresholds from `ReconciliationConfig` |
| `po_lookup` | *(existing tool)* | PO lookup — also registered in base tools and ERP-routable |
| `grn_lookup` | *(existing tool)* | GRN lookup — also registered in base tools and ERP-routable |

#### INVESTIGATE Phase (6 tools)

| Tool | Permission | Description |
|------|-----------|-------------|
| `re_extract_field` | `extraction.run` | (Shared with UNDERSTAND) |
| `invoke_po_retrieval_agent` | `agents.run_po_retrieval` | Broader PO search by vendor when direct lookup fails |
| `invoke_grn_retrieval_agent` | `agents.run_grn_retrieval` | GRN search when missing for 3-way match |
| `get_vendor_history` | `vendors.view` | Vendor's recent 5 invoices + 5 POs for pattern detection |
| `get_case_history` | `cases.view` | Previous AP cases for the invoice |
| `invoice_details` | *(existing tool)* | Invoice detail retrieval |

#### DECIDE Phase (9 tools)

| Tool | Permission | Description |
|------|-----------|-------------|
| `persist_invoice` | `invoices.edit` | Save/update invoice status (validates against `InvoiceStatus` enum) |
| `create_case` | `cases.create` | Create or find AP case (shadow mode: defers creation to main pipeline) |
| `submit_recommendation` | `recommendations.route_review` | **Mandatory** — submit final recommendation with type, confidence, reasoning |
| `assign_reviewer` | `reviews.assign` | Route to review queue (AP_REVIEW, PROCUREMENT, VENDOR_CLARIFICATION) with priority 1-10 |
| `generate_case_summary` | `cases.view` | Generate human-readable case summary (max 2000 chars) |
| `auto_close_case` | `recommendations.auto_close` | Auto-close when all criteria met |
| `escalate_case` | `cases.escalate` | Escalate to finance manager with severity level |
| `exception_list` | *(existing tool)* | Exception list retrieval |
| `reconciliation_summary` | *(existing tool)* | Reconciliation summary retrieval |

### 5.2 Inherited Base Tools (6)

Always included regardless of skill selection:
`po_lookup`, `grn_lookup`, `vendor_search`, `invoice_details`, `exception_list`, `reconciliation_summary`

### 5.3 AP Insights Tools (12)

All defined in `apps/tools/registry/ap_insights_tools.py`. Used for system-wide analytics queries (AP_INSIGHTS and HYBRID modes). Registered via the `ap_insights` skill.

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_ap_dashboard_summary` | `dashboard.view` | Overall AP KPIs: total invoices, matched %, pending reviews, open exceptions, avg confidence |
| `get_match_status_breakdown` | `dashboard.view` | Distribution of match statuses (MATCHED, PARTIAL, UNMATCHED, etc.) |
| `get_exception_breakdown` | `dashboard.view` | Exception counts by type (PRICE_VARIANCE, QTY_MISMATCH, etc.) |
| `get_mode_breakdown` | `dashboard.view` | 2-way vs 3-way vs non-PO reconciliation mode distribution |
| `get_daily_volume_trend` | `dashboard.view` | Invoice processing volume over last N days (default 30) |
| `get_recent_activity` | `dashboard.view` | Last N processing events (invoices, reconciliations, reviews) |
| `get_agent_performance_summary` | `dashboard.view` | Agent success rates, avg confidence, tool call counts |
| `get_agent_reliability_matrix` | `dashboard.view` | Per-agent reliability metrics (success rate, avg duration) |
| `get_agent_token_cost` | `dashboard.view` | Token usage and estimated cost per agent type |
| `get_recommendation_intelligence` | `dashboard.view` | Recommendation type distribution and acceptance rates |
| `get_extraction_approval_analytics` | `extraction.view` | Touchless rate, most-corrected fields, approval breakdown |
| `get_review_queue_status` | `reviews.view` | Open review counts by status (pending, assigned, in-review), oldest pending |

### 5.4 Tool Design Patterns

- **All tools extend `BaseTool`** with `@register_tool` decorator
- **Tenant scoping** via `self._scoped(queryset)` — inherited from BaseTool
- **Permission enforcement** — each tool declares `required_permission`; enforced by `AgentGuardrailsService`
- **Shadow mode** — some tools (e.g., `create_case`, `re_extract_field`) operate in shadow mode, returning current state without mutating data
- **Service delegation** — tools call existing deterministic services (`HeaderMatchService`, `LineMatchService`, `ValidationService`, `DuplicateDetectionService`, etc.) rather than reimplementing logic

---

## 6. Plugin Tool Router (ERP Integration Layer)

**File**: `apps/agents/plugins/plugin_router.py`

The `PluginToolRouter` provides an ERP-aware routing layer that intercepts tool calls for a subset of tools and routes them through tenant-specific ERP connectors.

### ERP-Routable Tools

```python
ERP_ROUTABLE_TOOLS = frozenset({
    "po_lookup",
    "grn_lookup",
    "vendor_search",
    "verify_vendor",
    "check_duplicate",
})
```

### Resolution Chain

```
Tool Call
  │
  ├──► Is tool in ERP_ROUTABLE_TOOLS? ──► No ──► ToolRegistry (standard)
  │
  └──► Yes ──► Does tenant have active ERP connector?
                 │
                 ├──► No ──► ToolRegistry (fallback)
                 │
                 └──► Yes ──► Does connector support this operation?
                                │
                                ├──► No ──► ToolRegistry (fallback)
                                │
                                └──► Yes ──► ERPResolutionService
                                              (resolve_po / resolve_grn / resolve_vendor)
```

### ERP Connector Integration

- Uses `ConnectorFactory.get_default_connector(tenant=tenant)`
- Delegates to `ERPResolutionService` for PO, GRN, and vendor resolution
- Adds `_source: "erp_connector"` and `_connector_type` to results for traceability
- All ERP routing failures are non-fatal — graceful fallback to standard ToolRegistry

---

## 7. Prompt Assembly

**File**: `apps/agents/services/supervisor_prompt_builder.py`

### Resolution Chain

```
1. PromptRegistry.get_or_default("agent.supervisor_ap_lifecycle", default=_BASE_SYSTEM_PROMPT)
   │
   ├── Langfuse (if configured)
   ├── Database PromptTemplate
   └── Hardcoded _BASE_SYSTEM_PROMPT (fallback)
   │
2. Replace {max_tool_rounds} placeholder
   │
3. Append skill prompt extensions
   │  "# SKILL-SPECIFIC GUIDANCE"
   │  + compose_prompt(skill_names)
   │
4. Append decision hints
      "# DECISION HINTS"
      + numbered hints from compose_hints(skill_names)
```

### Base System Prompt Content

The `_BASE_SYSTEM_PROMPT` establishes:

- **Role**: AP Lifecycle Supervisor — full invoice processing lifecycle
- **Phases**: UNDERSTAND → VALIDATE → MATCH → INVESTIGATE → DECIDE (non-linear)
- **Reasoning framework**: State intent → call tools → analyze → decide next phase
- **Decision rules**:
  - Never auto-close without checking ALL lines against tolerance
  - Always verify vendor by tax ID, not by name alone
  - Always attempt re-extraction before escalating PO_NOT_FOUND
  - Must call `submit_recommendation` before finishing
  - Default to SEND_TO_AP_REVIEW when uncertain
- **Tolerance handling**: Never hardcode — always call `get_tolerance_config`
- **Output format**: Structured JSON with recommendation_type, confidence, reasoning, evidence, decisions, tools_used, case_summary
- **Valid recommendation types**: AUTO_CLOSE, SEND_TO_AP_REVIEW, SEND_TO_PROCUREMENT, SEND_TO_VENDOR_CLARIFICATION, REPROCESS_EXTRACTION, ESCALATE_TO_MANAGER
- **Guardrails**: Max tool rounds, no fabricated outputs, no RBAC bypass
- **Query routing**: Three operating modes indicated by `[MODE: ...]` in user message:
  - `CASE_ANALYSIS` (default): Full invoice lifecycle, must call `submit_recommendation`
  - `AP_INSIGHTS`: System-wide analytics, use AP insights tools, no `submit_recommendation` required
  - `HYBRID`: Both case-specific and system-wide analysis

### Default Skills

```python
DEFAULT_SKILLS = [
    "invoice_extraction",     # UNDERSTAND
    "ap_validation",          # VALIDATE
    "ap_3way_matching",       # MATCH
    "ap_investigation",       # INVESTIGATE
    "ap_review_routing",      # DECIDE
    "ap_insights",            # AP Analytics (always force-included)
]
```

Note: Even if `ap_insights` is omitted from the list, `SupervisorAgent.__init__()` force-appends it.

---

## 8. Context Builder

**File**: `apps/agents/services/supervisor_context_builder.py`

`build_supervisor_context()` creates a fully-populated `AgentContext` by:

1. **Initializing AgentMemory** with reconciliation mode facts (`is_two_way`, `reconciliation_mode`)
2. **Loading Invoice metadata** from DB (invoice_number, vendor_name, vendor_id, extraction_confidence, invoice_status)
3. **Detecting extraction state** -- sets `extraction_done` flag based on invoice status (EXTRACTED, VALIDATED, PENDING_APPROVAL, READY_FOR_RECON, RECONCILED)
4. **Inferring PO number** from Invoice if not provided
5. **Gathering existing ReconciliationExceptions** (type, severity, field_name, description)
6. **Assembling RBAC context** (actor_user_id, actor_primary_role, actor_roles_snapshot, permission_checked, permission_source, access_granted)
7. **Attaching observability** (trace_id, span_id, Langfuse trace)

### Dashboard Enrichment

**Function**: `enrich_context_with_dashboard(ctx, *, user, tenant)`

Called by `route_and_run()` when query mode is `AP_INSIGHTS` or `HYBRID`. Pre-loads system-wide metrics into `ctx.extra["dashboard"]` to reduce tool-call overhead:

| Key | Source | Data |
|-----|--------|------|
| `ap_summary` | `DashboardService.get_summary()` | Total invoices, matched %, pending reviews, open exceptions, avg confidence |
| `match_breakdown` | `DashboardService.get_match_status_breakdown()` | Distribution of match statuses |
| `exception_breakdown` | `DashboardService.get_exception_breakdown()` | Exception type counts |
| `extraction_analytics` | `ExtractionApprovalService.get_approval_analytics()` | Touchless rate, corrected fields |
| `agent_performance` | `AgentPerformanceDashboardService.get_summary()` | Agent success rates and performance |

All loads are fail-silent -- individual failures do not block the supervisor run.

### User Message Assembly

`SupervisorAgent.build_user_message()` constructs a mode-aware context string:

**For CASE_ANALYSIS (default):**
```
Reconciliation Mode: {mode description}
Invoice ID: {id}
PO Number (from invoice): {po_number}
Invoice Number: {number}
Vendor Name: {name}
Extraction Confidence: {score}
Current Status: {status}

[EXTRACTION ALREADY COMPLETE -- skip UNDERSTAND phase...]  (if applicable)
[RECONCILIATION ALREADY COMPLETE -- skip MATCH phase...]   (if applicable)

Existing Exceptions ({count}):
  - [SEVERITY] TYPE: description

Process this invoice through the full lifecycle...
```

**For AP_INSIGHTS:**
```
User Query: {query}
[MODE: AP_INSIGHTS] Answer the user's analytics/performance question...

--- Pre-loaded Dashboard Context ---
AP Summary: X invoices, Y% matched, Z pending reviews...
Extraction: X% touchless rate
--- End Dashboard Context ---

Answer the analytics question above using available AP insights tools...
```

**For HYBRID:**
Includes both dashboard context and invoice facts, instructs the agent to address both aspects.

**Mode-specific guidance:**
- `TWO_WAY`: "GRN/receipt data is NOT part of this reconciliation. Do NOT flag GRN-related issues."
- `THREE_WAY`: "Invoice vs PO vs GRN"
- `NON_PO`: "No PO matching — focus on validation and vendor verification only."

---

## 9. Output Interpreter

**File**: `apps/agents/services/supervisor_output_interpreter.py`

### JSON Parsing (`parse_supervisor_response`)

1. Strip markdown fences (``` ```json)
2. Attempt full JSON parse
3. Fallback: find first `{` to last `}` and parse substring
4. Final fallback: return empty dict

### Output Validation (`interpret_supervisor_output`)

1. Parse JSON from LLM response
2. Validate through `AgentOutputSchema` (Pydantic model)
3. On validation failure → use defaults (SEND_TO_AP_REVIEW, confidence 0.3)
4. Enrich evidence with `case_summary` (max 2000 chars)
5. Enrich with aggregated tool results if provided
6. Enforce: recommendation_type must be present → default to SEND_TO_AP_REVIEW if missing

### Recommendation Extraction (`extract_recommendation_from_tools`)

Scans tool call history in reverse for the `submit_recommendation` tool call. Extracts recommendation_type, confidence, and reasoning.

### Guardrail: submit_recommendation Enforcement

In `SupervisorAgent.interpret_response()` (mode-aware):

**AP_INSIGHTS mode:**
- `submit_recommendation` is **not required**
- If recommendation_type is missing, defaults to `SEND_TO_AP_REVIEW`
- Sets `_recommendation_submitted = True` and `_query_mode = "AP_INSIGHTS"` in evidence

**CASE_ANALYSIS / HYBRID mode:**
- If `_recommendation_submitted` flag is absent in evidence:
  - Check if `recommendation_type` was set via structured output -> mark as submitted
  - Otherwise: default to `SEND_TO_AP_REVIEW`, cap confidence at 0.3, add warning
- Query mode is recorded in `evidence._query_mode`

---

## 10. Decision Rules and Guardrails

### Mandatory Rules (enforced in prompt + code)

| Rule | Enforcement |
|------|-------------|
| Must call `submit_recommendation` | Code check in `interpret_response()` |
| Never auto-close without checking ALL lines | Prompt instruction |
| Verify vendor by tax ID, not name | Prompt instruction + `verify_vendor` tool |
| Attempt re-extraction before PO_NOT_FOUND escalation | Prompt instruction |
| Never hardcode tolerance values | Prompt instruction + `get_tolerance_config` tool |
| No fabricated tool outputs | Prompt instruction |
| Max 15 tool rounds per session | Code enforcement via `MAX_TOOL_ROUNDS` patch |

### Confidence-Based Routing (from decision hints)

| Confidence | Condition | Recommendation |
|-----------|-----------|----------------|
| ≥ 0.9 | All lines match within tolerance | `AUTO_CLOSE` |
| ≥ 0.6 | Some deviations exist | `SEND_TO_AP_REVIEW` |
| < 0.6 | Critical exceptions found | `ESCALATE_TO_MANAGER` |

### Valid Recommendation Types

| Type | Description |
|------|-------------|
| `AUTO_CLOSE` | All checks pass, within tolerance |
| `SEND_TO_AP_REVIEW` | Needs human AP review |
| `SEND_TO_PROCUREMENT` | Procurement team issue |
| `SEND_TO_VENDOR_CLARIFICATION` | Vendor needs to clarify |
| `REPROCESS_EXTRACTION` | Extraction quality too low |
| `ESCALATE_TO_MANAGER` | High-risk issue requiring management |

---

## 11. RBAC and Security Integration

### Agent Permission

- **Permission**: `agents.run_supervisor`
- **Enforced by**: `AgentGuardrailsService` (pre-flight check in BaseAgent)

### Tool Permissions (24 supervisor tools + 12 AP insights tools)

| Permission | Tools |
|-----------|-------|
| `invoices.view` | `get_ocr_text`, `classify_document`, `verify_tax_computation` |
| `invoices.edit` | `persist_invoice` |
| `extraction.run` | `extract_invoice_fields`, `validate_extraction`, `repair_extraction`, `re_extract_field` |
| `extraction.view` | `get_extraction_approval_analytics` |
| `vendors.view` | `verify_vendor`, `get_vendor_history` |
| `reconciliation.run` | `run_header_match`, `run_line_match`, `run_grn_match` |
| `reconciliation.view` | `get_tolerance_config` |
| `agents.run_po_retrieval` | `invoke_po_retrieval_agent` |
| `agents.run_grn_retrieval` | `invoke_grn_retrieval_agent` |
| `cases.view` | `get_case_history`, `generate_case_summary` |
| `cases.create` | `create_case` |
| `cases.escalate` | `escalate_case` |
| `recommendations.route_review` | `submit_recommendation` |
| `recommendations.auto_close` | `auto_close_case` |
| `reviews.assign` | `assign_reviewer` |
| `reviews.view` | `get_review_queue_status` |
| `dashboard.view` | `get_ap_dashboard_summary`, `get_match_status_breakdown`, `get_exception_breakdown`, `get_mode_breakdown`, `get_daily_volume_trend`, `get_recent_activity`, `get_agent_performance_summary`, `get_agent_reliability_matrix`, `get_agent_token_cost`, `get_recommendation_intelligence` |

### Tenant Isolation

All tools use `self._scoped(queryset)` which filters by the `tenant` attribute in the execution context. This is inherited from `BaseTool` and ensures multi-tenant data isolation.

---

## 12. Relationship to Existing Agent Pipeline

### Existing Pipeline (Orchestrator-Based)

```
Orchestrator (PolicyEngine / ReasoningPlanner)
  │
  ├── ExtractionAgent
  ├── ValidationAgent
  ├── PORetrievalAgent
  ├── GRNRetrievalAgent
  ├── MatchingAgent
  ├── ExceptionAnalysisAgent
  ├── RecommendationAgent
  └── ReviewAgent (stub)
```

Each agent is a separate `BaseAgent` subclass with its own prompt, tools, and single responsibility. The Orchestrator sequences them.

### Supervisor (Single-Agent)

```
SupervisorAgent (single ReAct loop)
  ├── 6 skills compose into one prompt
  ├── 42 tools available in one session (24 supervisor + 12 AP insights + 6 base)
  └── Non-linear phase progression + AP insights mode
```

### Key Differences

| Aspect | Existing Pipeline | Supervisor |
|--------|------------------|------------|
| Architecture | Multi-agent, orchestrator-sequenced | Single-agent, self-directed |
| Tool budget | 10 rounds per agent | 15 rounds total |
| Phase control | Orchestrator decides next agent | LLM decides next phase |
| Backtracking | Not supported (linear) | Supported (non-linear) |
| Tool access | Scoped per agent | All 42 tools available |
| Prompt | Per-agent, static | Composed from skills |
| Recovery | Limited (agent fails → escalate) | Investigation phase with recovery actions |
| Output | Per-agent AgentOutput | Single comprehensive AgentOutput |

### Coexistence

The Supervisor runs alongside the existing pipeline. It is registered as `AgentType.SUPERVISOR` with its own permission (`agents.run_supervisor`) and prompt key (`agent.supervisor_ap_lifecycle`). The existing orchestrator-based pipeline remains fully functional and is the primary production path. The Supervisor currently operates in a shadow/parallel mode — some tools (e.g., `create_case`) defer mutations to the main pipeline.

---

## 13. Test Coverage

**File**: `apps/agents/tests/test_supervisor_agent.py`

### Test Categories (~40 tests)

| Category | ID Prefix | Count | Scope |
|----------|-----------|-------|-------|
| SkillRegistry | SR-01..06 | 6 | Registration, retrieval, tool merging, prompt composition, hints, clear |
| PluginToolRouter | PT-01..04 | 4 | ERP routing, fallback, non-ERP tools, missing connector |
| Agent Registration | SA-01..03 | 3 | AgentType enum, class registry, permission mapping |
| Prompt Assembly | SP-01..03 | 3 | Base prompt, skill extensions, hint injection |
| Tool Integration | ST-01..05 | 5 | Tool merging, deduplication, existing tool inclusion, skill-specific tools |
| User Message | SU-01..03 | 3 | TWO_WAY mode text, exceptions formatting, minimal context |
| Output Interpreter | SO-01..04 | 4 | Valid JSON, markdown-fenced JSON, partial JSON, empty response |
| Guardrails | SG-01..02 | 2 | Missing recommendation enforcement, confidence capping |
| Prompt Registry | SPR-01 | 1 | Langfuse/DB prompt key mapping |
| Full Agent Run | SAR-01..04 | 4 | Mocked LLM end-to-end runs with various scenarios |
| Tool Execution | STE-01..04 | 4 | Individual tool `run()` methods with mocked DB |
| Context Builder | SCB-01..02 | 2 | Context assembly with/without invoice data |
| Max Tool Rounds | SMR-01 | 1 | MAX_TOOL_ROUNDS patch and restore |

### Key Test Assertions

- All 24 supervisor tools have entries in `TOOL_PERMISSIONS`
- `SUPERVISOR_MAX_TOOL_ROUNDS = 15`
- `AgentType.SUPERVISOR` exists in enum
- `AGENT_CLASS_REGISTRY[AgentType.SUPERVISOR]` maps to `SupervisorAgent`
- Missing `submit_recommendation` defaults to `SEND_TO_AP_REVIEW` with confidence ≤ 0.3
- Skills compose prompts in order and merge tools without duplicates
- PluginToolRouter falls back to ToolRegistry when no ERP connector is active

---

## 14. Configuration and Extensibility

### Adding a New Skill

1. Create `apps/agents/skills/my_skill.py`
2. Define a `Skill` dataclass with name, description, prompt_extension, tools, decision_hints
3. Call `register_skill(skill)` at module level
4. Add the skill name to `DEFAULT_SKILLS` in `supervisor_prompt_builder.py` (or pass custom list to `SupervisorAgent(skill_names=[...])`)
5. Register corresponding tools in `supervisor_tools.py` or `ap_insights_tools.py`
6. Add tool permissions to `TOOL_PERMISSIONS` dict
7. Add import to `_ensure_skills_loaded()` in `supervisor_agent.py`

### Custom Skill Selection

```python
# Use only extraction and matching
agent = SupervisorAgent(skill_names=["invoice_extraction", "ap_3way_matching"])
```

### Prompt Override via PromptRegistry

The supervisor prompt can be managed externally via:
- **Langfuse**: Configure prompt `agent.supervisor_ap_lifecycle`
- **Database**: Create `PromptTemplate` with key `agent.supervisor_ap_lifecycle`
- **Fallback**: Hardcoded `_BASE_SYSTEM_PROMPT` in `supervisor_prompt_builder.py`

---

## 15. Data Flow: End-to-End Invoice Processing

```
1. Entry
   build_supervisor_context(invoice_id=123, reconciliation_mode="THREE_WAY", ...)
     → AgentContext with invoice facts, exceptions, RBAC, memory

2. Initialization
   SupervisorAgent(skill_names=DEFAULT_SKILLS)
     -> Lazy-build system_prompt from 6 skills
     -> Merge 42 tools from skills + base

3. ReAct Loop (up to 15 rounds)
   Loop:
     a. LLM receives: system_prompt + user_message + tool results
     b. LLM outputs: reasoning + tool call(s)
     c. Tool executes: PluginToolRouter → ERP or ToolRegistry
     d. Result returned to LLM
   Until: LLM produces final JSON response (no more tool calls)

4. Output Interpretation
   interpret_supervisor_output(raw_content)
     → Parse JSON → Validate schema → Enrich evidence
     → Enforce submit_recommendation guardrail

5. Result
   AgentOutput with:
     - recommendation_type (e.g., AUTO_CLOSE)
     - confidence (0.0-1.0)
     - reasoning
     - evidence (match_status, vendor_verified, deviations, etc.)
     - decisions list
     - tools_used list
     - case_summary
```

---

## 16. Open Questions and Future Considerations

| # | Question | Status |
|---|----------|--------|
| 1 | Shadow mode: When will supervisor tools perform actual mutations (create cases, update status)? | Open |
| 2 | Supervisor vs orchestrator: Is the plan to eventually replace the multi-agent pipeline? | Open |
| 3 | Tool budget: Is 15 rounds sufficient for complex invoices with many line items? | Needs monitoring |
| 4 | ERP routing: `verify_vendor` and `check_duplicate` are in ERP_ROUTABLE_TOOLS but have no ERP-specific implementation paths | Open |
| 5 | Skill hot-loading: Can skills be enabled/disabled per tenant at runtime? | Not currently supported |
| 6 | ~~Concurrent supervisor runs: Is there locking to prevent two supervisors processing the same invoice?~~ | **Resolved**: Cache-based lock (`supervisor:run:{tenant}:{invoice}`, 600s TTL) in `run_supervisor_pipeline_task` prevents concurrent runs |
| 7 | ~~Eval framework: Are there eval benchmarks comparing supervisor vs multi-agent pipeline accuracy?~~ | **Resolved**: `AgentEvalAdapter.sync_for_agent_run()` + `_record_supervisor_signals()` capture 4 learning signal types; eval framework active |
| 8 | Query router: Should classification use LLM fallback for ambiguous queries (code has extension point but no implementation)? | Open |
| 9 | AP Insights: Should dashboard enrichment be cached across queries within the same session? | Open |

---

## 17. File Reference

| File | Purpose |
|------|---------|
| `apps/agents/services/supervisor_agent.py` | SupervisorAgent class, run() override, route_and_run(), skill loading |
| `apps/agents/services/supervisor_prompt_builder.py` | Prompt assembly from base + skills + hints |
| `apps/agents/services/supervisor_context_builder.py` | AgentContext factory with invoice/exception/RBAC data + dashboard enrichment |
| `apps/agents/services/supervisor_output_interpreter.py` | JSON parsing, schema validation, guardrail enforcement |
| `apps/agents/services/supervisor_query_router.py` | QueryMode classification (CASE_ANALYSIS / AP_INSIGHTS / HYBRID) |
| `apps/agents/skills/base.py` | Skill dataclass and SkillRegistry singleton |
| `apps/agents/skills/invoice_extraction.py` | UNDERSTAND phase skill |
| `apps/agents/skills/ap_validation.py` | VALIDATE phase skill |
| `apps/agents/skills/ap_matching.py` | MATCH phase skill |
| `apps/agents/skills/ap_investigation.py` | INVESTIGATE phase skill |
| `apps/agents/skills/ap_review_routing.py` | DECIDE phase skill |
| `apps/agents/skills/ap_insights.py` | AP Analytics skill (12 tools for system-wide queries) |
| `apps/agents/plugins/plugin_router.py` | PluginToolRouter with ERP connector routing |
| `apps/tools/registry/supervisor_tools.py` | 24 supervisor-specific tool implementations |
| `apps/tools/registry/ap_insights_tools.py` | 12 AP insights analytics tool implementations |
| `apps/agents/tests/test_supervisor_agent.py` | ~40 tests covering all supervisor components |
| `apps/copilot/views.py` | Copilot SSE integration: `supervisor_run`, `supervisor_run_stream`, `_build_supervisor_summary` |
| `apps/agents/tasks.py` | `run_supervisor_pipeline_task` Celery task with Langfuse tracing + eval/learning signals |

---

## 18. Copilot Integration (SSE Streaming)

The Supervisor is integrated into the Copilot chat interface via two endpoints:

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/copilot/supervisor/run/` | POST | Synchronous (eager) supervisor run; returns JSON result |
| `/api/v1/copilot/supervisor/stream/` | POST | SSE streaming supervisor run with real-time progress events |

Both require `agents.use_copilot` permission.

### SSE Streaming Architecture (`supervisor_run_stream`)

The streaming endpoint orchestrates a full pipeline in a background thread:

1. **Phase 1: Extraction** (if `upload_id` provided without existing invoice) -- runs `process_invoice_upload_task` synchronously
2. **Phase 2: Reconciliation** (if no `reconciliation_result_id`) -- runs `ReconciliationRunnerService.run()` directly
3. **Phase 3: Supervisor Analysis** -- runs `SupervisorAgent.run()` with `progress_callback`

**SSE Event Types:**

| Event Type | Payload | When |
|-----------|---------|------|
| `pipeline_stage` | `{stage, status, message}` | Extraction/reconciliation/analysis start/done/failed |
| `tool_call` | `{tool, status, ...}` | Each tool call progress (via `progress_callback`) |
| `complete` | `{recommendation, confidence, summary, agent_run_id, ...}` | Supervisor finished |
| `error` | `{message}` | Unrecoverable failure |
| `heartbeat` | `{}` | Every 180s to keep connection alive |

### Summary Builder (`_build_supervisor_summary`)

Transforms an `AgentRun` into a structured dict for the UI:
- **Recommendation**: label, type, severity (success/warning/danger)
- **Findings**: invoice number, vendor, extraction confidence, match status, duplicate check, vendor verification, tax computation, PO
- **Issues**: failed tools, warnings, min_tool_calls not met
- **Tool details**: per-tool label, success, duration, input/output summaries with human-readable formatting
- **Analysis text**: case_summary from evidence (max 500 chars)

### Message Persistence

After completion, supervisor results are persisted as `CopilotMessage` records (user message "Run Supervisor Agent" + assistant message with markdown summary + evidence cards + follow-up chips).

### Concurrent Run Protection

`run_supervisor_pipeline_task` uses a cache-based lock (`supervisor:run:{tenant}:{invoice}`, 600s TTL) to prevent duplicate concurrent runs for the same invoice.

### UI Integration (Copilot Case Hub)

**Page**: `/copilot/cases/` (`copilot_case_hub` view)
**Template**: `templates/copilot/ap_copilot.html`
**JS**: `static/js/ap-copilot.js`

The Copilot Case Hub is a ChatGPT-style interface (sidebar with sessions, main chat area) that serves as the primary UI for triggering the supervisor. Each session is optionally linked to an AP case.

**Trigger points for the supervisor:**

| Trigger | Context | Behavior |
|---------|---------|----------|
| "Run Supervisor Agent" chip button | Case-linked session (welcome chips) | Calls `runSupervisor()` -> SSE stream |
| Supervisor icon button (`#btnRunSupervisor`) | Chat input bar (case mode) | Calls `runSupervisor()` -> SSE stream |
| Auto-run on page load | `/copilot/session/<id>/?auto_run=1` | `CFG.autoRunSupervisor=true` triggers `runSupervisor()` after 500ms delay |
| Post-upload auto-run | After invoice file upload completes | `waitForInvoiceThenRunSupervisor()` polls for invoice creation, then runs |
| Chat message routing | User sends message in case session | `chipSend()` routes to `runSupervisor(text)` for supervisor-driven queries |

**JS flow (`runSupervisor`):**

1. Ensures a copilot session exists (`ensureSession()`)
2. Appends user message bubble + animated progress indicator
3. Opens SSE connection to `/api/v1/copilot/supervisor/stream/` with `{invoice_id, upload_id, case_id, session_id, reconciliation_result_id}`
4. Renders real-time progress steps as SSE events arrive (pipeline stages, tool calls with labels, reasoning)
5. On `complete` event: renders recommendation card with findings, evidence cards, tool execution timeline, and follow-up action chips
6. On `error` event: displays error message in chat

**Two UI modes:**
- **Case mode** (`IS_CASE=true`): Full case workspace with header tabs (Overview, Lines, Exceptions, Chat). Supervisor results show rich streaming steps with tool input/output details
- **Plain chat mode** (`IS_CASE=false`): Simpler progress steps; used for system-wide AP insights queries without a linked case

---

## 19. Eval & Learning Integration

### Eval Adapter

`AgentEvalAdapter.sync_for_agent_run(agent_run)` is called after every supervisor run (both task and SSE paths). Creates/updates `EvalRun` and `EvalMetric` records for the supervisor execution.

### Learning Signals

`_record_supervisor_signals(agent_run, invoice_id, tenant)` records 4 types of learning signals:

| Signal Type | Condition | Purpose |
|-------------|-----------|---------|
| `supervisor_low_confidence` | COMPLETED with confidence < 0.5 | Flags uncertain decisions for prompt tuning |
| `supervisor_tool_failure` | Any `AgentStep` with `success=False` | Tracks tool reliability issues |
| `supervisor_recovery_used` | `evidence.recovery_actions` non-empty | Indicates quality issues requiring re-extraction |
| `supervisor_fallback_recommendation` | `SEND_TO_AP_REVIEW` with confidence < 0.4 or no tool submission | Detects when supervisor could not make a confident decision |

All signals are linked to `EvalRun` records and feed into the `LearningEngine` for pattern detection and corrective action proposals.

### Langfuse Observability

Both trigger paths create root Langfuse traces:
- **Task**: trace_id = celery_task_id (hex), name = `TRACE_SUPERVISOR_PIPELINE`
- **SSE stream**: trace_id = uuid4().hex, name = `TRACE_SUPERVISOR_PIPELINE`
- **Score**: `SUPERVISOR_CONFIDENCE` emitted on completion and failure (0.0)
- **Session linkage**: `derive_session_id()` links traces to case/invoice sessions
