"""Import validators — validate required columns and row-level data."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

# Required columns per batch type
REQUIRED_COLUMNS: Dict[str, Set[str]] = {
    "VENDOR": {"vendor_code", "vendor_name"},
    "ITEM": {"item_code", "item_name"},
    "TAX": {"tax_code"},
    "COST_CENTER": {"cost_center_code", "cost_center_name"},
    "OPEN_PO": {"po_number"},
    "GRN": {"grn_number", "po_number"},
}


def validate_columns(
    batch_type: str,
    row_keys: Set[str],
) -> Tuple[bool, List[str]]:
    """Validate that required columns are present.

    Returns (is_valid, list_of_missing_columns).
    """
    required = REQUIRED_COLUMNS.get(batch_type, set())
    missing = required - row_keys
    return len(missing) == 0, sorted(missing)


def validate_row(
    batch_type: str,
    row: Dict[str, Any],
    row_index: int,
) -> Tuple[bool, List[str]]:
    """Validate a single row for required fields being non-empty.

    Returns (is_valid, list_of_error_messages).
    """
    errors: List[str] = []
    required = REQUIRED_COLUMNS.get(batch_type, set())

    for col in required:
        val = row.get(col)
        if val is None or (isinstance(val, str) and not val.strip()):
            errors.append(f"Row {row_index}: missing required field '{col}'")

    return len(errors) == 0, errors
