from __future__ import annotations

from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import CompanyProfile
from apps.erp_integration.enums import ERPConnectorType, ERPSourceType
from apps.erp_integration.models import ERPConnection
from apps.erp_integration.services.connectors.voucher_sqlserver import (
    VoucherSQLServerERPConnector,
)


def _mock_connection(rows):
    cursor = mock.MagicMock()
    if rows:
        cursor.description = [(key,) for key in rows[0].keys()]
        cursor.fetchall.return_value = [tuple(row.values()) for row in rows]
    else:
        cursor.description = [("placeholder",)]
        cursor.fetchall.return_value = []

    connection = mock.MagicMock()
    connection.cursor.return_value = cursor
    return connection, cursor


class VoucherSQLServerERPConnectorTests(SimpleTestCase):
    def setUp(self):
        self.connector = VoucherSQLServerERPConnector(
            {
                "timeout_seconds": 30,
                "metadata_json": {
                    "voucher_series": {
                        "purchase_invoice": "App PI%",
                        "purchase_order": "App PO%",
                    }
                },
            }
        )

    def test_vendor_lookup_returns_normalized_payload(self):
        connection, cursor = _mock_connection(
            [
                {
                    "vendor_name": "Acme Supplies",
                    "vendor_code": "V001",
                    "gstin": "29ABCDE1234F1Z5",
                    "pan_no": "ABCDE1234F",
                    "credit_period_days": 45,
                    "address1": "Industrial Area",
                    "address2": "Phase 2",
                    "city": "Bengaluru",
                    "state": "KA",
                    "country": "IN",
                }
            ]
        )
        with mock.patch.object(self.connector, "_connect", return_value=connection):
            result = self.connector.lookup_vendor(vendor_code="V001")

        self.assertTrue(result.resolved)
        self.assertEqual(result.source_type, ERPSourceType.API)
        self.assertEqual(result.value["vendor_code"], "V001")
        cursor.execute.assert_called_once()
        query, params = cursor.execute.call_args.args
        self.assertIn("Master_Table", query)
        self.assertEqual(params, ["V001", "", "V001"])

    def test_po_lookup_uses_configured_voucher_series(self):
        connection, cursor = _mock_connection(
            [
                {
                    "po_series": "App PO 24-25",
                    "po_number": 1001,
                    "po_date": "2026-04-01",
                    "vendor_name": "Acme Supplies",
                    "vendor_po_reference": "PO-EXT-88",
                    "total_amount": 12500.0,
                    "currency": "INR",
                    "remarks": "Urgent order",
                }
            ]
        )
        with mock.patch.object(self.connector, "_connect", return_value=connection):
            result = self.connector.lookup_po(po_number="1001", vendor_code="Acme Supplies")

        self.assertTrue(result.resolved)
        self.assertEqual(result.value["po_number"], 1001)
        query, params = cursor.execute.call_args.args
        self.assertIn("LIKE 'App PO%'", query)
        self.assertEqual(params, ["1001", "1001", "Acme Supplies", "Acme Supplies"])

    def test_grn_lookup_returns_efi_receipt_payload(self):
        connection, cursor = _mock_connection(
            [
                {
                    "grn_number": "GRN-45",
                    "receipt_date": "2026-04-02",
                    "po_number": 1001,
                    "po_date": "2026-04-01",
                    "supplier_code": "SUP-01",
                    "supplier_name": "Acme Supplies",
                    "item_code": "ITEM-1",
                    "item_description": "Steel Coil",
                    "grn_qty": 10,
                    "grn_price": 100,
                    "grn_value": 1000,
                    "currency": "INR",
                }
            ]
        )
        with mock.patch.object(self.connector, "_connect", return_value=connection):
            result = self.connector.lookup_grn(po_number="1001", grn_number="GRN-45")

        self.assertTrue(result.resolved)
        self.assertEqual(result.value["grn_number"], "GRN-45")
        query, params = cursor.execute.call_args.args
        self.assertIn("EFIMRDetailsTable", query)
        self.assertEqual(params, ["1001", "GRN-45", "GRN-45"])

    def test_duplicate_invoice_check_sets_duplicate_flags(self):
        connection, _cursor = _mock_connection(
            [
                {
                    "invoice_number": "INV-100",
                    "document_date": "2026-04-20",
                    "vendor_name": "Acme Supplies",
                    "amount": 1500,
                    "voucher_series": "App PI 24-25",
                    "voucher_no": 7001,
                }
            ]
        )
        with mock.patch.object(self.connector, "_connect", return_value=connection):
            result = self.connector.check_duplicate_invoice(
                invoice_number="INV-100",
                vendor_code="Acme Supplies",
                fiscal_year="2026",
            )

        self.assertTrue(result.resolved)
        self.assertTrue(result.value["is_duplicate"])
        self.assertEqual(result.value["duplicate_count"], 1)

    def test_lookup_returns_not_resolved_when_no_rows(self):
        connection, _cursor = _mock_connection([])
        with mock.patch.object(self.connector, "_connect", return_value=connection):
            result = self.connector.lookup_item(item_code="ITEM-X")

        self.assertFalse(result.resolved)
        self.assertEqual(result.source_type, ERPSourceType.API)


class SeedVoucherSQLServerConnectionCommandTests(TestCase):
    def test_command_creates_prefilled_connection_for_tenant(self):
        tenant = CompanyProfile.objects.create(name="Demo Tenant", is_default=True)

        call_command(
            "seed_voucher_sqlserver_connection",
            "--tenant",
            str(tenant.pk),
            "--name",
            "demo-voucher-erp",
            "--set-default",
        )

        connection = ERPConnection.objects.get(name="demo-voucher-erp", tenant=tenant)
        self.assertEqual(connection.connector_type, ERPConnectorType.VOUCHER_SQLSERVER)
        self.assertTrue(connection.is_default)
        self.assertEqual(connection.connection_string_env, "CLIENT_ERP_SQLSERVER_CONNECTION_STRING")
        self.assertEqual(
            connection.metadata_json["voucher_series"]["purchase_invoice"],
            "App PI%",
        )

    def test_command_updates_existing_profile(self):
        tenant = CompanyProfile.objects.create(name="Tenant Two")
        ERPConnection.objects.create(
            tenant=tenant,
            name="demo-voucher-erp",
            connector_type=ERPConnectorType.VOUCHER_SQLSERVER,
            status="INACTIVE",
            metadata_json={"voucher_series": {"purchase_invoice": "OLD%", "purchase_order": "OLDPO%"}},
        )

        call_command(
            "seed_voucher_sqlserver_connection",
            "--tenant",
            str(tenant.pk),
            "--name",
            "demo-voucher-erp",
            "--purchase-invoice-series",
            "NEW PI%",
            "--purchase-order-series",
            "NEW PO%",
            "--activate",
        )

        connection = ERPConnection.objects.get(name="demo-voucher-erp", tenant=tenant)
        self.assertEqual(connection.metadata_json["voucher_series"]["purchase_invoice"], "NEW PI%")
        self.assertEqual(connection.metadata_json["voucher_series"]["purchase_order"], "NEW PO%")
        self.assertEqual(connection.status, "ACTIVE")
