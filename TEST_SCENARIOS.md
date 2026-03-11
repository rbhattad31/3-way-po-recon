# 3-Way PO Reconciliation — Test Scenarios & Expected Results

**Document Version:** 1.0  
**Date:** 2026-03-11  
**Dataset:** `seed_saudi_mcd_data` (master) + `seed_invoice_test_data` (18 scenarios)  
**Active Config:** Default Production  

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

## Quick Reference — All 18 Scenarios

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

## Test Execution Checklist

### Pre-Conditions
- [ ] Master data seeded: `python manage.py seed_saudi_mcd_data`
- [ ] Invoice data seeded: `python manage.py seed_invoice_test_data`
- [ ] Active config is "Default Production" (strict: 2%/1%/1%, auto-close: 5%/3%/3%)
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

### Post-Reconciliation Checks
- [ ] Dashboard analytics reflect correct counts (matched, partial, unmatched, review)
- [ ] Agent Monitor shows agent runs for non-matched scenarios
- [ ] Case Console shows correct exception details per scenario
- [ ] Auto-closed cases (013, 014) show no agent runs and MATCHED status
- [ ] CSV export downloads correctly for any case
- [ ] Settings page shows active tolerance config used during reconciliation
