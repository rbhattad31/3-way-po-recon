# TWO_WAY PO Invoice – Test Scenarios

> **Seed command:** `python manage.py seed_two_way_invoices`
> **Path:** `apps/cases/management/commands/seed_two_way_invoices.py`
> **Domain:** McDonald's Saudi Arabia – AP Automation (Service Invoices)
> **Processing path:** TWO_WAY (Invoice vs PO only — no GRN dependency)

---

## Overview

This document describes the 15 deterministic test scenarios seeded by the `seed_two_way_invoices` management command. Each scenario creates a **service invoice** with a backing Purchase Order (where applicable) designed to exercise a specific TWO_WAY reconciliation outcome when matching is triggered later from the invoice detail page.

**What is seeded:** Vendors, vendor aliases, POs, PO line items, invoices, invoice line items, document uploads, extraction results (`ExtractionResult`), extraction JSON payloads, and **audit events** (`INVOICE_UPLOADED` + `EXTRACTION_COMPLETED` per invoice — 30 total).

**What is NOT seeded:** AP Cases, reconciliation runs/results/exceptions, agent runs, review assignments. These are created later when reconciliation is triggered.

### Audit & RBAC Attribution

All seeded records are created under the **AP_PROCESSOR** role:

| Field | Value |
|-------|-------|
| **Seed user** | `ap.processor@mcd-ksa.com` (Fatima Al-Rashid) |
| **Legacy role** | `AP_PROCESSOR` |
| **RBAC role** | `AP_PROCESSOR` (primary, active, non-expiring) |
| **Audit fields** | `created_by`, `updated_by`, `created_at`, `updated_at` |

The command ensures the RBAC `Role` record exists and creates a `UserRole` assignment linking the user to the AP_PROCESSOR role. Every `BaseModel` record (Vendor, VendorAlias, PurchaseOrder, DocumentUpload, Invoice, ExtractionResult) carries both `created_by` and `updated_by` pointing to this actor.

### Traceability

Each invoice has a corresponding `ExtractionResult` record that stores:

| Field | Value |
|-------|-------|
| `engine_name` | `azure_document_intelligence` |
| `engine_version` | `2024-02-29-preview` |
| `raw_response` | Full extraction JSON payload (same as `Invoice.extraction_raw_json`) |
| `confidence` | Matches `Invoice.extraction_confidence` |
| `duration_ms` | Simulated 1200–4500ms |
| `success` | `True` |

Invoice records also include traceability-enabling attributes:
- `extraction_raw_json` — realistic OCR output with vendor block, totals, tax, PO detection, service period, line items, and per-field confidence scores
- `extraction_confidence` — 0.0–1.0 overall confidence
- `raw_*` fields — raw extracted values before normalization
- `document_upload` — linked `DocumentUpload` with filename, size, content type, and processing state

### Seed Modes

| Mode | Deterministic | Random | Total |
|------|--------------|--------|-------|
| `demo` (default) | 15 | 0 | 15 |
| `qa` | 15 | 10 | 25 |
| `large` | 15 | 30 | 45 |

### Command Examples

```bash
python manage.py seed_two_way_invoices                     # demo mode (15 invoices)
python manage.py seed_two_way_invoices --mode=qa           # +10 random = 25
python manage.py seed_two_way_invoices --mode=large        # +30 random = 45
python manage.py seed_two_way_invoices --reset             # delete & recreate
python manage.py seed_two_way_invoices --summary           # print invoice table
python manage.py seed_two_way_invoices --seed=99           # custom random seed
python manage.py seed_two_way_invoices --reset --mode=qa --summary
```

### Reset Behavior (`--reset`)

The `--reset` flag performs a **full cascading delete** of all seeded TWO_WAY data, including downstream records created during reconciliation:

1. **AP Cases** — `APCase`, `APCaseStage`, `APCaseArtifact`
2. **Reconciliation** — `ReconciliationResult`, `ReconciliationResultLine`, `ReconciliationException`
3. **Agent pipeline** — `AgentRun`, `AgentStep`, `AgentMessage`, `DecisionLog`, `AgentEscalation`, `AgentRecommendation`, `ToolCall`
4. **Reviews** — `ReviewAssignment`, `ReviewComment`, `ManualReviewAction`, `ReviewDecision`
5. **Audit events** — `AuditEvent` records linked to seeded invoices
6. **Extraction** — `ExtractionResult` records
7. **Documents** — `Invoice`, `InvoiceLineItem`, `DocumentUpload`
8. **Purchase Orders** — `PurchaseOrder`, `PurchaseOrderLineItem`
9. **Vendors** — `Vendor`, `VendorAlias`

This ensures a clean re-seed even after reconciliation has been triggered.

### Example Console Output (demo mode)

```
================================================================
  McDonald's KSA – TWO_WAY Invoice Seed Data
  Mode: DEMO | Reset: True | Seed: 42
================================================================

  Resetting seeded TWO_WAY data...
  Reset complete.
  [0/5] Setting up AP_PROCESSOR seed user...
        Audit actor: Fatima Al-Rashid (ap.processor@mcd-ksa.com)
        Legacy role: AP_PROCESSOR
        RBAC roles:  AP_PROCESSOR
  [1/5] Creating TWO_WAY service vendors...
        10 vendors, 26 aliases
  [2/5] Creating POs & Invoices (15 scenarios)...
        15 invoices, 14 POs created
  [3/5] Skipping bulk generation (demo mode)
  [4/5] Creating audit trail events...
        30 audit events created (2 per invoice)
  [5/5] Seed statistics:

  ──────────────────────────────────────────────────
  SEED SUMMARY
  ──────────────────────────────────────────────────
  Vendors created/reused:       10
  Vendor aliases:               26
  Purchase Orders:              14
  Invoices created:             15
  Extraction results:           15
  ├─ Duplicate-prone:           1
  ├─ Malformed PO refs:         2
  ├─ High-value (>50k SAR):     4
  └─ Incomplete fields:         3  Audit events:                 30  ──────────────────────────────────────────────────
  Audit user:                   ap.processor@mcd-ksa.com
  Audit role:                   AP_PROCESSOR

  Seeding completed in 67.6s
```

### Expected TWO_WAY Processing Pipeline

Once reconciliation is triggered, each invoice passes through these stages:

```
INTAKE → EXTRACTION → PATH_RESOLUTION → PO_RETRIEVAL → TWO_WAY_MATCHING → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
```

---

## Master Data

### Vendors (10)

| Code | Name | Category | Aliases |
|------|------|----------|---------|
| V2W-001 | Zamil Air Conditioners | HVAC Maintenance | زامل للمكيفات, Zamil AC, Zamil HVAC Services |
| V2W-002 | Rentokil Initial Saudi Arabia | Pest Control | رينتوكيل السعودية, Rentokil KSA, Rentokil Initial |
| V2W-003 | Saudi Services Co. Ltd. (SSCO) | Facility Maintenance | الشركة السعودية للخدمات, SSCO, Saudi Services |
| V2W-004 | G4S Saudi Arabia | Security Services | جي4اس السعودية, G4S KSA |
| V2W-005 | Al Tamimi Cleaning Services | Cleaning Services | التميمي لخدمات النظافة, Al-Tamimi Cleaning, Tamimi Janitorial |
| V2W-006 | Henny Penny Arabia LLC | Kitchen Equipment Service | هيني بيني العربية, Henny Penny KSA, HP Arabia |
| V2W-007 | Almajdouie Logistics | Cold Chain Logistics | المجدوعي للخدمات اللوجستية, Almajdouie |
| V2W-008 | National Fire & Safety Co. | Fire Safety Services | الوطنية للحريق والسلامة, NFSC, National Fire Safety |
| V2W-009 | Pinnacle Consulting Arabia | Consulting Services | بيناكل للاستشارات العربية, Pinnacle Arabia |
| V2W-010 | Jeddah Office Admin Services | Office Admin Services | خدمات جدة الإدارية, Jeddah Admin Svc |

### Branches (7)

| Code | Name | City |
|------|------|------|
| BR-RUH-001 | McDonald's Olaya Street | Riyadh |
| BR-RUH-002 | McDonald's King Fahd Road | Riyadh |
| BR-RUH-003 | McDonald's Exit 15 DT | Riyadh |
| BR-JED-001 | McDonald's Tahlia Street | Jeddah |
| BR-JED-002 | McDonald's Corniche | Jeddah |
| BR-DMM-001 | McDonald's King Saud Street | Dammam |
| BR-DMM-002 | McDonald's Dhahran Mall | Dammam |

### Cost Centers

| Code | Department |
|------|-----------|
| CC-3010 | Facilities & Maintenance |
| CC-2010 | Store Operations |
| CC-5010 | IT |
| CC-6010 | Finance |

---

## Scenario Buckets

The 15 scenarios are organized into three buckets based on expected reconciliation outcome:

| Bucket | Scenarios | Expected Result |
|--------|-----------|----------------|
| **A. Likely Matched** | SCN-01 through SCN-05 | MATCHED — clean PO match, no exceptions |
| **B. Partial Match / Review** | SCN-06 through SCN-10 | PARTIAL_MATCH or REQUIRES_REVIEW — salvageable with agent assistance |
| **C. Exception / Fail** | SCN-11 through SCN-15 | UNMATCHED or ERROR — missing/corrupted data, duplicates, threshold breaches |

---

## Bucket A: Likely Matched (SCN-01 to SCN-05)

These invoices have clean data, valid PO references, matching amounts, correct VAT, and high extraction confidence. When reconciliation runs, they should produce a **MATCHED** result with no exceptions.

---

### SCN-01 — HVAC Annual Maintenance (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `2W-HVAC-PERFECT` |
| **Invoice** | INV-2W-0001 |
| **PO** | PO-2W-0001 |
| **Vendor** | V2W-001 — Zamil Air Conditioners |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Cost Center** | CC-3010 |
| **Category** | HVAC Maintenance |
| **Total** | ~SAR 35,995.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.96 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** HVAC annual maintenance – Riyadh branch

**What to verify:**
- Invoice amounts match PO amounts exactly
- Vendor resolves correctly
- PO retrieval finds PO-2W-0001 on first attempt
- TWO_WAY matching produces MATCHED
- No exceptions generated
- Case auto-closes or routes to summary

---

### SCN-02 — Cleaning Services (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `2W-CLEANING-PERFECT` |
| **Invoice** | INV-2W-0002 |
| **PO** | PO-2W-0002 |
| **Vendor** | V2W-005 — Al Tamimi Cleaning Services |
| **Branch** | BR-JED-001 — McDonald's Tahlia Street, Jeddah |
| **Cost Center** | CC-3010 |
| **Category** | Cleaning Services |
| **Total** | ~SAR 30,015.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.94 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Cleaning services – Jeddah restaurant cluster

**What to verify:**
- Standard cleaning service invoice with clean extraction
- PO match is immediate
- Amounts align
- Auto-close eligible

---

### SCN-03 — Pest Control Monthly (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `2W-PESTCONTROL-PERFECT` |
| **Invoice** | INV-2W-0003 |
| **PO** | PO-2W-0003 |
| **Vendor** | V2W-002 — Rentokil Initial Saudi Arabia |
| **Branch** | BR-DMM-001 — McDonald's King Saud Street, Dammam |
| **Cost Center** | CC-3010 |
| **Category** | Pest Control |
| **Total** | ~SAR 7,072.50 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.93 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Pest control monthly service – Dammam

**What to verify:**
- Low-value service invoice
- Clean PO reference
- Straightforward match

---

### SCN-04 — Security Services (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `2W-SECURITY-PERFECT` |
| **Invoice** | INV-2W-0004 |
| **PO** | PO-2W-0004 |
| **Vendor** | V2W-004 — G4S Saudi Arabia |
| **Branch** | BR-RUH-002 — McDonald's King Fahd Road, Riyadh |
| **Cost Center** | CC-2010 |
| **Category** | Security Services |
| **Total** | ~SAR 73,140.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.97 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Security services invoice – Riyadh region

**What to verify:**
- Higher-value service invoice (>50k SAR)
- Highest extraction confidence in the set
- Clean match despite higher value
- Verify approval threshold logic doesn't flag this as needing extra approval just due to value

---

### SCN-05 — Facility AMC Retainer (Perfect Match)

| Field | Value |
|-------|-------|
| **Tag** | `2W-FACILITY-AMC-PERFECT` |
| **Invoice** | INV-2W-0005 |
| **PO** | PO-2W-0005 |
| **Vendor** | V2W-003 — Saudi Services Co. Ltd. (SSCO) |
| **Branch** | BR-JED-002 — McDonald's Corniche, Jeddah |
| **Cost Center** | CC-3010 |
| **Category** | Facility Maintenance |
| **Total** | ~SAR 35,880.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.95 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None (standard 15% VAT) |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Facility maintenance retainer – Jeddah Corniche

**What to verify:**
- Monthly retainer-style service invoice
- This invoice is later referenced by SCN-12 (duplicate test)
- Clean match

---

## Bucket B: Partial Match / Review-Likely (SCN-06 to SCN-10)

These invoices have data quality issues, amount discrepancies, or tax mismatches that should produce **PARTIAL_MATCH** or **REQUIRES_REVIEW** results. Agents should be able to assist with resolution.

---

### SCN-06 — Branch Repair with OCR PO Noise

| Field | Value |
|-------|-------|
| **Tag** | `2W-REPAIR-OCR-NOISE` |
| **Invoice** | INV-2W-0006 |
| **PO** | PO-2W-0006 (actual) |
| **PO on Invoice** | PO-2W-00**60** (last 2 digits swapped by OCR) |
| **Vendor** | V2W-003 — Saudi Services Co. Ltd. (SSCO) |
| **Branch** | BR-RUH-003 — McDonald's Exit 15 DT, Riyadh |
| **Cost Center** | CC-3010 |
| **Category** | Facility Maintenance |
| **Total** | ~SAR 45,655.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.72 |
| **PO Noise** | `swap_digit` — last two digits transposed |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Branch repair service invoice – OCR PO noise

**What to verify:**
- **PO retrieval challenge:** Invoice shows `PO-2W-0060` but actual PO is `PO-2W-0006`
- Normalized PO `2W0060` does not match any PO → `PO_NOT_FOUND` exception raised
- **PO lookup behavior:** Because the invoice has an extracted PO reference, the vendor+amount discovery fallback is **skipped** — the system deliberately returns "not found" so the PO_RETRIEVAL agent handles fuzzy matching
- PolicyEngine detects `PO_NOT_FOUND` exception → queues **PO_RETRIEVAL** agent
- Agent pipeline: `PO_RETRIEVAL → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY` (4 agents)
- Once PO_RETRIEVAL agent resolves the correct PO, amounts should match
- Lower confidence (0.72) also triggers `EXTRACTION_LOW_CONFIDENCE` exception
- Tests OCR-noise recovery via agent pipeline (not silent auto-correction)

**Validated outcome:** Case created with 4 agent runs, final status `READY_FOR_REVIEW`, recommendation `SEND_TO_AP_REVIEW`.

**Expected exceptions:** PO_NOT_FOUND, EXTRACTION_LOW_CONFIDENCE

---

### SCN-07 — HVAC Maintenance with Surcharge

| Field | Value |
|-------|-------|
| **Tag** | `2W-HVAC-SURCHARGE` |
| **Invoice** | INV-2W-0007 |
| **PO** | PO-2W-0007 |
| **Vendor** | V2W-001 — Zamil Air Conditioners |
| **Branch** | BR-DMM-002 — McDonald's Dhahran Mall, Dammam |
| **Cost Center** | CC-3010 |
| **Category** | HVAC Maintenance |
| **Total** | ~SAR 21,016.25 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.89 |
| **PO Noise** | None |
| **Amount Delta** | **+SAR 475.00** (surcharge not on PO) |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Annual maintenance invoice with surcharge – Dhahran

**What to verify:**
- PO retrieval succeeds normally
- **AMOUNT_MISMATCH exception** — invoice subtotal exceeds PO by SAR 475
- Check if delta falls within or outside auto-close band (strict: 2%, auto-close: 5%)
- If within auto-close → auto-close; if outside → PARTIAL_MATCH + review routing
- Exception analysis agent should identify the surcharge pattern

**Expected exceptions:** AMOUNT_MISMATCH

---

### SCN-08 — Consulting Milestone (Under-billed)

| Field | Value |
|-------|-------|
| **Tag** | `2W-CONSULTING-MILESTONE` |
| **Invoice** | INV-2W-0008 |
| **PO** | PO-2W-0008 |
| **Vendor** | V2W-009 — Pinnacle Consulting Arabia |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Cost Center** | CC-6010 |
| **Category** | Consulting Services |
| **Total** | ~SAR 414,862.50 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.91 |
| **PO Noise** | None |
| **Amount Delta** | **-SAR 1,250.00** (invoice less than PO) |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Operations consulting milestone invoice – Riyadh

**What to verify:**
- High-value consulting engagement (>SAR 400k)
- **Invoice is UNDER PO value** — this is generally acceptable
- Should still trigger AMOUNT_MISMATCH but possibly within tolerance
- Test that under-billing is treated differently than over-billing
- Finance department cost center (CC-6010)

**Expected exceptions:** AMOUNT_MISMATCH (under-billed — may auto-close)

---

### SCN-09 — Emergency Call-Out (Amount + Tax + Missing Cost Center)

| Field | Value |
|-------|-------|
| **Tag** | `2W-EMERGENCY-CALLOUT` |
| **Invoice** | INV-2W-0009 |
| **PO** | PO-2W-0009 |
| **Vendor** | V2W-006 — Henny Penny Arabia LLC |
| **Branch** | BR-JED-001 — McDonald's Tahlia Street, Jeddah |
| **Cost Center** | *(blank — intentionally missing)* |
| **Category** | Kitchen Equipment Service |
| **Total** | ~SAR 15,435.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.78 |
| **PO Noise** | None |
| **Amount Delta** | **+SAR 600.00** (emergency premium) |
| **Tax Override** | **5% instead of 15%** |
| **Missing Fields** | `cost_center` |
| **Duplicate** | No |

**Description:** Emergency kitchen equipment service – Jeddah Tahlia

**What to verify:**
- **Triple issue:** amount over PO + wrong VAT rate + missing cost center
- AMOUNT_MISMATCH from the +SAR 600 emergency premium
- TAX_MISMATCH from 5% vs expected 15% VAT
- Missing cost center should be flagged during validation
- This combination should guarantee REQUIRES_REVIEW routing
- Tests multi-exception handling in a single invoice

**Expected exceptions:** AMOUNT_MISMATCH, TAX_MISMATCH

---

### SCN-10 — Fire Safety Inspection (Tax Mismatch Only)

| Field | Value |
|-------|-------|
| **Tag** | `2W-FIRESAFETY-TAX-MISMATCH` |
| **Invoice** | INV-2W-0010 |
| **PO** | PO-2W-0010 |
| **Vendor** | V2W-008 — National Fire & Safety Co. |
| **Branch** | BR-RUH-002 — McDonald's King Fahd Road, Riyadh |
| **Cost Center** | CC-3010 |
| **Category** | Fire Safety Services |
| **Total** | ~SAR 19,140.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.88 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | **10% instead of 15%** |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Fire safety inspection service – Riyadh King Fahd

**What to verify:**
- Subtotals match between invoice and PO
- **TAX_MISMATCH only** — invoice shows 10% VAT, PO has standard 15%
- Total amounts will differ due to VAT difference
- Isolated tax exception — good for testing TAX_MISMATCH handling without amount noise
- Exception analysis agent should identify the VAT rate discrepancy

**Expected exceptions:** TAX_MISMATCH

---

## Bucket C: Exception / Fail-Likely (SCN-11 to SCN-15)

These invoices have severe data quality issues — corrupted PO references, duplicates, missing fields, or threshold-breaching amounts. They should produce **UNMATCHED**, **ERROR**, or multi-exception results requiring manual intervention.

---

### SCN-11 — Consulting Invoice with Malformed PO

| Field | Value |
|-------|-------|
| **Tag** | `2W-CONSULTING-BAD-PO` |
| **Invoice** | INV-2W-0011 |
| **PO** | PO-2W-0011 (actual, but invoice can't find it) |
| **PO on Invoice** | **P0-2W-0011-X** (letter O→zero, garbage suffix) |
| **Vendor** | V2W-009 — Pinnacle Consulting Arabia |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Cost Center** | CC-6010 |
| **Category** | Consulting Services |
| **Total** | ~SAR 476,100.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.65 |
| **PO Noise** | `malformed` — prefix corrupted (`PO` → `P0`) + garbage suffix `-X` |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** Consulting invoice with malformed PO reference

**What to verify:**
- **PO_NOT_FOUND** — the malformed reference `P0-2W-0011-X` won't match any PO
- PO_RETRIEVAL agent should attempt fuzzy match but the corruption is severe
- Very high value invoice (>SAR 476k)
- Low confidence (0.65) compounds the problem
- Should route to REQUIRES_REVIEW or ESCALATED
- Tests the system's handling of truly unrecoverable PO references

**Expected exceptions:** PO_NOT_FOUND

---

### SCN-12 — Duplicate Facility Maintenance Invoice

| Field | Value |
|-------|-------|
| **Tag** | `2W-FACILITY-DUPLICATE` |
| **Invoice** | INV-2W-0012 |
| **PO** | PO-2W-0012 |
| **Vendor** | V2W-003 — Saudi Services Co. Ltd. (SSCO) |
| **Branch** | BR-JED-002 — McDonald's Corniche, Jeddah |
| **Cost Center** | CC-3010 |
| **Category** | Facility Maintenance |
| **Total** | ~SAR 63,135.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.92 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | **Yes** — `duplicate_of` references INV-2W-0005 (SCN-05) |

**Description:** Duplicate facility services invoice – same vendor/amount

**What to verify:**
- `is_duplicate = True` flag is set, linked to INV-2W-0005
- Same vendor (SSCO) as SCN-05
- **DUPLICATE_INVOICE exception** should be raised
- Even though extraction is clean and PO matches, the duplicate flag should prevent auto-close
- Tests duplicate detection and review routing
- Verify the invoice detail page shows the duplicate linkage

**Expected exceptions:** DUPLICATE_INVOICE

---

### SCN-13 — Logistics Invoice with Corrupted PO Field

| Field | Value |
|-------|-------|
| **Tag** | `2W-LOGISTICS-CORRUPTED` |
| **Invoice** | INV-2W-0013 |
| **PO** | *(no PO created — PO noise = "missing")* |
| **PO on Invoice** | *(empty)* |
| **Vendor** | V2W-007 — Almajdouie Logistics |
| **Branch** | BR-DMM-001 — McDonald's King Saud Street, Dammam |
| **Cost Center** | CC-2010 |
| **Category** | Cold Chain Logistics |
| **Total** | ~SAR 58,190.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.55 |
| **PO Noise** | `missing` — PO field completely empty |
| **Amount Delta** | 0 |
| **Tax Override** | None |
| **Missing Fields** | `po_number`, `currency` |
| **Duplicate** | No |

**Description:** Imported invoice with corrupted PO field – logistics

**What to verify:**
- **No PO reference at all** — `po_number` is blank
- **No currency** — `currency` field is blank
- Very low extraction confidence (0.55)
- No backing PO was created (unlike SCN-11 which has a PO but can't find it)
- Tests path resolution — should this stay TWO_WAY or reroute to NON_PO?
- PO_RETRIEVAL agent has nothing to search with
- HIGH-VALUE flag (>50k SAR)
- Multiple missing fields compound the data quality issue

**Expected exceptions:** PO_NOT_FOUND, CURRENCY_MISMATCH, EXTRACTION_LOW_CONFIDENCE

---

### SCN-14 — High-Value Consulting (Over PO Threshold)

| Field | Value |
|-------|-------|
| **Tag** | `2W-HIGHVALUE-CONSULTING` |
| **Invoice** | INV-2W-0014 |
| **PO** | PO-2W-0014 |
| **Vendor** | V2W-009 — Pinnacle Consulting Arabia |
| **Branch** | BR-RUH-001 — McDonald's Olaya Street, Riyadh |
| **Cost Center** | CC-6010 |
| **Category** | Consulting Services |
| **Total** | ~SAR 217,580.00 |
| **Invoice Status** | READY_FOR_RECON |
| **Extraction Confidence** | 0.90 |
| **PO Noise** | None |
| **Amount Delta** | **+SAR 5,200.00** (over PO value) |
| **Tax Override** | None |
| **Missing Fields** | None |
| **Duplicate** | No |

**Description:** High-value consulting engagement invoice – Riyadh HQ

**What to verify:**
- PO retrieval succeeds (clean PO reference)
- **AMOUNT_MISMATCH** — invoice exceeds PO by SAR 5,200
- Very high value invoice (>SAR 200k)
- Check if +SAR 5,200 on a ~SAR 212k PO falls within auto-close band (~2.5%)
- Strict tolerance: 2% → ~SAR 4,240 → OUTSIDE strict
- Auto-close band: 5% → ~SAR 10,600 → INSIDE auto-close
- **May auto-close** if policy engine allows
- Tests high-value + borderline tolerance interaction

**Expected exceptions:** AMOUNT_MISMATCH

---

### SCN-15 — Office Admin with Missing Tax and Cost Center

| Field | Value |
|-------|-------|
| **Tag** | `2W-ADMIN-MISSING-TAX` |
| **Invoice** | INV-2W-0015 |
| **PO** | PO-2W-0015 |
| **Vendor** | V2W-010 — Jeddah Office Admin Services |
| **Branch** | BR-JED-001 — McDonald's Tahlia Street, Jeddah |
| **Cost Center** | *(blank — intentionally missing)* |
| **Category** | Office Admin Services |
| **Total** | ~SAR 28,400.00 |
| **Invoice Status** | EXTRACTED |
| **Extraction Confidence** | 0.70 |
| **PO Noise** | None |
| **Amount Delta** | 0 |
| **Tax Override** | **"missing"** — tax_amount field is NULL |
| **Missing Fields** | `tax_amount`, `cost_center` |
| **Duplicate** | No |

**Description:** Office admin invoice – missing tax and cost center

**What to verify:**
- PO retrieval succeeds (clean PO reference)
- **tax_amount is NULL** — cannot compute total correctly without tax
- **cost_center is blank** — GL coding will be blocked
- Invoice total = subtotal only (no tax added)
- PO has 15% VAT → total mismatch guaranteed when compared
- Tests how system handles invoices with NULL/missing mandatory financial fields
- Should route to review for manual tax and cost center entry

**Expected exceptions:** TAX_MISMATCH, AMOUNT_MISMATCH

---

## Scenario Coverage Matrix

| # | Tag | Vendor | City | Status | Conf | PO Found | Amt Match | Tax Match | Dup | Missing Fields | Expected Result |
|---|-----|--------|------|--------|------|----------|-----------|-----------|-----|----------------|-----------------|
| 01 | 2W-HVAC-PERFECT | Zamil AC | Riyadh | READY_FOR_RECON | 0.96 | Yes | Yes | Yes | No | None | MATCHED |
| 02 | 2W-CLEANING-PERFECT | Al Tamimi | Jeddah | READY_FOR_RECON | 0.94 | Yes | Yes | Yes | No | None | MATCHED |
| 03 | 2W-PESTCONTROL-PERFECT | Rentokil | Dammam | READY_FOR_RECON | 0.93 | Yes | Yes | Yes | No | None | MATCHED |
| 04 | 2W-SECURITY-PERFECT | G4S | Riyadh | READY_FOR_RECON | 0.97 | Yes | Yes | Yes | No | None | MATCHED |
| 05 | 2W-FACILITY-AMC-PERFECT | SSCO | Jeddah | READY_FOR_RECON | 0.95 | Yes | Yes | Yes | No | None | MATCHED |
| 06 | 2W-REPAIR-OCR-NOISE | SSCO | Riyadh | EXTRACTED | 0.72 | Noisy | Yes | Yes | No | None | PARTIAL_MATCH |
| 07 | 2W-HVAC-SURCHARGE | Zamil AC | Dammam | READY_FOR_RECON | 0.89 | Yes | +475 | Yes | No | None | PARTIAL_MATCH |
| 08 | 2W-CONSULTING-MILESTONE | Pinnacle | Riyadh | READY_FOR_RECON | 0.91 | Yes | -1250 | Yes | No | None | PARTIAL_MATCH |
| 09 | 2W-EMERGENCY-CALLOUT | Henny Penny | Jeddah | EXTRACTED | 0.78 | Yes | +600 | 5%≠15% | No | cost_center | REQUIRES_REVIEW |
| 10 | 2W-FIRESAFETY-TAX | NFSC | Riyadh | READY_FOR_RECON | 0.88 | Yes | Yes | 10%≠15% | No | None | PARTIAL_MATCH |
| 11 | 2W-CONSULTING-BAD-PO | Pinnacle | Riyadh | EXTRACTED | 0.65 | Malformed | N/A | N/A | No | None | UNMATCHED |
| 12 | 2W-FACILITY-DUPLICATE | SSCO | Jeddah | READY_FOR_RECON | 0.92 | Yes | Yes | Yes | **Yes** | None | REQUIRES_REVIEW |
| 13 | 2W-LOGISTICS-CORRUPTED | Almajdouie | Dammam | EXTRACTED | 0.55 | Missing | N/A | N/A | No | po_number, currency | UNMATCHED |
| 14 | 2W-HIGHVALUE-CONSULTING | Pinnacle | Riyadh | READY_FOR_RECON | 0.90 | Yes | +5200 | Yes | No | None | PARTIAL_MATCH |
| 15 | 2W-ADMIN-MISSING-TAX | Jeddah Admin | Jeddah | EXTRACTED | 0.70 | Yes | Yes | NULL | No | tax_amount, cost_center | REQUIRES_REVIEW |

---

## Exception Coverage

| Exception Type | Scenarios |
|---------------|-----------|
| PO_NOT_FOUND | SCN-06 (recoverable), SCN-11, SCN-13 |
| AMOUNT_MISMATCH | SCN-07, SCN-08, SCN-09, SCN-14, SCN-15 |
| TAX_MISMATCH | SCN-09, SCN-10, SCN-15 |
| DUPLICATE_INVOICE | SCN-12 |
| EXTRACTION_LOW_CONFIDENCE | SCN-06, SCN-13 |
| CURRENCY_MISMATCH | SCN-13 |

## Data Quality Signal Coverage

| Signal | Scenarios |
|--------|-----------|
| High confidence (>0.90) | SCN-01, 02, 03, 04, 05, 08, 12, 14 |
| Medium confidence (0.70–0.90) | SCN-06, 07, 09, 10, 15 |
| Low confidence (<0.70) | SCN-11, 13 |
| Missing PO reference | SCN-13 |
| Malformed PO reference | SCN-11 |
| OCR-noisy PO reference | SCN-06 |
| Amount over PO | SCN-07, 09, 14 |
| Amount under PO | SCN-08 |
| Wrong VAT rate | SCN-09 (5%), SCN-10 (10%) |
| Missing tax | SCN-15 |
| Missing cost center | SCN-09, 15 |
| Missing currency | SCN-13 |
| Duplicate flag set | SCN-12 |
| High-value (>50k SAR) | SCN-04, 08, 11, 12, 13, 14 |

---

## QA/Large Mode — Randomly Generated Scenarios

When running with `--mode=qa` or `--mode=large`, additional scenarios are generated with randomized characteristics:

| Characteristic | Distribution |
|---------------|-------------|
| PO noise | 70% none, 12% swap_digit, 10% malformed, 8% missing |
| Amount delta | Weighted selection from [0, ±75, ±120, 300, 500, 1200] |
| Tax override | 75% normal, 10% at 5%, 8% at 10%, 7% missing |
| Duplicate flag | 8% probability |
| Cost center | Random from [CC-3010, CC-2010, CC-5010, CC-6010, ""] |
| Vendor | Uniform random from 10 TWO_WAY vendors |
| Branch | Uniform random from 7 branches |
| Confidence | Uniform 0.50–0.98 |

Random seed is configurable via `--seed=N` for reproducibility.

---

## UI Verification Checklist

### Invoice List Page
- [ ] All 15 (or 25/45) invoices appear in the list
- [ ] Status badges display correctly (EXTRACTED, VALIDATED, READY_FOR_RECON)
- [ ] Vendor names render properly (including long names like "Saudi Services Co. Ltd. (SSCO)")
- [ ] Total amounts format correctly with SAR currency
- [ ] Duplicate indicator shows on SCN-12
- [ ] Filtering by status works across all three statuses
- [ ] Sorting by date/amount/vendor works correctly

### Invoice Detail Page
- [ ] All header fields populated (invoice number, vendor, dates, amounts)
- [ ] PO reference shown (including malformed ones)
- [ ] Extraction confidence displayed as percentage
- [ ] Raw extraction JSON renders in the extraction data section
- [ ] ExtractionResult record accessible (engine name, version, duration)
- [ ] Line items table shows all service line items with descriptions
- [ ] Missing fields (cost center, tax, currency) shown as blank or "N/A"
- [ ] Duplicate linkage shown for SCN-12 → SCN-05
- [ ] "Start Reconciliation" action is available for READY_FOR_RECON invoices
- [ ] Document upload metadata (filename, size) is accessible
- [ ] `created_by` / `updated_by` show AP_PROCESSOR user (Fatima Al-Rashid)

### Reconciliation Trigger
- [ ] Can trigger reconciliation from invoice detail for READY_FOR_RECON invoices
- [ ] EXTRACTED invoices may need status progression before reconciliation
- [ ] Reconciliation creates AP Case + stages correctly
- [ ] TWO_WAY path is selected (not THREE_WAY) based on service invoice characteristics

### Audit Trail Verification
- [ ] All invoices have `created_by` = ap.processor@mcd-ksa.com
- [ ] All invoices have `updated_by` = ap.processor@mcd-ksa.com
- [ ] All POs have `created_by` / `updated_by` = AP_PROCESSOR user
- [ ] All vendors have `created_by` / `updated_by` = AP_PROCESSOR user
- [ ] RBAC `UserRole` record exists: AP_PROCESSOR, primary=True, active=True
- [ ] `ExtractionResult` records exist for all 15 invoices
- [ ] `ExtractionResult.created_by` = AP_PROCESSOR user
- [ ] `DocumentUpload.uploaded_by` = AP_PROCESSOR user

### Governance Page Verification (`/governance/`)
- [ ] 30 `AuditEvent` records visible (2 per invoice)
- [ ] `INVOICE_UPLOADED` events present for all 15 invoices
- [ ] `EXTRACTION_COMPLETED` events present for all 15 invoices
- [ ] `performed_by` = ap.processor@mcd-ksa.com on all events
- [ ] `actor_primary_role` = AP_PROCESSOR on all events
- [ ] `invoice_id` cross-reference is populated on all events
- [ ] `status_before` / `status_after` reflect correct transitions (UPLOADED→EXTRACTION_IN_PROGRESS, EXTRACTION_IN_PROGRESS→EXTRACTED/VALIDATED/READY_FOR_RECON)
- [ ] `metadata_json` contains extraction details (confidence, engine, vendor, amount)
