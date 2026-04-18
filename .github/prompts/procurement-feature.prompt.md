---
mode: agent
description: "Add or modify a procurement intelligence feature (request lifecycle, recommendation, validation, market intelligence, agentic bridge)"
---

# Procurement Feature

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/PROCUREMENT.md` -- full procurement platform reference (17 sections: models, enums, service layer, agent system, API, templates, Celery tasks, governance, observability, RBAC, status transitions, flow walkthroughs)
- `docs/PROCUREMENT_REQUEST_WALKTHROUGH.txt` -- end-to-end scenario walkthrough from form fill to analysis results
- `docs/current_system_review/05_Features_and_Workflows.md` -- procurement workflow overview section

### Source Files (read in this order)
1. `apps/procurement/models.py` -- 15+ models: ProcurementRequest, ProcurementRequestAttribute, SupplierQuotation, QuotationLineItem, AnalysisRun, RecommendationResult, BenchmarkResult, BenchmarkResultLine, ComplianceResult, ValidationRuleSet, ValidationRule, ValidationResult, ValidationResultItem, MarketIntelligenceSuggestion, ExternalSourceRegistry, ProcurementAgentExecutionRecord
2. `apps/procurement/services/request_service.py` -- ProcurementRequestService (create, status transitions, mark_ready) + AttributeService (bulk_set, get_attributes_dict)
3. `apps/procurement/services/analysis_run_service.py` -- AnalysisRunService (create_run, start_run, complete_run, fail_run lifecycle)
4. `apps/procurement/services/recommendation_service.py` -- RecommendationService.run_recommendation() -- deterministic rules first, AI fallback, compliance check, result persistence
5. `apps/procurement/services/validation/orchestrator_service.py` -- ValidationOrchestratorService.run_validation() -- 6 deterministic validators + optional agent augmentation
6. `apps/procurement/services/validation/rule_resolver_service.py` -- domain/schema-scoped rule resolution with specificity ordering
7. `apps/procurement/services/market_intelligence_service.py` -- MarketIntelligenceService (Perplexity sonar + OpenAI dual-path, ExternalSourceRegistry, citation resolution)
8. `apps/procurement/services/compliance_service.py` -- ComplianceService (rule-based compliance checking)
9. `apps/procurement/runtime/procurement_agent_orchestrator.py` -- ProcurementAgentOrchestrator bridge (context, memory, execution record, audit events, Langfuse spans)
10. `apps/procurement/tasks.py` -- run_analysis_task, run_validation_task, run_quotation_prefill_task
11. `apps/procurement/agents/hvac_recommendation_agent.py` -- primary recommendation agent (two entry points: recommend + explain)
12. `apps/core/enums.py` -- ProcurementRequestStatus, ProcurementRequestType, AnalysisRunType, AnalysisRunStatus, ValidationType, ValidationOverallStatus, ComplianceStatus, VarianceStatus, BenchmarkRiskLevel, AttributeDataType + 6 validation enums

### Comprehension Check
1. Request lifecycle: DRAFT -> READY -> PROCESSING -> COMPLETED | REVIEW_REQUIRED | FAILED
2. Analysis runs are independent from the request -- one request can have multiple runs of different types
3. Recommendation flow: gather attributes -> apply deterministic rules -> if not confident, invoke AI via ProcurementAgentOrchestrator -> compliance check -> persist RecommendationResult + ComplianceResult
4. Validation runs 6 deterministic validators (attribute completeness, document completeness, scope coverage, ambiguity check, commercial completeness, compliance readiness) plus optional agent augmentation when >= 3 ambiguous items
5. The Phase 1 agentic bridge (ProcurementAgentOrchestrator) wraps existing agent calls without rewriting them -- creates ProcurementAgentExecutionRecord, fires audit events, opens Langfuse spans
6. Market intelligence has dual providers: Perplexity sonar (live web search with domain filtering from ExternalSourceRegistry) and Azure OpenAI (knowledge-base fallback)
7. All procurement models are tenant-scoped via CompanyProfile FK

---

## When Adding a New Analysis Type

1. Add enum value to `AnalysisRunType` in `apps/core/enums.py`
2. Create result model in `apps/procurement/models.py` inheriting from `TimestampMixin` (lightweight) with a OneToOne FK to `AnalysisRun`
3. Create service class in `apps/procurement/services/` following the pattern of `RecommendationService` or `BenchmarkService`:
   - Decorate entry point with `@observed_service`
   - Call `AnalysisRunService.start_run()` at the top
   - Run deterministic logic first, invoke AI only if rules are insufficient
   - Persist results in a transaction
   - Call `AnalysisRunService.complete_run()` or `fail_run()` at the end
   - Update request status accordingly
4. Wire into `run_analysis_task` dispatch in `apps/procurement/tasks.py` (add a branch for the new `run_type`)
5. Add serializer in `apps/procurement/serializers.py`
6. Add nested action to `ProcurementRequestViewSet` if the result needs its own API endpoint
7. Add result display section to `templates/procurement/request_workspace.html` and `templates/procurement/run_detail.html`
8. Create migration: `python manage.py makemigrations procurement`

## When Adding a New Validation Dimension

1. Add enum value to `ValidationType` in `apps/core/enums.py`
2. Create a new service file in `apps/procurement/services/validation/` following the pattern of existing validators:
   - Static `validate(request, rules) -> list[dict]` method
   - Each finding dict has keys: `item_code`, `item_label`, `category`, `status`, `severity`, `source_type`, `source_reference`, `remarks`
   - Only evaluate rules matching the new `rule_type`
3. Wire the new validator into `ValidationOrchestratorService.run_validation()` -- call it alongside the existing 6 validators
4. Add corresponding `ValidationRuleType` enum value in `apps/core/enums.py` if needed
5. Seed domain-specific `ValidationRuleSet` + `ValidationRule` records via management command or fixture
6. Update `templates/procurement/partials/validation_summary.html` if the new dimension needs special rendering

## When Adding a New Procurement Agent

1. Create agent class in `apps/procurement/agents/` following these conventions:
   - Single entry point method (e.g. `execute()`, `extract()`, `run()`)
   - Use `LLMClient` from `apps.agents.services.llm_client` for LLM calls
   - Catch all exceptions and return safe fallback dict -- never raise to caller
   - Respect the 60K char OCR text limit for document-based agents
   - Strip markdown fences from LLM JSON responses
2. Wire the agent call through `ProcurementAgentOrchestrator.run()` so it gets:
   - `ProcurementAgentExecutionRecord` DB row
   - `PROCUREMENT_AGENT_RUN_STARTED/COMPLETED/FAILED` audit events
   - Langfuse span attached to the active trace
   - RBAC snapshot in the execution record
3. Register the agent_type string in the orchestrator call site (e.g. in the service method that invokes it)
4. If the agent is invoked per-line-item, use unique `agent_type` strings (e.g. `"benchmark_item_{pk}"`) for per-invocation traceability

## When Adding Market Intelligence Sources

1. Add `ExternalSourceRegistry` records via admin, seed command, or migration:
   - Set `hvac_system_type` to match `_SYSTEM_CODE_TO_DB_NAME` mapping in `market_intelligence_service.py`
   - Set `source_type` to appropriate `ExternalSourceClass` enum value
   - Set `allowed_for_discovery=True` for AI agent search, `allowed_for_compliance=True` for regulatory citation
   - Set `priority` (lower = higher priority -- OEM_OFFICIAL sources should be lowest number)
2. Do NOT include URL paths in `source_url` -- Perplexity discovers real product pages via live search. Only set the root domain URL.
3. If adding a new system type, add the mapping to `_SYSTEM_CODE_TO_DB_NAME` in `market_intelligence_service.py`

## When Modifying the Request Lifecycle

1. Check `ProcurementRequestService.update_status()` for existing transition logic
2. The request status is updated by services (recommendation, benchmark, validation) based on their outcomes -- do not update status directly in views
3. Mark ready validation: all `is_required=True` attributes must have a non-empty value
4. Status transitions are logged as `PROCUREMENT_REQUEST_STATUS_CHANGED` audit events with `status_before`/`status_after`

## Coding Rules

- **Deterministic first**: Always run rule-based logic before invoking LLM agents. Only call AI when rules return `confident=False` or cannot produce an answer.
- **Stateless services**: Use static/class methods. Accept model instances or IDs as arguments.
- **ASCII only**: Use `_sanitise_text()` on any LLM-generated content before `.save()`.
- **Tenant scoping**: All queries on procurement models must filter by tenant via `TenantQuerysetMixin` (views) or `scoped_queryset()` (services).
- **Audit everything**: Log via `AuditService.log_event()` for business events. Use `@observed_service` on service entry points.
- **Fail-silent agents**: Agent errors should not propagate to the caller. Return a safe fallback and let the orchestrator record the failure.
