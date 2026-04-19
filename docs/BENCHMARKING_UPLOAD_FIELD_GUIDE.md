# Benchmarking App Upload and Field Guide

This guide is for positive-path testing of the benchmarking workflow from request creation to result review.

## 1) Create a new benchmarking request

Open `Benchmarking -> New Request` and fill these fields:

- **Request Title** (required): Example `HVAC Quotation - Positive Test Batch`
- **Project Name**: Example `Dubai Mall Expansion Phase 3`
- **Store / Project Type**: Example `MALL`
- **Geography** (required): choose `UAE` (or your scenario)
- **Commercial Scope** (required): choose `SITC`, `ITC`, or `EQUIPMENT_ONLY`
- **Notes** (optional): any internal comments

## 2) RFQ selection rules (important)

In the **RFQ (optional)** section, choose one option:

- **Yes - select from generated RFQs** (`rfq_source=system`)
  - Use when an RFQ already exists in the procurement module.
  - Pick one RFQ from the dropdown `Select RFQ Reference`.
- **Upload RFQ** (`rfq_source=upload`)
  - Use when RFQ exists outside the system.
  - Upload one RFQ PDF in `Upload Your RFQ Document`.
  - `RFQ Reference` is auto-filled from filename (you can edit it).
- **No** (`rfq_source=manual`)
  - Use when no RFQ document/reference is available.

## 3) Quotation upload options

Use **Quotation File (PDF or ZIP)**:

- Single vendor test: upload one PDF.
- Multi-vendor test: upload a ZIP containing multiple PDFs.
- ZIP rules:
  - Only PDF files are processed.
  - Maximum 50 PDFs in one ZIP.

For this repo, generated positive test documents are at:

- `media/benchmarking/positive_test_docs/quotation_01_al_najah_hvac.pdf`
- `media/benchmarking/positive_test_docs/quotation_02_desert_cooling_solutions.pdf`
- `media/benchmarking/positive_test_docs/quotation_03_polar_air_mep.pdf`
- `media/benchmarking/positive_test_docs/quotation_04_gulf_climate_technologies.pdf`

## 4) Recommended positive test runs

### A) Single vendor run

1. Create request with required fields.
2. Keep RFQ as `No` (manual) or choose valid RFQ source.
3. Upload one quotation PDF.
4. Submit `Analyse Quotation`.

Expected behavior:

- Request moves through processing and reaches completed state.
- Extracted lines are visible on request detail.
- Benchmark source appears per line (DB corridor or market fallback).
- Quoted and benchmark columns show `AED` values.

### B) Multi-vendor run

1. Create one request.
2. Upload multiple quotation PDFs (one by one later) or ZIP upload on create.
3. Submit and open request detail.

Expected behavior:

- Vendor panels are listed.
- If supplier name is missing in extraction, UI fallback labels show as `Vendor A`, `Vendor B`, etc.
- `View Document` opens the quotation preview modal inside the page.

## 5) Field checklist before submit

Verify these minimum inputs:

- `title` not empty
- `geography` selected
- `scope_type` selected
- RFQ path is valid for chosen `rfq_source`
- Quotation file is PDF or ZIP (with PDFs)

## 6) Quick local generation command

If you need to regenerate the sample quotation files:

```powershell
"c:/Users/BRADSOL/OneDrive - bradsol.com/Sridhar_Bradsol_Projects/Reconcilation_Project/3-way-po-recon/.venv/Scripts/python.exe" scripts/generate_benchmark_positive_docs.py
```
