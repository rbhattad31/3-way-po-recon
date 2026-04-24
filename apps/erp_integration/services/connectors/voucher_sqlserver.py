"""Voucher-based SQL Server ERP Connector.

This connector is designed for legacy ERP schemas where AP documents are
stored in shared transaction tables and identified by voucher series.
It extends the generic SQLServerERPConnector with ERP-specific default
queries discovered from the client schema.
"""
from __future__ import annotations

from typing import Any, Dict

from apps.erp_integration.services.connectors.sqlserver import (
    SQLServerERPConnector,
)


# Default queries for voucher-based ERP schemas.
#
# NOTE:
# - All lookups are parameterized and safe for pyodbc execution.
# - Voucher-series patterns can be overridden via metadata_json:
#   {
#     "voucher_series": {
#       "purchase_invoice": "App PI%",
#       "purchase_order": "App PO%"
#     }
#   }
DEFAULT_VOUCHER_QUERIES: Dict[str, str] = {
    # Single-record lookups for reconciliation queries
    "vendor_lookup": (
        "SELECT TOP 1 "
        "  mt.MasterName AS vendor_name, "
        "  mmc.MasterCode AS vendor_code, "
        "  mrd.GSTIN AS gstin, "
        "  mrd.PANo AS pan_no, "
        "  mp.CreditPeriod AS credit_period_days, "
        "  ma.Address1 AS address1, "
        "  ma.Address2 AS address2, "
        "  ma.City AS city, "
        "  ma.[State] AS [state], "
        "  ma.Country AS country "
        "FROM Master_Table mt "
        "LEFT JOIN Master_MasterCodes_Table mmc ON mmc.MasterName = mt.MasterName "
        "LEFT JOIN Master_RegistrationDetails_Table mrd ON mrd.MasterName = mt.MasterName "
        "LEFT JOIN Master_Preferences_Table mp ON mp.MasterName = mt.MasterName "
        "LEFT JOIN Master_Address_Table ma ON ma.MasterName = mt.MasterName "
        "WHERE (mt.MasterType LIKE '%creditor%' OR mt.MasterType LIKE '%supplier%') "
        "  AND (mmc.MasterCode = ? OR mt.MasterName LIKE '%' + ? + '%') "
        "ORDER BY CASE WHEN mmc.MasterCode = ? THEN 0 ELSE 1 END"
    ),
    "item_lookup": (
        "SELECT TOP 1 "
        "  mt.MasterName AS item_name, "
        "  mmc.MasterCode AS item_code, "
        "  md.Description AS item_description, "
        "  mip.BaseUnit AS unit_of_measure, "
        "  mip.PurchaseAccount AS purchase_account "
        "FROM Master_Table mt "
        "LEFT JOIN Master_MasterCodes_Table mmc ON mmc.MasterName = mt.MasterName "
        "LEFT JOIN Master_Description_Table md ON md.MasterName = mt.MasterName "
        "LEFT JOIN Master_InventoryPreferences_Table mip ON mip.MasterName = mt.MasterName "
        "WHERE (mt.MasterType LIKE '%stock%' OR mt.MasterType LIKE '%item%' OR mt.MasterType LIKE '%product%') "
        "  AND (mmc.MasterCode = ? OR mt.MasterName LIKE '%' + ? + '%') "
        "ORDER BY CASE WHEN mmc.MasterCode = ? THEN 0 ELSE 1 END"
    ),
    "tax_lookup": (
        "SELECT TOP 1 "
        "  tct.Taxes AS tax_code, "
        "  tct.TaxComponent AS tax_component, "
        "  mtr.NewRate AS rate, "
        "  tc.TransactionType AS transaction_type, "
        "  tc.TaxAccount AS tax_account "
        "FROM Taxes_TaxComponents_Table tct "
        "LEFT JOIN Master_TaxRates_Table mtr ON mtr.MasterName = tct.Taxes "
        "LEFT JOIN TaxConfigurationDetails_Table tc ON tc.MasterName = tct.Taxes "
        "WHERE tct.Taxes = ? OR ISNULL(mtr.NewRate, 0) = ?"
    ),
    "cost_center_lookup": (
        "SELECT TOP 1 "
        "  CostCentre AS cost_center_code, "
        "  CostCentre AS description, "
        "  CAST(1 AS bit) AS is_active "
        "FROM Ledger_Table "
        "WHERE CostCentre = ?"
    ),
    "po_lookup": (
        "SELECT TOP 1 "
        "  th.VoucherSeries AS po_series, "
        "  CASE "
        "    WHEN th.PartyRefDoc LIKE '%/%' THEN th.PartyRefDoc "
        "    ELSE CAST(th.VoucherNo AS nvarchar(50)) "
        "  END AS po_number, "
        "  th.[Date] AS po_date, "
        "  th.Account AS vendor_name, "
        "  th.PartyRefDoc AS vendor_po_reference, "
        "  th.TotalBillValue AS total_amount, "
        "  th.Currency AS currency, "
        "  th.Remarks AS remarks "
        "FROM Transaction_Header_Table th "
        "WHERE th.VoucherSeries LIKE 'App PO%' "
        "  AND (CAST(th.VoucherNo AS nvarchar(50)) = ? OR th.PartyRefDoc = ?) "
        "  AND (? IS NULL OR th.Account = ?) "
        "ORDER BY th.[Date] DESC"
    ),
    "grn_lookup": (
        "SELECT TOP 1 "
        "  em.GRNNO AS grn_number, "
        "  em.GRNDATE AS receipt_date, "
        "  em.POrderNum AS po_number, "
        "  em.POrderDate AS po_date, "
        "  em.Suppcode AS supplier_code, "
        "  em.SuppName AS supplier_name, "
        "  em.ItemCode AS item_code, "
        "  em.ItemDesc AS item_description, "
        "  em.GRNQTY AS grn_qty, "
        "  em.GRNPRICE AS grn_price, "
        "  em.GRNVALUE AS grn_value, "
        "  em.CURRENCYCODE AS currency "
        "FROM EFIMRDetailsTable em "
        "WHERE CAST(em.POrderNum AS varchar(60)) = ? "
        "  AND (? IS NULL OR em.GRNNO = ?) "
        "ORDER BY em.GRNDATE DESC"
    ),
    "duplicate_check": (
        "SELECT "
        "  tp.SupplierInvNo AS invoice_number, "
        "  tp.SupplierInvDate AS document_date, "
        "  tp.PartyAccount AS vendor_name, "
        "  tp.NetAmount AS amount, "
        "  tp.VoucherSeries AS voucher_series, "
        "  tp.VoucherNo AS voucher_no "
        "FROM Transaction_Payments_Table tp "
        "WHERE tp.SupplierInvNo = ? "
        "  AND tp.PartyAccount = ? "
        "  AND (? IS NULL OR CONVERT(nvarchar(4), YEAR(ISNULL(tp.SupplierInvDate, GETDATE()))) = LEFT(?, 4))"
    ),
    # Bulk import queries (return ALL records, not just first match)
    # These are used by DirectERPImporter for reference data sync
    # Updated to use actual Azure SQL test schema columns
    "vendor_bulk": (
        "SELECT DISTINCT "
        "  ISNULL(mmc.MasterCode, mt.MasterName) AS vendor_code, "
        "  mt.MasterName AS vendor_name, "
        "  mt.MasterType AS vendor_group, "
        "  UPPER(LTRIM(RTRIM(ISNULL(mrd.GSTIN, '')))) AS tax_id, "
        "  1 AS is_active "
        "FROM Master_Table mt "
        "LEFT JOIN Master_MasterCodes_Table mmc ON mmc.MasterName = mt.MasterName "
        "LEFT JOIN Master_RegistrationDetails_Table mrd ON mrd.MasterName = mt.MasterName "
        "WHERE (mt.MasterType LIKE '%creditor%' OR mt.MasterType LIKE '%supplier%') "
        "ORDER BY mt.MasterName"
    ),
    "item_bulk": (
        "SELECT DISTINCT "
        "  tib.Code AS item_code, "
        "  tib.Product AS item_name, "
        "  tib.[Description] AS [description], "
        "  tib.[Unit] AS unit_of_measure, "
        "  tib.[Department] AS category, "
        "  1 AS is_active, "
        "  'Item' AS item_group "
        "FROM Transaction_ItemBody_Table tib "
        "WHERE tib.Code IS NOT NULL "
        "ORDER BY tib.Code"
    ),
    "tax_bulk": (
        "SELECT DISTINCT "
        "  'SGST' AS tax_code, "
        "  'SGST' AS tax_component, "
        "  1 AS is_active "
        "UNION ALL "
        "SELECT 'CGST', 'CGST', 1 "
        "UNION ALL "
        "SELECT 'IGST', 'IGST', 1"
    ),
    "cost_center_bulk": (
        "SELECT "
        "  ISNULL(tib.CostCentre, 'DEFAULT') AS cost_center_code, "
        "  ISNULL(tib.CostCentre, 'DEFAULT') AS description, "
        "  ISNULL(tib.[Department], '') AS department, "
        "  1 AS is_active "
        "FROM Transaction_ItemBody_Table tib "
        "WHERE tib.CostCentre IS NOT NULL "
        "GROUP BY tib.CostCentre, tib.[Department] "
        "ORDER BY tib.CostCentre"
    ),
    "po_bulk": (
        "SELECT "
        "  CASE "
        "    WHEN th.PartyRefDoc LIKE '%/%' THEN th.PartyRefDoc "
        "    ELSE CAST(th.VoucherNo AS nvarchar(50)) "
        "  END AS po_number, "
        "  ISNULL(CAST(tib.VoucherLineNo AS nvarchar(20)), '1') AS po_line_number, "
        "  ISNULL(mmc.MasterCode, th.Account) AS vendor_code, "
        "  ISNULL(mmc.MasterCode, th.Account) AS purchase_account, "
        "  th.Account AS vendor_name, "
        "  ISNULL(tib.Code, '') AS item_code, "
        "  ISNULL(tib.[Description], ISNULL(tib.Product, th.Remarks)) AS description, "
        "  ISNULL(tib.Quantity, 0) AS quantity, "
        "  ISNULL(tib.[Unit], 'UNIT') AS uom, "
        "  ISNULL(tib.Rate, 0) AS unit_price, "
        "  ISNULL(tib.Net, ISNULL(th.TotalBillValue, 0)) AS total_amount, "
        "  th.Remarks AS remarks, "
        "  th.[Date] AS po_date, "
        "  th.Currency AS currency, "
        "  'OPEN' AS [status], "
        "  1 AS is_open "
        "FROM Transaction_Header_Table th "
        "LEFT JOIN Transaction_ItemBody_Table tib "
        "  ON tib.VoucherSeries = th.VoucherSeries AND tib.VoucherNo = th.VoucherNo "
        "LEFT JOIN Master_MasterCodes_Table mmc ON mmc.MasterName = th.Account "
        "WHERE th.VoucherSeries LIKE 'App PO%' AND th.VoucherNo IS NOT NULL "
        "ORDER BY th.[Date] DESC, th.VoucherNo, tib.VoucherLineNo"
    ),
}



class VoucherSQLServerERPConnector(SQLServerERPConnector):
    """SQL Server connector pre-configured for voucher-based ERP schemas."""

    connector_name = "voucher_sqlserver"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        super().__init__(connection_config)

        meta = connection_config.get("metadata_json") or {}
        series_cfg = meta.get("voucher_series") or {}
        purchase_invoice_series = series_cfg.get("purchase_invoice", "App PI%")
        purchase_order_series = series_cfg.get("purchase_order", "App PO%")

        base_queries = {
            key: value.replace("App PI%", purchase_invoice_series).replace("App PO%", purchase_order_series)
            for key, value in DEFAULT_VOUCHER_QUERIES.items()
        }
        user_queries = meta.get("queries") or {}
        self._queries = {**base_queries, **user_queries}

    def lookup_po(
        self, po_number: str = "", vendor_code: str = "", **kw
    ):
        vendor_name = vendor_code or None
        return self._do_query(
            "po_lookup",
            [po_number, po_number, vendor_name, vendor_name],
            "po",
        )
