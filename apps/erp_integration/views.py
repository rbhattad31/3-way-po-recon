"""ERP Integration views — debug/admin resolution endpoints."""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connector_factory import ConnectorFactory
from apps.erp_integration.services.resolution.cost_center_resolver import CostCenterResolver
from apps.erp_integration.services.resolution.duplicate_invoice_resolver import DuplicateInvoiceResolver
from apps.erp_integration.services.resolution.grn_resolver import GRNResolver
from apps.erp_integration.services.resolution.item_resolver import ItemResolver
from apps.erp_integration.services.resolution.po_resolver import POResolver
from apps.erp_integration.services.resolution.tax_resolver import TaxResolver
from apps.erp_integration.services.resolution.vendor_resolver import VendorResolver

logger = logging.getLogger(__name__)

_RESOLVER_MAP = {
    ERPResolutionType.VENDOR: VendorResolver,
    ERPResolutionType.PO: POResolver,
    ERPResolutionType.GRN: GRNResolver,
    ERPResolutionType.ITEM: ItemResolver,
    ERPResolutionType.TAX: TaxResolver,
    ERPResolutionType.COST_CENTER: CostCenterResolver,
    ERPResolutionType.DUPLICATE_INVOICE: DuplicateInvoiceResolver,
}


@api_view(["POST"])
@permission_classes([IsAdminUser])
def resolve_erp_reference(request, resolution_type: str):
    """Debug/admin endpoint: resolve a single ERP reference.

    POST /erp/resolve/{type}/
    Body: JSON with lookup params (e.g. {"vendor_code": "V001"})
    """
    resolution_type_upper = resolution_type.upper()

    if resolution_type_upper not in _RESOLVER_MAP:
        return Response(
            {"error": f"Unknown resolution type: {resolution_type}",
             "valid_types": list(_RESOLVER_MAP.keys())},
            status=status.HTTP_400_BAD_REQUEST,
        )

    resolver_cls = _RESOLVER_MAP[resolution_type_upper]
    resolver = resolver_cls()

    connector = ConnectorFactory.get_default_connector()
    params = request.data or {}

    result = resolver.resolve(connector, **params)

    return Response({
        "resolution_type": resolution_type_upper,
        "resolved": result.resolved,
        "source_type": result.source_type,
        "fallback_used": result.fallback_used,
        "confidence": result.confidence,
        "connector_name": result.connector_name,
        "reason": result.reason,
        "value": result.value,
        "metadata": result.metadata,
    })
