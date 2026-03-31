"""Base ERP connector — abstract contract for all ERP integrations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from apps.erp_integration.enums import ERPSourceType


# ============================================================================
# Structured Result Objects
# ============================================================================


@dataclass
class ERPResolutionResult:
    """Structured result from any ERP resolution (lookup) call.

    Fields
    ------
    resolved        True when a usable value was found.
    value           Normalised data dict (always present when resolved=True).
    source_type     Where the value came from (see ERPSourceType choices).
    fallback_used   True when a secondary source was used (e.g. DB_FALLBACK
                    after a failed API call).
    confidence      0.0-1.0 quality score of the resolved value.
    source_as_of    When the upstream ERP data was valid/exported. Used to
                    judge whether the snapshot is current enough.
    synced_at       When this specific record was last written into our system.
    is_stale        True when synced_at (or source_as_of) exceeds the
                    configured freshness threshold for this data domain.
    stale_reason    Human-readable explanation of why is_stale is True.
    warnings        Non-blocking notices e.g. tier-2 fallback, partial match.
    source_keys     Raw ERP reference identifiers keyed by type
                    (e.g. {\"po_id\": \"42\", \"batch_id\": \"7\"}).
    connector_name  Name of the ERPConnection used (empty = DB-only path).
    reason          Short explanation of resolution outcome.
    metadata        Freeform extra metadata (connector-specific details etc.).
    """

    resolved: bool = False
    value: Optional[Dict[str, Any]] = None
    source_type: str = ERPSourceType.NONE
    fallback_used: bool = False
    confidence: float = 0.0
    # -- Provenance / freshness --
    source_as_of: Optional[datetime] = None
    synced_at: Optional[datetime] = None
    is_stale: bool = False
    stale_reason: str = ""
    warnings: List[str] = field(default_factory=list)
    source_keys: Dict[str, str] = field(default_factory=dict)
    # -- Legacy / audit --
    freshness_timestamp: Optional[datetime] = None  # deprecated; use synced_at
    connector_name: str = ""
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_provenance_dict(self) -> Dict[str, Any]:
        """Serialise provenance metadata for storage in JSON fields.

        This dict is designed to be stored in ReconciliationResult.erp_source_metadata_json
        or PostingRun.erp_source_metadata_json so auditors can trace exactly
        which source was used and whether it was fresh.
        """
        return {
            "source_type": self.source_type,
            "fallback_used": self.fallback_used,
            "confidence": self.confidence,
            "source_as_of": self.source_as_of.isoformat() if self.source_as_of else None,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
            "warnings": self.warnings,
            "source_keys": self.source_keys,
            "connector_name": self.connector_name,
            "reason": self.reason,
        }


@dataclass
class ERPSubmissionResult:
    """Structured result from an ERP submission call."""

    success: bool = False
    status: str = ""
    erp_document_number: str = ""
    error_code: str = ""
    error_message: str = ""
    response_data: Dict[str, Any] = field(default_factory=dict)
    connector_name: str = ""
    duration_ms: int = 0


# ============================================================================
# Base Connector
# ============================================================================


class BaseERPConnector:
    """Abstract base class for ERP connectors.

    All connectors must implement capability checks and lookup/submission
    methods. Default implementations return unsupported results.
    """

    connector_name: str = "base"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        self.config = connection_config

    # ------------------------------------------------------------------
    # Capability checks
    # ------------------------------------------------------------------

    def supports_vendor_lookup(self) -> bool:
        return False

    def supports_po_lookup(self) -> bool:
        return False

    def supports_grn_lookup(self) -> bool:
        return False

    def supports_item_lookup(self) -> bool:
        return False

    def supports_tax_lookup(self) -> bool:
        return False

    def supports_cost_center_lookup(self) -> bool:
        return False

    def supports_duplicate_check(self) -> bool:
        return False

    def supports_invoice_posting(self) -> bool:
        return False

    def supports_invoice_parking(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    def test_connectivity(self) -> tuple:
        """Test actual connectivity to the ERP system.

        Returns:
            (success: bool, message: str)
        """
        return False, "Connectivity test not implemented for this connector."

    # ------------------------------------------------------------------
    # Reference lookups
    # ------------------------------------------------------------------

    def lookup_vendor(self, vendor_code: str = "", vendor_name: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Vendor lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_po(self, po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="PO lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_grn(self, po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="GRN lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_item(self, item_code: str = "", description: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Item lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_tax(self, tax_code: str = "", rate: float = 0.0, **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Tax lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_cost_center(self, cost_center_code: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Cost center lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def check_duplicate_invoice(
        self, invoice_number: str = "", vendor_code: str = "",
        fiscal_year: str = "", **kwargs,
    ) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Duplicate check not supported by this connector",
            connector_name=self.connector_name,
        )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def create_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return ERPSubmissionResult(
            success=False, status="UNSUPPORTED",
            error_message="Invoice creation not supported by this connector",
            connector_name=self.connector_name,
        )

    def park_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return ERPSubmissionResult(
            success=False, status="UNSUPPORTED",
            error_message="Invoice parking not supported by this connector",
            connector_name=self.connector_name,
        )

    def get_posting_status(self, erp_document_number: str) -> ERPSubmissionResult:
        return ERPSubmissionResult(
            success=False, status="UNSUPPORTED",
            error_message="Status check not supported by this connector",
            connector_name=self.connector_name,
        )
