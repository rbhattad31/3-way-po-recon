"""MySQL ERP Connector -- direct database queries against an ERP's MySQL / MariaDB.

Uses ``MySQLdb`` (mysqlclient) which is already a project dependency.
Query overrides go in ``metadata_json["queries"]``; missing keys fall
back to ``DEFAULT_QUERIES``.

Connection can be configured via:
  1. Env-var reference (``connection_string_env`` -- a DSN or URI)
  2. Individual typed fields (``db_host``, ``db_port``, ``db_username``,
     ``db_password_encrypted``, ``database_name``)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
    ERPSubmissionResult,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Default SQL queries -- MySQL syntax.  Use %s for parameter placeholders.
# ============================================================================

DEFAULT_QUERIES: Dict[str, str] = {
    "vendor_lookup": (
        "SELECT vendor_code, vendor_name, address, city, country, "
        "currency, tax_number, is_active "
        "FROM vendors "
        "WHERE vendor_code = %s OR vendor_name LIKE CONCAT('%%', %s, '%%') "
        "ORDER BY CASE WHEN vendor_code = %s THEN 0 ELSE 1 END "
        "LIMIT 1"
    ),
    "item_lookup": (
        "SELECT item_code, description, unit_of_measure, "
        "item_group, is_active "
        "FROM items "
        "WHERE item_code = %s OR description LIKE CONCAT('%%', %s, '%%') "
        "ORDER BY CASE WHEN item_code = %s THEN 0 ELSE 1 END "
        "LIMIT 1"
    ),
    "tax_lookup": (
        "SELECT tax_code, description, rate "
        "FROM tax_codes "
        "WHERE tax_code = %s OR rate = %s "
        "LIMIT 1"
    ),
    "cost_center_lookup": (
        "SELECT cost_center_code, description, is_active "
        "FROM cost_centers "
        "WHERE cost_center_code = %s "
        "LIMIT 1"
    ),
    "po_lookup": (
        "SELECT po_number, vendor_code, po_date, status, "
        "total_amount, currency "
        "FROM purchase_orders "
        "WHERE po_number = %s "
        "AND (%s IS NULL OR vendor_code = %s) "
        "LIMIT 1"
    ),
    "grn_lookup": (
        "SELECT grn_number, po_number, receipt_date, status "
        "FROM goods_receipt_notes "
        "WHERE po_number = %s "
        "AND (%s IS NULL OR grn_number = %s) "
        "LIMIT 1"
    ),
    "duplicate_check": (
        "SELECT invoice_number, vendor_code, fiscal_year, status, "
        "document_date "
        "FROM invoices "
        "WHERE invoice_number = %s AND vendor_code = %s "
        "AND (%s IS NULL OR fiscal_year = %s)"
    ),
}


def _rows_to_dicts(cursor) -> List[Dict[str, Any]]:
    """Convert MySQLdb cursor rows to a list of dicts."""
    if not cursor.description:
        return []
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class MySQLERPConnector(BaseERPConnector):
    """Connector that queries an ERP's MySQL / MariaDB database directly.

    Configuration (via ERPConnection record):
        Builder fields (preferred):
            db_host, db_port, database_name, db_username,
            db_password_encrypted
        -OR-
        connection_string_env -- env-var holding a DSN or URI
        metadata_json:
            queries  -- dict of query overrides (optional)
            query_timeout  -- per-query timeout in seconds (default 30)
    """

    connector_name = "mysql"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self.timeout = connection_config.get("timeout_seconds", 30)

        meta = connection_config.get("metadata_json") or {}
        user_queries = meta.get("queries") or {}
        self._queries: Dict[str, str] = {**DEFAULT_QUERIES, **user_queries}
        self._query_timeout: int = meta.get("query_timeout", 30)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_connect_kwargs(self) -> Dict[str, Any]:
        """Build MySQLdb.connect() kwargs from config fields."""
        host = self.config.get("db_host") or ""
        if not host:
            raise ValueError(
                "MySQL connection requires db_host (and database_name)."
            )

        database = self.config.get("database_name") or ""
        port = int(self.config.get("db_port") or 3306)
        username = self.config.get("db_username") or "root"

        password = ""
        encrypted_pw = self.config.get("db_password_encrypted") or ""
        if encrypted_pw:
            from apps.erp_integration.crypto import decrypt_value
            password = decrypt_value(encrypted_pw)

        kwargs: Dict[str, Any] = {
            "host": host,
            "port": port,
            "user": username,
            "passwd": password,
            "db": database,
            "connect_timeout": self.timeout,
            "charset": "utf8mb4",
        }

        # SSL -- enabled by default (required by Azure MySQL and most
        # cloud-hosted instances).  db_trust_cert=True relaxes cert
        # validation for self-signed certs.
        if self.config.get("db_trust_cert"):
            # Accept any server certificate (self-signed OK)
            kwargs["ssl_mode"] = "REQUIRED"
            kwargs["ssl"] = {"ca": ""}
        else:
            # Standard SSL with server cert verification
            kwargs["ssl_mode"] = "REQUIRED"

        return kwargs

    def _connect(self):
        """Return a new MySQLdb connection (caller must close)."""
        import MySQLdb  # shipped with mysqlclient (project dep)

        return MySQLdb.connect(**self._get_connect_kwargs())

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    def test_connectivity(self) -> tuple:
        """Test connectivity with a simple SELECT 1."""
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                conn.close()
            return True, "MySQL connection successful."
        except ImportError:
            return False, "mysqlclient is not installed. Run: pip install mysqlclient"
        except Exception as exc:
            return False, f"MySQL connection failed: {exc}"

    # ------------------------------------------------------------------
    # Capability flags
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
        meta = self.config.get("metadata_json") or {}
        queries = meta.get("queries") or {}
        return "invoice_create" in queries

    def supports_invoice_parking(self) -> bool:
        meta = self.config.get("metadata_json") or {}
        queries = meta.get("queries") or {}
        return "invoice_park" in queries

    # ------------------------------------------------------------------
    # Reference lookups
    # ------------------------------------------------------------------

    def lookup_vendor(
        self, vendor_code: str = "", vendor_name: str = "", **kw
    ) -> ERPResolutionResult:
        return self._do_query(
            "vendor_lookup",
            [vendor_code, vendor_name, vendor_code],
            "vendor",
        )

    def lookup_item(
        self, item_code: str = "", description: str = "", **kw
    ) -> ERPResolutionResult:
        return self._do_query(
            "item_lookup",
            [item_code, description, item_code],
            "item",
        )

    def lookup_tax(
        self, tax_code: str = "", rate: float = 0.0, **kw
    ) -> ERPResolutionResult:
        return self._do_query("tax_lookup", [tax_code, rate], "tax")

    def lookup_cost_center(
        self, cost_center_code: str = "", **kw
    ) -> ERPResolutionResult:
        return self._do_query(
            "cost_center_lookup", [cost_center_code], "cost_center"
        )

    def lookup_po(
        self, po_number: str = "", vendor_code: str = "", **kw
    ) -> ERPResolutionResult:
        vc = vendor_code or None
        return self._do_query("po_lookup", [po_number, vc, vc], "po")

    def lookup_grn(
        self, po_number: str = "", grn_number: str = "", **kw
    ) -> ERPResolutionResult:
        gn = grn_number or None
        return self._do_query("grn_lookup", [po_number, gn, gn], "grn")

    def check_duplicate_invoice(
        self,
        invoice_number: str = "",
        vendor_code: str = "",
        fiscal_year: str = "",
        **kw,
    ) -> ERPResolutionResult:
        fy = fiscal_year or None
        result = self._do_query(
            "duplicate_check",
            [invoice_number, vendor_code, fy, fy],
            "duplicate_invoice",
        )
        if result.resolved and result.value:
            rows = result.value.get("results", [])
            result.value["is_duplicate"] = len(rows) > 0
            result.value["duplicate_count"] = len(rows)
        return result

    # ------------------------------------------------------------------
    # Submission (only if custom queries are provided)
    # ------------------------------------------------------------------

    def create_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return self._do_submit("invoice_create", payload)

    def park_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return self._do_submit("invoice_park", payload)

    def get_posting_status(
        self, erp_document_number: str
    ) -> ERPSubmissionResult:
        start = time.time()
        query = self._queries.get("invoice_status")
        if not query:
            return ERPSubmissionResult(
                success=False,
                status="UNSUPPORTED",
                error_message="No invoice_status query configured",
                connector_name=self.connector_name,
                duration_ms=0,
            )
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                cursor.execute(query, [erp_document_number])
                rows = _rows_to_dicts(cursor)
                elapsed = int((time.time() - start) * 1000)
                if rows:
                    return ERPSubmissionResult(
                        success=True,
                        status=rows[0].get("status", "UNKNOWN"),
                        erp_document_number=erp_document_number,
                        response_data=rows[0],
                        connector_name=self.connector_name,
                        duration_ms=elapsed,
                    )
                return ERPSubmissionResult(
                    success=False,
                    status="NOT_FOUND",
                    erp_document_number=erp_document_number,
                    connector_name=self.connector_name,
                    duration_ms=elapsed,
                )
            finally:
                conn.close()
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

    def _do_query(
        self, query_key: str, params: list, label: str
    ) -> ERPResolutionResult:
        """Execute a parameterized SELECT and return results."""
        query = self._queries.get(query_key)
        if not query:
            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.NONE,
                connector_name=self.connector_name,
                reason=f"No query configured for '{query_key}'",
            )

        start = time.time()
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = _rows_to_dicts(cursor)
                elapsed = int((time.time() - start) * 1000)

                if rows:
                    return ERPResolutionResult(
                        resolved=True,
                        value={"results": rows, **rows[0]},
                        source_type=ERPSourceType.API,
                        confidence=1.0,
                        connector_name=self.connector_name,
                        reason=f"MySQL {label} lookup found {len(rows)} row(s)",
                        metadata={
                            "duration_ms": elapsed,
                            "row_count": len(rows),
                        },
                    )

                return ERPResolutionResult(
                    resolved=False,
                    source_type=ERPSourceType.API,
                    connector_name=self.connector_name,
                    reason=f"MySQL {label} lookup returned no rows",
                    metadata={"duration_ms": elapsed, "row_count": 0},
                )
            finally:
                conn.close()

        except ImportError:
            logger.error("mysqlclient is not installed -- cannot use MySQLERPConnector")
            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.NONE,
                connector_name=self.connector_name,
                reason="mysqlclient is not installed",
            )

        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.exception("MySQL error for %s lookup", label)
            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.NONE,
                connector_name=self.connector_name,
                reason=f"MySQL error: {type(exc).__name__}: {str(exc)[:200]}",
                metadata={"duration_ms": elapsed, "error": str(exc)[:200]},
            )

    def _do_submit(
        self, query_key: str, payload: Dict[str, Any]
    ) -> ERPSubmissionResult:
        """Execute a write query (INSERT/CALL) for invoice submission."""
        query = self._queries.get(query_key)
        if not query:
            return ERPSubmissionResult(
                success=False,
                status="UNSUPPORTED",
                error_message=f"No '{query_key}' query configured",
                connector_name=self.connector_name,
                duration_ms=0,
            )

        start = time.time()
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                cursor.execute(query, list(payload.values()))
                conn.commit()
                elapsed = int((time.time() - start) * 1000)
                return ERPSubmissionResult(
                    success=True,
                    status="SUBMITTED",
                    connector_name=self.connector_name,
                    duration_ms=elapsed,
                    response_data={"rows_affected": cursor.rowcount},
                )
            finally:
                conn.close()
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.exception("MySQL submit error for %s", query_key)
            return ERPSubmissionResult(
                success=False,
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )
