# AP Finance Agents Functional Test Document

Version: 1.0
Last Updated: 2026-04-21
Scope: AP finance agent flows only (reconciliation + agent pipeline)

---

## 1. Purpose

This document is a tester-ready functional guide for validating AP finance agent behavior in the 3-way PO reconciliation platform.

It includes:
- End-to-end functional scenarios
- Agent-specific test cases
- Sample test data (without PDF file creation)
- Exact match, fuzzy match, and LLM-assisted behavior expectations

It excludes:
- Procurement module scenarios
- Posting module deep tests
- UI pixel-level testing

---

## 2. In-Scope Components

Primary apps/services covered:
- `apps/reconciliation/` (deterministic matching engine)
- `apps/agents/` (LLM + deterministic system agents)
- `apps/tools/registry/tools.py` (PO/GRN/vendor/invoice tools)
- `apps/cases/` and review routing touchpoints

Primary AP agent types covered:
- `INVOICE_UNDERSTANDING`
- `PO_RETRIEVAL`
- `GRN_RETRIEVAL`
- `RECONCILIATION_ASSIST`
- `EXCEPTION_ANALYSIS`
- `COMPLIANCE_AGENT`
- `REVIEW_ROUTING`
- `CASE_SUMMARY`
- System tail agents where applicable (`SYSTEM_REVIEW_ROUTING`, `SYSTEM_CASE_SUMMARY`)

---

## 3. Test Preconditions

1. At least one tenant exists.
2. Test users exist for these roles:
- `ADMIN`
- `AP_PROCESSOR`
- `REVIEWER`
- `FINANCE_MANAGER`
3. Core master data is loaded for vendors, POs, and GRNs.
4. Reconciliation config exists with default tolerances:
- Quantity tolerance: 2%
- Price tolerance: 1%
- Amount tolerance: 1%
- Auto-close quantity tolerance: 5%
- Auto-close price tolerance: 3%
- Auto-close amount tolerance: 3%
5. Agent definitions and tool definitions are seeded.
6. For async validation, Celery worker is running. For local deterministic validation, eager mode is acceptable.

Optional precondition for LLM-assisted line matching test:
- A custom `LineMatchLLMFallbackService` implementation is plugged in. By default, fallback returns `None` and no LLM line-resolution occurs.

---

## 4. Sample Master Test Data

Use this as a reusable test pack. You can load via admin/API/fixtures.

### 4.1 Vendors

| Vendor ID | Code | Name | Alias |
|---|---|---|---|
| 101 | VEND-ALPHA | Alpha Cooling Solutions Pvt Ltd | Alpha Cooling |
| 102 | VEND-BETA | Beta Facility Services LLP | Beta Services |
| 103 | VEND-GAMMA | Gamma Logistics and Supply | Gamma Supply |

### 4.2 Purchase Orders

| PO Number | Vendor ID | Currency | PO Date | Total Amount | Notes |
|---|---|---|---|---:|---|
| PO-1001 | 101 | INR | 2026-04-10 | 500000.00 | Stock/goods style PO, should route 3-way by policy/heuristic |
| PO-1002 | 102 | INR | 2026-04-11 | 120000.00 | Service style PO, should route 2-way by policy/heuristic |
| PO-1003 | 103 | INR | 2026-04-12 | 250000.00 | Used for mismatch and unresolved cases |

### 4.3 PO Line Items

| PO Number | Line | Item Code | Description | Qty | UOM | Unit Price | Line Amount |
|---|---:|---|---|---:|---|---:|---:|
| PO-1001 | 1 | AC-ODU-01 | VRF Outdoor Unit 10HP | 2 | NOS | 120000.00 | 240000.00 |
| PO-1001 | 2 | AC-IDU-01 | VRF Indoor Cassette Unit 2TR | 6 | NOS | 30000.00 | 180000.00 |
| PO-1001 | 3 | AC-INST-01 | Installation and Commissioning | 1 | LOT | 80000.00 | 80000.00 |
| PO-1002 | 1 | SV-MAINT-01 | Quarterly HVAC Preventive Maintenance | 4 | QTR | 30000.00 | 120000.00 |
| PO-1003 | 1 | LOG-COLD-01 | Cold Chain Logistics Service | 10 | TRIP | 25000.00 | 250000.00 |

### 4.4 GRNs (for 3-way tests)

| GRN Number | PO Number | Receipt Date | Status |
|---|---|---|---|
| GRN-1001-A | PO-1001 | 2026-04-14 | RECEIVED |

### 4.5 GRN Line Items

| GRN Number | Line | Item Code | Qty Received | Qty Accepted | Qty Rejected |
|---|---:|---|---:|---:|---:|
| GRN-1001-A | 1 | AC-ODU-01 | 2 | 2 | 0 |
| GRN-1001-A | 2 | AC-IDU-01 | 6 | 6 | 0 |
| GRN-1001-A | 3 | AC-INST-01 | 1 | 1 | 0 |

---

## 5. Sample Invoice Payloads (No PDF Required)

You can create invoice records directly via API/admin with these fields.

### INV-EXACT-001 (Exact Match Candidate)

Header:
- invoice_number: `INV-EXACT-001`
- vendor: `Alpha Cooling Solutions Pvt Ltd` (Vendor 101)
- po_number: `PO-1001`
- currency: `INR`
- subtotal: `500000.00`
- tax_amount: `90000.00`
- total_amount: `590000.00`
- extraction_confidence: `0.95`

Lines:
1. `AC-ODU-01`, `VRF Outdoor Unit 10HP`, qty `2`, unit_price `120000.00`
2. `AC-IDU-01`, `VRF Indoor Cassette Unit 2TR`, qty `6`, unit_price `30000.00`
3. `AC-INST-01`, `Installation and Commissioning`, qty `1`, unit_price `80000.00`

### INV-FUZZY-001 (Deterministic Fuzzy Match Candidate)

Header:
- invoice_number: `INV-FUZZY-001`
- vendor: `Alpha Cooling` (alias of Vendor 101)
- po_number: `PO-1001`
- currency: `INR`
- subtotal: `500500.00`
- tax_amount: `90090.00`
- total_amount: `590590.00`
- extraction_confidence: `0.93`

Lines (intentional text variation):
1. item_code blank, description `VRF Outdor Unit 10 HP` (typo: Outdor), qty `2`, unit_price `120100.00`
2. item_code blank, description `VRF Indoor Casette Unit 2TR` (typo: Casette), qty `6`, unit_price `29980.00`
3. item_code blank, description `Install & Commissioning`, qty `1`, unit_price `80400.00`

### INV-LOWCONF-001 (Low Extraction Confidence)

Header:
- invoice_number: `INV-LOWCONF-001`
- vendor: `Beta Facility Services LLP`
- po_number: `PO-1002`
- currency: `INR`
- total_amount: `120000.00`
- extraction_confidence: `0.55`

Line:
1. `SV-MAINT-01`, `Quarterly HVAC Preventive Maintenance`, qty `4`, unit_price `30000.00`

### INV-PO-MISSING-001 (PO Not Found)

Header:
- invoice_number: `INV-PO-MISSING-001`
- vendor: `Gamma Logistics and Supply`
- po_number: `PO-9999` (does not exist)
- currency: `INR`
- total_amount: `250000.00`
- extraction_confidence: `0.91`

Line:
1. `LOG-COLD-01`, `Cold Chain Logistics Service`, qty `10`, unit_price `25000.00`

### INV-GRN-MISSING-001 (3-way GRN Missing)

Header:
- invoice_number: `INV-GRN-MISSING-001`
- vendor: `Alpha Cooling Solutions Pvt Ltd`
- po_number: `PO-1001`
- currency: `INR`
- total_amount: `590000.00`
- extraction_confidence: `0.94`

Lines same as `INV-EXACT-001`

Precondition mutation for this test only:
- Temporarily remove/disable GRN `GRN-1001-A` or create a separate PO with no GRN.

### INV-UNRESOLVED-LINE-001 (Ambiguous/Unresolved)

Header:
- invoice_number: `INV-UNRESOLVED-LINE-001`
- vendor: `Alpha Cooling Solutions Pvt Ltd`
- po_number: `PO-1001`
- currency: `INR`
- total_amount: `590000.00`
- extraction_confidence: `0.92`

Lines:
1. description `Cooling Unit Package` (too generic), qty `2`, unit_price `120000.00`
2. description `Cooling Unit Package` (same generic), qty `6`, unit_price `30000.00`

This should create ambiguity in deterministic line scoring.

---

## 6. Functional Test Cases

### Legend
- Match Status values: `MATCHED`, `PARTIAL_MATCH`, `UNMATCHED`, `REQUIRES_REVIEW`
- Agent pipeline triggers only when deterministic outcome is not cleanly MATCHED/auto-closed.

### TC-AP-001 Exact Header + Line Match (Deterministic Exact)

Objective:
- Validate exact deterministic match without agent intervention.

Input:
- Invoice: `INV-EXACT-001`

Steps:
1. Ensure invoice is `READY_FOR_RECON`.
2. Trigger reconciliation.
3. Observe `ReconciliationResult` and `exceptions`.
4. Check whether agent pipeline was triggered.

Expected:
- PO found (`PO-1001`).
- Mode resolved to `THREE_WAY` for goods/stock context.
- Header comparisons within tolerance.
- All lines matched with high confidence.
- GRN checks pass.
- Final status: `MATCHED`.
- No agent orchestration run for this result.

---

### TC-AP-002 Deterministic Fuzzy Line Match

Objective:
- Validate typo/variant descriptions still match through deterministic scoring.

Input:
- Invoice: `INV-FUZZY-001`

Expected:
- Vendor alias resolution succeeds (`Alpha Cooling` -> Vendor 101).
- Deterministic line matching uses token/fuzzy signals and selects correct PO lines.
- Result should be `MATCHED` or `PARTIAL_MATCH` depending on tolerance deltas.
- If all values within strict tolerance: `MATCHED`.
- If minor breaches but within auto-close band: `PARTIAL_MATCH` with auto-close behavior.

Notes for tester:
- This is not an LLM line match.
- It is deterministic weighted scoring in `LineMatchService`.

---

### TC-AP-003 Auto-Close for Partial Within Wider Band

Objective:
- Validate policy auto-close for partial discrepancies within wider thresholds.

Input:
- Use `INV-FUZZY-001` with slight deltas within 5% qty / 3% price / 3% amount.

Expected:
- Deterministic status initially `PARTIAL_MATCH`.
- Policy engine flags as within auto-close band.
- Agent plan sets `skip_agents=True` and `auto_close=True`.
- Case does not go to manual review queue.

---

### TC-AP-004 Partial Outside Auto-Close -> Agent Pipeline

Objective:
- Validate escalation from deterministic to agentic flow.

Input:
- Copy `INV-FUZZY-001` but increase one line price by +7%.

Expected:
- Deterministic `PARTIAL_MATCH` outside auto-close band.
- Agent pipeline executes.
- Typical sequence includes `RECONCILIATION_ASSIST`, `EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, `CASE_SUMMARY`.
- Recommendation created for AP review.

---

### TC-AP-005 PO Not Found -> PO Retrieval Agent

Objective:
- Validate PO missing path and PO retrieval attempt.

Input:
- Invoice: `INV-PO-MISSING-001`

Expected:
- Deterministic classification: `UNMATCHED` with `PO_NOT_FOUND` exception.
- Agent plan includes `PO_RETRIEVAL`.
- `po_lookup` tool call appears in `ToolCall` records.
- If no PO recovered: routed to AP review with recommendation.

---

### TC-AP-006 3-Way GRN Missing -> GRN Retrieval Agent

Objective:
- Validate GRN retrieval in 3-way mode.

Input:
- Invoice: `INV-GRN-MISSING-001` with missing GRN precondition.

Expected:
- Deterministic result not `MATCHED`; GRN-related exception exists.
- Agent plan includes `GRN_RETRIEVAL` (3-way mode only).
- `grn_lookup` tool call appears.
- If GRN still missing, recommendation routes to AP review or escalation.

---

### TC-AP-007 Two-Way Mode Must Ignore GRN

Objective:
- Validate mode-aware suppression of GRN checks.

Input:
- Service PO `PO-1002` and service invoice matching that PO.

Expected:
- Mode resolved to `TWO_WAY`.
- No GRN exceptions generated.
- `GRN_RETRIEVAL` agent not planned.
- Reconciliation depends only on invoice vs PO fields.

---

### TC-AP-008 Low Extraction Confidence -> Invoice Understanding Agent

Objective:
- Validate low-confidence routing behavior.

Input:
- Invoice: `INV-LOWCONF-001` (`extraction_confidence=0.55`).

Expected:
- Deterministic classification: `REQUIRES_REVIEW`.
- Agent plan includes `INVOICE_UNDERSTANDING` before analysis/routing tail.
- Reviewer summary is generated from `EXCEPTION_ANALYSIS` path.

---

### TC-AP-009 Duplicate Invoice Handling

Objective:
- Validate duplicate invoice protection.

Input:
1. Create and reconcile `INV-EXACT-001`.
2. Create second invoice with same invoice_number, vendor, and amount.

Expected:
- Duplicate flag set (`is_duplicate=True` or duplicate exception path).
- Classification should route to review (`REQUIRES_REVIEW`).
- Agent recommendation should not auto-close duplicate-risk cases.

---

### TC-AP-010 Currency Mismatch Exception

Objective:
- Validate currency mismatch exception creation.

Input:
- Invoice against `PO-1001` but with `currency=USD` instead of `INR`.

Expected:
- `CURRENCY_MISMATCH` exception generated.
- Final status should not be clean `MATCHED`.
- Agent analysis should include exception rationale and reviewer action.

---

### TC-AP-011 Tax Mismatch Exception

Objective:
- Validate tax variance exception path.

Input:
- Use `INV-EXACT-001` baseline but set tax_amount materially different.

Expected:
- `TAX_MISMATCH` exception generated.
- Agent plan may include `COMPLIANCE_AGENT` depending on policy/exception set.

---

### TC-AP-012 LLM-Assisted Line Match (Optional Feature Test)

Objective:
- Validate optional LLM fallback for ambiguous line matching.

Input:
- Invoice: `INV-UNRESOLVED-LINE-001`

Expected in default system configuration:
- Deterministic scorer marks line as `AMBIGUOUS` or `UNRESOLVED`.
- No LLM line-resolution occurs.
- Exception path to review/agents proceeds.

Expected only if custom fallback is enabled:
- LLM fallback service returns selected PO line.
- Match method includes `LLM_FALLBACK` in line metadata.
- Confidence and rationale captured.

Tester note:
- This test is pass/fail based on environment configuration. Record whether fallback service is enabled before executing.

---

### TC-AP-013 Tool Authorization Guardrail

Objective:
- Validate RBAC enforcement for agent tool calls.

Input:
- Trigger agent flow using a user lacking `purchase_orders.view` or `grns.view`.

Expected:
- Tool authorization denied by guardrail.
- Denial captured in audit events.
- Agent should not fabricate evidence; should return uncertainty/routing recommendation.

---

### TC-AP-014 Recommendation Routing Quality

Objective:
- Validate recommendation types map correctly to outcomes.

Input:
- Run scenarios from TC-AP-004, 005, 006, 008.

Expected:
- Recommendation types are one of:
  - `AUTO_CLOSE`
  - `SEND_TO_AP_REVIEW`
  - `SEND_TO_VENDOR_CLARIFICATION`
  - `ESCALATE_TO_MANAGER`
  - `REPROCESS_EXTRACTION`
- `DecisionLog` includes rationale and confidence.

---

### TC-AP-015 End-to-End Case Timeline Completeness

Objective:
- Validate audit and trace completeness across deterministic + agentic path.

Input:
- Execute TC-AP-005 or TC-AP-006 end-to-end.

Expected:
- `AuditEvent` has key events for reconciliation start/end and agent actions.
- `AgentRun`, `AgentStep`, and `ToolCall` records exist for executed agents.
- Governance timeline is chronologically complete and role-aware.

---

## 7. Exact vs Fuzzy vs LLM Matching - Tester Cheat Sheet

### Exact Match Should Work When
- Item code matches exactly.
- Description text is exact or near-exact.
- Qty/price/amount are within strict tolerance.
- Output target: `MATCHED` without agent pipeline.

### Fuzzy Match Should Work When
- Item code is missing or differs.
- Description has typos or synonym-like phrase shifts.
- Token/fuzzy similarity remains strong enough for deterministic selection.
- Output target: deterministic line selection still succeeds.

### LLM Match Should Work When
- Only if custom line-match fallback service is enabled.
- Deterministic candidate ranking is ambiguous/unresolved.
- Fallback returns selected candidate with rationale.
- Output target: line metadata marks method `LLM_FALLBACK`.

Default product behavior note:
- Base fallback implementation is a no-op, so LLM line matching is not active out of the box.

---

## 8. Suggested Execution Order for Testers

1. TC-AP-001 (baseline exact)
2. TC-AP-002 (fuzzy deterministic)
3. TC-AP-003 (partial auto-close)
4. TC-AP-004 (partial agentic)
5. TC-AP-005 (PO missing)
6. TC-AP-006 (GRN missing in 3-way)
7. TC-AP-007 (2-way suppression of GRN)
8. TC-AP-008 (low extraction confidence)
9. TC-AP-009 to 011 (risk/compliance paths)
10. TC-AP-012 (optional LLM fallback)
11. TC-AP-013 to 015 (guardrail + governance completeness)

---

## 9. Test Evidence Checklist

For each executed test case, capture:
- Invoice ID and ReconciliationResult ID
- Match status and reconciliation mode
- Exception list with severity
- Agent plan and actual agents executed
- Tool calls made (`po_lookup`, `grn_lookup`, etc.)
- Final recommendation type and confidence
- Review assignment created (yes/no)
- Relevant audit events present (yes/no)

---

## 10. Exit Criteria for AP Finance Agent Functional Sign-Off

Minimum pass criteria:
1. Baseline exact and fuzzy deterministic scenarios pass.
2. PO/GRN missing scenarios trigger expected agent + tool behavior.
3. Low-confidence and duplicate/risk scenarios route to review correctly.
4. RBAC guardrail denial behavior is fail-closed.
5. Governance evidence (AgentRun/ToolCall/AuditEvent/DecisionLog) is complete for at least one complex case.

Optional criteria:
- LLM fallback line matching validated only in environments where fallback service is explicitly enabled.
