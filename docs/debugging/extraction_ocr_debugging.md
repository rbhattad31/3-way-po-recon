# Extraction OCR Debugging Guide

## Overview

When an invoice is uploaded, the system:
1. Runs OCR (Azure Document Intelligence or native PyPDF2) to get raw text from the PDF.
2. Sends that raw text to GPT-4o (via `InvoiceExtractionAgent`) to extract structured fields.
3. Saves the structured JSON in `ExtractionResult.raw_response`.
4. Saves the raw OCR text in `ExtractionResult.ocr_text` (added 2026-03-27).

If a field like `invoice_number` is blank after extraction, you need to check whether:
- The OCR text contained the field but the LLM failed to extract it (prompt issue).
- The OCR text itself was blank or garbled (OCR/PDF issue).

---

## Where Data Is Stored

| What | Model | Field | Notes |
|---|---|---|---|
| Structured extracted fields (JSON) | `ExtractionResult` | `raw_response` | What the LLM returned |
| Raw OCR text sent to LLM | `ExtractionResult` | `ocr_text` | Added 2026-03-27; empty for extractions before this date |
| Governed pipeline OCR text | `ExtractionDocument` | `ocr_text` | Only populated when governed pipeline succeeds |
| Invoice normalized fields | `Invoice` | `invoice_number`, `raw_invoice_number`, etc. | Final persisted values |
| Agent run details | `AgentRun` | `output_payload`, `prompt_tokens`, etc. | Linked via `ExtractionResult.agent_run_id` |

---

## How to Debug a Missed Field

### Step 1 -- Find the ExtractionResult

From the console URL `/extraction/console/<pk>/`, the `pk` is the `ExtractionResult` ID.

```python
from apps.extraction.models import ExtractionResult

ext = ExtractionResult.objects.get(pk=<pk>)
```

### Step 2 -- Check what the LLM returned

```python
import json
print(json.dumps(ext.raw_response, indent=2))
```

Look for the field in question (e.g. `"invoice_number"`). If it is `""` or missing, the LLM did not extract it.

### Step 3 -- Check what the OCR produced

```python
print(ext.ocr_text)
```

Search for the invoice number manually in the OCR text (Ctrl+F in a text editor).

- **If the text IS there but extraction missed it** -- this is a prompt issue. The LLM did not recognize the label. Update the extraction prompt in `apps/core/prompt_registry.py` or via Django admin under Prompt Templates.
- **If the text is NOT there** -- this is an OCR issue. The PDF may be image-only, scanned poorly, or text may be in a non-extractable layer.
- **If `ocr_text` is empty** -- the extraction was run before 2026-03-27 (field did not exist yet). Reprocess the invoice to populate it.

### Step 4 -- Check OCR mode

```python
from apps.extraction_core.models import ExtractionRuntimeSettings

settings = ExtractionRuntimeSettings.get_active()
print("OCR enabled:", settings.ocr_enabled if settings else "Using env var")
```

- `ocr_enabled=True` -> Azure Document Intelligence (handles scanned/image PDFs).
- `ocr_enabled=False` -> Native PDF text extraction via PyPDF2 (only works for PDFs with a text layer).

If the invoice is a scanned image and OCR is disabled, switch it on at `/extraction/control-center/settings/`.

### Step 5 -- Check the AgentRun

```python
from apps.agents.models import AgentRun

agent_run = AgentRun.objects.get(pk=ext.agent_run_id)
print("Status:", agent_run.status)
print("Error:", agent_run.error_message)
print("Tokens:", agent_run.total_tokens)
print("Output:", agent_run.output_payload)
```

If `status=FAILED`, the LLM call failed entirely -- check `error_message` for API key or quota issues.

---

## Common Causes of Missed invoice_number

| Symptom | Likely Cause | Fix |
|---|---|---|
| OCR text has the number but extraction missed it | LLM did not recognize the label (e.g. "Tax Inv. No.", "Bill No.") | The prompt was updated 2026-03-27 with 15+ label variants. Reprocess. |
| OCR text is garbled around the number | Low-quality scan or rotated page | Re-scan the document; ensure Azure DI is enabled. |
| OCR text is empty | Scanned image PDF with OCR disabled | Enable OCR in Extraction Control Center. |
| `raw_response` is null | `InvoiceExtractionAgent` failed | Check `AgentRun.error_message`; check LLM API keys. |
| `invoice_number` present in `raw_response` but blank in `Invoice` | Normalization stripped it | Check `normalize_invoice_number()` in `apps/core/utils.py`. |

---

## Reprocessing an Invoice

From the extraction console (`/extraction/console/<pk>/`), click **Reprocess**. This:
1. Re-runs OCR on the original uploaded file.
2. Re-sends OCR text to the LLM with the current prompt.
3. Overwrites `ExtractionResult.raw_response` and `ExtractionResult.ocr_text`.
4. Updates all `Invoice` fields with the new extraction output.

Requires the `extraction.reprocess` permission.

---

## Checking via Django Shell (Quick Reference)

```python
# Get extraction result for an invoice
from apps.documents.models import Invoice
from apps.extraction.models import ExtractionResult

invoice = Invoice.objects.get(pk=<invoice_pk>)
ext = ExtractionResult.objects.filter(invoice=invoice).order_by("-created_at").first()

# Check what was extracted
print("invoice_number in raw_response:", ext.raw_response.get("invoice_number"))
print("invoice_number on invoice:", invoice.invoice_number)
print("raw_invoice_number on invoice:", invoice.raw_invoice_number)

# Check OCR text (search for number manually)
if ext.ocr_text:
    for i, line in enumerate(ext.ocr_text.splitlines(), 1):
        if any(kw in line.lower() for kw in ["invoice", "bill", "ref", "no.", "#"]):
            print(f"Line {i}: {line}")
else:
    print("OCR text not saved -- extraction pre-dates 2026-03-27 or governed pipeline failed")
```

---

## Key Files

| File | Purpose |
|---|---|
| `apps/extraction/models.py` | `ExtractionResult.ocr_text` field definition |
| `apps/extraction/migrations/0010_add_ocr_text_to_extraction_result.py` | Migration that added the field |
| `apps/extraction/services/persistence_service.py` | Where `ocr_text` is written on every extraction save |
| `apps/extraction/services/extraction_adapter.py` | OCR + LLM pipeline; `ExtractionResponse.ocr_text` carries the raw text |
| `apps/core/prompt_registry.py` | `extraction.invoice_system` prompt -- the instruction set sent to GPT-4o |
| `apps/core/management/commands/seed_prompts.py` | Push updated prompt to DB: `python manage.py seed_prompts --force` |
| `apps/core/utils.py` | `normalize_invoice_number()` -- post-extraction normalization |
