"""Connector Factory — creates connector instances from ERPConnection records."""
from __future__ import annotations

import logging
from typing import Optional

from apps.erp_integration.enums import ERPConnectionStatus, ERPConnectorType
from apps.erp_integration.models import ERPConnection
from apps.erp_integration.services.connectors.base import BaseERPConnector
from apps.erp_integration.services.connectors.custom_erp import CustomERPConnector
from apps.erp_integration.services.connectors.dynamics import DynamicsConnector
from apps.erp_integration.services.connectors.mysql import MySQLERPConnector
from apps.erp_integration.services.connectors.salesforce import SalesforceConnector
from apps.erp_integration.services.connectors.sqlserver import SQLServerERPConnector
from apps.erp_integration.services.connectors.zoho import ZohoConnector

logger = logging.getLogger(__name__)

_CONNECTOR_MAP = {
    ERPConnectorType.CUSTOM: CustomERPConnector,
    ERPConnectorType.SQLSERVER: SQLServerERPConnector,
    ERPConnectorType.MYSQL: MySQLERPConnector,
    ERPConnectorType.DYNAMICS: DynamicsConnector,
    ERPConnectorType.ZOHO: ZohoConnector,
    ERPConnectorType.SALESFORCE: SalesforceConnector,
}


class ConnectorFactory:
    """Creates ERP connector instances from ERPConnection records."""

    @staticmethod
    def create_from_connection(connection: ERPConnection) -> BaseERPConnector:
        """Instantiate the appropriate connector for a given ERPConnection."""
        connector_cls = _CONNECTOR_MAP.get(connection.connector_type)
        if connector_cls is None:
            raise ValueError(
                f"Unknown connector type: {connection.connector_type}"
            )
        config = {
            "base_url": connection.base_url,
            "timeout_seconds": connection.timeout_seconds,
            "auth_config_json": connection.auth_config_json or {},
            "metadata_json": connection.metadata_json or {},
            "connection_id": connection.id,
            "connection_name": connection.name,
            # Typed credential fields
            "auth_type": connection.auth_type,
            "api_key_env": connection.api_key_env,
            "connection_string_env": connection.connection_string_env,
            "database_name": connection.database_name,
            "db_host": connection.db_host,
            "db_port": connection.db_port,
            "db_username": connection.db_username,
            "db_password_encrypted": connection.db_password_encrypted,
            "db_driver": connection.db_driver,
            "db_trust_cert": connection.db_trust_cert,
            "tenant_id": connection.tenant_id,
            "client_id_env": connection.client_id_env,
            "client_secret_env": connection.client_secret_env,
        }
        return connector_cls(config)

    @staticmethod
    def create_from_config(config: dict) -> BaseERPConnector:
        """Instantiate a connector from a raw config dict (no saved record needed).

        The dict must include ``connector_type`` plus the fields relevant
        to that connector type.
        """
        connector_type = config.get("connector_type", "")
        connector_cls = _CONNECTOR_MAP.get(connector_type)
        if connector_cls is None:
            raise ValueError(f"Unknown connector type: {connector_type}")
        return connector_cls(config)

    @staticmethod
    def get_default_connector() -> Optional[BaseERPConnector]:
        """Return the default active ERP connector, or None if none configured."""
        connection = (
            ERPConnection.objects.filter(
                is_default=True,
                status=ERPConnectionStatus.ACTIVE,
            )
            .first()
        )
        if connection is None:
            logger.debug("No default active ERP connection configured")
            return None
        return ConnectorFactory.create_from_connection(connection)

    @staticmethod
    def get_connector_by_name(name: str) -> Optional[BaseERPConnector]:
        """Return a connector by connection name, or None if not found/inactive."""
        connection = (
            ERPConnection.objects.filter(
                name=name,
                status=ERPConnectionStatus.ACTIVE,
            )
            .first()
        )
        if connection is None:
            logger.debug("ERP connection '%s' not found or inactive", name)
            return None
        return ConnectorFactory.create_from_connection(connection)
