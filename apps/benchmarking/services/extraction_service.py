"""
Quotation PDF extraction service.
Extracts text from uploaded PDF using pdfplumber (pure-Python, no external dependencies).
Falls back to raw binary scan if pdfplumber is unavailable.
Then parses line items using pattern matching.
"""
import logging
import re

logger = logging.getLogger(__name__)


class ExtractionService:
    """Extract raw text and parse line items from a quotation PDF."""

    # Minimum columns expected in a line-item row
    MIN_COLS = 3

    @classmethod
    def extract_text_from_pdf(cls, file_path: str) -> str:
        """Return plain text extracted from the PDF at file_path."""
        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
            return "\n".join(pages)
        except ImportError:
            logger.warning("pdfplumber not installed -- falling back to raw text scan")
            return cls._raw_text_fallback(file_path)
        except Exception as exc:
            logger.error("PDF extraction failed for %s: %s", file_path, exc)
            return ""

    @classmethod
    def _raw_text_fallback(cls, file_path: str) -> str:
        """Very basic fallback: read bytes and decode printable ASCII."""
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            text = raw.decode("latin-1", errors="ignore")
            # Strip non-printable chars
            printable = re.sub(r"[^\x20-\x7e\n\r]", " ", text)
            return printable
        except Exception as exc:
            logger.error("Raw text fallback failed: %s", exc)
            return ""

    @classmethod
    def parse_line_items(cls, raw_text: str) -> list:
        """
        Heuristic parser: identify table rows from extracted PDF text.
        Returns list of dicts with keys:
          line_number, description, uom, quantity, unit_rate, amount, extraction_confidence
        """
        if not raw_text:
            return []

        lines = [l.rstrip() for l in raw_text.splitlines()]
        items = []
        line_num = 0

        # Pattern: optional number, description text, optional UOM, qty, rate, amount
        # Example row: "1  Supply of VRF Outdoor Unit 10TR  Nos  1  85000  85000"
        item_pattern = re.compile(
            r"^(\d{1,4})\s+"                            # line number
            r"(.+?)\s+"                                  # description (greedy to last group)
            r"([A-Za-z/]+)?\s*"                          # UOM (optional)
            r"(\d+(?:\.\d+)?)\s+"                        # quantity
            r"([\d,]+(?:\.\d{1,2})?)\s+"                # unit rate
            r"([\d,]+(?:\.\d{1,2})?)$"                  # total amount
        )

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            m = item_pattern.match(line)
            if m:
                line_num += 1
                try:
                    qty = float(m.group(4).replace(",", ""))
                    rate = float(m.group(5).replace(",", ""))
                    amount = float(m.group(6).replace(",", ""))
                except ValueError:
                    continue

                items.append({
                    "line_number": int(m.group(1)) if m.group(1) else line_num,
                    "description": m.group(2).strip(),
                    "uom": (m.group(3) or "").strip(),
                    "quantity": qty,
                    "unit_rate": rate,
                    "amount": amount,
                    "extraction_confidence": 0.85,
                })
                continue

            # Fallback: detect lines with 3+ numeric tokens (amount-like patterns)
            tokens = line.split()
            numeric_tokens = [t for t in tokens if re.match(r"^[\d,]+(?:\.\d{1,2})?$", t)]
            if len(numeric_tokens) >= 2 and len(tokens) >= cls.MIN_COLS:
                try:
                    amount = float(numeric_tokens[-1].replace(",", ""))
                    rate = float(numeric_tokens[-2].replace(",", ""))
                    qty = float(numeric_tokens[-3].replace(",", "")) if len(numeric_tokens) >= 3 else 1.0
                except (ValueError, IndexError):
                    continue

                desc_tokens = [t for t in tokens if t not in numeric_tokens]
                if not desc_tokens:
                    continue

                line_num += 1
                items.append({
                    "line_number": line_num,
                    "description": " ".join(desc_tokens),
                    "uom": "",
                    "quantity": qty,
                    "unit_rate": rate,
                    "amount": amount,
                    "extraction_confidence": 0.55,
                })

        return items

    @classmethod
    def extract_and_parse(cls, file_path: str) -> dict:
        """
        Full pipeline for one quotation file.
        Returns {"text": str, "line_items": list, "error": str or None}
        """
        try:
            text = cls.extract_text_from_pdf(file_path)
            items = cls.parse_line_items(text)
            return {"text": text, "line_items": items, "error": None}
        except Exception as exc:
            logger.exception("extract_and_parse failed for %s", file_path)
            return {"text": "", "line_items": [], "error": str(exc)}
