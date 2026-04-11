# 17 — Supervisor Agent Architecture

**Generated**: 2026-04-11 | **Method**: Code-first inspection | **Confidence**: High

---

## 1. Executive Summary

The **SupervisorAgent** is a full AP lifecycle orchestrator that extends the platform's existing `BaseAgent` with a larger tool budget, dynamic skill-based prompt assembly, and end-to-end invoice processing capability. Unlike the existing pipeline of 8 specialized LLM agents (each handling a single concern), the Supervisor owns the entire invoice lifecycle in a single ReAct loop — from document ingestion to final recommendation.

**Key design principles:**
- **Skill-based composition** — prompt and toolset assembled dynamically from registered skills
- **Non-linear phase progression** — five phases (UNDERSTAND → VALIDATE → MATCH → INVESTIGATE → DECIDE) with backtracking
- **Deterministic tool delegation** — LLM reasons over tool outputs; tools wrap existing deterministic services
- **ERP-aware routing** — PluginToolRouter routes tools through ERP connectors when available
- **Guardrailed output** — mandatory `submit_recommendation` call; fallback to safe defaults

**Evidence**: `apps/agents/services/supervisor_agent.py`, `apps/agents/skills/`, `apps/tools/registry/supervisor_tools.py`

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    SupervisorAgent                          │
│                                                             │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ SkillRegistry│  │PromptBuilder     │  │ContextBuilder│  │
│  │  5 skills    │  │ base + skills    │  │ invoice facts│  │
│  │  24+ tools   │  │ + hints          │  │ + exceptions │  │
│  └──────┬───────┘  └────────┬─────────┘  └──────┬───────┘  │
│         │                   │                    │          │
│         ▼                   ▼                    ▼          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              BaseAgent ReAct Loop                    │   │
│  │  (system_prompt + user_message → LLM → tool calls)  │   │
│  │  Max 15 tool rounds (vs 10 default)                  │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                   │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │           OutputInterpreter                          │   │
│  │  JSON parse → AgentOutputSchema → guardrail checks   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Tool Execution Layer                      │
│                                                             │
│  ┌──────────────────┐       ┌───────────────────────────┐  │
│  │PluginToolRouter  │──────▶│ ERP Connector (if active) │  │
│  │ 5 ERP-routable   │       └───────────────────────────┘  │
│  └────────┬─────────┘                                      │
│           │ fallback                                        │
│           ▼                                                 │
│  ┌──────────────────┐                                      │
│  │  ToolRegistry    │  24 supervisor tools                 │
│  │  (BaseTool)      │  + 6 existing agent tools            │
│  └──────────────────┘                                      │
└─────────────────────────────────────────────────────────────┘
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

**Key overrides from BaseAgent:**

| Method / Property | Purpose |
|---|---|
| `system_prompt` | Lazy-built from `build_supervisor_prompt()` with skill composition |
| `allowed_tools` | Merged from all active skills + 6 existing base tools |
| `build_user_message()` | Rich context with reconciliation mode, invoice facts, exceptions |
| `interpret_response()` | Delegates to `interpret_supervisor_output()` + enforces `submit_recommendation` |
| `run()` | Temporarily patches `MAX_TOOL_ROUNDS` from 10 → 15 |

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
1. Imports all 5 skill modules (triggers `register_skill()` at module level)
2. If SkillRegistry is empty (e.g., after test `clear()`), reloads modules
3. Also imports `supervisor_tools` to ensure tool registration

---

## 4. Five-Phase Lifecycle

The Supervisor operates through five non-linear phases. Unlike a fixed pipeline, the agent can **backtrack** between phases based on findings.

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

## 5. Tool Inventory (30 Total)

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

### 5.3 Tool Design Patterns

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

### Default Skills

```python
DEFAULT_SKILLS = [
    "invoice_extraction",     # UNDERSTAND
    "ap_validation",          # VALIDATE
    "ap_3way_matching",       # MATCH
    "ap_investigation",       # INVESTIGATE
    "ap_review_routing",      # DECIDE
]
```

---

## 8. Context Builder

**File**: `apps/agents/services/supervisor_context_builder.py`

`build_supervisor_context()` creates a fully-populated `AgentContext` by:

1. **Initializing AgentMemory** with reconciliation mode facts (`is_two_way`, `reconciliation_mode`)
2. **Loading Invoice metadata** from DB (invoice_number, vendor_name, vendor_id, extraction_confidence, invoice_status)
3. **Inferring PO number** from Invoice if not provided
4. **Gathering existing ReconciliationExceptions** (type, severity, field_name, description)
5. **Assembling RBAC context** (actor_user_id, actor_primary_role, actor_roles_snapshot, permission_checked, permission_source, access_granted)
6. **Attaching observability** (trace_id, span_id, Langfuse trace)

### User Message Assembly

`SupervisorAgent.build_user_message()` constructs a rich context string:

```
Reconciliation Mode: {mode description}

Invoice ID: {id}
PO Number (from invoice): {po_number}
Document Upload ID: {upload_id}
Invoice Number: {number}
Vendor Name: {name}
Extraction Confidence: {score}
Current Status: {status}

Existing Exceptions ({count}):
  - [SEVERITY] TYPE: description

Process this invoice through the full lifecycle...
```

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

In `SupervisorAgent.interpret_response()`:
- If `_recommendation_submitted` flag is absent in evidence:
  - Check if `recommendation_type` was set via structured output → mark as submitted
  - Otherwise: default to `SEND_TO_AP_REVIEW`, cap confidence at 0.3, add warning

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

### Tool Permissions (24 supervisor tools)

| Permission | Tools |
|-----------|-------|
| `invoices.view` | `get_ocr_text`, `classify_document`, `verify_tax_computation` |
| `invoices.edit` | `persist_invoice` |
| `extraction.run` | `extract_invoice_fields`, `validate_extraction`, `repair_extraction`, `re_extract_field` |
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
  ├── 5 skills compose into one prompt
  ├── 30 tools available in one session
  └── Non-linear phase progression
```

### Key Differences

| Aspect | Existing Pipeline | Supervisor |
|--------|------------------|------------|
| Architecture | Multi-agent, orchestrator-sequenced | Single-agent, self-directed |
| Tool budget | 10 rounds per agent | 15 rounds total |
| Phase control | Orchestrator decides next agent | LLM decides next phase |
| Backtracking | Not supported (linear) | Supported (non-linear) |
| Tool access | Scoped per agent | All 30 tools available |
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
5. Register corresponding tools in `supervisor_tools.py`
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
     → Lazy-build system_prompt from skills
     → Merge 30 tools from skills + base

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
| 6 | Concurrent supervisor runs: Is there locking to prevent two supervisors processing the same invoice? | Not visible in code |
| 7 | Eval framework: Are there eval benchmarks comparing supervisor vs multi-agent pipeline accuracy? | Not visible |

---

## 17. File Reference

| File | Purpose |
|------|---------|
| `apps/agents/services/supervisor_agent.py` | SupervisorAgent class, run() override, skill loading |
| `apps/agents/services/supervisor_prompt_builder.py` | Prompt assembly from base + skills + hints |
| `apps/agents/services/supervisor_context_builder.py` | AgentContext factory with invoice/exception/RBAC data |
| `apps/agents/services/supervisor_output_interpreter.py` | JSON parsing, schema validation, guardrail enforcement |
| `apps/agents/skills/base.py` | Skill dataclass and SkillRegistry singleton |
| `apps/agents/skills/invoice_extraction.py` | UNDERSTAND phase skill |
| `apps/agents/skills/ap_validation.py` | VALIDATE phase skill |
| `apps/agents/skills/ap_matching.py` | MATCH phase skill |
| `apps/agents/skills/ap_investigation.py` | INVESTIGATE phase skill |
| `apps/agents/skills/ap_review_routing.py` | DECIDE phase skill |
| `apps/agents/plugins/plugin_router.py` | PluginToolRouter with ERP connector routing |
| `apps/tools/registry/supervisor_tools.py` | 24 supervisor-specific tool implementations |
| `apps/agents/tests/test_supervisor_agent.py` | ~40 tests covering all supervisor components |
