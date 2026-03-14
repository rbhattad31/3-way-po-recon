# THREE_WAY PO Invoice – Test Scenarios

> **Seed command:** `python manage.py seed_three_way_invoices`
> **Path:** `apps/documents/management/commands/seed_three_way_invoices.py`
> **Domain:** McDonald's Saudi Arabia – AP Automation (Goods/Stock Invoices)
> **Processing path:** THREE_WAY (Invoice vs PO vs GRN — full goods receipt verification)

---

## Overview

This document describes the **24 deterministic test scenarios** seeded by the `seed_three_way_invoices` management command. Each scenario creates a **goods/stock invoice** with a backing Purchase Order and (optionally) one or more Goods Receipt Notes, designed to exercise a specific THREE_WAY reconciliation outcome when matching is triggered later from the invoice detail page.

**What is seeded:** Vendors, vendor aliases (including OCR typo variations), POs, PO line items, GRNs, GRN line items, invoices, invoice line items, document uploads, extraction results, and raw/normalized extraction JSON payloads.

**What is NOT seeded:** AP Cases, reconciliation runs/results/exceptions, agent runs, review assignments, audit events. These are created later when reconciliation is triggered.

### Seed Modes

| Mode | Deterministic | Bulk Random | Total |
|------|--------------|-------------|-------|
| `demo` (default) | 24 | 0 | 24 |
| `qa` | 24 | 30 | 54 |
| `large` | 24 | 100 | 124 |

### Command Examples

```bash
python manage.py seed_three_way_invoices                     # demo mode (24)
python manage.py seed_three_way_invoices --mode=qa           # +30 random
python manage.py seed_three_way_invoices --mode=large        # +100 random
python manage.py seed_three_way_invoices --reset             # delete & recreate
python manage.py seed_three_way_invoices --summary           # print table
python manage.py seed_three_way_invoices --reset --mode=qa --summary
python manage.py seed_three_way_invoices --seed=99           # alternate RNG seed
```

### Prerequisites

Run `python manage.py seed_rbac` first to create RBAC roles & permissions.

### Expected THREE_WAY Processing Pipeline

Once reconciliation is triggered, each invoice passes through these stages:

```
INTAKE → EXTRACTION → PATH_RESOLUTION → PO_RETRIEVAL → THREE_WAY_MATCHING → GRN_ANALYSIS → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
```

### GRN Behaviours

Unlike TWO_WAY, THREE_WAY matching requires verification against Goods Receipt Notes. Each scenario's GRN behaviour is determined by its `special` directives:

| Behaviour | How Triggered | Expected Exception |
|-----------|---------------|-------------------|
| `FULL_RECEIPT` | Default — GRN qty == PO qty | None (match) |
| `NO_GRN` | `skip_grn: True` or no PO created | GRN_NOT_FOUND |
| `PARTIAL_RECEIPT` | `receipt_pct` < 1.0 (e.g. 0.70, 0.80) | RECEIPT_SHORTAGE / INVOICE_QTY_EXCEEDS_RECEIVED |
| `OVER_RECEIPT` | `receipt_pct` > 1.0 (e.g. 1.15) | OVER_RECEIPT |
| `MULTI_GRN` | `multi_grn_drops` > 1 (e.g. 3 drops) | MULTI_GRN_PARTIAL_RECEIPT |
| `DELAYED_RECEIPT` | `grn_delay_days` > 0 (receipt after invoice date) | DELAYED_RECEIPT |
| `LOCATION_MISMATCH` | `grn_warehouse` differs from scenario warehouse | RECEIPT_LOCATION_MISMATCH |

---

## Master Data

### Vendors (12)

| Code | Name | Category | Aliases |
|------|------|----------|---------|
| V3W-001 | Arabian Foodstuff Co. Ltd. | Frozen Foods | Arabian Foodstuff, AFC Ltd, Arabian Foodstuff Company, شركة المواد الغذائية العربية, ARABIAN FOODSTUF CO |
| V3W-002 | Al Kabeer Frozen Foods | Frozen Foods | Al-Kabeer, Kabeer Frozen, Al Kabeer Frozen, الكبير للأغذية المجمدة, ALKABEER FROZEN FDS |
| V3W-003 | Napco National Paper Products Co. | Packaging | NAPCO, Napco National, Napco Paper Products, نابكو للمنتجات الورقية, NAPC0 NATIONAL |
| V3W-004 | Saudi Paper Manufacturing Co. | Packaging | SPM Co, Saudi Paper Mfg, Saudi Paper, شركة الورق السعودية, SAUDI PAPER MFG CO. |
| V3W-005 | Coca-Cola Bottling Co. of Saudi Arabia | Beverages | CCBA Saudi, Coca Cola KSA, Coca-Cola Saudi Arabia, كوكاكولا السعودية, COCA COLA BOTTLING SA |
| V3W-006 | Al Wazzan Trading & Supplies | Cleaning & Consumables | Al-Wazzan, Wazzan Supplies, Al Wazzan Trading, الوزان للتجارة والتوريدات, ALWAZZAN TRADING |
| V3W-007 | Henny Penny Arabia LLC | Kitchen Equipment Parts | Henny Penny KSA, HP Arabia, Henny Penny Parts, هيني بيني العربية, HENNY PENNY ARABIA |
| V3W-008 | Al Hokair Uniform Solutions | Uniforms & Housekeeping | Al-Hokair Uniforms, Hokair Uniform, Al Hokair Solutions, الحكير للأزياء الموحدة, ALHOKAIR UNIFORM |
| V3W-009 | IFFCO Saudi Arabia | Food Ingredients | IFFCO KSA, IFFCO Saudi, IFFCO Group KSA, إيفكو السعودية, IFFC0 SAUDI ARABIA |
| V3W-010 | Lamb Weston Arabia | Frozen Potato Products | Lamb Weston KSA, LW Arabia, Lamb Weston, لامب وستون العربية, LAMB WEST0N ARABIA |
| V3W-011 | Almarai Company | Dairy & Sauces | Al-Marai, Almarai Co, Almarai Company JSC, المراعي, ALMARAI C0MPANY |
| V3W-012 | Saudi Modern Bakeries Co. | Bakery & Buns | Saudi Bakeries, SMB Co, Saudi Modern Bakeries, شركة المخابز السعودية الحديثة, SAUDI MODERN BAKRIES |

All vendors include Arabic aliases and OCR-damaged aliases (zero-for-O, truncations, typos) for extraction testing.

### Branches (6)

| Code | Name | City |
|------|------|------|
| BR-RUH-001 | McDonald's Olaya Street | Riyadh |
| BR-RUH-002 | McDonald's King Fahd Road | Riyadh |
| BR-JED-001 | McDonald's Tahlia Street | Jeddah |
| BR-JED-002 | McDonald's Corniche | Jeddah |
| BR-DMM-001 | McDonald's King Saud Street | Dammam |
| BR-DMM-002 | McDonald's Dhahran Mall | Dammam |

### Warehouses (4)

| Code | Name | City | Aliases |
|------|------|------|---------|
| RIYADH_DC | Riyadh Distribution Center | Riyadh | Riyadh DC, RUH DC, Central DC Riyadh |
| JEDDAH_DC | Jeddah Distribution Center | Jeddah | Jeddah DC, JED DC |
| DAMMAM_DC | Dammam Distribution Center | Dammam | Dammam DC, DMM DC, Eastern Province DC |
| CENTRAL_KITCHEN | Central Kitchen — Riyadh | Riyadh | CK Riyadh, Central Production Kitchen |

### Cost Centers (5)

| Code | Department |
|------|-----------|
| OPS_RIYADH | Store Operations — Riyadh |
| OPS_JEDDAH | Store Operations — Jeddah |
| OPS_DAMMAM | Store Operations — Dammam |
| SUPPLY_CHAIN | Supply Chain Management |
| WAREHOUSE_OPS | Warehouse Operations |

### Goods Line Item Catalog (10 categories)

| Category | Example Items | UoMs |
|----------|--------------|------|
| Frozen Foods | Beef Patties, Chicken Breast, Crispy Strips, Fish Fillet, Nuggets | CTN, BAG, BOX |
| Packaging | Paper Cups 16oz/22oz, Burger Wrappers, Takeaway Bags, Napkins, Fry Cartons | CASE |
| Beverages | Coca-Cola/Fanta/Sprite BiB Syrup, Iced Tea, CO2 Cylinder | BIB, CYL |
| Cleaning & Consumables | Degreaser, Sanitizer, Hand Soap, Vinyl Gloves, Floor Cleaner | EA, BOX |
| Kitchen Equipment Parts | Fryer Heating Element, Grill Platen, Soft-Serve Pump Kit, Oil Filter | EA, SET |
| Uniforms & Housekeeping | Crew Polo, Kitchen Apron, Safety Shoes, Hair Nets, Mop & Bucket | EA, PAIR, BOX, SET |
| Food Ingredients | Cooking Oil, Sesame Seeds, Flour, Salt, Sugar | DRUM, SACK |
| Frozen Potato Products | French Fries 9mm, Hash Browns, Seasoned Wedges, Curly Fries | CTN, CASE |
| Dairy & Sauces | Cheese Slices, Soft-Serve Mix, Shake Mix, Big Mac Sauce, UHT Creamer | BOX, CTN, BAG, PCH |
| Bakery & Buns | Sesame Burger Buns, Big Mac Buns, English Muffins, Artisan Rolls | TRAY, CASE |

### PO Number Format Variations

| Format | Template | Example | Used By |
|--------|----------|---------|---------|
| `clean` | `PO-3W-{num:04d}` | PO-3W-0001 | Most scenarios |
| `normalized` | `PO3W{num:04d}` | PO3W0008 | SCN-08 |
| `hash_prefix` | `PO#3W{num:04d}` | PO#3W0023 | SCN-23 |
| `ocr_damaged` | `P0-3W-{num:04d}` | P0-3W-0005 | SCN-05 |
| `malformed` | *(per-scenario override)* | PO 3W 00!0 | SCN-10 |
| `missing` | *(empty string)* | — | SCN-07 |

---

## Scenario Buckets

The 24 scenarios are organized into **6 buckets** based on expected reconciliation outcome:

| Bucket | Scenarios | Theme | Expected Result |
|--------|-----------|-------|----------------|
| **A. Clean Matches** | SCN-01 through SCN-04 | Perfect PO + GRN match | MATCHED |
| **B. Agent Recovery Required** | SCN-05 through SCN-08 | OCR damage, alias mismatch, missing PO, warehouse noise | PARTIAL_MATCH / REVIEW_REQUIRED |
| **C. Exception-Prone** | SCN-09 through SCN-12 | Duplicate, malformed PO, price mismatch, missing vendor | REVIEW_REQUIRED / ESCALATION |
| **D. GRN Agent Triggers** | SCN-13 through SCN-16 | No GRN, shortage, over-receipt, multi-GRN | GRN_EXCEPTION / AUTO_CLOSE |
| **E. Special Conditions** | SCN-17 through SCN-20 | Delayed receipt, warehouse mismatch, missing cost center, missing tax | GRN_EXCEPTION / REVIEW_REQUIRED |
| **F. Edge Cases** | SCN-21 through SCN-24 | Dual mismatch, duplicate amount mirror, missing currency, qty exceeds received | REVIEW_REQUIRED / GRN_EXCEPTION |

---

## Bucket A: Clean Matches (SCN-01 to SCN-04)

These invoices have clean data, valid PO references, full GRN receipts matching PO quantities, correct VAT (15%), and high extraction confidence. When reconciliation runs, they should produce a **MATCHED** result with no exceptions.

---

### SCN-01 — Frozen Fries Stock Replenishment (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-CLEAN-FRIES-RIYADH` |
| **Invoice** | INV-3W-0001 |
| **PO** | PO-3W-0001 |
| **Vendor** | V3W-010 — Lamb Weston Arabia |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | WAREHOUSE_OPS |
| **Category** | Frozen Potato Products |
| **Line Count** | 3 lines, qty range 10–30 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.96 |
| **PO Format** | `clean` — PO-3W-0001 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0001 at RIYADH_DC |
| **Exceptions** | None |
| **Special** | None |

**Description:** Frozen fries stock replenishment — Riyadh DC, clean PO and GRN

**What to verify:**
- Invoice amounts match PO amounts exactly
- GRN quantities match PO quantities exactly (full receipt)
- Vendor resolves correctly
- PO retrieval finds PO-3W-0001 on first attempt
- THREE_WAY matching produces MATCHED
- No exceptions generated

---

### SCN-02 — Packaging Materials (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-CLEAN-PACKAGING-JEDDAH` |
| **Invoice** | INV-3W-0002 |
| **PO** | PO-3W-0002 |
| **Vendor** | V3W-003 — Napco National Paper Products Co. |
| **Branch** | BR-JED-001 — McDonald's Tahlia Street, Jeddah |
| **Warehouse** | JEDDAH_DC — Jeddah Distribution Center |
| **Cost Center** | OPS_JEDDAH |
| **Category** | Packaging |
| **Line Count** | 4 lines, qty range 20–60 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.94 |
| **PO Format** | `clean` — PO-3W-0002 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0002 at JEDDAH_DC |
| **Exceptions** | None |
| **Special** | None |

**Description:** Packaging material supply — Jeddah DC, perfect match

**What to verify:**
- Standard packaging stock order with clean extraction
- PO match is immediate
- GRN confirms full receipt at Jeddah warehouse
- This invoice's number (INV-3W-0002) is later duplicated by SCN-09 (duplicate test)
- Auto-close eligible

---

### SCN-03 — Beverage Concentrate Shipment (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-CLEAN-BEVERAGE-DAMMAM` |
| **Invoice** | INV-3W-0003 |
| **PO** | PO-3W-0003 |
| **Vendor** | V3W-005 — Coca-Cola Bottling Co. of Saudi Arabia |
| **Branch** | BR-DMM-001 — McDonald's King Saud Street, Dammam |
| **Warehouse** | DAMMAM_DC — Dammam Distribution Center |
| **Cost Center** | OPS_DAMMAM |
| **Category** | Beverages |
| **Line Count** | 3 lines, qty range 5–15 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.97 |
| **PO Format** | `clean` — PO-3W-0003 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0003 at DAMMAM_DC |
| **Exceptions** | None |
| **Special** | None |

**Description:** Beverage concentrate shipment — Dammam DC, full receipt

**What to verify:**
- Highest confidence in Bucket A (0.97)
- BiB syrups and CO2 supply
- Full GRN receipt at Dammam DC
- Clean 3-way match: Invoice = PO = GRN

---

### SCN-04 — Cleaning Chemicals Bulk Supply (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `3W-CLEAN-CHEMICALS-BULK` |
| **Invoice** | INV-3W-0004 |
| **PO** | PO-3W-0004 |
| **Vendor** | V3W-006 — Al Wazzan Trading & Supplies |
| **Branch** | BR-RUH-002 — McDonald's King Fahd Road, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | SUPPLY_CHAIN |
| **Category** | Cleaning & Consumables |
| **Line Count** | 4 lines, qty range 8–25 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.93 |
| **PO Format** | `clean` — PO-3W-0004 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0004 at RIYADH_DC |
| **Exceptions** | None |
| **Special** | None |

**Description:** Cleaning chemicals bulk supply — Riyadh DC, all quantities match

**What to verify:**
- Hygiene products stock order
- Full GRN receipt confirmed
- Straightforward 3-way match

---

## Bucket B: Agent Recovery Required (SCN-05 to SCN-08)

These invoices have data quality issues that require agent assistance: OCR-damaged PO references, vendor alias variations, missing PO numbers, or warehouse text noise. Expected outcomes are **PARTIAL_MATCH** or **REVIEW_REQUIRED**.

---

### SCN-05 — Chicken Patties with OCR-Damaged PO

| Field | Value |
|-------|-------|
| **Tag** | `3W-AGENT-OCR-CHICKEN` |
| **Invoice** | INV-3W-0005 |
| **PO** | PO-3W-0005 (actual) |
| **PO on Invoice** | `P0-3W-0005` (letter O replaced with digit zero) |
| **Vendor** | V3W-002 — Al Kabeer Frozen Foods |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | WAREHOUSE_OPS |
| **Category** | Frozen Foods |
| **Line Count** | 3 lines, qty range 10–40 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.72 |
| **PO Format** | `ocr_damaged` — P0-3W-0005 (zero for O) |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0005 at RIYADH_DC (on actual PO) |
| **Exceptions** | — |
| **Special** | `ocr_po_variation: "P0-3W-0005"` |

**Description:** Chicken patties invoice with OCR-damaged PO reference

**What to verify:**
- Invoice PO field shows `P0-3W-0005` (zero-for-O OCR error) but actual PO is `PO-3W-0005`
- PO_RETRIEVAL agent should detect the near-miss and resolve
- Once PO is found, amounts should match and GRN confirms full receipt
- Lower confidence (0.72) should trigger agent attention
- Tests PO number fuzzy-match / OCR correction logic

**Expected agent trigger:** PO_NOT_FOUND (initially), recoverable via PO agent

---

### SCN-06 — Burger Buns with Vendor Alias Variation

| Field | Value |
|-------|-------|
| **Tag** | `3W-AGENT-ALIAS-BUNS` |
| **Invoice** | INV-3W-0006 |
| **PO** | PO-3W-0006 |
| **Vendor** | V3W-012 — Saudi Modern Bakeries Co. |
| **Vendor on Invoice** | `SAUDI MODERN BAKRIES` (OCR typo — missing letter 'E') |
| **Branch** | BR-JED-002 — McDonald's Corniche, Jeddah |
| **Warehouse** | JEDDAH_DC — Jeddah Distribution Center |
| **Cost Center** | OPS_JEDDAH |
| **Category** | Bakery & Buns |
| **Line Count** | 3 lines, qty range 15–45 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.78 |
| **PO Format** | `clean` — PO-3W-0006 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0006 at JEDDAH_DC |
| **Exceptions** | — |
| **Special** | `vendor_alias_on_invoice: "SAUDI MODERN BAKRIES"` |

**Description:** Burger buns invoice with vendor alias variation on OCR

**What to verify:**
- Invoice vendor name is `SAUDI MODERN BAKRIES` — OCR typo that matches a registered alias
- PO reference is clean, amounts should match
- Vendor resolution agent should match via alias lookup
- Tests vendor fuzzy-match / alias resolution

---

### SCN-07 — Dairy Invoice with Missing PO Number

| Field | Value |
|-------|-------|
| **Tag** | `3W-AGENT-MISSING-PO` |
| **Invoice** | INV-3W-0007 |
| **PO** | None (no PO created — `po_format: "missing"`) |
| **Vendor** | V3W-011 — Almarai Company |
| **Branch** | BR-DMM-002 — McDonald's Dhahran Mall, Dammam |
| **Warehouse** | DAMMAM_DC — Dammam Distribution Center |
| **Cost Center** | OPS_DAMMAM |
| **Category** | Dairy & Sauces |
| **Line Count** | 4 lines, qty range 10–30 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.81 |
| **PO Format** | `missing` — empty string |
| **GRN Behaviour** | NO_GRN (no PO means no GRN linkage) |
| **GRN** | None |
| **Exceptions** | — |
| **Special** | None |

**Description:** Cold chain dairy invoice with missing PO number — agent must infer from vendor + amount

**What to verify:**
- **No PO number** on the invoice — extraction found nothing
- No PO record created → no GRN linkage possible
- PO_RETRIEVAL agent would need to search by vendor + amount to find a candidate
- THREE_WAY matching cannot proceed without PO
- Tests the complete PO absence scenario

**Expected agent trigger:** PO_NOT_FOUND

---

### SCN-08 — Packaging with Warehouse Text Noise

| Field | Value |
|-------|-------|
| **Tag** | `3W-AGENT-WAREHOUSE-NOISE` |
| **Invoice** | INV-3W-0008 |
| **PO** | PO-3W-0008 (actual), `PO3W0008` on invoice (normalized, no dashes) |
| **Vendor** | V3W-004 — Saudi Paper Manufacturing Co. |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Warehouse on Invoice** | `Riyadh Dist Center` (alias text, not code) |
| **Cost Center** | SUPPLY_CHAIN |
| **Category** | Packaging |
| **Line Count** | 3 lines, qty range 25–70 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.75 |
| **PO Format** | `normalized` — PO3W0008 (no dashes) |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0008 at RIYADH_DC |
| **Exceptions** | — |
| **Special** | `warehouse_on_invoice: "Riyadh Dist Center"` |

**Description:** Packaging invoice with warehouse text noise ('Riyadh Dist Center' instead of 'RIYADH_DC')

**What to verify:**
- PO reference uses normalized format (no dashes): `PO3W0008`
- Warehouse field shows alias text instead of canonical code
- Both PO normalization and warehouse alias resolution should work
- Tests dual noise: PO format variation + warehouse text variation
- GRN confirms full receipt once PO is resolved

---

## Bucket C: Exception-Prone Invoices (SCN-09 to SCN-12)

These invoices have data quality issues that generate exceptions: duplicates, malformed PO references, price mismatches, or missing vendor extraction. Expected outcomes are **REVIEW_REQUIRED** or **ESCALATION**.

---

### SCN-09 — Duplicate Packaging Invoice

| Field | Value |
|-------|-------|
| **Tag** | `3W-EXCEPT-DUPLICATE-PKG` |
| **Invoice** | INV-3W-0009-DUP (DB key), displays as INV-3W-0002 (duplicate of SCN-02) |
| **PO** | PO-3W-0009 |
| **Vendor** | V3W-003 — Napco National Paper Products Co. |
| **Branch** | BR-RUH-002 — McDonald's King Fahd Road, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | WAREHOUSE_OPS |
| **Category** | Packaging |
| **Line Count** | 4 lines, qty range 20–60 |
| **Invoice Status** | VALIDATED |
| **Extraction Confidence** | 0.91 |
| **PO Format** | `clean` — PO-3W-0009 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0009 at RIYADH_DC |
| **Exceptions** | DUPLICATE_INVOICE |
| **Special** | `duplicate_of_scenario: 2` |

**Description:** Duplicate packaging invoice — same vendor + invoice number as scenario 2

**What to verify:**
- `is_duplicate=True` flag set on the invoice
- Raw invoice number shows `INV-3W-0002` (same as SCN-02) but stored as `INV-3W-0009-DUP`
- Despite clean extraction and valid PO/GRN, should be flagged as DUPLICATE_INVOICE
- Tests duplicate detection regardless of other match quality
- Should NOT be approved — paying twice for the same delivery

---

### SCN-10 — Malformed PO Reference (OCR Garbled)

| Field | Value |
|-------|-------|
| **Tag** | `3W-EXCEPT-MALFORMED-PO` |
| **Invoice** | INV-3W-0010 |
| **PO** | PO-3W-0010 (actual, created on back-end) |
| **PO on Invoice** | `PO 3W 00!0` (garbled by OCR — spaces, exclamation mark) |
| **Vendor** | V3W-009 — IFFCO Saudi Arabia |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | CENTRAL_KITCHEN — Central Kitchen, Riyadh |
| **Cost Center** | SUPPLY_CHAIN |
| **Category** | Food Ingredients |
| **Line Count** | 3 lines, qty range 5–20 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.58 |
| **PO Format** | `malformed` — `PO 3W 00!0` |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0010 at CENTRAL_KITCHEN (on actual PO, if agent resolves) |
| **Exceptions** | EXTRACTION_LOW_CONFIDENCE |
| **Special** | `malformed_po_text: "PO 3W 00!0"` |

**Description:** Imported stock invoice with malformed PO — garbled by OCR

**What to verify:**
- PO field contains `PO 3W 00!0` (severely corrupted)
- Low confidence (0.58) reinforces data quality concern
- PO_RETRIEVAL agent may attempt correction but `malformed` is harder than `ocr_damaged`
- Even if PO is found, the low confidence may still route to review
- Tests severely corrupted PO reference handling

**Expected exceptions:** PO_NOT_FOUND, LOW_CONFIDENCE

---

### SCN-11 — High-Value Spare Parts with Price Mismatch

| Field | Value |
|-------|-------|
| **Tag** | `3W-EXCEPT-HIGHVAL-PARTS` |
| **Invoice** | INV-3W-0011 |
| **PO** | PO-3W-0011 |
| **Vendor** | V3W-007 — Henny Penny Arabia LLC |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | OPS_RIYADH |
| **Category** | Kitchen Equipment Parts |
| **Line Count** | 3 lines, qty range 1–3 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.89 |
| **PO Format** | `clean` — PO-3W-0011 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0011 at RIYADH_DC |
| **Exceptions** | PRICE_MISMATCH |
| **Special** | `high_value: True, price_inflate_pct: 15` |

**Description:** High-value spare parts invoice — SAR 25K+, requires finance approval

**What to verify:**
- **High value invoice** — kitchen equipment parts with individual items priced SAR 1K–4.5K
- First line item price is inflated by 15% vs PO price
- GRN confirms full receipt — goods were delivered
- Price mismatch should trigger exception
- `high_value: True` flag signals need for elevated approval threshold
- Tests price tolerance checking on high-value goods

**Expected exceptions:** PRICE_MISMATCH, HIGH_VALUE (potential escalation)

---

### SCN-12 — Missing Vendor Extraction

| Field | Value |
|-------|-------|
| **Tag** | `3W-EXCEPT-MISSING-VENDOR` |
| **Invoice** | INV-3W-0012 |
| **PO** | PO-3W-0012 |
| **Vendor** | V3W-001 — Arabian Foodstuff Co. Ltd. (actual, but not linked on invoice) |
| **Vendor on Invoice** | *(empty — OCR failed to read vendor block)* |
| **Branch** | BR-JED-001 — McDonald's Tahlia Street, Jeddah |
| **Warehouse** | JEDDAH_DC — Jeddah Distribution Center |
| **Cost Center** | OPS_JEDDAH |
| **Category** | Frozen Foods |
| **Line Count** | 3 lines, qty range 10–25 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.45 |
| **PO Format** | `clean` — PO-3W-0012 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0012 at JEDDAH_DC |
| **Exceptions** | EXTRACTION_LOW_CONFIDENCE, VENDOR_MISMATCH |
| **Special** | `missing_vendor_name: True` |

**Description:** Invoice with missing vendor extraction — OCR failed to read vendor block

**What to verify:**
- **Lowest confidence in the set** (0.45) — significant extraction failure
- Vendor name field is empty, vendor FK is not linked
- PO reference is clean so PO retrieval should work
- GRN confirms full receipt
- Vendor mismatch: invoice has no vendor but PO links to V3W-001
- Tests vendor resolution when extraction completely fails

**Expected exceptions:** VENDOR_MISMATCH, LOW_CONFIDENCE

---

## Bucket D: GRN Agent Trigger Scenarios (SCN-13 to SCN-16)

These scenarios exercise GRN-specific exceptions: no GRN found, receipt shortage, over-receipt, and multi-GRN aggregation. Expected outcomes are **GRN_EXCEPTION** or **AUTO_CLOSE**.

---

### SCN-13 — Frozen Foods with No GRN

| Field | Value |
|-------|-------|
| **Tag** | `3W-GRN-NOT-FOUND` |
| **Invoice** | INV-3W-0013 |
| **PO** | PO-3W-0013 |
| **Vendor** | V3W-001 — Arabian Foodstuff Co. Ltd. |
| **Branch** | BR-DMM-001 — McDonald's King Saud Street, Dammam |
| **Warehouse** | DAMMAM_DC — Dammam Distribution Center |
| **Cost Center** | OPS_DAMMAM |
| **Category** | Frozen Foods |
| **Line Count** | 3 lines, qty range 15–35 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.92 |
| **PO Format** | `clean` — PO-3W-0013 |
| **GRN Behaviour** | NO_GRN |
| **GRN** | None — `skip_grn: True` |
| **Exceptions** | GRN_NOT_FOUND |
| **Special** | `skip_grn: True` |

**Description:** GRN not found — frozen food invoice linked to PO but no receipt record exists

**What to verify:**
- Invoice and PO match on amounts, but **no GRN exists** for this PO
- This is the critical THREE_WAY gate: cannot approve payment without goods receipt
- GRN_ANALYSIS agent should halt invoice processing
- Tests the fundamental value of THREE_WAY matching over TWO_WAY
- Invoice should NOT be approved — goods haven't been confirmed received

---

### SCN-14 — Fries with Receipt Shortage (70%)

| Field | Value |
|-------|-------|
| **Tag** | `3W-GRN-RECEIPT-SHORTAGE` |
| **Invoice** | INV-3W-0014 |
| **PO** | PO-3W-0014 |
| **Vendor** | V3W-010 — Lamb Weston Arabia |
| **Branch** | BR-RUH-002 — McDonald's King Fahd Road, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | WAREHOUSE_OPS |
| **Category** | Frozen Potato Products |
| **Line Count** | 3 lines, qty range 20–50 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.94 |
| **PO Format** | `clean` — PO-3W-0014 |
| **GRN Behaviour** | PARTIAL_RECEIPT (70% of PO qty) |
| **GRN** | GRN-3W-0014 at RIYADH_DC |
| **Exceptions** | RECEIPT_SHORTAGE, QTY_MISMATCH |
| **Special** | `receipt_pct: 0.70` |

**Description:** Receipt shortage — only 70% of fries cartons received at Riyadh DC

**What to verify:**
- GRN shows only 70% of PO quantities received
- Invoice claims full PO amount but warehouse only received partial delivery
- Tests the core THREE_WAY value proposition: catching payment for undelivered goods
- GRN_ANALYSIS agent should flag the discrepancy

---

### SCN-15 — Cleaning Supplies with Over-Receipt (115%)

| Field | Value |
|-------|-------|
| **Tag** | `3W-GRN-OVER-RECEIPT` |
| **Invoice** | INV-3W-0015 |
| **PO** | PO-3W-0015 |
| **Vendor** | V3W-006 — Al Wazzan Trading & Supplies |
| **Branch** | BR-JED-001 — McDonald's Tahlia Street, Jeddah |
| **Warehouse** | JEDDAH_DC — Jeddah Distribution Center |
| **Cost Center** | OPS_JEDDAH |
| **Category** | Cleaning & Consumables |
| **Line Count** | 4 lines, qty range 10–30 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.93 |
| **PO Format** | `clean` — PO-3W-0015 |
| **GRN Behaviour** | OVER_RECEIPT (115% of PO qty) |
| **GRN** | GRN-3W-0015 at JEDDAH_DC |
| **Exceptions** | OVER_RECEIPT |
| **Special** | `receipt_pct: 1.15` |

**Description:** Over receipt — cleaning supplies vendor shipped 15% extra

**What to verify:**
- GRN shows 115% of PO quantity received
- Invoice matches PO amounts but warehouse received more than ordered
- Over-delivery is common in bulk orders (vendor rounds up)
- Tests over-receipt detection and tolerance handling

---

### SCN-16 — Beverage Syrups with Multi-GRN (3 Drops)

| Field | Value |
|-------|-------|
| **Tag** | `3W-GRN-MULTI-PARTIAL` |
| **Invoice** | INV-3W-0016 |
| **PO** | PO-3W-0016 |
| **Vendor** | V3W-005 — Coca-Cola Bottling Co. of Saudi Arabia |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | SUPPLY_CHAIN |
| **Category** | Beverages |
| **Line Count** | 4 lines, qty range 6–18 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.95 |
| **PO Format** | `clean` — PO-3W-0016 |
| **GRN Behaviour** | MULTI_GRN (3 partial drops) |
| **GRNs** | GRN-3W-0016-1, GRN-3W-0016-2, GRN-3W-0016-3 at RIYADH_DC |
| **Exceptions** | MULTI_GRN_PARTIAL_RECEIPT |
| **Special** | `multi_grn_drops: 3` |

**Description:** Multi-GRN partial receipt — beverage syrups delivered in 3 drops across days

**What to verify:**
- **Three separate GRNs** exist for this PO, each covering a portion of the order
- System must aggregate GRN-3W-0016-1 + GRN-3W-0016-2 + GRN-3W-0016-3 to determine total receipt
- Combined receipt should cover ~100% of PO qty (split evenly across drops)
- Tests GRN aggregation logic in THREE_WAY matching
- Expected outcome: AUTO_CLOSE (if aggregation confirms full receipt)

---

## Bucket E: Special Test Conditions (SCN-17 to SCN-20)

These scenarios test specific edge conditions: delayed receipt posting, warehouse mismatches, missing cost centers, and missing tax amounts.

---

### SCN-17 — Buns with Delayed GRN Receipt

| Field | Value |
|-------|-------|
| **Tag** | `3W-SPECIAL-DELAYED-GRN` |
| **Invoice** | INV-3W-0017 |
| **PO** | PO-3W-0017 |
| **Vendor** | V3W-012 — Saudi Modern Bakeries Co. |
| **Branch** | BR-DMM-002 — McDonald's Dhahran Mall, Dammam |
| **Warehouse** | DAMMAM_DC — Dammam Distribution Center |
| **Cost Center** | OPS_DAMMAM |
| **Category** | Bakery & Buns |
| **Line Count** | 3 lines, qty range 20–40 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.91 |
| **PO Format** | `clean` — PO-3W-0017 |
| **GRN Behaviour** | DELAYED_RECEIPT (3 days after invoice date) |
| **GRN** | GRN-3W-0017 at DAMMAM_DC |
| **Exceptions** | DELAYED_RECEIPT |
| **Special** | `grn_delay_days: 3` |

**Description:** Delayed GRN — buns receipt posted 3 days after invoice date

**What to verify:**
- GRN receipt_date is 3 days **after** the invoice date
- GRN quantities match PO correctly, but timing is anomalous
- System should flag the timing gap even though qty/amounts match
- Tests temporal validation in GRN analysis
- May warrant review to confirm goods quality was acceptable given delay

---

### SCN-18 — Frozen Foods with Warehouse Mismatch

| Field | Value |
|-------|-------|
| **Tag** | `3W-SPECIAL-WH-MISMATCH` |
| **Invoice** | INV-3W-0018 |
| **PO** | PO-3W-0018 |
| **Vendor** | V3W-002 — Al Kabeer Frozen Foods |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC (expected from scenario) |
| **Cost Center** | WAREHOUSE_OPS |
| **Category** | Frozen Foods |
| **Line Count** | 3 lines, qty range 10–30 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.88 |
| **PO Format** | `clean` — PO-3W-0018 |
| **GRN Behaviour** | LOCATION_MISMATCH |
| **GRN** | GRN-3W-0018 at **JEDDAH_DC** (expected RIYADH_DC) |
| **Exceptions** | RECEIPT_LOCATION_MISMATCH |
| **Special** | `grn_warehouse: "JEDDAH_DC"` |

**Description:** Warehouse mismatch — invoice says RIYADH_DC but GRN posted at JEDDAH_DC

**What to verify:**
- Invoice references Riyadh branch (RIYADH_DC expected)
- GRN was received at Jeddah Distribution Center instead
- Amounts and quantities may match, but delivery location is wrong
- Tests THREE_WAY location validation
- Could indicate a routing error or intentional cross-dock; needs human review

---

### SCN-19 — Uniforms with Missing Cost Center

| Field | Value |
|-------|-------|
| **Tag** | `3W-SPECIAL-NO-COSTCENTER` |
| **Invoice** | INV-3W-0019 |
| **PO** | PO-3W-0019 |
| **Vendor** | V3W-008 — Al Hokair Uniform Solutions |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | *(missing — None)* |
| **Category** | Uniforms & Housekeeping |
| **Line Count** | 4 lines, qty range 20–80 |
| **Invoice Status** | VALIDATED |
| **Extraction Confidence** | 0.87 |
| **PO Format** | `clean` — PO-3W-0019 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0019 at RIYADH_DC |
| **Exceptions** | — |
| **Special** | `missing_cost_center: True` |

**Description:** Missing cost center — uniform supply invoice without cost allocation

**What to verify:**
- Cost center field is empty/null
- PO, amounts, and GRN all match correctly
- The only issue is accounting allocation: no cost center to post against
- Tests validation of non-matching fields (incomplete classification)
- Likely routes to review for manual cost center assignment

---

### SCN-20 — Food Ingredients with Missing Tax

| Field | Value |
|-------|-------|
| **Tag** | `3W-SPECIAL-NO-TAX` |
| **Invoice** | INV-3W-0020 |
| **PO** | PO-3W-0020 |
| **Vendor** | V3W-009 — IFFCO Saudi Arabia |
| **Branch** | BR-RUH-002 — McDonald's King Fahd Road, Riyadh |
| **Warehouse** | CENTRAL_KITCHEN — Central Kitchen, Riyadh |
| **Cost Center** | SUPPLY_CHAIN |
| **Category** | Food Ingredients |
| **Line Count** | 3 lines, qty range 8–20 |
| **Invoice Status** | VALIDATED |
| **Extraction Confidence** | 0.83 |
| **PO Format** | `clean` — PO-3W-0020 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0020 at CENTRAL_KITCHEN |
| **Exceptions** | TAX_MISMATCH |
| **Special** | `missing_tax: True` |

**Description:** Missing tax amount — ingredient invoice with blank VAT field

**What to verify:**
- Tax amount is SAR 0.00 (missing tax)
- PO has standard 15% VAT but invoice shows zero tax
- GRN confirms full receipt
- Tests tax calculation mismatch detection
- May indicate vendor billing error or tax-exempt claim

---

## Bucket F: Edge Cases / Stress Tests (SCN-21 to SCN-24)

These scenarios combine multiple issues or test unusual data patterns: combined amount + tax mismatches, duplicate amount mirrors, missing currency, and invoice quantity exceeding received quantity.

---

### SCN-21 — Dairy with Amount + Tax Dual Mismatch

| Field | Value |
|-------|-------|
| **Tag** | `3W-EDGE-AMOUNT-TAX-MISMATCH` |
| **Invoice** | INV-3W-0021 |
| **PO** | PO-3W-0021 |
| **Vendor** | V3W-011 — Almarai Company |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | OPS_RIYADH |
| **Category** | Dairy & Sauces |
| **Line Count** | 4 lines, qty range 10–25 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.91 |
| **PO Format** | `clean` — PO-3W-0021 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0021 at RIYADH_DC |
| **Exceptions** | AMOUNT_MISMATCH, TAX_MISMATCH |
| **Special** | `amount_inflate: 150, tax_rate_override: 0.05` |

**Description:** Amount + tax mismatch — dairy invoice totals don't reconcile with PO line items

**What to verify:**
- Invoice subtotal is SAR 150 higher than PO subtotal (amount inflation)
- Tax calculated at 5% instead of standard 15% VAT
- GRN confirms full receipt — goods were delivered
- Tests dual mismatch detection: amount + tax in the same invoice
- Despite high confidence (0.91), the financial discrepancies should trigger review

---

### SCN-22 — Fries with Duplicate Amount Mirror

| Field | Value |
|-------|-------|
| **Tag** | `3W-EDGE-DUP-VENDOR-AMOUNT` |
| **Invoice** | INV-3W-0022 |
| **PO** | PO-3W-0022 |
| **Vendor** | V3W-010 — Lamb Weston Arabia |
| **Branch** | BR-JED-002 — McDonald's Corniche, Jeddah |
| **Warehouse** | JEDDAH_DC — Jeddah Distribution Center |
| **Cost Center** | OPS_JEDDAH |
| **Category** | Frozen Potato Products |
| **Line Count** | 3 lines, qty range 10–30 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.90 |
| **PO Format** | `clean` — PO-3W-0022 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0022 at JEDDAH_DC |
| **Exceptions** | DUPLICATE_INVOICE |
| **Special** | `mirror_amounts_of_scenario: 1` |

**Description:** Duplicate vendor + same amount — second fries invoice matches scenario 1 total exactly

**What to verify:**
- Invoice total is adjusted to match SCN-01's total exactly
- Same vendor (Lamb Weston Arabia) as SCN-01
- Different location (Jeddah vs Riyadh) and different PO
- Tests duplicate detection based on vendor + amount matching
- Should flag for review even though it's a legitimate separate order

---

### SCN-23 — Packaging with Missing Currency

| Field | Value |
|-------|-------|
| **Tag** | `3W-EDGE-NO-CURRENCY` |
| **Invoice** | INV-3W-0023 |
| **PO** | PO-3W-0023 |
| **PO on Invoice** | `PO#3W0023` (hash prefix format) |
| **Vendor** | V3W-003 — Napco National Paper Products Co. |
| **Branch** | BR-DMM-001 — McDonald's King Saud Street, Dammam |
| **Warehouse** | DAMMAM_DC — Dammam Distribution Center |
| **Cost Center** | OPS_DAMMAM |
| **Category** | Packaging |
| **Line Count** | 3 lines, qty range 15–40 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.68 |
| **PO Format** | `hash_prefix` — PO#3W0023 |
| **GRN Behaviour** | FULL_RECEIPT |
| **GRN** | GRN-3W-0023 at DAMMAM_DC |
| **Exceptions** | CURRENCY_MISMATCH |
| **Special** | `missing_currency: True` |

**Description:** Missing currency on invoice — OCR failed to detect currency field

**What to verify:**
- Currency field is empty (empty string instead of "SAR")
- PO reference uses `#` prefix format: `PO#3W0023`
- Lower confidence (0.68) from extraction issues
- GRN confirms full receipt
- Tests currency validation + PO format normalization together

---

### SCN-24 — Frozen Foods Qty Exceeds GRN Received (80%)

| Field | Value |
|-------|-------|
| **Tag** | `3W-EDGE-INV-QTY-EXCEEDS` |
| **Invoice** | INV-3W-0024 |
| **PO** | PO-3W-0024 |
| **Vendor** | V3W-001 — Arabian Foodstuff Co. Ltd. |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Warehouse** | RIYADH_DC — Riyadh Distribution Center |
| **Cost Center** | WAREHOUSE_OPS |
| **Category** | Frozen Foods |
| **Line Count** | 2 lines, qty range 40–100 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.92 |
| **PO Format** | `clean` — PO-3W-0024 |
| **GRN Behaviour** | PARTIAL_RECEIPT (80% of PO qty) |
| **GRN** | GRN-3W-0024 at RIYADH_DC |
| **Exceptions** | INVOICE_QTY_EXCEEDS_RECEIVED |
| **Special** | `receipt_pct: 0.80` |

**Description:** Invoice qty exceeds received — invoiced cartons but only 80% received (GRN)

**What to verify:**
- Invoice claims full PO quantity and matching amounts
- But GRN only confirmed receipt of 80% of PO quantity
- Invoice is attempting to bill for goods **not yet received**
- This is the classic THREE_WAY protection scenario
- Without GRN verification, TWO_WAY matching would approve this invoice
- Should be flagged and blocked until remaining goods are received or invoice is adjusted

---

## Coverage Matrices

### Scenario Coverage Matrix

| # | Tag | Vendor | Branch | GRN Behaviour | PO Format | Confidence | Expected Outcome |
|---|-----|--------|--------|---------------|-----------|------------|-----------------|
| 01 | 3W-CLEAN-FRIES-RIYADH | V3W-010 | BR-RUH-001 | FULL_RECEIPT | clean | 0.96 | MATCHED |
| 02 | 3W-CLEAN-PACKAGING-JEDDAH | V3W-003 | BR-JED-001 | FULL_RECEIPT | clean | 0.94 | MATCHED |
| 03 | 3W-CLEAN-BEVERAGE-DAMMAM | V3W-005 | BR-DMM-001 | FULL_RECEIPT | clean | 0.97 | MATCHED |
| 04 | 3W-CLEAN-CHEMICALS-BULK | V3W-006 | BR-RUH-002 | FULL_RECEIPT | clean | 0.93 | MATCHED |
| 05 | 3W-AGENT-OCR-CHICKEN | V3W-002 | BR-RUH-001 | FULL_RECEIPT | ocr_damaged | 0.72 | PARTIAL_MATCH |
| 06 | 3W-AGENT-ALIAS-BUNS | V3W-012 | BR-JED-002 | FULL_RECEIPT | clean | 0.78 | PARTIAL_MATCH |
| 07 | 3W-AGENT-MISSING-PO | V3W-011 | BR-DMM-002 | NO_GRN | missing | 0.81 | REVIEW_REQUIRED |
| 08 | 3W-AGENT-WAREHOUSE-NOISE | V3W-004 | BR-RUH-001 | FULL_RECEIPT | normalized | 0.75 | PARTIAL_MATCH |
| 09 | 3W-EXCEPT-DUPLICATE-PKG | V3W-003 | BR-RUH-002 | FULL_RECEIPT | clean | 0.91 | REVIEW_REQUIRED |
| 10 | 3W-EXCEPT-MALFORMED-PO | V3W-009 | BR-RUH-001 | FULL_RECEIPT | malformed | 0.58 | REVIEW_REQUIRED |
| 11 | 3W-EXCEPT-HIGHVAL-PARTS | V3W-007 | BR-RUH-001 | FULL_RECEIPT | clean | 0.89 | ESCALATION |
| 12 | 3W-EXCEPT-MISSING-VENDOR | V3W-001 | BR-JED-001 | FULL_RECEIPT | clean | 0.45 | REVIEW_REQUIRED |
| 13 | 3W-GRN-NOT-FOUND | V3W-001 | BR-DMM-001 | NO_GRN | clean | 0.92 | GRN_EXCEPTION |
| 14 | 3W-GRN-RECEIPT-SHORTAGE | V3W-010 | BR-RUH-002 | PARTIAL_RECEIPT | clean | 0.94 | GRN_EXCEPTION |
| 15 | 3W-GRN-OVER-RECEIPT | V3W-006 | BR-JED-001 | OVER_RECEIPT | clean | 0.93 | GRN_EXCEPTION |
| 16 | 3W-GRN-MULTI-PARTIAL | V3W-005 | BR-RUH-001 | MULTI_GRN | clean | 0.95 | AUTO_CLOSE |
| 17 | 3W-SPECIAL-DELAYED-GRN | V3W-012 | BR-DMM-002 | DELAYED_RECEIPT | clean | 0.91 | GRN_EXCEPTION |
| 18 | 3W-SPECIAL-WH-MISMATCH | V3W-002 | BR-RUH-001 | LOCATION_MISMATCH | clean | 0.88 | GRN_EXCEPTION |
| 19 | 3W-SPECIAL-NO-COSTCENTER | V3W-008 | BR-RUH-001 | FULL_RECEIPT | clean | 0.87 | REVIEW_REQUIRED |
| 20 | 3W-SPECIAL-NO-TAX | V3W-009 | BR-RUH-002 | FULL_RECEIPT | clean | 0.83 | REVIEW_REQUIRED |
| 21 | 3W-EDGE-AMOUNT-TAX-MISMATCH | V3W-011 | BR-RUH-001 | FULL_RECEIPT | clean | 0.91 | REVIEW_REQUIRED |
| 22 | 3W-EDGE-DUP-VENDOR-AMOUNT | V3W-010 | BR-JED-002 | FULL_RECEIPT | clean | 0.90 | REVIEW_REQUIRED |
| 23 | 3W-EDGE-NO-CURRENCY | V3W-003 | BR-DMM-001 | FULL_RECEIPT | hash_prefix | 0.68 | REVIEW_REQUIRED |
| 24 | 3W-EDGE-INV-QTY-EXCEEDS | V3W-001 | BR-RUH-001 | PARTIAL_RECEIPT | clean | 0.92 | GRN_EXCEPTION |

### GRN Behaviour Coverage

| GRN Behaviour | Count | Scenarios |
|---------------|-------|-----------|
| FULL_RECEIPT | 16 | SCN-01, 02, 03, 04, 05, 06, 08, 09, 10, 11, 12, 19, 20, 21, 22, 23 |
| NO_GRN | 2 | SCN-07, 13 |
| PARTIAL_RECEIPT | 2 | SCN-14, 24 |
| OVER_RECEIPT | 1 | SCN-15 |
| MULTI_GRN | 1 | SCN-16 |
| DELAYED_RECEIPT | 1 | SCN-17 |
| LOCATION_MISMATCH | 1 | SCN-18 |

### Exception Coverage

| Exception Type | Triggered By | Description |
|----------------|-------------|-------------|
| GRN_NOT_FOUND | SCN-07, 13 | No GRN exists (skip_grn or no PO) |
| RECEIPT_SHORTAGE / QTY_MISMATCH | SCN-14 | GRN qty < PO/Invoice qty (70%) |
| INVOICE_QTY_EXCEEDS_RECEIVED | SCN-24 | Invoice claims more than GRN confirms (80%) |
| OVER_RECEIPT | SCN-15 | GRN qty > PO qty (115%) |
| MULTI_GRN_PARTIAL_RECEIPT | SCN-16 | Multiple GRNs need aggregation (3 drops) |
| DELAYED_RECEIPT | SCN-17 | GRN receipt_date after invoice date (3 days) |
| RECEIPT_LOCATION_MISMATCH | SCN-18 | GRN warehouse (JEDDAH_DC) differs from expected (RIYADH_DC) |
| DUPLICATE_INVOICE | SCN-09, 22 | Invoice flagged as duplicate |
| EXTRACTION_LOW_CONFIDENCE | SCN-10, 12 | Extraction confidence < 0.60 |
| VENDOR_MISMATCH | SCN-12 | Vendor name missing from extraction |
| PRICE_MISMATCH | SCN-11 | First line item price inflated 15% |
| AMOUNT_MISMATCH | SCN-21 | Invoice subtotal inflated by SAR 150 |
| TAX_MISMATCH | SCN-20, 21 | Missing tax or incorrect rate (5% vs 15%) |
| CURRENCY_MISMATCH | SCN-23 | Currency field empty |

### PO Format Coverage

| Format | Count | Scenarios |
|--------|-------|-----------|
| `clean` | 18 | SCN-01..04, 06, 09, 11..22, 24 |
| `ocr_damaged` | 1 | SCN-05 |
| `normalized` | 1 | SCN-08 |
| `malformed` | 1 | SCN-10 |
| `hash_prefix` | 1 | SCN-23 |
| `missing` | 1 | SCN-07 |

### Data Quality Signal Coverage

| Signal | Scenarios | Notes |
|--------|-----------|-------|
| High confidence (≥ 0.90) | SCN-01..04, 09, 11, 13, 14, 15, 16, 17, 21, 22, 24 | Clean extraction |
| Medium confidence (0.70–0.89) | SCN-05, 06, 08, 18, 19, 20 | Minor issues |
| Low confidence (< 0.70) | SCN-10 (0.58), 12 (0.45), 23 (0.68) | Significant extraction problems |
| PO noise: ocr_damaged | SCN-05 | Zero-for-O — recoverable via fuzzy match |
| PO noise: normalized | SCN-08 | No dashes — recoverable via normalization |
| PO noise: malformed | SCN-10 | Spaces + special chars — hard to recover |
| PO noise: hash_prefix | SCN-23 | `PO#` prefix — recoverable |
| PO noise: missing | SCN-07 | No PO reference at all |
| Vendor alias on invoice | SCN-06 | OCR typo alias: "SAUDI MODERN BAKRIES" |
| Missing vendor name | SCN-12 | Empty vendor block |
| Missing cost center | SCN-19 | Null cost center |
| Missing tax | SCN-20 | Tax = SAR 0.00 |
| Missing currency | SCN-23 | Empty currency field |
| Amount inflate | SCN-21 | Subtotal + SAR 150 |
| Tax rate override | SCN-21 (5%) | Non-standard VAT rate |
| Price mismatch | SCN-11 | Line 1 price +15% |
| Duplicate flag | SCN-09, 22 | is_duplicate = True |
| High value | SCN-11 | Equipment parts, SAR 25K+ |
| Mirror amount | SCN-22 | Total matches SCN-01 exactly |

### Vendor Coverage

| Vendor | Scenarios | Count |
|--------|-----------|-------|
| V3W-001 (Arabian Foodstuff) | 12, 13, 24 | 3 |
| V3W-002 (Al Kabeer Frozen) | 05, 18 | 2 |
| V3W-003 (Napco National) | 02, 09, 23 | 3 |
| V3W-004 (Saudi Paper Mfg) | 08 | 1 |
| V3W-005 (Coca-Cola Bottling) | 03, 16 | 2 |
| V3W-006 (Al Wazzan Trading) | 04, 15 | 2 |
| V3W-007 (Henny Penny Arabia) | 11 | 1 |
| V3W-008 (Al Hokair Uniform) | 19 | 1 |
| V3W-009 (IFFCO Saudi Arabia) | 10, 20 | 2 |
| V3W-010 (Lamb Weston Arabia) | 01, 14, 22 | 3 |
| V3W-011 (Almarai Company) | 07, 21 | 2 |
| V3W-012 (Saudi Modern Bakeries) | 06, 17 | 2 |

### Branch / City Coverage

| City | Branches Used | Scenarios |
|------|--------------|-----------|
| Riyadh | BR-RUH-001, BR-RUH-002 | 01, 04, 05, 08, 10, 11, 14, 16, 18, 19, 20, 21, 24 |
| Jeddah | BR-JED-001, BR-JED-002 | 02, 06, 12, 15, 22 |
| Dammam | BR-DMM-001, BR-DMM-002 | 03, 07, 13, 17, 23 |

### Category Coverage

| Category | Scenarios |
|----------|-----------|
| Frozen Potato Products | 01, 14, 22 |
| Packaging | 02, 08, 09, 23 |
| Beverages | 03, 16 |
| Cleaning & Consumables | 04, 15 |
| Frozen Foods | 05, 12, 13, 18, 24 |
| Bakery & Buns | 06, 17 |
| Dairy & Sauces | 07, 21 |
| Food Ingredients | 10, 20 |
| Kitchen Equipment Parts | 11 |
| Uniforms & Housekeeping | 19 |

---

## QA & Large Mode: Bulk Random Scenarios

In `qa` mode (+30) and `large` mode (+100), additional random invoices are generated starting at SCN-25. These use a seeded random number generator (default seed: 42 + 1000 = 1042) for deterministic output.

### Random Distribution

| Attribute | Distribution |
|-----------|-------------|
| **Vendor** | Uniform across all 12 vendors |
| **Branch** | Uniform across all 6 branches |
| **Category** | Uniform across all 10 goods categories |
| **Warehouse** | Uniform across all 4 warehouses |
| **Confidence** | Uniform 0.45 – 0.98 |
| **PO format** | clean (×3), normalized, hash_prefix, ocr_damaged, missing — ~43% clean |
| **Invoice status** | READY_FOR_RECON (×3), VALIDATED, EXTRACTED — ~60% READY_FOR_RECON |
| **Line count** | 2–5 lines per invoice |
| **Qty range** | 5–60 per line item |
| **Exception pools** | None (×2), RECEIPT_SHORTAGE+QTY_MISMATCH, OVER_RECEIPT, GRN_NOT_FOUND, DELAYED_RECEIPT, PRICE_MISMATCH, TAX_MISMATCH, AMOUNT_MISMATCH, DUPLICATE_INVOICE, RECEIPT_LOCATION_MISMATCH |
| **Expected outcomes** | MATCHED (×2), PARTIAL_MATCH, REVIEW_REQUIRED, GRN_EXCEPTION, AUTO_CLOSE |

Bulk scenarios are tagged as `3W-BULK-NNNN` (e.g., `3W-BULK-0025`).

---

## UI Verification Checklist

After seeding, verify the following in the web application:

### Invoice List Page
- [ ] All 24 (demo) / 54 (qa) / 124 (large) THREE_WAY invoices appear with `INV-3W-*` numbers
- [ ] Status column shows correct values (READY_FOR_RECON, VALIDATED, EXTRACTED)
- [ ] Vendor names display correctly (even for SCN-06 with OCR alias and SCN-12 with missing vendor)
- [ ] Total amounts are formatted with SAR currency
- [ ] Confidence values display as percentages (e.g., 96%, not 0.96)
- [ ] Duplicate flags (SCN-09, SCN-22) are visually indicated

### Invoice Detail Page
- [ ] PO reference links to the correct PO (where applicable)
- [ ] PO noise scenarios (SCN-05 ocr_damaged, SCN-08 normalized, SCN-10 malformed, SCN-23 hash_prefix) show the noisy PO reference, not the actual PO
- [ ] Missing PO scenario (SCN-07) shows blank/empty PO field
- [ ] Line items display with correct quantities, prices, and amounts
- [ ] Extraction confidence bar/badge renders correctly
- [ ] Extraction raw JSON is accessible for each invoice
- [ ] GRN information visible (warehouse, receipt date, quantities — where GRN exists)
- [ ] Missing cost center (SCN-19) and currency (SCN-23) fields display appropriately
- [ ] Missing vendor (SCN-12) shows empty vendor field

### Reconciliation Trigger
- [ ] Invoices in READY_FOR_RECON status appear in the reconciliation candidates list
- [ ] Invoices in EXTRACTED/VALIDATED status (SCN-09, 10, 12, 19, 20, 23) are NOT in READY_FOR_RECON candidates
- [ ] Selecting and starting reconciliation creates ReconciliationRun + Results
- [ ] THREE_WAY matching mode is correctly resolved for goods/stock invoices
- [ ] GRN verification is performed as part of THREE_WAY matching
- [ ] Agent pipeline triggers for non-MATCHED results
- [ ] GRN exceptions (shortage, no-GRN, over-receipt, etc.) appear in results

### GRN Verification (THREE_WAY-specific)
- [ ] SCN-01..04: Full receipt GRNs pass verification cleanly
- [ ] SCN-05, 06, 08: Full receipt but agent needed for PO/vendor/warehouse resolution
- [ ] SCN-13: No GRN found, blocking invoice approval
- [ ] SCN-14: Receipt shortage flagged (70% received)
- [ ] SCN-15: Over-receipt quantity flagged (115% received)
- [ ] SCN-16: Multi-GRN aggregation attempted (3 drops)
- [ ] SCN-17: Delayed receipt timing flagged (3 days late)
- [ ] SCN-18: Location mismatch between invoice warehouse and GRN warehouse detected
- [ ] SCN-24: Invoice qty exceeds GRN received qty (80% received)
