"""Reconciliation mode resolver — determines TWO_WAY vs THREE_WAY per invoice.

Resolution strategy (in priority order):
  1. Explicit policy match (ReconciliationPolicy rules, ordered by priority)
  2. Config-driven heuristics (service/stock flags on line items)
  3. Fallback to config default mode
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from apps.core.enums import ReconciliationMode
from apps.documents.models import Invoice, InvoiceLineItem, PurchaseOrder
from apps.reconciliation.models import ReconciliationConfig, ReconciliationPolicy

logger = logging.getLogger(__name__)

# Service-related keywords used in heuristic classification
_SERVICE_KEYWORDS = frozenset({
    "service", "maintenance", "cleaning", "transport", "logistics",
    "consulting", "advisory", "utilities", "electricity", "water",
    "telecom", "internet", "security", "pest control", "waste",
    "landscaping", "laundry", "rental", "lease", "insurance",
    "audit", "legal", "training", "subscription", "license",
})

# Stock-related keywords used in heuristic classification
_STOCK_KEYWORDS = frozenset({
    "frozen", "chilled", "fresh", "meat", "chicken", "beef", "fish",
    "bread", "bun", "lettuce", "tomato", "onion", "cheese", "sauce",
    "packaging", "cup", "lid", "wrapper", "box", "napkin", "straw",
    "oil", "fries", "potato", "beverage", "syrup", "coffee",
    "inventory", "stock", "warehouse", "replenishment",
})


@dataclass
class ModeResolutionResult:
    """Outcome of reconciliation mode resolution."""

    mode: str  # ReconciliationMode value
    policy_code: str = ""
    policy_name: str = ""
    reason: str = ""
    grn_required: bool = True
    resolution_method: str = ""  # "policy" | "heuristic" | "default"


class ReconciliationModeResolver:
    """Resolve the reconciliation mode for an invoice.

    Usage::

        resolver = ReconciliationModeResolver(config)
        result = resolver.resolve(invoice, purchase_order)
        # result.mode -> "TWO_WAY" or "THREE_WAY"
    """

    def __init__(self, config: Optional[ReconciliationConfig] = None):
        self.config = config or self._default_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resolve(
        self,
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder] = None,
    ) -> ModeResolutionResult:
        """Determine reconciliation mode for an invoice.

        Args:
            invoice: The invoice being reconciled.
            purchase_order: The matched PO (if found).

        Returns:
            ModeResolutionResult with mode, policy, reason, and GRN flag.
        """
        if not self.config.enable_mode_resolver:
            return self._fallback_default(
                reason="Mode resolver disabled in config -- using default mode",
            )

        # 0. Non-PO early exit: no PO number on invoice and no PO found
        if not invoice.po_number and purchase_order is None:
            logger.info(
                "Mode resolver: invoice %s has no PO number and no PO found "
                "-- classifying as NON_PO",
                invoice.pk,
            )
            return ModeResolutionResult(
                mode=ReconciliationMode.NON_PO,
                reason="Invoice has no PO number and no matching PO was found",
                grn_required=False,
                resolution_method="heuristic",
            )

        # 1. Try explicit policy rules
        policy_result = self._resolve_from_policies(invoice, purchase_order)
        if policy_result is not None:
            return policy_result

        # 2. Try config-driven heuristics on line items
        heuristic_result = self._resolve_from_heuristics(invoice, purchase_order)
        if heuristic_result is not None:
            return heuristic_result

        # 3. Fallback to config default
        return self._fallback_default(
            reason="No matching policy or heuristic — falling back to config default",
        )

    def resolve_for_preview(
        self,
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder] = None,
    ) -> ModeResolutionResult:
        """Same as resolve(), but does not log or persist anything.

        Intended for the API preview endpoint.
        """
        return self.resolve(invoice, purchase_order)

    # ------------------------------------------------------------------
    # Policy-based resolution
    # ------------------------------------------------------------------
    def _resolve_from_policies(
        self,
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder],
    ) -> Optional[ModeResolutionResult]:
        """Evaluate active policies in priority order; return first match."""
        today = date.today()

        policies = (
            ReconciliationPolicy.objects
            .filter(is_active=True)
            .order_by("priority", "policy_code")
        )

        for policy in policies:
            if self._policy_matches(policy, invoice, purchase_order, today):
                grn_required = policy.reconciliation_mode == ReconciliationMode.THREE_WAY
                result = ModeResolutionResult(
                    mode=policy.reconciliation_mode,
                    policy_code=policy.policy_code,
                    policy_name=policy.policy_name,
                    reason=(
                        f"Matched policy '{policy.policy_code}' "
                        f"(priority {policy.priority}): {policy.policy_name}"
                    ),
                    grn_required=grn_required,
                    resolution_method="policy",
                )
                logger.info(
                    "Mode resolved for invoice %s via policy %s: %s",
                    invoice.pk, policy.policy_code, result.mode,
                )
                return result

        return None

    def _policy_matches(
        self,
        policy: ReconciliationPolicy,
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder],
        today: date,
    ) -> bool:
        """Check if a single policy matches the invoice context."""
        # Date validity
        if policy.effective_from and today < policy.effective_from:
            return False
        if policy.effective_to and today > policy.effective_to:
            return False

        # Vendor
        if policy.vendor_id is not None:
            if invoice.vendor_id != policy.vendor_id:
                return False

        # Invoice type
        if policy.invoice_type:
            # Match against extraction_raw_json or raw fields if available
            inv_type = self._extract_invoice_type(invoice)
            if inv_type and policy.invoice_type.upper() != inv_type.upper():
                return False
            elif not inv_type:
                # Can't evaluate — skip this criterion (don't disqualify)
                pass

        # Item category
        if policy.item_category:
            if not self._invoice_has_category(invoice, policy.item_category):
                return False

        # Business unit
        if policy.business_unit:
            po_dept = (purchase_order.department or "") if purchase_order else ""
            if policy.business_unit.upper() != po_dept.upper():
                return False

        # Location code
        if policy.location_code:
            po_location = self._extract_location(purchase_order)
            if po_location and policy.location_code.upper() != po_location.upper():
                return False
            elif not po_location:
                pass  # Can't evaluate — don't disqualify

        # Service invoice flag
        if policy.is_service_invoice is not None:
            is_service = self._is_service_invoice(invoice, purchase_order)
            if is_service is None:
                # Can't determine — policy requires a definite flag, skip this policy
                return False
            if is_service != policy.is_service_invoice:
                return False

        # Stock invoice flag
        if policy.is_stock_invoice is not None:
            is_stock = self._is_stock_invoice(invoice, purchase_order)
            if is_stock is None:
                # Can't determine — policy requires a definite flag, skip this policy
                return False
            if is_stock != policy.is_stock_invoice:
                return False

        return True

    # ------------------------------------------------------------------
    # Heuristic-based resolution
    # ------------------------------------------------------------------
    def _resolve_from_heuristics(
        self,
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder],
    ) -> Optional[ModeResolutionResult]:
        """Apply config-driven heuristics based on line item classification."""
        is_service = self._is_service_invoice(invoice, purchase_order)
        is_stock = self._is_stock_invoice(invoice, purchase_order)

        # Heuristic 1: Service invoices → TWO_WAY (if enabled)
        if self.config.enable_two_way_for_services and is_service is True:
            return ModeResolutionResult(
                mode=ReconciliationMode.TWO_WAY,
                reason=(
                    "Heuristic: invoice classified as service-type "
                    "(enable_two_way_for_services=True)"
                ),
                grn_required=False,
                resolution_method="heuristic",
            )

        # Heuristic 2: Stock invoices → THREE_WAY (if enabled)
        if self.config.enable_grn_for_stock_items and is_stock is True:
            return ModeResolutionResult(
                mode=ReconciliationMode.THREE_WAY,
                reason=(
                    "Heuristic: invoice classified as stock/inventory-type "
                    "(enable_grn_for_stock_items=True)"
                ),
                grn_required=True,
                resolution_method="heuristic",
            )

        # Heuristic 3: Keyword-based classification on line descriptions
        keyword_result = self._classify_by_keywords(invoice)
        if keyword_result is not None:
            return keyword_result

        return None

    def _classify_by_keywords(
        self, invoice: Invoice,
    ) -> Optional[ModeResolutionResult]:
        """Inspect line item descriptions for service/stock keywords."""
        lines = list(
            InvoiceLineItem.objects.filter(invoice=invoice)
            .values_list("description", "normalized_description", "raw_description")
        )
        if not lines:
            return None

        service_hits = 0
        stock_hits = 0
        total = len(lines)

        for desc, norm_desc, raw_desc in lines:
            text = (norm_desc or desc or raw_desc or "").lower()
            words = set(text.split())
            if words & _SERVICE_KEYWORDS:
                service_hits += 1
            if words & _STOCK_KEYWORDS:
                stock_hits += 1

        # Majority rule: if >50% of lines are service → TWO_WAY
        if service_hits > 0 and service_hits >= stock_hits and service_hits > total / 2:
            if self.config.enable_two_way_for_services:
                return ModeResolutionResult(
                    mode=ReconciliationMode.TWO_WAY,
                    reason=(
                        f"Heuristic: {service_hits}/{total} line items match "
                        f"service keywords — classified as service invoice"
                    ),
                    grn_required=False,
                    resolution_method="heuristic",
                )

        # Majority rule: if >50% of lines are stock → THREE_WAY
        if stock_hits > 0 and stock_hits > service_hits and stock_hits > total / 2:
            if self.config.enable_grn_for_stock_items:
                return ModeResolutionResult(
                    mode=ReconciliationMode.THREE_WAY,
                    reason=(
                        f"Heuristic: {stock_hits}/{total} line items match "
                        f"stock/inventory keywords — classified as stock invoice"
                    ),
                    grn_required=True,
                    resolution_method="heuristic",
                )

        return None

    # ------------------------------------------------------------------
    # Invoice inspection helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_service_invoice(
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder] = None,
    ) -> Optional[bool]:
        """Check if the invoice is a service invoice based on line item flags.

        Falls back to PO line item flags when invoice lines have no flags.
        """
        lines = list(
            InvoiceLineItem.objects
            .filter(invoice=invoice)
            .values_list("is_service_item", flat=True)
        )
        if not lines:
            return None

        flagged = [v for v in lines if v is not None]

        # Fallback: check PO line items when invoice lines have no flags
        if not flagged and purchase_order:
            from apps.documents.models import PurchaseOrderLineItem
            po_flags = list(
                PurchaseOrderLineItem.objects
                .filter(purchase_order=purchase_order)
                .values_list("is_service_item", flat=True)
            )
            flagged = [v for v in po_flags if v is not None]

        if not flagged:
            return None

        # If all flagged lines are service items → True
        # If none are → False
        # Mixed → None (ambiguous)
        if all(flagged):
            return True
        if not any(flagged):
            return False
        return None

    @staticmethod
    def _is_stock_invoice(
        invoice: Invoice,
        purchase_order: Optional[PurchaseOrder] = None,
    ) -> Optional[bool]:
        """Check if the invoice is a stock invoice based on line item flags.

        Falls back to PO line item flags when invoice lines have no flags.
        """
        lines = list(
            InvoiceLineItem.objects
            .filter(invoice=invoice)
            .values_list("is_stock_item", flat=True)
        )
        if not lines:
            return None

        flagged = [v for v in lines if v is not None]

        # Fallback: check PO line items when invoice lines have no flags
        if not flagged and purchase_order:
            from apps.documents.models import PurchaseOrderLineItem
            po_flags = list(
                PurchaseOrderLineItem.objects
                .filter(purchase_order=purchase_order)
                .values_list("is_stock_item", flat=True)
            )
            flagged = [v for v in po_flags if v is not None]

        if not flagged:
            return None

        if all(flagged):
            return True
        if not any(flagged):
            return False
        return None

    @staticmethod
    def _invoice_has_category(invoice: Invoice, category: str) -> bool:
        """Check if any line item on the invoice matches the given category."""
        return InvoiceLineItem.objects.filter(
            invoice=invoice,
            item_category__iexact=category,
        ).exists()

    @staticmethod
    def _extract_invoice_type(invoice: Invoice) -> str:
        """Extract invoice type from raw JSON metadata if available."""
        if invoice.extraction_raw_json and isinstance(invoice.extraction_raw_json, dict):
            return invoice.extraction_raw_json.get("invoice_type", "")
        return ""

    @staticmethod
    def _extract_location(purchase_order: Optional[PurchaseOrder]) -> str:
        """Extract location from PO if available."""
        if not purchase_order:
            return ""
        # Try department as a proxy if no explicit location field
        return purchase_order.department or ""

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------
    def _fallback_default(self, reason: str = "") -> ModeResolutionResult:
        """Return the config's default reconciliation mode."""
        mode = self.config.default_reconciliation_mode or ReconciliationMode.THREE_WAY
        grn_required = mode == ReconciliationMode.THREE_WAY

        result = ModeResolutionResult(
            mode=mode,
            reason=reason or f"Using config default mode: {mode}",
            grn_required=grn_required,
            resolution_method="default",
        )
        logger.info(
            "Mode resolved via default: %s (reason: %s)", mode, reason,
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _default_config() -> ReconciliationConfig:
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config:
            return config
        return ReconciliationConfig.objects.create(
            name="Default", is_default=True,
        )
