"""Field-level confidence scoring for extracted invoice data.

Produces a per-field confidence map (0.0–1.0) based on:
  - Presence / absence in the repaired LLM JSON
  - Parse / normalization success
  - Whether a repair action affected the field
  - Cross-field consistency (e.g., qty × unit_price ≈ line_amount)

Scoring bands:
  0.95–1.00  Explicit value, clean parse, no repair affecting this field
  0.80–0.94  Minor repair not directly modifying this field; all values parse OK
  0.60–0.79  Repair action directly modified this field, OR field recovered from OCR
  0.30–0.59  Value present but suspicious / ambiguous (zero totals, mismatched math)
  0.00–0.29  Field absent from LLM output or normalization produced None / empty
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Critical header fields — drive requires_review_override in ValidationService
CRITICAL_FIELDS = {"invoice_number", "vendor_name", "invoice_date", "currency", "total_amount"}

# Repair action prefixes/substrings that affect each field
_REPAIR_FIELD_MAP = {
    "invoice_number": ["invoice_number"],
    "tax_percentage": ["tax_percentage"],
    "subtotal": ["subtotal"],
    "line_items": ["line_", "travel"],
    "tax_amount": ["tax_amount"],
}


@dataclass
class FieldConfidenceResult:
    """Per-field confidence map for a single extracted invoice."""
    header: Dict[str, float] = field(default_factory=dict)
    lines: List[Dict[str, float]] = field(default_factory=list)
    weakest_critical_field: str = ""
    weakest_critical_score: float = 1.0
    low_confidence_fields: List[str] = field(default_factory=list)  # score < 0.6
    evidence_flags: Dict[str, str] = field(default_factory=dict)  # field → evidence note


class FieldConfidenceService:
    """Deterministic field-level confidence scoring. Fail-silent by design."""

    @staticmethod
    def score(
        normalized,
        raw_json: dict,
        repair_actions: Optional[List[str]] = None,
        ocr_text: Optional[str] = None,
        evidence_context: Optional[Dict[str, Any]] = None,
    ) -> FieldConfidenceResult:
        """Score each field.

        Args:
            normalized: NormalizedInvoice instance (post-normalization)
            raw_json: The repaired JSON dict (ExtractionResponse.raw_json)
            repair_actions: List of repair action strings from _repair metadata
            ocr_text: Optional raw OCR text — used for substring confirmation of
                      extracted values (boosts confidence when value found in OCR).
            evidence_context: Optional dict with extraction evidence snippets.
                Keys of interest:
                  - "extraction_method": str ("explicit", "repaired", "recovered", "derived")
                  - "snippets": dict[field_name, str] — raw text snippet near the field
        """
        try:
            return FieldConfidenceService._score(
                normalized,
                raw_json,
                repair_actions or [],
                ocr_text=ocr_text or "",
                evidence_context=evidence_context or {},
            )
        except Exception:
            logger.exception("FieldConfidenceService.score failed — returning neutral scores")
            return FieldConfidenceResult()

    @staticmethod
    def _score(
        normalized,
        raw_json: dict,
        repair_actions: List[str],
        ocr_text: str = "",
        evidence_context: Dict[str, Any] = None,
    ) -> FieldConfidenceResult:
        repaired_fields = FieldConfidenceService._repaired_field_set(repair_actions)
        evidence_context = evidence_context or {}
        extraction_method = evidence_context.get("extraction_method", "")
        evidence_snippets = evidence_context.get("snippets", {}) or {}
        ocr_lower = ocr_text.lower() if ocr_text else ""
        evidence_flags: Dict[str, str] = {}

        header = {}

        # invoice_number
        raw_inv = raw_json.get("invoice_number") or ""
        if not raw_inv or not str(raw_inv).strip():
            header["invoice_number"] = 0.0
        elif not normalized.normalized_invoice_number:
            header["invoice_number"] = 0.1  # present but normalization stripped it
        elif "invoice_number" in repaired_fields:
            # check if it was "recovered" vs "excluded/replaced"
            recovery = any("recovered" in a for a in repair_actions if "invoice_number" in a)
            header["invoice_number"] = 0.65 if recovery else 0.78
        else:
            header["invoice_number"] = 1.0

        # vendor_name
        if not (raw_json.get("vendor_name") or "").strip():
            header["vendor_name"] = 0.0
        elif not normalized.vendor_name_normalized:
            header["vendor_name"] = 0.1
        else:
            header["vendor_name"] = 1.0

        # vendor_tax_id — optional
        if (raw_json.get("vendor_tax_id") or "").strip():
            header["vendor_tax_id"] = 1.0
        else:
            header["vendor_tax_id"] = 0.5  # optional field

        # buyer_name — optional
        if (raw_json.get("buyer_name") or "").strip():
            header["buyer_name"] = 1.0
        else:
            header["buyer_name"] = 0.5  # optional field

        # invoice_date
        if not (raw_json.get("invoice_date") or "").strip():
            header["invoice_date"] = 0.0
        elif normalized.invoice_date is None:
            header["invoice_date"] = 0.35  # present but parse failed
        else:
            header["invoice_date"] = 1.0

        # due_date — optional
        if not (raw_json.get("due_date") or "").strip():
            header["due_date"] = 0.5  # optional
        elif normalized.due_date is None:
            header["due_date"] = 0.4  # present but unparseable
        else:
            header["due_date"] = 1.0

        # po_number — optional but important
        if (raw_json.get("po_number") or "").strip():
            header["po_number"] = 1.0
        else:
            header["po_number"] = 0.5

        # currency
        raw_cur = (raw_json.get("currency") or "").strip().upper()
        if not raw_cur:
            header["currency"] = 0.0
        elif len(raw_cur) != 3:
            header["currency"] = 0.35  # present but invalid format; defaulted to USD
        elif normalized.currency == "USD" and raw_cur != "USD":
            header["currency"] = 0.4  # defaulted
        else:
            header["currency"] = 1.0

        # total_amount — critical
        if not (raw_json.get("total_amount") or ""):
            header["total_amount"] = 0.0
        elif normalized.total_amount is None:
            header["total_amount"] = 0.1  # present but unparseable
        elif normalized.total_amount == Decimal("0"):
            header["total_amount"] = 0.25  # zero total is suspicious
        else:
            header["total_amount"] = 1.0

        # subtotal
        if not (raw_json.get("subtotal") or ""):
            header["subtotal"] = 0.2
        elif normalized.subtotal is None:
            header["subtotal"] = 0.15
        elif "subtotal" in repaired_fields:
            header["subtotal"] = 0.72
        else:
            header["subtotal"] = 1.0

        # tax_amount
        if not (raw_json.get("tax_amount") or ""):
            header["tax_amount"] = 0.3  # tax can be 0 legitimately
        elif normalized.tax_amount is None:
            header["tax_amount"] = 0.2
        else:
            header["tax_amount"] = 1.0

        # tax_percentage
        if not (raw_json.get("tax_percentage") or ""):
            header["tax_percentage"] = 0.2
        elif "tax_percentage" in repaired_fields:
            # It was derived/recomputed — deterministic but not directly stated
            header["tax_percentage"] = 0.55
        else:
            header["tax_percentage"] = 1.0

        # tax_breakdown — optional
        raw_tb = raw_json.get("tax_breakdown") or {}
        tb = normalized.tax_breakdown or {}
        if not raw_tb or not isinstance(raw_tb, dict):
            header["tax_breakdown"] = 0.3
        elif all(float(tb.get(k, 0)) == 0.0 for k in ("cgst", "sgst", "igst", "vat")):
            header["tax_breakdown"] = 0.4  # all zeros
        else:
            header["tax_breakdown"] = 1.0

        # Line items
        line_confidences = []
        raw_lines = raw_json.get("line_items") or []
        for idx, li in enumerate(normalized.line_items):
            raw_li = raw_lines[idx] if idx < len(raw_lines) else {}
            lc = FieldConfidenceService._score_line(li, raw_li, repair_actions, idx + 1)
            line_confidences.append(lc)

        # ── Evidence-aware adjustments ────────────────────────────────────────
        # 1. extraction_method signal: lower bands for repaired/recovered/derived
        if extraction_method in ("repaired", "recovered", "derived"):
            _method_cap = {"repaired": 0.78, "recovered": 0.65, "derived": 0.55}.get(
                extraction_method, 1.0
            )
            for cf in CRITICAL_FIELDS:
                if cf in header and header[cf] > _method_cap:
                    header[cf] = _method_cap
                    evidence_flags[cf] = f"capped_by_extraction_method:{extraction_method}"

        # 2. OCR substring confirmation: boost fields that appear verbatim in OCR
        if ocr_lower:
            _ocr_boost_fields = {
                "invoice_number": normalized.normalized_invoice_number or "",
                "vendor_name": normalized.vendor_name_normalized or "",
                "currency": normalized.currency or "",
            }
            for fname, fval in _ocr_boost_fields.items():
                if not fval:
                    continue
                fval_lower = str(fval).lower().strip()
                if len(fval_lower) >= 3 and fval_lower in ocr_lower:
                    old_score = header.get(fname, 0.0)
                    if old_score < 0.95:
                        # Confirmed in OCR — nudge up within the current scoring band
                        boosted = min(old_score + 0.10, 0.95)
                        header[fname] = boosted
                        evidence_flags[fname] = (
                            evidence_flags.get(fname, "") + f" ocr_confirmed"
                        ).strip()

        # 3. Evidence snippets: field-level snippet present → add small confidence credit
        for fname, snippet in evidence_snippets.items():
            if fname in header and snippet and len(str(snippet).strip()) >= 2:
                old_score = header[fname]
                if old_score < 0.90:
                    header[fname] = min(old_score + 0.05, 0.90)
                    evidence_flags[fname] = (
                        evidence_flags.get(fname, "") + " snippet_present"
                    ).strip()

        # Summarize weakest critical field and low-confidence list
        low_conf = [f for f, s in header.items() if s < 0.6]
        worst_crit_name = ""
        worst_crit_score = 1.0
        for cf in CRITICAL_FIELDS:
            s = header.get(cf, 1.0)
            if s < worst_crit_score:
                worst_crit_score = s
                worst_crit_name = cf

        return FieldConfidenceResult(
            header=header,
            lines=line_confidences,
            weakest_critical_field=worst_crit_name,
            weakest_critical_score=worst_crit_score,
            low_confidence_fields=low_conf,
            evidence_flags=evidence_flags,
        )

    @staticmethod
    def _score_line(li, raw_li: dict, repair_actions: List[str], line_num: int) -> Dict[str, float]:
        lc: Dict[str, float] = {}

        lc["description"] = 1.0 if li.description else 0.0
        lc["line_amount"] = 1.0 if li.line_amount is not None else 0.0
        lc["quantity"] = 1.0 if li.quantity is not None else 0.5
        lc["unit_price"] = 1.0 if li.unit_price is not None else 0.5
        lc["tax_percentage"] = 1.0 if li.tax_percentage is not None else 0.5
        lc["tax_amount"] = 1.0 if li.tax_amount is not None else 0.5

        # Line math check: qty × unit_price ≈ line_amount
        if li.quantity is not None and li.unit_price is not None and li.line_amount is not None:
            try:
                computed = (li.quantity * li.unit_price).quantize(Decimal("0.01"))
                expected = li.line_amount.quantize(Decimal("0.01"))
                if expected > Decimal("0"):
                    ratio = abs(computed - expected) / expected
                    if ratio <= Decimal("0.02"):
                        lc["line_math"] = 1.0
                    elif ratio <= Decimal("0.10"):
                        lc["line_math"] = 0.65
                    else:
                        lc["line_math"] = 0.35
                else:
                    lc["line_math"] = 0.7  # zero expected — indeterminate
            except (InvalidOperation, ZeroDivisionError):
                lc["line_math"] = 0.7
        else:
            lc["line_math"] = 0.7  # not enough data to verify

        # Was this line affected by a repair action?
        line_tag = f"line_{line_num}"
        if any(line_tag in a for a in repair_actions):
            for k in ("line_amount", "tax_amount"):
                if lc.get(k, 0) > 0.5:
                    lc[k] = min(lc[k], 0.72)

        return lc

    @staticmethod
    def _repaired_field_set(repair_actions: List[str]) -> set:
        """Map repair action strings to the set of affected field names."""
        affected = set()
        for action in repair_actions:
            for field_name, patterns in _REPAIR_FIELD_MAP.items():
                if any(p in action for p in patterns):
                    affected.add(field_name)
        return affected

    @staticmethod
    def to_serializable(result: FieldConfidenceResult) -> dict:
        """Convert FieldConfidenceResult to a JSON-serializable dict for raw_response storage."""
        return {
            "header": result.header,
            "lines": result.lines,
            "weakest_critical_field": result.weakest_critical_field,
            "weakest_critical_score": result.weakest_critical_score,
            "low_confidence_fields": result.low_confidence_fields,
            "evidence_flags": result.evidence_flags,
        }
