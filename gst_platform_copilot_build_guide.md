# GST Platform Build Guide for GitHub Copilot

## Purpose

This document is a step-by-step implementation guide for building a **separate GST platform** using the same enterprise patterns as the existing AP platform, while keeping GST as a clean bounded context.

This guide is written so a developer can use it directly with **GitHub Copilot** during implementation.

It covers:

- target architecture
- app-by-app scope
- data model plan
- stage and case lifecycle
- agent design
- API plan
- UI plan
- phased implementation
- ready-to-use Copilot prompts

---

## 1. Build Principles

### 1.1 Separation Principle
Do **not** extend the AP case domain for GST.

Instead:

- reuse platform patterns
- create a separate GST case system
- keep AP and GST as sibling domains

### 1.2 Reuse Principle
The following can be reused conceptually or technically from the current platform:

- RBAC
- audit logging
- traceability
- observability
- LLM client
- tool execution framework
- Celery task conventions
- Django template conventions
- dashboard conventions
- review workflow patterns
- case console patterns

### 1.3 GST-first Domain Principle
The GST domain should revolve around:

- GST documents
- GST transactions
- tax determination
- compliance checks
- ITC decisions
- reconciliation
- returns
- filing
- GST cases
- GST reviews
- GST agents

### 1.4 Deterministic-first Principle
Always prefer:

1. deterministic rules
2. config-driven logic
3. validation services
4. agent fallback only when needed

Agents should be used for:

- ambiguity
- classification help
- explanation
- exception analysis
- summary generation
- review routing
- filing readiness interpretation

---

## 2. Target Architecture

## 2.1 High-level architecture

```text
GST UI Layer
  Dashboard | Cases | Documents | Transactions | Compliance | Reconciliation | Returns | Reviews | Governance | Copilot

Django Template Views + DRF APIs

GST Orchestrators
  GSTCaseOrchestrator
  GSTStageExecutor
  GSTAgentOrchestrator
  GSTReturnPreparationOrchestrator

GST Domain Services
  Documents
  Transactions
  Compliance
  Reconciliation
  Returns
  Reviews
  Integrations

Shared Platform Layer
  accounts
  auditlog
  core
  tools
  metrics
  logging
  tracing
  blob storage
  LLM client

Infrastructure
  MySQL
  Redis
  Celery
  Azure OpenAI
  Azure Document Intelligence
  Azure Blob Storage
```

---

## 3. App Structure

Create the following Django apps:

```text
apps/
  gst_masterdata/
  gst_documents/
  gst_transactions/
  gst_compliance/
  gst_reconciliation/
  gst_returns/
  gst_cases/
  gst_reviews/
  gst_agents/
  gst_integrations/
  gst_copilot/
  gst_reports/
```

Shared reusable apps already available or expected:

```text
apps/
  accounts/
  auditlog/
  core/
  tools/
  dashboard/
  shared_documents/
```

---

## 4. Implementation Sequence

Build the platform in this exact order.

### Phase 1 — Foundation
Create:

- gst_masterdata
- gst_documents
- gst_transactions
- gst_cases

### Phase 2 — Compliance core
Create:

- gst_compliance
- basic deterministic validation
- ITC eligibility
- vendor compliance checks

### Phase 3 — Reconciliation
Create:

- gst_reconciliation
- purchase vs GSTR-2B
- sales vs GSTR-1
- books vs GSTR-3B placeholders

### Phase 4 — Review and governance
Create:

- gst_reviews
- GST case console
- GST timeline and audit views

### Phase 5 — Agents
Create:

- gst_agents
- policy engine
- deterministic resolver
- selected GST agents

### Phase 6 — Returns and filing
Create:

- gst_returns
- filing readiness
- filing integration abstractions

### Phase 7 — Copilot
Create:

- gst_copilot
- read-only GST case assistant

---

## 5. Folder Structure

```text
project/
├── apps/
│   ├── gst_masterdata/
│   ├── gst_documents/
│   ├── gst_transactions/
│   ├── gst_compliance/
│   ├── gst_reconciliation/
│   ├── gst_returns/
│   ├── gst_cases/
│   ├── gst_reviews/
│   ├── gst_agents/
│   ├── gst_integrations/
│   ├── gst_copilot/
│   └── gst_reports/
├── templates/
│   ├── gst_cases/
│   ├── gst_documents/
│   ├── gst_transactions/
│   ├── gst_compliance/
│   ├── gst_reconciliation/
│   ├── gst_returns/
│   ├── gst_reviews/
│   ├── gst_governance/
│   └── gst_copilot/
├── static/
│   ├── css/
│   └── js/
└── config/
```

---

## 6. Domain Model Plan

## 6.1 gst_masterdata

### Models
- GSTEntity
- GSTRegistration
- VendorTaxProfile
- HSNSACMaster
- TaxRule
- FilingPeriod

### Purpose
This app stores all static or slowly changing GST references.

### Must-have fields

#### GSTEntity
- entity_code
- entity_name
- country
- default_currency
- is_active

#### GSTRegistration
- entity
- gstin
- state_code
- registration_type
- effective_from
- effective_to
- filing_frequency
- is_active

#### VendorTaxProfile
- vendor_code
- vendor_name
- gstin
- pan
- state_code
- vendor_type
- is_compliant
- last_compliance_check_at
- risk_rating

#### HSNSACMaster
- code
- code_type
- description
- default_gst_rate
- is_exempt
- effective_from
- effective_to

#### TaxRule
- rule_code
- rule_type
- transaction_type
- hsn_sac_code
- conditions_json
- outcome_json
- priority
- effective_from
- effective_to
- is_active

#### FilingPeriod
- period_code
- month
- year
- start_date
- end_date
- due_date
- status

---

## 6.2 gst_documents

### Models
- GSTDocument
- GSTDocumentExtraction
- GSTDocumentLink

### Purpose
This app handles document upload, extraction, storage, and linking to GST business objects.

### Notes
Reuse the existing extraction pattern from the AP platform as a reference for:
- upload flow
- OCR extraction
- normalization
- validation
- document persistence

The uploaded AP project strongly demonstrates how upload, extraction, validation, and orchestration can be layered in services and Celery tasks. fileciteturn0file0

### Must-have fields

#### GSTDocument
- document_type
- original_filename
- file_hash
- source_channel
- processing_status
- entity
- filing_period
- uploaded_by
- uploaded_at

#### GSTDocumentExtraction
- gst_document
- engine_name
- engine_version
- raw_ocr_text
- raw_json
- confidence
- success
- error_message
- duration_ms

#### GSTDocumentLink
- gst_document
- linked_object_type
- linked_object_id
- link_type

---

## 6.3 gst_transactions

### Models
- GSTTransaction
- GSTTransactionLine
- GSTTransactionClassification
- GSTTaxDetermination

### Purpose
This is the transaction intelligence layer.

### Must-have fields

#### GSTTransaction
- transaction_number
- transaction_type
- source_type
- document_date
- posting_date
- entity
- registration
- vendor_tax_profile
- customer_name
- counterparty_gstin
- place_of_supply_state
- invoice_number
- invoice_date
- taxable_value
- cgst_amount
- sgst_amount
- igst_amount
- cess_amount
- total_tax_amount
- invoice_total
- currency
- is_reverse_charge
- is_import
- is_export
- is_sez
- status
- created_from_document

#### GSTTransactionLine
- transaction
- line_number
- description
- hsn_sac_code
- quantity
- unit_of_measure
- taxable_value
- gst_rate
- cgst_amount
- sgst_amount
- igst_amount
- cess_amount
- is_itc_eligible_candidate

#### GSTTransactionClassification
- transaction
- supply_type
- counterparty_type
- place_of_supply_state
- intra_or_inter_state
- classification_confidence
- classified_by_type
- classified_by_agent
- rationale

#### GSTTaxDetermination
- transaction
- tax_rule
- determined_rate
- tax_type
- place_of_supply_state
- is_reverse_charge
- determination_status
- confidence
- determined_by_type
- rationale

---

## 6.4 gst_compliance

### Models
- GSTComplianceCheckRun
- GSTComplianceCheckResult
- GSTITCDecision
- GSTComplianceIssue

### Purpose
This app validates whether a transaction is GST-compliant and whether ITC is claimable.

### Core validations
- mandatory invoice fields
- GSTIN format and presence
- tax math
- place of supply consistency
- tax type consistency
- reverse charge applicability
- vendor compliance status
- ITC block conditions

### Must-have fields

#### GSTComplianceCheckRun
- run_number
- entity
- filing_period
- run_type
- status
- started_at
- completed_at
- triggered_by
- summary_json

#### GSTComplianceCheckResult
- run
- transaction
- check_type
- status
- severity
- message
- details_json
- rule_reference

#### GSTITCDecision
- transaction
- eligibility_status
- reason_code
- reason_text
- blocked_credit_type
- confidence
- decided_by_type
- decided_by_agent

#### GSTComplianceIssue
- transaction
- issue_type
- severity
- status
- message
- details_json
- resolved
- resolved_by
- resolved_at

---

## 6.5 gst_reconciliation

### Models
- GSTReconciliationRun
- GSTReconciliationResult
- GSTReconciliationException
- GSTPortalTransaction

### Purpose
This app compares internal books/documents against portal data.

### Reconciliation types
- PURCHASE_2B
- SALES_1
- BOOKS_3B

### Must-have fields

#### GSTReconciliationRun
- run_number
- reconciliation_type
- entity
- registration
- filing_period
- status
- started_at
- completed_at
- triggered_by
- summary_json

#### GSTReconciliationResult
- run
- transaction
- match_status
- portal_reference
- difference_type
- difference_amount
- difference_tax
- requires_review
- confidence
- summary

#### GSTReconciliationException
- result
- exception_type
- severity
- message
- details_json
- resolved
- resolved_by
- resolved_at

#### GSTPortalTransaction
- registration
- filing_period
- source_type
- portal_doc_number
- portal_doc_date
- counterparty_gstin
- taxable_value
- cgst_amount
- sgst_amount
- igst_amount
- cess_amount
- raw_payload_json

---

## 6.6 gst_returns

### Models
- GSTReturn
- GSTReturnSection
- GSTReturnLine
- GSTFilingLog

### Purpose
This app groups validated transactions into return structures and tracks filing.

### Must-have fields

#### GSTReturn
- entity
- registration
- filing_period
- return_type
- status
- prepared_at
- reviewed_at
- approved_at
- filed_at
- file_reference
- prepared_by
- reviewed_by
- approved_by
- summary_json

#### GSTReturnSection
- gst_return
- section_code
- section_name
- amount_json
- source_summary_json
- status

#### GSTReturnLine
- gst_return
- transaction
- section_code
- bucket_code
- taxable_value
- cgst_amount
- sgst_amount
- igst_amount
- cess_amount
- included_flag
- exclusion_reason

#### GSTFilingLog
- gst_return
- filing_channel
- status
- request_payload_json
- response_payload_json
- ack_number
- reference_number
- error_message
- attempted_at

---

## 6.7 gst_cases

### Models
- GSTCase
- GSTCaseStage
- GSTCaseDecision
- GSTCaseAssignment
- GSTCaseSummary
- GSTCaseComment
- GSTCaseActivity

### Purpose
This is the primary operational workflow app.

### GST case types
Create enum `GSTCaseType`:

- PURCHASE_COMPLIANCE
- PURCHASE_ITC_ELIGIBILITY
- PURCHASE_2B_RECONCILIATION
- SALES_TAX_DETERMINATION
- SALES_1_RECONCILIATION
- RETURN_PREPARATION
- RETURN_REVIEW
- RETURN_FILING
- AMENDMENT
- CREDIT_NOTE_ADJUSTMENT
- DEBIT_NOTE_ADJUSTMENT
- VENDOR_NON_COMPLIANCE
- GST_EXCEPTION
- GST_NOTICE_RESPONSE
- REFUND_CLAIM
- E_INVOICE_VALIDATION
- E_WAY_BILL_VALIDATION

### GST case statuses
Create enum `GSTCaseStatus`:

- NEW
- INTAKE_IN_PROGRESS
- DOCUMENT_PROCESSING
- TRANSACTION_ANALYSIS_IN_PROGRESS
- COMPLIANCE_CHECK_IN_PROGRESS
- RECONCILIATION_IN_PROGRESS
- RETURN_PREPARATION_IN_PROGRESS
- REVIEW_PENDING
- IN_REVIEW
- APPROVED
- READY_FOR_FILING
- FILED
- CLOSED
- REJECTED
- ESCALATED
- FAILED
- ON_HOLD
- NEEDS_CLARIFICATION

### Stage types
Create enum `GSTCaseStageType`:

- INTAKE
- DOCUMENT_CLASSIFICATION
- EXTRACTION
- NORMALIZATION
- TRANSACTION_CLASSIFICATION
- VENDOR_TAX_LOOKUP
- TAX_DETERMINATION
- PLACE_OF_SUPPLY_ANALYSIS
- RCM_EVALUATION
- COMPLIANCE_VALIDATION
- ITC_EVALUATION
- RECONCILIATION_PREPARATION
- TWO_B_RECONCILIATION
- ONE_RECONCILIATION
- THREE_B_RECONCILIATION
- RETURN_BUCKETING
- RETURN_PREPARATION
- EXCEPTION_ANALYSIS
- REVIEW_ROUTING
- HUMAN_REVIEW
- APPROVAL
- FILING_PREPARATION
- PORTAL_FILING
- CASE_SUMMARY
- CLOSURE

### Must-have fields

#### GSTCase
- case_number
- case_type
- status
- priority
- risk_score
- entity
- registration
- filing_period
- related_transaction
- related_return
- related_reconciliation_result
- requires_human_review
- assigned_to
- assigned_queue
- source_channel
- opened_at
- closed_at

#### GSTCaseStage
- case
- stage_name
- status
- performed_by_type
- performed_by_agent
- input_payload_json
- output_payload_json
- started_at
- completed_at
- retry_count

#### GSTCaseDecision
- case
- stage
- decision_type
- decision_source
- confidence
- rationale
- evidence_json

#### GSTCaseAssignment
- case
- assignment_type
- assigned_user
- assigned_role
- queue_name
- status
- due_date

#### GSTCaseSummary
- case
- latest_summary
- reviewer_summary
- filing_summary
- recommendation

#### GSTCaseComment
- case
- author
- body
- is_internal
- created_at

#### GSTCaseActivity
- case
- activity_type
- actor
- metadata_json
- created_at

---

## 6.8 gst_reviews

### Models
- GSTReviewAssignment
- GSTReviewComment
- GSTManualReviewAction
- GSTReviewDecision

### Purpose
This app manages review queues and human intervention.

### Review statuses
- PENDING
- ASSIGNED
- IN_REVIEW
- APPROVED
- REJECTED
- REPROCESSED

---

## 6.9 gst_agents

### Models
- GSTAgentDefinition
- GSTAgentRun
- GSTAgentStep
- GSTDecisionLog
- GSTAgentRecommendation

### Purpose
This app manages agent orchestration, traceability, and recommendation tracking.

### GST agent types
Create enum `GSTAgentType`:

- GST_DOCUMENT_CLASSIFICATION
- GST_TRANSACTION_CLASSIFICATION
- GST_RATE_DETERMINATION
- PLACE_OF_SUPPLY_ANALYSIS
- REVERSE_CHARGE_EVALUATION
- GST_INVOICE_VALIDATION
- VENDOR_COMPLIANCE
- ITC_ELIGIBILITY
- PURCHASE_2B_RECONCILIATION
- SALES_1_RECONCILIATION
- BOOKS_3B_RECONCILIATION
- GST_EXCEPTION_ANALYSIS
- GST_REVIEW_ROUTING
- GST_CASE_SUMMARY
- GST_RETURN_PREPARATION
- GST_FILING_READINESS
- GST_PORTAL_FILING
- GST_COPILOT

---

## 6.10 gst_integrations

### Models
- GSTIntegrationConfig
- GSTIntegrationLog

### Purpose
This app handles ERP, GST portal, and external tax data integrations.

---

## 7. Orchestrators and Services

## 7.1 Core orchestrators

### GSTCaseOrchestrator
Responsible for running case stages in sequence.

### GSTStageExecutor
Runs one stage at a time and calls the correct domain service.

### GSTCaseStateMachine
Defines valid transitions and terminal states.

### GSTAgentOrchestrator
Runs agents based on policy-driven decisions.

### GSTReturnPreparationOrchestrator
Builds and validates returns from transaction pools.

---

## 7.2 Domain services by app

### gst_documents/services/
- upload_service.py
- classification_service.py
- extraction_adapter.py
- parser_service.py
- normalization_service.py
- link_service.py

### gst_transactions/services/
- transaction_creation_service.py
- classification_service.py
- tax_computation_service.py
- pos_service.py
- reverse_charge_service.py

### gst_compliance/services/
- validation_service.py
- itc_eligibility_service.py
- vendor_compliance_service.py
- compliance_run_service.py
- issue_builder_service.py

### gst_reconciliation/services/
- purchase_2b_reconciliation_service.py
- sales_1_reconciliation_service.py
- books_3b_reconciliation_service.py
- result_service.py
- exception_builder_service.py

### gst_returns/services/
- return_preparation_service.py
- bucketing_service.py
- validation_service.py
- filing_readiness_service.py
- portal_filing_service.py

### gst_cases/services/
- case_creation_service.py
- routing_service.py
- summary_service.py
- assignment_service.py

### gst_agents/services/
- base_agent.py
- agent_orchestrator.py
- policy_engine.py
- deterministic_resolver.py
- recommendation_service.py
- decision_log_service.py
- trace_service.py
- guardrails_service.py

### gst_integrations/services/
- gst_portal_connector.py
- erp_connector.py
- compliance_data_connector.py

### gst_copilot/services/
- copilot_service.py
- context_builder.py
- evidence_builder.py
- governance_builder.py

---

## 8. Step-by-step Build Plan

## Step 1 — Create apps and wire them into Django

### Deliverables
- create all GST apps
- register in `INSTALLED_APPS`
- add basic `api_urls.py`, `urls.py`, `admin.py`, `models.py`, `serializers.py`, `views.py`

### Copilot Prompt

```text
Create a new GST domain inside this Django project using separate apps:
gst_masterdata, gst_documents, gst_transactions, gst_compliance, gst_reconciliation, gst_returns, gst_cases, gst_reviews, gst_agents, gst_integrations, gst_copilot, gst_reports.

For each app:
- create models.py
- create admin.py
- create serializers.py
- create views.py
- create api_urls.py
- create urls.py
- follow the same architectural conventions already used in the project
- use Django 4.2 and DRF
- keep the GST domain separate from the AP domain
- do not reuse AP case models
- prepare empty service folders where applicable
- update config URLs so /api/v1/gst/... routes can be included cleanly
```

---

## Step 2 — Build gst_masterdata

### Deliverables
- GSTEntity
- GSTRegistration
- VendorTaxProfile
- HSNSACMaster
- TaxRule
- FilingPeriod
- admin screens
- list/detail APIs

### Copilot Prompt

```text
Implement the gst_masterdata app for a Django GST platform.

Create the following models with proper foreign keys, indexes, ordering, __str__ methods, admin configuration, serializers, and DRF list/detail endpoints:

1. GSTEntity
2. GSTRegistration
3. VendorTaxProfile
4. HSNSACMaster
5. TaxRule
6. FilingPeriod

Requirements:
- use audit-friendly timestamps
- use is_active where appropriate
- add database indexes for gstin, vendor_code, code, period_code, entity_code
- add filtering/search support in list APIs
- keep the code enterprise-grade and migration-ready
```

---

## Step 3 — Build gst_documents

### Deliverables
- upload model
- extraction model
- Celery task placeholder
- upload endpoint
- extraction pipeline shell

### Copilot Prompt

```text
Implement the gst_documents app for a Django GST platform.

Create:
- GSTDocument
- GSTDocumentExtraction
- GSTDocumentLink

Also create services for:
- document upload
- document classification
- OCR extraction adapter
- extraction parsing
- normalization
- business object linking

Requirements:
- reuse the platform pattern of upload -> extraction -> parsing -> normalization -> persistence
- make the design GST-specific
- support PDF and image uploads
- store processing status
- create Celery task placeholders for async extraction
- create DRF endpoints for upload, list, detail, and extraction detail
- keep the code modular and service-oriented
```

---

## Step 4 — Build gst_transactions

### Deliverables
- transaction model
- line model
- classification model
- tax determination model
- create transaction from extracted document

### Copilot Prompt

```text
Implement the gst_transactions app.

Create models:
- GSTTransaction
- GSTTransactionLine
- GSTTransactionClassification
- GSTTaxDetermination

Create services:
- GSTTransactionCreationService
- GSTTransactionClassificationService
- GSTTaxComputationService
- GSTPlaceOfSupplyService
- GSTReverseChargeService

Requirements:
- transaction creation should consume normalized extracted document data
- support purchase and sales transactions
- support tax breakup fields: cgst, sgst, igst, cess
- support flags for reverse charge, import, export, SEZ
- create DRF list/detail APIs
- create transaction detail serializer including lines, classification, and tax determination
```

---

## Step 5 — Build gst_cases foundation

### Deliverables
- GSTCase and related models
- GST enums
- case creation service
- case state machine
- case APIs

### Copilot Prompt

```text
Implement the gst_cases app for a separate GST case management system.

Create:
- GSTCase
- GSTCaseStage
- GSTCaseDecision
- GSTCaseAssignment
- GSTCaseSummary
- GSTCaseComment
- GSTCaseActivity

Also implement:
- GSTCaseType enum
- GSTCaseStatus enum
- GSTCaseStageType enum
- GSTCaseCreationService
- GSTCaseStateMachine
- list/detail APIs for cases
- timeline-ready structure for case activity and stages

Requirements:
- GSTCase should be able to link to transaction, return, and reconciliation result
- design the state machine with valid transitions and terminal states
- generate case numbers in a readable enterprise format
- keep the app independent from APCase
```

---

## Step 6 — Build gst_compliance

### Deliverables
- compliance run models
- validation service
- ITC service
- vendor compliance service
- issue builder

### Copilot Prompt

```text
Implement the gst_compliance app.

Create models:
- GSTComplianceCheckRun
- GSTComplianceCheckResult
- GSTITCDecision
- GSTComplianceIssue

Create services:
- GSTValidationService
- GSTITCEligibilityService
- GSTVendorComplianceService
- GSTComplianceRunService
- GSTIssueBuilderService

Validation rules should include:
- mandatory invoice fields
- GSTIN presence and format
- tax math checks
- place of supply consistency
- tax type consistency
- reverse charge applicability
- vendor compliance checks
- ITC decisioning

Return structured results suitable for case orchestration and review routing.
```

---

## Step 7 — Build gst_reconciliation

### Deliverables
- reconciliation models
- portal transaction model
- services for 2B and 1 reconciliation
- exception builder

### Copilot Prompt

```text
Implement the gst_reconciliation app.

Create models:
- GSTReconciliationRun
- GSTReconciliationResult
- GSTReconciliationException
- GSTPortalTransaction

Create services:
- Purchase2BReconciliationService
- Sales1ReconciliationService
- Books3BReconciliationService
- GSTReconciliationResultService
- GSTReconciliationExceptionBuilder

Requirements:
- support reconciliation by filing period and GST registration
- match internal GST transactions against portal transactions
- classify results as MATCHED, PARTIAL, MISSING_IN_PORTAL, EXTRA_IN_PORTAL, REVIEW
- create structured exceptions for mismatches
- create list/detail APIs for reconciliation runs and results
```

---

## Step 8 — Build gst_reviews

### Deliverables
- review assignment
- comments
- manual action logging
- decision model
- review APIs

### Copilot Prompt

```text
Implement the gst_reviews app.

Create models:
- GSTReviewAssignment
- GSTReviewComment
- GSTManualReviewAction
- GSTReviewDecision

Create service methods for:
- create assignment
- assign reviewer
- start review
- record action
- add comment
- approve
- reject
- request reprocess

Requirements:
- integrate cleanly with GSTCase
- allow manual field correction tracking
- keep an audit-friendly design
- expose review list/detail/action APIs
```

---

## Step 9 — Build GST stage orchestration

### Deliverables
- GSTStageExecutor
- GSTCaseOrchestrator
- routing logic
- summary service

### Suggested V1 stage flow
For `PURCHASE_ITC_ELIGIBILITY`:

```text
INTAKE
→ DOCUMENT_CLASSIFICATION
→ EXTRACTION
→ NORMALIZATION
→ TRANSACTION_CLASSIFICATION
→ TAX_DETERMINATION
→ COMPLIANCE_VALIDATION
→ ITC_EVALUATION
→ EXCEPTION_ANALYSIS
→ REVIEW_ROUTING
→ CASE_SUMMARY
```

For `PURCHASE_2B_RECONCILIATION`:

```text
INTAKE
→ DOCUMENT_CLASSIFICATION
→ EXTRACTION
→ NORMALIZATION
→ TRANSACTION_CLASSIFICATION
→ COMPLIANCE_VALIDATION
→ TWO_B_RECONCILIATION
→ EXCEPTION_ANALYSIS
→ REVIEW_ROUTING
→ CASE_SUMMARY
```

### Copilot Prompt

```text
Implement GST case orchestration.

Create:
- GSTStageExecutor
- GSTCaseOrchestrator
- GSTCaseRoutingService
- GSTCaseSummaryService

Requirements:
- the orchestrator must execute stage handlers in order based on GST case type
- the stage executor should call the relevant GST services for each stage
- use a service-oriented design similar to enterprise case platforms
- support stage retries
- store input/output payloads on GSTCaseStage
- create audit-friendly activity logging hooks
- support terminal states and review routing
```

---

## Step 10 — Build gst_agents framework

### Deliverables
- agent definitions
- agent runs
- agent steps
- recommendation model
- GST agent orchestrator
- GST policy engine

### V1 agents to implement first
- GSTInvoiceValidationAgent
- ITCEligibilityAgent
- Purchase2BReconciliationAgent
- GSTExceptionAnalysisAgent
- GSTReviewRoutingAgent
- GSTCaseSummaryAgent

### Copilot Prompt

```text
Implement the gst_agents app.

Create models:
- GSTAgentDefinition
- GSTAgentRun
- GSTAgentStep
- GSTDecisionLog
- GSTAgentRecommendation

Create services:
- GSTAgentOrchestrator
- GSTPolicyEngine
- GSTDeterministicResolver
- GSTRecommendationService
- GSTDecisionLogService
- GSTAgentTraceService
- GSTGuardrailsService

Requirements:
- prefer deterministic rules before invoking agents
- support agent run traceability fields
- support confidence, reasoning summary, duration, token fields
- support recommendation generation and acceptance tracking
- keep the design aligned with enterprise agent governance practices
```

---

## Step 11 — Build concrete GST agents

### Copilot Prompt

```text
Inside gst_agents/services/agents, implement the following GST agents as separate classes extending a common base agent:

- GSTInvoiceValidationAgent
- ITCEligibilityAgent
- Purchase2BReconciliationAgent
- GSTExceptionAnalysisAgent
- GSTReviewRoutingAgent
- GSTCaseSummaryAgent

Requirements:
- each agent should have a clear purpose, input schema, output schema, and summarized reasoning
- agents should operate on GST case, transaction, compliance, and reconciliation context
- keep prompts deterministic where possible
- include placeholders for tool calling
- do not make the agents directly mutate database records except through orchestrator-approved outputs
```

---

## Step 12 — Build gst_returns

### Deliverables
- return model
- return sections
- return lines
- filing logs
- return prep services

### Copilot Prompt

```text
Implement the gst_returns app.

Create models:
- GSTReturn
- GSTReturnSection
- GSTReturnLine
- GSTFilingLog

Create services:
- GSTReturnPreparationService
- GSTReturnBucketingService
- GSTReturnValidationService
- GSTFilingReadinessService
- GSTPortalFilingService

Requirements:
- returns should be grouped by entity, registration, filing period, and return type
- transactions should be bucketed into return sections
- keep status flow for DRAFT, REVIEW, APPROVED, READY, FILED, FAILED
- create list/detail/action APIs for returns
```

---

## Step 13 — Build GST governance and timeline views

### Deliverables
- audit history per case
- timeline builder
- agent trace viewer
- access history

The AP reference project shows strong governance, audit, and traceability concepts that should be mirrored here in GST with GST-specific filters and UI screens. fileciteturn0file0

### Copilot Prompt

```text
Create GST governance views and APIs.

Requirements:
- build a GST case timeline combining case stages, review actions, agent runs, decisions, and audit events
- add APIs for audit history, case timeline, agent trace, and access history
- create template views for GST governance pages
- keep the design compatible with existing auditlog and trace infrastructure
- surface role-based visibility where appropriate
```

---

## Step 14 — Build GST Copilot

### Deliverables
- session model
- message model
- read-only case chat
- evidence/context builder
- case-linked workspace

### Copilot Prompt

```text
Implement a gst_copilot app for a read-only GST conversational assistant.

Create:
- Copilot session model
- Copilot message model
- context builder
- evidence builder
- governance builder
- AP-style workspace but GST-focused

Requirements:
- the assistant should answer questions about GST cases, transactions, compliance issues, ITC decisions, reconciliation findings, and return readiness
- it must remain read-only
- support case-linked sessions
- support role-aware visibility
- create APIs and template views for the workspace
```

---

## 9. Recommended V1 Scope

Build this first:

### Apps
- gst_masterdata
- gst_documents
- gst_transactions
- gst_cases
- gst_compliance
- gst_reconciliation
- gst_reviews
- gst_agents

### Use cases
- purchase invoice GST validation
- ITC eligibility assessment
- purchase vs GSTR-2B reconciliation
- exception analysis
- review routing
- GST case summary

Do **not** start with:
- full filing automation
- notices
- refunds
- e-way bill
- advanced portal integration

---

## 10. Suggested Milestone Plan

### Milestone 1
Foundation and master data

### Milestone 2
Document extraction to GST transaction creation

### Milestone 3
Compliance and ITC engine

### Milestone 4
2B reconciliation and review workflow

### Milestone 5
Agent orchestration and case console

### Milestone 6
Returns and filing readiness

### Milestone 7
Copilot and governance enhancements

---

## 11. Build Quality Rules

Developers should follow these rules during implementation:

- keep each app isolated
- avoid cross-app circular imports
- put business logic in services, not views
- keep views thin
- keep APIs RESTful and filterable
- store stage input/output payloads for audit
- never let agents directly change core records without orchestrator control
- design for traceability first
- use enums/constants for all workflow states
- create migrations app by app
- use admin pages for early testing and seed setup

---

## 12. Final Build Instruction for Copilot

Use this as the master Copilot prompt when beginning the project.

```text
You are implementing a new enterprise GST platform inside an existing Django application.

Important design rule:
- GST must be a separate bounded context
- do not extend or reuse the AP case domain model
- reuse only shared infrastructure patterns like RBAC, audit logging, tracing, Celery conventions, LLM client patterns, and template conventions

Build the GST platform in phases using these apps:
gst_masterdata, gst_documents, gst_transactions, gst_compliance, gst_reconciliation, gst_returns, gst_cases, gst_reviews, gst_agents, gst_integrations, gst_copilot, gst_reports.

Implementation goals:
1. create clean Django app scaffolding
2. build GST master data models
3. build GST document extraction flow
4. build GST transaction models and services
5. build a separate GST case management system
6. build GST compliance and ITC decisioning
7. build GST reconciliation against portal data
8. build GST review workflow
9. build GST agent orchestration
10. build GST returns and filing readiness
11. build GST copilot

Architecture requirements:
- service-oriented design
- thin views
- DRF APIs + Django template views
- Celery-ready async tasks
- audit-friendly stage payloads
- enterprise-grade enums and state machines
- modular agents with deterministic-first policy logic

Start with V1 scope:
- purchase invoice GST validation
- ITC eligibility
- purchase vs GSTR-2B reconciliation
- exception analysis
- review routing
- GST case summary

Generate code step by step, app by app, with migrations, serializers, admin registration, API URLs, and service stubs where needed.
```

---

## 13. Notes for the Developer

- First make the models stable.
- Then build services.
- Then build orchestrators.
- Then wire APIs and UI.
- Then add agents.
- Then add returns and copilot.

Do not try to build everything together in one pass.

The uploaded AP reference platform is useful as a pattern source for enterprise structure, orchestration, governance, and traceability, but GST should remain a separate clean domain. fileciteturn0file0
