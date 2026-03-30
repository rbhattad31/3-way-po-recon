"""Hard reconciliation validator — deterministic math checks on extracted invoice data.

Complements ExtractionConfidenceScorer (which produces a scalar score) by surfacing
*structured* issues that explain *why* a value is suspicious.  The scorer remains
unchanged; this validator is additive.

Checks (run only when sufficient data is present):
  1. TOTAL_CHECK         subtotal + tax_amount ≈ total_amount          (2% tol)  ERROR
  2. LINE_SUM_CHECK      Σ line_amounts ≈ subtotal                     (5% tol)  WARNING
  3. LINE_MATH_CHECK     qty × unit_price ≈ line_amount per line       (2% tol)  WARNING
  4. TAX_BREAKDOWN_CHECK sum(cgst+sgst+igst+vat) ≈ tax_amount          (abs 0.5) WARNING
  5. TAX_PCT_CHECK       (tax_amount / subtotal) × 100 ≈ tax_percentage (1% tol)  INFO
  6. LINE_TAX_SUM_CHECK  Σ line.tax_amounts ≈ tax_amount               (5% tol)  INFO
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

logger = logging.getLogger(__name__)

# Tolerances
_TOL_TOTAL = Decimal("0.02")       # 2%
_TOL_LINE_SUM = Decimal("0.05")    # 5%
_TOL_LINE_MATH = Decimal("0.02")   # 2%
_ABS_TOL_TAX_BD = Decimal("0.50")  # absolute 0.50 for tax breakdown
_TOL_TAX_PCT = Decimal("0.01")     # 1 percentage-point difference
_TOL_LINE_TAX = Decimal("0.05")    # 5%


@dataclass
class ReconciliationIssue:
    check_name: str
    issue_code: str          # e.g. "TOTAL_MISMATCH"
    severity: str            # "ERROR" | "WARNING" | "INFO"
    message: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    delta: Optional[str] = None


@dataclass
class ReconciliationValidationResult:
    is_clean: bool = True            # True only if no ERROR issues
    has_warnings: bool = False
    issues: List[ReconciliationIssue] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def errors(self) -> List[ReconciliationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> List[ReconciliationIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    def _add(self, issue: ReconciliationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "ERROR":
            self.is_clean = False
        if issue.severity in ("ERROR", "WARNING"):
            self.has_warnings = True


class ReconciliationValidatorService:
    """Run hard math checks on a NormalizedInvoice.  Fail-silent."""

    @staticmethod
    def validate(normalized) -> ReconciliationValidationResult:
        try:
            return ReconciliationValidatorService._validate(normalized)
        except Exception:
            logger.exception("ReconciliationValidatorService.validate failed — returning empty result")
            return ReconciliationValidationResult()

    @staticmethod
    def _validate(normalized) -> ReconciliationValidationResult:
        result = ReconciliationValidationResult()

        subtotal = normalized.subtotal
        tax_amount = normalized.tax_amount
        total_amount = normalized.total_amount
        tax_percentage = normalized.tax_percentage
        tax_breakdown = normalized.tax_breakdown or {}
        line_items = normalized.line_items or []

        # ── Check 1: subtotal + tax ≈ total ───────────────────────────────────
        if subtotal is not None and tax_amount is not None and total_amount is not None:
            result.checks_run += 1
            computed_total = subtotal + tax_amount
            if total_amount > Decimal("0"):
                delta_pct = abs(computed_total - total_amount) / total_amount
                if delta_pct > _TOL_TOTAL:
                    result._add(ReconciliationIssue(
                        check_name="TOTAL_CHECK",
                        issue_code="TOTAL_MISMATCH",
                        severity="ERROR",
                        message=(
                            f"subtotal ({subtotal}) + tax ({tax_amount}) = {computed_total} "
                            f"≠ total ({total_amount}); delta {delta_pct:.1%}"
                        ),
                        expected=str(computed_total),
                        actual=str(total_amount),
                        delta=f"{delta_pct:.1%}",
                    ))
                else:
                    result.checks_passed += 1

        # ── Check 2: Σ line_amounts ≈ subtotal ───────────────────────────────
        if subtotal is not None and line_items:
            line_amounts = [li.line_amount for li in line_items if li.line_amount is not None]
            if line_amounts:
                result.checks_run += 1
                line_sum = sum(line_amounts, Decimal("0"))
                if subtotal > Decimal("0"):
                    delta_pct = abs(line_sum - subtotal) / subtotal
                    if delta_pct > _TOL_LINE_SUM:
                        result._add(ReconciliationIssue(
                            check_name="LINE_SUM_CHECK",
                            issue_code="LINE_SUM_MISMATCH",
                            severity="WARNING",
                            message=(
                                f"Σ line_amounts ({line_sum}) ≠ subtotal ({subtotal}); "
                                f"delta {delta_pct:.1%}"
                            ),
                            expected=str(subtotal),
                            actual=str(line_sum),
                            delta=f"{delta_pct:.1%}",
                        ))
                    else:
                        result.checks_passed += 1

        # ── Check 3: qty × unit_price ≈ line_amount per line ─────────────────
        for li in line_items:
            if li.quantity is not None and li.unit_price is not None and li.line_amount is not None:
                try:
                    result.checks_run += 1
                    computed = (li.quantity * li.unit_price).quantize(Decimal("0.0001"))
                    expected = li.line_amount
                    if expected > Decimal("0"):
                        delta_pct = abs(computed - expected) / expected
                        if delta_pct > _TOL_LINE_MATH:
                            result._add(ReconciliationIssue(
                                check_name="LINE_MATH_CHECK",
                                issue_code="LINE_MATH_MISMATCH",
                                severity="WARNING",
                                message=(
                                    f"Line {li.line_number}: qty ({li.quantity}) × "
                                    f"unit_price ({li.unit_price}) = {computed} "
                                    f"≠ line_amount ({expected}); delta {delta_pct:.1%}"
                                ),
                                expected=str(computed),
                                actual=str(expected),
                                delta=f"{delta_pct:.1%}",
                            ))
                        else:
                            result.checks_passed += 1
                except (InvalidOperation, ZeroDivisionError):
                    pass

        # ── Check 4: sum(tax_breakdown) ≈ tax_amount ─────────────────────────
        if tax_amount is not None and tax_breakdown:
            try:
                result.checks_run += 1
                bd_sum = Decimal(str(
                    sum(float(tax_breakdown.get(k, 0) or 0) for k in ("cgst", "sgst", "igst", "vat"))
                )).quantize(Decimal("0.01"))
                # Only check if at least one breakdown component is non-zero
                if bd_sum > Decimal("0"):
                    delta_abs = abs(bd_sum - tax_amount)
                    if delta_abs > _ABS_TOL_TAX_BD:
                        result._add(ReconciliationIssue(
                            check_name="TAX_BREAKDOWN_CHECK",
                            issue_code="TAX_BREAKDOWN_MISMATCH",
                            severity="WARNING",
                            message=(
                                f"sum(tax_breakdown)={bd_sum} ≠ tax_amount={tax_amount}; "
                                f"abs delta={delta_abs}"
                            ),
                            expected=str(tax_amount),
                            actual=str(bd_sum),
                            delta=str(delta_abs),
                        ))
                    else:
                        result.checks_passed += 1
                else:
                    result.checks_run -= 1  # all-zero breakdown; skip
            except (InvalidOperation, TypeError, ValueError):
                result.checks_run -= 1

        # ── Check 5: tax_percentage consistency ──────────────────────────────
        if tax_percentage is not None and subtotal is not None and tax_amount is not None:
            if subtotal > Decimal("0"):
                try:
                    result.checks_run += 1
                    computed_pct = (tax_amount / subtotal * Decimal("100")).quantize(Decimal("0.01"))
                    delta_pct = abs(computed_pct - tax_percentage)
                    if delta_pct > _TOL_TAX_PCT * Decimal("100"):
                        result._add(ReconciliationIssue(
                            check_name="TAX_PCT_CHECK",
                            issue_code="TAX_PCT_INCONSISTENT",
                            severity="INFO",
                            message=(
                                f"Stated tax_percentage ({tax_percentage}%) ≠ "
                                f"computed (tax/subtotal × 100 = {computed_pct}%); "
                                f"delta {delta_pct}pp"
                            ),
                            expected=str(computed_pct),
                            actual=str(tax_percentage),
                            delta=str(delta_pct),
                        ))
                    else:
                        result.checks_passed += 1
                except (InvalidOperation, ZeroDivisionError):
                    result.checks_run -= 1

        # ── Check 6: Σ line.tax_amounts ≈ tax_amount ─────────────────────────
        if tax_amount is not None and line_items:
            line_taxes = [li.tax_amount for li in line_items if li.tax_amount is not None]
            if line_taxes:
                try:
                    result.checks_run += 1
                    line_tax_sum = sum(line_taxes, Decimal("0"))
                    if tax_amount > Decimal("0") and line_tax_sum > Decimal("0"):
                        delta_pct = abs(line_tax_sum - tax_amount) / tax_amount
                        if delta_pct > _TOL_LINE_TAX:
                            result._add(ReconciliationIssue(
                                check_name="LINE_TAX_SUM_CHECK",
                                issue_code="LINE_TAX_SUM_MISMATCH",
                                severity="INFO",
                                message=(
                                    f"Σ line.tax_amounts ({line_tax_sum}) ≠ "
                                    f"header tax_amount ({tax_amount}); delta {delta_pct:.1%}"
                                ),
                                expected=str(tax_amount),
                                actual=str(line_tax_sum),
                                delta=f"{delta_pct:.1%}",
                            ))
                        else:
                            result.checks_passed += 1
                    else:
                        result.checks_run -= 1
                except (InvalidOperation, ZeroDivisionError):
                    result.checks_run -= 1

        logger.info(
            "ReconciliationValidator: checks_run=%d passed=%d issues=%d (errors=%d warnings=%d)",
            result.checks_run, result.checks_passed, len(result.issues),
            len(result.errors), len(result.warnings),
        )
        return result

    @staticmethod
    def to_serializable(result: ReconciliationValidationResult) -> dict:
        """Convert result to a JSON-serializable dict for raw_response storage."""
        return {
            "is_clean": result.is_clean,
            "has_warnings": result.has_warnings,
            "checks_run": result.checks_run,
            "checks_passed": result.checks_passed,
            "issues": [
                {
                    "check_name": i.check_name,
                    "issue_code": i.issue_code,
                    "severity": i.severity,
                    "message": i.message,
                    "expected": i.expected,
                    "actual": i.actual,
                    "delta": i.delta,
                }
                for i in result.issues
            ],
        }
