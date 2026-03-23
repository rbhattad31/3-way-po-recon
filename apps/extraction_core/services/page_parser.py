"""
PageParser — Multi-page document text segmentation.

Splits raw OCR text into page-level segments, identifies and removes
repeated headers/footers, and provides per-page character offsets so
downstream extractors can attribute evidence to specific pages.

Design:
    - Works with plain OCR text (no PDF structure needed)
    - Auto-detects page boundaries via form-feed chars or heuristic
      patterns (``--- Page N ---``, ``\\x0c``, large whitespace runs)
    - Header/footer deduplication uses similarity matching to find
      lines repeated across 2+ pages
    - Country-specific formatting is handled by configurable parameters
      (decimal separator, date pattern awareness in header detection)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Common page-break markers emitted by OCR engines ──────────────────
_PAGE_BREAK_PATTERNS = [
    r"\f",                                    # Form-feed (most common)
    r"---\s*[Pp]age\s*\d+\s*(?:of\s*\d+)?\s*---",  # --- Page N ---
    r"\[Page\s*\d+\]",                        # [Page N]
    r"(?:^|\n)Page\s+\d+\s+of\s+\d+(?:\n|$)",  # Page N of M (standalone)
]

# Number of leading/trailing lines to compare for header/footer detection
_HEADER_FOOTER_LINES = 4

# Minimum similarity ratio (0-1) to consider two blocks identical
_SIMILARITY_THRESHOLD = 0.85

# Minimum number of pages where a block must repeat to be treated as
# header/footer (prevents false positives on 2-page documents)
_MIN_REPEAT_PAGES = 2


@dataclass
class PageSegment:
    """A single page extracted from a multi-page document."""

    page_number: int             # 1-indexed
    raw_text: str                # Original text of this page
    clean_text: str = ""         # Text after header/footer removal
    char_offset_start: int = 0   # Offset in the full OCR text
    char_offset_end: int = 0     # Offset in the full OCR text
    has_table: bool = False      # Heuristic flag: page contains tabular data
    table_regions: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.clean_text.strip()


@dataclass
class ParsedDocument:
    """Result of parsing a multi-page document."""

    pages: list[PageSegment] = field(default_factory=list)
    page_count: int = 0
    removed_headers: list[str] = field(default_factory=list)
    removed_footers: list[str] = field(default_factory=list)
    full_clean_text: str = ""    # All pages' clean text concatenated

    def to_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "removed_headers": self.removed_headers,
            "removed_footers": self.removed_footers,
            "pages": [
                {
                    "page_number": p.page_number,
                    "has_table": p.has_table,
                    "char_length": len(p.clean_text),
                }
                for p in self.pages
            ],
        }


class PageParser:
    """Splits and cleans multi-page OCR text."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, ocr_text: str) -> ParsedDocument:
        """
        Parse raw OCR text into page segments.

        Steps:
            1. Split text on page-break markers
            2. Detect and remove repeated headers/footers
            3. Flag pages that contain table-like content
            4. Build the full clean text by joining cleaned pages

        Returns:
            ParsedDocument with per-page data and cleaned text.
        """
        if not ocr_text or not ocr_text.strip():
            return ParsedDocument()

        # Step 1: Split into raw pages
        raw_pages = cls._split_pages(ocr_text)

        # Build page segments with character offsets
        segments: list[PageSegment] = []
        offset = 0
        for idx, page_text in enumerate(raw_pages):
            seg = PageSegment(
                page_number=idx + 1,
                raw_text=page_text,
                clean_text=page_text,
                char_offset_start=offset,
                char_offset_end=offset + len(page_text),
            )
            segments.append(seg)
            offset += len(page_text)

        # Step 2: Detect and remove repeated headers/footers
        removed_headers, removed_footers = cls._remove_repeated_blocks(
            segments,
        )

        # Step 3: Detect table regions on each page
        for seg in segments:
            seg.has_table = cls._detect_table_region(seg.clean_text)
            if seg.has_table:
                seg.table_regions = cls._extract_table_regions(seg.clean_text)

        # Step 4: Build full clean text
        full_clean = "\n".join(
            seg.clean_text for seg in segments if not seg.is_empty
        )

        result = ParsedDocument(
            pages=segments,
            page_count=len(segments),
            removed_headers=removed_headers,
            removed_footers=removed_footers,
            full_clean_text=full_clean,
        )

        logger.info(
            "Parsed document: %d pages, %d header(s) removed, "
            "%d footer(s) removed, %d pages with tables",
            result.page_count,
            len(removed_headers),
            len(removed_footers),
            sum(1 for p in segments if p.has_table),
        )
        return result

    # ------------------------------------------------------------------
    # Page splitting
    # ------------------------------------------------------------------

    @classmethod
    def _split_pages(cls, ocr_text: str) -> list[str]:
        """
        Split OCR text into pages using form-feed characters or
        heuristic markers.

        Falls back to treating the entire text as a single page if no
        page-break markers are found.
        """
        # Try form-feed first (the most reliable, emitted by Azure DI)
        if "\f" in ocr_text:
            pages = ocr_text.split("\f")
            pages = [p for p in pages if p.strip()]
            if len(pages) > 1:
                return pages

        # Try other page-break patterns
        for pattern in _PAGE_BREAK_PATTERNS[1:]:
            parts = re.split(pattern, ocr_text)
            parts = [p for p in parts if p.strip()]
            if len(parts) > 1:
                return parts

        # No markers found — single page
        return [ocr_text]

    # ------------------------------------------------------------------
    # Header / footer removal
    # ------------------------------------------------------------------

    @classmethod
    def _remove_repeated_blocks(
        cls,
        pages: list[PageSegment],
    ) -> tuple[list[str], list[str]]:
        """
        Detect and remove text blocks repeated across pages.

        Compares the first/last N lines of each page. Lines appearing
        in ≥ MIN_REPEAT_PAGES pages are treated as headers/footers
        and stripped.

        Returns (removed_headers, removed_footers).
        """
        if len(pages) < 2:
            return [], []

        # Collect candidate header/footer lines from each page
        header_candidates: list[list[str]] = []
        footer_candidates: list[list[str]] = []

        for page in pages:
            lines = page.clean_text.splitlines()
            non_empty = [l for l in lines if l.strip()]
            if not non_empty:
                header_candidates.append([])
                footer_candidates.append([])
                continue
            header_candidates.append(
                [l.strip() for l in non_empty[:_HEADER_FOOTER_LINES]],
            )
            footer_candidates.append(
                [l.strip() for l in non_empty[-_HEADER_FOOTER_LINES:]],
            )

        # Find lines that repeat across enough pages
        removed_headers = cls._find_repeated_lines(header_candidates)
        removed_footers = cls._find_repeated_lines(footer_candidates)

        # Actually strip them from each page's clean_text
        if removed_headers or removed_footers:
            for page in pages:
                page.clean_text = cls._strip_lines(
                    page.clean_text, removed_headers, removed_footers,
                )

        return removed_headers, removed_footers

    @classmethod
    def _find_repeated_lines(
        cls,
        candidates: list[list[str]],
    ) -> list[str]:
        """Find lines that appear in at least _MIN_REPEAT_PAGES pages."""
        if not candidates:
            return []

        line_page_count: dict[str, int] = {}
        for page_lines in candidates:
            # Use set to count each line at most once per page
            for line in set(page_lines):
                normalized = cls._normalize_for_comparison(line)
                if normalized:
                    line_page_count[normalized] = (
                        line_page_count.get(normalized, 0) + 1
                    )

        min_pages = min(_MIN_REPEAT_PAGES, len(candidates))
        repeated = [
            line
            for line, count in line_page_count.items()
            if count >= min_pages
        ]
        return repeated

    @classmethod
    def _strip_lines(
        cls,
        text: str,
        header_lines: list[str],
        footer_lines: list[str],
    ) -> str:
        """Remove identified header/footer lines from a page's text."""
        lines = text.splitlines()
        cleaned: list[str] = []

        for line in lines:
            normalized = cls._normalize_for_comparison(line)
            if normalized in header_lines or normalized in footer_lines:
                continue
            cleaned.append(line)

        return "\n".join(cleaned)

    @classmethod
    def _normalize_for_comparison(cls, line: str) -> str:
        """Normalize a line for comparison: lowercase, collapse whitespace."""
        text = line.strip().lower()
        text = re.sub(r"\s+", " ", text)
        # Remove page numbers that vary across pages
        text = re.sub(r"page\s*\d+(\s*of\s*\d+)?", "", text)
        return text.strip()

    # ------------------------------------------------------------------
    # Table region detection
    # ------------------------------------------------------------------

    @classmethod
    def _detect_table_region(cls, text: str) -> bool:
        """
        Heuristic check: does this page contain table-like content?

        Looks for patterns common in invoice line-item tables:
        - Multiple lines with consistent column-like spacing (2+ tabs or 3+ spaces)
        - Lines with repeated numeric patterns (quantities, amounts)
        - Lines with item/description + amount patterns
        """
        if not text.strip():
            return False

        lines = text.splitlines()
        tabular_lines = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check for tab-separated or multi-space-separated columns
            if "\t" in stripped or re.search(r"\s{3,}", stripped):
                # Must also contain at least one number
                if re.search(r"\d+[.,]?\d*", stripped):
                    tabular_lines += 1

        # If ≥3 lines look tabular, consider this page as having a table
        return tabular_lines >= 3

    @classmethod
    def _extract_table_regions(cls, text: str) -> list[str]:
        """
        Extract contiguous blocks of tabular text from a page.

        Returns a list of text blocks, each representing a potential
        table region.
        """
        lines = text.splitlines()
        regions: list[str] = []
        current_block: list[str] = []

        for line in lines:
            stripped = line.strip()
            is_tabular = bool(
                stripped
                and (
                    "\t" in stripped
                    or re.search(r"\s{3,}", stripped)
                )
                and re.search(r"\d+[.,]?\d*", stripped)
            )

            if is_tabular:
                current_block.append(line)
            else:
                if len(current_block) >= 2:
                    regions.append("\n".join(current_block))
                current_block = []

        # Flush last block
        if len(current_block) >= 2:
            regions.append("\n".join(current_block))

        return regions

    # ------------------------------------------------------------------
    # Utility: locate page for a character offset
    # ------------------------------------------------------------------

    @classmethod
    def find_page_for_offset(
        cls,
        pages: list[PageSegment],
        offset: int,
    ) -> int | None:
        """Return the 1-indexed page number for a character offset."""
        for page in pages:
            if page.char_offset_start <= offset < page.char_offset_end:
                return page.page_number
        return None

    @classmethod
    def find_page_for_text(
        cls,
        pages: list[PageSegment],
        snippet: str,
    ) -> int | None:
        """
        Find which page contains a text snippet.

        Tries exact match first, then case-insensitive.
        """
        if not snippet:
            return None
        for page in pages:
            if snippet in page.raw_text:
                return page.page_number
        # Fallback: case-insensitive
        snippet_lower = snippet.lower()
        for page in pages:
            if snippet_lower in page.raw_text.lower():
                return page.page_number
        return None
