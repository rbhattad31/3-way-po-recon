"""SQL Server ERP Connector -- direct database queries against an ERP's SQL Server.

Endpoint paths are replaced by SQL queries, configured via
``metadata_json["queries"]`` on the ERPConnection record.  Missing keys
fall back to the built-in defaults defined in ``DEFAULT_QUERIES``.

Connection string is resolved from an env-var reference stored in
``auth_config_json["connection_string_env"]``.
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
from apps.erp_integration.services.secrets_resolver import resolve_secret

logger = logging.getLogger(__name__)


_TRANSIENT_SQL_ERROR_CODES = {
    "40613",  # Database not currently available (Azure SQL)
    "40197",  # Service encountered an error processing request
    "40501",  # Service busy / throttled
    "49918",  # Not enough resources to process request
    "49919",  # Too many create/update operations in progress
    "49920",  # Too many operations in progress for subscription
    "HYT00",  # Timeout expired
    "08001",  # Client unable to establish connection
}


def _is_transient_sql_error(exc: Exception) -> bool:
    """Best-effort transient SQL error detection.

    Works for pyodbc tuple-shaped messages and plain string messages.
    """
    msg = str(exc or "")
    if not msg:
        return False

    if any(f"({code})" in msg for code in _TRANSIENT_SQL_ERROR_CODES):
        return True

    lowered = msg.lower()
    transient_markers = (
        "not currently available",
        "retry the connection later",
        "service is currently busy",
        "timeout expired",
        "temporarily unavailable",
        "connection timeout",
    )
    return any(marker in lowered for marker in transient_markers)


# ============================================================================
# Default SQL queries -- one per resolution type.
#
# Placeholders use %(name)s style (pyodbc paramstyle = "pyformat" when
# used via cursor.execute with a dict).  Callers should NEVER interpolate
# user input into these strings; always pass params separately.
# ============================================================================

DEFAULT_QUERIES: Dict[str, str] = {
    # ---- Lookups ----
    "vendor_lookup": (
        "SELECT TOP 1 vendor_code, vendor_name, address, city, country, "
        "currency, tax_number, is_active "
        "FROM vendors "
        "WHERE (vendor_code = ? OR vendor_name LIKE '%' + ? + '%') "
        "ORDER BY CASE WHEN vendor_code = ? THEN 0 ELSE 1 END"
    ),
    "item_lookup": (
        "SELECT TOP 1 item_code, description, unit_of_measure, "
        "item_group, is_active "
        "FROM items "
        "WHERE (item_code = ? OR description LIKE '%' + ? + '%') "
        "ORDER BY CASE WHEN item_code = ? THEN 0 ELSE 1 END"
    ),
    "tax_lookup": (
        "SELECT TOP 1 tax_code, description, rate "
        "FROM tax_codes "
        "WHERE tax_code = ? OR rate = ?"
    ),
    "cost_center_lookup": (
        "SELECT TOP 1 cost_center_code, description, is_active "
        "FROM cost_centers "
        "WHERE cost_center_code = ?"
    ),
    "po_lookup": (
        "SELECT TOP 1 po_number, vendor_code, po_date, status, "
        "total_amount, currency "
        "FROM purchase_orders "
        "WHERE po_number = ? "
        "AND (? IS NULL OR vendor_code = ?)"
    ),
    "grn_lookup": (
        "SELECT TOP 1 grn_number, po_number, receipt_date, status "
        "FROM goods_receipt_notes "
        "WHERE po_number = ? "
        "AND (? IS NULL OR grn_number = ?)"
    ),
    "duplicate_check": (
        "SELECT invoice_number, vendor_code, fiscal_year, status, "
        "document_date "
        "FROM invoices "
        "WHERE invoice_number = ? AND vendor_code = ? "
        "AND (? IS NULL OR fiscal_year = ?)"
    ),
}


def _rows_to_dicts(cursor) -> List[Dict[str, Any]]:
    """Convert pyodbc cursor rows to a list of dicts."""
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class SQLServerERPConnector(BaseERPConnector):
    """Connector that queries an ERP's SQL Server database directly.

    Configuration (via ERPConnection record):
        auth_config_json:
            connection_string_env  -- env-var name holding the ODBC
                                      connection string (REQUIRED)
        metadata_json:
            queries  -- dict of query overrides (optional; missing keys
                        use DEFAULT_QUERIES)
            query_timeout  -- per-query timeout in seconds (default 30)

    The connection string is resolved at first use and cached for the
    connector's lifetime.
    """

    connector_name = "sqlserver"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)
        self.timeout = connection_config.get("timeout_seconds", 30)
        self._conn_str: Optional[str] = None

        meta = connection_config.get("metadata_json") or {}
        user_queries = meta.get("queries") or {}
        self._queries: Dict[str, str] = {**DEFAULT_QUERIES, **user_queries}
        self._query_timeout: int = meta.get("query_timeout", 30)
        self._retry_attempts: int = int(meta.get("retry_attempts", 2) or 2)
        self._retry_backoff_seconds: float = float(meta.get("retry_backoff_seconds", 0.5) or 0.5)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_connection_string(self) -> str:
        """Resolve the ODBC connection string.

        Priority:
        1. Env-var reference (connection_string_env typed field or legacy auth_config_json)
        2. Build from individual fields (db_host, db_port, db_username, db_password_encrypted, db_driver, database_name)
        """
        if self._conn_str is None:
            # Try env-var reference first.
            env_var = self.config.get("connection_string_env") or ""
            if not env_var:
                auth = self.config.get("auth_config_json") or {}
                env_var = auth.get("connection_string_env", "")

            if env_var:
                try:
                    self._conn_str = resolve_secret(env_var)
                except KeyError:
                    # Graceful fallback: if builder fields are present,
                    # use them instead of failing hard on missing env var.
                    if self.config.get("db_host"):
                        self._conn_str = self._build_connection_string()
                    else:
                        raise
            else:
                # Build from individual fields.
                self._conn_str = self._build_connection_string()
        return self._conn_str

    def _build_connection_string(self) -> str:
        """Build an ODBC connection string from individual config fields."""
        host = self.config.get("db_host") or ""
        if not host:
            raise ValueError(
                "SQL Server connection requires either 'connection_string_env' "
                "or individual fields (db_host, database_name, etc.)"
            )
        driver = self.config.get("db_driver") or "ODBC Driver 17 for SQL Server"
        database = self.config.get("database_name") or ""
        port = self.config.get("db_port") or 1433
        username = self.config.get("db_username") or ""

        # Decrypt password if present.
        password = ""
        encrypted_pw = self.config.get("db_password_encrypted") or ""
        if encrypted_pw:
            from apps.erp_integration.crypto import decrypt_value
            password = decrypt_value(encrypted_pw)

        parts = [
            f"Driver={{{driver}}}",
            f"Server={host},{port}",
            f"Database={database}",
        ]
        if username:
            parts.append(f"UID={username}")
            parts.append(f"PWD={password}")
        else:
            # Windows Integrated Auth (common on-prem with domain accounts)
            parts.append("Trusted_Connection=yes")

        # On-prem servers often use self-signed certs; ODBC Driver 18
        # defaults to Encrypt=yes which fails without this.
        if self.config.get("db_trust_cert"):
            parts.append("Encrypt=yes")
            parts.append("TrustServerCertificate=yes")

        return ";".join(parts) + ";"

    def _connect(self):
        """Return a new pyodbc connection (caller must close)."""
        import pyodbc  # deferred import -- pyodbc may not be installed

        return pyodbc.connect(
            self._get_connection_string(),
            timeout=self.timeout,
        )

    # ------------------------------------------------------------------
    # Capability checks
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    def test_connectivity(self) -> tuple:
        """Test connectivity by opening and closing a database connection."""
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                conn.close()
            return True, "Database connection successful."
        except ImportError:
            return False, "pyodbc is not installed. Run: pip install pyodbc"
        except Exception as exc:
            return False, f"Database connection failed: {exc}"

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
        # Posting writes require stored-proc or INSERT -- user must
        # supply custom queries.
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
        return self._do_query(
            "tax_lookup",
            [tax_code, rate],
            "tax",
        )

    def lookup_cost_center(
        self, cost_center_code: str = "", **kw
    ) -> ERPResolutionResult:
        return self._do_query(
            "cost_center_lookup",
            [cost_center_code],
            "cost_center",
        )

    def lookup_po(
        self, po_number: str = "", vendor_code: str = "", **kw
    ) -> ERPResolutionResult:
        vc = vendor_code or None
        return self._do_query(
            "po_lookup",
            [po_number, vc, vc],
            "po",
        )

    def lookup_grn(
        self, po_number: str = "", grn_number: str = "", **kw
    ) -> ERPResolutionResult:
        gn = grn_number or None
        return self._do_query(
            "grn_lookup",
            [po_number, gn, gn],
            "grn",
        )

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
        # For duplicate check, "resolved" means duplicates were *found*.
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
        """Execute a parameterized SELECT and return the first row."""
        query = self._queries.get(query_key)
        if not query:
            return ERPResolutionResult(
                resolved=False,
                source_type=ERPSourceType.NONE,
                connector_name=self.connector_name,
                reason=f"No query configured for '{query_key}'",
            )

        start = time.time()
        max_attempts = max(1, self._retry_attempts + 1)

        for attempt in range(1, max_attempts + 1):
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
                            reason=f"SQL {label} lookup found {len(rows)} row(s)",
                            metadata={
                                "duration_ms": elapsed,
                                "row_count": len(rows),
                                "attempt": attempt,
                            },
                        )

                    return ERPResolutionResult(
                        resolved=False,
                        source_type=ERPSourceType.API,
                        connector_name=self.connector_name,
                        reason=f"SQL {label} lookup returned no rows",
                        metadata={"duration_ms": elapsed, "row_count": 0, "attempt": attempt},
                    )
                finally:
                    conn.close()

            except ImportError:
                logger.error("pyodbc is not installed -- cannot use SQLServerERPConnector")
                return ERPResolutionResult(
                    resolved=False,
                    source_type=ERPSourceType.NONE,
                    connector_name=self.connector_name,
                    reason="pyodbc is not installed",
                )

            except Exception as exc:
                is_transient = _is_transient_sql_error(exc)
                if is_transient and attempt < max_attempts:
                    backoff = max(0.1, self._retry_backoff_seconds * attempt)
                    logger.warning(
                        "Transient SQL Server error for %s lookup (attempt %s/%s): %s -- retrying in %.1fs",
                        label,
                        attempt,
                        max_attempts,
                        str(exc)[:200],
                        backoff,
                    )
                    time.sleep(backoff)
                    continue

                elapsed = int((time.time() - start) * 1000)
                if is_transient:
                    logger.warning(
                        "SQL Server transient error persisted for %s lookup after %s attempt(s): %s",
                        label,
                        attempt,
                        str(exc)[:200],
                    )
                else:
                    logger.exception("SQL Server error for %s lookup", label)
                return ERPResolutionResult(
                    resolved=False,
                    source_type=ERPSourceType.NONE,
                    connector_name=self.connector_name,
                    reason=f"SQL error: {type(exc).__name__}: {str(exc)[:200]}",
                    metadata={
                        "duration_ms": elapsed,
                        "error": str(exc)[:200],
                        "attempts": attempt,
                        "transient": is_transient,
                    },
                )

    def execute_bulk_query(
        self, query_key: str, params: list = None
    ) -> List[Dict[str, Any]]:
        """Execute a bulk SELECT query and return ALL matching rows (not just the first).
        
        Used for data imports that need all records, not just the first match.
        
        Args:
            query_key: Key into self._queries to look up the SQL
            params: List of parameter values for parameterized query
            
        Returns:
            List of dict rows; empty list if no matches or query not found
            
        Raises:
            Exception if query not found or database error
        """
        query = self._queries.get(query_key)
        if not query:
            raise ValueError(f"No query configured for '{query_key}'")

        start = time.time()
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                rows = _rows_to_dicts(cursor)
                elapsed = int((time.time() - start) * 1000)
                
                logger.info(
                    "Bulk query '%s' returned %d rows in %dms",
                    query_key, len(rows), elapsed
                )
                return rows
            finally:
                conn.close()

        except ImportError:
            logger.error("pyodbc is not installed -- cannot use SQLServerERPConnector")
            raise

        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.exception("SQL Server error for bulk query '%s'", query_key)
            raise

    def _do_submit(
        self, query_key: str, payload: Dict[str, Any]
    ) -> ERPSubmissionResult:
        """Execute an INSERT/stored-proc for invoice submission."""
        query = self._queries.get(query_key)
        if not query:
            return ERPSubmissionResult(
                success=False,
                status="UNSUPPORTED",
                error_message=f"No '{query_key}' query configured in metadata_json",
                connector_name=self.connector_name,
                duration_ms=0,
            )

        start = time.time()
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                # Pass payload values as positional params in sorted key order
                # so the query can use ? placeholders in a known sequence.
                sorted_keys = sorted(payload.keys())
                cursor.execute(query, [payload[k] for k in sorted_keys])
                conn.commit()

                # Try to read back a result set (e.g. OUTPUT from stored proc)
                result_data: Dict[str, Any] = {}
                doc_number = ""
                if cursor.description:
                    rows = _rows_to_dicts(cursor)
                    if rows:
                        result_data = rows[0]
                        doc_number = str(
                            result_data.get("document_number", "")
                        )

                elapsed = int((time.time() - start) * 1000)
                return ERPSubmissionResult(
                    success=True,
                    status="SUCCESS",
                    erp_document_number=doc_number,
                    response_data=result_data,
                    connector_name=self.connector_name,
                    duration_ms=elapsed,
                )
            finally:
                conn.close()

        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.exception("SQL Server submission error for %s", query_key)
            return ERPSubmissionResult(
                success=False,
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
                connector_name=self.connector_name,
                duration_ms=elapsed,
            )
