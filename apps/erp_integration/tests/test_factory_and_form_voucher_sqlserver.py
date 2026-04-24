from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import CompanyProfile
from apps.erp_integration.enums import ERPConnectionStatus, ERPConnectorType
from apps.erp_integration.forms import ERPConnectionForm
from apps.erp_integration.models import ERPConnection
from apps.erp_integration.services.connector_factory import ConnectorFactory
from apps.erp_integration.services.connectors.voucher_sqlserver import (
    VoucherSQLServerERPConnector,
)


class VoucherSQLServerFactoryTests(TestCase):
    def test_factory_create_from_config_returns_voucher_connector(self):
        connector = ConnectorFactory.create_from_config(
            {
                "connector_type": ERPConnectorType.VOUCHER_SQLSERVER,
                "metadata_json": {
                    "voucher_series": {
                        "purchase_invoice": "App PI%",
                        "purchase_order": "App PO%",
                    }
                },
            }
        )

        self.assertIsInstance(connector, VoucherSQLServerERPConnector)

    def test_factory_create_from_connection_preserves_metadata(self):
        tenant = CompanyProfile.objects.create(name="Factory Tenant")
        connection = ERPConnection.objects.create(
            tenant=tenant,
            name="voucher-factory-profile",
            connector_type=ERPConnectorType.VOUCHER_SQLSERVER,
            status=ERPConnectionStatus.ACTIVE,
            metadata_json={
                "voucher_series": {
                    "purchase_invoice": "LIVE PI%",
                    "purchase_order": "LIVE PO%",
                }
            },
        )

        connector = ConnectorFactory.create_from_connection(connection)

        self.assertIsInstance(connector, VoucherSQLServerERPConnector)
        self.assertEqual(
            connector.config["metadata_json"]["voucher_series"]["purchase_invoice"],
            "LIVE PI%",
        )


class VoucherSQLServerFormTests(TestCase):
    def test_form_accepts_voucher_sqlserver_connector_type(self):
        form = ERPConnectionForm(
            data={
                "name": "voucher-form-profile",
                "connector_type": ERPConnectorType.VOUCHER_SQLSERVER,
                "status": ERPConnectionStatus.ACTIVE,
                "timeout_seconds": 30,
                "is_default": True,
                "base_url": "",
                "auth_type": "",
                "api_key_env": "",
                "connection_string_env": "CLIENT_ERP_SQLSERVER_CONNECTION_STRING",
                "db_host": "",
                "db_port": 1433,
                "database_name": "",
                "db_username": "",
                "db_driver": "ODBC Driver 17 for SQL Server",
                "db_trust_cert": True,
                "erp_tenant_id": "",
                "client_id_env": "",
                "client_secret_env": "",
                "metadata_json": '{"voucher_series": {"purchase_invoice": "App PI%", "purchase_order": "App PO%"}}',
                "db_password": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        connection = form.save()
        self.assertEqual(connection.connector_type, ERPConnectorType.VOUCHER_SQLSERVER)
        self.assertEqual(
            connection.metadata_json["voucher_series"]["purchase_order"],
            "App PO%",
        )

    def test_form_default_toggle_unsets_previous_default(self):
        ERPConnection.objects.create(
            name="existing-default",
            connector_type=ERPConnectorType.SQLSERVER,
            status=ERPConnectionStatus.ACTIVE,
            is_default=True,
        )

        form = ERPConnectionForm(
            data={
                "name": "new-voucher-default",
                "connector_type": ERPConnectorType.VOUCHER_SQLSERVER,
                "status": ERPConnectionStatus.ACTIVE,
                "timeout_seconds": 30,
                "is_default": True,
                "base_url": "",
                "auth_type": "",
                "api_key_env": "",
                "connection_string_env": "CLIENT_ERP_SQLSERVER_CONNECTION_STRING",
                "db_host": "",
                "db_port": 1433,
                "database_name": "",
                "db_username": "",
                "db_driver": "ODBC Driver 17 for SQL Server",
                "db_trust_cert": True,
                "erp_tenant_id": "",
                "client_id_env": "",
                "client_secret_env": "",
                "metadata_json": '{}',
                "db_password": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        new_default = form.save()

        self.assertTrue(new_default.is_default)
        self.assertFalse(ERPConnection.objects.get(name="existing-default").is_default)
