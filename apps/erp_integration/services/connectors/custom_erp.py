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


# Default endpoint paths -- used when metadata_json has no "endpoints" key.
DEFAULT_ENDPOINTS: Dict[str, str] = {
    "vendor_lookup": "/api/vendors/lookup",
    "item_lookup": "/api/items/lookup",
    "tax_lookup": "/api/tax-codes/lookup",
    "cost_center_lookup": "/api/cost-centers/lookup",
    "po_lookup": "/api/purchase-orders/lookup",
    "grn_lookup": "/api/grns/lookup",
    "duplicate_check": "/api/invoices/duplicate-check",
    "invoice_create": "/api/invoices/create",
    "invoice_park": "/api/invoices/park",
    "invoice_status": "/api/invoices/{document_number}/status",
}


class CustomERPConnector(BaseERPConnector):
    """Connector for custom ERP systems using REST API.

    Endpoint paths are read from ``metadata_json["endpoints"]`` on the
    ERPConnection record.  When a key is missing the connector falls back
    to the built-in defaults defined in ``DEFAULT_ENDPOINTS``.

    Secrets are resolved from environment variables via resolve_secret().
    """

    connector_name = "custom_erp"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self.base_url = connection_config.get("base_url", "").rstrip("/")
        self.timeout = connection_config.get("timeout_seconds", 30)
        self._api_key: str | None = None

        # Merge user-supplied endpoint overrides with defaults.
        meta = connection_config.get("metadata_json") or {}
        user_endpoints = meta.get("endpoints") or {}
        self._endpoints: Dict[str, str] = {**DEFAULT_ENDPOINTS, **user_endpoints}

    def _endpoint(self, key: str, **fmt_kwargs: str) -> str:
        """Return the endpoint path for *key*, applying any format kwargs."""
        path = self._endpoints.get(key, DEFAULT_ENDPOINTS.get(key, ""))
        if fmt_kwargs:
            path = path.format(**fmt_kwargs)
        return path

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_headers(self) -> Dict[str, str]:
        if self._api_key is None:
            # Prefer typed field; fall back to legacy auth_config_json.
            key_ref = self.config.get("api_key_env") or ""
            if not key_ref:
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

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    def test_connectivity(self) -> tuple:
        """Test connectivity by sending a HEAD request to the base URL."""
        if not self.base_url:
            return False, "Base URL is not configured."
        try:
            resp = requests.head(
                self.base_url,
                headers=self._get_headers(),
                timeout=min(self.timeout, 10),
                allow_redirects=True,
            )
            if resp.status_code < 500:
                return True, f"Connected successfully (HTTP {resp.status_code})."
            return False, f"Server error (HTTP {resp.status_code})."
        except requests.ConnectionError:
            return False, f"Cannot reach {self.base_url} -- connection refused or DNS failure."
        except requests.Timeout:
            return False, f"Connection to {self.base_url} timed out."
        except Exception as exc:
            return False, f"Connection test failed: {exc}"

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
        return self._do_lookup(self._endpoint("vendor_lookup"), params, "vendor")

    def lookup_po(self, po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if po_number:
            params["po_number"] = po_number
        if vendor_code:
            params["vendor_code"] = vendor_code
        return self._do_lookup(self._endpoint("po_lookup"), params, "po")

    def lookup_grn(self, po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if po_number:
            params["po_number"] = po_number
        if grn_number:
            params["grn_number"] = grn_number
        return self._do_lookup(self._endpoint("grn_lookup"), params, "grn")

    def lookup_item(self, item_code: str = "", description: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if item_code:
            params["item_code"] = item_code
        if description:
            params["description"] = description
        return self._do_lookup(self._endpoint("item_lookup"), params, "item")

    def lookup_tax(self, tax_code: str = "", rate: float = 0.0, **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if tax_code:
            params["tax_code"] = tax_code
        if rate:
            params["rate"] = str(rate)
        return self._do_lookup(self._endpoint("tax_lookup"), params, "tax")

    def lookup_cost_center(self, cost_center_code: str = "", **kwargs) -> ERPResolutionResult:
        params: Dict[str, str] = {}
        if cost_center_code:
            params["cost_center_code"] = cost_center_code
        return self._do_lookup(self._endpoint("cost_center_lookup"), params, "cost_center")

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
            self._endpoint("duplicate_check"), params, "duplicate_invoice",
        )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def create_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return self._do_submit(self._endpoint("invoice_create"), payload)

    def park_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return self._do_submit(self._endpoint("invoice_park"), payload)

    def get_posting_status(self, erp_document_number: str) -> ERPSubmissionResult:
        start = time.time()
        try:
            resp = self._request(
                "GET",
                self._endpoint("invoice_status", document_number=erp_document_number),
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
