# 01 — Inferred Business Requirements

**Generated**: 2026-04-09 | **Method**: Inferred from models, services, config, and README  
**Confidence**: High for functional; Medium for non-functional (some inferred from code choices)

---

## 1. Inferred Business Objectives

| # | Objective | Evidence |
|---|-----------|---------|
| B1 | Automate AP invoice processing — reduce manual data entry | Extraction pipeline, OCR + LLM |
| B2 | Enforce PO compliance — no invoice paid without valid PO match | Reconciliation engine, exception building |
| B3 | Support 3-way matching where goods receipt is required | `THREE_WAY` mode, `GRNRetrievalAgent` |
| B4 | Allow 2-way matching for service invoices | `TWO_WAY` mode, `ReconciliationPolicy` rules |
| B5 | Handle multi-vendor, multi-currency, multi-location operations | Currency field, CompanyProfile tenant, tolerance engine |
| B6 | Reduce touchless rate latency via AI-assisted exception analysis | Agent pipeline auto-running post-reconciliation |
| B7 | Maintain audit trail for regulatory/compliance purposes | AuditEvent, DecisionLog, RBAC snapshots |
| B8 | Integrate with existing ERP systems (multiple types) | 6 ERP connector types |
| B9 | Post approved invoices back to ERP | Posting workflow (PROPOSED → SUBMITTED) |
| B10 | Support multiple organizations on one platform | Multi-tenant CompanyProfile model |

---

## 2. Inferred Functional Requirements

### Invoice Ingestion
- FR-I1: Accept PDF invoices via web upload (single) and bulk upload
- FR-I2: Store originals in Azure Blob Storage with content hash deduplication
- FR-I3: Support multiple document types (Invoice, Credit Note, inferred from `DocumentType` enum)

### Invoice Extraction
- FR-E1: OCR invoice using Azure Document Intelligence
- FR-E2: Classify invoice category (goods / service / travel) before extraction
- FR-E3: Compose extraction prompt using base + category + country overlays
- FR-E4: Extract: vendor, vendor_tax_id, buyer_name, invoice_number, invoice_date, due_date, po_number, currency, subtotal, tax_percentage, tax_breakdown (cgst/sgst/igst/vat), total_amount, document_type, line_items
- FR-E5: Apply 5 deterministic repair rules before parsing
- FR-E6: Validate extraction output; produce field-level confidence scores
- FR-E7: Detect duplicate invoices
- FR-E8: Gate extraction on human approval when confidence < configurable threshold
- FR-E9: Support auto-approval above threshold (default: 0.85 confidence)
- FR-E10: Allow field-level correction during human approval review
- FR-E11: Track credit consumption per user for extraction

### Reconciliation
- FR-R1: Resolve reconciliation mode (2-way / 3-way / non-PO) per invoice
- FR-R2: Mode resolution via: ReconciliationPolicy rules → heuristic → config default
- FR-R3: Match invoice header to PO: vendor, currency, total amount within tolerance
- FR-R4: Match invoice line items to PO lines: quantity, unit price, amount within tolerance
- FR-R5: Match against GRN for 3-way mode: quantities received vs invoiced
- FR-R6: Classify match outcome: MATCHED / PARTIAL_MATCH / UNMATCHED
- FR-R7: Build structured exception list per mismatch
- FR-R8: Auto-close PARTIAL_MATCH within auto-close tolerance band (5%/3%/3%)
- FR-R9: Support configurable tolerance bands (strict + auto-close) per config record
- FR-R10: Handle partial invoices (invoice < 95% of PO total → partial milestone)

### Agent Analysis
- FR-A1: Auto-trigger agent pipeline for non-MATCHED reconciliation results
- FR-A2: Agent sequence determined by `PolicyEngine` based on match status, exceptions, and mode
- FR-A3: PO Retrieval Agent: attempt to find PO if deterministic lookup failed
- FR-A4: If PO found by agent, re-run deterministic reconciliation atomically (feedback loop)
- FR-A5: GRN Retrieval Agent: investigate GRN data (3-way mode only)
- FR-A6: Exception Analysis Agent: analyze exceptions, produce recommendation + reviewer summary
- FR-A7: Review Routing Agent: determine review queue, priority, assignee role
- FR-A8: Case Summary Agent: produce human-readable case summary
- FR-A9: Persist all agent runs, steps, messages, decisions, recommendations to DB

### Human Review
- FR-H1: Auto-assign cases requiring review to available REVIEWER-role users
- FR-H2: Support bulk assignment to specific reviewer
- FR-H3: Reviewer sees: exceptions, agent recommendation, reviewer summary, suggested actions
- FR-H4: Review actions: Approve, Approve with Fixes, Reject, Needs Info, Escalate
- FR-H5: Full audit trail of review decisions (who, when, what, why)
- FR-H6: Escalation path to Finance Manager

### ERP Integration
- FR-ERP1: Support 6 connector types: Custom API, SQL Server, MySQL, Dynamics 365, Zoho, Salesforce
- FR-ERP2: Cache ERP data at L1/L2/L3 with configurable freshness (transactional: 24h, master: 168h)
- FR-ERP3: Fall back to local mirror tables when ERP unavailable
- FR-ERP4: Resolve vendor, item, tax rate, cost center, PO, GRN from ERP
- FR-ERP5: Post approved invoices to ERP with mapping engine

### Governance
- FR-G1: Role-based access control with 6 roles and 40+ named permissions
- FR-G2: Per-user permission overrides (ALLOW/DENY with expiry)
- FR-G3: All business-significant events recorded in AuditEvent table
- FR-G4: Agent execution fully traced: AgentRun, AgentStep, AgentMessage, DecisionLog
- FR-G5: RBAC snapshot captured at time of every sensitive operation
- FR-G6: Governance dashboard with agent compliance metrics

---

## 3. Inferred Non-Functional Requirements

| # | Requirement | Evidence |
|---|-------------|---------|
| NFR1 | Multi-tenancy: row-level isolation per CompanyProfile | `tenant` FK on all models |
| NFR2 | Performance: async all long-running operations | Celery tasks for extraction, recon, agents |
| NFR3 | Resilience: Celery task retries with backoff | max_retries=5 on reconciliation, acks_late on case tasks |
| NFR4 | LLM timeout: 120s HTTP timeout per LLM call | `LLM_REQUEST_TIMEOUT=120` setting |
| NFR5 | Observability: full trace from task → agent → tool → LLM | Langfuse hierarchy, trace_id propagation |
| NFR6 | Auditability: every decision persisted with actor, role, rationale | DecisionLog, AuditEvent |
| NFR7 | Security: RBAC at middleware, service, and agent level | TenantMiddleware, RBACMiddleware, AgentGuardrailsService |
| NFR8 | LLM cost control: token tracking + cost estimation | `LLMCostRate`, `actual_cost_usd` on AgentRun |
| NFR9 | Storage: Azure Blob for document storage with SHA-256 deduplication | `DocumentUpload.file_hash`, blob fields |
| NFR10 | Timezone: Asia/Kolkata configured | `TIME_ZONE = "Asia/Kolkata"` in settings |
| NFR11 | Database: MySQL utf8mb4 strict mode | DB OPTIONS in settings |

---

## 4. User Roles / Personas

| Persona | Primary Workflow | Key Permissions |
|---------|----------------|----------------|
| AP Processor | Upload invoices, monitor extraction, trigger reconciliation | `invoices.view`, `invoices.upload`, `reconciliation.run` |
| Reviewer | Review exceptions, make approval decisions | `reviews.view`, `reviews.approve`, `reviews.reject` |
| Finance Manager | Approve escalations, view financial reports | `cases.escalate`, finance-level views |
| Auditor | Read-only governance and audit log access | All `.view` permissions, no write |
| Admin | Full platform management including RBAC admin | All permissions (admin bypass) |
| System Agent | Autonomous pipeline operations | Least-privilege: extraction/recon/routing only |

---

## 5. Implemented vs Partial vs Intended

| Requirement Area | Status | Notes |
|----------------|--------|-------|
| Invoice extraction pipeline (full) | **Implemented** | 11 stages, 51 tests |
| Reconciliation engine (full) | **Implemented** | 14 services, 73 tests |
| Agent pipeline (LLM + system) | **Implemented** | 8 LLM + 5 system agents |
| Human review workflow | **Implemented** | Auto-assignment, actions, audit |
| ERP integration framework | **Implemented** | 6 connectors, resolution service |
| Invoice posting workflow | **Implemented** | State machine, mapping engine |
| Multi-tenant isolation | **Implemented** | CompanyProfile FK pattern |
| RBAC + audit | **Implemented** | 6 roles, 40 permissions, AuditEvent |
| Langfuse observability | **Implemented** | Full trace hierarchy |
| Report exports | **Partial** | URL registered, service not implemented |
| Email notifications | **Not implemented** | No email framework wired |
| Celery Beat scheduling | **Partial** | Only learning_actions scheduled |
| Docker / CI-CD | **Not implemented** | Deployment guide exists (Nginx/Gunicorn) |
| Extraction edge cases (multi-page) | **Partial** | Core pipeline complete; edge cases noted as backlog |

---

## 6. Assumptions Needing Validation

1. **India GST handling**: Timezone is `Asia/Kolkata`, tax_breakdown has cgst/sgst/igst fields — platform appears to handle Indian GST invoices as a primary use case. Confirm if global is also required.
2. **Saudi Arabia seed data**: Seed data references "Saudi McD" — is this a demo tenant or indicative of actual deployment?
3. **Non-PO invoice approval authority**: Who approves non-PO invoices? Finance Manager? Policy unclear from code alone.
4. **Credit system**: Per-user credit accounts for extraction — what is the business model? SaaS billing or internal cost allocation?
5. **Procurement intelligence**: Module exists (benchmarking, compliance, quotations) but not deeply inspected — scope to validate.
6. **ERP posting approval**: Posting has a REVIEW_REQUIRED state — who approves, and what are the approval rules?
