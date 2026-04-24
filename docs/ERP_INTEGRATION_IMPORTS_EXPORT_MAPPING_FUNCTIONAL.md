# ERP Integration + ERP Imports + Export Mapping Functional Document

Version: 1.1
Last Updated: 2026-04-24
Scope: Functional behavior for ERP connectivity, direct and batch reference imports, and purchase-invoice export mapping.

---

## 1. Purpose

This document defines how ERP data is connected, imported, and consumed for export mapping in the AP platform.

It is intended for:
- Product owners
- AP operations
- Implementation engineers
- QA and UAT teams
- Support and incident response teams

It includes:
- End-to-end functional flows
- Data ownership and precedence rules
- Failure handling and fallback behavior
- Configuration controls
- Runbook steps for safe operations
- Acceptance criteria for release and UAT

It excludes:
- Procurement-specific flows
- Frontend visual design details
- ERP connector implementation internals beyond functional behavior

---

## 2. Functional Scope

### 2.1 In scope

1. ERP integration layer as shared platform capability.
2. ERP reference data imports:
- Vendor
- Item
- Tax code
- Cost center
- Open PO
3. Direct ERP imports through configured connectors.
4. ERP import flush operations (global and tenant-specific).
5. Export field mapping for purchase invoice workbook (single and bulk export).
6. Deterministic-first mapping with optional AI fallback for unresolved fields only.
7. First-class platform system-agent registration for export mapping governance.

### 2.2 Out of scope

1. Final ERP posting submission lifecycle details (covered by posting docs).
2. Full reconciliation business rules (covered by reconciliation docs).
3. Non-invoice export templates.

---

## 3. Business Objectives

1. Ensure export templates are populated from ERP-authoritative data whenever available.
2. Reduce blank fields in export outputs without sacrificing determinism.
3. Keep mapping auditable, repeatable, and safe for finance workflows.
4. Allow phased AI usage only where deterministic mapping cannot resolve fields.
5. Support tenant-safe operations across import, lookup, and export.

---

## 4. High-Level Architecture

Primary modules:

1. ERP Integration Layer
- Path: apps/erp_integration/
- Responsibility: connectors, resolution chain, caching, fallback, audit.

2. Reference Import Pipeline
- Path: apps/posting_core/services/direct_erp_importer.py
- Responsibility: source extraction and normalization into ERP reference tables.

3. Export Resolution and Mapping
- Paths:
  - apps/extraction/template_views.py
  - apps/extraction/services/export_mapping_agent.py
- Responsibility: map invoice export fields from imported ERP references.

4. Reference Storage
- Path: apps/posting_core/models.py
- Responsibility: ERP reference snapshots and import batch records.

---

## 5. Primary Users and Roles

1. AP Processor
- Uses extraction workbench exports.
- Relies on mapped fields in output workbook.

2. Finance Manager
- Reviews exported accounting fields for posting workflows.

3. Admin / Platform Ops
- Configures ERP connectors.
- Runs imports and flush commands.
- Handles outage recovery and re-import cycles.

4. Auditor
- Verifies repeatability and source provenance in operational process.

---

## 6. End-to-End Functional Flow

### 6.1 ERP connection and data availability

1. Admin configures an active default ERP connection.
2. Connector validates connectivity.
3. Import process reads ERP data and writes normalized reference snapshots.
4. Export routines consume latest completed snapshots per tenant scope.

### 6.2 Reference import flow

1. Import is triggered for one batch type.
2. System validates batch type and connector.
3. Connectivity is tested with transient retry behavior.
4. Rows are queried from ERP and normalized.
5. Validation and importer pipeline persists rows in reference tables.
6. Import batch is marked completed with row statistics.

### 6.3 Export mapping flow

1. User triggers single or bulk purchase invoice export.
2. Export initializes reference resolver using latest completed batches.
3. Mapping agent resolves header and line fields using deterministic rules first.
4. Optional AI fallback runs only for unresolved fields when enabled.
5. Workbook sheets are generated with mapped values and defaults.
6. File is returned as xlsx response.

---

## 7. ERP Imports Functional Details

## 7.1 Import types

Supported batch types:
1. VENDOR
2. ITEM
3. TAX
4. COST_CENTER
5. OPEN_PO

## 7.2 Source and target behavior

1. Source rows are connector-specific raw payloads.
2. Import layer normalizes into platform schema.
3. Original source details are retained in raw_json for downstream mapping.
4. Latest completed batch per type is used by export resolver.

## 7.3 Connectivity resilience

1. Import orchestration retries transient connectivity errors.
2. SQL transient unavailability errors (for example 40613) are treated as retryable.
3. If retries fail, import is marked failed and no partial silent success is reported as complete.

## 7.4 Tenant behavior

1. Import batches are tenant-scoped when tenant is provided.
2. Export resolver reads latest completed batch within tenant scope.
3. Tenant-specific flush can remove imports for one tenant without affecting others.

---

## 8. ERP Import Flush Functional Behavior

Command:
- apps/posting_core/management/commands/flush_erp_imports.py

Modes:
1. Global flush (all tenants)
2. Tenant flush (--tenant-id)

Deleted artifacts:
1. ERPReferenceImportBatch (and cascaded reference rows)
2. VendorAliasMapping linked to imported vendor references
3. ItemAliasMapping linked to imported item references
4. Related AuditEvent rows for ERPReferenceImportBatch entities

Safety controls:
1. Interactive confirmation by default
2. Non-interactive mode with --confirm

Operational impact:
1. Export mapping quality drops immediately after flush until re-import completes.
2. Any resolver that depends on reference snapshots can return blanks/defaults until repopulation.

---

## 9. Export Mapping Functional Design

## 9.1 Export surfaces

1. Single invoice workbook export
- Function: extraction_export_purchase_invoice_excel

2. Bulk workbook export
- Function: extraction_export_purchase_invoice_excel_bulk

Both use same resolver and mapping agent behavior for consistency.

## 9.2 Deterministic reference resolver

Resolver class:
- _ExportReferenceResolver in apps/extraction/template_views.py

Responsibilities:
1. Load latest completed ERP snapshot batches per type.
2. Build lookup indexes for vendor, item, PO, cost center references.
3. Resolve fields using deterministic priority and canonical alias matching.
4. Normalize raw_json key variants (case, spacing, underscore variations).

## 9.3 Mapping agent (new)

Class:
- ExportFieldMappingAgent
- Path: apps/extraction/services/export_mapping_agent.py

Behavior:
1. Always call deterministic resolver first.
2. Identify unresolved fields only.
3. If AI fallback is disabled, return deterministic result as final.
4. If AI fallback is enabled, call LLM for unresolved fields only.
5. Apply AI suggestions only when confidence threshold is met.
6. Never override already-resolved deterministic fields.

## 9.3.1 First-class platform agent type

The export mapping capability is now a first-class system agent type in the central platform agent stack.

Agent type:
1. SYSTEM_EXPORT_FIELD_MAPPING

Core wiring:
1. Agent enum registration in core enum catalog.
2. Central agent registry mapping to deterministic system agent class.
3. Guardrail permission mapping to agents.run_system_export_field_mapping.
4. Governance screen classification as deterministic (not LLM coverage gap).
5. Eval adapter mapping for agent-run telemetry compatibility.

## 9.4 Resolved field sets

Header field focus:
1. party_account
2. purchase_account
3. currency
4. due_days
5. due_date

Line field focus:
1. item_code
2. uom
3. cost_center
4. department
5. purchase_account

## 9.5 Field precedence (functional)

For each field:
1. Deterministic ERP snapshot resolution
2. Deterministic fallback (invoice/vendor defaults)
3. Optional AI unresolved-only suggestion (feature-gated)
4. Final hard default (empty or configured default where applicable)

Important safety rule:
- Deterministic value is authoritative and is not replaced by AI.

## 9.6 Governance runtime emission during exports

Export execution now emits a best-effort AgentRun for SYSTEM_EXPORT_FIELD_MAPPING so runtime governance history shows actual export-mapping activity.

Emission characteristics:
1. One AgentRun per export request (single or bulk), not per invoice line.
2. Non-blocking: emission failures never fail the export response.
3. RBAC-aware: emission occurs only when actor is authorized for the system agent permission.
4. Captured telemetry includes:
- scope (single or bulk)
- invoices_count
- header_unresolved_count
- line_unresolved_count
- ai_fallback_enabled
- ai_fallback_used
- ai_fields_applied

---

## 10. AI Fallback Controls (Phase 2)

Settings:
1. EXPORT_MAPPING_AI_FALLBACK_ENABLED (default false)
2. EXPORT_MAPPING_AI_MIN_CONFIDENCE (default 0.80)

Functional rollout:
1. Phase 1: deterministic-only in production (recommended baseline).
2. Phase 2: enable AI fallback in test/UAT.
3. Promote to production only after measurable quality gain and no regression.

AI guardrails:
1. AI only sees unresolved fields.
2. Structured JSON contract only.
3. Low-confidence outputs are discarded.
4. Missing or invalid JSON is ignored safely.

---

## 11. Data Quality and Mapping Rules

1. ERP master or transactional snapshot values should be preferred over extracted OCR text for accounting-sensitive columns.
2. raw_json is treated as source evidence for connector-specific fields.
3. Alias normalization is required to absorb connector key drift and reduce recurring blank regressions.
4. Purchase account should derive from PO/vendor ERP data before falling back to party account.
5. Due Days and Due Date should resolve from explicit invoice dates or payment terms-derived logic.

---

## 12. Failure Modes and Expected Behavior

## 12.1 ERP connector unavailable

Expected:
1. Import fails with explicit status.
2. Existing snapshot remains usable.
3. Export continues using last completed batches.

## 12.2 Imports flushed without re-import

Expected:
1. Export field population decreases.
2. Deterministic resolver may return blanks for ERP-dependent fields.
3. Re-import restores mapping quality.

## 12.3 Missing keys in raw_json

Expected:
1. Canonical alias resolver attempts normalized key variants.
2. If still unresolved, deterministic fallback applies.
3. Optional AI fallback may propose values for unresolved fields only.

## 12.4 AI fallback returns low confidence or invalid response

Expected:
1. Suggestions are ignored.
2. Deterministic result remains final.
3. Export still completes successfully.

---

## 13. Security, Governance, and Audit

1. ERP secrets are resolved via environment variable references, not stored in plaintext.
2. Tenant scoping is preserved for import and lookup reads.
3. Import batch status and row counts support operational auditability.
4. Export mapping remains deterministic by default to support reproducible financial output.
5. AI fallback is explicitly feature-gated and confidence-gated.
6. Export mapping governance now records runtime system-agent runs for operational traceability.

### 13.1 Migration-safe rollout items

Recent migration updates introduced as part of this feature set:

1. accounts.0007_add_system_export_field_mapping_permission
- Adds agents.run_system_export_field_mapping permission.
- Grants to SUPER_ADMIN, ADMIN, and SYSTEM_AGENT.

2. agents.0018_alter_agentdefinition_agent_type_and_more
- Adds SYSTEM_EXPORT_FIELD_MAPPING to AgentDefinition and AgentRun choices.
- Seeds AgentDefinition record for system export field mapping.

3. reconciliation.0019_remove_global_partial_unique_constraint
- Removes MySQL-unsupported conditional unique constraint on global config names.
- Warning models.W036 is eliminated.
- Global-name uniqueness remains enforced at application validation layer.

---

## 14. Operational Runbook

## 14.1 Initial setup

1. Configure active default ERP connection.
2. Run ERP reference imports for all required batch types.
3. Validate reference row counts and latest batch statuses.
4. Execute sample single and bulk exports for sanity check.

## 14.2 Post-flush recovery

1. Run flush_erp_imports (global or tenant-specific as needed).
2. Immediately re-import all required ERP reference types.
3. Validate that latest completed batches exist.
4. Re-run export validation samples.

## 14.3 AI fallback rollout

1. Keep fallback disabled in baseline.
2. Enable in test environment only.
3. Compare blank rate, accuracy, and reconciliation with finance users.
4. Tune confidence threshold if needed.
5. Enable in production only after sign-off.

---

## 15. UAT Acceptance Criteria

Functional acceptance is met when all criteria below are satisfied:

1. ERP imports complete successfully for all 5 batch types.
2. Latest completed snapshots are consumed by both single and bulk export.
3. Header fields (party_account, purchase_account, due_days, due_date, currency) populate as expected from ERP-preferred rules.
4. Line fields (item_code, uom, cost_center, department, purchase_account) populate consistently between single and bulk exports.
5. After flush without re-import, reduced population is observable and expected.
6. After re-import, mapping quality is restored.
7. With AI fallback disabled, behavior is deterministic and repeatable.
8. With AI fallback enabled, only unresolved fields are enriched and low-confidence suggestions are rejected.

---

## 16. QA Test Matrix (Minimum)

1. Happy path imports for each batch type.
2. Transient ERP outage during import (retry behavior).
3. Tenant-specific import and tenant-specific flush.
4. Single export mapping verification for one invoice with PO.
5. Bulk export mapping verification across mixed invoices.
6. Blank-key regression test with variant raw_json key styles.
7. AI fallback off vs on comparison.
8. Confidence threshold rejection test for AI fallback.

---

## 17. Known Constraints

1. Mapping quality is bounded by reference data freshness and connector query coverage.
2. If fields are absent from both current snapshots and source schema, export cannot fill them without schema/query extension.
3. AI fallback is advisory for unresolved fields and is intentionally constrained to avoid nondeterministic overrides.

---

## 18. Change Log

1.0 (2026-04-24)
1. Consolidated functional view across ERP integration, imports, and export mapping.
2. Added deterministic-first export mapping agent model.
3. Added phased AI fallback controls and guardrails.
4. Added operational and UAT acceptance guidance.

1.1 (2026-04-24)
1. Added first-class system agent type documentation for export mapping.
2. Documented runtime AgentRun emission in governance history for export requests.
3. Added migration-safe rollout section with accounts.0007 and agents.0018.
4. Added reconciliation MySQL warning remediation note for models.W036 removal.
