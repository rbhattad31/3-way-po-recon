"""Posting Snapshot Builder — captures invoice state for posting."""
from __future__ import annotations

from typing import Any, Dict


class PostingSnapshotBuilder:
    """Builds JSON-serializable snapshots of invoice data for posting runs."""

    @staticmethod
    def build_invoice_snapshot(invoice) -> Dict[str, Any]:
        """Capture current invoice header + line items as a snapshot."""
        header = {
            "invoice_id": invoice.pk,
            "invoice_number": invoice.invoice_number or "",
            "po_number": invoice.po_number or "",
            "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else "",
            "currency": invoice.currency or "",
            "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else "",
            "tax_amount": str(invoice.tax_amount) if invoice.tax_amount is not None else "",
            "total_amount": str(invoice.total_amount) if invoice.total_amount is not None else "",
            "raw_vendor_name": invoice.raw_vendor_name or "",
            "vendor_id": invoice.vendor_id,
            "extraction_confidence": invoice.extraction_confidence,
            "status": invoice.status,
        }
        lines = []
        for li in invoice.line_items.order_by("line_number"):
            lines.append({
                "pk": li.pk,
                "line_number": li.line_number,
                "description": li.description or "",
                "quantity": str(li.quantity) if li.quantity is not None else "",
                "unit_price": str(li.unit_price) if li.unit_price is not None else "",
                "tax_amount": str(li.tax_amount) if li.tax_amount is not None else "",
                "line_amount": str(li.line_amount) if li.line_amount is not None else "",
                "item_category": getattr(li, "item_category", "") or "",
                "is_service_item": getattr(li, "is_service_item", None),
            })
        return {"header": header, "lines": lines}
