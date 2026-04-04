"""Extraction decision codes — machine-readable vocabulary for failure / routing reasons.

Used by:
  - ValidationService (critical field failures)
  - ReconciliationValidatorService (math check failures)
  - RecoveryLaneService (trigger policy + outcome)
  - ReviewRoutingEngine (queue assignment logic)
  - ExtractionResult.raw_response["_decision_codes"] (persistence)
  - ExecutionContext (UI / console surfacing)

Design rules:
  - Codes are UPPERCASE_SNAKE_CASE strings.
  - Codes are additive alongside human-readable messages — never replace them.
  - Each code maps to a canonical review queue recommendation (see ROUTING_MAP).
  - New codes must be added here first, then referenced by services.
"""
from __future__ import annotations

# ── Extraction failures ───────────────────────────────────────────────────────

INV_NUM_UNRECOVERABLE       = "INV_NUM_UNRECOVERABLE"
"""Invoice number is absent or has near-zero field confidence — recovery required."""

TOTAL_MISMATCH_HARD         = "TOTAL_MISMATCH_HARD"
"""subtotal + tax ≠ total_amount beyond 2% tolerance — hard math error."""

LINE_SUM_MISMATCH           = "LINE_SUM_MISMATCH"
"""Σ line_amounts ≠ subtotal beyond 5% tolerance."""

LINE_TABLE_INCOMPLETE       = "LINE_TABLE_INCOMPLETE"
"""More than half of extracted lines are missing a line_amount."""

LINE_AMOUNT_SUSPECT         = "LINE_AMOUNT_SUSPECT"
"""Line amounts diverge from header subtotal -- possible OCR table misread
or tax-inclusive line amounts. Repair guard kept the header value."""

TAX_ALLOC_AMBIGUOUS         = "TAX_ALLOC_AMBIGUOUS"
"""Tax breakdown present but sum does not reconcile with tax_amount."""

TAX_BREAKDOWN_MISMATCH      = "TAX_BREAKDOWN_MISMATCH"
"""sum(cgst+sgst+igst+vat) ≠ tax_amount."""

VENDOR_MATCH_LOW            = "VENDOR_MATCH_LOW"
"""Vendor name field confidence is very low (< 0.40)."""

LOW_CONFIDENCE_CRITICAL_FIELD = "LOW_CONFIDENCE_CRITICAL_FIELD"
"""At least one critical field (invoice_number, vendor_name, invoice_date,
currency, total_amount) has field_confidence below the critical threshold (0.60)."""

# ── Prompt / composition ──────────────────────────────────────────────────────

PROMPT_COMPOSITION_FALLBACK_USED = "PROMPT_COMPOSITION_FALLBACK_USED"
"""InvoicePromptComposer fell back to monolithic extraction.invoice_system prompt."""

PROMPT_SOURCE_AGENT_DEFAULT = "PROMPT_SOURCE_AGENT_DEFAULT"
"""Extraction agent used its own system_prompt property (composer unavailable)."""

# ── Recovery lane ─────────────────────────────────────────────────────────────

RECOVERY_LANE_INVOKED       = "RECOVERY_LANE_INVOKED"
"""InvoiceUnderstandingAgent was invoked as a recovery step."""

RECOVERY_LANE_SUCCEEDED     = "RECOVERY_LANE_SUCCEEDED"
"""Recovery lane produced at least one recovered field or routing suggestion."""

RECOVERY_LANE_FAILED        = "RECOVERY_LANE_FAILED"
"""Recovery lane was invoked but produced no useful output."""

RECOVERY_NOT_APPLICABLE     = "RECOVERY_NOT_APPLICABLE"
"""Named failure mode detected but recovery lane was suppressed (already valid / finalized)."""

# ── QR / e-invoice ───────────────────────────────────────────────────────────

QR_DATA_VERIFIED            = "QR_DATA_VERIFIED"
"""Indian e-invoice QR decoded successfully; key fields (invoice_number, total,
vendor GSTIN) confirmed against extracted values."""

QR_MISMATCH                 = "QR_MISMATCH"
"""At least one QR-verified field conflicts with the LLM-extracted value beyond
the allowed tolerance — likely OCR/extraction error; requires human review."""

QR_IRN_PRESENT              = "QR_IRN_PRESENT"
"""IRN (Invoice Reference Number) is available in the QR payload — enables
deterministic duplicate detection by IRN in addition to fuzzy invoice-number
matching."""

IRN_DUPLICATE               = "IRN_DUPLICATE"
"""Same IRN seen on a previously processed invoice — hard duplicate, must be
rejected or escalated."""

# ── Review routing ────────────────────────────────────────────────────────────

ROUTE_EXCEPTION_OPS         = "ROUTE_EXCEPTION_OPS"
ROUTE_TAX_REVIEW            = "ROUTE_TAX_REVIEW"
ROUTE_VENDOR_OPS            = "ROUTE_VENDOR_OPS"
ROUTE_AP_REVIEW             = "ROUTE_AP_REVIEW"


# ── Canonical routing map  ────────────────────────────────────────────────────
# Maps decision code → preferred review queue (string).
# ReviewRoutingEngine consults this to assign queues from decision codes.
# Uses the ReviewQueue enum values as strings to avoid circular import.

ROUTING_MAP: dict[str, str] = {
    INV_NUM_UNRECOVERABLE:          "EXCEPTION_OPS",
    TOTAL_MISMATCH_HARD:            "EXCEPTION_OPS",
    LINE_TABLE_INCOMPLETE:          "EXCEPTION_OPS",
    IRN_DUPLICATE:                  "EXCEPTION_OPS",
    TAX_ALLOC_AMBIGUOUS:            "TAX_REVIEW",
    TAX_BREAKDOWN_MISMATCH:         "TAX_REVIEW",
    VENDOR_MATCH_LOW:               "MASTER_DATA_REVIEW",
    QR_MISMATCH:                    "AP_REVIEW",
    LOW_CONFIDENCE_CRITICAL_FIELD:  "AP_REVIEW",
    LINE_SUM_MISMATCH:              "AP_REVIEW",
    LINE_AMOUNT_SUSPECT:            "AP_REVIEW",
    PROMPT_COMPOSITION_FALLBACK_USED: "AP_REVIEW",
}

# Codes that always require human review (cannot be auto-approved)
HARD_REVIEW_CODES: frozenset[str] = frozenset({
    INV_NUM_UNRECOVERABLE,
    TOTAL_MISMATCH_HARD,
    LINE_TABLE_INCOMPLETE,
    IRN_DUPLICATE,
    QR_MISMATCH,
})


def derive_codes(
    validation_result=None,
    recon_val_result=None,
    field_conf_result=None,
    prompt_source_type: str = "",
    qr_data=None,
) -> list[str]:
    """Derive the full list of applicable decision codes from pipeline outputs.

    Arguments are all optional — pass whatever is available.
    Fail-silent: any exception returns an empty list.

    Args:
        validation_result:   ValidationResult from ValidationService.
        recon_val_result:    ReconciliationValidationResult from ReconciliationValidatorService.
        field_conf_result:   FieldConfidenceResult from FieldConfidenceService.
        prompt_source_type:  Prompt source type string from _prompt_meta.
        qr_data:             Optional QRInvoiceData from QRCodeDecoderService.
    """
    try:
        return _derive(validation_result, recon_val_result, field_conf_result, prompt_source_type, qr_data)
    except Exception:
        import logging as _log
        _log.getLogger(__name__).exception("decision_codes.derive_codes failed")
        return []


def _derive(validation_result, recon_val_result, field_conf_result, prompt_source_type: str, qr_data=None, repair_metadata=None) -> list[str]:
    codes: list[str] = []

    # ── From ValidationResult ─────────────────────────────────────────────────
    if validation_result is not None:
        if getattr(validation_result, "critical_failures", []):
            codes.append(LOW_CONFIDENCE_CRITICAL_FIELD)
            # Drill into specific critical failures
            crit = validation_result.critical_failures
            if "invoice_number" in crit:
                codes.append(INV_NUM_UNRECOVERABLE)
            if "vendor_name" in crit:
                codes.append(VENDOR_MATCH_LOW)

    # ── From ReconciliationValidationResult ───────────────────────────────────
    if recon_val_result is not None:
        for issue in recon_val_result.issues:
            ic = issue.issue_code
            if ic == "TOTAL_MISMATCH":
                codes.append(TOTAL_MISMATCH_HARD)
            elif ic == "LINE_SUM_MISMATCH":
                codes.append(LINE_SUM_MISMATCH)
            elif ic == "TAX_BREAKDOWN_MISMATCH":
                codes.append(TAX_BREAKDOWN_MISMATCH)
                codes.append(TAX_ALLOC_AMBIGUOUS)
            elif ic == "LINE_MATH_MISMATCH":
                pass  # sub-issue of LINE_TABLE_INCOMPLETE, not separate code

    # ── From FieldConfidenceResult ────────────────────────────────────────────
    if field_conf_result is not None:
        header = getattr(field_conf_result, "header", {})
        # Check vendor specifically (lower threshold = 0.40)
        vendor_score = header.get("vendor_name", 1.0)
        if vendor_score < 0.40:
            if VENDOR_MATCH_LOW not in codes:
                codes.append(VENDOR_MATCH_LOW)
        # Check line table completeness via lines
        lines = getattr(field_conf_result, "lines", [])
        if lines:
            missing_amount = sum(1 for lc in lines if lc.get("line_amount", 1.0) < 0.5)
            if missing_amount > len(lines) / 2:
                codes.append(LINE_TABLE_INCOMPLETE)

    # ── From repair metadata ──────────────────────────────────────────────────
    if repair_metadata is not None:
        warnings = repair_metadata.get("warnings", []) or []
        for w in warnings:
            wl = str(w).lower()
            if "kept header" in wl or "guard" in wl:
                codes.append(LINE_AMOUNT_SUSPECT)
                break

    # ── Prompt source ─────────────────────────────────────────────────────────
    if prompt_source_type in ("monolithic_fallback", "agent_default"):
        codes.append(PROMPT_COMPOSITION_FALLBACK_USED)

    # ── QR / e-invoice ────────────────────────────────────────────────────────
    if qr_data is not None:
        irn = getattr(qr_data, "irn", "") or ""
        if irn:
            codes.append(QR_IRN_PRESENT)

        # evidence_flags from field_conf_result reveal whether QR matched or mismatched
        qr_mismatch = False
        qr_confirmed = False
        if field_conf_result is not None:
            ev_flags = getattr(field_conf_result, "evidence_flags", {}) or {}
            for _flag_val in ev_flags.values():
                if "qr_mismatch" in str(_flag_val):
                    qr_mismatch = True
                if "qr_confirmed" in str(_flag_val):
                    qr_confirmed = True

        if qr_mismatch:
            codes.append(QR_MISMATCH)
        elif qr_confirmed:
            codes.append(QR_DATA_VERIFIED)
        elif irn:
            # QR decoded but confidence service hasn't compared yet (no field_conf_result)
            codes.append(QR_DATA_VERIFIED)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result
