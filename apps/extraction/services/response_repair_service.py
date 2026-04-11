"""ResponseRepairService — deterministic post-LLM JSON repair layer.

Sits between raw LLM output and ExtractionParserService. Applies
deterministic rules to fix common LLM extraction mistakes before
the parser receives the data.

Design principles:
  - Never silently invents values — only repairs when there is OCR evidence.
  - Returns the original JSON unchanged if it cannot be safely repaired.
  - Every repair action is recorded in repair_actions for audit/Langfuse.
  - Fail-silent: any unhandled exception returns the original JSON with a warning.

Phase 1 rules implemented:
  a. invoice_number exclusion — reject reference-field values (CART Ref, IRN, etc.)
  b. tax_percentage recomputation — derive from tax_amount / subtotal
  c. subtotal / line reconciliation — align subtotal with pre-tax line amounts
  d. line-level tax allocation — move GST to service-charge line for travel invoices
  e. travel line consolidation — prefer consolidated hotel Total Fare line
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RepairResult:
    repaired_json: dict[str, Any]
    repair_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    was_repaired: bool = False


# ---------------------------------------------------------------------------
# Patterns for excluded reference values that must not be invoice_number
# ---------------------------------------------------------------------------

# These label patterns appear in OCR just before a reference number that the
# LLM may incorrectly copy into invoice_number.
_EXCLUDED_REFERENCE_LABELS: list[str] = [
    r"client\s+code",
    r"\birn\b",                       # Invoice Reference Number (GST e-invoice hash)
    r"document\s+no\.?",
    r"booking\s+confirmation\s+no\.?",
    r"hotel\s+booking\s+(?:id|no\.?)",
    r"booking\s+(?:id|no\.?|reference)",
    r"e[-\s]?way\s+bill\s+no\.?",
    r"acknowledgement\s+no\.?",
    r"pnr",
    r"cart\s+ref(?:erence)?\.?\s*no\.?",  # Travel agency booking reference
    # Travel agency internal IDs that appear after print-copy labels
    r"original\s*/\s*duplicate\s+for\s+recipient",
    r"iata\s+approved\s+agency",
]

# Regex that matches IRN (64-char hex-like string) directly
_IRN_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

# Labels under which a REAL invoice number is stored
_INVOICE_NUMBER_LABELS = re.compile(
    r"(?:invoice|inv|tax\s+inv|bill|receipt|voucher|sr|serial)\s*(?:no\.?|number|#|id)",
    re.IGNORECASE,
)


class ResponseRepairService:
    """Apply deterministic repairs to raw LLM extraction JSON."""

    @classmethod
    def repair(
        cls,
        raw_json: dict[str, Any],
        *,
        ocr_text: str = "",
        invoice_category: Optional[str] = None,
    ) -> RepairResult:
        """Run all repair rules and return a RepairResult.

        Parameters
        ----------
        raw_json : dict
            Raw JSON dict from the LLM (may contain line_items list).
        ocr_text : str
            Original OCR text — used for recovery attempts (e.g. invoice number).
        invoice_category : str or None
            'goods', 'service', 'travel', or None. Enables category-specific rules.

        Returns
        -------
        RepairResult with repaired_json (copy), actions, warnings, was_repaired flag.
        """
        if not raw_json or not isinstance(raw_json, dict):
            return RepairResult(repaired_json=raw_json or {})

        try:
            data = dict(raw_json)  # shallow copy; line_items replaced below
            if "line_items" in data and isinstance(data["line_items"], list):
                data["line_items"] = [dict(li) for li in data["line_items"] if isinstance(li, dict)]

            actions: list[str] = []
            warnings: list[str] = []

            # Rule a — invoice number exclusion
            cls._repair_invoice_number(data, ocr_text, actions, warnings)

            # Rule b — tax percentage recomputation
            cls._repair_tax_percentage(data, ocr_text, actions, warnings)

            # Rule c — subtotal / line reconciliation
            cls._repair_subtotal(data, invoice_category, actions, warnings)

            # Rule d — line-level tax allocation (travel/service)
            if invoice_category in ("travel", "service"):
                cls._repair_line_tax_allocation(data, actions, warnings)

            # Rule e — travel line consolidation
            if invoice_category == "travel":
                cls._consolidate_travel_lines(data, actions, warnings)

            was_repaired = bool(actions)
            return RepairResult(
                repaired_json=data,
                repair_actions=actions,
                warnings=warnings,
                was_repaired=was_repaired,
            )

        except Exception as exc:
            logger.warning("ResponseRepairService.repair() failed unexpectedly: %s", exc)
            return RepairResult(
                repaired_json=raw_json,
                warnings=[f"Repair service error (pass-through): {exc}"],
                was_repaired=False,
            )

    # ------------------------------------------------------------------
    # Rule a — invoice number exclusion
    # ------------------------------------------------------------------

    @classmethod
    def _repair_invoice_number(
        cls,
        data: dict,
        ocr_text: str,
        actions: list[str],
        warnings: list[str],
    ) -> None:
        inv_num = str(data.get("invoice_number") or "").strip()
        if not inv_num:
            return

        excluded_value = cls._is_excluded_reference(inv_num, ocr_text)
        if not excluded_value:
            return

        # Attempt OCR recovery — search for a real invoice number label + value
        recovered = cls._recover_invoice_number_from_ocr(ocr_text, exclude_value=inv_num)
        if recovered:
            data["invoice_number"] = recovered
            actions.append(
                f"invoice_number: replaced excluded reference value "
                f"'{inv_num}' (matched: {excluded_value}) with OCR-recovered '{recovered}'"
            )
        else:
            data["invoice_number"] = ""
            warnings.append(
                f"invoice_number: cleared excluded reference value '{inv_num}' "
                f"(matched: {excluded_value}); could not recover from OCR"
            )
            actions.append(f"invoice_number: cleared excluded reference '{inv_num}'")

    @classmethod
    def _is_excluded_reference(cls, value: str, ocr_text: str) -> str:
        """Return the matched exclusion label if value appears to be an excluded reference.

        Checks:
        1. Value matches IRN pattern (64-char hex).
        2. Value appears immediately after an excluded label in the OCR text.
        Returns the matched label string, or '' if not excluded.
        """
        if _IRN_PATTERN.match(value):
            return "irn"

        if not ocr_text:
            return ""

        # Check if this exact value follows any excluded label in the OCR text
        escaped = re.escape(value)
        for label_pattern in _EXCLUDED_REFERENCE_LABELS:
            pattern = rf"{label_pattern}[\s:.\-]*{escaped}"
            if re.search(pattern, ocr_text, re.IGNORECASE):
                return label_pattern
        return ""

    @staticmethod
    def _recover_invoice_number_from_ocr(ocr_text: str, exclude_value: str = "") -> str:
        """Search OCR for a real invoice number using invoice-specific labels.

        Returns the first match not equal to exclude_value, or '' if none found.

        The pattern explicitly consumes common label suffixes (No., Number, #, ID)
        so they are not captured as part of the value.
        Document IDs must contain at least one digit to distinguish them from
        label words like 'number' or 'No.'.
        """
        if not ocr_text:
            return ""

        def _extract_from_pattern(src: str, pat: re.Pattern) -> str:
            for match in pat.finditer(src):
                candidate = match.group(1).strip().rstrip(".")
                if not candidate:
                    continue
                # Must contain at least one digit -- real IDs always do
                if not re.search(r"\d", candidate):
                    continue
                if len(candidate) < 3:
                    continue
                if exclude_value and candidate == exclude_value:
                    continue
                return candidate
            return ""

        def _find_near_label(label_pat: str, text: str, window: int = 10) -> str:
            """Scan line-by-line: find label, then look in next `window` lines
            for a standalone alphanumeric ID (handles multi-column OCR layouts
            where the label and value are separated by other labels)."""
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if re.search(label_pat, line, re.IGNORECASE):
                    for j in range(i + 1, min(i + window + 1, len(lines))):
                        candidate = lines[j].strip().rstrip(".")
                        # Must be a standalone token (no spaces), contain a digit,
                        # and be between 3-40 chars.
                        if (
                            candidate
                            and " " not in candidate
                            and re.search(r"\d", candidate)
                            and 3 <= len(candidate) <= 40
                            and (not exclude_value or candidate != exclude_value)
                        ):
                            return candidate
            return ""

        # Pass 1: standard invoice/bill/receipt labels.
        primary_pattern = re.compile(
            r"(?:tax\s+invoice|invoice|inv\.?|bill|receipt|voucher|serial\s*no\.?|sr\.?\s*no\.?)"
            r"\s*(?:no\.?|number|#|id)?"
            r"[\s:.\-#]+"
            r"([A-Za-z0-9][A-Za-z0-9/\-_.]{1,49})",
            re.IGNORECASE,
        )
        found = _extract_from_pattern(ocr_text, primary_pattern)
        if found:
            return found

        # Pass 2: travel agency fallback -- 'CART Ref. No.' is the booking
        # reference used as the invoice number in FCM-style travel invoices.
        # OCR layouts are often multi-column so the value may be several lines
        # below the label; use a line-window scan instead of inline regex.
        found = _find_near_label(
            r"cart\s+ref(?:erence)?\.?\s*no\.?",
            ocr_text,
        )
        if found:
            return found

        return ""

    # ------------------------------------------------------------------
    # Rule b — tax percentage recomputation
    # ------------------------------------------------------------------

    # Standard Indian GST slab rates (percent).
    # 0.25 is included for precious/semi-precious stones (Chapter 71 HSN 7102-7104).
    _GST_STANDARD_RATES: tuple = (0, 0.25, 3, 5, 12, 18, 28)

    @classmethod
    def _snap_to_gst_rate(cls, computed: float) -> float:
        """Return the nearest standard GST slab rate (always -- no tolerance)."""
        return float(min(cls._GST_STANDARD_RATES, key=lambda r: abs(r - computed)))

    @classmethod
    def _extract_gst_rate_from_ocr(cls, ocr_text: str) -> Optional[float]:
        """Scan OCR text for an explicitly stated GST rate.

        Handles two forms:
          - IGST <rate>%  (inter-state)  -> rate
          - CGST <rate>% + SGST <rate>% (intra-state) -> sum
        Returns the rate as a float if it is a valid GST slab, else None.
        """
        if not ocr_text:
            return None
        valid = set(cls._GST_STANDARD_RATES)

        # IGST rate: "IGST 18.00%" or "IGST: 18%"
        igst_match = re.search(
            r"igst[\s:]+([0-9]+(?:\.[0-9]+)?)\s*%",
            ocr_text, re.IGNORECASE,
        )
        if igst_match:
            rate = float(igst_match.group(1))
            if rate in valid:
                return rate

        # CGST + SGST rates — pick the first clearly labelled pair
        cgst_match = re.search(
            r"cgst[\s:]+([0-9]+(?:\.[0-9]+)?)\s*%",
            ocr_text, re.IGNORECASE,
        )
        sgst_match = re.search(
            r"sgst[\s:]+([0-9]+(?:\.[0-9]+)?)\s*%",
            ocr_text, re.IGNORECASE,
        )
        if cgst_match and sgst_match:
            combined = float(cgst_match.group(1)) + float(sgst_match.group(1))
            if combined in valid:
                return combined

        return None

    @classmethod
    def _repair_tax_percentage(
        cls,
        data: dict,
        ocr_text: str,
        actions: list[str],
        warnings: list[str],
    ) -> None:
        subtotal = _to_decimal(data.get("subtotal"))
        tax_amount = _to_decimal(data.get("tax_amount"))
        current_pct = _to_decimal(data.get("tax_percentage"))

        if subtotal is None or subtotal <= 0:
            return
        if tax_amount is None or tax_amount <= 0:
            return

        recomputed = round(float(tax_amount / subtotal * 100), 4)

        # For GST invoices (cgst/sgst/igst keys present in breakdown), if the
        # current extracted rate is not a valid slab, try to read the explicit
        # rate from the OCR text before falling back to the recomputed value.
        tax_breakdown = data.get("tax_breakdown") or {}
        is_gst = any(k in tax_breakdown for k in ("cgst", "sgst", "igst"))
        final_rate = recomputed
        ocr_rate_used = False
        if is_gst:
            valid_gst_rates = set(cls._GST_STANDARD_RATES)
            current_val = float(current_pct) if current_pct is not None else None
            if current_val is None or current_val not in valid_gst_rates:
                ocr_rate = cls._extract_gst_rate_from_ocr(ocr_text)
                if ocr_rate is not None:
                    final_rate = ocr_rate
                    ocr_rate_used = True

        # Only repair if the LLM value differs by more than 0.5 percentage points.
        # GST slab validation downstream in ValidationService handles the error
        # case when we still cannot determine a valid rate.
        if current_pct is not None and abs(float(current_pct) - final_rate) < 0.5:
            return

        data["tax_percentage"] = final_rate
        if ocr_rate_used:
            actions.append(
                f"tax_percentage: set to {final_rate}% from explicit OCR rate "
                f"(computed rate {recomputed}% from subtotal was not a valid GST slab)"
            )
        else:
            actions.append(
                f"tax_percentage: recomputed to {final_rate}% "
                f"(tax_amount={tax_amount}, subtotal={subtotal})"
            )

    # ------------------------------------------------------------------
    # Rule c — subtotal / line reconciliation
    # ------------------------------------------------------------------

    @staticmethod
    def _repair_subtotal(
        data: dict,
        invoice_category: Optional[str],
        actions: list[str],
        warnings: list[str],
    ) -> None:
        line_items = data.get("line_items") or []
        if not line_items:
            return

        # Sum line_amount values, excluding any line that looks like a tax line
        line_sum = Decimal("0")
        has_valid_lines = False
        for li in line_items:
            desc = str(li.get("item_description") or li.get("description") or "").lower()
            # Skip GST/tax/roundoff lines
            if re.search(r"\b(?:gst|cgst|sgst|igst|vat|tax|round\s*off|roundoff)\b", desc):
                continue
            amt = _to_decimal(li.get("line_amount") or li.get("amount"))
            if amt is not None and amt > 0:
                line_sum += amt
                has_valid_lines = True

        if not has_valid_lines or line_sum <= 0:
            return

        current_subtotal = _to_decimal(data.get("subtotal"))

        # Only repair if gap > 1 (avoid floating-point noise)
        if current_subtotal is not None and abs(float(line_sum - current_subtotal)) <= 1.0:
            return

        # ── Guard: when header subtotal is closer to total_amount, trust
        # the header rather than the (possibly mis-extracted) line sums.
        # A large divergence (>10%) between line_sum and header subtotal
        # usually means the line amounts are wrong, not the header.
        total_amount = _to_decimal(data.get("total_amount"))
        tax_amount = _to_decimal(data.get("tax_amount"))
        if current_subtotal is not None and current_subtotal > 0 and total_amount is not None and total_amount > 0:
            # Guard 1: if current subtotal + tax already reconciles to
            # total_amount, it is almost certainly correct.  Do not
            # override with a line_sum that would break that equation.
            if tax_amount is not None and tax_amount >= 0:
                header_total = current_subtotal + tax_amount
                if abs(float(header_total - total_amount)) <= 1.0:
                    line_total = line_sum + tax_amount
                    if abs(float(line_total - total_amount)) > 1.0:
                        warnings.append(
                            f"subtotal: header subtotal ({current_subtotal}) + tax "
                            f"({tax_amount}) = {header_total} matches total_amount "
                            f"({total_amount}); line sum ({line_sum}) would break "
                            f"this -- kept header value"
                        )
                        return

            # Guard 2: header subtotal closer to total_amount than line sum
            header_gap = abs(float(current_subtotal - total_amount)) / float(total_amount)
            line_gap = abs(float(line_sum - total_amount)) / float(total_amount)
            delta_pct = abs(float(line_sum - current_subtotal)) / float(current_subtotal) * 100
            if delta_pct > 10.0 and header_gap < line_gap:
                # Header subtotal is closer to total_amount than line sum --
                # the lines are suspect, not the header.  Skip the repair.
                warnings.append(
                    f"subtotal: line sum ({line_sum}) diverges {delta_pct:.1f}% "
                    f"from header subtotal ({current_subtotal}); header is closer "
                    f"to total_amount ({total_amount}) -- kept header value"
                )
                return

        data["subtotal"] = str(line_sum)
        actions.append(
            f"subtotal: aligned to sum of pre-tax line amounts "
            f"({line_sum}; was {current_subtotal})"
        )

    # ------------------------------------------------------------------
    # Rule d — line-level tax allocation for service/travel invoices
    # ------------------------------------------------------------------

    @staticmethod
    def _repair_line_tax_allocation(
        data: dict,
        actions: list[str],
        warnings: list[str],
    ) -> None:
        """Move tax to the service-charge line when OCR confirms tax applies only there.

        Only fires when:
        - There is exactly one service/finance-charge line in the invoice
        - All other lines (base fare / hotel) have zero tax
        - The invoice-level tax_amount is non-zero
        """
        line_items = data.get("line_items") or []
        if len(line_items) < 2:
            return

        total_tax = _to_decimal(data.get("tax_amount"))
        if not total_tax or total_tax <= 0:
            return

        # Identify service-charge / finance-charge lines
        _SERVICE_DESC = re.compile(
            r"\b(?:service\s+charge|finance\s+charge|convenience\s+fee"
            r"|processing\s+fee|management\s+fee|handling\s+charge)\b",
            re.IGNORECASE,
        )
        _BASE_DESC = re.compile(
            r"\b(?:base\s+fare|basic\s+fare|room\s+rate|hotel\s+rate"
            r"|accommodation|room\s+charge|net\s+fare)\b",
            re.IGNORECASE,
        )

        service_indices = []
        base_indices = []
        for i, li in enumerate(line_items):
            desc = str(li.get("item_description") or li.get("description") or "")
            if _SERVICE_DESC.search(desc):
                service_indices.append(i)
            elif _BASE_DESC.search(desc):
                base_indices.append(i)

        # Only repair when exactly one service line exists alongside base lines
        if len(service_indices) != 1 or not base_indices:
            return

        # Check if line tax is already correctly allocated
        svc_li = line_items[service_indices[0]]
        existing_svc_tax = _to_decimal(svc_li.get("tax_amount"))
        if existing_svc_tax and abs(float(existing_svc_tax) - float(total_tax)) <= 0.01:
            return  # already correct

        # Zero out tax on base/hotel lines, assign all tax to service line
        for i in base_indices:
            if _to_decimal(line_items[i].get("tax_amount")):
                line_items[i]["tax_amount"] = "0"
                line_items[i]["tax_percentage"] = "0"

        line_items[service_indices[0]]["tax_amount"] = str(total_tax)
        actions.append(
            f"line_tax_allocation: moved tax ({total_tax}) to service-charge "
            f"line (index {service_indices[0]}); zeroed base/hotel line taxes"
        )

    # ------------------------------------------------------------------
    # Rule e — travel line consolidation
    # ------------------------------------------------------------------

    @staticmethod
    def _consolidate_travel_lines(
        data: dict,
        actions: list[str],
        warnings: list[str],
    ) -> None:
        """If OCR shows Basic Fare + Hotel Taxes + Total Fare for the same stay,
        prefer one consolidated line using Total Fare and remove the sub-lines.

        Only fires when all three of the following appear in line_items:
          - a Basic/Base Fare line
          - a Hotel Taxes line
          - a Total Fare line whose amount == basic + taxes (within 1 unit tolerance)
        """
        line_items = data.get("line_items") or []
        if len(line_items) < 3:
            return

        _BASIC = re.compile(r"\b(?:basic|base)\s+fare\b", re.IGNORECASE)
        _HOTEL_TAX = re.compile(r"\bhotel\s+tax(?:es)?\b", re.IGNORECASE)
        _TOTAL_FARE = re.compile(r"\btotal\s+fare\b", re.IGNORECASE)

        basic_idx = None
        hotel_tax_idx = None
        total_fare_idx = None

        for i, li in enumerate(line_items):
            desc = str(li.get("item_description") or li.get("description") or "")
            if _BASIC.search(desc) and basic_idx is None:
                basic_idx = i
            elif _HOTEL_TAX.search(desc) and hotel_tax_idx is None:
                hotel_tax_idx = i
            elif _TOTAL_FARE.search(desc) and total_fare_idx is None:
                total_fare_idx = i

        if None in (basic_idx, hotel_tax_idx, total_fare_idx):
            return

        basic_amt = _to_decimal(line_items[basic_idx].get("line_amount") or line_items[basic_idx].get("amount"))
        hotel_tax_amt = _to_decimal(line_items[hotel_tax_idx].get("line_amount") or line_items[hotel_tax_idx].get("amount"))
        total_fare_amt = _to_decimal(line_items[total_fare_idx].get("line_amount") or line_items[total_fare_idx].get("amount"))

        if None in (basic_amt, hotel_tax_amt, total_fare_amt):
            return

        # Verify Total Fare ≈ Basic + Hotel Tax
        expected = basic_amt + hotel_tax_amt
        if abs(float(total_fare_amt - expected)) > 1.0:
            warnings.append(
                f"travel_consolidation: Total Fare ({total_fare_amt}) != "
                f"Basic ({basic_amt}) + Hotel Tax ({hotel_tax_amt}) — skipped"
            )
            return

        # Keep only the Total Fare line; remove Basic and Hotel Tax lines
        indices_to_remove = sorted({basic_idx, hotel_tax_idx}, reverse=True)
        for idx in indices_to_remove:
            line_items.pop(idx)

        actions.append(
            f"travel_consolidation: replaced Basic Fare ({basic_amt}) + "
            f"Hotel Taxes ({hotel_tax_amt}) with consolidated Total Fare ({total_fare_amt})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_decimal(value: Any) -> Optional[Decimal]:
    """Convert a value to Decimal, stripping currency symbols. Returns None on failure."""
    if value is None:
        return None
    try:
        cleaned = re.sub(r"[^\d.,\-]", "", str(value)).replace(",", "")
        if not cleaned or cleaned == "-":
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
