"""Posting Mapping Engine — resolves ERP values from imported reference tables.

This is the main Phase 1 value service. It resolves likely ERP values
(vendor, items, tax codes, cost centers) using the imported reference
data, alias mappings, and posting rules.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from apps.core.enums import (
    ERPReferenceBatchStatus,
    ERPReferenceBatchType,
    PostingFieldCategory,
    PostingFieldSourceType,
    PostingIssueSeverity,
)
from apps.posting_core.models import (
    ERPCostCenterReference,
    ERPItemReference,
    ERPPOReference,
    ERPReferenceImportBatch,
    ERPTaxCodeReference,
    ERPVendorReference,
    ItemAliasMapping,
    PostingEvidence,
    PostingFieldValue,
    PostingIssue,
    PostingLineItem,
    PostingRule,
    VendorAliasMapping,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Dataclasses for Posting Proposal
# ============================================================================


@dataclass
class PostingHeaderProposal:
    """Resolved header-level posting fields."""
    vendor_code: str = ""
    vendor_name: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    currency: str = ""
    total_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    subtotal: Optional[Decimal] = None
    po_number: str = ""
    vendor_confidence: float = 0.0
    vendor_source: str = ""
    batch_refs: Dict[str, int] = field(default_factory=dict)


@dataclass
class PostingLineProposal:
    """Resolved line-level posting fields."""
    line_index: int = 0
    invoice_line_item_id: Optional[int] = None
    source_description: str = ""
    mapped_description: str = ""
    source_category: str = ""
    mapped_category: str = ""
    erp_item_code: str = ""
    erp_line_type: str = ""
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    line_amount: Optional[Decimal] = None
    tax_code: str = ""
    cost_center: str = ""
    gl_account: str = ""
    uom: str = ""
    confidence: float = 0.0
    item_source: str = ""
    tax_source: str = ""
    cost_center_source: str = ""


@dataclass
class PostingProposal:
    """Complete posting proposal combining header and lines."""
    header: PostingHeaderProposal = field(default_factory=PostingHeaderProposal)
    lines: List[PostingLineProposal] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    batch_refs: Dict[str, int] = field(default_factory=dict)


class PostingMappingEngine:
    """Resolves ERP values from imported reference tables."""

    def __init__(self):
        self._latest_batches: Dict[str, ERPReferenceImportBatch] = {}

    def resolve(
        self,
        invoice,
        line_items,
        *,
        po_number: str = "",
    ) -> PostingProposal:
        """Run full mapping resolution for an invoice.

        Args:
            invoice: Invoice model instance.
            line_items: QuerySet or list of InvoiceLineItem.
            po_number: PO number from invoice (used for PO ref lookup).

        Returns:
            PostingProposal with resolved values and issues.
        """
        proposal = PostingProposal()
        self._load_latest_batches()
        self._store_batch_refs(proposal)

        # A: Vendor resolution
        self._resolve_vendor(invoice, proposal)

        # B–E: Line-level resolution
        po_refs = self._load_po_refs(po_number) if po_number else []

        for idx, line in enumerate(line_items):
            line_proposal = PostingLineProposal(
                line_index=idx,
                invoice_line_item_id=line.pk if hasattr(line, "pk") else None,
                source_description=line.description or "",
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_amount=line.line_amount,
                source_category=getattr(line, "item_category", "") or "",
                uom="",
            )

            # B: Item resolution
            self._resolve_item(line, line_proposal, po_refs, proposal)

            # C: Tax resolution
            self._resolve_tax(line, line_proposal, proposal)

            # D: Cost center resolution
            self._resolve_cost_center(line, line_proposal, proposal)

            # E: Category / line type
            self._resolve_line_type(line, line_proposal, proposal)

            proposal.lines.append(line_proposal)

        # Header fields from invoice
        proposal.header.invoice_number = invoice.invoice_number or ""
        proposal.header.invoice_date = str(invoice.invoice_date) if invoice.invoice_date else ""
        proposal.header.currency = invoice.currency or ""
        proposal.header.total_amount = invoice.total_amount
        proposal.header.tax_amount = invoice.tax_amount
        proposal.header.subtotal = invoice.subtotal
        proposal.header.po_number = po_number or invoice.po_number or ""

        return proposal

    # ------------------------------------------------------------------
    # Vendor Resolution
    # ------------------------------------------------------------------

    def _resolve_vendor(self, invoice, proposal: PostingProposal) -> None:
        """Resolve vendor code using precedence chain."""
        vendor = getattr(invoice, "vendor", None)
        vendor_name = invoice.raw_vendor_name or ""

        # 1. Exact vendor code from vendor profile
        if vendor and hasattr(vendor, "vendor_code") and vendor.vendor_code:
            match = self._find_vendor_by_code(vendor.vendor_code)
            if match:
                proposal.header.vendor_code = match.vendor_code
                proposal.header.vendor_name = match.vendor_name
                proposal.header.vendor_confidence = 1.0
                proposal.header.vendor_source = PostingFieldSourceType.VENDOR_REF
                self._add_evidence(proposal, "vendor_code", PostingFieldSourceType.VENDOR_REF,
                                   f"Exact code match: {match.vendor_code}")
                return

        # 2. Alias mapping
        alias_match = self._find_vendor_alias(vendor_name)
        if alias_match and alias_match.vendor_reference:
            ref = alias_match.vendor_reference
            proposal.header.vendor_code = ref.vendor_code
            proposal.header.vendor_name = ref.vendor_name
            proposal.header.vendor_confidence = alias_match.confidence
            proposal.header.vendor_source = PostingFieldSourceType.VENDOR_REF
            self._add_evidence(proposal, "vendor_code", PostingFieldSourceType.VENDOR_REF,
                               f"Alias match: '{alias_match.alias_text}' → {ref.vendor_code}")
            return

        # 3. Exact normalized name match
        name_match = self._find_vendor_by_name(vendor_name)
        if name_match:
            proposal.header.vendor_code = name_match.vendor_code
            proposal.header.vendor_name = name_match.vendor_name
            proposal.header.vendor_confidence = 0.9
            proposal.header.vendor_source = PostingFieldSourceType.VENDOR_REF
            self._add_evidence(proposal, "vendor_code", PostingFieldSourceType.VENDOR_REF,
                               f"Exact name match: {name_match.vendor_name}")
            return

        # 4. Partial / contains match
        partial_match = self._find_vendor_partial(vendor_name)
        if partial_match:
            proposal.header.vendor_code = partial_match.vendor_code
            proposal.header.vendor_name = partial_match.vendor_name
            proposal.header.vendor_confidence = 0.6
            proposal.header.vendor_source = PostingFieldSourceType.VENDOR_REF
            self._add_evidence(proposal, "vendor_code", PostingFieldSourceType.VENDOR_REF,
                               f"Partial match: '{vendor_name}' ≈ {partial_match.vendor_name}")
            return

        # 5. Unresolved
        self._add_issue(proposal, PostingIssueSeverity.ERROR, "vendor_code",
                        "vendor_resolution", f"Could not resolve vendor: '{vendor_name}'")

    # ------------------------------------------------------------------
    # Item Resolution
    # ------------------------------------------------------------------

    def _resolve_item(self, line, line_proposal: PostingLineProposal,
                      po_refs: List[ERPPOReference], proposal: PostingProposal) -> None:
        """Resolve item code for a line item."""
        description = line.description or ""
        normalized_desc = _normalize(description)

        # 1. PO reference line match
        if po_refs:
            po_match = self._match_po_line(line, po_refs)
            if po_match:
                line_proposal.erp_item_code = po_match.item_code
                line_proposal.mapped_description = po_match.description
                line_proposal.confidence = 0.95
                line_proposal.item_source = PostingFieldSourceType.PO_REF
                if po_match.item_code:
                    self._add_evidence(
                        proposal, "item_code", PostingFieldSourceType.PO_REF,
                        f"PO line match: {po_match.po_number}/{po_match.po_line_number}",
                        line_item_index=line_proposal.line_index,
                    )
                return

        # 2. Exact item code on line
        item_code = getattr(line, "item_code", "") or ""
        if item_code:
            ref = self._find_item_by_code(item_code)
            if ref:
                line_proposal.erp_item_code = ref.item_code
                line_proposal.mapped_description = ref.item_name
                line_proposal.mapped_category = ref.category
                line_proposal.uom = ref.uom or line_proposal.uom
                line_proposal.confidence = 1.0
                line_proposal.item_source = PostingFieldSourceType.ITEM_REF
                return

        # 3. Alias mapping
        alias = self._find_item_alias(description)
        if alias and alias.item_reference:
            ref = alias.item_reference
            line_proposal.erp_item_code = ref.item_code
            line_proposal.mapped_description = alias.mapped_description or ref.item_name
            line_proposal.mapped_category = alias.mapped_category or ref.category
            line_proposal.uom = ref.uom or ""
            line_proposal.confidence = alias.confidence
            line_proposal.item_source = PostingFieldSourceType.ITEM_REF
            self._add_evidence(
                proposal, "item_code", PostingFieldSourceType.ITEM_REF,
                f"Item alias: '{alias.alias_text}' → {ref.item_code}",
                line_item_index=line_proposal.line_index,
            )
            return

        # 4. Normalized description match
        name_match = self._find_item_by_name(description)
        if name_match:
            line_proposal.erp_item_code = name_match.item_code
            line_proposal.mapped_description = name_match.item_name
            line_proposal.mapped_category = name_match.category
            line_proposal.uom = name_match.uom or ""
            line_proposal.confidence = 0.7
            line_proposal.item_source = PostingFieldSourceType.ITEM_REF
            return

        # 5. Category/rule-based fallback
        rule_result = self._apply_rules("CATEGORY_MAP", {
            "description": normalized_desc,
            "category": line_proposal.source_category,
        })
        if rule_result:
            line_proposal.mapped_category = rule_result.get("category", "")
            line_proposal.erp_line_type = rule_result.get("line_type", "")
            line_proposal.confidence = 0.5
            line_proposal.item_source = PostingFieldSourceType.RULE
            return

        # 6. Unresolved
        line_proposal.confidence = 0.0
        self._add_issue(
            proposal, PostingIssueSeverity.WARNING, "item_code",
            "item_resolution",
            f"Could not resolve item for line {line_proposal.line_index}: '{description[:80]}'",
            line_item_index=line_proposal.line_index,
        )

    # ------------------------------------------------------------------
    # Tax Resolution
    # ------------------------------------------------------------------

    def _resolve_tax(self, line, line_proposal: PostingLineProposal,
                     proposal: PostingProposal) -> None:
        """Resolve tax code for a line item."""
        # 1. Explicit invoice tax code
        tax_code_on_line = getattr(line, "tax_code", "") or ""
        if tax_code_on_line:
            ref = self._find_tax_by_code(tax_code_on_line)
            if ref:
                line_proposal.tax_code = ref.tax_code
                line_proposal.tax_source = PostingFieldSourceType.TAX_REF
                return

        # 2. Item default tax code
        if line_proposal.erp_item_code:
            item_ref = self._find_item_by_code(line_proposal.erp_item_code)
            if item_ref and item_ref.tax_code:
                line_proposal.tax_code = item_ref.tax_code
                line_proposal.tax_source = PostingFieldSourceType.ITEM_REF
                return

        # 3. Tax ref by country/rate/label
        invoice_tax = line.tax_amount if hasattr(line, "tax_amount") else None
        if invoice_tax and line.line_amount:
            implied_rate = float(invoice_tax / line.line_amount) if line.line_amount else None
            if implied_rate is not None:
                rate_match = self._find_tax_by_rate(implied_rate)
                if rate_match:
                    line_proposal.tax_code = rate_match.tax_code
                    line_proposal.tax_source = PostingFieldSourceType.TAX_REF
                    self._add_evidence(
                        proposal, "tax_code", PostingFieldSourceType.TAX_REF,
                        f"Implied rate {implied_rate:.4f} matched tax code {rate_match.tax_code}",
                        line_item_index=line_proposal.line_index,
                    )
                    return

        # 4. Posting rules fallback
        rule_result = self._apply_rules("TAX_MAP", {
            "category": line_proposal.mapped_category or line_proposal.source_category,
            "line_type": line_proposal.erp_line_type,
        })
        if rule_result and rule_result.get("tax_code"):
            line_proposal.tax_code = rule_result["tax_code"]
            line_proposal.tax_source = PostingFieldSourceType.RULE
            return

        # 5. Unresolved
        self._add_issue(
            proposal, PostingIssueSeverity.WARNING, "tax_code",
            "tax_resolution",
            f"Could not resolve tax code for line {line_proposal.line_index}",
            line_item_index=line_proposal.line_index,
        )

    # ------------------------------------------------------------------
    # Cost Center Resolution
    # ------------------------------------------------------------------

    def _resolve_cost_center(self, line, line_proposal: PostingLineProposal,
                             proposal: PostingProposal) -> None:
        """Resolve cost center for a line item."""
        # 1. Invoice/entity/business unit mapping via rules
        rule_result = self._apply_rules("COST_CENTER_MAP", {
            "category": line_proposal.mapped_category or line_proposal.source_category,
            "line_type": line_proposal.erp_line_type,
            "vendor_code": proposal.header.vendor_code,
        })
        if rule_result and rule_result.get("cost_center"):
            cc_code = rule_result["cost_center"]
            ref = self._find_cost_center_by_code(cc_code)
            if ref:
                line_proposal.cost_center = ref.cost_center_code
                line_proposal.cost_center_source = PostingFieldSourceType.RULE
                return

        # 2. Exact reference match by code
        cc_on_line = getattr(line, "cost_center", "") or ""
        if cc_on_line:
            ref = self._find_cost_center_by_code(cc_on_line)
            if ref:
                line_proposal.cost_center = ref.cost_center_code
                line_proposal.cost_center_source = PostingFieldSourceType.COST_CENTER_REF
                return

        # 3. Unresolved — warning only (cost center may not be required for all lines)
        self._add_issue(
            proposal, PostingIssueSeverity.INFO, "cost_center",
            "cost_center_resolution",
            f"Cost center not resolved for line {line_proposal.line_index}",
            line_item_index=line_proposal.line_index,
        )

    # ------------------------------------------------------------------
    # Line Type Resolution
    # ------------------------------------------------------------------

    def _resolve_line_type(self, line, line_proposal: PostingLineProposal,
                           proposal: PostingProposal) -> None:
        """Resolve category / line type."""
        if line_proposal.erp_line_type:
            return  # Already set

        # 1. From item reference category
        if line_proposal.erp_item_code:
            item_ref = self._find_item_by_code(line_proposal.erp_item_code)
            if item_ref and item_ref.item_type:
                line_proposal.erp_line_type = item_ref.item_type
                return

        # 2. From posting rules
        rule_result = self._apply_rules("LINE_TYPE_MAP", {
            "category": line_proposal.mapped_category or line_proposal.source_category,
            "description": _normalize(line_proposal.source_description),
        })
        if rule_result and rule_result.get("line_type"):
            line_proposal.erp_line_type = rule_result["line_type"]
            return

        # 3. Keyword fallback
        desc_lower = (line_proposal.source_description or "").lower()
        service_keywords = {"service", "consulting", "labour", "labor", "maintenance",
                            "support", "advisory", "professional"}
        if any(kw in desc_lower for kw in service_keywords):
            line_proposal.erp_line_type = "SERVICE"
        else:
            line_proposal.erp_line_type = "MATERIAL"

    # ------------------------------------------------------------------
    # Reference Lookups
    # ------------------------------------------------------------------

    def _load_latest_batches(self) -> None:
        """Load the latest completed batch for each type."""
        for bt in ERPReferenceBatchType.values:
            batch = (
                ERPReferenceImportBatch.objects
                .filter(batch_type=bt, status=ERPReferenceBatchStatus.COMPLETED)
                .order_by("-imported_at")
                .first()
            )
            if batch:
                self._latest_batches[bt] = batch

    def _store_batch_refs(self, proposal: PostingProposal) -> None:
        """Store batch IDs used for provenance."""
        for bt, batch in self._latest_batches.items():
            proposal.batch_refs[bt] = batch.pk

    def _find_vendor_by_code(self, code: str) -> Optional[ERPVendorReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.VENDOR)
        if not batch:
            return None
        return (
            ERPVendorReference.objects
            .filter(batch=batch, vendor_code=code, is_active=True)
            .first()
        )

    def _find_vendor_alias(self, name: str) -> Optional[VendorAliasMapping]:
        norm = _normalize(name)
        if not norm:
            return None
        return (
            VendorAliasMapping.objects
            .filter(normalized_alias=norm, is_active=True)
            .select_related("vendor_reference")
            .first()
        )

    def _find_vendor_by_name(self, name: str) -> Optional[ERPVendorReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.VENDOR)
        if not batch:
            return None
        norm = _normalize(name)
        if not norm:
            return None
        return (
            ERPVendorReference.objects
            .filter(batch=batch, normalized_vendor_name=norm, is_active=True)
            .first()
        )

    def _find_vendor_partial(self, name: str) -> Optional[ERPVendorReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.VENDOR)
        if not batch:
            return None
        norm = _normalize(name)
        if not norm or len(norm) < 3:
            return None
        return (
            ERPVendorReference.objects
            .filter(batch=batch, normalized_vendor_name__icontains=norm, is_active=True)
            .first()
        )

    def _find_item_by_code(self, code: str) -> Optional[ERPItemReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.ITEM)
        if not batch:
            return None
        return (
            ERPItemReference.objects
            .filter(batch=batch, item_code=code, is_active=True)
            .first()
        )

    def _find_item_alias(self, description: str) -> Optional[ItemAliasMapping]:
        norm = _normalize(description)
        if not norm:
            return None
        return (
            ItemAliasMapping.objects
            .filter(normalized_alias=norm, is_active=True)
            .select_related("item_reference")
            .first()
        )

    def _find_item_by_name(self, description: str) -> Optional[ERPItemReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.ITEM)
        if not batch:
            return None
        norm = _normalize(description)
        if not norm:
            return None
        return (
            ERPItemReference.objects
            .filter(batch=batch, normalized_item_name=norm, is_active=True)
            .first()
        )

    def _match_po_line(self, line, po_refs: List[ERPPOReference]) -> Optional[ERPPOReference]:
        """Match invoice line to PO reference by line number or description."""
        line_num = getattr(line, "line_number", None)
        if line_num:
            for pr in po_refs:
                if pr.po_line_number and str(pr.po_line_number) == str(line_num):
                    return pr

        desc_norm = _normalize(line.description or "")
        if desc_norm:
            for pr in po_refs:
                if pr.normalized_description and pr.normalized_description == desc_norm:
                    return pr
        return None

    def _load_po_refs(self, po_number: str) -> List[ERPPOReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.OPEN_PO)
        if not batch:
            return []
        return list(
            ERPPOReference.objects
            .filter(batch=batch, po_number=po_number, is_open=True)
            .order_by("po_line_number")
        )

    def _find_tax_by_code(self, code: str) -> Optional[ERPTaxCodeReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.TAX)
        if not batch:
            return None
        return (
            ERPTaxCodeReference.objects
            .filter(batch=batch, tax_code=code, is_active=True)
            .first()
        )

    def _find_tax_by_rate(self, rate: float, tolerance: float = 0.005) -> Optional[ERPTaxCodeReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.TAX)
        if not batch:
            return None
        from decimal import Decimal
        rate_dec = Decimal(str(round(rate, 4)))
        lower = rate_dec - Decimal(str(tolerance))
        upper = rate_dec + Decimal(str(tolerance))
        return (
            ERPTaxCodeReference.objects
            .filter(batch=batch, rate__gte=lower, rate__lte=upper, is_active=True)
            .first()
        )

    def _find_cost_center_by_code(self, code: str) -> Optional[ERPCostCenterReference]:
        batch = self._latest_batches.get(ERPReferenceBatchType.COST_CENTER)
        if not batch:
            return None
        return (
            ERPCostCenterReference.objects
            .filter(batch=batch, cost_center_code=code, is_active=True)
            .first()
        )

    # ------------------------------------------------------------------
    # Posting Rules
    # ------------------------------------------------------------------

    def _apply_rules(self, rule_type: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply posting rules for the given type and context."""
        rules = (
            PostingRule.objects
            .filter(rule_type=rule_type, is_active=True)
            .order_by("priority")
        )
        for rule in rules:
            if self._rule_matches(rule.condition_json, context):
                return rule.output_json
        return None

    @staticmethod
    def _rule_matches(conditions: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Check if rule conditions match the context."""
        for key, pattern in conditions.items():
            ctx_val = context.get(key, "")
            if not ctx_val:
                return False
            if isinstance(pattern, str):
                if pattern.startswith("re:"):
                    if not re.search(pattern[3:], str(ctx_val), re.IGNORECASE):
                        return False
                elif pattern.lower() != str(ctx_val).lower():
                    return False
            elif ctx_val != pattern:
                return False
        return True

    # ------------------------------------------------------------------
    # Issue / Evidence Helpers
    # ------------------------------------------------------------------

    def _add_issue(self, proposal: PostingProposal, severity: str,
                   field_code: str, check_type: str, message: str,
                   line_item_index: Optional[int] = None) -> None:
        proposal.issues.append({
            "severity": severity,
            "field_code": field_code,
            "check_type": check_type,
            "message": message,
            "line_item_index": line_item_index,
        })

    def _add_evidence(self, proposal: PostingProposal, field_code: str,
                      source_type: str, snippet: str,
                      line_item_index: Optional[int] = None,
                      confidence: Optional[float] = None) -> None:
        proposal.evidence.append({
            "field_code": field_code,
            "source_type": source_type,
            "snippet": snippet,
            "line_item_index": line_item_index,
            "confidence": confidence,
        })


def _normalize(text: str) -> str:
    """Normalize text for matching."""
    if not text:
        return ""
    t = str(text).strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t
