# ERP Scenario Expected Outcomes

This document defines the expected outcomes for the ERP seed scenarios added in [imports_formats/azure_test_setup.sql](c:/3-way-po-recon/imports_formats/azure_test_setup.sql).

Use it after loading the Azure SQL test data to validate three layers:

1. ERP connector lookup behavior.
2. Reconciliation classification and exceptions.
3. Agent-planning behavior from the resulting exception pattern.

Source of truth for expectations:

1. [imports_formats/azure_test_setup.sql](c:/3-way-po-recon/imports_formats/azure_test_setup.sql)
2. [docs/TEST_DOCUMENTATION.md](c:/3-way-po-recon/docs/TEST_DOCUMENTATION.md)
3. [docs/AGENT_ARCHITECTURE_COMBINED.md](c:/3-way-po-recon/docs/AGENT_ARCHITECTURE_COMBINED.md)

## Validation Scope

The SQL seed now covers these categories:

1. Existing live-case PO recovery for invoice numbers `BPPL/2026-27/033` and `SI994099283`.
2. GRN missing.
3. Over receipt.
4. Invoice exceeds received quantity.
5. Delayed receipt.
6. Auto-close tolerance band.
7. Fuzzy or ambiguous line matching.
8. Duplicate invoice detection.

## Validation Method

For each scenario, validate in this order:

1. ERP seed rows exist and are queryable by the connector.
2. The invoice is able to resolve the intended PO and, where applicable, GRN.
3. Reconciliation returns the expected `match_status` and primary exception pattern.
4. Agent planning follows the policy matrix in [docs/AGENT_ARCHITECTURE_COMBINED.md](c:/3-way-po-recon/docs/AGENT_ARCHITECTURE_COMBINED.md).

## Scenario Matrix

| Scenario ID | SQL Seed Anchor | Business Intent | Expected PO Lookup | Expected GRN Lookup | Expected Reconciliation Outcome | Expected Agent Outcome |
|---|---|---|---|---|---|---|
| LIVE-616 | PO `616`, invoice `90/26-27` | Existing real invoice with tax uplift over PO base | Exact match by `PartyRefDoc='616/2025-26'` | Exact match `ABSR0616` | `PARTIAL_MATCH`; exceptions should include `GRN_NOT_FOUND` only if app-side GRN linkage still fails, otherwise primarily amount or line-confidence variance. Current live DB previously showed `PARTIAL_MATCH` with `LINE_MATCH_LOW_CONFIDENCE`, `GRN_NOT_FOUND`, `PARTIAL_INVOICE`. | If `GRN_NOT_FOUND` remains, planner adds `GRN_RETRIEVAL`; if partial remains outside auto-close, also `RECONCILIATION_ASSIST`, then deterministic routing and summary. |
| LIVE-680 | PO `680`, invoice `BPPL/2026-27/033` | Repair current `PO_NOT_FOUND` live case by adding missing ERP PO and GRN | Exact match by `PartyRefDoc='PO-KTD-680/2025-26'` or `VoucherNo=680` | Exact match `ABSR0680` with two lines | Expected to move from `UNMATCHED` to a matched or near-matched 3-way result. Target outcome is `MATCHED` if app-side line matching accepts both pigment lines; otherwise `PARTIAL_MATCH` with no `PO_NOT_FOUND`. | `PO_RETRIEVAL` should no longer be needed once ERP data is loaded. If partial remains, planner should prefer `RECONCILIATION_ASSIST`; deterministic tail always appends routing and summary. |
| LIVE-013 | PO `13`, invoice `SI994099283` | Repair current `PO_NOT_FOUND` live case by adding missing ERP PO and GRN | Exact match by `PartyRefDoc='PO-BUR-13/2026-27'` or `VoucherNo=13` | Exact match `ABSR0013` | Expected to move from `UNMATCHED` to `MATCHED` or `PARTIAL_MATCH`, but must not remain `PO_NOT_FOUND` if connector data is loaded correctly. | `PO_RETRIEVAL` should not be planned after successful PO resolution; only downstream assist or routing agents should remain if another mismatch survives. |
| GRN-MISSING | PO `1004`, invoice `2104` | Test documented `GRN_NOT_FOUND` scenario | Exact PO match by `PartyRefDoc='SCN-GRN-MISSING/2026-01'` | No GRN rows by design | `REQUIRES_REVIEW` with `GRN_NOT_FOUND` in 3-way mode | Planner should add `GRN_RETRIEVAL`, then `EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, and `CASE_SUMMARY`. |
| OVER-RECEIPT | PO `1005`, invoice `2105` | Test goods receipt quantity exceeding order quantity | Exact PO match by `PartyRefDoc='SCN-OVER-RECEIPT/2026-01'` | GRN `ABSR1005` where `GRNQTY=850` and `ORDERQTY=800` | `PARTIAL_MATCH` with `OVER_RECEIPT` as primary exception | Planner should not add `PO_RETRIEVAL` or `GRN_RETRIEVAL`; partial outside auto-close should drive `RECONCILIATION_ASSIST`, then deterministic routing and summary. |
| INVOICE-EXCEEDS | PO `1006`, invoice `2106` | Test invoice quantity higher than received quantity | Exact PO match by `PartyRefDoc='SCN-INVOICE-EXCEEDS/2026-01'` | GRN `ABSR1006` where `GRNQTY=70` and invoice quantity is `100` | `REQUIRES_REVIEW` with `INVOICE_EXCEEDS` as primary exception | Planner should not add retrieval agents; deterministic path should route to procurement-style review recommendation because this is a receipt exception family. |
| DELAYED-RECEIPT | PO `1007`, invoice `2107` | Test late receipt after PO date | Exact PO match by `PartyRefDoc='SCN-DELAYED-RECEIPT/2026-01'` | GRN `ABSR1007` exists and is intentionally later than PO operationally | `PARTIAL_MATCH` with `DELAYED_RECEIPT` | Planner should avoid retrieval agents and prefer `RECONCILIATION_ASSIST` plus deterministic routing and summary. |
| AUTO-CLOSE | PO `1008`, invoice `2108` | Test wider tolerance-band closure | Exact PO match by `PartyRefDoc='SCN-AUTO-CLOSE/2026-01'` | Exact GRN `ABSR1008` | `PARTIAL_MATCH` at raw reconciliation stage, but within auto-close tolerance band because invoice quantity is `1002` vs PO/GRN `1000`, a `0.2%` delta | Policy engine should set `skip_agents=True` and `auto_close=True`; final workflow target is auto-close with no LLM retrieval or assist agents. |
| LLM-FUZZY | PO `1010`, invoice `2110` | Test ambiguous, description-driven line matching across two similar PO lines | Exact PO match by `PartyRefDoc='SCN-LLM-FUZZY/2026-01'` | Exact GRN `ABSR1010` with two similar descriptions | Expected result is either `PARTIAL_MATCH` with `LINE_MATCH_LOW_CONFIDENCE` or another ambiguity exception, unless the deterministic matcher confidently resolves both lines. This scenario is intentionally designed to stress line matching. | If ambiguity survives reconciliation, planner should choose `RECONCILIATION_ASSIST`; no retrieval agents should be necessary because PO and GRN exist. |
| DUPLICATE-INVOICE | Payments `2001` and `2003` | Test duplicate invoice detection by supplier invoice number | Not applicable for PO lookup validation | Not applicable | Duplicate check should return two records for supplier invoice `INV-ACME-2026-0045` under `ACME Supplies Pvt Ltd`; reconciliation or posting duplicate guard should treat this as a duplicate signal | Agents are not the primary validator here. Expected operational outcome is a duplicate warning before downstream acceptance. |

## Scenario Details

### 1. LIVE-680 Expected Outcome

Purpose: convert a currently unresolved production-like case into a resolvable ERP-backed case.

Expected validations:

1. `po_lookup` returns PO `680` when queried with `PO-KTD-680/2025-26`.
2. `grn_lookup` returns `ABSR0680` with two lines.
3. `PO_NOT_FOUND` disappears from reconciliation exceptions.
4. The result should improve from `UNMATCHED` to either `MATCHED` or `PARTIAL_MATCH`.
5. If not fully matched, remaining exceptions should be line-match or amount-related, not document-retrieval related.

### 2. LIVE-013 Expected Outcome

Purpose: convert the Azelis case from ERP-missing to ERP-resolvable.

Expected validations:

1. `po_lookup` returns PO `13` for `PO-BUR-13/2026-27`.
2. `grn_lookup` returns `ABSR0013`.
3. `PO_NOT_FOUND` disappears.
4. Final classification should no longer be `UNMATCHED` because of missing PO.

### 3. GRN-MISSING Expected Outcome

Purpose: confirm documented `GRN_NOT_FOUND` behavior in three-way matching.

Expected validations:

1. PO resolves successfully.
2. GRN query returns zero rows.
3. Reconciliation classification is `REQUIRES_REVIEW`.
4. Exception list contains `GRN_NOT_FOUND`.
5. Planner includes `GRN_RETRIEVAL`.

### 4. OVER-RECEIPT Expected Outcome

Purpose: validate receipt quantity higher than PO quantity.

Expected validations:

1. PO quantity is `800`.
2. GRN quantity is `850`.
3. Invoice quantity is `850`.
4. Reconciliation raises `OVER_RECEIPT`.
5. Final status is `PARTIAL_MATCH` rather than document-not-found states.

### 5. INVOICE-EXCEEDS Expected Outcome

Purpose: validate invoice quantity higher than goods received.

Expected validations:

1. PO quantity is `100`.
2. GRN quantity is `70`.
3. Invoice quantity is `100`.
4. Reconciliation raises `INVOICE_EXCEEDS`.
5. Status is `REQUIRES_REVIEW`.

### 6. DELAYED-RECEIPT Expected Outcome

Purpose: validate late receipt scenario.

Expected validations:

1. PO exists.
2. GRN exists.
3. Receipt timing causes `DELAYED_RECEIPT`.
4. Final status is `PARTIAL_MATCH`.

### 7. AUTO-CLOSE Expected Outcome

Purpose: validate policy-engine auto-close behavior.

Expected validations:

1. PO and GRN both resolve.
2. Variance is minor and inside auto-close thresholds.
3. Raw reconciliation may still be `PARTIAL_MATCH`.
4. Policy engine marks the case auto-close eligible.
5. No LLM agents should run for the final workflow path.

### 8. LLM-FUZZY Expected Outcome

Purpose: validate ambiguous line-matching behavior where PO and GRN exist but semantic resolution is still needed.

Expected validations:

1. PO and GRN both resolve successfully.
2. Two PO lines exist with near-identical descriptions.
3. Invoice descriptions are intentionally close to both seeded PO descriptions.
4. Expected output is ambiguity or low-confidence line matching unless the deterministic line scorer fully resolves it.
5. If unresolved, the correct downstream agent is `RECONCILIATION_ASSIST`, not retrieval agents.

### 9. DUPLICATE-INVOICE Expected Outcome

Purpose: validate duplicate invoice detection independently from PO or GRN matching.

Expected validations:

1. `Transaction_Payments_Table` contains two rows with `SupplierInvNo='INV-ACME-2026-0045'`.
2. Vendor name is the same on both rows.
3. Duplicate query returns count `2`.
4. Any operational flow using duplicate checks should flag this invoice number as already present.

## Validation Checklist

Mark each item as pass or fail after loading the SQL script.

| Check | Expected Result |
|---|---|
| Scenario-tagged POs exist | POs `13`, `680`, `1004`, `1005`, `1006`, `1007`, `1008`, `1010` are present |
| GRN gap exists for `1004` | Zero GRN rows |
| Over-receipt exists for `1005` | `GRNQTY > ORDERQTY` |
| Under-receipt exists for `1006` | `GRNQTY < invoice quantity` |
| Auto-close variance exists for `1008` | Invoice quantity `1002`, PO and GRN quantity `1000` |
| Fuzzy lines exist for `1010` | Two similar PO lines and two similar invoice lines |
| Duplicate payments exist | Two rows for `INV-ACME-2026-0045` |
| LIVE-680 PO resolves | No `PO_NOT_FOUND` after ERP-backed re-run |
| LIVE-013 PO resolves | No `PO_NOT_FOUND` after ERP-backed re-run |

## Notes

1. `LIVE-616` is included because it already exists in the seeded data and is useful as a known reference invoice, but its exact final exceptions can vary depending on how the app maps tax-inclusive invoice totals to PO and GRN data.
2. `LIVE-680` and `LIVE-013` are expected to improve from the current live `PO_NOT_FOUND` state once the ERP seed is loaded and the invoices are reprocessed.
3. The `LLM-FUZZY` scenario is intentionally non-binary: it is designed to validate that the system escalates to assist-style reasoning when deterministic matching confidence is insufficient.
4. The `AUTO-CLOSE` scenario validates post-reconciliation policy behavior, not just raw match computation.