# Agent Functions and LLM Usage Inventory
Generated: 2026-04-13T12:49:51.667556+00:00

## End-to-End Execution Path
1. Trigger sources:
   - `apps/reconciliation/tasks.py` dispatches `run_agent_pipeline_task`.
   - `apps/agents/views.py` can trigger `run_agent_pipeline_task`.
   - `apps/cases/orchestrators/stage_executor.py` instantiates `AgentOrchestrator`.
2. Async entrypoint: `apps/agents/tasks.py::run_agent_pipeline_task`.
3. Core execution: `apps/agents/services/orchestrator.py::AgentOrchestrator.execute`.
4. Planning:
   - `ReasoningPlanner.plan` (LLM-first) when enabled.
   - fallback `PolicyEngine.plan` (deterministic).
5. Agent run loop:
   - LLM agents execute through `BaseAgent.run` ReAct loop.
   - system agents execute through `DeterministicSystemAgent.run`.
6. Post-run:
   - recommendation resolution, auto-close/escalation policy, eval sync, trace/audit logging.

## LLM Usage Classification
### Agents using LLM
- `ExceptionAnalysisAgent`
- `InvoiceExtractionAgent`
- `InvoiceUnderstandingAgent`
- `PORetrievalAgent`
- `GRNRetrievalAgent`
- `ReviewRoutingAgent`
- `CaseSummaryAgent`
- `ReconciliationAssistAgent`
- `ComplianceAgent`
- `SupervisorAgent`

### Deterministic system agents (no LLM call)
- `SystemReviewRoutingAgent`
- `SystemCaseSummaryAgent`
- `SystemBulkExtractionIntakeAgent`
- `SystemCaseIntakeAgent`
- `SystemPostingPreparationAgent`

### Non-agent LLM components
- `LLMClient.chat` (single LLM gateway).
- `BaseAgent._call_llm_with_retry` (retry wrapper used by all BaseAgent agents).
- `ReasoningPlanner._llm_plan` (LLM-based agent plan selection).

## Agent Run Types (tabulated)

This is what can appear in `/agents/runs/` as `AgentRun.agent_type` now.

| AgentRun type | Display label | LLM used | Token/cost fields expected |
|---|---|---|---|
| INVOICE_EXTRACTION | Invoice Extraction | Yes | Yes |
| INVOICE_UNDERSTANDING | Invoice Understanding | Yes | Yes |
| PO_RETRIEVAL | PO Retrieval | Yes | Yes |
| GRN_RETRIEVAL | GRN Retrieval | Yes | Yes |
| RECONCILIATION_ASSIST | Reconciliation Assist | Yes | Yes |
| EXCEPTION_ANALYSIS | Exception Analysis | Yes | Yes |
| COMPLIANCE_AGENT | Compliance Agent | Yes | Yes |
| REVIEW_ROUTING | Review Routing | Yes | Yes |
| CASE_SUMMARY | Case Summary | Yes | Yes |
| SUPERVISOR | Supervisor | Yes | Yes |
| SYSTEM_REVIEW_ROUTING | System Review Routing | No (deterministic) | No (tokens 0) |
| SYSTEM_CASE_SUMMARY | System Case Summary | No (deterministic) | No (tokens 0) |
| SYSTEM_BULK_EXTRACTION_INTAKE | System Bulk Extraction Intake | No (deterministic) | No (tokens 0) |
| SYSTEM_CASE_INTAKE | System Case Intake | No (deterministic) | No (tokens 0) |
| SYSTEM_POSTING_PREPARATION | System Posting Preparation | No (deterministic) | No (tokens 0) |
| PROCUREMENT_RECOMMENDATION | Procurement Recommendation | Yes (HVAC recommendation/explain path) | Yes |
| PROCUREMENT_BENCHMARK | Procurement Benchmark | Depends (AI benchmark path) | Yes when AI is used |
| PROCUREMENT_VALIDATION | Procurement Validation | Yes | Yes |
| PROCUREMENT_COMPLIANCE | Procurement Compliance | Yes | Yes |
| PROCUREMENT_MARKET_INTELLIGENCE | Procurement Market Intelligence | Yes (Perplexity or fallback web+LLM parse) | Yes when usage is returned |

### Procurement runtime labels -> AgentRun type

The procurement orchestrator executes runtime labels and maps them into the enum values above.

| Runtime `agent_type` label | Saved `AgentRun.agent_type` |
|---|---|
| recommendation | PROCUREMENT_RECOMMENDATION |
| benchmark_item_* | PROCUREMENT_BENCHMARK |
| validation_augmentation | PROCUREMENT_VALIDATION |
| compliance | PROCUREMENT_COMPLIANCE |
| market_intelligence | PROCUREMENT_MARKET_INTELLIGENCE |

## Complete Function Inventory (apps/agents/services + apps/agents/tasks.py)

### `apps/agents/services/agent_classes.py`
Top-level functions:
- `_mode_context`
- `_parse_agent_json`
- `_to_agent_output`
- `_get_system_agent_classes`
Classes and methods:
- `ExceptionAnalysisAgent` bases: BaseAgent
  - `ExceptionAnalysisAgent.system_prompt`
  - `ExceptionAnalysisAgent.build_user_message`
  - `ExceptionAnalysisAgent.allowed_tools`
  - `ExceptionAnalysisAgent.interpret_response`
  - `ExceptionAnalysisAgent.run`
  - `ExceptionAnalysisAgent._generate_reviewer_summary`
- `InvoiceExtractionAgent` bases: BaseAgent
  - `InvoiceExtractionAgent.__init__`
  - `InvoiceExtractionAgent.system_prompt`
  - `InvoiceExtractionAgent.build_user_message`
  - `InvoiceExtractionAgent.allowed_tools`
  - `InvoiceExtractionAgent._init_messages`
  - `InvoiceExtractionAgent.interpret_response`
  - `InvoiceExtractionAgent.run`
- `InvoiceUnderstandingAgent` bases: BaseAgent
  - `InvoiceUnderstandingAgent.system_prompt`
  - `InvoiceUnderstandingAgent.build_user_message`
  - `InvoiceUnderstandingAgent.allowed_tools`
  - `InvoiceUnderstandingAgent.interpret_response`
- `PORetrievalAgent` bases: BaseAgent
  - `PORetrievalAgent.system_prompt`
  - `PORetrievalAgent.build_user_message`
  - `PORetrievalAgent.allowed_tools`
  - `PORetrievalAgent.interpret_response`
- `GRNRetrievalAgent` bases: BaseAgent
  - `GRNRetrievalAgent.system_prompt`
  - `GRNRetrievalAgent.build_user_message`
  - `GRNRetrievalAgent.allowed_tools`
  - `GRNRetrievalAgent.interpret_response`
- `ReviewRoutingAgent` bases: BaseAgent
  - `ReviewRoutingAgent.system_prompt`
  - `ReviewRoutingAgent.build_user_message`
  - `ReviewRoutingAgent.allowed_tools`
  - `ReviewRoutingAgent.interpret_response`
- `CaseSummaryAgent` bases: BaseAgent
  - `CaseSummaryAgent.system_prompt`
  - `CaseSummaryAgent.build_user_message`
  - `CaseSummaryAgent.allowed_tools`
  - `CaseSummaryAgent.interpret_response`
- `ReconciliationAssistAgent` bases: BaseAgent
  - `ReconciliationAssistAgent.system_prompt`
  - `ReconciliationAssistAgent.build_user_message`
  - `ReconciliationAssistAgent.allowed_tools`
  - `ReconciliationAssistAgent.interpret_response`
- `ComplianceAgent` bases: BaseAgent
  - `ComplianceAgent.system_prompt`
  - `ComplianceAgent.build_user_message`
  - `ComplianceAgent.allowed_tools`
  - `ComplianceAgent.interpret_response`

### `apps/agents/services/agent_memory.py`
Top-level functions:
- _(none)_
Classes and methods:
- `AgentMemory` bases: (no explicit base)
  - `AgentMemory.record_agent_output`

### `apps/agents/services/agent_output_schema.py`
Top-level functions:
- _(none)_
Classes and methods:
- `DecisionSchema` bases: BaseModel
  - _(no methods)_
- `AgentOutputSchema` bases: BaseModel
  - `AgentOutputSchema.validate_rec_type`
  - `AgentOutputSchema.coerce_confidence`
  - `AgentOutputSchema.clamp_confidence`

### `apps/agents/services/agent_trace_service.py`
Top-level functions:
- _(none)_
Classes and methods:
- `AgentTraceService` bases: (no explicit base)
  - `AgentTraceService.start_agent_run`
  - `AgentTraceService.log_agent_step`
  - `AgentTraceService.log_tool_call`
  - `AgentTraceService.log_agent_decision`
  - `AgentTraceService.finish_agent_run`
  - `AgentTraceService.get_trace_for_result`
  - `AgentTraceService.get_trace_for_invoice`

### `apps/agents/services/base_agent.py`
Top-level functions:
- _(none)_
Classes and methods:
- `AgentContext` bases: (no explicit base)
  - _(no methods)_
- `AgentOutput` bases: (no explicit base)
  - _(no methods)_
- `BaseAgent` bases: ABC
  - `BaseAgent.__init__`
  - `BaseAgent.system_prompt`
  - `BaseAgent.build_user_message`
  - `BaseAgent.allowed_tools`
  - `BaseAgent.interpret_response`
  - `BaseAgent.run`
  - `BaseAgent._fire_progress`
  - `BaseAgent._summarize_tool_output`
  - `BaseAgent._init_messages`
  - `BaseAgent._execute_tool`
  - `BaseAgent._finalise_run`
  - `BaseAgent._calculate_actual_cost`
  - `BaseAgent._sanitise_text`
  - `BaseAgent._enforce_evidence_keys`
  - `BaseAgent._guard_reasoning_quality`
  - `BaseAgent._truncate_exceptions`
  - `BaseAgent._save_message`
  - `BaseAgent._serialise_context`
  - `BaseAgent._elapsed_seconds`
  - `BaseAgent._call_llm_with_retry`
  - `BaseAgent._apply_tool_failure_guards`
  - `BaseAgent._compute_composite_confidence`
  - `BaseAgent._resolve_actor`

### `apps/agents/services/decision_log_service.py`
Top-level functions:
- _(none)_
Classes and methods:
- `DecisionLogService` bases: (no explicit base)
  - `DecisionLogService.log_decision`
  - `DecisionLogService.log_recommendation`
  - `DecisionLogService.get_decisions_for_result`
  - `DecisionLogService.get_recommendations_for_result`

### `apps/agents/services/deterministic_resolver.py`
Top-level functions:
- `_build_evidence`
- `_build_case_summary`
Classes and methods:
- `DeterministicResolution` bases: (no explicit base)
  - _(no methods)_
- `DeterministicResolver` bases: (no explicit base)
  - `DeterministicResolver.resolve`
  - `DeterministicResolver._apply_rules`

### `apps/agents/services/deterministic_system_agent.py`
Top-level functions:
- _(none)_
Classes and methods:
- `DeterministicSystemAgent` bases: BaseAgent
  - `DeterministicSystemAgent.__init__`
  - `DeterministicSystemAgent.system_prompt`
  - `DeterministicSystemAgent.build_user_message`
  - `DeterministicSystemAgent.allowed_tools`
  - `DeterministicSystemAgent.interpret_response`
  - `DeterministicSystemAgent.execute_deterministic`
  - `DeterministicSystemAgent.run`
  - `DeterministicSystemAgent._build_input_payload`
  - `DeterministicSystemAgent._finalise_deterministic_run`
  - `DeterministicSystemAgent._emit_audit_event`

### `apps/agents/services/eval_adapter.py`
Top-level functions:
- `_extract_po_found`
- `_extract_grn_found`
- `_extract_recommendation`
- `_extract_risk_level`
- `_extract_match_assessment`
- `_extract_review_queue`
- `_extract_posting_status`
- `_extract_vendor_mapped`
- `_extract_match_status`
- `_extract_vendor_verified`
- `_extract_lines_checked`
- `_extract_recovery_actions`
Classes and methods:
- `AgentEvalAdapter` bases: (no explicit base)
  - `AgentEvalAdapter.sync_for_agent_run`
  - `AgentEvalAdapter._sync_for_agent_run_inner`
  - `AgentEvalAdapter.sync_for_orchestration`
  - `AgentEvalAdapter._sync_for_orchestration_inner`

### `apps/agents/services/guardrails_service.py`
Top-level functions:
- _(none)_
Classes and methods:
- `AgentGuardrailsService` bases: (no explicit base)
  - `AgentGuardrailsService.get_system_agent_user`
  - `AgentGuardrailsService._assign_system_agent_role`
  - `AgentGuardrailsService.resolve_actor`
  - `AgentGuardrailsService.authorize_orchestration`
  - `AgentGuardrailsService.authorize_agent`
  - `AgentGuardrailsService.authorize_tool`
  - `AgentGuardrailsService.authorize_recommendation`
  - `AgentGuardrailsService.authorize_action`
  - `AgentGuardrailsService.ensure_permission`
  - `AgentGuardrailsService._lf_trace_id_for_run`
  - `AgentGuardrailsService.build_rbac_snapshot`
  - `AgentGuardrailsService.build_trace_context_for_agent`
  - `AgentGuardrailsService.log_guardrail_decision`
  - `AgentGuardrailsService.get_actor_scope`
  - `AgentGuardrailsService.get_result_scope`
  - `AgentGuardrailsService._scope_value_allowed`
  - `AgentGuardrailsService.authorize_data_scope`

### `apps/agents/services/llm_client.py`
Top-level functions:
- _(none)_
Classes and methods:
- `LLMMessage` bases: (no explicit base)
  - _(no methods)_
- `ToolSpec` bases: (no explicit base)
  - _(no methods)_
- `LLMToolCall` bases: (no explicit base)
  - _(no methods)_
- `LLMResponse` bases: (no explicit base)
  - _(no methods)_
- `LLMClient` bases: (no explicit base)
  - `LLMClient.__init__`
  - `LLMClient.chat`
  - `LLMClient._build_messages`
  - `LLMClient._tool_to_dict`
  - `LLMClient._parse_response`

### `apps/agents/services/orchestrator.py`
Top-level functions:
- _(none)_
Classes and methods:
- `OrchestrationResult` bases: (no explicit base)
  - _(no methods)_
- `_AgentRunOutputProxy` bases: (no explicit base)
  - `_AgentRunOutputProxy.__init__`
- `AgentOrchestrator` bases: (no explicit base)
  - `AgentOrchestrator.__init__`
  - `AgentOrchestrator.execute`
  - `AgentOrchestrator._reflect`
  - `AgentOrchestrator._resolve_final_recommendation`
  - `AgentOrchestrator._apply_post_policies`
  - `AgentOrchestrator._apply_deterministic_resolution`
  - `AgentOrchestrator._apply_agent_findings`
  - `AgentOrchestrator._apply_po_finding`

### `apps/agents/services/policy_engine.py`
Top-level functions:
- _(none)_
Classes and methods:
- `AgentPlan` bases: (no explicit base)
  - _(no methods)_
- `PolicyEngine` bases: (no explicit base)
  - `PolicyEngine.plan`
  - `PolicyEngine._within_auto_close_band`
  - `PolicyEngine.should_auto_close`
  - `PolicyEngine.should_escalate`

### `apps/agents/services/reasoning_planner.py`
Top-level functions:
- _(none)_
Classes and methods:
- `ReasoningPlanner` bases: (no explicit base)
  - `ReasoningPlanner.__init__`
  - `ReasoningPlanner.plan`
  - `ReasoningPlanner.should_auto_close`
  - `ReasoningPlanner.should_escalate`
  - `ReasoningPlanner._llm_plan`

### `apps/agents/services/recommendation_service.py`
Top-level functions:
- _(none)_
Classes and methods:
- `RecommendationService` bases: (no explicit base)
  - `RecommendationService.create_recommendation`
  - `RecommendationService.get_recommendations_for_invoice`
  - `RecommendationService.get_recommendations_for_result`
  - `RecommendationService.mark_recommendation_accepted`
  - `RecommendationService.mark_recommendation_overridden`

### `apps/agents/services/supervisor_agent.py`
Top-level functions:
- `_ensure_skills_loaded`
Classes and methods:
- `SupervisorAgent` bases: BaseAgent
  - `SupervisorAgent.__init__`
  - `SupervisorAgent.system_prompt`
  - `SupervisorAgent.allowed_tools`
  - `SupervisorAgent.build_user_message`
  - `SupervisorAgent.interpret_response`
  - `SupervisorAgent.run`

### `apps/agents/services/supervisor_context_builder.py`
Top-level functions:
- `build_supervisor_context`
Classes and methods:
- _(none)_

### `apps/agents/services/supervisor_output_interpreter.py`
Top-level functions:
- `parse_supervisor_response`
- `interpret_supervisor_output`
- `extract_recommendation_from_tools`
Classes and methods:
- _(none)_

### `apps/agents/services/supervisor_prompt_builder.py`
Top-level functions:
- `build_supervisor_prompt`
Classes and methods:
- _(none)_

### `apps/agents/services/system_agent_classes.py`
Top-level functions:
- _(none)_
Classes and methods:
- `SystemReviewRoutingAgent` bases: DeterministicSystemAgent
  - `SystemReviewRoutingAgent.execute_deterministic`
  - `SystemReviewRoutingAgent._fetch_exceptions`
- `SystemCaseSummaryAgent` bases: DeterministicSystemAgent
  - `SystemCaseSummaryAgent.execute_deterministic`
  - `SystemCaseSummaryAgent._fetch_exceptions`
- `SystemBulkExtractionIntakeAgent` bases: DeterministicSystemAgent
  - `SystemBulkExtractionIntakeAgent.execute_deterministic`
- `SystemCaseIntakeAgent` bases: DeterministicSystemAgent
  - `SystemCaseIntakeAgent.execute_deterministic`
- `SystemPostingPreparationAgent` bases: DeterministicSystemAgent
  - `SystemPostingPreparationAgent.execute_deterministic`

### `apps/agents/tasks.py`
Top-level functions:
- `run_agent_pipeline_task`
- `_record_supervisor_signals`
- `run_supervisor_pipeline_task`
Classes and methods:
- _(none)_
