# E2E Test Compliance Checklist
# 3-Way PO Reconciliation Platform

## Legend
- [x] Tested and expected to pass
- [~] Tested but may skip (optional dependency / not seeded)
- [!] Known failure / not-yet-implemented  
- [ ] Not tested

---

## Module 01 -- Health & Auth
- [x] GET /health/ returns 200
- [x] GET /health/live/ returns 200
- [x] GET /health/ready/ returns 200
- [x] Login page renders
- [x] Unauthenticated users are redirected
- [x] Admin user login succeeds
- [x] AP user login succeeds
- [x] Wrong credentials rejected
- [x] Logout works
- [x] Admin panel /admin/ requires superuser
- [x] Governance API rejects anonymous

## Module 02 -- Dashboard
- [x] Dashboard home loads, no 500
- [x] All 17 nav URLs no 500
- [x] 5 analytics API endpoints return 200

## Module 03 -- LLM Agents (9 agents)
- [x] All 9 AgentType enums defined
- [x] All 9 agent classes in registry
- [x] SupervisorAgent present
- [~] AgentDefinition DB records >= 5 (requires seed_agent_contracts)
- [x] AgentGuardrailsService importable
- [x] AgentOrchestrator importable
- [x] PolicyEngine importable
- [x] ReasoningPlanner importable

## Module 04 -- System Agents (5 deterministic)
- [x] SystemReviewRoutingAgent importable
- [x] SystemCaseSummaryAgent importable
- [x] SystemBulkExtractionIntakeAgent importable
- [x] SystemCaseIntakeAgent importable
- [x] SystemPostingPreparationAgent importable
- [x] DeterministicSystemAgent base class importable
- [x] All 5 in registry
- [x] AgentTraceService importable
- [x] AgentRun / AgentOrchestrationRun / DecisionLog models queryable
- [x] AgentOutputSchema confidence clamping

## Module 05 -- Extraction Pipeline
- [x] DocumentUpload / Invoice / PurchaseOrder / GoodsReceiptNote models
- [x] InvoiceLineItem / PurchaseOrderLineItem / GRNLineItem models
- [x] ExtractionResult / ExtractionApproval / ExtractionFieldCorrection models
- [x] ExtractionApprovalService: approve / reject / try_auto_approve / analytics
- [x] process_invoice_upload_task / run_bulk_job_task importable
- [x] /extraction/ / /extraction/control-center/ / /invoices/ no 500
- [x] ExtractionConfig model

## Module 06 -- Reconciliation
- [x] 6 reconciliation models (ReconciliationRun, Result, ResultLine, Exception, Config, Policy)
- [x] ToleranceEngine: exact match passes, 20% variance fails
- [x] LineMatchService v2: normalize_text, compute_token_similarity
- [x] ReconciliationModeResolver importable
- [x] TwoWayMatchService / ThreeWayMatchService importable
- [x] ReconciliationRunnerService run()/execute() importable
- [x] run_reconciliation_task importable
- [x] Reconciliation UI no 500
- [x] ReconciliationEvalAdapter importable

## Module 07 -- Posting + ERP
- [x] InvoicePosting / PostingRun / ERPVendorReference etc. models
- [x] ERPTaxCodeReference / ERPCostCenterReference / ERPPOReference models
- [x] PostingPipeline (9 stages) importable
- [x] PostingMappingEngine importable
- [x] PostingOrchestrator importable
- [x] PostingActionService importable
- [x] PostingStatus enum all 6 values
- [x] ERPConnection / ERPReferenceCacheRecord models
- [x] ConnectorFactory importable
- [x] BaseERPConnector importable
- [x] ERPResolutionService importable
- [x] ERP Langfuse helpers importable
- [x] ConnectorFactory returns None gracefully with no DB record
- [!] Real ERP submission -- MOCK ONLY (Phase 2 not implemented)
- [~] CustomERPConnector / DynamicsConnector (present in most installs)
- [x] /posting/ / /erp-connections/ / /erp-connections/reference-data/ no 500

## Module 08 -- Procurement + Benchmarking
- [x] ProcurementRequest / SupplierQuotation models
- [~] RFQDocument model
- [x] QuotationDocumentPrefillService importable
- [x] AttributeMappingService importable (synonym map)
- [~] PrefillReviewService importable
- [x] QuotationExtractionAgent importable + extract() method
- [x] /procurement/ pages no 500
- [x] /benchmarking/ accessible
- [~] BenchmarkingJob model

## Module 09 -- Email Integration
- [x] MailboxConfig / EmailThread / EmailMessage / EmailAttachment models
- [x] EmailParticipant / EmailRoutingDecision / EmailAction / EmailTemplate models
- [~] Mailboxes seeded >= 2 (requires seed_email_data)
- [~] Templates seeded >= 6 (requires seed_email_data)
- [~] Threads seeded >= 3
- [~] Messages seeded >= 7
- [~] AP_VENDOR_CLARIFICATION template present
- [~] PROCUREMENT_SUPPLIER_CLARIFICATION template present
- [x] AttachEmailToCaseTool / ExtractCaseApprovalFromEmailTool / SendVendorClarificationEmailTool
- [x] AttachEmailToProcurementRequestTool / AttachEmailToSupplierQuotationTool
- [x] ExtractSupplierResponseFieldsTool / SendSupplierClarificationEmailTool
- [x] All 7 tools have execute() method
- [~] EmailRoutingService importable
- [x] Email enums importable
- [x] /email/ pages no 500
- [x] APCase primary_email_thread field
- [x] APCase last_email_message field
- [x] DocumentUpload source_message field
- [!] Live email sync (Microsoft 365 / Gmail OAuth) -- not configured in test env

## Module 10 -- Cases & Reviews
- [x] APCase / ReviewAssignment / ReviewDecision / ReviewComment models
- [~] ManualReviewAction model
- [x] CaseStatus / ReviewStatus enums
- [x] ReviewWorkflowService importable + create_assignment, finalize methods
- [~] CaseOrchestrator importable
- [~] CaseCreationService importable
- [~] process_case_task importable
- [x] CaseTimelineService importable + build_timeline/get_timeline method
- [x] /cases/ / /reviews/ no 500
- [x] /api/v1/cases/ returns 200
- [x] /copilot/ accessible
- [x] /reports/ accessible

## Module 11 -- RBAC & Audit
- [x] Role / Permission / RolePermission / UserRole / UserPermissionOverride models
- [~] Roles seeded >= 5 (requires seed_rbac)
- [~] Permissions seeded >= 20 (requires seed_rbac)
- [x] HasPermissionCode / HasAnyPermission / HasRole importable
- [x] PermissionRequiredMixin importable
- [x] permission_required_code importable
- [x] AuditEvent / ProcessingLog / DecisionLog models
- [x] AuditEventType >= 20 values
- [x] AuditService: fetch_case_history, fetch_access_history, fetch_permission_denials
- [x] All 9 governance API endpoints return 200
- [x] Governance UI no 500
- [x] TraceContext importable + creates valid context
- [x] MetricsService importable
- [x] observed_service / observed_action / observed_task decorators
- [x] JSONLogFormatter importable
- [x] TenantMiddleware / RequestTraceMiddleware / RBACMiddleware importable
- [x] TenantQuerysetMixin / scoped_queryset / require_tenant importable

## Module 12 -- Eval & Learning
- [x] EvalRun / EvalMetric / EvalFieldOutcome / LearningSignal / LearningAction models
- [x] EvalRunService: create_or_update method
- [x] EvalMetricService / EvalFieldOutcomeService importable
- [x] LearningSignalService: record method
- [x] LearningActionService importable
- [x] LearningEngine: run()/execute()/apply_rules() method
- [x] ExtractionEvalAdapter importable
- [x] ReconciliationEvalAdapter importable
- [x] /eval/ pages no 500
- [x] Langfuse client importable (start_trace, end_span, score_trace)
- [x] start_trace returns None silently with no config key
- [x] score_trace never raises with span=None

## Module 13 -- Vendors & Reports
- [x] Vendor / VendorAlias models
- [x] VendorAlias has FK to Vendor
- [x] /vendors/ accessible for admin (no 500)
- [x] /vendors/ accessible for AP user (200/302/403)
- [x] /api/v1/vendors/ returns 200
- [x] /purchase-orders/ no 500
- [x] /grns/ no 500
- [x] /reports/ accessible
- [x] /api/v1/reports/ accessible
- [~] IntegrationConfig model importable
- [x] CompanyProfile model
- [x] BaseModel / SoftDeleteMixin importable
- [x] apps.core.enums importable
- [x] apps.core.utils importable
- [~] PromptRegistry importable
- [x] All required /api/v1/ roots no 500

---

## Known Not-Yet-Implemented (flagged in COMPLIANCE_REPORT.md)

| Feature | Status |
|---------|--------|
| Real ERP invoice submission | Phase 1 mock only |
| Auto ERP reference re-import (Celery Beat) | Not configured |
| Feedback learning auto-apply | LearningEngine proposes only |
| Docker / docker-compose | Not present |
| CI/CD (GitHub Actions) | Not configured |
| Email notifications | No notification system |
| Multi-page PDF extraction | Single-page only |
| LLM-assisted item fuzzy matching in PostingMappingEngine | Not implemented |
| Full CSV/Excel report export | Case console CSV only |

---

## Seed Commands Required for Full Green

```bash
# Run all seeds in order
python manage.py seed_all

# Email-specific seed
python manage.py seed_email_data

# Verify agent contracts
python manage.py seed_agent_contracts
```

---

## How to Run

```bash
# Full suite
python run_e2e.py

# Single module
python run_e2e.py --module test_09

# No report
python run_e2e.py --no-report

# Raw pytest (all modules)
python -m pytest e2e_tests/ -v --tb=short
```
