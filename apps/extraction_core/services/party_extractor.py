"""
PartyExtractor — Extract business entity information from documents.

Identifies supplier, buyer, ship-to, and bill-to parties with structured
address blocks.  Supports multiple entities per role and handles
international address formats without country-specific hardcoding.

Runs as part of the document intelligence pre-processing layer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class PartyAddress:
    """Structured address for a business party."""

    line1: str = ""
    line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""

    def to_dict(self) -> dict:
        d: dict = {}
        for attr in ("line1", "line2", "city", "state", "postal_code", "country"):
            val = getattr(self, attr)
            if val:
                d[attr] = val
        return d


@dataclass
class Party:
    """A single business entity extracted from the document."""

    role: str          # SUPPLIER | BUYER | SHIP_TO | BILL_TO
    name: str = ""
    tax_id: str = ""   # GSTIN, TRN, VAT number, etc.
    address: Optional[PartyAddress] = None
    confidence: float = 0.0
    source_snippet: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "role": self.role,
            "name": self.name,
            "confidence": round(self.confidence, 4),
        }
        if self.tax_id:
            d["tax_id"] = self.tax_id
        if self.address:
            d["address"] = self.address.to_dict()
        if self.source_snippet:
            d["source_snippet"] = self.source_snippet
        return d


@dataclass
class PartyExtractionResult:
    """Result of party extraction — all identified entities."""

    suppliers: list[Party] = field(default_factory=list)
    buyers: list[Party] = field(default_factory=list)
    ship_to: list[Party] = field(default_factory=list)
    bill_to: list[Party] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suppliers": [p.to_dict() for p in self.suppliers],
            "buyers": [p.to_dict() for p in self.buyers],
            "ship_to": [p.to_dict() for p in self.ship_to],
            "bill_to": [p.to_dict() for p in self.bill_to],
        }

    @property
    def primary_supplier(self) -> Party | None:
        if self.suppliers:
            return max(self.suppliers, key=lambda p: p.confidence)
        return None

    @property
    def primary_buyer(self) -> Party | None:
        if self.buyers:
            return max(self.buyers, key=lambda p: p.confidence)
        return None

    @property
    def all_parties(self) -> list[Party]:
        return self.suppliers + self.buyers + self.ship_to + self.bill_to


# ---------------------------------------------------------------------------
# Label patterns — multilingual, role-based
# ---------------------------------------------------------------------------

# Each entry: (regex, role, confidence_boost)
# The regex matches a section header/label that introduces a party block.

_ROLE_LABELS: list[tuple[str, str, float]] = [
    # ── Supplier / Seller / Vendor ──
    (r"\b(?:Sold|Supplied|Shipped)\s*By\b", "SUPPLIER", 0.90),
    (r"\bFrom\s*[:;]", "SUPPLIER", 0.70),
    (r"\bSupplier\s*(?:Name|Details?|Info)?", "SUPPLIER", 0.90),
    (r"\bSeller\s*(?:Name|Details?|Info)?", "SUPPLIER", 0.90),
    (r"\bVendor\s*(?:Name|Details?|Info)?", "SUPPLIER", 0.90),
    (r"\bConsignor\b", "SUPPLIER", 0.85),
    (r"\bExporter\b", "SUPPLIER", 0.85),
    # Arabic / Hindi / French / German / Spanish
    (r"\bالمورد|البائع\b", "SUPPLIER", 0.85),
    (r"\bविक्रेता|आपूर्तिकर्ता\b", "SUPPLIER", 0.85),
    (r"\bFournisseur\b", "SUPPLIER", 0.85),
    (r"\bLieferant\b", "SUPPLIER", 0.85),
    (r"\bProveedor\b", "SUPPLIER", 0.85),

    # ── Buyer / Customer ──
    (r"\bBill(?:ed)?\s*To\b", "BILL_TO", 0.95),
    (r"\bBilling\s*(?:Address|To|Party)", "BILL_TO", 0.90),
    (r"\bInvoice\s*To\b", "BILL_TO", 0.90),
    (r"\bSold\s*To\b", "BILL_TO", 0.90),
    (r"\bBuyer\s*(?:Name|Details?|Info)?", "BUYER", 0.90),
    (r"\bCustomer\s*(?:Name|Details?|Info)?", "BUYER", 0.85),
    (r"\bPurchaser\b", "BUYER", 0.85),
    (r"\bClient\b", "BUYER", 0.75),
    (r"\bConsignee\b", "BUYER", 0.80),
    (r"\bImporter\b", "BUYER", 0.80),
    # Arabic / Hindi / French / German / Spanish
    (r"\bالمشتري|العميل\b", "BUYER", 0.85),
    (r"\bक्रेता|ग्राहक\b", "BUYER", 0.85),
    (r"\bAcheteur\b", "BUYER", 0.85),
    (r"\bKäufer|Kunde\b", "BUYER", 0.85),
    (r"\bComprador\b", "BUYER", 0.85),

    # ── Ship-to ──
    (r"\bShip(?:ped|ping)?\s*To\b", "SHIP_TO", 0.95),
    (r"\bDeliver(?:y|ed)?\s*(?:To|Address)\b", "SHIP_TO", 0.90),
    (r"\bConsign(?:ee|ed)\s*To\b", "SHIP_TO", 0.90),
    (r"\bDestination\s*(?:Address)?\b", "SHIP_TO", 0.80),
    (r"\bPlace\s*of\s*(?:Delivery|Supply)\b", "SHIP_TO", 0.85),
    # Arabic
    (r"\bعنوان\s*(?:التسليم|الشحن)\b", "SHIP_TO", 0.85),
]

# ---------------------------------------------------------------------------
# Tax ID patterns — international (no country hardcoding)
# ---------------------------------------------------------------------------

_TAX_ID_PATTERNS: list[tuple[str, float]] = [
    # India GSTIN: 15-char alphanumeric
    (r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]{2}\b", 0.95),
    # UAE / Saudi TRN: 15-digit
    (r"\b\d{15}\b", 0.60),
    # EU VAT: 2-letter country + 2-15 alnum
    (r"\b[A-Z]{2}\d{2,15}\b", 0.70),
    (r"\b[A-Z]{2}[A-Z0-9]{2,13}\b", 0.65),
    # Generic tax ID with label
    (r"(?:GSTIN|TRN|VAT\s*(?:No|ID|Reg)|Tax\s*(?:ID|No|Reg(?:istration)?))[\s:]*([A-Z0-9][\w\-]{4,20})", 0.90),
]

# Postal code patterns — international
_POSTAL_CODE_RX = re.compile(
    r"\b(?:"
    r"\d{5,6}"          # US/IN/DE 5-6 digit
    r"|[A-Z]{1,2}\d{1,2}\s*\d[A-Z]{2}"  # UK format
    r"|[A-Z]\d[A-Z]\s*\d[A-Z]\d"        # Canada
    r"|[A-Z]{2}\s*\d{4}"                 # NL
    r")\b",
    re.IGNORECASE,
)


class PartyExtractor:
    """
    Extracts business party information from document text.

    Identifies supplier, buyer, ship-to, and bill-to entities by
    locating role-labelled sections and parsing the text block
    following each label for name, tax ID, and address.

    Country-agnostic — uses generic international patterns.
    Supports multiple entities per role.
    """

    # How many chars to scan after a role label for the party block
    _BLOCK_SIZE = 600
    # Minimum name length to accept
    _MIN_NAME_LENGTH = 3
    # Max lines in a party block
    _MAX_BLOCK_LINES = 12

    @classmethod
    def extract(cls, ocr_text: str) -> PartyExtractionResult:
        """
        Extract all party entities from OCR text.

        Returns PartyExtractionResult with deduplicated parties per role.
        """
        if not ocr_text or not ocr_text.strip():
            return PartyExtractionResult()

        result = PartyExtractionResult()
        seen: dict[str, set[str]] = {
            "SUPPLIER": set(),
            "BUYER": set(),
            "BILL_TO": set(),
            "SHIP_TO": set(),
        }

        for label_rx, role, base_confidence in _ROLE_LABELS:
            for m in re.finditer(label_rx, ocr_text, re.IGNORECASE):
                block_start = m.end()
                block_end = min(
                    len(ocr_text), block_start + cls._BLOCK_SIZE,
                )
                block = ocr_text[block_start:block_end]

                party = cls._parse_party_block(block, role, base_confidence)
                if not party or not party.name:
                    continue

                # Deduplicate by normalized name
                norm_name = party.name.strip().upper()
                if norm_name in seen.get(role, set()):
                    continue
                seen.setdefault(role, set()).add(norm_name)

                # Set source snippet
                snip_start = max(0, m.start() - 20)
                snip_end = min(len(ocr_text), block_start + 200)
                party.source_snippet = (
                    ocr_text[snip_start:snip_end].replace("\n", " ").strip()
                )

                cls._assign_party(result, party)

        # If we found BILL_TO but no BUYER, copy BILL_TO → BUYER
        if result.bill_to and not result.buyers:
            for p in result.bill_to:
                buyer = Party(
                    role="BUYER",
                    name=p.name,
                    tax_id=p.tax_id,
                    address=p.address,
                    confidence=p.confidence * 0.9,
                    source_snippet=p.source_snippet,
                )
                result.buyers.append(buyer)

        return result

    @classmethod
    def _parse_party_block(
        cls,
        block: str,
        role: str,
        base_confidence: float,
    ) -> Party | None:
        """
        Parse a text block following a role label to extract party details.
        """
        lines = block.strip().split("\n")
        if not lines:
            return None

        # Take up to MAX_BLOCK_LINES
        lines = lines[: cls._MAX_BLOCK_LINES]

        # Clean lines — strip, skip empty and separator lines
        clean_lines: list[str] = []
        for ln in lines:
            stripped = ln.strip()
            # Skip empty or separator lines
            if not stripped or re.match(r"^[-=_]+$", stripped):
                if clean_lines:  # stop at first blank after content
                    break
                continue
            # Skip if line starts a new label section
            if cls._is_new_section_label(stripped):
                break
            clean_lines.append(stripped)

        if not clean_lines:
            return None

        # First non-trivial line is usually the name
        # Skip lines that look like ":" or just punctuation
        name = ""
        name_idx = 0
        for i, ln in enumerate(clean_lines):
            candidate = re.sub(r"^[\s:;,\-]+", "", ln).strip()
            if len(candidate) >= cls._MIN_NAME_LENGTH and not cls._is_tax_id_line(candidate):
                name = candidate
                name_idx = i
                break

        if not name:
            return None

        # Truncate name at first comma or pipe if it's very long
        if len(name) > 80:
            for sep in (",", "|", ";"):
                if sep in name:
                    name = name[: name.index(sep)].strip()
                    break

        # Extract tax ID from the block
        tax_id = cls._find_tax_id("\n".join(clean_lines))

        # Extract address from remaining lines
        address = cls._parse_address(clean_lines[name_idx + 1:])

        confidence = base_confidence
        if tax_id:
            confidence = min(confidence + 0.05, 1.0)
        if address and (address.city or address.postal_code):
            confidence = min(confidence + 0.03, 1.0)

        return Party(
            role=role,
            name=name,
            tax_id=tax_id,
            address=address,
            confidence=confidence,
        )

    @classmethod
    def _is_new_section_label(cls, line: str) -> bool:
        """Check if a line starts a new labelled section."""
        for label_rx, _, _ in _ROLE_LABELS:
            if re.match(label_rx, line, re.IGNORECASE):
                return True
        # Generic section headers
        if re.match(
            r"^(?:Description|Qty|Amount|Total|Date|Invoice|Item|Sr|Sl|"
            r"Particulars|HSN|SAC|Rate|Tax|CGST|SGST|IGST|VAT)\b",
            line,
            re.IGNORECASE,
        ):
            return True
        return False

    @classmethod
    def _is_tax_id_line(cls, line: str) -> bool:
        """Check if the line is primarily a tax ID."""
        stripped = line.strip()
        if re.match(
            r"^(?:GSTIN|TRN|VAT|Tax\s*(?:ID|No))\s*[:;]?\s*\S+$",
            stripped,
            re.IGNORECASE,
        ):
            return True
        return False

    @classmethod
    def _find_tax_id(cls, text: str) -> str:
        """Find the best tax ID in a text block."""
        # Try labelled patterns first (highest confidence)
        labelled_rx = re.compile(
            r"(?:GSTIN|TRN|VAT\s*(?:No|ID|Reg(?:istration)?)|"
            r"Tax\s*(?:ID|No|Reg(?:istration)?))"
            r"[\s:;#\-]*([A-Z0-9][\w\-]{4,20})",
            re.IGNORECASE,
        )
        m = labelled_rx.search(text)
        if m:
            return m.group(1).strip()

        # Try standalone GSTIN pattern (15-char)
        gstin_rx = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]{2})\b")
        m = gstin_rx.search(text)
        if m:
            return m.group(1)

        return ""

    @classmethod
    def _parse_address(cls, lines: list[str]) -> PartyAddress | None:
        """
        Parse address from remaining lines of a party block.

        Uses heuristic line analysis — no country-specific assumptions.
        """
        if not lines:
            return None

        addr = PartyAddress()
        addr_lines: list[str] = []

        for ln in lines:
            stripped = ln.strip()
            # Skip tax ID lines
            if cls._is_tax_id_line(stripped):
                continue
            # Skip if it's a new section
            if cls._is_new_section_label(stripped):
                break
            addr_lines.append(stripped)

        if not addr_lines:
            return None

        # Try to identify postal code
        postal_match = None
        for ln in addr_lines:
            pm = _POSTAL_CODE_RX.search(ln)
            if pm:
                postal_match = pm
                addr.postal_code = pm.group(0).strip()
                break

        # Assign lines to address fields heuristically
        if len(addr_lines) >= 1:
            addr.line1 = addr_lines[0]
        if len(addr_lines) >= 2:
            addr.line2 = addr_lines[1]

        # Last line with postal code likely has city/state
        for ln in reversed(addr_lines):
            if _POSTAL_CODE_RX.search(ln):
                parts = re.split(r"[,\-]", ln)
                parts = [p.strip() for p in parts if p.strip()]
                for part in parts:
                    if _POSTAL_CODE_RX.search(part):
                        addr.postal_code = _POSTAL_CODE_RX.search(part).group(0)
                    elif not addr.city and len(part) > 2:
                        addr.city = part
                    elif not addr.state and len(part) > 1:
                        addr.state = part
                break

        # If no city found yet, try second-to-last line
        if not addr.city and len(addr_lines) >= 2:
            candidate = addr_lines[-1]
            parts = re.split(r"[,\-]", candidate)
            for part in parts:
                part = part.strip()
                if part and not _POSTAL_CODE_RX.search(part) and len(part) > 2:
                    if not addr.city:
                        addr.city = part

        return addr if (addr.line1 or addr.city) else None

    @classmethod
    def _assign_party(cls, result: PartyExtractionResult, party: Party) -> None:
        """Assign a party to the correct list in the result."""
        if party.role == "SUPPLIER":
            result.suppliers.append(party)
        elif party.role == "BUYER":
            result.buyers.append(party)
        elif party.role == "BILL_TO":
            result.bill_to.append(party)
        elif party.role == "SHIP_TO":
            result.ship_to.append(party)
