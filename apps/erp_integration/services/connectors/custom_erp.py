"""Custom ERP Connector — Phase 1 implementation with actual API calls."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

import requests

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
    ERPSubmissionResult,
)
from apps.erp_integration.services.secrets_resolver import resolve_secret

logger = logging.getLogger(__name__)


class CustomERPConnector(BaseERPConnector):
    """Connector for custom ERP systems using REST API.

    Secrets are resolved from environment variables via resolve_secret().
    """

    connector_name = "custom_erp"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self.base_url = connection_config.get("base_url", "").rstrip("/")
        self.timeout = connection_config.get("timeout_seconds", 30)
        self._api_key: str | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_headers(self) -> Dict[str, str]:
        if self._api_key is None:
            key_ref = self.config.get("auth_config_json", {}).get(
                "api_key_env", "ERP_API_KEY"
            )
            self._api_key = resolve_secret(key_ref)
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self, method: str, path: str, **kwargs
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("headers", self._get_headers())
        return requests.request(method, url, **kwargs)

    # ------------------------------------------------------------------
    # Capability checks
    # ------------------------------------------------------------------

    def supports_vendor_lookup(self) -> bool:
        return True

    def supports_po_lookup(self) -> bool:
        return True

    def supports_grn_lookup(self) -> bool:
        return True

    def supports_item_lookup(self) -> bool:
        return True

    def supports_tax_lookup(self) -> bool:
        return True

    def supports_cost_center_lookup(self) -> bool:
        return True

    def supports_duplicate_check(self) -> bool:
        return True

    def supports_invoice_posting(self) -> bool:
        return True

    def supports_invoice_parking(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Reference lookups
    # ------------------------------------------------------------------

    def lookup_vendor(self, vendor_code: str = "", vendor_name: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if vendor_code:
            params["vendor_code"] = vendor_code
        if vendor_name:
            params["vendor_name"] = vendor_name
        return self._do_lookup("/api/vendors/lookup", params, "vendor")

    def lookup_po(self, po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if po_number:
            params["po_number"] = po_number
        if vendor_code:
            params["vendor_code"] = vendor_code
        return self._do_lookup("/api/purchase-orders/lookup", params, "po")

    def lookup_grn(self, po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if po_number:
            params["po_number"] = po_number
        if grn_number:
            params["grn_number"] = grn_number
        return self._do_lookup("/api/grns/lookup", params, "grn")

    def lookup_item(self, item_code: str = "", description: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if item_code:
            params["item_code"] = item_code
        if description:
            params["description"] = description
        return self._do_lookup("/api/items/lookup", params, "item")

    def lookup_tax(self, tax_code: str = "", rate: float = 0.0, **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if tax_code:
            params["tax_code"] = tax_code
        if rate:
            params["rate"] = str(rate)
        return self._do_lookup("/api/tax-codes/lookup", params, "tax")

    def lookup_cost_center(self, cost_center_code: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if cost_center_code:
            params["cost_center_code"] = cost_center_code
        return self._do_lookup("/api/cost-centers/lookup", params, "cost_center")

    def check_duplicate_invoice(
        self, invoice_number: str = "", vendor_code: str = "",
        fiscal_year: str = "", **kwargs,
    ) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if invoice_number:
            params["invoice_number"] = invoice_number
        if vendor_code:
            params["vendor_code"] = vendor_code
        if fiscal_year:
            params["fiscal_year"] = fiscal_year
        return self._do_lookup(
            "/api/invoices/duplicate-check", params, "duplicate_invoice",
        )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def create_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return self._do_submit("/api/invoices/create", payload)

    def park_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return self._do_submit("/api/invoices/park", payload)

    def get_posting_status(self, erp_document_number: str) -> ERPSubmissionResult:
        start = time.time()
        try:
            resp = self._request(
                "GET",
                f"/api/invoices/{erp_document_number}/status",
            )
            elapsed = int((time.time() - start) * 1000)
            data = resp.json() if resp.status_code == 200 else {}
            return ERPSubmissionResult(
                success=resp.status_code == 200,
                status=data.get("status", "UNKNOWN"),
                erp_document_number=erp_document_number,
                response_data=data,
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return ERPSubmissionResult(
                success=False,
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_lookup(self, path: str, params: Dict[str, str], label: str) -> ERPResolutionResult:
        start = time.time()
        try:
            resp = self._request("GET", path, params=params)
            elapsed = int((time.time() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                found = data.get("found", bool(data.get("results")))
                return ERPResolutionResult(
                    resolved=found,
                    value=data if found else None,
                    source_type=ERPSourceType.API,
                    confidence=1.0 if found else 0.0,
                    connector_name=self.connector_name,
                    reason=f"API {label} lookup {'found' if found else 'not found'}",
                    metadata={"duration_ms": elapsed, "status_code": resp.status_code},
                )

            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.API,
                connector_name=self.connector_name,
                reason=f"API returned status {resp.status_code}",
                metadata={"duration_ms": elapsed, "status_code": resp.status_code},
            )

        except requests.Timeout:
            elapsed = int((time.time() - start) * 1000)
            logger.warning("ERP API timeout for %s lookup at %s", label, path)
            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.NONE,
                connector_name=self.connector_name,
                reason=f"API timeout after {elapsed}ms",
                metadata={"duration_ms": elapsed, "error": "timeout"},
            )

        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.exception("ERP API error for %s lookup", label)
            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.NONE,
                connector_name=self.connector_name,
                reason=f"API error: {type(exc).__name__}: {str(exc)[:200]}",
                metadata={"duration_ms": elapsed, "error": str(exc)[:200]},
            )

    def _do_submit(self, path: str, payload: Dict[str, Any]) -> ERPSubmissionResult:
        start = time.time()
        try:
            resp = self._request("POST", path, json=payload)
            elapsed = int((time.time() - start) * 1000)
            data = resp.json() if resp.status_code in (200, 201) else {}

            if resp.status_code in (200, 201):
                return ERPSubmissionResult(
                    success=True,
                    status="SUCCESS",
                    erp_document_number=data.get("document_number", ""),
                    response_data=data,
                    connector_name=self.connector_name,
                    duration_ms=elapsed,
                )

            return ERPSubmissionResult(
                success=False,
                status="FAILED",
                error_code=str(resp.status_code),
                error_message=resp.text[:500],
                response_data=data,
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )

        except requests.Timeout:
            elapsed = int((time.time() - start) * 1000)
            return ERPSubmissionResult(
                success=False,
                status="TIMEOUT",
                error_code="TIMEOUT",
                error_message=f"ERP API timeout after {elapsed}ms",
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )

        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return ERPSubmissionResult(
                success=False,
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )
