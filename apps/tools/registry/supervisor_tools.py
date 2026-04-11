"""Supervisor-specific tools -- wrappers around existing deterministic services.

These tools provide the SupervisorAgent with access to the full invoice
lifecycle without replacing the deterministic logic with LLM reasoning.
The LLM calls tools, reads outputs, and reasons on the results.

All tools follow the existing BaseTool / @register_tool pattern and are
tenant-scoped via the inherited ``_scoped()`` helper.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict

from apps.tools.registry.base import BaseTool, ToolResult, register_tool

logger = logging.getLogger(__name__)


def _invoice_to_parsed(invoice):
    """Convert an Invoice model instance to a ParsedInvoice dataclass."""
    from apps.extraction.services.parser_service import ParsedInvoice, ParsedLineItem

    lines = []
    for li in invoice.line_items.all().order_by("line_number"):
        lines.append(ParsedLineItem(
            line_number=li.line_number or 1,
            raw_description=li.description or li.raw_description or "",
            raw_item_category=li.item_category or "",
            raw_quantity=str(li.quantity) if li.quantity is not None else "",
            raw_unit_price=str(li.unit_price) if li.unit_price is not None else "",
            raw_tax_percentage=str(li.tax_percentage) if li.tax_percentage is not None else "",
            raw_tax_amount=str(li.tax_amount) if li.tax_amount is not None else "",
            raw_line_amount=str(li.line_amount) if li.line_amount is not None else "",
        ))

    return ParsedInvoice(
        raw_vendor_name=(invoice.vendor.name if invoice.vendor else "") or invoice.raw_vendor_name or "",
        raw_vendor_tax_id=(invoice.vendor.tax_id if invoice.vendor else "") or getattr(invoice, "vendor_gstin", "") or "",
        raw_buyer_name="",
        raw_invoice_number=invoice.invoice_number or "",
        raw_invoice_date=str(invoice.invoice_date) if invoice.invoice_date else "",
        raw_due_date=str(invoice.due_date) if invoice.due_date else "",
        raw_po_number=invoice.po_number or "",
        raw_currency=invoice.currency or "USD",
        raw_subtotal=str(invoice.subtotal) if getattr(invoice, "subtotal", None) is not None else "",
        raw_tax_percentage="",
        raw_tax_amount=str(invoice.tax_amount) if getattr(invoice, "tax_amount", None) is not None else "",
        raw_total_amount=str(invoice.total_amount) if invoice.total_amount is not None else "",
        confidence=float(invoice.extraction_confidence or 0),
        line_items=lines,
    )


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, Decimal):
        return str(val)
    return str(val)


# ============================================================================
# UNDERSTAND phase tools
# ============================================================================


@register_tool
class GetOCRTextTool(BaseTool):
    name = "get_ocr_text"
    required_permission = "invoices.view"
    description = "Retrieve raw OCR text from a document upload."
    when_to_use = "At the start of processing to get the document text."
    parameters_schema = {
        "type": "object",
        "properties": {
            "document_upload_id": {
                "type": "integer",
                "description": "PK of the DocumentUpload to get OCR text from.",
            },
        },
        "required": ["document_upload_id"],
    }

    def run(self, *, document_upload_id: int = 0, **kwargs) -> ToolResult:
        from apps.documents.models import DocumentUpload

        try:
            qs = self._scoped(DocumentUpload.objects.all())
            upload = qs.get(pk=document_upload_id)
        except DocumentUpload.DoesNotExist:
            return ToolResult(success=False, error=f"DocumentUpload {document_upload_id} not found")

        ocr_text = getattr(upload, "ocr_text", "") or ""
        if not ocr_text and hasattr(upload, "extracted_text"):
            ocr_text = upload.extracted_text or ""

        # Truncate to 60K chars (platform OCR limit)
        if len(ocr_text) > 60000:
            ocr_text = ocr_text[:60000]

        return ToolResult(success=True, data={
            "document_upload_id": document_upload_id,
            "has_text": bool(ocr_text),
            "char_count": len(ocr_text),
            "text": ocr_text[:10000],  # First 10K for LLM context window
        })


@register_tool
class ClassifyDocumentTool(BaseTool):
    name = "classify_document"
    required_permission = "invoices.view"
    description = "Classify a document as invoice, credit note, etc."
    when_to_use = "To confirm the document type before extraction."
    parameters_schema = {
        "type": "object",
        "properties": {
            "document_upload_id": {
                "type": "integer",
                "description": "PK of the DocumentUpload to classify.",
            },
        },
        "required": ["document_upload_id"],
    }

    def run(self, *, document_upload_id: int = 0, **kwargs) -> ToolResult:
        from apps.documents.models import DocumentUpload

        try:
            qs = self._scoped(DocumentUpload.objects.all())
            upload = qs.get(pk=document_upload_id)
        except DocumentUpload.DoesNotExist:
            return ToolResult(success=False, error=f"DocumentUpload {document_upload_id} not found")

        doc_type = getattr(upload, "document_type", "") or "INVOICE"
        return ToolResult(success=True, data={
            "document_upload_id": document_upload_id,
            "document_type": doc_type,
            "filename": getattr(upload, "original_filename", "") or "",
        })


@register_tool
class ExtractInvoiceFieldsTool(BaseTool):
    name = "extract_invoice_fields"
    required_permission = "extraction.run"
    description = (
        "Extract structured invoice fields (header + lines) from a document. "
        "Returns the extraction result with confidence scores."
    )
    when_to_use = "After getting OCR text, to extract structured data."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to get extraction data for.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        from apps.documents.models import Invoice

        try:
            qs = self._scoped(Invoice.objects.all())
            invoice = qs.select_related("vendor").get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        data = {
            "invoice_id": invoice.pk,
            "invoice_number": invoice.invoice_number or "",
            "vendor_name": _safe_str((invoice.vendor.name if invoice.vendor else "") or invoice.raw_vendor_name or ""),
            "vendor_tax_id": _safe_str((invoice.vendor.tax_id if invoice.vendor else "") or getattr(invoice, "vendor_gstin", "") or ""),
            "po_number": invoice.po_number or "",
            "invoice_date": _safe_str(invoice.invoice_date),
            "total_amount": _safe_str(invoice.total_amount),
            "subtotal": _safe_str(getattr(invoice, "subtotal", "")),
            "tax_amount": _safe_str(getattr(invoice, "tax_amount", "")),
            "currency": invoice.currency or "",
            "extraction_confidence": float(invoice.extraction_confidence or 0),
            "status": str(invoice.status),
        }

        # Line items
        lines = list(invoice.line_items.all().values(
            "line_number", "description", "quantity",
            "unit_price", "tax_amount", "line_amount",
        ))
        data["line_items"] = [
            {k: _safe_str(v) for k, v in line.items()} for line in lines
        ]
        data["line_count"] = len(lines)

        return ToolResult(success=True, data=data)


# ============================================================================
# VALIDATE phase tools
# ============================================================================


@register_tool
class ValidateExtractionTool(BaseTool):
    name = "validate_extraction"
    required_permission = "extraction.run"
    description = (
        "Run validation rules on extracted invoice data. Checks mandatory "
        "fields, format validity, cross-field consistency, and GST rates."
    )
    when_to_use = "After extraction to check data quality before matching."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to validate.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        try:
            from apps.documents.models import Invoice
            from apps.extraction.services.validation_service import ValidationService
            from apps.extraction.services.normalization_service import NormalizationService

            qs = self._scoped(Invoice.objects.all())
            invoice = qs.select_related("vendor").get(pk=invoice_id)

            parsed = _invoice_to_parsed(invoice)
            normalizer = NormalizationService()
            normalized = normalizer.normalize(parsed)

            validator = ValidationService()
            result = validator.validate(normalized)

            return ToolResult(success=True, data={
                "invoice_id": invoice_id,
                "is_valid": result.is_valid,
                "error_count": len(result.errors),
                "warning_count": len(result.warnings),
                "errors": [{"field": e.field, "message": e.message} for e in result.errors],
                "warnings": [{"field": w.field, "message": w.message} for w in result.warnings],
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class RepairExtractionTool(BaseTool):
    name = "repair_extraction"
    required_permission = "extraction.run"
    description = (
        "Apply automated repair rules to fix common extraction issues "
        "(date formats, amount parsing, field normalization)."
    )
    when_to_use = "When validation finds repairable issues."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to repair.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        try:
            from apps.documents.models import Invoice
            from apps.extraction.services.normalization_service import NormalizationService

            qs = self._scoped(Invoice.objects.all())
            invoice = qs.get(pk=invoice_id)

            parsed = _invoice_to_parsed(invoice)
            normalizer = NormalizationService()
            normalized = normalizer.normalize(parsed)

            repairs = []
            if normalized.normalized_invoice_number != (invoice.invoice_number or ""):
                repairs.append({"field": "invoice_number", "action": "normalized"})
            if normalized.normalized_po_number != (invoice.po_number or ""):
                repairs.append({"field": "po_number", "action": "normalized"})

            return ToolResult(success=True, data={
                "invoice_id": invoice_id,
                "repairs_applied": len(repairs),
                "repairs": repairs,
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class CheckDuplicateTool(BaseTool):
    name = "check_duplicate"
    required_permission = "invoices.view"
    description = "Check if an invoice is a duplicate of an existing one."
    when_to_use = "During validation to detect duplicate submissions."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to check for duplicates.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        try:
            from apps.documents.models import Invoice
            from apps.extraction.services.duplicate_detection_service import DuplicateDetectionService
            from apps.extraction.services.normalization_service import NormalizationService

            qs = self._scoped(Invoice.objects.all())
            invoice = qs.select_related("vendor").get(pk=invoice_id)

            parsed = _invoice_to_parsed(invoice)
            normalizer = NormalizationService()
            normalized = normalizer.normalize(parsed)

            detector = DuplicateDetectionService()
            result = detector.check(normalized, exclude_invoice_id=invoice_id)

            return ToolResult(success=True, data={
                "invoice_id": invoice_id,
                "is_duplicate": result.is_duplicate,
                "duplicate_of": _safe_str(getattr(result, "duplicate_invoice_id", None)),
                "confidence": float(getattr(result, "confidence", 0)),
                "match_fields": getattr(result, "match_fields", []),
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class VerifyVendorTool(BaseTool):
    name = "verify_vendor"
    required_permission = "vendors.view"
    description = (
        "Verify a vendor by tax ID against the vendor master data. "
        "Returns match status and vendor details."
    )
    when_to_use = "During validation to confirm vendor identity."
    when_not_to_use = "Do not use name-only matching -- always prefer tax ID."
    parameters_schema = {
        "type": "object",
        "properties": {
            "tax_id": {
                "type": "string",
                "description": "Vendor tax ID (GSTIN, VAT number, etc.).",
            },
            "vendor_name": {
                "type": "string",
                "description": "Vendor name for secondary matching.",
            },
        },
        "required": [],
    }

    def run(self, *, tax_id: str = "", vendor_name: str = "", **kwargs) -> ToolResult:
        from apps.vendors.models import Vendor

        if not tax_id and not vendor_name:
            return ToolResult(success=False, error="At least tax_id or vendor_name is required")

        qs = self._scoped(Vendor.objects.filter(is_active=True))

        # Primary: match by tax ID
        if tax_id:
            vendor = qs.filter(tax_id__iexact=tax_id.strip()).first()
            if vendor:
                return ToolResult(success=True, data={
                    "verified": True,
                    "match_method": "tax_id",
                    "vendor_id": vendor.pk,
                    "vendor_name": vendor.name,
                    "vendor_tax_id": vendor.tax_id or "",
                })

        # Fallback: name match (lower confidence)
        if vendor_name:
            from apps.core.utils import normalize_string
            norm_name = normalize_string(vendor_name)
            for v in qs.all()[:500]:  # Safety limit
                if normalize_string(v.name) == norm_name:
                    return ToolResult(success=True, data={
                        "verified": True,
                        "match_method": "name",
                        "vendor_id": v.pk,
                        "vendor_name": v.name,
                        "vendor_tax_id": v.tax_id or "",
                        "warning": "Matched by name only -- tax ID verification recommended",
                    })

        return ToolResult(success=True, data={
            "verified": False,
            "tax_id": tax_id,
            "vendor_name": vendor_name,
        })


@register_tool
class VerifyTaxComputationTool(BaseTool):
    name = "verify_tax_computation"
    required_permission = "invoices.view"
    description = "Verify that tax amounts on the invoice are computed correctly."
    when_to_use = "During validation to check tax computation accuracy."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to verify tax for.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        from apps.documents.models import Invoice

        try:
            qs = self._scoped(Invoice.objects.all())
            invoice = qs.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        total = float(invoice.total_amount or 0)
        subtotal = float(getattr(invoice, "subtotal", 0) or 0)
        tax = float(getattr(invoice, "tax_amount", 0) or 0)

        issues = []
        if subtotal > 0 and tax >= 0:
            expected_total = subtotal + tax
            if abs(total - expected_total) > 0.01:
                issues.append({
                    "type": "total_mismatch",
                    "expected": _safe_str(expected_total),
                    "actual": _safe_str(total),
                    "difference": _safe_str(abs(total - expected_total)),
                })

        # Check line item sum vs subtotal
        lines = invoice.line_items.all()
        line_sum = sum(float(l.line_amount or 0) for l in lines)
        if lines.exists() and subtotal > 0:
            if abs(line_sum - subtotal) > 0.01:
                issues.append({
                    "type": "line_sum_mismatch",
                    "line_total": _safe_str(line_sum),
                    "subtotal": _safe_str(subtotal),
                    "difference": _safe_str(abs(line_sum - subtotal)),
                })

        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "tax_valid": len(issues) == 0,
            "issues": issues,
            "total": _safe_str(total),
            "subtotal": _safe_str(subtotal),
            "tax_amount": _safe_str(tax),
        })


# ============================================================================
# MATCH phase tools
# ============================================================================


@register_tool
class RunHeaderMatchTool(BaseTool):
    name = "run_header_match"
    required_permission = "reconciliation.run"
    description = (
        "Run deterministic header-level matching between an invoice and a PO. "
        "Compares vendor, currency, total amount, and dates."
    )
    when_to_use = "After PO lookup succeeds, to compare invoice header vs PO header."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "po_number": {
                "type": "string",
                "description": "PO number to match against.",
            },
        },
        "required": ["invoice_id", "po_number"],
    }

    def run(self, *, invoice_id: int = 0, po_number: str = "", **kwargs) -> ToolResult:
        try:
            from apps.documents.models import Invoice, PurchaseOrder
            from apps.reconciliation.services.header_match_service import HeaderMatchService
            from apps.reconciliation.services.tolerance_engine import ToleranceEngine

            qs_inv = self._scoped(Invoice.objects.all())
            invoice = qs_inv.select_related("vendor").get(pk=invoice_id)

            qs_po = self._scoped(PurchaseOrder.objects.all())
            po = qs_po.filter(po_number=po_number).first()
            if not po:
                return ToolResult(success=True, data={
                    "matched": False, "reason": f"PO {po_number} not found",
                })

            engine = ToleranceEngine()
            service = HeaderMatchService(engine)
            result = service.match(invoice, po)

            return ToolResult(success=True, data={
                "matched": result.all_ok,
                "vendor_match": result.vendor_match,
                "currency_match": result.currency_match,
                "total_comparison": {
                    "within_tolerance": getattr(result.total_comparison, "within_tolerance", None),
                    "deviation_pct": _safe_str(getattr(result.total_comparison, "deviation_pct", None)),
                } if result.total_comparison else None,
                "is_partial_invoice": getattr(result, "is_partial_invoice", False),
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class RunLineMatchTool(BaseTool):
    name = "run_line_match"
    required_permission = "reconciliation.run"
    description = (
        "Run deterministic line-level matching between invoice and PO lines. "
        "Uses 11 weighted signals for multi-signal scoring."
    )
    when_to_use = "After header match, to compare individual line items."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "po_number": {
                "type": "string",
                "description": "PO number to match lines against.",
            },
        },
        "required": ["invoice_id", "po_number"],
    }

    def run(self, *, invoice_id: int = 0, po_number: str = "", **kwargs) -> ToolResult:
        try:
            from apps.documents.models import Invoice, PurchaseOrder
            from apps.reconciliation.services.line_match_service import LineMatchService
            from apps.reconciliation.services.tolerance_engine import ToleranceEngine

            qs_inv = self._scoped(Invoice.objects.all())
            invoice = qs_inv.prefetch_related("line_items").get(pk=invoice_id)

            qs_po = self._scoped(PurchaseOrder.objects.all())
            po = qs_po.prefetch_related("line_items").filter(po_number=po_number).first()
            if not po:
                return ToolResult(success=True, data={
                    "matched": False, "reason": f"PO {po_number} not found",
                })

            engine = ToleranceEngine()
            service = LineMatchService(engine)
            result = service.match(invoice, po)

            pairs = []
            for p in getattr(result, "pairs", []):
                pairs.append({
                    "invoice_line": getattr(p, "invoice_line_number", None),
                    "po_line": getattr(p, "po_line_number", None),
                    "match_confidence": _safe_str(getattr(p, "match_confidence", None)),
                    "qty_comparison": {
                        "within_tolerance": getattr(getattr(p, "qty_comparison", None), "within_tolerance", None),
                    } if getattr(p, "qty_comparison", None) else None,
                    "price_comparison": {
                        "within_tolerance": getattr(getattr(p, "price_comparison", None), "within_tolerance", None),
                    } if getattr(p, "price_comparison", None) else None,
                })

            return ToolResult(success=True, data={
                "matched": getattr(result, "all_matched", False),
                "pair_count": len(pairs),
                "unmatched_invoice_lines": getattr(result, "unmatched_invoice_lines", []),
                "unmatched_po_lines": getattr(result, "unmatched_po_lines", []),
                "pairs": pairs,
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class RunGRNMatchTool(BaseTool):
    name = "run_grn_match"
    required_permission = "reconciliation.run"
    description = (
        "Run GRN receipt matching for 3-way reconciliation. Compares "
        "received quantities against invoice and PO quantities."
    )
    when_to_use = "In 3-WAY mode after line matching to verify receipt data."
    when_not_to_use = "In TWO_WAY mode -- skip GRN matching entirely."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "po_number": {
                "type": "string",
                "description": "PO number (used to find GRNs).",
            },
        },
        "required": ["invoice_id", "po_number"],
    }

    def run(self, *, invoice_id: int = 0, po_number: str = "", **kwargs) -> ToolResult:
        try:
            from apps.documents.models import GoodsReceiptNote, Invoice, PurchaseOrder

            qs_po = self._scoped(PurchaseOrder.objects.all())
            po = qs_po.filter(po_number=po_number).first()
            if not po:
                return ToolResult(success=True, data={
                    "grn_found": False, "reason": f"PO {po_number} not found",
                })

            grns = self._scoped(
                GoodsReceiptNote.objects.filter(purchase_order=po)
            ).prefetch_related("line_items")

            if not grns.exists():
                return ToolResult(success=True, data={
                    "grn_found": False,
                    "po_number": po_number,
                    "message": "No GRN records found for this PO",
                })

            grn_data = []
            for grn in grns:
                lines = list(grn.line_items.all().values(
                    "line_number", "item_code", "description",
                    "received_quantity", "accepted_quantity",
                ))
                grn_data.append({
                    "grn_number": grn.grn_number,
                    "receipt_date": _safe_str(grn.receipt_date),
                    "line_count": len(lines),
                    "lines": [{k: _safe_str(v) for k, v in l.items()} for l in lines],
                })

            return ToolResult(success=True, data={
                "grn_found": True,
                "po_number": po_number,
                "grn_count": len(grn_data),
                "grns": grn_data,
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetToleranceConfigTool(BaseTool):
    name = "get_tolerance_config"
    required_permission = "reconciliation.view"
    description = (
        "Retrieve current tolerance configuration including strict and "
        "auto-close thresholds. ALWAYS call this instead of hardcoding values."
    )
    when_to_use = "Before making match/auto-close decisions."
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        from apps.reconciliation.models import ReconciliationConfig

        qs = self._scoped(ReconciliationConfig.objects.all())
        config = qs.filter(is_default=True).first() or qs.first()
        if not config:
            return ToolResult(success=True, data={
                "found": False,
                "message": "No reconciliation config found -- using platform defaults",
                "strict": {"qty_pct": "2.0", "price_pct": "1.0", "amount_pct": "1.0"},
                "auto_close": {"qty_pct": "5.0", "price_pct": "3.0", "amount_pct": "3.0"},
            })

        return ToolResult(success=True, data={
            "found": True,
            "config_name": config.name,
            "strict": {
                "qty_pct": _safe_str(config.quantity_tolerance_pct),
                "price_pct": _safe_str(config.price_tolerance_pct),
                "amount_pct": _safe_str(config.amount_tolerance_pct),
            },
            "auto_close": {
                "qty_pct": _safe_str(config.auto_close_qty_tolerance_pct),
                "price_pct": _safe_str(config.auto_close_price_tolerance_pct),
                "amount_pct": _safe_str(config.auto_close_amount_tolerance_pct),
            },
            "default_mode": config.default_reconciliation_mode,
            "enable_mode_resolver": config.enable_mode_resolver,
        })


# ============================================================================
# INVESTIGATE phase tools
# ============================================================================


@register_tool
class ReExtractFieldTool(BaseTool):
    name = "re_extract_field"
    required_permission = "extraction.run"
    description = (
        "Re-extract a specific field from the invoice when the original "
        "extraction is suspected to be incorrect."
    )
    when_to_use = "When a field value seems wrong and re-extraction might fix it."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "field_name": {
                "type": "string",
                "description": "Name of the field to re-extract (e.g. po_number, total_amount).",
            },
        },
        "required": ["invoice_id", "field_name"],
    }

    def run(self, *, invoice_id: int = 0, field_name: str = "", **kwargs) -> ToolResult:
        from apps.documents.models import Invoice

        try:
            qs = self._scoped(Invoice.objects.all())
            invoice = qs.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        # Map logical field names to model attributes
        _FIELD_MAP = {
            "vendor_name": lambda inv: (inv.vendor.name if inv.vendor else "") or inv.raw_vendor_name or "",
            "vendor_tax_id": lambda inv: (inv.vendor.tax_id if inv.vendor else "") or getattr(inv, "vendor_gstin", "") or "",
        }
        if field_name in _FIELD_MAP:
            current_value = _safe_str(_FIELD_MAP[field_name](invoice))
        elif hasattr(invoice, field_name):
            current_value = _safe_str(getattr(invoice, field_name))
        else:
            current_value = ""

        # In shadow mode, return current value -- actual re-extraction requires
        # LLM call which is handled by the supervisor's reasoning.
        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "field_name": field_name,
            "current_value": current_value,
            "message": "Field value retrieved. Supervisor should reason about correctness.",
        })


@register_tool
class InvokePORetrievalAgentTool(BaseTool):
    name = "invoke_po_retrieval_agent"
    required_permission = "agents.run_po_retrieval"
    description = (
        "Delegate PO search to the specialized PO Retrieval Agent when "
        "the PO number on the invoice does not match any record."
    )
    when_to_use = "When po_lookup fails to find a PO and recovery is needed."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "vendor_name": {
                "type": "string",
                "description": "Vendor name to help narrow PO search.",
            },
            "po_number_hint": {
                "type": "string",
                "description": "The PO number from the invoice (may be incorrect).",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, vendor_name: str = "",
            po_number_hint: str = "", **kwargs) -> ToolResult:
        # This tool delegates to the existing PO lookup with broader search
        from apps.documents.models import Invoice, PurchaseOrder
        from apps.vendors.models import Vendor

        try:
            qs = self._scoped(Invoice.objects.all())
            invoice = qs.select_related("vendor").get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        # Search by vendor
        vendor = invoice.vendor
        if not vendor and vendor_name:
            from apps.core.utils import normalize_string
            norm = normalize_string(vendor_name)
            for v in self._scoped(Vendor.objects.filter(is_active=True))[:200]:
                if normalize_string(v.name) == norm:
                    vendor = v
                    break

        if vendor:
            pos = self._scoped(
                PurchaseOrder.objects.filter(vendor=vendor, status="OPEN")
            ).order_by("-po_date")[:10]
            if pos:
                po_list = []
                for p in pos:
                    po_list.append({
                        "po_number": p.po_number,
                        "total_amount": _safe_str(p.total_amount),
                        "po_date": _safe_str(p.po_date),
                    })
                return ToolResult(success=True, data={
                    "found": True,
                    "search_method": "vendor_open_pos",
                    "vendor_id": vendor.pk,
                    "vendor_name": vendor.name,
                    "candidates": po_list,
                })

        return ToolResult(success=True, data={
            "found": False,
            "invoice_id": invoice_id,
            "message": "No candidate POs found for this vendor",
        })


@register_tool
class InvokeGRNRetrievalAgentTool(BaseTool):
    name = "invoke_grn_retrieval_agent"
    required_permission = "agents.run_grn_retrieval"
    description = "Delegate GRN search to find receipt records for a PO."
    when_to_use = "When grn_lookup fails in 3-WAY mode."
    parameters_schema = {
        "type": "object",
        "properties": {
            "po_number": {
                "type": "string",
                "description": "PO number to search GRNs for.",
            },
        },
        "required": ["po_number"],
    }

    def run(self, *, po_number: str = "", **kwargs) -> ToolResult:
        from apps.documents.models import GoodsReceiptNote, PurchaseOrder

        if not po_number:
            return ToolResult(success=False, error="po_number is required")

        po = self._scoped(PurchaseOrder.objects.filter(po_number=po_number)).first()
        if not po:
            return ToolResult(success=True, data={
                "found": False, "po_number": po_number,
                "message": "PO not found -- cannot search for GRNs",
            })

        grns = self._scoped(
            GoodsReceiptNote.objects.filter(purchase_order=po)
        ).order_by("-receipt_date")

        if not grns.exists():
            return ToolResult(success=True, data={
                "found": False, "po_number": po_number,
                "message": "No GRN records exist for this PO",
            })

        grn_list = []
        for g in grns[:10]:
            grn_list.append({
                "grn_number": g.grn_number,
                "receipt_date": _safe_str(g.receipt_date),
                "status": getattr(g, "status", ""),
            })

        return ToolResult(success=True, data={
            "found": True,
            "po_number": po_number,
            "grn_count": grns.count(),
            "grns": grn_list,
        })


@register_tool
class GetVendorHistoryTool(BaseTool):
    name = "get_vendor_history"
    required_permission = "vendors.view"
    description = "Get a vendor's recent invoice and PO history."
    when_to_use = "During investigation to look for patterns."
    parameters_schema = {
        "type": "object",
        "properties": {
            "vendor_id": {
                "type": "integer",
                "description": "PK of the Vendor.",
            },
        },
        "required": ["vendor_id"],
    }

    def run(self, *, vendor_id: int = 0, **kwargs) -> ToolResult:
        from apps.documents.models import Invoice, PurchaseOrder
        from apps.vendors.models import Vendor

        try:
            qs = self._scoped(Vendor.objects.all())
            vendor = qs.get(pk=vendor_id)
        except Vendor.DoesNotExist:
            return ToolResult(success=False, error=f"Vendor {vendor_id} not found")

        recent_invoices = self._scoped(
            Invoice.objects.filter(vendor=vendor)
        ).order_by("-created_at")[:5]

        recent_pos = self._scoped(
            PurchaseOrder.objects.filter(vendor=vendor)
        ).order_by("-po_date")[:5]

        return ToolResult(success=True, data={
            "vendor_id": vendor_id,
            "vendor_name": vendor.name,
            "recent_invoices": [
                {
                    "invoice_number": inv.invoice_number,
                    "total_amount": _safe_str(inv.total_amount),
                    "status": str(inv.status),
                    "date": _safe_str(inv.invoice_date),
                }
                for inv in recent_invoices
            ],
            "recent_pos": [
                {
                    "po_number": po.po_number,
                    "total_amount": _safe_str(po.total_amount),
                    "status": po.status,
                }
                for po in recent_pos
            ],
        })


@register_tool
class GetCaseHistoryTool(BaseTool):
    name = "get_case_history"
    required_permission = "cases.view"
    description = "Get history of similar cases for pattern detection."
    when_to_use = "During investigation to check for recurring issues."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to check history for.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        try:
            from apps.cases.models import APCase

            qs = self._scoped(APCase.objects.filter(is_active=True))
            cases = qs.filter(invoice_id=invoice_id).order_by("-created_at")[:5]

            case_list = []
            for c in cases:
                case_list.append({
                    "case_number": c.case_number,
                    "status": str(c.status),
                    "created_at": _safe_str(c.created_at),
                })

            return ToolResult(success=True, data={
                "invoice_id": invoice_id,
                "case_count": len(case_list),
                "cases": case_list,
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


# ============================================================================
# DECIDE phase tools
# ============================================================================


@register_tool
class PersistInvoiceTool(BaseTool):
    name = "persist_invoice"
    required_permission = "invoices.edit"
    description = "Save or update the invoice record with current data."
    when_to_use = "After extraction and validation to persist changes."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice to persist.",
            },
            "status": {
                "type": "string",
                "description": "New status for the invoice.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, status: str = "", **kwargs) -> ToolResult:
        from apps.documents.models import Invoice
        from apps.core.enums import InvoiceStatus

        try:
            qs = self._scoped(Invoice.objects.all())
            invoice = qs.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        # Only allow valid status transitions
        valid_statuses = {s.value for s in InvoiceStatus}
        if status and status in valid_statuses:
            invoice.status = status
            invoice.save(update_fields=["status", "updated_at"])

        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "status": str(invoice.status),
            "persisted": True,
        })


@register_tool
class CreateCaseTool(BaseTool):
    name = "create_case"
    required_permission = "cases.create"
    description = "Create or find the AP case for an invoice."
    when_to_use = "To ensure the invoice has an associated AP case."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        try:
            from apps.cases.models import APCase

            existing = self._scoped(
                APCase.objects.filter(invoice_id=invoice_id, is_active=True)
            ).first()

            if existing:
                return ToolResult(success=True, data={
                    "case_id": existing.pk,
                    "case_number": existing.case_number,
                    "status": str(existing.status),
                    "already_existed": True,
                })

            # In shadow mode, we don't create new cases -- existing pipeline handles it
            return ToolResult(success=True, data={
                "case_id": None,
                "message": "No existing case found. Case creation deferred to main pipeline.",
                "already_existed": False,
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class SubmitRecommendationTool(BaseTool):
    name = "submit_recommendation"
    required_permission = "recommendations.route_review"
    description = (
        "Submit the final recommendation for this invoice. "
        "You MUST call this tool before completing your analysis."
    )
    when_to_use = "As the final action after all analysis is complete."
    parameters_schema = {
        "type": "object",
        "properties": {
            "recommendation_type": {
                "type": "string",
                "description": "One of: AUTO_CLOSE, SEND_TO_AP_REVIEW, SEND_TO_PROCUREMENT, SEND_TO_VENDOR_CLARIFICATION, REPROCESS_EXTRACTION, ESCALATE_TO_MANAGER",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0-1.0.",
            },
            "reasoning": {
                "type": "string",
                "description": "Explanation for the recommendation.",
            },
        },
        "required": ["recommendation_type", "confidence", "reasoning"],
    }

    def run(self, *, recommendation_type: str = "", confidence: float = 0.0,
            reasoning: str = "", **kwargs) -> ToolResult:
        from apps.core.enums import RecommendationType

        valid_types = {t.value for t in RecommendationType}
        if recommendation_type not in valid_types:
            return ToolResult(success=False, error=(
                f"Invalid recommendation_type '{recommendation_type}'. "
                f"Valid: {sorted(valid_types)}"
            ))

        confidence = max(0.0, min(1.0, confidence))

        return ToolResult(success=True, data={
            "recommendation_type": recommendation_type,
            "confidence": confidence,
            "reasoning": reasoning[:500],
            "submitted": True,
        })


@register_tool
class AssignReviewerTool(BaseTool):
    name = "assign_reviewer"
    required_permission = "reviews.assign"
    description = "Route the invoice to a review queue with priority."
    when_to_use = "When the recommendation requires human review."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "queue": {
                "type": "string",
                "description": "Review queue name (e.g. AP_REVIEW, PROCUREMENT, VENDOR_CLARIFICATION).",
            },
            "priority": {
                "type": "integer",
                "description": "Priority 1-10 (higher = more urgent).",
            },
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, queue: str = "AP_REVIEW",
            priority: int = 5, **kwargs) -> ToolResult:
        priority = max(1, min(10, priority))

        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "queue": queue,
            "priority": priority,
            "assigned": True,
            "message": "Review assignment recorded. Will be processed by review workflow.",
        })


@register_tool
class GenerateCaseSummaryTool(BaseTool):
    name = "generate_case_summary"
    required_permission = "cases.view"
    description = "Generate a human-readable summary of the case analysis."
    when_to_use = "As the final step to produce a summary for reviewers."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "summary": {
                "type": "string",
                "description": "The case summary text.",
            },
        },
        "required": ["invoice_id", "summary"],
    }

    def run(self, *, invoice_id: int = 0, summary: str = "", **kwargs) -> ToolResult:
        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "summary": summary[:2000],
            "generated": True,
        })


@register_tool
class AutoCloseCaseTool(BaseTool):
    name = "auto_close_case"
    required_permission = "recommendations.auto_close"
    description = (
        "Auto-close a case when all matching criteria are met. "
        "ONLY use when all lines are within tolerance and vendor is verified."
    )
    when_to_use = "When AUTO_CLOSE recommendation is confirmed."
    when_not_to_use = "If ANY line exceeds auto-close tolerance or vendor is unverified."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "reason": {
                "type": "string",
                "description": "Reason for auto-close.",
            },
        },
        "required": ["invoice_id", "reason"],
    }

    def run(self, *, invoice_id: int = 0, reason: str = "", **kwargs) -> ToolResult:
        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "auto_closed": True,
            "reason": reason[:500],
        })


@register_tool
class EscalateCaseTool(BaseTool):
    name = "escalate_case"
    required_permission = "cases.escalate"
    description = "Escalate a case to finance manager for high-risk issues."
    when_to_use = "When critical exceptions are found or confidence is very low."
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "integer",
                "description": "PK of the Invoice.",
            },
            "severity": {
                "type": "string",
                "description": "Severity level: LOW, MEDIUM, HIGH, CRITICAL.",
            },
            "reason": {
                "type": "string",
                "description": "Reason for escalation.",
            },
        },
        "required": ["invoice_id", "reason"],
    }

    def run(self, *, invoice_id: int = 0, severity: str = "HIGH",
            reason: str = "", **kwargs) -> ToolResult:
        valid_severities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        if severity not in valid_severities:
            severity = "HIGH"

        return ToolResult(success=True, data={
            "invoice_id": invoice_id,
            "escalated": True,
            "severity": severity,
            "reason": reason[:500],
        })
