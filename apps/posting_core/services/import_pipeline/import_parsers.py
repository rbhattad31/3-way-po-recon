"""Excel import parsers — low-level file reading and column normalization.

Supports .xlsx and .csv via openpyxl / csv stdlib.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default column mappings per batch type
DEFAULT_COLUMN_MAPS: Dict[str, Dict[str, str]] = {
    "VENDOR": {
        "vendor_code": "vendor_code",
        "vendor_name": "vendor_name",
        "tax_id": "tax_id",
        "vendor_group": "vendor_group",
        "country_code": "country_code",
        "payment_terms": "payment_terms",
        "currency": "currency",
        "is_active": "is_active",
    },
    "ITEM": {
        "item_code": "item_code",
        "item_name": "item_name",
        "description": "description",
        "item_type": "item_type",
        "category": "category",
        "uom": "uom",
        "tax_code": "tax_code",
        "is_active": "is_active",
    },
    "TAX": {
        "tax_code": "tax_code",
        "tax_label": "tax_label",
        "country_code": "country_code",
        "rate": "rate",
        "is_active": "is_active",
    },
    "COST_CENTER": {
        "cost_center_code": "cost_center_code",
        "cost_center_name": "cost_center_name",
        "department": "department",
        "business_unit": "business_unit",
        "is_active": "is_active",
    },
    "OPEN_PO": {
        "po_number": "po_number",
        "po_line_number": "po_line_number",
        "vendor_code": "vendor_code",
        "item_code": "item_code",
        "description": "description",
        "quantity": "quantity",
        "unit_price": "unit_price",
        "line_amount": "line_amount",
        "currency": "currency",
        "status": "status",
        "is_open": "is_open",
    },
}


def normalize_header(header: str) -> str:
    """Normalize a column header to snake_case."""
    h = header.strip().lower()
    h = re.sub(r"[^a-z0-9]+", "_", h)
    return h.strip("_")


def parse_excel_file(
    file_path: str,
    column_map: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse an Excel (.xlsx) or CSV file into a list of row dicts.

    Returns (rows, raw_headers).
    """
    path = Path(file_path)
    if path.suffix.lower() == ".csv":
        return _parse_csv(file_path, column_map)
    else:
        return _parse_xlsx(file_path, column_map)


def _parse_xlsx(
    file_path: str,
    column_map: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse an .xlsx file."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl is required for Excel import. Install via: pip install openpyxl")

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    raw_headers_row = next(rows_iter, None)
    if not raw_headers_row:
        return [], []

    raw_headers = [str(h) if h else "" for h in raw_headers_row]
    normalized_headers = [normalize_header(h) for h in raw_headers]

    # Build reverse map: normalized_header → target field
    field_map = _build_field_map(normalized_headers, column_map)

    rows: List[Dict[str, Any]] = []
    for row_values in rows_iter:
        if not row_values or all(v is None for v in row_values):
            continue
        row_dict: Dict[str, Any] = {}
        for idx, val in enumerate(row_values):
            if idx < len(normalized_headers):
                target = field_map.get(normalized_headers[idx])
                if target:
                    row_dict[target] = _clean_value(val)
        if any(v for v in row_dict.values()):
            rows.append(row_dict)

    wb.close()
    return rows, raw_headers


def _parse_csv(
    file_path: str,
    column_map: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse a .csv file."""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        raw_headers_row = next(reader, None)
        if not raw_headers_row:
            return [], []

        raw_headers = [str(h).strip() for h in raw_headers_row]
        normalized_headers = [normalize_header(h) for h in raw_headers]
        field_map = _build_field_map(normalized_headers, column_map)

        rows: List[Dict[str, Any]] = []
        for csv_row in reader:
            if not csv_row or all(not v.strip() for v in csv_row):
                continue
            row_dict: Dict[str, Any] = {}
            for idx, val in enumerate(csv_row):
                if idx < len(normalized_headers):
                    target = field_map.get(normalized_headers[idx])
                    if target:
                        row_dict[target] = _clean_value(val)
            if any(v for v in row_dict.values()):
                rows.append(row_dict)

    return rows, raw_headers


def _build_field_map(
    normalized_headers: List[str],
    column_map: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Build map from normalized header name → target field name."""
    if column_map:
        # column_map: {target_field: source_header_normalized}
        reverse = {normalize_header(v): k for k, v in column_map.items()}
        return reverse
    # Identity map: use normalized headers as-is
    return {h: h for h in normalized_headers if h}


def _clean_value(val: Any) -> Any:
    """Clean cell value: strip strings, handle None."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    return val


def compute_file_checksum(file_path: str) -> str:
    """Compute SHA-256 checksum of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def safe_decimal(val: Any) -> Optional[Decimal]:
    """Safely convert a value to Decimal."""
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def safe_date(val: Any):
    """Safely convert a value to datetime.date. Returns None on failure."""
    import datetime
    if val is None or val == "":
        return None
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    try:
        return datetime.date.fromisoformat(str(val).strip()[:10])
    except (ValueError, TypeError):
        return None


def safe_bool(val: Any, default: bool = True) -> bool:
    """Safely convert a value to bool."""
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y", "active")


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching — lowercase, strip, collapse whitespace."""
    if not text:
        return ""
    t = str(text).strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t
