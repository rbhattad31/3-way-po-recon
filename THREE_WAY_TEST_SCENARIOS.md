# THREE_WAY PO Invoice – Test Scenarios

> **Seed command:** `python manage.py seed_three_way_invoices`
> **Path:** `apps/cases/management/commands/seed_three_way_invoices.py`
> **Domain:** McDonald's Saudi Arabia – AP Automation (Goods/Stock Invoices)
> **Processing path:** THREE_WAY (Invoice vs PO vs GRN — full goods receipt verification)

---

## Overview

This document describes the 20 deterministic test scenarios seeded by the `seed_three_way_invoices` management command. Each scenario creates a **goods/stock invoice** with a backing Purchase Order and (optionally) one or more Goods Receipt Notes, designed to exercise a specific THREE_WAY reconciliation outcome when matching is triggered later from the invoice detail page.

**What is seeded:** Vendors, vendor aliases, POs, PO line items, GRNs, GRN line items, invoices, invoice line items, document uploads, and extraction JSON payloads.

**What is NOT seeded:** AP Cases, reconciliation runs/results/exceptions, agent runs, review assignments, audit events. These are created later when reconciliation is triggered.

### Seed Modes

| Mode | Deterministic | Random | Total |
|------|--------------|--------|-------|
| `demo` (default) | 20 | 0 | 20 |
| `qa` | 20 | 15 | 35 |
| `large` | 20 | 40 | 60 |

### Command Examples

```bash
python manage.py seed_three_way_invoices                     # demo mode
python manage.py seed_three_way_invoices --mode=qa           # +15 random
python manage.py seed_three_way_invoices --mode=large        # +40 random
python manage.py seed_three_way_invoices --reset             # delete & recreate
python manage.py seed_three_way_invoices --summary           # print table
python manage.py seed_three_way_invoices --reset --mode=qa --summary
```

### Expected THREE_WAY Processing Pipeline

Once reconciliation is triggered, each invoice passes through these stages:

```
INTAKE -> EXTRACTION -> PATH_RESOLUTION -> PO_RETRIEVAL -> THREE_WAY_MATCHING -> GRN_ANALYSIS -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY
```

### GRN Behaviours

Unlike TWO_WAY, THREE_WAY matching requires verification against Goods Receipt Notes. Each scenario specifies a `grn_behaviour` that controls what GRN data is created:

| Behaviour | Description | Expected Exception |
|-----------|-------------|-------------------|
| `FULL_RECEIPT` | GRN qty == PO qty (perfect receipt) | None (match) |
| `NO_GRN` | No GRN created for the PO | GRN_NOT_FOUND |
| `PARTIAL_RECEIPT` | GRN qty < PO qty (60-85% received) | RECEIPT_SHORTAGE / INVOICE_QTY_EXCEEDS_RECEIVED |
| `OVER_RECEIPT` | GRN qty > PO qty (105-115% received) | OVER_RECEIPT |
| `MULTI_GRN` | Two partial GRNs (55% + 45%) that together cover PO | MULTI_GRN_PARTIAL_RECEIPT (aggregation needed) |
| `DELAYED_RECEIPT` | GRN exists but receipt_date is 40-60 days after PO | DELAYED_RECEIPT |
| `LOCATION_MISMATCH` | GRN warehouse differs from invoice/branch warehouse | RECEIPT_LOCATION_MISMATCH |

---

## Master Data

### Vendors (10)

| Code | Name | Category | Aliases |
|------|------|----------|---------|
| V3W-001 | Americana Foods Company | Frozen Foods & Proteins | Americana Foods, Americana Group KSA |
| V3W-002 | SADAFCO (Saudia Dairy & Foodstuff Co.) | Beverages & Dairy | SADAFCO, Saudia Dairy, Saudi Dairy & Foodstuff |
| V3W-003 | Al Marai Company | Bakery & Buns | Almarai, Al-Marai Co. |
| V3W-004 | Gulf Packaging Industries | Packaging Materials | Gulf Pack, GPI Saudi |
| V3W-005 | Diversey Arabia LLC | Cleaning Chemicals | Diversey KSA, Diversey Hygiene |
| V3W-006 | Binzagr Coca-Cola Saudi | Beverages & Dry Goods | Binzagr CocaCola, Binzagr Beverages |
| V3W-007 | Red Sea Uniforms & Workwear | Uniforms & Housekeeping Stock | Red Sea Uniforms, RS Workwear |
| V3W-008 | Henny Penny Parts Arabia | Spare Parts & Equipment | HP Parts Arabia, Henny Penny Equipment KSA |
| V3W-009 | Frozen Express Cold Chain Co. | Frozen Goods & Cold Chain | Frozen Express, FE Cold Chain |
| V3W-010 | Arabian Paper Products Co. | Paper & Takeaway Packaging | Arabian Paper, APP Saudi |

All vendors also have Arabic aliases for OCR testing.

### Branches (7)

| Code | Name | City | Warehouse |
|------|------|------|-----------|
| BR-RUH-001 | McDonald's Olaya Street | Riyadh | WH-RUH-CENTRAL |
| BR-RUH-002 | McDonald's King Fahd Road | Riyadh | WH-RUH-CENTRAL |
| BR-RUH-003 | McDonald's Exit 15 DT | Riyadh | WH-RUH-SOUTH |
| BR-JED-001 | McDonald's Tahlia Street | Jeddah | WH-JED-MAIN |
| BR-JED-002 | McDonald's Corniche | Jeddah | WH-JED-MAIN |
| BR-DMM-001 | McDonald's King Saud Street | Dammam | WH-DMM-DC |
| BR-DMM-002 | McDonald's Dhahran Mall | Dammam | WH-DMM-DC |

### Warehouses (4)

| Code | Name |
|------|------|
| WH-RUH-CENTRAL | Riyadh Central Distribution Center |
| WH-RUH-SOUTH | Riyadh South Warehouse |
| WH-JED-MAIN | Jeddah Main Distribution Center |
| WH-DMM-DC | Dammam Distribution Center |

### Cost Centers (5)

| Code | Department |
|------|-----------|
| CC-1010 | Food & Beverage |
| CC-1020 | Packaging & Supplies |
| CC-1030 | Cleaning & Hygiene |
| CC-2010 | Store Operations |
| CC-4010 | Equipment & Maintenance |

---

## Scenario Buckets

The 20 scenarios are organized into three buckets based on expected reconciliation outcome:

| Bucket | Scenarios | Expected Result |
|--------|-----------|----------------|
| **A. Likely Matched** | SCN-01 through SCN-05 | MATCHED -- clean PO + GRN match, no exceptions |
| **B. Partial Match / GRN Review** | SCN-06 through SCN-13 | PARTIAL_MATCH or REQUIRES_REVIEW -- GRN discrepancies, PO noise, or amount differences |
| **C. Exception / Fail** | SCN-14 through SCN-20 | UNMATCHED or ERROR -- duplicates, missing data, severe mismatches |

---

## Bucket A: Likely Matched (SCN-01 to SCN-05)

These invoices have clean data, valid PO references, full GRN receipts matching PO quantities, correct VAT, and high extraction confidence. When reconciliation runs, they should produce a **MATCHED** result with no exceptions.

---

### SCN-01 -- Frozen Fries Stock Replenishment (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-FRIES-PERFECT` |
| **Invoice** | INV-3W-0001 |
| **PO** | PO-3W-0001 |
| **Vendor** | V3W-001 -- Americana Foods Company |
| **Branch** | BR-RUH-001 -- McDonald's Olaya Street, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-1010 |
| **Category** | Frozen Foods & Proteins |
| **Total** | SAR 12,893.80 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.96 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0001 at WH-RUH-CENTRAL |

**Description:** Frozen fries stock replenishment -- Riyadh warehouse

**What to verify:**
- Invoice amounts match PO amounts exactly
- GRN quantities match PO quantities exactly (full receipt)
- Vendor resolves correctly
- PO retrieval finds PO-3W-0001 on first attempt
- THREE_WAY matching produces MATCHED
- No exceptions generated
- Case auto-closes or routes to summary

---

### SCN-02 -- Packaging Materials (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-PACKAGING-PERFECT` |
| **Invoice** | INV-3W-0002 |
| **PO** | PO-3W-0002 |
| **Vendor** | V3W-004 -- Gulf Packaging Industries |
| **Branch** | BR-JED-001 -- McDonald's Tahlia Street, Jeddah |
| **Warehouse** | WH-JED-MAIN |
| **Cost Center** | CC-1020 |
| **Category** | Packaging Materials |
| **Total** | SAR 14,789.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.94 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0002 at WH-JED-MAIN |

**Description:** Burger packaging materials invoice -- Jeddah DC

**What to verify:**
- Standard packaging stock order with clean extraction
- PO match is immediate
- GRN confirms full receipt at Jeddah warehouse
- This invoice is later referenced by SCN-14 (duplicate test)
- Auto-close eligible

---

### SCN-03 -- Beverages Stock (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-BEVERAGE-PERFECT` |
| **Invoice** | INV-3W-0003 |
| **PO** | PO-3W-0003 |
| **Vendor** | V3W-006 -- Binzagr Coca-Cola Saudi |
| **Branch** | BR-DMM-001 -- McDonald's King Saud Street, Dammam |
| **Warehouse** | WH-DMM-DC |
| **Cost Center** | CC-1010 |
| **Category** | Beverages & Dry Goods |
| **Total** | SAR 15,641.15 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.95 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0003 at WH-DMM-DC |

**Description:** Beverages stock replenishment -- Dammam DC

**What to verify:**
- Syrups and condiments supply invoice
- Full GRN receipt at Dammam DC
- Clean 3-way match: Invoice = PO = GRN

---

### SCN-04 -- Cleaning Chemicals (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-CLEANING-PERFECT` |
| **Invoice** | INV-3W-0004 |
| **PO** | PO-3W-0004 |
| **Vendor** | V3W-005 -- Diversey Arabia LLC |
| **Branch** | BR-RUH-002 -- McDonald's King Fahd Road, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-1030 |
| **Category** | Cleaning Chemicals |
| **Total** | SAR 14,317.50 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.93 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0004 at WH-RUH-CENTRAL |

**Description:** Cleaning chemicals bulk supply -- Riyadh cluster

**What to verify:**
- Hygiene products stock order
- Full GRN receipt confirmed
- Straightforward 3-way match

---

### SCN-05 -- Paper Cups & Lids (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-CUPS-PERFECT` |
| **Invoice** | INV-3W-0005 |
| **PO** | PO-3W-0005 |
| **Vendor** | V3W-010 -- Arabian Paper Products Co. |
| **Branch** | BR-JED-002 -- McDonald's Corniche, Jeddah |
| **Warehouse** | WH-JED-MAIN |
| **Cost Center** | CC-1020 |
| **Category** | Paper & Takeaway Packaging |
| **Total** | SAR 8,170.75 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.97 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0005 at WH-JED-MAIN |

**Description:** Paper cups and lids stock supply -- Jeddah

**What to verify:**
- Takeaway packaging invoice
- Highest confidence in the set (0.97)
- Full GRN receipt at Jeddah warehouse
- Clean match

---

## Bucket B: Partial Match / GRN-Review-Likely (SCN-06 to SCN-13)

These invoices have GRN discrepancies, PO noise, amount differences, or location mismatches that should produce **PARTIAL_MATCH** or **REQUIRES_REVIEW** results. Agents should be able to assist with resolution.

---

### SCN-06 -- Chicken Patties with OCR PO Noise

| Field | Value |
|-------|-------|
| **Tag** | `3W-CHICKEN-OCR-NOISE` |
| **Invoice** | INV-3W-0006 |
| **PO** | PO-3W-0006 (actual) |
| **PO on Invoice** | PO-3W-00**60** (last 2 digits swapped by OCR) |
| **Vendor** | V3W-001 -- Americana Foods Company |
| **Branch** | BR-RUH-003 -- McDonald's Exit 15 DT, Riyadh |
| **Warehouse** | WH-RUH-SOUTH |
| **Cost Center** | CC-1010 |
| **Category** | Frozen Foods & Proteins |
| **Total** | SAR 14,570.50 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.74 |
| **PO Noise** | `swap_digit` -- last two digits transposed |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0006 at WH-RUH-SOUTH (on actual PO) |

**Description:** Chicken patty supply invoice -- OCR noise on PO

**What to verify:**
- **PO retrieval challenge:** Invoice shows `PO-3W-0060` but actual PO is `PO-3W-0006`
- PO_RETRIEVAL agent should detect the near-miss and resolve
- Once PO is found, amounts should match and GRN confirms full receipt
- Lower confidence (0.74) should trigger agent attention
- Tests PO number fuzzy-match / correction logic

**Expected exceptions:** PO_NOT_FOUND (initially), recoverable via agent

---

### SCN-07 -- Bakery Buns with Partial GRN Receipt

| Field | Value |
|-------|-------|
| **Tag** | `3W-BUNS-PARTIAL-GRN` |
| **Invoice** | INV-3W-0007 |
| **PO** | PO-3W-0007 |
| **Vendor** | V3W-003 -- Al Marai Company |
| **Branch** | BR-JED-001 -- McDonald's Tahlia Street, Jeddah |
| **Warehouse** | WH-JED-MAIN |
| **Cost Center** | CC-1010 |
| **Category** | Bakery & Buns |
| **Total** | SAR 6,201.95 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.91 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | PARTIAL_RECEIPT (80% of PO qty) |
| **GRN** | GRN-3W-0007 at WH-JED-MAIN |

**Description:** Buns and bakery supply invoice -- partial receipt

**What to verify:**
- PO and invoice amounts match, but GRN shows only ~80% of PO quantity received
- THREE_WAY matching detects receipt shortage
- Invoice is billing for full PO amount but warehouse only received partial delivery
- GRN_ANALYSIS agent should flag the discrepancy
- Tests the core THREE_WAY value proposition: catching payment for undelivered goods

**Expected exceptions:** RECEIPT_SHORTAGE, INVOICE_QTY_EXCEEDS_RECEIVED

---

### SCN-08 -- Condiments with Multi-GRN

| Field | Value |
|-------|-------|
| **Tag** | `3W-CONDIMENTS-MULTI-GRN` |
| **Invoice** | INV-3W-0008 |
| **PO** | PO-3W-0008 |
| **Vendor** | V3W-006 -- Binzagr Coca-Cola Saudi |
| **Branch** | BR-RUH-001 -- McDonald's Olaya Street, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-1010 |
| **Category** | Beverages & Dry Goods |
| **Total** | SAR 19,348.75 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.88 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | MULTI_GRN (two partial GRNs) |
| **GRNs** | GRN-3W-0008-A (55% of PO, early), GRN-3W-0008-B (45% of PO, later) -- both at WH-RUH-CENTRAL |

**Description:** Kitchen consumables stock invoice -- multi-GRN

**What to verify:**
- **Two partial GRNs** exist for this PO, neither covers the full order alone
- System must aggregate GRN-3W-0008-A + GRN-3W-0008-B to determine total receipt
- Combined receipt should cover ~100% of PO qty
- Tests GRN aggregation logic in THREE_WAY matching
- If aggregation works correctly, this may resolve to MATCHED

**Expected exceptions:** MULTI_GRN_PARTIAL_RECEIPT (initial), potentially resolvable via aggregation

---

### SCN-09 -- Frozen Goods with Delayed GRN Receipt

| Field | Value |
|-------|-------|
| **Tag** | `3W-FROZEN-DELAYED-GRN` |
| **Invoice** | INV-3W-0009 |
| **PO** | PO-3W-0009 |
| **Vendor** | V3W-009 -- Frozen Express Cold Chain Co. |
| **Branch** | BR-DMM-002 -- McDonald's Dhahran Mall, Dammam |
| **Warehouse** | WH-DMM-DC |
| **Cost Center** | CC-1010 |
| **Category** | Frozen Goods & Cold Chain |
| **Total** | SAR 12,241.75 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.87 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | DELAYED_RECEIPT (45+ days after PO date) |
| **GRN** | GRN-3W-0009 at WH-DMM-DC |

**Description:** Cold chain goods replenishment -- delayed receipt

**What to verify:**
- GRN quantities match PO correctly, but receipt_date is 40-60 days after PO date
- This is anomalously late for a cold chain / frozen goods delivery
- System should flag the timing gap even though qty/amounts match
- Tests temporal validation in GRN analysis
- May warrant review to confirm goods quality was acceptable given delay

**Expected exceptions:** DELAYED_RECEIPT

---

### SCN-10 -- Dairy Supply with Close Amount Delta

| Field | Value |
|-------|-------|
| **Tag** | `3W-DAIRY-CLOSE-AMT` |
| **Invoice** | INV-3W-0010 |
| **PO** | PO-3W-0010 |
| **Vendor** | V3W-002 -- SADAFCO (Saudia Dairy & Foodstuff Co.) |
| **Branch** | BR-RUH-001 -- McDonald's Olaya Street, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-1010 |
| **Category** | Beverages & Dairy |
| **Total** | SAR 18,552.38 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.89 |
| **PO Noise** | None |
| **Amount Delta** | +32.50 SAR (invoice higher than PO) |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0010 at WH-RUH-CENTRAL |

**Description:** Dairy supply invoice -- amount close but slightly off

**What to verify:**
- Invoice total is ~SAR 32.50 higher than PO total
- GRN confirms full receipt (qty match)
- The delta is small relative to total (~0.18%), likely within auto-close tolerance band
- Tests tolerance engine: strict tolerance (2%) should flag, auto-close band (3%) may accept
- May produce PARTIAL_MATCH that auto-closes within the wider tolerance

**Expected exceptions:** AMOUNT_MISMATCH (minor), potentially auto-closeable

---

### SCN-11 -- Uniforms with Location Mismatch

| Field | Value |
|-------|-------|
| **Tag** | `3W-UNIFORM-LOC-MISMATCH` |
| **Invoice** | INV-3W-0011 |
| **PO** | PO-3W-0011 |
| **Vendor** | V3W-007 -- Red Sea Uniforms & Workwear |
| **Branch** | BR-DMM-001 -- McDonald's King Saud Street, Dammam |
| **Warehouse** | WH-DMM-DC (expected from branch) |
| **Cost Center** | CC-2010 |
| **Category** | Uniforms & Housekeeping Stock |
| **Total** | SAR 15,605.50 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.85 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | LOCATION_MISMATCH |
| **GRN** | GRN-3W-0011 at a **different** warehouse (not WH-DMM-DC) |

**Description:** Restaurant uniforms stock invoice -- location mismatch

**What to verify:**
- Invoice references Dammam branch (WH-DMM-DC expected)
- GRN was received at a different warehouse (e.g., WH-RUH-SOUTH or WH-JED-MAIN)
- Amounts and quantities may match, but delivery location is wrong
- Tests THREE_WAY location validation
- Could indicate a routing error or intentional cross-dock; needs human review

**Expected exceptions:** RECEIPT_LOCATION_MISMATCH

---

### SCN-12 -- Packaging with Over-Receipt

| Field | Value |
|-------|-------|
| **Tag** | `3W-PACKAGING-OVER-RECEIPT` |
| **Invoice** | INV-3W-0012 |
| **PO** | PO-3W-0012 |
| **Vendor** | V3W-004 -- Gulf Packaging Industries |
| **Branch** | BR-RUH-002 -- McDonald's King Fahd Road, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-1020 |
| **Category** | Packaging Materials |
| **Total** | SAR 7,488.80 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.90 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | OVER_RECEIPT (110% of PO qty) |
| **GRN** | GRN-3W-0012 at WH-RUH-CENTRAL |

**Description:** Takeaway packaging replenishment -- over-receipt

**What to verify:**
- GRN shows 105-115% of PO quantity received
- Invoice matches PO amounts but warehouse received more than ordered
- Over-delivery is common in packaging bulk orders (vendor rounds up)
- Tests over-receipt detection and tolerance handling
- May be acceptable with minor qty overage, or may require review

**Expected exceptions:** OVER_RECEIPT

---

### SCN-13 -- Frozen Protein with No GRN

| Field | Value |
|-------|-------|
| **Tag** | `3W-PROTEIN-NO-GRN` |
| **Invoice** | INV-3W-0013 |
| **PO** | PO-3W-0013 |
| **Vendor** | V3W-001 -- Americana Foods Company |
| **Branch** | BR-JED-002 -- McDonald's Corniche, Jeddah |
| **Warehouse** | WH-JED-MAIN |
| **Cost Center** | CC-1010 |
| **Category** | Frozen Foods & Proteins |
| **Total** | SAR 14,932.75 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.92 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | NO_GRN |
| **GRN** | None -- no GRN exists for PO-3W-0013 |

**Description:** Frozen protein supply -- GRN not yet received

**What to verify:**
- Invoice and PO match on amounts, but **no GRN exists** for this PO
- This is the critical THREE_WAY gate: cannot approve payment without goods receipt
- GRN_ANALYSIS agent should halt invoice processing
- Tests the fundamental value of THREE_WAY matching over TWO_WAY
- Invoice should NOT be approved -- goods haven't been confirmed received

**Expected exceptions:** GRN_NOT_FOUND

---

## Bucket C: Exception / Fail-Likely (SCN-14 to SCN-20)

These invoices have severe data quality issues, duplicates, corrupted PO references, large mismatches, or missing critical fields that should produce **UNMATCHED** or **ERROR** results requiring significant intervention.

---

### SCN-14 -- Duplicate Packaging Invoice

| Field | Value |
|-------|-------|
| **Tag** | `3W-PACKAGING-DUPLICATE` |
| **Invoice** | INV-3W-0014 |
| **PO** | PO-3W-0014 |
| **Vendor** | V3W-004 -- Gulf Packaging Industries |
| **Branch** | BR-JED-001 -- McDonald's Tahlia Street, Jeddah |
| **Warehouse** | WH-JED-MAIN |
| **Cost Center** | CC-1020 |
| **Category** | Packaging Materials |
| **Total** | SAR 12,023.25 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.92 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | **Yes** -- duplicate of SCN-02 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0014 at WH-JED-MAIN |

**Description:** Duplicate packaging invoice -- same vendor/amount as SCN-02

**What to verify:**
- `is_duplicate=True` flag is set, `duplicate_of` references SCN-02's invoice
- Despite clean extraction and valid PO/GRN, this should be flagged as DUPLICATE_INVOICE
- Tests duplicate detection regardless of other match quality
- Should NOT be approved -- paying twice for the same delivery

**Expected exceptions:** DUPLICATE_INVOICE

---

### SCN-15 -- Cold Chain with Corrupted PO Reference

| Field | Value |
|-------|-------|
| **Tag** | `3W-COLDCHAIN-BAD-PO` |
| **Invoice** | INV-3W-0015 |
| **PO** | PO-3W-0015 (actual, created on back-end) |
| **PO on Invoice** | `P0-3W-0015-X` (malformed: letter O replaced with zero + garbage suffix) |
| **Vendor** | V3W-009 -- Frozen Express Cold Chain Co. |
| **Branch** | BR-DMM-001 -- McDonald's King Saud Street, Dammam |
| **Warehouse** | WH-DMM-DC |
| **Cost Center** | CC-1010 |
| **Category** | Frozen Goods & Cold Chain |
| **Total** | SAR 22,517.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.62 |
| **PO Noise** | `malformed` -- "PO" prefix becomes "P0" + "-X" suffix added |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0015 on actual PO (if agent resolves) |

**Description:** Imported stock invoice with corrupted PO field

**What to verify:**
- Invoice PO field contains `P0-3W-0015-X` (corrupted by OCR)
- Actual PO is `PO-3W-0015` -- letter O was read as digit zero, plus garbage suffix
- Low extraction confidence (0.62) reinforces data quality concern
- PO_RETRIEVAL agent may attempt correction but `malformed` is harder than `swap_digit`
- Even if PO is found, the low confidence may still route to review

**Expected exceptions:** PO_NOT_FOUND, LOW_CONFIDENCE

---

### SCN-16 -- High-Value Spare Parts with Partial GRN

| Field | Value |
|-------|-------|
| **Tag** | `3W-SPARES-HIGH-VALUE` |
| **Invoice** | INV-3W-0016 |
| **PO** | PO-3W-0016 |
| **Vendor** | V3W-008 -- Henny Penny Parts Arabia |
| **Branch** | BR-RUH-001 -- McDonald's Olaya Street, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-4010 |
| **Category** | Spare Parts & Equipment |
| **Total** | SAR 176,697.50 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.90 |
| **PO Noise** | None |
| **Amount Delta** | +4,200.00 SAR |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | PARTIAL_RECEIPT |
| **GRN** | GRN-3W-0016 at WH-RUH-CENTRAL (60-85% of PO qty) |

**Description:** High-value spare parts goods invoice -- Riyadh

**What to verify:**
- **Highest value invoice** in the set (SAR 176k+)
- Invoice is SAR 4,200 higher than PO (surcharge/additional parts)
- GRN shows only partial receipt (60-85% of PO qty)
- **Triple hit:** high value + amount mismatch + partial GRN receipt
- Should definitely route to review -- human approval required
- Tests interaction between value thresholds, amount tolerance, and GRN shortage

**Expected exceptions:** AMOUNT_MISMATCH, RECEIPT_SHORTAGE, HIGH_VALUE

---

### SCN-17 -- Beverages with Weak Vendor & Tax Mismatch

| Field | Value |
|-------|-------|
| **Tag** | `3W-BEVERAGE-WEAK-VENDOR` |
| **Invoice** | INV-3W-0017 |
| **PO** | PO-3W-0017 |
| **Vendor** | V3W-002 -- SADAFCO (Saudia Dairy & Foodstuff Co.) |
| **Branch** | BR-JED-002 -- McDonald's Corniche, Jeddah |
| **Warehouse** | WH-JED-MAIN |
| **Cost Center** | (missing) |
| **Category** | Beverages & Dairy |
| **Total** | SAR 13,464.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.58 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | 10% (incorrect -- should be 15%) |
| **Missing Fields** | cost_center |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0017 at WH-JED-MAIN |

**Description:** Invoice with weak vendor extraction -- Arabic OCR

**What to verify:**
- **Low confidence (0.58):** vendor name extracted from Arabic OCR
- Tax calculated at 10% instead of standard 15% -- TAX_MISMATCH expected
- Cost center field is missing from extraction
- GRN is fine (full receipt) but invoice data quality is poor
- Tests multi-dimensional failure: confidence + tax + missing fields
- Vendor resolution may still work via Arabic alias matching

**Expected exceptions:** TAX_MISMATCH, LOW_CONFIDENCE, MISSING_FIELD (cost_center)

---

### SCN-18 -- Paper Products with No PO Reference

| Field | Value |
|-------|-------|
| **Tag** | `3W-PAPER-MISSING-PO` |
| **Invoice** | INV-3W-0018 |
| **PO** | None -- PO reference completely missing |
| **Vendor** | V3W-010 -- Arabian Paper Products Co. |
| **Branch** | BR-DMM-002 -- McDonald's Dhahran Mall, Dammam |
| **Warehouse** | WH-DMM-DC |
| **Cost Center** | CC-1020 |
| **Category** | Paper & Takeaway Packaging |
| **Total** | SAR 13,698.80 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.52 |
| **PO Noise** | `missing` -- no PO field at all |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | po_number, currency |
| **Duplicate** | No |
| **GRN Behaviour** | NO_GRN (no PO means no GRN linkage) |
| **GRN** | None |

**Description:** Paper products invoice -- missing PO reference entirely

**What to verify:**
- **No PO number** on the invoice at all -- extraction completely missed it
- Currency field also missing
- Lowest extraction confidence in the set (0.52)
- No PO means no GRN linkage possible -- THREE_WAY matching cannot proceed
- PO_RETRIEVAL agent would need to search by vendor + amount to find a candidate
- Tests the complete absence scenario

**Expected exceptions:** PO_NOT_FOUND, MISSING_FIELD (po_number, currency), LOW_CONFIDENCE

---

### SCN-19 -- Cleaning Chemicals with Tax & Amount Mismatch

| Field | Value |
|-------|-------|
| **Tag** | `3W-CHEMICAL-TAX-MISMATCH` |
| **Invoice** | INV-3W-0019 |
| **PO** | PO-3W-0019 |
| **Vendor** | V3W-005 -- Diversey Arabia LLC |
| **Branch** | BR-RUH-003 -- McDonald's Exit 15 DT, Riyadh |
| **Warehouse** | WH-RUH-SOUTH |
| **Cost Center** | CC-1030 |
| **Category** | Cleaning Chemicals |
| **Total** | SAR 25,236.75 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.86 |
| **PO Noise** | None |
| **Amount Delta** | -150.00 SAR (invoice lower than PO) |
| **Tax Override** | 5% (incorrect -- should be 15%) |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0019 at WH-RUH-SOUTH |

**Description:** Cleaning supply invoice -- tax and amount mismatch

**What to verify:**
- Invoice uses 5% VAT instead of standard 15% VAT -- significant tax discrepancy
- Invoice subtotal is SAR 150 lower than PO (shortfall on line 1)
- GRN confirms full receipt -- goods were delivered, but invoice math is wrong
- Tests dual mismatch: incorrect tax rate + amount difference
- May indicate vendor billing error or different tax treatment

**Expected exceptions:** TAX_MISMATCH, AMOUNT_MISMATCH

---

### SCN-20 -- Frozen Goods Qty Exceeds GRN Received

| Field | Value |
|-------|-------|
| **Tag** | `3W-FROZEN-QTY-EXCEEDS` |
| **Invoice** | INV-3W-0020 |
| **PO** | PO-3W-0020 |
| **Vendor** | V3W-009 -- Frozen Express Cold Chain Co. |
| **Branch** | BR-RUH-001 -- McDonald's Olaya Street, Riyadh |
| **Warehouse** | WH-RUH-CENTRAL |
| **Cost Center** | CC-1010 |
| **Category** | Frozen Goods & Cold Chain |
| **Total** | SAR 7,921.20 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.88 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |
| **GRN Behaviour** | PARTIAL_RECEIPT (60% of PO qty) |
| **GRN** | GRN-3W-0020 at WH-RUH-CENTRAL |

**Description:** Frozen goods invoice -- qty exceeds received

**What to verify:**
- Invoice claims full PO quantity and matching amounts
- But GRN only confirmed receipt of ~60% of PO quantity
- Invoice is attempting to bill for goods **not yet received**
- This is the classic THREE_WAY protection scenario
- Without GRN verification, TWO_WAY matching would approve this invoice
- Should be flagged and blocked until remaining goods are received or invoice is adjusted

**Expected exceptions:** INVOICE_QTY_EXCEEDS_RECEIVED, RECEIPT_SHORTAGE

---

## Coverage Matrices

### Scenario Coverage Matrix

| # | Tag | Vendor | Branch | GRN Behaviour | PO Noise | Amt Delta | Tax | Dup | Confidence | Expected Outcome |
|---|-----|--------|--------|---------------|----------|-----------|-----|-----|------------|-----------------|
| 01 | 3W-FRIES-PERFECT | V3W-001 | BR-RUH-001 | FULL_RECEIPT | None | 0 | 15% | No | 0.96 | MATCHED |
| 02 | 3W-PACKAGING-PERFECT | V3W-004 | BR-JED-001 | FULL_RECEIPT | None | 0 | 15% | No | 0.94 | MATCHED |
| 03 | 3W-BEVERAGE-PERFECT | V3W-006 | BR-DMM-001 | FULL_RECEIPT | None | 0 | 15% | No | 0.95 | MATCHED |
| 04 | 3W-CLEANING-PERFECT | V3W-005 | BR-RUH-002 | FULL_RECEIPT | None | 0 | 15% | No | 0.93 | MATCHED |
| 05 | 3W-CUPS-PERFECT | V3W-010 | BR-JED-002 | FULL_RECEIPT | None | 0 | 15% | No | 0.97 | MATCHED |
| 06 | 3W-CHICKEN-OCR-NOISE | V3W-001 | BR-RUH-003 | FULL_RECEIPT | swap_digit | 0 | 15% | No | 0.74 | PARTIAL_MATCH |
| 07 | 3W-BUNS-PARTIAL-GRN | V3W-003 | BR-JED-001 | PARTIAL_RECEIPT | None | 0 | 15% | No | 0.91 | REQUIRES_REVIEW |
| 08 | 3W-CONDIMENTS-MULTI-GRN | V3W-006 | BR-RUH-001 | MULTI_GRN | None | 0 | 15% | No | 0.88 | PARTIAL_MATCH |
| 09 | 3W-FROZEN-DELAYED-GRN | V3W-009 | BR-DMM-002 | DELAYED_RECEIPT | None | 0 | 15% | No | 0.87 | PARTIAL_MATCH |
| 10 | 3W-DAIRY-CLOSE-AMT | V3W-002 | BR-RUH-001 | FULL_RECEIPT | None | +32.50 | 15% | No | 0.89 | PARTIAL_MATCH |
| 11 | 3W-UNIFORM-LOC-MISMATCH | V3W-007 | BR-DMM-001 | LOCATION_MISMATCH | None | 0 | 15% | No | 0.85 | REQUIRES_REVIEW |
| 12 | 3W-PACKAGING-OVER-RECEIPT | V3W-004 | BR-RUH-002 | OVER_RECEIPT | None | 0 | 15% | No | 0.90 | PARTIAL_MATCH |
| 13 | 3W-PROTEIN-NO-GRN | V3W-001 | BR-JED-002 | NO_GRN | None | 0 | 15% | No | 0.92 | REQUIRES_REVIEW |
| 14 | 3W-PACKAGING-DUPLICATE | V3W-004 | BR-JED-001 | FULL_RECEIPT | None | 0 | 15% | Yes | 0.92 | UNMATCHED |
| 15 | 3W-COLDCHAIN-BAD-PO | V3W-009 | BR-DMM-001 | FULL_RECEIPT | malformed | 0 | 15% | No | 0.62 | UNMATCHED |
| 16 | 3W-SPARES-HIGH-VALUE | V3W-008 | BR-RUH-001 | PARTIAL_RECEIPT | None | +4200 | 15% | No | 0.90 | REQUIRES_REVIEW |
| 17 | 3W-BEVERAGE-WEAK-VENDOR | V3W-002 | BR-JED-002 | FULL_RECEIPT | None | 0 | 10% | No | 0.58 | UNMATCHED |
| 18 | 3W-PAPER-MISSING-PO | V3W-010 | BR-DMM-002 | NO_GRN | missing | 0 | 15% | No | 0.52 | UNMATCHED |
| 19 | 3W-CHEMICAL-TAX-MISMATCH | V3W-005 | BR-RUH-003 | FULL_RECEIPT | None | -150 | 5% | No | 0.86 | PARTIAL_MATCH |
| 20 | 3W-FROZEN-QTY-EXCEEDS | V3W-009 | BR-RUH-001 | PARTIAL_RECEIPT | None | 0 | 15% | No | 0.88 | REQUIRES_REVIEW |

### GRN Behaviour Coverage

| GRN Behaviour | Count | Scenarios |
|---------------|-------|-----------|
| FULL_RECEIPT | 10 | SCN-01, 02, 03, 04, 05, 06, 10, 14, 15, 17, 19 |
| NO_GRN | 2 | SCN-13, 18 |
| PARTIAL_RECEIPT | 3 | SCN-07, 16, 20 |
| OVER_RECEIPT | 1 | SCN-12 |
| MULTI_GRN | 1 | SCN-08 |
| DELAYED_RECEIPT | 1 | SCN-09 |
| LOCATION_MISMATCH | 1 | SCN-11 |

### Exception Coverage

| Exception Type | Triggered By | Description |
|----------------|-------------|-------------|
| GRN_NOT_FOUND | SCN-13, 18 | No GRN exists for the PO |
| RECEIPT_SHORTAGE | SCN-07, 16, 20 | GRN qty < PO/Invoice qty |
| INVOICE_QTY_EXCEEDS_RECEIVED | SCN-07, 20 | Invoice claims more than GRN confirms |
| OVER_RECEIPT | SCN-12 | GRN qty > PO qty |
| MULTI_GRN_PARTIAL_RECEIPT | SCN-08 | Multiple GRNs need aggregation |
| DELAYED_RECEIPT | SCN-09 | GRN timing anomaly |
| RECEIPT_LOCATION_MISMATCH | SCN-11 | GRN warehouse differs from expected |
| AMOUNT_MISMATCH | SCN-10, 16, 19 | Invoice total differs from PO total |
| TAX_MISMATCH | SCN-17, 19 | Invoice VAT rate differs from expected 15% |
| PO_NOT_FOUND | SCN-06, 15, 18 | PO number doesn't resolve (noise/missing) |
| DUPLICATE_INVOICE | SCN-14 | Invoice flagged as duplicate |
| LOW_CONFIDENCE | SCN-15, 17, 18 | Extraction confidence < 0.65 |
| MISSING_FIELD | SCN-17, 18 | Required fields missing from extraction |
| HIGH_VALUE | SCN-16 | Invoice total > SAR 50,000 |

### Data Quality Signal Coverage

| Signal | Scenarios | Notes |
|--------|-----------|-------|
| High confidence (>= 0.90) | SCN-01..05, 07, 12, 13, 14, 16 | Clean extraction |
| Medium confidence (0.70-0.89) | SCN-06, 08, 09, 10, 11, 19, 20 | Minor issues |
| Low confidence (< 0.70) | SCN-15, 17, 18 | Significant extraction problems |
| PO noise: swap_digit | SCN-06 | Recoverable via fuzzy match |
| PO noise: malformed | SCN-15 | Hard to recover |
| PO noise: missing | SCN-18 | No PO reference at all |
| Tax override (non-15%) | SCN-17 (10%), SCN-19 (5%) | Incorrect VAT rates |
| Amount delta (positive) | SCN-10 (+32.50), SCN-16 (+4200) | Invoice higher than PO |
| Amount delta (negative) | SCN-19 (-150) | Invoice lower than PO |
| Missing fields | SCN-17 (cost_center), SCN-18 (po_number, currency) | Incomplete extraction |
| Duplicate flag | SCN-14 | Duplicate of SCN-02 |
| High-value (>50k) | SCN-16 (176k+) | Requires elevated approval |

### Vendor Coverage

| Vendor | Scenarios | Count |
|--------|-----------|-------|
| V3W-001 (Americana Foods) | 01, 06, 13 | 3 |
| V3W-002 (SADAFCO) | 10, 17 | 2 |
| V3W-003 (Al Marai) | 07 | 1 |
| V3W-004 (Gulf Packaging) | 02, 12, 14 | 3 |
| V3W-005 (Diversey Arabia) | 04, 19 | 2 |
| V3W-006 (Binzagr Coca-Cola) | 03, 08 | 2 |
| V3W-007 (Red Sea Uniforms) | 11 | 1 |
| V3W-008 (Henny Penny Parts) | 16 | 1 |
| V3W-009 (Frozen Express) | 09, 15, 20 | 3 |
| V3W-010 (Arabian Paper) | 05, 18 | 2 |

### Branch / City Coverage

| City | Branches Used | Scenarios |
|------|--------------|-----------|
| Riyadh | BR-RUH-001, BR-RUH-002, BR-RUH-003 | 01, 04, 06, 08, 10, 12, 16, 19, 20 |
| Jeddah | BR-JED-001, BR-JED-002 | 02, 05, 07, 13, 14, 17 |
| Dammam | BR-DMM-001, BR-DMM-002 | 03, 09, 11, 15, 18 |

### Category Coverage

| Category | Scenarios |
|----------|-----------|
| Frozen Foods & Proteins | 01, 06, 13 |
| Packaging Materials | 02, 12, 14 |
| Beverages & Dry Goods | 03, 08 |
| Cleaning Chemicals | 04, 19 |
| Paper & Takeaway Packaging | 05, 18 |
| Bakery & Buns | 07 |
| Beverages & Dairy | 10, 17 |
| Uniforms & Housekeeping Stock | 11 |
| Spare Parts & Equipment | 16 |
| Frozen Goods & Cold Chain | 09, 15, 20 |

---

## QA & Large Mode: Random Scenarios

In `qa` mode (+15) and `large` mode (+40), additional random invoices are generated starting at SCN-21. These use a seeded random number generator (default seed: 42) for deterministic output.

### Random Distribution

| Attribute | Distribution |
|-----------|-------------|
| **Vendor** | Uniform across all 10 vendors |
| **Branch** | Uniform across all 7 branches |
| **Confidence** | Uniform 0.48 - 0.98 |
| **PO noise** | None: 70%, swap_digit: 12%, malformed: 10%, missing: 8% |
| **Amount delta** | 0 (40%), +95/+250/+420/+1800 (20%), -80/-300 (20%), 0 (20%) |
| **Tax override** | Standard 15%: 72%, 5%: 10%, 10%: 10%, missing: 8% |
| **Duplicate** | 8% chance |
| **Cost center** | Random from 5 codes + empty (missing) |
| **GRN behaviour** | FULL_RECEIPT: ~33%, PARTIAL: ~11%, NO_GRN: ~11%, OVER: ~11%, MULTI: ~11%, DELAYED: ~11%, LOC_MISMATCH: ~11% |
| **Invoice status** | Based on confidence/noise: EXTRACTED if conf < 0.60 or PO missing/malformed; otherwise weighted toward READY_FOR_RECON |

Random scenarios are tagged as `3W-GEN-NNNN` (e.g., `3W-GEN-0021`).

---

## UI Verification Checklist

After seeding, verify the following in the web application:

### Invoice List Page
- [ ] All 20 (demo) / 35 (qa) / 60 (large) THREE_WAY invoices appear with `INV-3W-*` numbers
- [ ] Status column shows correct values (READY_FOR_RECON, EXTRACTED)
- [ ] Vendor names display correctly (even Arabic-extracted ones)
- [ ] Total amounts are formatted with SAR currency
- [ ] Confidence values display as percentages (e.g., 96%, not 0.96)
- [ ] Duplicate flag (SCN-14) is visually indicated

### Invoice Detail Page
- [ ] PO reference links to the correct PO (where applicable)
- [ ] PO noise scenarios (SCN-06, 15) show the noisy PO reference, not the actual PO
- [ ] Missing PO scenario (SCN-18) shows blank/empty PO field
- [ ] Line items display with correct quantities, prices, and amounts
- [ ] Extraction confidence bar/badge renders correctly
- [ ] Extraction raw JSON is accessible for each invoice
- [ ] GRN information visible (warehouse, receipt date, quantities -- where GRN exists)
- [ ] Missing cost center (SCN-17) and currency (SCN-18) fields display appropriately

### Reconciliation Trigger
- [ ] Invoices in READY_FOR_RECON status appear in the reconciliation candidates list
- [ ] Invoices in EXTRACTED status (SCN-06, 15, 17, 18) are NOT in the candidates list
- [ ] Selecting and starting reconciliation creates ReconciliationRun + Results
- [ ] THREE_WAY matching mode is correctly resolved for goods/stock invoices
- [ ] GRN verification is performed as part of THREE_WAY matching
- [ ] Agent pipeline triggers for non-MATCHED results
- [ ] GRN exceptions (shortage, no-GRN, over-receipt, etc.) appear in results

### GRN Verification (THREE_WAY-specific)
- [ ] SCN-01..05: Full receipt GRNs pass verification cleanly
- [ ] SCN-07: Partial receipt flagged as shortage
- [ ] SCN-08: Multi-GRN aggregation attempted
- [ ] SCN-09: Delayed receipt timing flagged
- [ ] SCN-11: Location mismatch between invoice branch and GRN warehouse detected
- [ ] SCN-12: Over-receipt quantity flagged
- [ ] SCN-13: No GRN found, blocking invoice approval
- [ ] SCN-20: Invoice qty exceeds GRN received qty
