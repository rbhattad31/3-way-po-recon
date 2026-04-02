"""
NonPOValidationService — deterministic validation checks for non-PO invoices.

Runs vendor validation, duplicate checks, field completeness, policy compliance,
tax reasonability, and budget checks. Results persisted as APCaseArtifacts.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from django.db.models import Q
from django.utils import timezone

from apps.cases.models import APCase, APCaseArtifact, APCaseDecision
from apps.core.enums import ArtifactType, DecisionSource, DecisionType

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    check_name: str
    status: str  # PASS, FAIL, WARNING, SKIPPED
    message: str
    details: Dict = field(default_factory=dict)


@dataclass
class NonPOValidationResult:
    checks: Dict[str, CheckResult]
    overall_status: str  # PASS, FAIL, NEEDS_REVIEW
    approval_ready: bool = False
    issues: List[str] = field(default_factory=list)
    risk_score: float = 0.0


class NonPOValidationService:

    @staticmethod
    def validate(case: APCase) -> NonPOValidationResult:
        """Run all non-PO deterministic validation checks."""
        invoice = case.invoice
        checks = {}

        checks["vendor"] = NonPOValidationService._check_vendor(invoice)
        checks["duplicate"] = NonPOValidationService._check_duplicates(invoice)
        checks["mandatory_fields"] = NonPOValidationService._check_mandatory_fields(invoice)
        checks["supporting_documents"] = NonPOValidationService._check_supporting_documents(case)
        checks["spend_category"] = NonPOValidationService._classify_spend_category(invoice)
        checks["policy"] = NonPOValidationService._check_policies(invoice, case)
        checks["cost_center"] = NonPOValidationService._infer_cost_center(invoice)
        checks["tax"] = NonPOValidationService._check_tax_reasonability(invoice, case)
        checks["budget"] = NonPOValidationService._check_budget(invoice, case)

        # Compute overall status
        issues = [c.message for c in checks.values() if c.status == "FAIL"]
        warnings = [c.message for c in checks.values() if c.status == "WARNING"]
        fail_count = sum(1 for c in checks.values() if c.status == "FAIL")

        if fail_count > 0:
            overall_status = "FAIL"
        elif warnings:
            overall_status = "NEEDS_REVIEW"
        else:
            overall_status = "PASS"

        risk_score = min(1.0, fail_count * 0.25 + len(warnings) * 0.1)
        approval_ready = overall_status == "PASS"

        result = NonPOValidationResult(
            checks=checks,
            overall_status=overall_status,
            approval_ready=approval_ready,
            issues=issues,
            risk_score=risk_score,
        )

        # Persist as artifact
        NonPOValidationService._persist_result(case, result)

        return result

    @staticmethod
    def _check_vendor(invoice) -> CheckResult:
        """Validate vendor exists and is active."""
        if not invoice.vendor:
            return CheckResult(
                check_name="vendor",
                status="FAIL",
                message="Vendor not linked to invoice",
            )
        vendor = invoice.vendor
        if hasattr(vendor, "is_active") and not vendor.is_active:
            return CheckResult(
                check_name="vendor",
                status="FAIL",
                message=f"Vendor {vendor.name} is inactive",
                details={"vendor_id": vendor.id, "vendor_name": vendor.name},
            )
        return CheckResult(
            check_name="vendor",
            status="PASS",
            message=f"Vendor {vendor.name} is valid and active",
            details={"vendor_id": vendor.id},
        )

    @staticmethod
    def _check_duplicates(invoice) -> CheckResult:
        """Check for duplicate invoices by number + vendor + amount."""
        from apps.documents.models import Invoice

        ninety_days_ago = timezone.now().date() - timezone.timedelta(days=90)
        dupes = Invoice.objects.filter(
            ~Q(id=invoice.id),
            normalized_invoice_number=invoice.normalized_invoice_number,
            vendor=invoice.vendor,
        ).filter(
            Q(invoice_date__gte=ninety_days_ago) | Q(invoice_date__isnull=True)
        )

        if dupes.exists():
            dupe_list = list(dupes.values_list("invoice_number", flat=True)[:5])
            return CheckResult(
                check_name="duplicate",
                status="FAIL",
                message=f"Potential duplicate invoices found: {', '.join(dupe_list)}",
                details={"duplicate_invoice_numbers": dupe_list, "count": dupes.count()},
            )

        # Also check by amount + date proximity
        if invoice.total_amount and invoice.vendor:
            amount_dupes = Invoice.objects.filter(
                ~Q(id=invoice.id),
                vendor=invoice.vendor,
                total_amount=invoice.total_amount,
                invoice_date=invoice.invoice_date,
            )
            if amount_dupes.exists():
                return CheckResult(
                    check_name="duplicate",
                    status="WARNING",
                    message="Invoice with same vendor, amount, and date exists",
                    details={"count": amount_dupes.count()},
                )

        return CheckResult(
            check_name="duplicate",
            status="PASS",
            message="No duplicate invoices detected",
        )

    @staticmethod
    def _check_mandatory_fields(invoice) -> CheckResult:
        """Check that critical invoice fields are present."""
        missing = []
        if not invoice.invoice_number:
            missing.append("invoice_number")
        if not invoice.invoice_date:
            missing.append("invoice_date")
        if not invoice.vendor:
            missing.append("vendor")
        if not invoice.total_amount:
            missing.append("total_amount")
        if not invoice.currency:
            missing.append("currency")

        if missing:
            return CheckResult(
                check_name="mandatory_fields",
                status="FAIL",
                message=f"Missing mandatory fields: {', '.join(missing)}",
                details={"missing_fields": missing},
            )
        return CheckResult(
            check_name="mandatory_fields",
            status="PASS",
            message="All mandatory fields present",
        )

    @staticmethod
    def _check_supporting_documents(case: APCase) -> CheckResult:
        """Check supporting document completeness based on amount threshold."""
        amount = case.invoice.total_amount or Decimal("0")
        required_docs = []
        if amount >= 5000:
            required_docs.append("receipt_or_delivery_note")
        if amount >= 25000:
            required_docs.append("contract_reference")
        if amount >= 50000:
            required_docs.append("approval_email")

        if not required_docs:
            return CheckResult(
                check_name="supporting_documents",
                status="PASS",
                message="No supporting documents required for this amount",
            )

        # For now, supporting docs check is a stub — will integrate with DocumentUpload
        return CheckResult(
            check_name="supporting_documents",
            status="WARNING",
            message=f"Supporting documents required: {', '.join(required_docs)}",
            details={"required_documents": required_docs, "amount": str(amount)},
        )

    @staticmethod
    def _classify_spend_category(invoice) -> CheckResult:
        """Infer spend category from line descriptions."""
        from apps.documents.models import InvoiceLineItem

        lines = InvoiceLineItem.objects.filter(invoice=invoice)
        if not lines.exists():
            return CheckResult(
                check_name="spend_category",
                status="WARNING",
                message="No line items to classify",
            )

        descriptions = " ".join(
            (line.description or line.raw_description or "") for line in lines
        ).lower()

        categories = {
            "TRAVEL": {"travel", "hotel", "flight", "taxi", "uber"},
            "UTILITIES": {"electricity", "water", "gas", "internet", "telecom"},
            "MAINTENANCE": {"maintenance", "repair", "cleaning", "service"},
            "CONSULTING": {"consulting", "advisory", "professional"},
            "SUPPLIES": {"supplies", "stationery", "office"},
        }

        matched_category = "GENERAL"
        for cat, keywords in categories.items():
            if any(kw in descriptions for kw in keywords):
                matched_category = cat
                break

        return CheckResult(
            check_name="spend_category",
            status="PASS",
            message=f"Classified as {matched_category}",
            details={"category": matched_category},
        )

    # Currency-aware approval thresholds (finance manager / VP).
    # Values are in the invoice's own currency.
    _POLICY_THRESHOLDS: dict = {
        "USD": (100_000, 250_000),
        "EUR": (100_000, 250_000),
        "GBP": (80_000, 200_000),
        "SAR": (375_000, 940_000),
        "AED": (370_000, 920_000),
        "INR": (8_500_000, 21_000_000),
        "JPY": (15_000_000, 37_500_000),
    }
    _DEFAULT_THRESHOLDS: tuple = (100_000, 250_000)

    @classmethod
    def _check_policies(cls, invoice, case: APCase) -> CheckResult:
        """Check business rules and policy compliance."""
        amount = invoice.total_amount or Decimal("0")
        currency = (getattr(invoice, "currency", "") or "USD").upper()
        fm_limit, vp_limit = cls._POLICY_THRESHOLDS.get(
            currency, cls._DEFAULT_THRESHOLDS,
        )
        issues = []

        # Amount threshold checks (currency-aware)
        if amount > fm_limit:
            issues.append(
                f"Amount exceeds {currency} {fm_limit:,.0f}"
                " -- requires finance manager approval"
            )
        if amount > vp_limit:
            issues.append(
                f"Amount exceeds {currency} {vp_limit:,.0f}"
                " -- requires VP approval"
            )

        if issues:
            return CheckResult(
                check_name="policy",
                status="WARNING",
                message="; ".join(issues),
                details={"amount": str(amount), "currency": currency, "issues": issues},
            )
        return CheckResult(
            check_name="policy",
            status="PASS",
            message="Within standard policy thresholds",
        )

    @staticmethod
    def _infer_cost_center(invoice) -> CheckResult:
        """Attempt to infer cost center from vendor history or line items."""
        # Stub — would query historical invoices for same vendor
        return CheckResult(
            check_name="cost_center",
            status="SKIPPED",
            message="Cost center inference not yet implemented",
        )

    @staticmethod
    def _check_tax_reasonability(invoice, case=None) -> CheckResult:
        """Validate tax at line-item level against PO lines if available."""
        subtotal = invoice.subtotal or Decimal("0")
        tax = invoice.tax_amount or Decimal("0")

        if subtotal == 0:
            return CheckResult(
                check_name="tax",
                status="WARNING",
                message="Cannot verify tax \u2014 subtotal is zero",
            )

        header_rate = (tax / subtotal * 100) if subtotal else Decimal("0")

        # Try line-level comparison against the linked Purchase Order
        po = getattr(case, "purchase_order", None) if case else None
        if po:
            inv_lines = list(invoice.line_items.order_by("line_number"))
            po_lines = list(po.line_items.order_by("line_number"))

            if inv_lines and po_lines:
                # Build a map of PO lines by line_number for quick lookup
                po_line_map = {pl.line_number: pl for pl in po_lines}
                mismatches = []

                for inv_line in inv_lines:
                    inv_tax = inv_line.tax_amount
                    inv_amt = inv_line.line_amount
                    if inv_amt is None or inv_amt == 0:
                        continue

                    inv_rate = ((inv_tax or Decimal("0")) / inv_amt * 100)

                    # Match PO line by line_number
                    po_line = po_line_map.get(inv_line.line_number)
                    if po_line is None:
                        continue

                    po_tax = po_line.tax_amount
                    po_amt = po_line.line_amount
                    if po_amt is None or po_amt == 0:
                        continue

                    po_rate = ((po_tax or Decimal("0")) / po_amt * 100)
                    variance = abs(inv_rate - po_rate)

                    if variance > Decimal("3.0"):
                        mismatches.append({
                            "line": inv_line.line_number,
                            "inv_rate": str(inv_rate.quantize(Decimal("0.1"))),
                            "po_rate": str(po_rate.quantize(Decimal("0.1"))),
                            "variance": str(variance.quantize(Decimal("0.1"))),
                        })

                if mismatches:
                    lines_str = ", ".join(
                        f"Line {m['line']}: {m['inv_rate']}% vs PO {m['po_rate']}%"
                        for m in mismatches
                    )
                    return CheckResult(
                        check_name="tax",
                        status="WARNING",
                        message=f"Tax rate mismatch on {len(mismatches)} line(s): {lines_str}",
                        details={"mismatches": mismatches},
                    )

                return CheckResult(
                    check_name="tax",
                    status="PASS",
                    message=f"Tax rates match PO at line level (header rate {header_rate:.1f}%)",
                    details={"header_rate": str(header_rate)},
                )

        # No PO available — any tax rate is acceptable
        return CheckResult(
            check_name="tax",
            status="PASS",
            message=f"Tax rate {header_rate:.1f}% (no PO to compare against)",
            details={"header_rate": str(header_rate)},
        )

    @staticmethod
    def _check_budget(invoice, case: APCase) -> CheckResult:
        """Check budget availability — stub for future integration."""
        return CheckResult(
            check_name="budget",
            status="SKIPPED",
            message="Budget check not yet integrated",
        )

    @staticmethod
    def _persist_result(case: APCase, result: NonPOValidationResult) -> None:
        """Persist validation result as a case artifact."""
        payload = {
            "overall_status": result.overall_status,
            "approval_ready": result.approval_ready,
            "risk_score": result.risk_score,
            "issues": result.issues,
            "checks": {
                name: {
                    "status": check.status,
                    "message": check.message,
                    "details": check.details,
                }
                for name, check in result.checks.items()
            },
        }

        APCaseArtifact.objects.create(
            case=case,
            artifact_type=ArtifactType.VALIDATION_RESULT,
            payload=payload,
            version=(
                APCaseArtifact.objects.filter(
                    case=case, artifact_type=ArtifactType.VALIDATION_RESULT
                ).order_by("-version").values_list("version", flat=True).first() or 0
            ) + 1,
        )

        # Record decision
        APCaseDecision.objects.create(
            case=case,
            decision_type=DecisionType.MATCH_DETERMINED,
            decision_source=DecisionSource.DETERMINISTIC,
            decision_value=result.overall_status,
            rationale=f"Non-PO validation: {result.overall_status} ({len(result.issues)} issues)",
            evidence=payload,
        )

        # Update case risk score
        case.risk_score = result.risk_score
        case.duplicate_risk_flag = result.checks.get("duplicate", CheckResult("", "PASS", "")).status == "FAIL"
        case.save(update_fields=["risk_score", "duplicate_risk_flag", "updated_at"])
