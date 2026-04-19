---
mode: agent
description: "Add a new agent tool with BaseTool, permission declaration, scoped access, and registry wiring"
---

# Add a New Tool

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/AGENT_ARCHITECTURE.md` -- tool registry, `@register_tool`, BaseTool contract, tool-calling format
- `docs/current_system_review/04_Prompt_Tool_and_Model_Governance.md` -- tool governance, tool definition catalog
- `docs/current_system_review/07_RBAC_and_Security_Posture.md` -- per-tool RBAC enforcement via `AgentGuardrailsService.authorize_tool()`
- `docs/current_system_review/17_Supervisor_Agent_Architecture.md` -- supervisor tools, PluginToolRouter, ERP-routable tools

### Source Files
- `apps/tools/registry/base.py` -- `BaseTool`, `ToolRegistry`, `@register_tool` decorator (study the full contract)
- `apps/tools/registry/tools.py` -- 6 base tools: `POLookupTool`, `GRNLookupTool`, `VendorSearchTool`, `InvoiceDetailsTool`, `ExceptionListTool`, `ReconciliationSummaryTool`
- `apps/tools/registry/supervisor_tools.py` -- 24 supervisor-specific tools (study `SubmitRecommendationTool`, `RunReconciliationTool`)
- `apps/tools/registry/ap_insights_tools.py` -- 12 analytics tools (study `MatchRateBreakdownTool`)
- `apps/agents/services/guardrails_service.py` -- `authorize_tool()` method and `AGENT_PERMISSIONS` dict
- `apps/agents/services/base_agent.py` -- `_execute_tool()` method (how tools are called within ReAct loop, Langfuse span threading)

### Comprehension Check
1. `BaseTool` declares `name`, `description`, `parameters` (JSON Schema), `required_permission`
2. `@register_tool` registers the class in the global `ToolRegistry`
3. `execute()` receives `arguments: dict` and returns `dict` (JSON-serializable)
4. `self._scoped(Model, tenant)` provides tenant-filtered querysets inside tools
5. `AgentGuardrailsService.authorize_tool(user, tool_name)` checks `required_permission` before execution
6. Tool output is serialized into the OpenAI `tool` message with `tool_call_id` and `name`

---

## Inputs

- **Tool name**: snake_case identifier (e.g. `vendor_balance_lookup`)
- **Purpose**: one-sentence description of what this tool does
- **Required permission**: `{module}.{action}` code (e.g. `vendors.view`)
- **Parameters**: JSON Schema for arguments the LLM must provide
- **Which agents use it**: list of agent types whose `allowed_tools` should include this tool

---

## Steps

### 1. Create Tool Class

In `apps/tools/registry/tools.py` (for base tools) or `apps/tools/registry/supervisor_tools.py` (for supervisor tools):

```python
from apps.tools.registry.base import BaseTool, register_tool

@register_tool
class MyNewTool(BaseTool):
    name = "my_new_tool"
    description = "One-sentence ASCII description of what this tool does."
    required_permission = "module.action"
    parameters = {
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string",
                "description": "What this parameter is for."
            }
        },
        "required": ["param_name"]
    }

    def execute(self, arguments: dict) -> dict:
        tenant = self.context.get("tenant")
        param = arguments.get("param_name", "")

        # Use self._scoped() for tenant-safe queries
        qs = self._scoped(MyModel, tenant).filter(code=param)
        obj = qs.first()

        if not obj:
            return {"status": "not_found", "message": f"No record found for {param}"}

        return {
            "status": "found",
            "data": {
                "id": obj.pk,
                "name": obj.name,
                # ... relevant fields
            }
        }
```

### 2. Create ToolDefinition Record

Add a `ToolDefinition` record via migration or seed command:
- `name`: matches the class `name` attribute exactly
- `description`: same as class `description`
- `parameters_schema`: same JSON Schema as class `parameters`
- `is_active`: True

### 3. Add to Agent Allowed Tools

Update the relevant `AgentDefinition.config_json["allowed_tools"]` list to include the new tool name. This is in the `seed_agent_contracts` management command or directly in the DB.

### 4. Wire RBAC Permission

If using a new permission code:
1. Add to `seed_rbac.py` PERMISSIONS list
2. Map to appropriate roles in `ROLE_MATRIX`
3. Map to `SYSTEM_AGENT` role (so autonomous runs can use the tool)

### 5. Langfuse Tracing

Tool calls are automatically traced by `BaseAgent._execute_tool()`. The tool span includes:
- `tool_name`, `arguments`, `source_used` in metadata
- `tool_call_success` observation score

No additional Langfuse code is needed inside the tool unless you want sub-spans for complex operations (e.g. ERP resolution):

```python
# Only for complex tools that call external services
from apps.core.langfuse_client import start_span, end_span

lf_span = None
try:
    lf_parent = self.context.get("lf_parent_span")
    lf_span = start_span(lf_parent, name="erp_lookup", metadata={...}) if lf_parent else None
except Exception:
    lf_span = None
# ... do work ...
try:
    if lf_span:
        end_span(lf_span, output={...})
except Exception:
    pass
```

### 6. Write Tests

Minimum test cases:
- Happy path: valid arguments return expected output
- Not found: missing record returns `{"status": "not_found"}`
- Tenant isolation: tool cannot access data from another tenant
- Permission check: `authorize_tool()` denies for user without `required_permission`
- Invalid arguments: missing required params handled gracefully (no 500)

---

## Constraints

- ASCII only in tool name, description, all output strings
- Tool `execute()` must return a JSON-serializable dict, never raise unhandled exceptions
- Tool output should be concise -- LLM context window is limited
- Never put business logic in tools -- delegate to service classes
- Never access `request` inside a tool -- use `self.context` dict
