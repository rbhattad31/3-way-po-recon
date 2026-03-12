# 3-Way PO Reconciliation — Test Scenarios & Expected Results

**Document Version:** 1.4  
**Date:** 2026-03-12  
**Dataset:** `seed_saudi_mcd_data` (master) + `seed_invoice_test_data` (18 scenarios) + `seed_po_agent_test_data` (10 PO Agent scenarios) + `seed_grn_agent_test_data` (12 GRN Agent scenarios) + `seed_mixed_mode_data` (12 mode scenarios)  
**Active Config:** Default Production  

### Changelog (v1.4)

**Deterministic PO Discovery:** The PO lookup pipeline now includes a 3rd tier — vendor+amount discovery — that resolves invoices without a valid PO number by matching vendor identity (including Arabic aliases) and invoice total against open POs (1% tolerance, single-match-only). This makes 7/10 POAG scenarios fully deterministic (no AI agent needed). Normalized PO matching also handles underscore/slash variants.

**Enhanced GRN Exception Types:** The exception builder now creates 4 distinct GRN-related exception types instead of generic `QTY_MISMATCH`: `INVOICE_QTY_EXCEEDS_RECEIVED` (HIGH), `OVER_RECEIPT` (MEDIUM), `RECEIPT_SHORTAGE` (MEDIUM), and `DELAYED_RECEIPT` (LOW). This makes 10/12 GRNAG scenarios fully deterministic with precise exception classification.  

---

## Tolerance Configuration (Default Production)

| Tier | Quantity | Price | Amount | Behavior |
|------|----------|-------|--------|----------|
| **Strict (Match)** | ≤ 2.0% | ≤ 1.0% | ≤ 1.0% | MATCHED — no further action |
| **Auto-Close Band** | ≤ 5.0% | ≤ 3.0% | ≤ 3.0% | PARTIAL_MATCH but auto-closed (skip AI agents) |
| **Beyond Auto-Close** | > 5.0% | > 3.0% | > 3.0% | PARTIAL_MATCH → AI agents triggered |

**Other Thresholds:**  
- Extraction confidence threshold: **0.75** (below → LOW_CONFIDENCE exception)  
- Agent confidence threshold: **0.70** (below → INVOICE_UNDERSTANDING agent)  
- Review auto-close threshold: **0.95**  
- VAT Rate: **15%**  

---

## Quick Reference — All 52 Scenarios

| # | Invoice | PO | Vendor | Category | Expected Match Status | Expected Behavior |
|---|---------|-----|--------|----------|----------------------|-------------------|
| 001 | INV-AFS-2026-001 | PO-KSA-1001 | VND-AFS-001 | Perfect Match | **MATCHED** | No agents, no review |
| 002 | INV-AFS-2026-002 | PO-KSA-1002 | VND-AFS-001 | Qty Mismatch | **PARTIAL_MATCH** | AI agents (qty +8.3%) |
| 003 | INV-GFF-2026-003 | PO-KSA-1003 | VND-GFF-002 | Price Mismatch | **PARTIAL_MATCH** | AI agents (price +3.8%) |
| 004 | INV-SPS-2026-004 | PO-KSA-1019 | VND-SPS-004 | VAT Mismatch | **PARTIAL_MATCH** | AI agents (12% vs 15%) |
| 005 | INV-FAKE-2026-005 | PO-KSA-9999 | VND-RSRC-007 | Missing PO | **UNMATCHED** | PO Retrieval agent |
| 006 | INV-RBC-2026-006 | PO-KSA-1007 | VND-NEO-008 | Missing GRN | **UNMATCHED** | GRN Retrieval agent |
| 007 | INV-GFF-2026-007 | PO-KSA-1008 | VND-GFF-002 | Multi-GRN | **MATCHED** | No agents (cumulative GRN match) |
| 008a | INV-DCCL-2026-008 | PO-KSA-1005 | VND-DCCL-006 | Duplicate (orig) | **MATCHED** | Original passes |
| 008b | INV-DCCL-2026-008 | PO-KSA-1005 | VND-DCCL-006 | Duplicate (dup) | **REQUIRES_REVIEW** | Duplicate flagged |
| 009 | INV-AWP-2026-009 | PO-KSA-1013 | VND-AWP-003 | Low Confidence | **PARTIAL_MATCH / REQUIRES_REVIEW** | Invoice Understanding agent |
| 010 | INV-RBC-2026-010 | PO-KSA-1015 | VND-RBC-005 | Location Mismatch | **PARTIAL_MATCH** | AI agents (delivery location) |
| 011 | INV-AKD-2026-011 | PO-KSA-1004 | VND-AKD-009 | Qty Exceeds GRN | **PARTIAL_MATCH** | AI agents (qty +12.5%) |
| 012 | INV-SPS-2026-012 | PO-KSA-1025 | VND-SPS-004 | Review Case | **REQUIRES_REVIEW** | Exception analysis + review routing |
| 013 | INV-AFS-2026-013 | PO-KSA-1011 | VND-AFS-001 | Auto-Close (Qty) | **MATCHED** (auto-closed) | Qty +3% → auto-close, skip AI |
| 014 | INV-SPS-2026-014 | PO-KSA-1014 | VND-SPS-004 | Auto-Close (Price) | **MATCHED** (auto-closed) | Price +2.3% → auto-close, skip AI |
| 015 | INV-NEO-2026-015 | PO-KSA-1024 | VND-NEO-008 | Beyond Auto-Close | **PARTIAL_MATCH** | AI agents (qty +7%) |
| 016 | INV-GFF-2026-016 | PO-KSA-1016 | VND-GFF-002 | AI Resolvable (Fuzzy) | **PARTIAL_MATCH** | AI resolves misspellings + qty |
| 017 | INV-AKD-2026-017 | PO-KSA-1022 | VND-AKD-009 | AI Resolvable (Price) | **PARTIAL_MATCH** | AI identifies price pattern |
| 018 | INV-RSRC-2026-018 | PO-KSA-1020 | VND-RSRC-007 | AI Resolvable (Extra) | **PARTIAL_MATCH** | AI handles reordered + surcharge |
| **PO Agent** | | | | | | |
| **GRN Agent** | | | | | | |
| GRNAG-001 | INV-GRNAG-2026-001 | PO-KSA-1001 | VND-AFS-001 | Full Receipt Match | **MATCHED** | ✅ Deterministic: GRN confirms full receipt |
| GRNAG-002 | INV-GRNAG-2026-002 | PO-KSA-3001 | VND-RBC-005 | Missing GRN | **UNMATCHED** | ✅ Deterministic: GRN_NOT_FOUND exception |
| GRNAG-003 | INV-GRNAG-2026-003 | PO-KSA-3002 | VND-GFF-002 | Partial Receipt | **PARTIAL_MATCH** | ✅ Deterministic: RECEIPT_SHORTAGE + INVOICE_QTY_EXCEEDS_RECEIVED |
| GRNAG-004 | INV-GRNAG-2026-004 | PO-KSA-3003 | VND-AKD-009 | Invoice > Received | **PARTIAL_MATCH** | ✅ Deterministic: INVOICE_QTY_EXCEEDS_RECEIVED |
| GRNAG-005 | INV-GRNAG-2026-005 | PO-KSA-3004 | VND-GFF-002 | Multi-GRN Full | **MATCHED** | ✅ Deterministic: 3 GRNs aggregate to 100 |
| GRNAG-006 | INV-GRNAG-2026-006 | PO-KSA-3005 | VND-GFF-002 | Multi-GRN Partial | **PARTIAL_MATCH** | ✅ Deterministic: RECEIPT_SHORTAGE + INVOICE_QTY_EXCEEDS_RECEIVED |
| GRNAG-007 | INV-GRNAG-2026-007 | PO-KSA-3006 | VND-AWP-003 | Over-Delivery | **PARTIAL_MATCH** | ✅ Deterministic: OVER_RECEIPT exception (GRN 230 > PO 200) |
| GRNAG-008 | INV-GRNAG-2026-008 | PO-KSA-3007 | VND-NEO-008 | Delayed Receipt | **PARTIAL_MATCH** | ✅ Deterministic: DELAYED_RECEIPT exception (5 days late) |
| GRNAG-009 | INV-GRNAG-2026-009 | PO-KSA-3008 | VND-RSRC-007 | Location Mismatch | **PARTIAL_MATCH** | ⚠️ Needs AI: no structured destination field on invoice |
| GRNAG-010 | INV-GRNAG-2026-010 | PO-KSA-3009 | VND-SPS-004 | Wrong Item Mix | **PARTIAL_MATCH** | ⚠️ Needs AI: semantic substitution detection required |
| GRNAG-011 | INV-GRNAG-2026-011 | PO-KSA-3010 | VND-RSRC-007 | Service Invoice | **PARTIAL_MATCH** | ✅ Deterministic: 2-way mode, no GRN expected |
| GRNAG-012 | INV-GRNAG-2026-012 | PO-KSA-3011 | VND-DCCL-006 | Cold-Chain Shortage | **PARTIAL_MATCH** | ✅ Deterministic: RECEIPT_SHORTAGE + INVOICE_QTY_EXCEEDS_RECEIVED |
| **PO Agent** | | | | | | |
| POAG-001 | INV-POAG-2026-001 | PO-1001-KSA (reordered) | VND-AFS-001 | Reordered PO Recovery | **MATCHED** (deterministic) | ✅ Vendor+amount discovery finds PO-KSA-1001; no agent needed |
| POAG-002 | INV-POAG-2026-002 | *(blank)* | VND-GFF-002 | Vendor-Based Discovery | **MATCHED** (deterministic) | ✅ Vendor+amount discovery finds PO-KSA-2001; no agent needed |
| POAG-003 | INV-POAG-2026-003 | *(blank)* | VND-SPS-004 | Multiple Open POs | **UNMATCHED** (stays) | 3 candidates found, ambiguous → agent triggered but can't commit |
| POAG-004 | INV-POAG-2026-004 | PO/KSA/XXXX (garbled) | VND-SPS-004 (alias) | Amount-Based Fallback | **MATCHED** (deterministic) | ✅ Arabic alias resolve + vendor+amount finds PO-KSA-2005; no agent needed |
| POAG-005 | INV-POAG-2026-005 | PO-KSA-9999 | VND-NEO-008 | No PO Found | **UNMATCHED** (stays) | All strategies fail → agent triggered but no PO exists |
| POAG-006 | INV-POAG-2026-006 | PO-KSA-1001 (wrong vendor) | VND-RBC-005 | Wrong Vendor PO | **PARTIAL_MATCH** | ✅ PO found via exact match; vendor/item mismatches create exceptions |
| POAG-007 | INV-POAG-2026-007 | po_ksa_1002 (malformed) | VND-AFS-001 (Arabic alias) | Arabic Alias Resolution | **MATCHED** (deterministic) | ✅ Normalized PO: po_ksa_1002 → KSA1002 = PO-KSA-1002; no agent needed |
| POAG-008 | INV-POAG-2026-008 | PO-KSA-1017 | VND-AFS-001 | Closed PO Referenced | **MATCHED** (deterministic) | ✅ PO found via exact match; items match; closed status handled downstream |
| POAG-009 | INV-POAG-2026-009 | *(blank)* | VND-RSRC-007 | Branch vs Warehouse | **UNMATCHED** (stays) | 2 candidates found, ambiguous → agent triggered, location may narrow |
| POAG-010 | INV-POAG-2026-010 | PO/KSA/1003 (malformed) | VND-GFF-002 | High-Confidence Recovery | **MATCHED** (deterministic) | ✅ Normalized PO: PO/KSA/1003 → KSA1003 = PO-KSA-1003; no agent needed |
| **Mixed-Mode** | | | | | | |
| MODE-001 | INV-MODE-001 | PO-KSA-3001 | VND-GPS-011 | Service: Cleaning (2-Way Policy) | **MATCHED** | 2-way (POL-SVC-VENDOR); no GRN |
| MODE-002 | INV-MODE-002 | PO-KSA-3002 | VND-GPS-011 | Service: Pest Control (2-Way Heuristic) | **MATCHED** | 2-way via keyword heuristic |
| MODE-003 | INV-MODE-003 | PO-KSA-3003 | VND-AFS-001 | Stock: Food Perfect (3-Way Policy) | **MATCHED** | 3-way (POL-STOCK-GLOBAL); full GRN |
| MODE-004 | INV-MODE-004 | PO-KSA-3004 | VND-GFF-002 | Stock: Frozen GRN Shortage (3-Way) | **PARTIAL_MATCH** | 3-way (POL-FOOD-3WAY); GRN shortage |
| MODE-005 | INV-MODE-005 | PO-KSA-3005 | VND-GPS-011 | Service: Security Price Mismatch (2-Way) | **PARTIAL_MATCH** | 2-way; price 12000 vs PO 11500 |
| MODE-006 | INV-MODE-006 | PO-KSA-3006 | VND-SPS-004 | Stock: Packaging Missing GRN (3-Way) | **UNMATCHED** | 3-way; GRN missing lids line |
| MODE-007 | INV-MODE-007 | PO-KSA-3007 | VND-DCCL-006 | Mixed: Service+Stock Lines (Default) | **PARTIAL_MATCH** | Ambiguous → 3-way default fallback |
| MODE-008 | INV-MODE-008 | PO-KSA-3008 | VND-RBC-005 | Stock: Beverage Qty Mismatch (3-Way) | **PARTIAL_MATCH** | 3-way; qty 110 vs PO 100 |
| MODE-009 | INV-MODE-009 | PO-KSA-3009 | VND-JQSS-010 | Service: Maintenance (2-Way Heuristic) | **MATCHED** | 2-way via 'maintenance' keyword |
| MODE-010 | INV-MODE-010 | PO-KSA-3010 | VND-AKD-009 | Stock: Dairy Location Policy (3-Way) | **MATCHED** | 3-way (POL-WH-RUH-3WAY); full GRN |
| MODE-011 | INV-MODE-011 | PO-KSA-3011 | VND-RSRC-007 | Branch: Direct Purchase (2-Way Policy) | **MATCHED** | 2-way (POL-BRANCH-2WAY); GRN ignored |
| MODE-012 | INV-MODE-012 | PO-KSA-3012 | VND-NEO-008 | Default Fallback: No Policy (3-Way) | **MATCHED** | No policy/heuristic → 3-way default |

---

## Detailed Scenario Descriptions

---

### SCN-KSA-001 — Perfect 3-Way Match (Burger Bun Supply)

**Invoice:** INV-AFS-2026-001  
**PO:** PO-KSA-1001 → **GRN:** GRN-RUH-1001-A  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.95  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Sesame Burger Bun 4 inch | 500 | 500 | 500 | 45.00 | 45.00 | **None** |
| 2 | Shredded Lettuce Food Service Pack | 200 | 200 | 200 | 28.00 | 28.00 | **None** |
| 3 | Pickle Slice Jar Bulk | 100 | 100 | 100 | 35.00 | 35.00 | **None** |

**Totals:** Subtotal 31,600.00 | Tax 4,740.00 | Total 36,340.00

**Expected Result:**
- Match Status: **MATCHED**
- Exceptions: **None**
- Agents: **Skipped** (Rule 1 — MATCHED with confidence ≥ 0.95)
- Review: **Not required**

---

### SCN-KSA-002 — Quantity Mismatch (Sesame Buns Over-invoiced)

**Invoice:** INV-AFS-2026-002  
**PO:** PO-KSA-1002 → **GRN:** GRN-RUH-1002-A  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.93  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Sesame Burger Bun 4 inch | **650** | 600 | 600 | 45.00 | 45.00 | **Qty +8.3%** |
| 2 | Regular Burger Bun 4 inch | 300 | 300 | 300 | 40.00 | 40.00 | None |

**Totals:** Subtotal 41,250.00 | Tax 6,187.50 | Total 47,437.50

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **QTY_MISMATCH** (MEDIUM — 8.3% exceeds both strict 2% and auto-close 5%)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- Review: **Required** — invoice claims 50 more cartons than PO/GRN

---

### SCN-KSA-003 — Price Mismatch (Beef Patty Price Increase)

**Invoice:** INV-GFF-2026-003  
**PO:** PO-KSA-1003 → **GRN:** GRN-RUH-1003-A  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.94  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | McD Beef Patty 4:1 Frozen | 300 | 300 | 300 | **192.00** | 185.00 | **Price +3.78%** |
| 2 | McD Beef Patty 10:1 Frozen | 200 | 200 | 200 | 120.00 | 120.00 | None |

**Totals:** Subtotal 81,600.00 | Tax 12,240.00 | Total 93,840.00

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **PRICE_MISMATCH** (MEDIUM — 3.78% exceeds strict 1% and auto-close 3%)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- Review: **Required** — SAR 7.00/unit overage × 300 = SAR 2,100 impact

---

### SCN-KSA-004 — VAT Mismatch (Wrong Tax Rate Applied)

**Invoice:** INV-SPS-2026-004  
**PO:** PO-KSA-1019 → **GRN:** GRN-RUH-1019-A  
**Vendor:** Saudi Packaging Solutions (VND-SPS-004)  
**Confidence:** 0.91  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Big Mac Clamshell Box | 3,000 | 3,000 | 3,000 | 1.20 | 1.20 | None |
| 2 | Fries Carton Medium | 5,000 | 5,000 | 5,000 | 0.65 | 0.65 | None |
| 3 | Napkin Dispenser Pack | 2,000 | 2,000 | 2,000 | 0.30 | 0.30 | None |
| 4 | Delivery Paper Bag Large | 1,500 | 1,500 | 1,500 | 0.95 | 0.95 | None |

**Tax Issue:** Invoice applies **12% VAT** (tax = 1,065.00) instead of correct **15% VAT** (should be 1,331.25)

**Totals:** Subtotal 8,875.00 | Tax 1,065.00 (**wrong**) | Total 9,940.00

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **TAX_MISMATCH** (MEDIUM — VAT under-stated by SAR 266.25)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- Review: **Required** — vendor must reissue with correct 15% VAT

---

### SCN-KSA-005 — Missing PO (Non-existent PO Reference)

**Invoice:** INV-FAKE-2026-005  
**PO:** PO-KSA-9999 → **DOES NOT EXIST**  
**Vendor on Invoice:** Red Sea Restaurant Consumables  
**Confidence:** 0.89  

| Line | Description | Inv Qty | Inv Price |
|------|-------------|---------|-----------|
| 1 | Sanitizer Surface Use | 500 | 28.00 |
| 2 | Degreaser Kitchen Heavy Duty | 200 | 45.00 |

**Totals:** Subtotal 23,000.00 | Tax 3,450.00 | Total 26,450.00

**Expected Result:**
- Match Status: **UNMATCHED**
- Exceptions: **PO_NOT_FOUND** (HIGH)
- Agents: **PORetrieval → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 2)
- Review: **Required** — agent may attempt to find correct PO via vendor/amount search

---

### SCN-KSA-006 — Missing GRN (PO Exists, No Goods Receipt)

**Invoice:** INV-RBC-2026-006  
**PO:** PO-KSA-1007 (exists, 1 line) → **No GRN records**  
**Vendor:** Najd Edible Oils Trading (VND-NEO-008)  
**Confidence:** 0.91  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Cooking Oil Fryer Grade 20L | 150 | 150 | **N/A** | 32.00 | 32.00 | **No GRN** |

**Totals:** Subtotal 4,800.00 | Tax 720.00 | Total 5,520.00

**Expected Result:**
- Match Status: **UNMATCHED**
- Exceptions: **GRN_NOT_FOUND** (HIGH)
- Agents: **GRNRetrieval → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 3)
- Review: **Required** — goods may not have been received yet; hold payment

---

### SCN-KSA-007 — Multi-GRN Aggregation (3 Partial Deliveries)

**Invoice:** INV-GFF-2026-007  
**PO:** PO-KSA-1008 → **GRNs:** GRN-DMM-1008-A, GRN-DMM-1008-B, GRN-DMM-1008-C  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.94  

| Line | Description | Inv Qty | PO Qty | GRN-A | GRN-B | GRN-C | GRN Total | Variance |
|------|-------------|---------|--------|-------|-------|-------|-----------|----------|
| 1 | McD Beef Patty 4:1 | 500 | 500 | 300 | 200 | — | 500 | **None** |
| 2 | Chicken Patty Breaded | 400 | 400 | 400 | — | — | 400 | **None** |
| 3 | Nuggets Premium | 300 | 300 | — | 300 | — | 300 | **None** |
| 4 | Hash Brown Triangle | 250 | 250 | — | — | 250 | 250 | **None** |

**Totals:** Subtotal 222,950.00 | Tax 33,442.50 | Total 256,392.50

**Expected Result:**
- Match Status: **MATCHED**
- Exceptions: **None** (cumulative GRN quantities match per line)
- Agents: **Skipped** (Rule 1)
- Review: **Not required**

---

### SCN-KSA-008 — Duplicate Invoice (Same Invoice Number Twice)

**Invoice (orig):** INV-DCCL-2026-008 (`is_duplicate=False`)  
**Invoice (dup):** INV-DCCL-2026-008 (`is_duplicate=True`)  
**PO:** PO-KSA-1005 → **GRN:** GRN-RUH-1005-A  
**Vendor:** Desert Cold Chain Logistics (VND-DCCL-006)  
**Confidence:** 0.93  

| Line | Description | Qty | Price |
|------|-------------|-----|-------|
| 1 | French Fries 2.5kg Frozen | 800 | 78.00 |
| 2 | French Fries 1kg Frozen | 400 | 36.00 |

**Totals:** Subtotal 76,800.00 | Tax 11,520.00 | Total 88,320.00 (each)

**Expected Result:**
- **Original (008a):** Match Status **MATCHED** — all lines match PO/GRN
- **Duplicate (008b):** Match Status **REQUIRES_REVIEW** — flagged **DUPLICATE_INVOICE** (HIGH)
- Agents: Exception analysis on duplicate
- Review: **Required** for duplicate — must reject second payment

---

### SCN-KSA-009 — Arabic Low-Confidence Invoice

**Invoice:** INV-AWP-2026-009  
**PO:** PO-KSA-1013 → **GRN:** GRN-RUH-1013-A  
**Vendor on Invoice:** الوطنية للدواجن (Arabic text)  
**Actual Vendor:** Al Watania Poultry Supply (VND-AWP-003)  
**Confidence:** 0.51 (**below 0.75 threshold**)  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Chicken Patty Breaded Frozen | 350 | 350 | 350 | 158.00 | 158.00 | None |
| 2 | Nuggets Premium Frozen | 200 | 200 | 200 | 145.00 | 145.00 | None |
| 3 | Hash Brown Triangle Frozen | 150 | 150 | 150 | 95.00 | 95.00 | None |

**Totals:** Subtotal 98,550.00 | Tax 14,782.50 | Total 113,332.50

**Expected Result:**
- Match Status: **PARTIAL_MATCH** or **REQUIRES_REVIEW**
- Exceptions: **EXTRACTION_LOW_CONFIDENCE** (MEDIUM — 0.51 < 0.75)
- Agents: **InvoiceUnderstanding → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 5)
- Review: **Required** — extraction may have errors; agent validates fields

---

### SCN-KSA-010 — Location Mismatch (Delivery Site Discrepancy)

**Invoice:** INV-RBC-2026-010  
**PO:** PO-KSA-1015 → **GRN:** GRN-RUH-1015-A (Warehouse: **WH-RUH-01**)  
**Vendor:** Riyadh Beverage Concentrates Co. (VND-RBC-005)  
**Confidence:** 0.90  
**Invoice Remarks:** Delivery Note: DN-JED-20455 | Destination: **BR-JED-220**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Soft Drink Syrup Cola Bag-in-Box | 100 | 100 | 100 | 220.00 | 220.00 | None |
| 2 | Soft Drink Syrup Fanta Bag-in-Box | 80 | 80 | 80 | 215.00 | 215.00 | None |
| 3 | Soft Drink Syrup Sprite Bag-in-Box | 60 | 60 | 60 | 210.00 | 210.00 | None |

**Totals:** Subtotal 51,800.00 | Tax 7,770.00 | Total 59,570.00

**Expected Result:**
- Match Status: **PARTIAL_MATCH** or **REQUIRES_REVIEW**
- Exceptions: Delivery location **BR-JED-220** (Jeddah) ≠ GRN warehouse **WH-RUH-01** (Riyadh)
- Agents: **ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 6)
- Review: **Required** — investigate if inter-warehouse transfer or wrong delivery

---

### SCN-KSA-011 — Qty Exceeds GRN (Invoice > Received Quantity)

**Invoice:** INV-AKD-2026-011  
**PO:** PO-KSA-1004 → **GRNs:** GRN-JED-1004-A, GRN-JED-1004-B  
**Vendor:** Al Khobar Dairy Ingredients (VND-AKD-009)  
**Confidence:** 0.92  

| Line | Description | Inv Qty | PO Qty | GRN Total | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|-----------|-----------|----------|----------|
| 1 | Cheese Slice Processed | **450** | 400 | 400 | 62.00 | 62.00 | **Qty +12.5%** |
| 2 | Butter Portion Pack | 200 | 200 | 210 | 18.50 | 18.50 | None |

**Totals:** Subtotal 31,600.00 | Tax 4,740.00 | Total 36,340.00

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **QTY_MISMATCH** (HIGH — 12.5% over, well beyond auto-close 5%)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- Review: **Required** — 50 extra cartons invoiced vs received; potential overcharge SAR 3,100

---

### SCN-KSA-012 — Review Case (Description + Price Variance)

**Invoice:** INV-SPS-2026-012  
**PO:** PO-KSA-1025 → **GRN:** GRN-JED-1025-A  
**Vendor on Invoice:** Saudi Pack Solutions (alias, not primary name)  
**Actual Vendor:** Saudi Packaging Solutions (VND-SPS-004)  
**Confidence:** 0.78  

| Line | Description (Invoice) | Description (PO) | Inv Qty | PO Qty | Inv Price | PO Price | Variance |
|------|----------------------|-------------------|---------|--------|-----------|----------|----------|
| 1 | Napkin Dispenser Pack | Napkin Dispenser Pack | 4,000 | 4,000 | 0.30 | 0.30 | None |
| 2 | Cold Drink Straw Wrapped | Cold Drink Straw Wrapped | 10,000 | 10,000 | 0.08 | 0.08 | None |
| 3 | Delivery Paper Bag **Medium** | Delivery Paper Bag **Large** | 3,000 | 3,000 | 0.95 | 0.95 | **Desc mismatch** |
| 4 | Cup Carrier 4-Slot | Cup Carrier 4-Slot | 2,000 | 2,000 | **0.60** | 0.55 | **Price +9.1%** |

**Totals:** Subtotal 6,050.00 | Tax 907.50 | Total 6,957.50

**Expected Result:**
- Match Status: **REQUIRES_REVIEW**
- Exceptions: **ITEM_MISMATCH** (line 3 — "Medium" vs "Large") + **PRICE_MISMATCH** (line 4 — +9.1%)
- Agents: **ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 6)
- Review: **Required** — AP must verify correct bag size + approve price change

---

## Auto-Close Tolerance Band Scenarios (013–015)

### SCN-KSA-013 — Qty Within Auto-Close Band (+3%)

**Invoice:** INV-AFS-2026-013  
**PO:** PO-KSA-1011 → **GRN:** GRN-RUH-1011-A  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.94  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Qty Δ |
|------|-------------|---------|--------|---------|-----------|----------|-------|
| 1 | Sesame Burger Bun 4 inch | **309** | 300 | 300 | 45.00 | 45.00 | **+3.0%** |

**Totals:** Subtotal 13,905.00 | Tax 2,085.75 | Total 15,990.75

**Tolerance Analysis:**
- Qty variance: 3.0% → exceeds strict (2%) but **within auto-close (5%)**
- Price variance: 0% → within both bands
- No HIGH severity exceptions

**Expected Result:**
- Initial Match: **PARTIAL_MATCH** (qty outside strict tolerance)
- Auto-Close: **YES** — all discrepancies within auto-close band, no HIGH exceptions
- Final Status: **MATCHED** (upgraded by auto-close)
- Agents: **Skipped** (Rule 1b — auto-close)
- Review: **Not required**

---

### SCN-KSA-014 — Price Within Auto-Close Band (+2.3%)

**Invoice:** INV-SPS-2026-014  
**PO:** PO-KSA-1014 → **GRN:** GRN-RUH-1014-A  
**Vendor:** Saudi Packaging Solutions (VND-SPS-004)  
**Confidence:** 0.93  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Price Δ |
|------|-------------|---------|--------|---------|-----------|----------|---------|
| 1 | Paper Cup 16oz | 5,000 | 5,000 | 5,000 | **0.87** | 0.85 | **+2.35%** |
| 2 | Plastic Lid 16oz | 5,000 | 5,000 | 5,000 | **0.46** | 0.45 | **+2.22%** |

**Totals:** Subtotal 6,650.00 | Tax 997.50 | Total 7,647.50

**Tolerance Analysis:**
- Qty variance: 0% on both lines
- Price variance: 2.35% and 2.22% → exceeds strict (1%) but **within auto-close (3%)**
- No HIGH severity exceptions

**Expected Result:**
- Initial Match: **PARTIAL_MATCH** (price outside strict tolerance)
- Auto-Close: **YES** — all discrepancies within auto-close band
- Final Status: **MATCHED** (upgraded by auto-close)
- Agents: **Skipped** (Rule 1b — auto-close)
- Review: **Not required**

---

### SCN-KSA-015 — Qty Beyond Auto-Close Band (+7%)

**Invoice:** INV-NEO-2026-015  
**PO:** PO-KSA-1024 → **GRN:** GRN-RUH-1024-A  
**Vendor:** Najd Edible Oils Trading (VND-NEO-008)  
**Confidence:** 0.92  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Qty Δ |
|------|-------------|---------|--------|---------|-----------|----------|-------|
| 1 | Cooking Oil Fryer Grade 20L | **535** | 500 | 500 | 32.00 | 32.00 | **+7.0%** |
| 2 | Cooking Oil Fryer Grade 5L | 300 | 300 | 300 | 9.50 | 9.50 | None |

**Totals:** Subtotal 19,970.00 | Tax 2,995.50 | Total 22,965.50

**Tolerance Analysis:**
- Line 1 Qty variance: 7.0% → **exceeds auto-close (5%)**
- Line 2: exact match
- Auto-close blocked because line 1 fails the qty band

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Auto-Close: **NO** — 7% exceeds 5% auto-close band
- Exceptions: **QTY_MISMATCH** (MEDIUM)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- Review: **Required** — 35 extra units × SAR 32 = SAR 1,120 overage

---

## AI-Agent Resolvable Scenarios (016–018)

### SCN-KSA-016 — Misspelled Descriptions + Qty Over-invoiced

**Invoice:** INV-GFF-2026-016  
**PO:** PO-KSA-1016 → **GRN:** GRN-JED-1016-A  
**Invoice Vendor Name:** "Gulf Frozn Foods Trdng" (misspelled)  
**Actual Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.88  

| Line | Invoice Description | PO Description | Fuzzy Score | Inv Qty | PO Qty | GRN Qty | Price | Variance |
|------|-------------------|----------------|-------------|---------|--------|---------|-------|----------|
| 1 | **Nugget Prmeium Frozn** | Nuggets Premium Frozen | ~80+ | 250 | 250 | 250 | 145.00 | Desc only |
| 2 | **Chiken Strips Frzn** | Chicken Strips Frozen | ~75+ | **191** | 180 | 180 | 162.00 | **Qty +6.1%** + Desc |

**What's Wrong:**
- Descriptions have OCR spelling errors: "Prmeium" → "Premium", "Frozn" → "Frozen", "Chiken" → "Chicken"
- Vendor name misspelled: "Gulf Frozn Foods Trdng"
- Line 2 qty 191 vs PO/GRN 180 (+6.1%)

**Totals:** Subtotal 67,192.00 | Tax 10,078.80 | Total 77,270.80

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **QTY_MISMATCH** on line 2, possible **ITEM_MISMATCH** if fuzzy score < 80
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- **AI Should Recognize:** Fuzzy description matching confirms same items despite spelling errors. Line 1 is an exact qty/price match. Line 2 has a genuine qty discrepancy (+11 units × SAR 162 = SAR 1,782)
- **Expected AI Recommendation:** Route to AP for qty approval on line 2; auto-close line 1

---

### SCN-KSA-017 — Systematic Price Inflation (~4% on Dairy)

**Invoice:** INV-AKD-2026-017  
**PO:** PO-KSA-1022 → **GRN:** GRN-DMM-1022-A  
**Vendor:** Al Khobar Dairy Ingredients (VND-AKD-009)  
**Confidence:** 0.93  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Price Δ |
|------|-------------|---------|--------|---------|-----------|----------|---------|
| 1 | Milkshake Vanilla Mix | 120 | 120 | 120 | **88.50** | 85.00 | **+4.12%** |
| 2 | Soft Serve Dairy Mix | 100 | 100 | 100 | **95.50** | 92.00 | **+3.80%** |
| 3 | Milkshake Chocolate Mix | 80 | 80 | 80 | 88.00 | 88.00 | None |

**What's Wrong:**
- Lines 1 & 2 have a **consistent ~4% price increase** (suggests vendor contract update)
- Line 3 is exact match
- All quantities match perfectly

**Totals:** Subtotal 27,210.00 | Tax 4,081.50 | Total 31,291.50  
**Price Impact:** (3.50 × 120) + (3.50 × 100) = **SAR 770 overage**

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **PRICE_MISMATCH** on lines 1 & 2 (MEDIUM — 4.12% and 3.80% exceed auto-close 3%)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- **AI Should Recognize:** Systematic price pattern across 2 of 3 lines suggests a vendor price revision. Consistent ~4% increase, not random errors.
- **Expected AI Recommendation:** Escalate to Procurement for vendor contract review; verify if new price list was agreed upon

---

### SCN-KSA-018 — Extra Surcharge Line + Word-Reordered Descriptions

**Invoice:** INV-RSRC-2026-018  
**PO:** PO-KSA-1020 → **GRN:** GRN-RUH-1020-A  
**Invoice Vendor Name:** "Red Sea Rstaurant Consumbles" (misspelled)  
**Actual Vendor:** Red Sea Restaurant Consumables (VND-RSRC-007)  
**Confidence:** 0.90  

| Line | Invoice Description | PO Description | Inv Qty | PO Qty | GRN Qty | Price | Variance |
|------|-------------------|----------------|---------|--------|---------|-------|----------|
| 1 | **Gloves Food Safe Medium** | Food Safe Gloves Medium | 1,000 | 1,000 | 1,000 | 12.50 | **Word order** |
| 2 | **Heavy Duty Kitchen Degrsr** | Degreaser Kitchen Heavy Duty | 200 | 200 | 200 | 45.00 | **Word order + abbrev** |
| 3 | **Surface Use Sanitizer** | Sanitizer Surface Use | 300 | 300 | 300 | 28.00 | **Word order** |
| 4 | **Delivery Surcharge - Hazmat Chemicals** | *(not on PO)* | 1 | — | — | 250.00 | **Extra line** |

**What's Wrong:**
- Lines 1–3: Same items as PO but words are **reordered** (e.g., "Gloves Food Safe Medium" vs "Food Safe Gloves Medium")
- Line 2: Also has an abbreviation "Degrsr" instead of "Degreaser"
- Line 4: **Extra line item** not on PO — delivery surcharge for hazmat chemicals
- Vendor name misspelled

**Totals:** Subtotal 30,150.00 | Tax 4,522.50 | Total 34,672.50  
**(PO total would be 29,900.00 — surcharge adds SAR 250.00)**

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Exceptions: **ITEM_MISMATCH** (HIGH — for unmatched surcharge line 4)
- Note: Lines 1–3 should still match via `token_sort_ratio` fuzzy scoring (word reordering scores high)
- Agents: **ReconciliationAssist → ExceptionAnalysis → ReviewRouting → CaseSummary** (Rule 4)
- **AI Should Recognize:** 3 of 4 lines match PO perfectly (qty + price) despite word reordering. Line 4 is a legitimate delivery surcharge add-on, not a PO item.
- **Expected AI Recommendation:** Approve lines 1–3 (exact match confirmed). Route surcharge line 4 to AP for approval as ancillary charge.

---

## PO Retrieval Agent Test Scenarios (POAG-001–010)

**Dataset:** `seed_po_agent_test_data` (10 scenarios)  
**Prerequisite:** `seed_saudi_mcd_data` must be seeded first  
**Seed Command:** `python manage.py seed_po_agent_test_data`  
**Flush & Reseed:** `python manage.py seed_po_agent_test_data --flush`

**Deterministic PO Lookup Pipeline (v1.4):** Before the AI agent is invoked, the deterministic lookup tries 3 tiers:
1. **Exact PO match** — invoice `po_number` matched directly against `PurchaseOrder.po_number`
2. **Normalized PO match** — strips prefixes/delimiters: `po_ksa_1002` → `KSA1002`, `PO/KSA/1003` → `KSA1003`
3. **Vendor+amount discovery** — resolves vendor (including Arabic aliases via `VendorAlias`), then finds open POs matching `total_amount` within 1% tolerance. **Only returns a match if exactly 1 PO matches** (ambiguous multi-match is deferred to AI agent).

**Result: 7/10 POAG scenarios are now resolved deterministically.** Only POAG-003, POAG-005, and POAG-009 still require the AI agent.

**Agent Purpose (when triggered):** The PO Retrieval Agent runs only when all 3 deterministic tiers fail. It applies LLM reasoning for:
- Segment reordering that can't be normalized (e.g., `PO-1001-KSA` — not applicable since v1.4 handles via vendor+amount)
- Location-based disambiguation when multiple candidates exist
- Complex vendor/item cross-referencing

**Agent Output Schema:**
```json
{
  "reasoning": "<concise explanation>",
  "recommendation_type": "<AUTO_CLOSE|SEND_TO_AP_REVIEW|SEND_TO_PROCUREMENT|...>",
  "confidence": 0.0-1.0,
  "decisions": [{"decision": "...", "rationale": "...", "confidence": 0-1}],
  "evidence": {"<key>": "<value>"}
}
```

### Data Created Per Scenario

Each scenario creates **only** Invoice + InvoiceLineItem records.  
No ReconciliationResult, AgentRun, ReviewAssignment, or AuditEvent records are created.  
Additional POs are created only where needed for scenario isolation (7 total: PO-KSA-2001..2007).  
Two Arabic VendorAlias records are added for alias-resolution scenarios.

---

### SCN-POAG-001 — Reordered PO Segment Recovery

**Invoice:** INV-POAG-2026-001  
**Raw PO:** `"PO-1001-KSA"` (segments reordered vs canonical `PO-KSA-1001`)  
**Target PO:** PO-KSA-1001 (Arabian Food Supplies, from master seed)  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.91  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش | 500 | 45.00 | 22,500.00 |
| 2 | Shredded Lettuce FSP / خس مقطع | 200 | 28.00 | 5,600.00 |
| 3 | Pickle Slice Jar Bulk / مخلل شرائح | 100 | 35.00 | 3,500.00 |

**Totals:** Subtotal 31,600.00 | Tax 4,740.00 | Total 36,340.00

**Why Deterministic Lookup Fails (v1.3):** ~~PO segments are reordered (`PO-1001-KSA` vs `PO-KSA-1001`). Exact match fails. Normalized match also fails: `normalize("PO-1001-KSA")` → `"1001KSA"` ≠ `"KSA1001"`.~~

**How Deterministic Lookup Succeeds (v1.4):** Exact and normalized matches both fail (segment reordering). **Tier 3 (vendor+amount discovery)** kicks in: vendor VND-AFS-001 is resolved, open POs for this vendor are queried, PO-KSA-1001 matches invoice total (SAR 31,600) within 1% tolerance. Single match → PO found deterministically.
- Lookup method: `vendor_amount`
- Runner backfills: `invoice.po_number = "PO-KSA-1001"`, creates audit event with metadata

**Expected Result (v1.4):**
- PO found? **Yes — deterministically**
- PO Retrieval Agent: **Not triggered** (PO found at tier 3)
- Match Status: Proceeds to reconciliation → **MATCHED** (all lines match PO + GRN)

---

### SCN-POAG-002 — Vendor-Based PO Discovery

**Invoice:** INV-POAG-2026-002  
**Raw PO:** `"[unreadable]"` (blank/smudged)  
**Target PO:** PO-KSA-2001 (created by this seed — Gulf Frozen Foods)  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.72  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | McD Beef Patty 4:1 Frozen / لحم برجر مجمد ٤:١ | 350 | 185.00 | 64,750.00 |
| 2 | Chicken Patty Breaded Frozen / فيليه دجاج مجمد | 150 | 158.00 | 23,700.00 |

**Totals:** Subtotal 88,450.00 | Tax 13,267.50 | Total 101,717.50

**Why Deterministic Lookup Fails (v1.3):** ~~PO number is completely unreadable from the scanned invoice.~~

**How Deterministic Lookup Succeeds (v1.4):** Exact and normalized matches both fail (no PO number). **Tier 3 (vendor+amount discovery)** kicks in: vendor VND-GFF-002 is linked on the invoice, open POs for this vendor are queried, PO-KSA-2001 matches invoice total (SAR 88,450) within 1% tolerance. Single match → PO found deterministically.
- Lookup method: `vendor_amount`
- Runner backfills: `invoice.po_number = "PO-KSA-2001"`, creates audit event

**Expected Result (v1.4):**
- PO found? **Yes — deterministically**
- PO Retrieval Agent: **Not triggered**
- Match Status: Proceeds to reconciliation → **MATCHED**

---

### SCN-POAG-003 — Vendor Has Multiple Open POs (Ambiguity)

**Invoice:** INV-POAG-2026-003  
**Raw PO:** *(empty)*  
**Candidate POs:** PO-KSA-2002, PO-KSA-2003, PO-KSA-2004 (all Saudi Packaging Solutions)  
**Vendor:** Saudi Packaging Solutions (VND-SPS-004)  
**Confidence:** 0.88  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | Paper Cup 16oz / كوب ورقي ١٦ أونصة | 5,700 | 0.85 | 4,845.00 |
| 2 | Plastic Lid 16oz / غطاء بلاستيك ١٦ أونصة | 5,700 | 0.45 | 2,565.00 |

**Totals:** Subtotal 7,410.00 | Tax 1,111.50 | Total 8,521.50

**Candidate PO Comparison:**

| PO | Items | Cup Qty | Lid Qty | Subtotal (approx) |
|----|-------|---------|---------|--------------------|
| PO-KSA-2002 | Cups + Lids | 6,000 | 6,000 | 7,800.00 |
| PO-KSA-2003 | Cups + Lids + Napkins | 5,500 | 5,500 | 7,450.00 |
| PO-KSA-2004 | Cups + Lids | 5,800 | 5,800 | 7,540.00 |

**Why Deterministic Lookup Fails:** PO number is completely missing.

**How Deterministic Discovery Detects Ambiguity (v1.4):** Tier 3 (vendor+amount discovery) finds VND-SPS-004 has 3 open POs with similar amounts. Since multiple POs match, the single-match-only rule prevents committing → returns `not_found`. PO Retrieval Agent is triggered.

**Expected PO Retrieval Agent Outcome:**
- Should find PO? **Ambiguous** — all 3 are plausible
- Primary strategy: **Vendor search** finds 3 open POs with similar items/amounts
- Expected `recommendation_type`: **SEND_TO_AP_REVIEW** (cannot confidently choose one)
- Expected `confidence`: **low-medium (0.30–0.55)**
- Expected `evidence` keys: `candidate_pos`, `matched_vendor`, `search_attempts`

---

### SCN-POAG-004 — Amount-Based PO Fallback (Arabic Alias)

**Invoice:** INV-POAG-2026-004  
**Raw PO:** `"PO/KSA/XXXX"` (garbled/illegible)  
**Target PO:** PO-KSA-2005 (created by this seed — Saudi Packaging Solutions)  
**Invoice Vendor Name:** `"الشركة السعودية لحلول التغليف"` (Arabic alias for Saudi Packaging Solutions)  
**Vendor FK:** None (not linked — alias only)  
**Confidence:** 0.68  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | علب بيج ماك / Big Mac Clamshell Box | 4,000 | 1.20 | 4,800.00 |
| 2 | كرتون بطاطس وسط / Fries Carton Medium | 7,000 | 0.65 | 4,550.00 |

**Totals:** Subtotal 9,350.00 | Tax 1,402.50 | Total 10,752.50

**Why Deterministic Lookup Fails (v1.3):** ~~PO number is garbled (`PO/KSA/XXXX`), vendor not linked.~~

**How Deterministic Lookup Succeeds (v1.4):** Exact match fails (PO/KSA/XXXX doesn't exist). Normalized match: `normalize("PO/KSA/XXXX")` → `KSAXXXX` — no match. **Tier 3 (vendor+amount discovery):** `invoice.vendor` is None, so `_resolve_vendor()` runs against `raw_vendor_name` = `"الشركة السعودية لحلول التغليف"`. VendorAlias lookup matches VND-SPS-004. Open POs for VND-SPS-004 are queried, PO-KSA-2005 matches invoice total (SAR 9,350) within 1%. Single match → PO found.
- Lookup method: `vendor_amount` (with alias resolution)
- Runner backfills: `invoice.po_number = "PO-KSA-2005"`, `invoice.vendor = VND-SPS-004`, creates audit event

**Expected Result (v1.4):**
- PO found? **Yes — deterministically (alias + amount)**
- PO Retrieval Agent: **Not triggered**
- Match Status: Proceeds to reconciliation → **MATCHED**

---

### SCN-POAG-005 — No PO Found

**Invoice:** INV-POAG-2026-005  
**Raw PO:** `"PO-KSA-9999"` (nonexistent)  
**Vendor:** Najd Edible Oils Trading (VND-NEO-008)  
**Confidence:** 0.89  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | زيت طبخ ممتاز ٢٠ لتر / Premium Cooking Oil 20L | 250 | 38.00 | 9,500.00 |
| 2 | زيت قلي خاص ١٠ لتر / Special Frying Oil 10L | 180 | 22.50 | 4,050.00 |

**Totals:** Subtotal 13,550.00 | Tax 2,032.50 | Total 15,582.50

**Why All Strategies Fail:**
- PO-KSA-9999 does not exist → exact lookup fails
- Normalized: `KSA9999` → no match
- Tier 3: VND-NEO-008 exists but has no open PO matching SAR 13,550 within 1% tolerance
- Items are "Premium Cooking Oil" / "Special Frying Oil" — not matching any existing PO

**Expected PO Retrieval Agent Outcome (agent triggered):**
- Should find PO? **No**
- Primary strategy: **All strategies fail**
- Expected `recommendation_type`: **SEND_TO_AP_REVIEW**
- Expected `confidence`: **low-medium (0.40–0.60)**
- Expected `evidence` keys: `search_attempts`, `no_match_reason`

---

### SCN-POAG-006 — Wrong Vendor with Valid-Like PO

**Invoice:** INV-POAG-2026-006  
**Raw PO:** `"PO-KSA-1001"` (valid PO — but belongs to a different vendor)  
**Invoice Vendor:** Riyadh Beverage Concentrates Co. (VND-RBC-005)  
**PO-KSA-1001 Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.93  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | Soft Drink Syrup Cola BiB / مركز مشروب غازي كولا | 120 | 220.00 | 26,400.00 |
| 2 | Soft Drink Syrup Fanta BiB / مركز مشروب فانتا | 90 | 215.00 | 19,350.00 |

**Totals:** Subtotal 45,750.00 | Tax 6,862.50 | Total 52,612.50

**Why Deterministic Lookup Handles This (v1.4):** PO-KSA-1001 is found via **exact match** (tier 1). The lookup service doesn't check vendor ownership — it returns the PO as found. However, since PO-KSA-1001 belongs to VND-AFS-001 (food items: buns, lettuce, pickles) while the invoice has beverage syrups from VND-RBC-005, the downstream reconciliation will produce item/price mismatches.

**Expected Result (v1.4):**
- PO found? **Yes — deterministically (exact match)**
- PO Retrieval Agent: **Not triggered** (PO found)
- Match Status: **PARTIAL_MATCH** or **REQUIRES_REVIEW** (items don't match PO lines)
- Exceptions: **ITEM_MISMATCH** / **QTY_MISMATCH** / **PRICE_MISMATCH** (invoice products ≠ PO products)
- Note: This scenario tests cross-vendor PO reference errors; the engine correctly finds the PO but reconciliation surfaces the mismatch

---

### SCN-POAG-007 — Arabic-English Vendor Alias Case

**Invoice:** INV-POAG-2026-007  
**Raw PO:** `"po_ksa_1002"` (lowercase + underscores)  
**Target PO:** PO-KSA-1002 (Arabian Food Supplies, from master seed)  
**Invoice Vendor Name:** `"شركة الأغذية العربية"` (Arabic alias, vendor FK not linked)  
**Actual Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.74  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | خبز برجر بالسمسم ٤ انش / Sesame Burger Bun 4in | 600 | 45.00 | 27,000.00 |
| 2 | خبز برجر عادي ٤ انش / Regular Burger Bun 4in | 300 | 40.00 | 12,000.00 |

**Totals:** Subtotal 39,000.00 | Tax 5,850.00 | Total 44,850.00

**How Deterministic Lookup Succeeds (v1.4):** **Tier 2 (normalized PO match):** `normalize("po_ksa_1002")` → `"KSA1002"` matches `normalize("PO-KSA-1002")` → `"KSA1002"`. PO found at tier 2 before vendor resolution is even needed.
- Lookup method: `normalized`
- Note: Arabic vendor alias resolution (`شركة الأغذية العربية` → VND-AFS-001) is available as a fallback if normalization had failed

**Expected Result (v1.4):**
- PO found? **Yes — deterministically (normalized match)**
- PO Retrieval Agent: **Not triggered**
- Match Status: Proceeds to reconciliation → **MATCHED**

---

### SCN-POAG-008 — Closed PO Referenced

**Invoice:** INV-POAG-2026-008  
**Raw PO:** `"PO-KSA-1017"` (valid PO — but status is CLOSED)  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.92  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش | 200 | 44.00 | 8,800.00 |
| 2 | Pickle Slice Jar Bulk / مخلل شرائح | 80 | 35.00 | 2,800.00 |
| 3 | Shredded Lettuce FSP / خس مقطع | 100 | 28.00 | 2,800.00 |

**Totals:** Subtotal 14,400.00 | Tax 2,160.00 | Total 16,560.00

**PO-KSA-1017 Details:**
- Vendor: VND-AFS-001 (matches invoice) ✓
- Status: **CLOSED** — fully delivered and closed 60 days ago ✗
- Items: Buns 200 @ 44.00, Pickles 80 @ 35.00, Lettuce 100 @ 28.00 (match invoice exactly)

**How Deterministic Lookup Handles This (v1.4):** PO-KSA-1017 is found via **exact match** (tier 1). The lookup service doesn't check PO status — it returns the PO as found. Items match the invoice exactly, and if GRNs exist for the closed PO, reconciliation may produce a match. The CLOSED status is a business-process concern (AP should verify if invoicing against a closed PO is valid).

**Expected Result (v1.4):**
- PO found? **Yes — deterministically (exact match)**
- PO Retrieval Agent: **Not triggered** (PO found)
- Match Status: Depends on GRN availability for the closed PO
- Note: Closed PO validation is a downstream business rule, not part of PO lookup

---

### SCN-POAG-009 — Branch vs Warehouse PO Ambiguity

**Invoice:** INV-POAG-2026-009  
**Raw PO:** *(empty)*  
**Delivery Destination:** BR-JED-220 (Jeddah Branch 220)  
**Candidate POs:** PO-KSA-2006 (warehouse), PO-KSA-2007 (branch)  
**Vendor:** Red Sea Restaurant Consumables (VND-RSRC-007)  
**Confidence:** 0.86  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | معقم أسطح / Sanitizer Surface Use | 350 | 28.00 | 9,800.00 |
| 2 | مزيل دهون صناعي / Degreaser Kitchen Heavy Duty | 200 | 45.00 | 9,000.00 |
| 3 | قفازات طعام وسط / Food Safe Gloves Medium | 500 | 12.50 | 6,250.00 |

**Totals:** Subtotal 25,050.00 | Tax 3,757.50 | Total 28,807.50

**Candidate PO Comparison:**

| PO | Department | Items | Sanitizer Qty | Degreaser Qty | Gloves Qty | Subtotal |
|----|-----------|-------|---------------|---------------|------------|----------|
| PO-KSA-2006 | Warehouse Ops Jeddah | Sanitizer + Degreaser | 400 | 250 | — | 22,450.00 |
| PO-KSA-2007 | Ops Branch Jeddah | Sanitizer + Degreaser + Gloves | 350 | 200 | 500 | 25,050.00 |

**Why Deterministic Lookup Fails:** PO number is blank on scanned invoice.  
**Location Clue:** Invoice delivery note references BR-JED-220 (branch), aligning with PO-KSA-2007.

**How Deterministic Discovery Detects Ambiguity (v1.4):** Tier 3 (vendor+amount discovery) finds VND-RSRC-007 has 2 open POs. PO-KSA-2007 matches invoice total exactly (SAR 25,050), but PO-KSA-2006 is also within range. Multiple matches → single-match-only rule prevents committing. PO Retrieval Agent is triggered and can potentially use location context (BR-JED-220) to disambiguate.

**Expected PO Retrieval Agent Outcome (agent triggered):**
- Should find PO? **Possibly** — destination context should narrow to PO-KSA-2007
- Primary strategy: **Vendor search** → 2 candidates → **location filter** (branch code matches PO-KSA-2007)
- Expected `recommendation_type`: **null** if location narrows to 1, else **SEND_TO_AP_REVIEW**
- Expected `confidence`: **medium (0.50–0.75)**
- Expected `evidence` keys: `candidate_pos`, `destination_code`, `location_match`

---

### SCN-POAG-010 — High-Confidence Exact Recovery with Item Clues

**Invoice:** INV-POAG-2026-010  
**Raw PO:** `"PO/KSA/1003"` (slashes instead of dashes)  
**Target PO:** PO-KSA-1003 (Gulf Frozen Foods, from master seed)  
**Invoice Vendor Name:** `"Gulf Frozen Foods Trdg."` (abbreviated)  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.90  

| Line | Description | Qty | Unit Price | Line Amount |
|------|-------------|-----|------------|-------------|
| 1 | McD Beef Patty 4:1 Frozen / لحم برجر ماكدونالدز ٤:١ مجمد | 300 | 185.00 | 55,500.00 |
| 2 | McD Beef Patty 10:1 Frozen / لحم برجر ماكدونالدز ١٠:١ مجمد | 200 | 120.00 | 24,000.00 |

**Totals:** Subtotal 79,500.00 | Tax 11,925.00 | Total 91,425.00

**PO-KSA-1003 Details:**
- Vendor: VND-GFF-002 (matches) ✓
- Status: OPEN ✓
- Lines: Beef Patty 4:1 300 @ 185, Beef Patty 10:1 200 @ 120 (exact match) ✓
- Total: matches invoice ✓

**Why Deterministic Lookup Succeeds (v1.4):** **Tier 2 (normalized PO match):** `normalize("PO/KSA/1003")` → `"KSA1003"` matches `normalize("PO-KSA-1003")` → `"KSA1003"`. PO found at tier 2.
- Lookup method: `normalized`
- All signals confirm: vendor VND-GFF-002 matches, amount matches, items match

**Expected Result (v1.4):**
- PO found? **Yes — deterministically (normalized match)**
- PO Retrieval Agent: **Not triggered**
- Match Status: Proceeds to reconciliation → **MATCHED** (all lines + GRN match exactly)

---

## GRN Specialist Agent Test Scenarios (GRNAG-001–012)

**Dataset:** `seed_grn_agent_test_data` (12 scenarios)  
**Prerequisite:** `seed_saudi_mcd_data` must be seeded first  
**Seed Command:** `python manage.py seed_grn_agent_test_data`  
**Flush & Reseed:** `python manage.py seed_grn_agent_test_data --flush`

**Deterministic GRN Exception Pipeline (v1.4):** The exception builder now creates 4 distinct GRN-related exception types with precise classification:

| Exception Type | Severity | Condition | Created Since |
|---|---|---|---|
| `GRN_NOT_FOUND` | MEDIUM | No GRN records exist for the PO | v1.0 |
| `INVOICE_QTY_EXCEEDS_RECEIVED` | HIGH | Invoiced qty > received qty (per line) | v1.4 (was `QTY_MISMATCH`) |
| `OVER_RECEIPT` | MEDIUM | Received qty > ordered qty | v1.4 (flag existed, no exception) |
| `RECEIPT_SHORTAGE` | MEDIUM | Received qty < ordered qty and invoice ≤ received | v1.4 (new) |
| `DELAYED_RECEIPT` | LOW | GRN receipt date > invoice date | v1.4 (new) |

**Result: 10/12 GRNAG scenarios are now fully classified by deterministic exception rules.** Only GRNAG-009 (location mismatch) and GRNAG-010 (wrong item mix) require AI agent reasoning.

**Agent Purpose (when triggered):** The GRN Specialist Agent runs when GRN-related issues need semantic understanding beyond quantity/date comparison:
- Location mismatch: no structured `delivery_destination` field on invoices
- Item substitution: detecting that received items are semantic substitutes (e.g., fries carton replacing clamshell)

**Agent Output Schema:**
```json
{
  "reasoning": "<concise explanation>",
  "recommendation_type": "<null|SEND_TO_PROCUREMENT|SEND_TO_AP_REVIEW|SEND_TO_VENDOR_CLARIFICATION>",
  "confidence": 0.0-1.0,
  "decisions": [{"decision": "...", "rationale": "...", "confidence": 0-1}],
  "evidence": {"po_number": "...", "invoice_qty": ..., "grn_qty": ..., "receipt_status": "..."}
}
```

### Data Created Per Scenario

Each scenario creates Invoice + InvoiceLineItem records.  
Minimal POs (11 total: PO-KSA-3001..3011) and GRNs (13 total) are created where needed for scenario isolation.  
SCN-GRNAG-001 reuses existing PO-KSA-1001 and GRN-RUH-1001-A from master seed.  
No ReconciliationResult, AgentRun, ReviewAssignment, or AuditEvent records are created.

---

### SCN-GRNAG-001 — Full Receipt Exact Match

**Invoice:** INV-GRNAG-2026-001  
**PO:** PO-KSA-1001 → **GRN:** GRN-RUH-1001-A (reused from master seed)  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Confidence:** 0.93  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Sesame Burger Bun 4 inch | 500 | 500 | 500 | 45.00 | 45.00 | **None** |
| 2 | Shredded Lettuce Food Service Pack | 200 | 200 | 200 | 28.00 | 28.00 | **None** |
| 3 | Pickle Slice Jar Bulk | 100 | 100 | 100 | 35.00 | 35.00 | **None** |

**Totals:** Subtotal 31,600.00 | Tax 4,740.00 | Total 36,340.00

**Expected GRN Specialist Agent Outcome:**
- Should find GRN? **Yes**
- Should aggregate multiple GRNs? **No**
- Receipt status: **full**
- Expected `recommendation_type`: **null**
- Expected `confidence`: **high**
- Expected `evidence` keys: `po_number`, `invoice_qty`, `grn_qty`, `grn_numbers`, `receipt_status`

---

### SCN-GRNAG-002 — Missing GRN

**Invoice:** INV-GRNAG-2026-002  
**PO:** PO-KSA-3001 (created by this seed) → **No GRN records**  
**Vendor:** Riyadh Beverage Concentrates Co. (VND-RBC-005)  
**Confidence:** 0.91  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Soft Drink Syrup Cola Bag-in-Box | 120 | 120 | **N/A** | 220.00 | 220.00 | **No GRN** |
| 2 | Soft Drink Syrup Fanta Bag-in-Box | 80 | 80 | **N/A** | 215.00 | 215.00 | **No GRN** |

**Totals:** Subtotal 43,600.00 | Tax 6,540.00 | Total 50,140.00

**Expected GRN Specialist Agent Outcome:**
- Should find GRN? **No**
- Should aggregate multiple GRNs? **No**
- Receipt status: **missing**
- Expected `recommendation_type`: **SEND_TO_PROCUREMENT**
- Expected `confidence`: **medium-high**
- Expected `evidence` keys: `po_number`, `invoice_qty`, `grn_qty=0`, `receipt_status=missing`

---

### SCN-GRNAG-003 — Partial Receipt

**Invoice:** INV-GRNAG-2026-003  
**PO:** PO-KSA-3002 → **GRN:** GRN-RUH-3002-A (PARTIAL)  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.92  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Gap | Variance |
|------|-------------|---------|--------|---------|-----|----------|
| 1 | French Fries 2.5kg Frozen | 100 | 100 | 60 | 40 | **Partial receipt** |
| 2 | Nuggets Premium Frozen | 100 | 100 | 60 | 40 | **Partial receipt** |

**Totals:** Subtotal 22,300.00 | Tax 3,345.00 | Total 25,645.00

**Expected Deterministic Result (v1.4):**
- GRN found? **Yes**
- Receipt status: **partial** (60/100 per line)
- Exceptions created:
  - **RECEIPT_SHORTAGE** (MEDIUM) — received 60 < ordered 100 per line
  - **INVOICE_QTY_EXCEEDS_RECEIVED** (HIGH) — invoiced 100 > received 60 per line
- GRN Specialist Agent: **Triggered** (exceptions present, but fully classified deterministically)
- Expected agent recommendation: `SEND_TO_PROCUREMENT` (partial delivery, hold invoice for remaining receipt)

---

### SCN-GRNAG-004 — Invoice Exceeds Received Quantity

**Invoice:** INV-GRNAG-2026-004  
**PO:** PO-KSA-3003 → **GRN:** GRN-RUH-3003-A  
**Vendor:** Al Khobar Dairy Ingredients (VND-AKD-009)  
**Confidence:** 0.90  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Excess | Variance |
|------|-------------|---------|--------|---------|--------|----------|
| 1 | Cheese Slice Processed | **90** | 100 | 80 | **+10** | **Invoice > GRN** |
| 2 | Butter Portion Pack | **50** | 50 | 40 | **+10** | **Invoice > GRN** |

**Totals:** Subtotal 6,505.00 | Tax 975.75 | Total 7,480.75

**Expected Deterministic Result (v1.4):**
- GRN found? **Yes**
- Receipt status: **partial** (invoice qty > received qty on both lines)
- Exceptions created:
  - **INVOICE_QTY_EXCEEDS_RECEIVED** (HIGH) — Cheese: invoiced 90 > received 80; Butter: invoiced 50 > received 40
  - **RECEIPT_SHORTAGE** (MEDIUM) — Cheese: received 80 < ordered 100; Butter: received 40 < ordered 50
- GRN Specialist Agent: **Triggered** (exceptions present, but classified deterministically)
- Expected agent recommendation: `SEND_TO_PROCUREMENT` (short delivery + over-invoicing)

---

### SCN-GRNAG-005 — Multiple GRNs Cumulative Full Receipt

**Invoice:** INV-GRNAG-2026-005  
**PO:** PO-KSA-3004 → **GRNs:** GRN-DMM-3004-A, GRN-DMM-3004-B, GRN-DMM-3004-C  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.94  

| Line | Description | Inv Qty | PO Qty | GRN-A | GRN-B | GRN-C | GRN Total | Variance |
|------|-------------|---------|--------|-------|-------|-------|-----------|----------|
| 1 | McD Beef Patty 4:1 Frozen | 100 | 100 | 30 | 40 | 30 | 100 | **None** |

**Totals:** Subtotal 18,500.00 | Tax 2,775.00 | Total 21,275.00

**Expected GRN Specialist Agent Outcome:**
- Should find GRN? **Yes**
- Should aggregate multiple GRNs? **Yes** (3 GRNs)
- Receipt status: **full**
- Expected `recommendation_type`: **null**
- Expected `confidence`: **high**
- Expected `evidence` keys: `po_number`, `invoice_qty=100`, `cumulative_grn_qty=100`, `grn_numbers=[GRN-DMM-3004-A/B/C]`, `receipt_status=full`

---

### SCN-GRNAG-006 — Multiple GRNs Cumulative Partial Receipt

**Invoice:** INV-GRNAG-2026-006  
**PO:** PO-KSA-3005 → **GRNs:** GRN-JED-3005-A, GRN-JED-3005-B  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Confidence:** 0.91  

| Line | Description | Inv Qty | PO Qty | GRN-A | GRN-B | GRN Total | Gap | Variance |
|------|-------------|---------|--------|-------|-------|-----------|-----|----------|
| 1 | Chicken Patty Breaded Frozen | 200 | 200 | 80 | 50 | 130 | 70 | **Partial** |
| 2 | Hash Brown Triangle Frozen | 150 | 150 | 60 | 40 | 100 | 50 | **Partial** |

**Totals:** Subtotal 45,850.00 | Tax 6,877.50 | Total 52,727.50

**Expected Deterministic Result (v1.4):**
- GRN found? **Yes** (2 GRNs aggregated)
- Receipt status: **partial** (chicken 130/200, hash brown 100/150)
- Exceptions created:
  - **RECEIPT_SHORTAGE** (MEDIUM) — chicken: received 130 < ordered 200; hash brown: received 100 < ordered 150
  - **INVOICE_QTY_EXCEEDS_RECEIVED** (HIGH) — invoiced qty > cumulative received per line
- GRN Specialist Agent: **Triggered** (exceptions present, classified deterministically)
- Expected agent recommendation: `SEND_TO_PROCUREMENT` (partial multi-GRN delivery)

---

### SCN-GRNAG-007 — Over-Delivery Case

**Invoice:** INV-GRNAG-2026-007  
**PO:** PO-KSA-3006 → **GRN:** GRN-RUH-3006-A  
**Vendor:** Al Watania Poultry Supply (VND-AWP-003)  
**Confidence:** 0.93  

| Line | Description | Inv Qty | PO Qty | GRN Rcvd | GRN Accepted | GRN Rejected | Variance |
|------|-------------|---------|--------|----------|-------------|-------------|----------|
| 1 | Chicken Patty Breaded Frozen | **230** | 200 | **230** | 200 | 30 | **Over-delivery +30** |

**Totals:** Subtotal 36,340.00 | Tax 5,451.00 | Total 41,791.00

**Expected Deterministic Result (v1.4):**
- GRN found? **Yes**
- Receipt status: **over-delivery** (GRN 230 > PO 200)
- Exceptions created:
  - **OVER_RECEIPT** (MEDIUM) — received 230 > ordered 200 (over-delivery of 30 units)
  - **INVOICE_QTY_EXCEEDS_RECEIVED** (HIGH) — invoiced 230 matches received 230, but exceeds PO ordered 200
- Note: Invoice qty (230) matches GRN qty (230), so no `INVOICE_QTY_EXCEEDS_RECEIVED` gap vs. received. But the over-delivery itself is flagged.
- GRN Specialist Agent: **Triggered** (OVER_RECEIPT exception)
- Expected agent recommendation: `SEND_TO_PROCUREMENT` (vendor delivered more than ordered)

---

### SCN-GRNAG-008 — Delayed Receipt After Invoice Date

**Invoice:** INV-GRNAG-2026-008  
**PO:** PO-KSA-3007 → **GRN:** GRN-RUH-3007-A  
**Vendor:** Najd Edible Oils Trading (VND-NEO-008)  
**Confidence:** 0.89  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Date | GRN Date | Timing |
|------|-------------|---------|--------|---------|----------|----------|--------|
| 1 | Cooking Oil Fryer Grade 20L | 200 | 200 | 200 | 2026-02-28 | 2026-03-05 | **GRN 5 days late** |

**Totals:** Subtotal 6,400.00 | Tax 960.00 | Total 7,360.00

**Expected Deterministic Result (v1.4):**
- GRN found? **Yes**
- Receipt status: **full** (qty matches: invoiced 200 = received 200 = ordered 200)
- Timing issue: **DELAYED_RECEIPT** detected
- Exceptions created:
  - **DELAYED_RECEIPT** (LOW) — GRN receipt date 2026-03-05 is 5 days after invoice date 2026-02-28
- GRN Specialist Agent: May or may not be triggered (LOW severity only)
- Note: Quantities match perfectly; the only issue is timing. AP should verify if pre-invoicing is acceptable.

---

### SCN-GRNAG-009 — Branch vs Warehouse Receipt Mismatch

**Invoice:** INV-GRNAG-2026-009  
**PO:** PO-KSA-3008 → **GRN:** GRN-RUH-3008-A (Warehouse: **WH-RUH-01**)  
**Invoice Destination:** **BR-JED-220** (Jeddah Branch 220)  
**Vendor:** Red Sea Restaurant Consumables (VND-RSRC-007)  
**Confidence:** 0.88  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Invoice Dest | GRN Warehouse | Variance |
|------|-------------|---------|--------|---------|-------------|---------------|----------|
| 1 | Sanitizer Surface Use | 150 | 150 | 150 | BR-JED-220 | WH-RUH-01 | **Location mismatch** |
| 2 | Degreaser Kitchen Heavy Duty | 100 | 100 | 100 | BR-JED-220 | WH-RUH-01 | **Location mismatch** |

**Totals:** Subtotal 8,700.00 | Tax 1,305.00 | Total 10,005.00

**Why AI Agent Is Needed (v1.4):** Quantities match perfectly (150/150 and 100/100). The location mismatch (BR-JED-220 vs WH-RUH-01) cannot be detected deterministically because invoices don't have a structured `delivery_destination` field — the location is embedded in free-text remarks. The GRN Specialist Agent can parse invoice remarks to identify the discrepancy.

**Expected GRN Specialist Agent Outcome (agent triggered):**
- Should find GRN? **Yes** (but wrong location)
- Receipt status: **full** (quantities match, location mismatch)
- No deterministic exceptions created for location (qty/price all match)
- Expected `recommendation_type`: **SEND_TO_PROCUREMENT** or **SEND_TO_AP_REVIEW**
- Expected `confidence`: **medium**
- Expected `evidence` keys: `po_number`, `invoice_destination=BR-JED-220`, `grn_warehouse=WH-RUH-01`, `location_mismatch=True`

---

### SCN-GRNAG-010 — Receipt Exists for Wrong Item Mix

**Invoice:** INV-GRNAG-2026-010  
**PO:** PO-KSA-3009 → **GRN:** GRN-JED-3009-A  
**Vendor:** Saudi Packaging Solutions (VND-SPS-004)  
**Confidence:** 0.90  

| Line | Description | Inv Qty | PO Qty | GRN Qty | GRN Notes | Variance |
|------|-------------|---------|--------|---------|-----------|----------|
| 1 | Paper Cup 16oz | 5,000 | 5,000 | 5,000 | ✓ Full | None |
| 2 | Big Mac Clamshell Box | **3,000** | 3,000 | **1,000** | ✗ Short | **Gap = 2,000** |
| — | *(Fries Carton Medium — not on PO/inv)* | — | — | 2,000 | Substitution | **Unexpected item** |

**Totals:** Subtotal 7,850.00 | Tax 1,177.50 | Total 9,027.50

**Why AI Agent Is Needed (v1.4):** Deterministic matching detects the clamshell box shortage (3000 invoiced vs 1000 received) and creates `INVOICE_QTY_EXCEEDS_RECEIVED` + `RECEIPT_SHORTAGE`. However, the **item substitution** (fries carton received instead of clamshell) requires semantic understanding that the AI agent provides. The unexpected fries carton item on the GRN doesn't map to any PO/invoice line.

**Expected GRN Specialist Agent Outcome (agent triggered):**
- Should find GRN? **Yes**
- Receipt status: **partial / item mismatch**
- Deterministic exceptions: `INVOICE_QTY_EXCEEDS_RECEIVED` (clamshell), `RECEIPT_SHORTAGE` (clamshell)
- Agent adds context: substitution detection (fries carton → clamshell swap)
- Expected `recommendation_type`: **SEND_TO_PROCUREMENT** or **SEND_TO_VENDOR_CLARIFICATION**
- Expected `confidence`: **medium**
- Expected `evidence` keys: `po_number`, `item_level_mismatch=True`, `cups_match=True`, `clamshell_gap=2000`, `unexpected_item=Fries Carton`

---

### SCN-GRNAG-011 — Service / Non-GRN Invoice

**Invoice:** INV-GRNAG-2026-011  
**PO:** PO-KSA-3010 (Service PO) → **No GRN expected**  
**Vendor:** Red Sea Restaurant Consumables (VND-RSRC-007)  
**Confidence:** 0.87  

| Line | Description | Inv Qty | PO Qty | GRN Qty | UOM | Variance |
|------|-------------|---------|--------|---------|-----|----------|
| 1 | Monthly Kitchen Deep Cleaning Service | 1 | 1 | **N/A** | SVC | **Service item** |
| 2 | Pest Control Service — Quarterly | 1 | 1 | **N/A** | SVC | **Service item** |
| 3 | Grease Trap Maintenance Service | 1 | 1 | **N/A** | SVC | **Service item** |

**Totals:** Subtotal 8,500.00 | Tax 1,275.00 | Total 9,775.00

**Expected Deterministic Result (v1.4):**
- GRN found? **No** (and correctly so — service items)
- Receipt status: **non-GRN-applicable** (2-way mode should be resolved by heuristic/policy)
- Exceptions: **GRN_NOT_FOUND** may be created if 3-way mode is forced, but mode resolver should detect service keywords and apply 2-way mode
- GRN Specialist Agent: **Not triggered in 2-way mode** (GRN check skipped entirely)
- Note: This tests the mode resolver’s interaction with GRN exceptions — service invoices should resolve to 2-way mode

---

### SCN-GRNAG-012 — Cold-Chain Shortage Scenario

**Invoice:** INV-GRNAG-2026-012  
**PO:** PO-KSA-3011 → **GRN:** GRN-DMM-3011-A (PARTIAL)  
**Vendor:** Desert Cold Chain Logistics (VND-DCCL-006)  
**Confidence:** 0.91  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Gap | Variance |
|------|-------------|---------|--------|---------|-----|----------|
| 1 | French Fries 2.5kg Frozen | 500 | 500 | 420 | 80 | **Cold-chain loss** |
| 2 | Nuggets Premium Frozen | 300 | 300 | 250 | 50 | **Partial unloading** |

**Totals:** Subtotal 82,500.00 | Tax 12,375.00 | Total 94,875.00

**Expected Deterministic Result (v1.4):**
- GRN found? **Yes** (partial receipt)
- Receipt status: **partial** (cold-chain shortage)
- Exceptions created:
  - **RECEIPT_SHORTAGE** (MEDIUM) — Fries: received 420 < ordered 500 (gap=80); Nuggets: received 250 < ordered 300 (gap=50)
  - **INVOICE_QTY_EXCEEDS_RECEIVED** (HIGH) — Fries: invoiced 500 > received 420; Nuggets: invoiced 300 > received 250
- GRN Specialist Agent: **Triggered** (exceptions present, classified deterministically)
- Expected agent recommendation: `SEND_TO_PROCUREMENT` (cold-chain loss, hold for credit note)

---

## Mixed-Mode Reconciliation Scenarios (SCN-MODE-001..012)

**Seed Command:** `python manage.py seed_mixed_mode_data` (requires `seed_saudi_mcd_data` first)

These scenarios test the **configurable 2-way/3-way reconciliation mode** feature, covering all three resolution tiers (policy, heuristic, config default) and both matching modes.

### Reconciliation Policies Created

| Code | Name | Mode | Match Criteria | Priority |
|------|------|------|----------------|----------|
| POL-SVC-VENDOR | Gulf Professional Services - 2-Way | TWO_WAY | vendor=VND-GPS-011, is_service=True | 10 |
| POL-SVC-GLOBAL | Service Invoices - 2-Way | TWO_WAY | is_service_invoice=True | 20 |
| POL-STOCK-GLOBAL | Stock/Inventory Invoices - 3-Way | THREE_WAY | is_stock_invoice=True | 30 |
| POL-FOOD-3WAY | Food Category - 3-Way | THREE_WAY | item_category=Food | 40 |
| POL-LOGISTICS-2WAY | Logistics & Transport - 2-Way | TWO_WAY | item_category=Logistics | 50 |
| POL-WH-RUH-3WAY | Riyadh Warehouse - 3-Way | THREE_WAY | location_code=WH-RUH-01 | 60 |
| POL-BRANCH-2WAY | Direct Branch Purchases - 2-Way | TWO_WAY | business_unit=Branch Operations | 70 |

---

### SCN-MODE-001 — Service: Cleaning Contract (2-Way, Vendor Policy)

**Invoice:** INV-MODE-001  
**PO:** PO-KSA-3001 (no GRN)  
**Vendor:** Gulf Professional Services Co. (VND-GPS-011)  
**Mode Resolution:** Policy (POL-SVC-VENDOR, priority 10) -> **TWO_WAY**  

| Line | Description | Inv Qty | PO Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|-----------|----------|----------|
| 1 | Monthly Cleaning Service - Riyadh HQ | 1 | 1 | 8,500.00 | 8,500.00 | **None** |
| 2 | Deep Cleaning - Kitchen Area | 2 | 2 | 3,200.00 | 3,200.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **TWO_WAY**
- Mode Resolved By: **policy** (POL-SVC-VENDOR)
- GRN Check: **Skipped** (2-way mode)
- Agents: **Skipped**

---

### SCN-MODE-002 — Service: Pest Control (2-Way, Keyword Heuristic)

**Invoice:** INV-MODE-002  
**PO:** PO-KSA-3002 (no GRN)  
**Vendor:** Gulf Professional Services Co. (VND-GPS-011) — alias "Gulf Pro Services"  
**Mode Resolution:** Heuristic (service keywords: "Pest Control Service") -> **TWO_WAY**  

| Line | Description | Inv Qty | PO Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|-----------|----------|----------|
| 1 | Pest Control Service - Quarterly Treatment | 1 | 1 | 4,500.00 | 4,500.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **TWO_WAY**
- Mode Resolved By: **heuristic** (keyword match)
- GRN Check: **Skipped**
- Agents: **Skipped**

---

### SCN-MODE-003 — Stock: Food Supply Perfect 3-Way Match

**Invoice:** INV-MODE-003  
**PO:** PO-KSA-3003 -> **GRN:** GRN-MODE-3003  
**Vendor:** Arabian Food Supplies Co. (VND-AFS-001)  
**Mode Resolution:** Policy (POL-STOCK-GLOBAL, priority 30) -> **THREE_WAY**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Sesame Burger Bun 4 inch | 400 | 400 | 400 | 45.00 | 45.00 | **None** |
| 2 | Shredded Lettuce Food Service Pack | 150 | 150 | 150 | 28.00 | 28.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **THREE_WAY**
- Mode Resolved By: **policy** (POL-STOCK-GLOBAL)
- GRN Check: **Full receipt**
- Agents: **Skipped**

---

### SCN-MODE-004 — Stock: Frozen Food, GRN Shortage (3-Way)

**Invoice:** INV-MODE-004  
**PO:** PO-KSA-3004 -> **GRN:** GRN-MODE-3004 (partial)  
**Vendor:** Gulf Frozen Foods Trading (VND-GFF-002)  
**Mode Resolution:** Policy (POL-FOOD-3WAY, priority 40) -> **THREE_WAY**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Variance |
|------|-------------|---------|--------|---------|----------|
| 1 | Beef Patty 150g Premium Frozen | 300 | 300 | 280 | GRN shortage: -6.7% |
| 2 | Chicken Nuggets 6pc Frozen Pack | 200 | 200 | 190 | GRN shortage: -5.0% |

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Reconciliation Mode: **THREE_WAY**
- Mode Resolved By: **policy** (POL-FOOD-3WAY)
- Exceptions: RECEIPT_SHORTAGE
- Agents: **Triggered** (GRN discrepancies)

---

### SCN-MODE-005 — Service: Security, Price Mismatch (2-Way)

**Invoice:** INV-MODE-005  
**PO:** PO-KSA-3005 (no GRN)  
**Vendor:** Gulf Professional Services Co. (VND-GPS-011)  
**Mode Resolution:** Policy (POL-SVC-VENDOR, priority 10) -> **TWO_WAY**  

| Line | Description | Inv Qty | PO Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|-----------|----------|----------|
| 1 | Security Guard Service - Monthly | 1 | 1 | 12,000.00 | 11,500.00 | Price +4.3% |

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Reconciliation Mode: **TWO_WAY**
- Mode Resolved By: **policy** (POL-SVC-VENDOR)
- Exceptions: PRICE_MISMATCH
- GRN Check: **Skipped**
- Agents: **Triggered** (price mismatch beyond auto-close)

---

### SCN-MODE-006 — Stock: Packaging, Missing GRN Lines (3-Way)

**Invoice:** INV-MODE-006  
**PO:** PO-KSA-3006 -> **GRN:** GRN-MODE-3006 (cups only, lids missing)  
**Vendor:** Saudi Packaging Solutions (VND-SPS-004)  
**Mode Resolution:** Policy (POL-STOCK-GLOBAL, priority 30) -> **THREE_WAY**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Variance |
|------|-------------|---------|--------|---------|----------|
| 1 | Paper Cup 12oz - McD Branded | 5,000 | 5,000 | 5,000 | **None** |
| 2 | Cup Lid 12oz Dome | 5,000 | 5,000 | **N/A** | **No GRN line** |

**Expected Result:**
- Match Status: **UNMATCHED** or **PARTIAL_MATCH**
- Reconciliation Mode: **THREE_WAY**
- Mode Resolved By: **policy** (POL-STOCK-GLOBAL)
- Exceptions: RECEIPT_SHORTAGE / GRN_NOT_FOUND
- Agents: **Triggered**

---

### SCN-MODE-007 — Mixed: Service + Stock Lines (Default Fallback)

**Invoice:** INV-MODE-007  
**PO:** PO-KSA-3007 -> **GRN:** GRN-MODE-3007 (stock line only)  
**Vendor:** Desert Cold Chain Logistics (VND-DCCL-006)  
**Mode Resolution:** Default fallback (mixed classifications, no majority) -> **THREE_WAY**  

| Line | Description | Type | Inv Qty | PO Qty | GRN Qty | Variance |
|------|-------------|------|---------|--------|---------|----------|
| 1 | Refrigerated Transport - Riyadh to Jeddah | Service | 4 | 4 | N/A | Service line (no GRN) |
| 2 | Dry Ice Packs for Cold Storage | Stock | 100 | 100 | 100 | **None** |

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Reconciliation Mode: **THREE_WAY**
- Mode Resolved By: **default** (config fallback)
- Note: Mixed service+stock lines; ambiguous for heuristic, falls to config default

---

### SCN-MODE-008 — Stock: Beverage, Qty Mismatch (3-Way)

**Invoice:** INV-MODE-008  
**PO:** PO-KSA-3008 -> **GRN:** GRN-MODE-3008  
**Vendor:** Riyadh Beverage Concentrates Co. (VND-RBC-005)  
**Mode Resolution:** Policy (POL-STOCK-GLOBAL) or heuristic -> **THREE_WAY**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Variance |
|------|-------------|---------|--------|---------|----------|
| 1 | Cola Syrup Concentrate 20L | 110 | 100 | 100 | Qty +10% (invoice over-claims) |

**Expected Result:**
- Match Status: **PARTIAL_MATCH**
- Reconciliation Mode: **THREE_WAY**
- Exceptions: QTY_MISMATCH
- Agents: **Triggered** (qty mismatch beyond auto-close)

---

### SCN-MODE-009 — Service: Maintenance (2-Way, Keyword Heuristic)

**Invoice:** INV-MODE-009  
**PO:** PO-KSA-3009 (no GRN)  
**Vendor:** Jeddah Quick Service Supplies (VND-JQSS-010)  
**Mode Resolution:** Heuristic (keyword: "Maintenance Service") -> **TWO_WAY**  

| Line | Description | Inv Qty | PO Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|-----------|----------|----------|
| 1 | Kitchen Equipment Maintenance Service Q1 | 1 | 1 | 6,500.00 | 6,500.00 | **None** |
| 2 | Walk-in Freezer Maintenance Service | 1 | 1 | 4,200.00 | 4,200.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **TWO_WAY**
- Mode Resolved By: **heuristic** (keyword match)
- GRN Check: **Skipped**
- Agents: **Skipped**
- Note: Vendor (VND-JQSS-010) has no explicit policy; resolved purely by keyword heuristic

---

### SCN-MODE-010 — Stock: Dairy, Location-Based Policy (3-Way)

**Invoice:** INV-MODE-010  
**PO:** PO-KSA-3010 -> **GRN:** GRN-MODE-3010  
**Vendor:** Al Khobar Dairy Ingredients (VND-AKD-009)  
**Mode Resolution:** Policy (POL-WH-RUH-3WAY, priority 60) -> **THREE_WAY**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Processed Cheese Slices 200pc | 350 | 350 | 350 | 62.00 | 62.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **THREE_WAY**
- Mode Resolved By: **policy** (POL-WH-RUH-3WAY, location_code match)
- GRN Check: **Full receipt**
- Agents: **Skipped**

---

### SCN-MODE-011 — Branch: Direct Purchase (2-Way, Business Unit Policy)

**Invoice:** INV-MODE-011  
**PO:** PO-KSA-3011 -> **GRN:** GRN-MODE-3011 (exists but ignored)  
**Vendor:** Red Sea Restaurant Consumables (VND-RSRC-007)  
**Mode Resolution:** Policy (POL-BRANCH-2WAY, priority 70) -> **TWO_WAY**  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Paper Napkins Branded 500ct | 200 | 200 | 200 | 18.00 | 18.00 | **None** |
| 2 | Drinking Straw Biodegradable 1000ct | 100 | 100 | 100 | 25.00 | 25.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **TWO_WAY**
- Mode Resolved By: **policy** (POL-BRANCH-2WAY, business_unit match)
- GRN Check: **Skipped** (GRN exists but irrelevant in 2-way mode)
- Agents: **Skipped**
- Note: Tests that GRN is correctly ignored when business-unit policy dictates 2-way mode

---

### SCN-MODE-012 — Default Fallback: No Policy, No Heuristic (3-Way)

**Invoice:** INV-MODE-012  
**PO:** PO-KSA-3012 -> **GRN:** GRN-MODE-3012  
**Vendor:** Najd Edible Oils Trading (VND-NEO-008)  
**Mode Resolution:** Default fallback -> **THREE_WAY** (config default)  

| Line | Description | Inv Qty | PO Qty | GRN Qty | Inv Price | PO Price | Variance |
|------|-------------|---------|--------|---------|-----------|----------|----------|
| 1 | Blend Premium Grade A 20L | 80 | 80 | 80 | 145.00 | 145.00 | **None** |
| 2 | Blend Standard Grade B 20L | 50 | 50 | 50 | 120.00 | 120.00 | **None** |

**Expected Result:**
- Match Status: **MATCHED**
- Reconciliation Mode: **THREE_WAY**
- Mode Resolved By: **default** (no policy or heuristic matched)
- GRN Check: **Full receipt**
- Agents: **Skipped**
- Note: Lines have no item_category, no is_service/is_stock flags — tests pure fallback path

---

## Test Execution Checklist

### Pre-Conditions
- [ ] Master data seeded: `python manage.py seed_saudi_mcd_data`
- [ ] Invoice data seeded: `python manage.py seed_invoice_test_data`
- [ ] PO Agent test data seeded: `python manage.py seed_po_agent_test_data`
- [ ] GRN Agent test data seeded: `python manage.py seed_grn_agent_test_data`
- [ ] Mixed-mode data seeded: `python manage.py seed_mixed_mode_data`
- [ ] Active config is "Default Production" (strict: 2%/1%/1%, auto-close: 5%/3%/3%, mode resolver enabled)
- [ ] All invoices show status `READY_FOR_RECON`
- [ ] No stale ReconciliationRun/Result/Exception records exist

### Reconciliation Execution
- [ ] Navigate to Reconciliation page
- [ ] Select all SCN-KSA invoices (IDs 87–105)
- [ ] Click "Start Reconciliation"
- [ ] Verify each scenario produces expected match status (see table above)

### Scenario Verification

| # | Verify Match Status | Verify Exceptions | Verify Agents | Verify Review | Pass? |
|---|--------------------|--------------------|---------------|---------------|-------|
| 001 | MATCHED | None | Skipped | Not required | ☐ |
| 002 | PARTIAL_MATCH | QTY_MISMATCH | 4 agents ran | Created | ☐ |
| 003 | PARTIAL_MATCH | PRICE_MISMATCH | 4 agents ran | Created | ☐ |
| 004 | PARTIAL_MATCH | TAX_MISMATCH | 4 agents ran | Created | ☐ |
| 005 | UNMATCHED | PO_NOT_FOUND | PORetrieval + 3 | Created | ☐ |
| 006 | UNMATCHED | GRN_NOT_FOUND | GRNRetrieval + 3 | Created | ☐ |
| 007 | MATCHED | None | Skipped | Not required | ☐ |
| 008a | MATCHED | None | Skipped | Not required | ☐ |
| 008b | REQUIRES_REVIEW | DUPLICATE_INVOICE | Agents ran | Created | ☐ |
| 009 | PARTIAL/REVIEW | LOW_CONFIDENCE | InvoiceUnderstanding + 3 | Created | ☐ |
| 010 | PARTIAL/REVIEW | Location-related | Agents ran | Created | ☐ |
| 011 | PARTIAL_MATCH | QTY_MISMATCH (HIGH) | 4 agents ran | Created | ☐ |
| 012 | REQUIRES_REVIEW | ITEM+PRICE MISMATCH | 3 agents ran | Created | ☐ |
| 013 | **MATCHED** (auto-closed) | Low-severity only | **Skipped** | **Not required** | ☐ |
| 014 | **MATCHED** (auto-closed) | Low-severity only | **Skipped** | **Not required** | ☐ |
| 015 | PARTIAL_MATCH | QTY_MISMATCH | 4 agents ran | Created | ☐ |
| 016 | PARTIAL_MATCH | QTY+DESC mismatch | 4 agents ran | Created | ☐ |
| 017 | PARTIAL_MATCH | PRICE_MISMATCH ×2 | 4 agents ran | Created | ☐ |
| 018 | PARTIAL_MATCH | ITEM_MISMATCH | 4 agents ran | Created | ☐ |

### PO Retrieval Agent Scenario Verification (v1.4 — Deterministic Pipeline)

| # | Invoice | Raw PO | Lookup Tier | PO Found? | Agent Needed? | Expected Match Status | Pass? |
|---|---------|--------|-------------|-----------|---------------|----------------------|-------|
| POAG-001 | INV-POAG-2026-001 | `PO-1001-KSA` | Tier 3: vendor+amount | ✅ PO-KSA-1001 | **No** | **MATCHED** | ☐ |
| POAG-002 | INV-POAG-2026-002 | `[unreadable]` | Tier 3: vendor+amount | ✅ PO-KSA-2001 | **No** | **MATCHED** | ☐ |
| POAG-003 | INV-POAG-2026-003 | *(empty)* | Tier 3: 3 candidates | ❌ Ambiguous | **Yes** | UNMATCHED (agent can't resolve) | ☐ |
| POAG-004 | INV-POAG-2026-004 | `PO/KSA/XXXX` | Tier 3: alias+amount | ✅ PO-KSA-2005 | **No** | **MATCHED** | ☐ |
| POAG-005 | INV-POAG-2026-005 | `PO-KSA-9999` | All tiers fail | ❌ Not found | **Yes** | UNMATCHED (no PO exists) | ☐ |
| POAG-006 | INV-POAG-2026-006 | `PO-KSA-1001` | Tier 1: exact | ✅ PO-KSA-1001 | **No** | **PARTIAL_MATCH** (item mismatch) | ☐ |
| POAG-007 | INV-POAG-2026-007 | `po_ksa_1002` | Tier 2: normalized | ✅ PO-KSA-1002 | **No** | **MATCHED** | ☐ |
| POAG-008 | INV-POAG-2026-008 | `PO-KSA-1017` | Tier 1: exact | ✅ PO-KSA-1017 | **No** | Depends on GRN | ☐ |
| POAG-009 | INV-POAG-2026-009 | *(empty)* | Tier 3: 2 candidates | ❌ Ambiguous | **Yes** | UNMATCHED (agent may resolve via location) | ☐ |
| POAG-010 | INV-POAG-2026-010 | `PO/KSA/1003` | Tier 2: normalized | ✅ PO-KSA-1003 | **No** | **MATCHED** | ☐ |

**Summary: 7/10 deterministic, 3/10 need agent (003, 005, 009)**

### GRN Specialist Agent Scenario Verification (v1.4 — Enhanced Exception Types)

| # | Invoice | PO → GRN | Receipt Status | Deterministic Exceptions | Agent Needed? | Pass? |
|---|---------|----------|----------------|--------------------------|---------------|-------|
| GRNAG-001 | INV-GRNAG-2026-001 | PO-KSA-1001 → GRN-RUH-1001-A | Full | None | **No** | ☐ |
| GRNAG-002 | INV-GRNAG-2026-002 | PO-KSA-3001 → *(none)* | Missing | GRN_NOT_FOUND (MEDIUM) | **No** | ☐ |
| GRNAG-003 | INV-GRNAG-2026-003 | PO-KSA-3002 → GRN-RUH-3002-A | Partial (60/100) | RECEIPT_SHORTAGE + INV_QTY_EXCEEDS_RECEIVED | **No** | ☐ |
| GRNAG-004 | INV-GRNAG-2026-004 | PO-KSA-3003 → GRN-RUH-3003-A | Inv > Rcvd | INVOICE_QTY_EXCEEDS_RECEIVED + RECEIPT_SHORTAGE | **No** | ☐ |
| GRNAG-005 | INV-GRNAG-2026-005 | PO-KSA-3004 → 3 GRNs | Full (aggregated) | None | **No** | ☐ |
| GRNAG-006 | INV-GRNAG-2026-006 | PO-KSA-3005 → 2 GRNs | Partial (130/200) | RECEIPT_SHORTAGE + INV_QTY_EXCEEDS_RECEIVED | **No** | ☐ |
| GRNAG-007 | INV-GRNAG-2026-007 | PO-KSA-3006 → GRN-RUH-3006-A | Over-delivery (230>200) | OVER_RECEIPT (MEDIUM) | **No** | ☐ |
| GRNAG-008 | INV-GRNAG-2026-008 | PO-KSA-3007 → GRN-RUH-3007-A | Delayed (GRN 5d late) | DELAYED_RECEIPT (LOW) | **No** | ☐ |
| GRNAG-009 | INV-GRNAG-2026-009 | PO-KSA-3008 → GRN-RUH-3008-A | Location mismatch | None (qty matches) | **Yes** ⚠️ | ☐ |
| GRNAG-010 | INV-GRNAG-2026-010 | PO-KSA-3009 → GRN-JED-3009-A | Wrong item mix | INV_QTY_EXCEEDS_RECEIVED (clamshell) | **Yes** ⚠️ | ☐ |
| GRNAG-011 | INV-GRNAG-2026-011 | PO-KSA-3010 → *(none)* | Non-GRN (service) | 2-way mode → GRN skipped | **No** | ☐ |
| GRNAG-012 | INV-GRNAG-2026-012 | PO-KSA-3011 → GRN-DMM-3011-A | Cold-chain shortage | RECEIPT_SHORTAGE + INV_QTY_EXCEEDS_RECEIVED | **No** | ☐ |

**Summary: 10/12 deterministic, 2/12 need agent (009 location mismatch, 010 item substitution)**

### Mixed-Mode Scenario Verification

| # | Invoice | PO → GRN | Mode | Mode Resolved By | Expected Match | Pass? |
|---|---------|----------|------|------------------|----------------|-------|
| MODE-001 | INV-MODE-001 | PO-KSA-3001 → *(none)* | TWO_WAY | policy (POL-SVC-VENDOR) | MATCHED | ☐ |
| MODE-002 | INV-MODE-002 | PO-KSA-3002 → *(none)* | TWO_WAY | heuristic (keyword) | MATCHED | ☐ |
| MODE-003 | INV-MODE-003 | PO-KSA-3003 → GRN-MODE-3003 | THREE_WAY | policy (POL-STOCK-GLOBAL) | MATCHED | ☐ |
| MODE-004 | INV-MODE-004 | PO-KSA-3004 → GRN-MODE-3004 | THREE_WAY | policy (POL-FOOD-3WAY) | PARTIAL_MATCH | ☐ |
| MODE-005 | INV-MODE-005 | PO-KSA-3005 → *(none)* | TWO_WAY | policy (POL-SVC-VENDOR) | PARTIAL_MATCH | ☐ |
| MODE-006 | INV-MODE-006 | PO-KSA-3006 → GRN-MODE-3006 | THREE_WAY | policy (POL-STOCK-GLOBAL) | UNMATCHED | ☐ |
| MODE-007 | INV-MODE-007 | PO-KSA-3007 → GRN-MODE-3007 | THREE_WAY | default (config fallback) | PARTIAL_MATCH | ☐ |
| MODE-008 | INV-MODE-008 | PO-KSA-3008 → GRN-MODE-3008 | THREE_WAY | policy/heuristic | PARTIAL_MATCH | ☐ |
| MODE-009 | INV-MODE-009 | PO-KSA-3009 → *(none)* | TWO_WAY | heuristic (keyword) | MATCHED | ☐ |
| MODE-010 | INV-MODE-010 | PO-KSA-3010 → GRN-MODE-3010 | THREE_WAY | policy (POL-WH-RUH-3WAY) | MATCHED | ☐ |
| MODE-011 | INV-MODE-011 | PO-KSA-3011 → GRN-MODE-3011 | TWO_WAY | policy (POL-BRANCH-2WAY) | MATCHED | ☐ |
| MODE-012 | INV-MODE-012 | PO-KSA-3012 → GRN-MODE-3012 | THREE_WAY | default (config fallback) | MATCHED | ☐ |

### Post-Reconciliation Checks
- [ ] Dashboard analytics reflect correct counts (matched, partial, unmatched, review)
- [ ] Agent Monitor shows agent runs for non-matched scenarios
- [ ] Case Console shows correct exception details per scenario
- [ ] Auto-closed cases (013, 014) show no agent runs and MATCHED status
- [ ] CSV export downloads correctly for any case
- [ ] Settings page shows active tolerance config used during reconciliation
- [ ] Mode filter on reconciliation results page works correctly (TWO_WAY / THREE_WAY)
- [ ] Mode badges display correctly on result list and detail pages
- [ ] 2-way results hide GRN sections in detail/console views
- [ ] Mode breakdown dashboard endpoint returns correct counts
