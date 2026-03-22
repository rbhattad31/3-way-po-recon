"""
LineItemExtractor — Schema-driven line-item extraction from stitched tables.

Takes a ``StitchedTable`` and an ``ExtractionTemplate`` and produces a
list of line-item dicts (each a ``dict[str, FieldResult]``) consistent
with the existing extraction pipeline output.

Design:
    - Schema-driven: only extracts fields defined in
      ``template.line_item_fields``
    - Column mapping: maps detected table column headers to schema field
      keys using aliases + fuzzy matching
    - Country-format-aware: delegates number parsing to the locale
      settings (decimal/thousands separators)
    - Evidence-rich: each extracted cell carries a ``FieldEvidence``
      with page number, table row index, and source snippet
    - Totals consistency: validates that sum of line amounts matches
      detected total rows
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.extraction_core.services.extraction_service import (
        ExtractionTemplate,
        FieldSpec,
    )
    from apps.extraction_core.services.table_stitcher import (
        StitchedTable,
        TableRow,
    )

logger = logging.getLogger(__name__)

# Minimum similarity to accept a column→field mapping
_COLUMN_MATCH_THRESHOLD = 0.55


@dataclass
class LineItemExtractionResult:
    """Result of line-item extraction from stitched tables."""

    line_items: list[dict] = field(default_factory=list)
    # FieldResult dicts — one dict per line item row
    total_rows_detected: int = 0
    totals_consistent: bool = True
    totals_discrepancy: str = ""
    column_mapping: dict[str, str] = field(default_factory=dict)
    # column_header → field_key

    def to_dict(self) -> dict:
        return {
            "line_item_count": len(self.line_items),
            "total_rows_detected": self.total_rows_detected,
            "totals_consistent": self.totals_consistent,
            "totals_discrepancy": self.totals_discrepancy,
            "column_mapping": self.column_mapping,
        }


class LineItemExtractor:
    """Extracts line items from stitched tables using schema field specs."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def extract(
        cls,
        tables: list["StitchedTable"],
        template: "ExtractionTemplate",
        decimal_separator: str = ".",
    ) -> LineItemExtractionResult:
        """
        Extract line items from stitched tables.

        Args:
            tables: Stitched tables from TableStitcher.
            template: Extraction template with line_item_fields.
            decimal_separator: Locale-appropriate decimal separator.

        Returns:
            LineItemExtractionResult with extracted line items.
        """
        from apps.extraction_core.services.extraction_service import (
            FieldEvidence,
            FieldResult,
        )

        if not tables or not template.line_item_fields:
            return LineItemExtractionResult()

        all_items: list[dict[str, FieldResult]] = []
        total_rows: list["TableRow"] = []
        column_mapping: dict[str, str] = {}

        for table in tables:
            # Map columns to field specs
            mapping = cls._map_columns(
                table.column_headers, template.line_item_fields,
            )
            column_mapping.update(mapping)

            if not mapping:
                logger.warning(
                    "Could not map any columns for table spanning pages %s",
                    table.page_span,
                )
                continue

            # Extract data rows
            for row in table.rows:
                if row.is_header:
                    continue
                if row.is_total:
                    total_rows.append(row)
                    continue

                item = cls._extract_row(
                    row=row,
                    mapping=mapping,
                    column_headers=table.column_headers,
                    specs={s.field_key: s for s in template.line_item_fields},
                    decimal_separator=decimal_separator,
                )
                if item:
                    all_items.append(item)

        result = LineItemExtractionResult(
            line_items=all_items,
            total_rows_detected=len(total_rows),
            column_mapping=column_mapping,
        )

        # Validate totals consistency
        if total_rows and all_items:
            cls._validate_totals(
                result, all_items, total_rows,
                template.line_item_fields, decimal_separator,
            )

        logger.info(
            "Line-item extraction: %d items from %d table(s), "
            "totals_consistent=%s",
            len(all_items),
            len(tables),
            result.totals_consistent,
        )

        return result

    # ------------------------------------------------------------------
    # Column mapping
    # ------------------------------------------------------------------

    @classmethod
    def _map_columns(
        cls,
        column_headers: list[str],
        field_specs: list["FieldSpec"],
    ) -> dict[str, str]:
        """
        Map detected column headers to schema field keys.

        Uses exact alias matching first, then fuzzy string similarity.

        Returns:
            dict mapping column_header → field_key
        """
        mapping: dict[str, str] = {}
        used_fields: set[str] = set()

        if not column_headers or not field_specs:
            return mapping

        # Phase 1: Exact alias matching
        for col_idx, col_header in enumerate(column_headers):
            col_norm = col_header.strip().lower()
            for spec in field_specs:
                if spec.field_key in used_fields:
                    continue
                # Check display_name
                if col_norm == spec.display_name.lower():
                    mapping[col_header] = spec.field_key
                    used_fields.add(spec.field_key)
                    break
                # Check field_key
                if col_norm == spec.field_key.replace("_", " "):
                    mapping[col_header] = spec.field_key
                    used_fields.add(spec.field_key)
                    break
                # Check aliases
                for alias in spec.aliases:
                    if col_norm == alias.lower():
                        mapping[col_header] = spec.field_key
                        used_fields.add(spec.field_key)
                        break
                if col_header in mapping:
                    break

        # Phase 2: Fuzzy matching for unmapped columns
        for col_header in column_headers:
            if col_header in mapping:
                continue
            col_norm = col_header.strip().lower()
            best_score = 0.0
            best_field: str | None = None

            for spec in field_specs:
                if spec.field_key in used_fields:
                    continue

                # Compare against display_name and field_key
                candidates = [
                    spec.display_name.lower(),
                    spec.field_key.replace("_", " "),
                ] + [a.lower() for a in spec.aliases]

                for candidate in candidates:
                    score = SequenceMatcher(
                        None, col_norm, candidate,
                    ).ratio()
                    if score > best_score:
                        best_score = score
                        best_field = spec.field_key

            if best_field and best_score >= _COLUMN_MATCH_THRESHOLD:
                mapping[col_header] = best_field
                used_fields.add(best_field)

        return mapping

    # ------------------------------------------------------------------
    # Row extraction
    # ------------------------------------------------------------------

    @classmethod
    def _extract_row(
        cls,
        row: "TableRow",
        mapping: dict[str, str],
        column_headers: list[str],
        specs: dict[str, "FieldSpec"],
        decimal_separator: str,
    ) -> dict | None:
        """
        Extract field values from a single table row.

        Returns a dict[field_key → FieldResult] or None if the row
        has no usable data.
        """
        from apps.extraction_core.services.extraction_service import (
            FieldEvidence,
            FieldResult,
        )

        if not row.cells:
            return None

        item: dict[str, FieldResult] = {}
        any_extracted = False

        for col_idx, col_header in enumerate(column_headers):
            field_key = mapping.get(col_header)
            if not field_key:
                continue

            spec = specs.get(field_key)
            if not spec:
                continue

            # Get cell value (handle misaligned columns gracefully)
            if col_idx < len(row.cells):
                raw_value = row.cells[col_idx].strip()
            else:
                raw_value = ""

            if not raw_value:
                item[field_key] = FieldResult(
                    field_key=field_key,
                    display_name=spec.display_name,
                    category="LINE_ITEM",
                    data_type=spec.data_type,
                    is_mandatory=spec.is_mandatory,
                    method="DETERMINISTIC",
                    extracted=False,
                    confidence=0.0,
                )
                continue

            # Build evidence
            evidence = FieldEvidence(
                source_snippet=row.raw_text[:200],
                page_number=row.page_number,
                table_row_index=row.row_index,
                extraction_method="DETERMINISTIC",
            )

            item[field_key] = FieldResult(
                field_key=field_key,
                display_name=spec.display_name,
                category="LINE_ITEM",
                data_type=spec.data_type,
                raw_value=raw_value,
                is_mandatory=spec.is_mandatory,
                method="DETERMINISTIC",
                confidence=0.80,  # table cell extraction confidence
                source_snippet=row.raw_text[:200],
                extracted=True,
                evidence=evidence,
            )
            any_extracted = True

        return item if any_extracted else None

    # ------------------------------------------------------------------
    # Totals validation
    # ------------------------------------------------------------------

    @classmethod
    def _validate_totals(
        cls,
        result: LineItemExtractionResult,
        items: list[dict],
        total_rows: list["TableRow"],
        field_specs: list["FieldSpec"],
        decimal_separator: str,
    ) -> None:
        """
        Compare the sum of line-item amounts against detected total rows.

        Sets ``result.totals_consistent`` and ``result.totals_discrepancy``.
        """
        # Find the amount/total field in line-item specs
        amount_keys = []
        for spec in field_specs:
            key_lower = spec.field_key.lower()
            if any(
                kw in key_lower
                for kw in ("amount", "total", "net_amount", "line_total")
            ):
                amount_keys.append(spec.field_key)

        if not amount_keys:
            return

        # Sum line-item amounts
        for amount_key in amount_keys:
            line_sum = 0.0
            count = 0
            for item in items:
                fr = item.get(amount_key)
                if fr and fr.extracted:
                    parsed = cls._parse_number(
                        fr.raw_value, decimal_separator,
                    )
                    if parsed is not None:
                        line_sum += parsed
                        count += 1

            if count == 0:
                continue

            # Try to find a matching total in total rows
            for total_row in total_rows:
                for cell in total_row.cells:
                    total_val = cls._parse_number(cell, decimal_separator)
                    if total_val is not None and total_val > 0:
                        # Check if line sum matches the total (within 1% tolerance)
                        if total_val != 0:
                            diff_pct = abs(line_sum - total_val) / total_val
                        else:
                            diff_pct = 1.0 if line_sum != 0 else 0.0

                        if diff_pct <= 0.01:
                            result.totals_consistent = True
                            return
                        elif diff_pct <= 0.05:
                            result.totals_consistent = False
                            result.totals_discrepancy = (
                                f"Line sum {line_sum:.2f} vs total "
                                f"{total_val:.2f} for '{amount_key}' "
                                f"(diff: {diff_pct:.1%})"
                            )
                            return

        # No total row matched — cannot validate, assume consistent
        result.totals_consistent = True

    @classmethod
    def _parse_number(
        cls,
        text: str,
        decimal_separator: str = ".",
    ) -> float | None:
        """
        Parse a number string respecting locale-specific formatting.

        Handles:
            - ``1,234.56`` (US/UK/IN)
            - ``1.234,56`` (EU)
            - ``1234.56`` (no separators)
            - Surrounding whitespace, currency symbols, minus signs
        """
        if not text:
            return None

        # Strip currency symbols and whitespace
        cleaned = re.sub(r"[^\d.,\-]", "", text.strip())
        if not cleaned:
            return None

        if decimal_separator == ",":
            # EU format: thousands='.', decimal=','
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            # US format: thousands=',', decimal='.'
            cleaned = cleaned.replace(",", "")

        try:
            return float(cleaned)
        except ValueError:
            return None
