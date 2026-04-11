"""
Azure Document Intelligence extraction service for benchmarking quotations.

Replaces the pdfplumber/heuristic extraction in ExtractionService with a
proper Azure DI prebuilt-layout call that extracts tables, key-value pairs,
and full text from uploaded quotation PDFs.

Falls back to the pdfplumber-based ExtractionService gracefully when:
  - Azure DI credentials are not configured (AZURE_DI_ENDPOINT / AZURE_DI_KEY)
  - The azure-ai-formrecognizer SDK is not installed
  - The Azure DI API call fails

Usage:
    from apps.benchmarking.services.azure_di_extraction_service import AzureDIExtractionService
    result = AzureDIExtractionService.extract(file_path_or_bytes, source_name="Q-001.pdf")
    # result["text"]      -> full concatenated text
    # result["tables"]    -> list of parsed table dicts
    # result["line_items"]-> list of parsed line item dicts (numeric rows)
    # result["raw_json"]  -> serialisable Azure DI response snapshot
    # result["error"]     -> None or error message string
    # result["engine"]    -> "azure_di" | "pdfplumber_fallback"
"""
from __future__ import annotations

import io
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_amount(raw: str) -> Decimal | None:
    """Strip currency symbols, commas, and spaces; return Decimal or None."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _is_numeric_cell(val: str) -> bool:
    """Return True if the cell looks like a number (possibly with commas/AED)."""
    if not val:
        return False
    cleaned = re.sub(r"[^\d.]", "", val.replace(",", ""))
    return bool(cleaned) and len(cleaned) >= 1


def _parse_table_rows(table_data: list[list[str]]) -> list[dict]:
    """
    Heuristic: scan a 2-D table for rows that look like line items
    (description + 2-4 numeric trailing columns = qty, unit-rate, total).
    Returns a list of raw line item dicts.
    """
    items = []
    line_num = 0
    for row in table_data:
        if not row:
            continue
        # Strip whitespace from every cell
        cells = [str(c or "").strip() for c in row]
        # Need at least 3 non-empty cells
        non_empty = [c for c in cells if c]
        if len(non_empty) < 3:
            continue

        # Try to identify numeric columns from the right
        numeric_vals = []
        for c in reversed(cells):
            if _is_numeric_cell(c):
                numeric_vals.insert(0, _clean_amount(c))
            else:
                break

        if len(numeric_vals) < 2:
            continue  # Not a data row

        # Description = everything before the first numeric block
        # The description text might span several left cells
        numeric_start_idx = len(cells) - len(numeric_vals)
        description_parts = [c for c in cells[:numeric_start_idx] if c]
        if not description_parts:
            continue

        description = " ".join(description_parts)
        # Skip header-ish rows
        lower = description.lower()
        if any(kw in lower for kw in ["description", "item", "particulars", "sr no", "s.no"]):
            continue

        line_num += 1
        # Extract numeric values: last = amount, second-to-last = unit_rate, third-to-last = qty
        amount = numeric_vals[-1] if len(numeric_vals) >= 1 else None
        unit_rate = numeric_vals[-2] if len(numeric_vals) >= 2 else None
        qty = numeric_vals[-3] if len(numeric_vals) >= 3 else None

        # Find UOM in description (common patterns: Nos, No., m2, m, Lot, Lump Sum, LS)
        uom = ""
        uom_match = re.search(
            r"\b(nos?\.?|m2|m3|lm|rm|sqm|sqft|ls|lot|lump\s*sum|set|unit|kg|ton|hr)\b",
            description,
            re.IGNORECASE,
        )
        if uom_match:
            uom = uom_match.group(0).strip()

        items.append(
            {
                "line_number": line_num,
                "description": description,
                "uom": uom,
                "quantity": qty,
                "unit_rate": unit_rate,
                "amount": amount,
                "extraction_confidence": 0.85,
            }
        )
    return items


def _tables_from_di_result(result) -> list[dict]:
    """Convert Azure DI AnalyzeResult tables to a JSON-serialisable list of dicts."""
    tables = []
    for t_idx, table in enumerate(result.tables or []):
        rows = []
        row_count = table.row_count
        col_count = table.column_count
        # Initialise as empty grid
        grid = [[""] * col_count for _ in range(row_count)]
        for cell in table.cells:
            r, c = cell.row_index, cell.column_index
            if r < row_count and c < col_count:
                grid[r][c] = cell.content or ""
        rows = [list(row) for row in grid]
        tables.append(
            {
                "table_index": t_idx,
                "row_count": row_count,
                "col_count": col_count,
                "rows": rows,
            }
        )
    return tables


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class AzureDIExtractionService:
    """
    Extract text and line items from a quotation PDF using Azure Document Intelligence.

    All methods are class methods for stateless usage.
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @classmethod
    def extract(
        cls,
        source: Union[str, bytes, io.IOBase],
        source_name: str = "",
    ) -> dict:
        """
        Extract text, tables, and parsed line items from a quotation PDF.

        Args:
            source: Absolute file path (str), raw bytes, or a file-like object.
            source_name: Descriptive label for logging (e.g. filename).

        Returns:
            {
                "text": str,
                "tables": list[dict],
                "line_items": list[dict],
                "raw_json": dict,        # serialisable snapshot of DI response
                "error": str | None,
                "engine": str,           # "azure_di" or "pdfplumber_fallback"
                "page_count": int,
                "duration_ms": int,
            }
        """
        try:
            return cls._extract_via_azure_di(source, source_name)
        except ImportError as exc:
            logger.warning(
                "AzureDIExtractionService: azure-ai-formrecognizer not installed "
                "-- falling back to pdfplumber for '%s': %s",
                source_name,
                exc,
            )
        except ValueError as exc:
            logger.warning(
                "AzureDIExtractionService: Azure DI not configured for '%s' -- "
                "falling back to pdfplumber: %s",
                source_name,
                exc,
            )
        except Exception as exc:
            logger.exception(
                "AzureDIExtractionService: Azure DI call failed for '%s' -- "
                "falling back to pdfplumber",
                source_name,
            )

        # Pdfplumber fallback
        return cls._extract_via_pdfplumber(source, source_name)

    # ------------------------------------------------------------------
    # Azure DI path
    # ------------------------------------------------------------------

    @classmethod
    def _extract_via_azure_di(cls, source, source_name: str) -> dict:
        from azure.ai.formrecognizer import DocumentAnalysisClient
        from azure.core.credentials import AzureKeyCredential
        from django.conf import settings

        endpoint = getattr(settings, "AZURE_DI_ENDPOINT", "")
        key = getattr(settings, "AZURE_DI_KEY", "")
        if not endpoint or not key:
            raise ValueError(
                "AZURE_DI_ENDPOINT and AZURE_DI_KEY must be set to use "
                "Azure Document Intelligence for benchmarking quotation extraction."
            )

        client = DocumentAnalysisClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )

        t0 = time.time()

        # Normalise source to bytes buffer
        if isinstance(source, str):
            with open(source, "rb") as f:
                document_bytes = f.read()
        elif isinstance(source, (bytes, bytearray)):
            document_bytes = source
        else:
            document_bytes = source.read()

        poller = client.begin_analyze_document(
            "prebuilt-layout",
            document=io.BytesIO(document_bytes),
        )
        result = poller.result()
        duration_ms = int((time.time() - t0) * 1000)
        page_count = len(result.pages) if result.pages else 0

        logger.info(
            "AzureDIExtractionService: Azure DI prebuilt-layout completed for '%s' "
            "in %dms -- %d pages, %d tables",
            source_name,
            duration_ms,
            page_count,
            len(result.tables or []),
        )

        # Concatenate all text lines
        full_lines = []
        for page in result.pages or []:
            for line in page.lines or []:
                full_lines.append(line.content)
        full_text = "\n".join(full_lines)

        # Extract tables
        tables = _tables_from_di_result(result)

        # Build serialisable raw_json snapshot (no DI SDK objects)
        raw_json = {
            "engine": "azure_di_prebuilt_layout",
            "page_count": page_count,
            "table_count": len(tables),
            "duration_ms": duration_ms,
            "tables": tables,                          # already serialisable
        }

        # Parse line items from tables (prefer tables over raw text heuristic)
        line_items = []
        if tables:
            for table_dict in tables:
                rows = table_dict.get("rows", [])
                parsed = _parse_table_rows(rows)
                if parsed:
                    line_items.extend(parsed)
                    break  # Use first table that yields results

        # If no tables produced line items, fall back to text-based heuristic
        if not line_items:
            from apps.benchmarking.services.extraction_service import ExtractionService
            line_items = ExtractionService.parse_line_items(full_text)

        # Re-number line items sequentially
        for idx, item in enumerate(line_items, start=1):
            item["line_number"] = idx

        return {
            "text": full_text,
            "tables": tables,
            "line_items": line_items,
            "raw_json": raw_json,
            "error": None,
            "engine": "azure_di",
            "page_count": page_count,
            "duration_ms": duration_ms,
        }

    # ------------------------------------------------------------------
    # pdfplumber fallback
    # ------------------------------------------------------------------

    @classmethod
    def _extract_via_pdfplumber(cls, source, source_name: str) -> dict:
        """Delegate to the existing ExtractionService (pdfplumber / raw fallback)."""
        from apps.benchmarking.services.extraction_service import ExtractionService

        t0 = time.time()

        # Normalise to file path or string
        if isinstance(source, (bytes, bytearray)):
            # Write to temp file then extract
            import tempfile
            import os
            suffix = os.path.splitext(source_name)[-1] or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(source)
                tmp_path = tmp.name
            text = ExtractionService.extract_text_from_pdf(tmp_path)
            os.unlink(tmp_path)
        elif isinstance(source, str):
            text = ExtractionService.extract_text_from_pdf(source)
        else:
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(source.read())
                tmp_path = tmp.name
            text = ExtractionService.extract_text_from_pdf(tmp_path)
            os.unlink(tmp_path)

        line_items = ExtractionService.parse_line_items(text)
        duration_ms = int((time.time() - t0) * 1000)

        return {
            "text": text,
            "tables": [],
            "line_items": line_items,
            "raw_json": {"engine": "pdfplumber_fallback", "duration_ms": duration_ms},
            "error": None if text else "pdfplumber returned empty text",
            "engine": "pdfplumber_fallback",
            "page_count": 0,
            "duration_ms": duration_ms,
        }
