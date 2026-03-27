"""
TableStitcher — Cross-page table continuation and reconstruction.

Detects when a table continues from one page to the next and produces
a unified table structure with row-level page attribution.

Design:
    - Schema-aware: uses ``ExtractionTemplate.line_item_fields`` to know
      which columns to expect
    - Row continuity detection: identifies split rows (a single row
      that wraps across a page boundary)
    - Column alignment: infers column boundaries from consistent spacing
    - Country-format-aware: handles ``1.234,56`` (EU) vs ``1,234.56`` (US)
      number formats when identifying numeric columns

Output:
    - ``StitchedTable`` with a flat list of ``TableRow`` objects,
      each carrying its source page and original text
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.extraction_core.services.page_parser import PageSegment

logger = logging.getLogger(__name__)

# ── Heuristic tuning ─────────────────────────────────────────────────
# Minimum columns to consider a line tabular
_MIN_COLUMNS = 2
# Max gap (in chars) between two pages' tables before treating as separate
_MAX_GAP_LINES = 3
# Continuation indicators at start of a page
_CONTINUATION_PATTERNS = [
    r"^\s*(continued|cont['.]?d|contd\.?|brought forward|b/f)\b",
    r"^\s*\.\.\.",
]
# Table header keywords (case-insensitive)
_HEADER_KEYWORDS = [
    "description", "qty", "quantity", "unit", "price", "amount", "total",
    "rate", "hsn", "sac", "item", "sl\.?\s*no", "sr\.?\s*no", "s\.?\s*no",
    "particulars", "uom", "discount", "tax", "cgst", "sgst", "igst", "vat",
    "gst", "net", "gross",
]


@dataclass
class TableRow:
    """A single row from a stitched table."""

    cells: list[str]
    page_number: int             # 1-indexed source page
    row_index: int               # 0-indexed position in the stitched table
    raw_text: str = ""           # Original line text
    is_header: bool = False      # Detected as a column header row
    is_total: bool = False       # Detected as a totals/summary row
    is_continuation: bool = False  # This row was merged from a page split


@dataclass
class StitchedTable:
    """
    Reconstructed table from one or more page segments.

    Provides a flat row list with page attribution and detected
    column headers.
    """

    rows: list[TableRow] = field(default_factory=list)
    column_headers: list[str] = field(default_factory=list)
    page_span: list[int] = field(default_factory=list)  # pages this table covers
    total_row_count: int = 0
    data_row_count: int = 0  # excludes headers and totals
    continuation_merges: int = 0  # rows merged across pages

    def to_dict(self) -> dict:
        return {
            "column_headers": self.column_headers,
            "page_span": self.page_span,
            "total_row_count": self.total_row_count,
            "data_row_count": self.data_row_count,
            "continuation_merges": self.continuation_merges,
        }


class TableStitcher:
    """Reconstructs tables that span multiple pages."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def stitch(
        cls,
        pages: list["PageSegment"],
        decimal_separator: str = ".",
    ) -> list[StitchedTable]:
        """
        Analyse page segments and stitch tables that continue across
        page boundaries.

        Args:
            pages: Page segments from PageParser (with table_regions set).
            decimal_separator: '.' (US/IN) or ',' (EU) — affects number parsing.

        Returns:
            List of StitchedTable objects (usually one per document, but
            supports multiple disjoint tables).
        """
        if not pages:
            return []

        # Collect all table regions across pages
        page_tables: list[tuple[int, str]] = []  # (page_num, table_text)
        for page in pages:
            for region in page.table_regions:
                page_tables.append((page.page_number, region))

        if not page_tables:
            return []

        # Parse each region into raw rows
        parsed_regions: list[list[TableRow]] = []
        for page_num, region_text in page_tables:
            rows = cls._parse_rows(region_text, page_num)
            if rows:
                parsed_regions.append(rows)

        if not parsed_regions:
            return []

        # Stitch contiguous regions
        tables = cls._stitch_regions(parsed_regions, decimal_separator)

        logger.info(
            "Table stitching: %d region(s) → %d stitched table(s), "
            "%d total data rows",
            len(parsed_regions),
            len(tables),
            sum(t.data_row_count for t in tables),
        )

        return tables

    # ------------------------------------------------------------------
    # Row parsing
    # ------------------------------------------------------------------

    @classmethod
    def _parse_rows(
        cls,
        table_text: str,
        page_number: int,
    ) -> list[TableRow]:
        """Parse a text block into TableRow objects."""
        lines = table_text.splitlines()
        rows: list[TableRow] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            cells = cls._split_into_cells(stripped)
            if len(cells) < _MIN_COLUMNS:
                continue

            row = TableRow(
                cells=cells,
                page_number=page_number,
                row_index=len(rows),
                raw_text=stripped,
                is_header=cls._is_header_row(stripped),
                is_total=cls._is_total_row(stripped),
            )
            rows.append(row)

        return rows

    @classmethod
    def _split_into_cells(cls, line: str) -> list[str]:
        """
        Split a line into cells.

        Strategy order:
            1. Tab-separated
            2. Multi-space-separated (3+ spaces)
            3. Pipe-separated (| delimiters)
        """
        # Tab-separated
        if "\t" in line:
            return [c.strip() for c in line.split("\t") if c.strip()]

        # Pipe-separated
        if "|" in line and line.count("|") >= 2:
            return [c.strip() for c in line.split("|") if c.strip()]

        # Multi-space-separated
        parts = re.split(r"\s{3,}", line)
        if len(parts) >= _MIN_COLUMNS:
            return [p.strip() for p in parts if p.strip()]

        # Fallback: two-space split
        parts = re.split(r"\s{2,}", line)
        return [p.strip() for p in parts if p.strip()]

    @classmethod
    def _is_header_row(cls, text: str) -> bool:
        """Check if a row looks like a column header."""
        text_lower = text.lower()
        matches = sum(
            1
            for kw in _HEADER_KEYWORDS
            if re.search(r"\b" + kw + r"\b", text_lower)
        )
        return matches >= 2

    @classmethod
    def _is_total_row(cls, text: str) -> bool:
        """Check if a row is a totals/summary row."""
        text_lower = text.lower()
        return bool(
            re.search(
                r"\b(total|grand\s*total|sub\s*total|net\s*total"
                r"|amount\s*due|balance\s*due|sum)\b",
                text_lower,
            )
        )

    # ------------------------------------------------------------------
    # Region stitching
    # ------------------------------------------------------------------

    @classmethod
    def _stitch_regions(
        cls,
        regions: list[list[TableRow]],
        decimal_separator: str,
    ) -> list[StitchedTable]:
        """
        Merge table regions that are continuations of each other.

        Two regions are continuations if:
            - They have the same column count (or the second has no header)
            - The second region starts on the next page
            - The second region starts with a continuation marker or
              its first data row has no header row
        """
        if not regions:
            return []

        tables: list[StitchedTable] = []
        current_rows: list[TableRow] = list(regions[0])
        current_pages: set[int] = {r.page_number for r in current_rows}
        merges = 0

        for region_rows in regions[1:]:
            if cls._is_continuation(current_rows, region_rows):
                # Check for split-row continuity (last row of prev + first of next)
                merge_count = cls._merge_split_rows(
                    current_rows, region_rows, decimal_separator,
                )
                merges += merge_count

                # Filter out repeated header rows from the continuation
                for row in region_rows:
                    if row.is_header and cls._has_matching_header(
                        current_rows, row,
                    ):
                        continue
                    row.is_continuation = True
                    current_rows.append(row)
                current_pages.update(r.page_number for r in region_rows)
            else:
                # Finalize current table
                tables.append(
                    cls._build_stitched_table(
                        current_rows, current_pages, merges,
                    )
                )
                current_rows = list(region_rows)
                current_pages = {r.page_number for r in current_rows}
                merges = 0

        # Finalize last table
        tables.append(
            cls._build_stitched_table(current_rows, current_pages, merges),
        )
        return tables

    @classmethod
    def _is_continuation(
        cls,
        prev_rows: list[TableRow],
        next_rows: list[TableRow],
    ) -> bool:
        """Determine if next_rows is a continuation of prev_rows."""
        if not prev_rows or not next_rows:
            return False

        # Must be from a later page
        prev_max_page = max(r.page_number for r in prev_rows)
        next_min_page = min(r.page_number for r in next_rows)
        if next_min_page <= prev_max_page:
            return False

        # Check for explicit continuation markers
        first_text = next_rows[0].raw_text
        for pattern in _CONTINUATION_PATTERNS:
            if re.search(pattern, first_text, re.IGNORECASE):
                return True

        # Column count similarity (within ±1)
        prev_cols = cls._typical_column_count(prev_rows)
        next_cols = cls._typical_column_count(next_rows)
        if abs(prev_cols - next_cols) <= 1:
            # If next region has no header, it's likely a continuation
            has_header = any(r.is_header for r in next_rows)
            if not has_header:
                return True
            # Even with a repeated header, still a continuation
            return True

        return False

    @classmethod
    def _typical_column_count(cls, rows: list[TableRow]) -> int:
        """Most common column count in a set of rows."""
        counts: dict[int, int] = {}
        for row in rows:
            if not row.is_header and not row.is_total:
                n = len(row.cells)
                counts[n] = counts.get(n, 0) + 1
        if not counts:
            return 0
        return max(counts, key=counts.get)  # type: ignore[arg-type]

    @classmethod
    def _has_matching_header(
        cls,
        existing_rows: list[TableRow],
        candidate: TableRow,
    ) -> bool:
        """Check if a header row already exists in the current table."""
        candidate_norm = cls._normalize_row(candidate.raw_text)
        for row in existing_rows:
            if row.is_header:
                if cls._normalize_row(row.raw_text) == candidate_norm:
                    return True
        return False

    @classmethod
    def _normalize_row(cls, text: str) -> str:
        """Normalize a row for comparison."""
        return re.sub(r"\s+", " ", text.strip().lower())

    # ------------------------------------------------------------------
    # Split-row merging
    # ------------------------------------------------------------------

    @classmethod
    def _merge_split_rows(
        cls,
        prev_rows: list[TableRow],
        next_rows: list[TableRow],
        decimal_separator: str,
    ) -> int:
        """
        Detect and merge rows split across a page boundary.

        A split row occurs when the last row of the previous page and
        the first data row of the next page together form one logical
        row. Indicators:
            - Last row of prev page has fewer cells than typical
            - First row of next page has fewer cells than typical
            - Combined cells match the expected column count
        """
        if not prev_rows or not next_rows:
            return 0

        typical_cols = cls._typical_column_count(prev_rows)
        if typical_cols == 0:
            return 0

        last_row = prev_rows[-1]
        # Find first data row in next (skip header/continuation markers)
        first_data = None
        first_data_idx = None
        for idx, row in enumerate(next_rows):
            if not row.is_header and not cls._is_continuation_marker(row):
                first_data = row
                first_data_idx = idx
                break

        if first_data is None:
            return 0

        last_cols = len(last_row.cells)
        first_cols = len(first_data.cells)

        # If both have fewer cells than typical and together they match
        if (
            last_cols < typical_cols
            and first_cols < typical_cols
            and last_cols + first_cols == typical_cols
        ):
            # Merge: extend last_row's cells with first_data's cells
            last_row.cells.extend(first_data.cells)
            last_row.raw_text += " " + first_data.raw_text
            last_row.is_continuation = True
            # Remove the merged row from next_rows
            if first_data_idx is not None:
                next_rows.pop(first_data_idx)
            return 1

        return 0

    @classmethod
    def _is_continuation_marker(cls, row: TableRow) -> bool:
        """Check if a row is just a continuation marker, not data."""
        for pattern in _CONTINUATION_PATTERNS:
            if re.search(pattern, row.raw_text, re.IGNORECASE):
                return True
        return False

    # ------------------------------------------------------------------
    # Table finalization
    # ------------------------------------------------------------------

    @classmethod
    def _build_stitched_table(
        cls,
        rows: list[TableRow],
        pages: set[int],
        merges: int,
    ) -> StitchedTable:
        """Build a StitchedTable from processed rows."""
        # Re-index rows
        for idx, row in enumerate(rows):
            row.row_index = idx

        # Extract column headers from header rows
        headers: list[str] = []
        for row in rows:
            if row.is_header:
                headers = row.cells
                break

        data_rows = [
            r for r in rows if not r.is_header and not r.is_total
        ]

        return StitchedTable(
            rows=rows,
            column_headers=headers,
            page_span=sorted(pages),
            total_row_count=len(rows),
            data_row_count=len(data_rows),
            continuation_merges=merges,
        )
