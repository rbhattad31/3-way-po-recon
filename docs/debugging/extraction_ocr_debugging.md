# Extraction OCR Debugging Guide

## Overview

When an invoice is uploaded, the system runs a multi-stage pipeline:

1. **OCR** (Azure Document Intelligence or native PyPDF2) → raw text from the PDF.
2. **Category classification** (`InvoiceCategoryClassifier`) → goods / service / travel.
3. **Prompt composition** (`InvoicePromptComposer`) → base + category + country overlays.
4. **LLM extraction** (`InvoiceExtractionAgent`) → structured JSON.
5. **Response repair** (`ResponseRepairService`) → 5 deterministic rules fix common LLM errors before the parser.
6. **Parse + normalize + validate** → `Invoice` record saved.

`ExtractionResult.raw_response` contains the repaired LLM JSON (plus `_repair` metadata if any repairs were made). `ExtractionResult.ocr_text` contains the raw OCR text.

---

## Where Data Is Stored

| What | Model | Field | Notes |
|---|---|---|---|
| Repaired LLM JSON | `ExtractionResult` | `raw_response` | Includes `_repair` key if ResponseRepairService acted |
| Repair actions taken | `ExtractionResult` | `raw_response["_repair"]` | `{was_repaired, repair_actions, warnings}` |
| Raw OCR text | `ExtractionResult` | `ocr_text` | What was sent to LLM |
| Governed pipeline OCR | `ExtractionDocument` | `ocr_text` | Only when governed pipeline succeeds |
| Invoice normalized fields | `Invoice` | `invoice_number`, `vendor_tax_id`, `buyer_name`, `due_date`, `tax_breakdown`, etc. | Final persisted values |
| Agent run details | `AgentRun` | `output_payload`, `prompt_tokens`, `input_payload` | Linked via `ExtractionResult.agent_run_id` (latest run); all runs for an upload queryable via `AgentRun.objects.filter(document_upload_id=...)` |
| All extraction runs for upload | `AgentRun` | `document_upload` FK (indexed) | Added 2026-03-28. Token totals in the console are `SUM()` across all rows. |

---

## How to Debug a Missed or Wrong Field

### Step 1 — Find the ExtractionResult

From the console URL `/extraction/console/<pk>/`, the `pk` is the `ExtractionResult` ID.

```python
from apps.extraction.models import ExtractionResult

ext = ExtractionResult.objects.get(pk=<pk>)
```

### Step 2 — Check what the LLM returned (after repair)

```python
import json
print(json.dumps(ext.raw_response, indent=2))
```

Check whether `_repair` was applied:

```python
repair = ext.raw_response.get("_repair", {})
if repair.get("was_repaired"):
    print("Repair actions:", repair["repair_actions"])
    print("Repair warnings:", repair.get("warnings", []))
```

### Step 3 — Check the OCR text

```python
print(ext.ocr_text)
```

Search for the field manually in the OCR text:

- **Field present in OCR but wrong in LLM output** → prompt issue or repair rule needed. Update the prompt in Langfuse (`extraction-invoice_system`) or add a repair rule to `ResponseRepairService`.
- **Field absent from OCR** → OCR/PDF issue. Check scan quality and OCR mode.
- **`ocr_text` empty** → Extraction pre-dates the `ocr_text` field, or governed pipeline failed. Reprocess.

### Step 4 — Check invoice category and prompt used

```python
# Check what category was detected and which prompt hash was used
print("Category:", ext.raw_response.get("_category"))  # if stored
# Or check the AgentRun
from apps.agents.models import AgentRun, AgentMessage
agent_run = AgentRun.objects.get(pk=ext.agent_run_id)
sys_msg = AgentMessage.objects.filter(agent_run=agent_run, role="system").first()
print("System prompt (first 500 chars):", sys_msg.content[:500] if sys_msg else "Not found")
```

### Step 5 — Check OCR mode

```python
from apps.extraction_core.models import ExtractionRuntimeSettings

settings = ExtractionRuntimeSettings.get_active()
print("OCR enabled:", settings.ocr_enabled if settings else "Using env var")
```

- `ocr_enabled=True` → Azure Document Intelligence (handles scanned/image PDFs).
- `ocr_enabled=False` → PyPDF2 native text extraction (text-layer PDFs only).

Enable/disable at `/extraction/control-center/settings/`.

### Step 6 — Check the AgentRun

```python
from apps.agents.models import AgentRun

agent_run = AgentRun.objects.get(pk=ext.agent_run_id)
print("Status:", agent_run.status)
print("Error:", agent_run.error_message)
print("Tokens:", agent_run.total_tokens)
```

If `status=FAILED`, the LLM call failed entirely — check `error_message` for API key or quota issues.

---

## Common Causes of Missed or Wrong Fields

| Symptom | Likely Cause | Fix |
|---|---|---|
| `invoice_number` is blank | LLM extracted CART Ref / IRN instead | ResponseRepairService should have cleared it. Check `raw_response["_repair"]`. If repair didn't fire, the OCR label pattern may not be in `_EXCLUDED_REFERENCE_LABELS`. |
| `invoice_number` is label word like "No." | OCR recovery regex matched label suffix | Fixed in Phase 2 (digit requirement in `_recover_invoice_number_from_ocr`). Reprocess. |
| `vendor_tax_id` or `buyer_name` is blank | Field missing from old prompt version | Prompt was updated. Run `push_prompts_to_langfuse`. Reprocess. |
| `tax_breakdown` is all zeros | LLM returned tax_breakdown but parser didn't save it | Check `NormalizationService` tax_breakdown handling. |
| `tax_percentage` is wrong | LLM copied line-level rate instead of computing header rate | ResponseRepairService rule b recomputes from `tax_amount/subtotal` and snaps to nearest valid GST slab (0/3/5/12/18/28%) when CGST/SGST/IGST breakdown keys are present. Check `raw_response["_repair"]` — repair action will say "snapped from computed X% to GST slab" if snapping occurred. |
| `tax_percentage` shows fractional rate like 8.33% or 16.67% on Indian invoice | Computed rate did not snap to GST slab | Ensure `tax_breakdown` contains at least one of `cgst`, `sgst`, `igst` keys so GST context is detected. If breakdown is missing, the LLM prompt may not have extracted it — reprocess after updating the India GST overlay prompt. |
| `subtotal` doesn't match line items | LLM summed incorrectly | ResponseRepairService rule c aligns subtotal. Check `_repair` metadata. |
| OCR text is garbled | Low-quality scan or rotated page | Re-scan; ensure Azure DI is enabled. |
| `raw_response` is null | `InvoiceExtractionAgent` failed | Check `AgentRun.error_message`; verify LLM API keys. |
| `invoice_number` present in `raw_response` but blank in `Invoice` | Normalization stripped it | Check `normalize_invoice_number()` in `apps/core/utils.py`. |
| `tax_percentage` 0.25% rejected on Indian invoice | Valid for precious stones (HSN 7102-7104, Chapter 71) but invoice not detected as such | `_is_precious_stone_invoice()` scans line item descriptions and vendor name for keywords (diamond, ruby, gemstone, etc.) and HSN codes 7102-7104. If keywords are absent, 0.25% is blocked as invalid. Add a keyword or correct the rate. |

---

## Reprocessing an Invoice

From the extraction console (`/extraction/console/<pk>/`), click **Reprocess**. This:
1. Re-runs OCR on the original uploaded file.
2. Re-classifies invoice category.
3. Composes a fresh prompt (picks up Langfuse prompt changes).
4. Re-sends OCR text to LLM; applies response repair.
5. Overwrites `ExtractionResult.raw_response` and `ExtractionResult.ocr_text`.
6. Updates all `Invoice` fields with the new extraction output.

Requires the `extraction.reprocess` permission.

> **Note:** Reprocessing will NOT mark the invoice as a duplicate of itself. The duplicate check
> correctly excludes the existing invoice on the same upload when reprocessing.

> **Credits:** Every successful reprocess consumes 1 credit (charged independently per attempt with a unique `reference_id`). Token counts and costs shown in the Cost & Tokens panel are the **cumulative SUM across all extraction runs** for this document, not just the most recent. The "Extraction Runs" KPI card shows how many runs have been recorded.

---

## Known Issues & Fixes (changelog)

| Date | Issue | Fix |
|---|---|---|
| 2026-03-28 | Invoice incorrectly marked as duplicate on reprocess | `DuplicateDetectionService.check()` now receives `exclude_invoice_id` set to the existing invoice on the same upload in both `tasks.py` and `template_views.py`. |
| 2026-03-28 | Indian GST tax rate shows fractional values (e.g. 8.33%, 16.67%) | `ResponseRepairService._repair_tax_percentage()` now snaps the computed rate to the nearest standard GST slab (0/3/5/12/18/28%) within ±2 pp when CGST/SGST/IGST breakdown keys are detected. |
| 2026-03-28 | Credits not deducting on 2nd+ reprocess | `reference_id` was `upload.pk` (never changes); idempotency guard treated every reprocess after the first as a duplicate and skipped it. Now uses `f"reprocess-{upload.pk}-{timestamp}"` — unique per attempt — passed as task kwargs `credit_ref_type` / `credit_ref_id`. |
| 2026-03-28 | Token total in Cost & Tokens panel showed only the latest extraction run | `AgentRun` had no FK back to `DocumentUpload`; `ExtractionResult.agent_run_id` was overwritten on each reprocess. Added `AgentRun.document_upload` FK (migration 0009). Console now aggregates `SUM(prompt_tokens, completion_tokens, total_tokens)` across all runs for the upload. |
| 2026-03-28 | 0.25% GST incorrectly rejected for precious stones | Added `_is_precious_stone_invoice()` keyword detection in `ValidationService`. 0.25% is allowed when the invoice line items or vendor name contain precious stone keywords or HSN codes 7102-7104 (Schedule I, Chapter 71). `ResponseRepairService._GST_STANDARD_RATES` also updated to include 0.25. |

---

## Checking via Django Shell (Quick Reference)

```python
from apps.documents.models import Invoice
from apps.extraction.models import ExtractionResult
import json

invoice = Invoice.objects.get(pk=<invoice_pk>)
ext = ExtractionResult.objects.filter(invoice=invoice).order_by("-created_at").first()

# Check extracted fields
for field in ["invoice_number", "vendor_tax_id", "buyer_name", "due_date", "tax_breakdown"]:
    print(f"{field}: raw={ext.raw_response.get(field)!r}  normalized={getattr(invoice, field, 'N/A')!r}")

# Check repair activity
repair = ext.raw_response.get("_repair", {})
print("Was repaired:", repair.get("was_repaired", False))
print("Repair actions:", repair.get("repair_actions", []))

# Scan OCR text for invoice number label patterns
if ext.ocr_text:
    for i, line in enumerate(ext.ocr_text.splitlines(), 1):
        if any(kw in line.lower() for kw in ["invoice", "bill", "ref", "no.", "irn", "cart"]):
            print(f"Line {i}: {line}")
```

---

## Key Files

| File | Purpose |
|---|---|
| `apps/extraction/services/extraction_adapter.py` | Main pipeline — OCR, classify, compose, LLM, repair |
| `apps/extraction_core/services/invoice_category_classifier.py` | Invoice type classifier (goods/service/travel) |
| `apps/extraction/services/invoice_prompt_composer.py` | Modular prompt builder |
| `apps/extraction/services/response_repair_service.py` | 5 deterministic repair rules |
| `apps/extraction/services/parser_service.py` | JSON → ParsedInvoice dataclass |
| `apps/extraction/services/persistence_service.py` | Saves Invoice + line items to DB |
| `apps/core/prompt_registry.py` | 18 prompt defaults; Langfuse resolution chain |
| `apps/core/management/commands/push_prompts_to_langfuse.py` | Sync prompts: `python manage.py push_prompts_to_langfuse` |
| `apps/core/utils.py` | `normalize_invoice_number()` post-extraction normalization |
