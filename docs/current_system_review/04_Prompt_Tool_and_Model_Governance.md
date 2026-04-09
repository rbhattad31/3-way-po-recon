# 04 — Prompt, Tool, and Model Governance

**Generated**: 2026-04-09 | **Method**: Code-first inspection  
**Evidence files**: `core/prompt_registry.py`, `core/models.py` (PromptTemplate), `tools/registry/`, `agents/services/llm_client.py`, `agents/services/agent_classes.py`, `extraction/services/invoice_prompt_composer.py`

---

## 1. Prompt Architecture

### Prompt Resolution Chain

```
PromptRegistry.get(slug, **format_vars)
  1. In-process dict cache  (_cache[slug])
  2. Langfuse prompt management  (label="production")
     └── slug → Langfuse name via slug_to_langfuse_name()
     └── Returns text with { } re-escaped to {{ }} for Python format_map
  3. Database  (PromptTemplate model, is_active=True, filter by slug)
  4. Hardcoded fallback  (defined in prompt_registry.py for critical prompts)
```

**Fail behavior**: If all sources fail → warning logged, empty string returned. Callers receive empty system prompt (not a hard failure).

**Cache invalidation**: In-process cache is per-process, not shared across workers. A Celery worker restart clears the cache. No explicit invalidation mechanism.

---

### Prompt Keys (confirmed from code)

#### Extraction Prompts
| Slug | Type | Description |
|------|------|-------------|
| `extraction.invoice_base` | Modular base | Core invoice extraction instructions (18-field schema) |
| `extraction.invoice_system` | Monolithic fallback | Legacy single-prompt extraction; used if composed prompt unavailable |
| `extraction.category.goods` | Category overlay | Additional instructions for goods/product invoices |
| `extraction.category.service` | Category overlay | Additional instructions for service invoices |
| `extraction.category.travel` | Category overlay | Additional instructions for travel/expense invoices |
| `extraction.country.india_gst` | Country overlay | India GST: CGST/SGST/IGST handling |
| `extraction.country.generic_vat` | Country overlay | Generic VAT extraction |

#### Agent Prompts
| Slug | Agent |
|------|-------|
| `agent.invoice_understanding` | InvoiceUnderstandingAgent |
| `agent.po_retrieval` | PORetrievalAgent |
| `agent.grn_retrieval` | GRNRetrievalAgent |
| `agent.reconciliation_assist` | ReconciliationAssistAgent |
| `agent.exception_analysis` | ExceptionAnalysisAgent |
| `agent.review_routing` | ReviewRoutingAgent |
| `agent.case_summary` | CaseSummaryAgent |

**Total confirmed prompt keys**: 14 (matches README's "18 prompts in Langfuse" — 4 additional not inspected in detail)

---

### Modular Prompt Composition (`InvoicePromptComposer`)

For extraction, the system builds a composed prompt at runtime:

```
InvoicePromptComposer.compose(ocr_text, vendor_name, country_hint)
  1. Load base prompt   (extraction.invoice_base)
  2. Classify category  (LLM call → goods / service / travel)
  3. Load category overlay  (extraction.category.<type>)
  4. Load country overlay  (extraction.country.<hint> if applicable)
  5. Concatenate: base + "\n\n" + category_overlay + "\n\n" + country_overlay
  6. Return composed_prompt + metadata dict

Metadata captured:
  - invoice_category, invoice_category_confidence
  - base_prompt_key, base_prompt_version
  - category_prompt_key, category_prompt_version
  - country_prompt_key, country_prompt_version
  - prompt_hash (SHA-256 of composed prompt)
  - schema_code
```

The composed prompt is passed to `InvoiceExtractionAgent` via `ctx.extra["composed_prompt"]`.

---

### Prompt Traceability

Per extraction run, the following is persisted:
- `AgentRun.prompt_version` = prompt_hash (first 50 chars) or source type
- `AgentRun.input_payload._prompt_meta` = full composition metadata (keys, versions, hash)
- `AgentRun.invocation_reason` = `"extraction:composed"` or `"extraction:monolithic_fallback"`
- Langfuse span metadata: `invoice_category`, `prompt_hash`, `base_prompt_key`, etc.

**Prompt versioning**: Langfuse handles versioning (labeled by "production"). The `prompt_hash` provides content-hash-based traceability across runs.

---

### PromptTemplate Model (DB Storage)

```python
class PromptTemplate(BaseModel):
    slug    = CharField(unique=True)     # e.g. "agent.exception_analysis"
    name    = CharField()
    content = TextField()                # raw prompt text
    version = PositiveIntegerField()
    is_active = BooleanField()
    # (additional metadata fields likely present — not read in full)
```

DB storage acts as fallback when Langfuse is unavailable. The `seed_prompts` management command populates this from code.

---

## 2. Tool Inventory

| Tool Name | Class | Required Permission | ERP-Aware |
|-----------|-------|-------------------|----------|
| `po_lookup` | `POLookupTool` | `purchase_orders.view` | Yes (ERPResolutionService first) |
| `grn_lookup` | `GRNLookupTool` | `grns.view` | Yes (ERPResolutionService first) |
| `vendor_search` | `VendorSearchTool` | `vendors.view` | No (DB only, VendorAliasMapping) |
| `invoice_details` | `InvoiceDetailsTool` | `invoices.view` | No (DB only) |
| `exception_list` | `ExceptionListTool` | `reconciliation.view` | No (DB only) |
| `reconciliation_summary` | `ReconciliationSummaryTool` | `reconciliation.view` | No (DB only) |

### Tool Registration

Tools are registered via `@register_tool` decorator in `apps/tools/registry/tools.py`.  
The `ToolRegistry` (in `apps/tools/registry/base.py`) maintains a dict of `name → tool_class`.  
`BaseAgent.run()` fetches the tool schema for the LLM's `tools=` parameter from the registry.

---

### Tool Rich Metadata (Beyond Permission)

Each tool declares semantic metadata used for agent instruction and safety:

```python
class POLookupTool(BaseTool):
    name = "po_lookup"
    required_permission = "purchase_orders.view"
    description = "..."          # What the tool does
    when_to_use = "..."          # Guidance to the LLM agent
    when_not_to_use = "..."      # Negative guidance (e.g. "don't use for receipt confirmation")
    no_result_meaning = "..."    # How to interpret empty results
    failure_handling_instruction = "..."  # What to do on tool failure
    authoritative_fields = [...]  # Fields the tool is authoritative on
    evidence_keys_produced = [...]  # Keys the tool puts in evidence dict
    parameters_schema = {...}    # JSON schema for LLM tool-calling
```

This metadata is likely injected into the system prompt to guide safe tool usage.

---

### Tool Invocation Flow

```
LLM returns tool_call { name: "po_lookup", arguments: {"po_number": "PO-001"} }
  ├── AgentGuardrailsService.check_tool_permission(tool_name, actor_context)
  │    ├── If DENIED → ToolResult(success=False, error="Permission denied")
  │    └── If GRANTED → continue
  ├── TOOL_PERMISSIONS["po_lookup"] → "purchase_orders.view"
  ├── _scoped(queryset) → adds tenant filter automatically
  ├── _resolve_via_erp(po_number, vendor_id, **kwargs) → ERPResolutionService
  │    ├── If ERP resolved → ToolResult with _erp_source, _erp_confidence metadata
  │    └── If ERP unavailable → fallback to direct DB lookup
  ├── AgentStep.objects.create(action="po_lookup", input_data, output_data, duration_ms)
  └── ToolCallLogger.log(tool_name, result, agent_run_id, trace_id)
```

**Tenant scoping**: `BaseTool._scoped(queryset)` injects `.filter(tenant=self.tenant)` on all queries.

---

## 3. Model / Provider Selection

| Component | Provider | Model | Config |
|-----------|---------|-------|--------|
| Invoice extraction | Azure OpenAI | GPT-4o (deployment name from env) | temperature=0.0, max_tokens=4096 |
| All other LLM agents | Azure OpenAI | GPT-4o | temperature=0.1, max_tokens=4096 |
| ReasoningPlanner (optional) | Azure OpenAI | GPT-4o | Inferred same config |
| Invoice category classification | Azure OpenAI | GPT-4o | Embedded in InvoicePromptComposer |

**LLM_PROVIDER** setting: `azure_openai` (default). `openai` is also supported in `LLMClient`.

**Key settings**:
```
AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT
AZURE_OPENAI_API_VERSION = "2024-02-01"
LLM_MODEL_NAME = "gpt-4o"
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 4096
LLM_REQUEST_TIMEOUT = 120  # seconds
```

---

## 4. Runtime Observability

### Langfuse Integration

Each pipeline gets a trace hierarchy:

```
Celery Task (root trace, ID = task_id.replace("-", ""))
  └── Pipeline span (e.g. "extraction_pipeline", "reconciliation_run", "agent_pipeline")
       └── Per-agent span (e.g. "INVOICE_EXTRACTION", "EXCEPTION_ANALYSIS")
            └── LLM generation (via LangChain-Langfuse callback or direct span)
            └── Tool call spans
```

**Scores recorded per run**:
- `EXTRACTION_CONFIDENCE` — float confidence of invoice extraction
- `EXTRACTION_IS_VALID` — boolean
- `EXTRACTION_IS_DUPLICATE` — boolean
- `CASE_PROCESSING_SUCCESS` — 1.0 / 0.0
- `RECON_FINAL_SUCCESS` — 1.0 / 0.0
- `RECON_ROUTED_TO_AGENTS` — 1.0 if any non-MATCHED results dispatched
- Agent pipeline scores defined in `core/evaluation_constants.py`

**Token + cost tracking**:
- `AgentRun.prompt_tokens`, `completion_tokens`, `total_tokens`
- `AgentRun.actual_cost_usd` (computed from `LLMCostRate` table)
- `AgentRun.cost_estimate`, `cost_currency`

---

## 5. Governance Gaps

| Gap | Description | Risk |
|-----|-------------|------|
| In-process prompt cache | No cross-worker cache invalidation; each worker has its own copy | Prompt updates may not propagate uniformly until workers restart |
| No prompt A/B testing | Only "production" label served from Langfuse; no experimentation framework | Low (intentional simplicity) |
| `prohibited_actions` not enforced | `AgentDefinition.prohibited_actions` JSON field exists but no enforcement code verified | Medium — governance intent not implemented |
| Tool semantic metadata injection | `when_to_use` / `when_not_to_use` fields declared but it's unclear if they're injected into system prompts | Needs verification |
| LLM timeout is per-call | 120s timeout; no overall agent pipeline timeout | A multi-tool agent run could exceed expected duration |
| No prompt rollback UI | Rollback requires re-labeling in Langfuse or DB update | Low for ops; higher risk if Langfuse is unavailable |
