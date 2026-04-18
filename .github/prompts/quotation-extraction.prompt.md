---
mode: agent
description: "Add or modify quotation extraction and prefill features (OCR pipeline, LLM extraction, field mapping, prefill review)"
---

# Quotation Extraction Feature

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/PROCUREMENT.md` -- Section 5.5 (Quotation Document Prefill Pipeline: architecture diagram, 5-step pipeline, two-phase persistence), Section 6.4 (AzureDocumentIntelligenceExtractorAgent), Section 6.7 (RequestExtractionAgent)
- `docs/EXTRACTION_AGENT.md` -- invoice extraction pipeline reference (the quotation pipeline reuses the same OCR adapter)
- `docs/PROCUREMENT_REQUEST_WALKTHROUGH.txt` -- Step 2 (PDF-led entry mode), quotation upload flow

### Source Files (read in this order)
1. `apps/procurement/services/prefill/quotation_prefill_service.py` -- QuotationDocumentPrefillService.run_prefill() -- 5-step pipeline: OCR -> LLM extraction -> field mapping -> confidence classification -> payload storage. OCR text limit: 60K chars. LLM max_tokens: 8192.
2. `apps/procurement/services/prefill/attribute_mapping_service.py` -- AttributeMappingService.map_quotation_fields() -- synonym dictionaries for header fields, commercial terms, line items. `_QUOTATION_FIELD_SYNONYMS` mapping. classify_confidence() separates high (>=0.7) vs low (<0.7) fields.
3. `apps/procurement/services/prefill/prefill_review_service.py` -- PrefillReviewService.confirm_quotation_prefill() -- atomic: updates header fields on SupplierQuotation + bulk-creates QuotationLineItem records from confirmed data
4. `apps/procurement/services/prefill/prefill_status_service.py` -- PrefillStatusService (mark_quotation_in_progress, mark_quotation_completed with REVIEW_PENDING status, mark_quotation_failed)
5. `apps/procurement/services/prefill/request_prefill_service.py` -- RequestDocumentPrefillService (SOW/RFQ attribute extraction -- separate from quotation prefill)
6. `apps/procurement/agents/quotation_extraction_agent.py` -- QuotationExtractionAgent.extract() -- single-shot LLM call with structured JSON prompt, 60K char input limit
7. `apps/procurement/agents/Azure_Document_Intelligence_Extractor_Agent.py` -- ReAct-style DI extractor (tool-calling loop), supports PDF/image/DOCX/XLSX, returns structured JSON with header + line_items + commercial_terms
8. `apps/procurement/agents/request_extraction_agent.py` -- lightweight OCR text -> structured procurement request dict (12K char limit)
9. `apps/procurement/models.py` -- SupplierQuotation (extraction_status, extraction_confidence, prefill_payload_json, prefill_status), QuotationLineItem (description, normalized_description, category_code, quantity, unit, unit_rate, brand, model, extraction_confidence)
10. `apps/procurement/tasks.py` -- run_quotation_prefill_task (Celery task wrapping run_prefill)

### Comprehension Check
1. Two-phase persistence: Phase 1 stores extracted data as JSON in `prefill_payload_json` (status REVIEW_PENDING). Phase 2 persists to QuotationLineItem table only after user confirmation via PrefillReviewService.
2. OCR text limit is 60K characters (handles 40+ page proposals). LLM response limit is 8192 tokens.
3. Field mapping uses synonym dictionaries -- header fields (vendor_name, quotation_number, etc.), commercial terms (warranty, payment, delivery, etc.), and line items (description, qty, unit_rate, etc.)
4. Confidence classification: high >= 0.7, low < 0.7. Low-confidence fields are flagged for user review.
5. The pipeline reuses Azure Document Intelligence OCR from the invoice extraction pipeline (via InvoiceExtractionAdapter._ocr_document).
6. Line items are extracted from pricing tables, BOQ sections, licensing tables, cost breakdowns, and commercial schedules anywhere in the document.

---

## When Modifying the Extraction Pipeline

1. The 5-step pipeline in `QuotationDocumentPrefillService.run_prefill()`:
   - Step 1: OCR (Azure Document Intelligence)
   - Step 2: LLM extraction (GPT-4o, 60K char input, 8192 token response)
   - Step 3: Field mapping (AttributeMappingService.map_quotation_fields)
   - Step 4: Confidence classification
   - Step 5: Store prefill_payload_json, set status to REVIEW_PENDING
2. Never skip step 3 (field mapping) -- it normalizes LLM output to canonical field names
3. The LLM system prompt requires JSON output with: header fields, line_items array, commercial terms
4. Always strip markdown code fences from LLM responses before JSON parsing

## When Adding New Extractable Fields

1. Add the field to the LLM system prompt in `QuotationExtractionAgent.extract()` (or `_extract_quotation_data()`)
2. Add synonym entries to `_QUOTATION_FIELD_SYNONYMS` in `AttributeMappingService` so variant LLM output keys map to the canonical field name
3. If the field is a header field, add it to the SupplierQuotation model and update `PrefillReviewService.confirm_quotation_prefill()` to persist it
4. If the field is per-line-item, add it to QuotationLineItem model and update the line item creation in `confirm_quotation_prefill()`
5. If the field is a commercial term, add it to the commercial terms mapping section
6. Update the prefill review UI to display the new field for user confirmation
7. Create migration: `python manage.py makemigrations procurement`

## When Adding a New Document Type for Extraction

1. Check if `AzureDocumentIntelligenceExtractorAgent` already supports the format (PDF, JPEG, PNG, BMP, TIFF, HEIF, DOCX, XLSX, PPTX)
2. If it is a new format, extend `_validate_input()` in the agent to accept the MIME type
3. Create a new prefill service (following the pattern of `QuotationDocumentPrefillService` or `RequestDocumentPrefillService`):
   - OCR step may reuse the same DI adapter or need a custom extraction prompt
   - LLM extraction prompt must be tailored to the document structure
   - Field mapping service may need new synonym dictionaries
4. Create a corresponding Celery task in `apps/procurement/tasks.py`
5. Wire the upload endpoint to dispatch the new task

## When Modifying Field Synonyms

1. All synonym dictionaries are in `AttributeMappingService` in `apps/procurement/services/prefill/attribute_mapping_service.py`
2. `_QUOTATION_FIELD_SYNONYMS` maps variant keys to canonical names:
   ```
   {"vendor": "vendor_name", "supplier": "vendor_name", "company": "vendor_name", ...}
   ```
3. Add new synonyms when LLM output uses unexpected field names (check extraction logs for unmapped keys)
4. Synonyms are case-insensitive (keys are lowercased before matching)
5. Do not change canonical field names unless you also update all downstream consumers (PrefillReviewService, serializers, templates)

## When Modifying the Prefill Review Flow

1. `PrefillReviewService.confirm_quotation_prefill(quotation, reviewed_data)` is atomic -- either all fields and line items are persisted or none
2. The `reviewed_data` dict is the user-corrected version of `prefill_payload_json`
3. Header fields are set directly on the `SupplierQuotation` instance
4. Line items are bulk-created as `QuotationLineItem` records -- existing line items from a previous prefill are NOT deleted (check for duplicates if re-running)
5. Status transitions: REVIEW_PENDING -> COMPLETED (on confirm), REVIEW_PENDING -> FAILED (on reject)

## Coding Rules

- **Two-phase persistence**: Never persist line items directly during extraction. Store everything in `prefill_payload_json` first. Line items are only created in the DB after user confirmation.
- **OCR text limit**: Trim OCR text to 60K characters before sending to LLM. For the request extraction agent, the limit is 12K.
- **LLM response parsing**: Always strip markdown code fences (`\`\`\`json`, `\`\`\``) from LLM output before JSON parsing. Handle truncated JSON responses gracefully.
- **Confidence scores**: Per-field confidence is 0.0-1.0. Threshold for high confidence is 0.7. Flag anything below for user review.
- **ASCII only**: Apply `_sanitise_text()` before persisting any LLM-generated content.
- **Fail-silent extraction**: If any step fails, set `prefill_status=FAILED` and `extraction_status=FAILED` on the quotation. Never leave it in IN_PROGRESS state.
