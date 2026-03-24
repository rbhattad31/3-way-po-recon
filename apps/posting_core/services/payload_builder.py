"""Payload Builder — builds a canonical posting payload for ERP submission."""
from __future__ import annotations

from typing import Any, Dict, List

from apps.posting_core.services.posting_mapping_engine import PostingProposal


class PostingPayloadBuilder:
    """Builds a canonical ERP-ready posting payload from a PostingProposal."""

    @classmethod
    def build(cls, proposal: PostingProposal) -> Dict[str, Any]:
        """Build the canonical payload.

        This payload format is designed for future ERP/RPA integration.
        Phase 1: stored as JSON snapshot. Phase 2+: sent to ERP connector.
        """
        h = proposal.header
        payload: Dict[str, Any] = {
            "vendor_code": h.vendor_code,
            "vendor_name": h.vendor_name,
            "invoice_number": h.invoice_number,
            "invoice_date": h.invoice_date,
            "currency": h.currency,
            "total_amount": str(h.total_amount) if h.total_amount is not None else "",
            "tax_amount": str(h.tax_amount) if h.tax_amount is not None else "",
            "subtotal": str(h.subtotal) if h.subtotal is not None else "",
            "po_number": h.po_number,
            "line_items": [],
            "metadata": {
                "batch_refs": proposal.batch_refs,
                "vendor_confidence": h.vendor_confidence,
                "vendor_source": h.vendor_source,
            },
        }

        for lp in proposal.lines:
            payload["line_items"].append({
                "line_no": lp.line_index + 1,
                "item_code": lp.erp_item_code,
                "description": lp.mapped_description or lp.source_description,
                "quantity": str(lp.quantity) if lp.quantity is not None else "",
                "unit_price": str(lp.unit_price) if lp.unit_price is not None else "",
                "line_amount": str(lp.line_amount) if lp.line_amount is not None else "",
                "tax_code": lp.tax_code,
                "cost_center": lp.cost_center,
                "gl_account": lp.gl_account,
                "category": lp.mapped_category or lp.source_category,
                "line_type": lp.erp_line_type,
                "uom": lp.uom,
                "confidence": lp.confidence,
            })

        return payload
