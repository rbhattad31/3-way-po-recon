"""Connector Factory — creates connector instances from ERPConnection records."""
from __future__ import annotations

import logging
from typing import Optional

from apps.erp_integration.enums import ERPConnectionStatus, ERPConnectorType
from apps.erp_integration.models import ERPConnection
from apps.erp_integration.services.connectors.base import BaseERPConnector
from apps.erp_integration.services.connectors.custom_erp import CustomERPConnector
from apps.erp_integration.services.connectors.dynamics import DynamicsConnector
from apps.erp_integration.services.connectors.salesforce import SalesforceConnector
from apps.erp_integration.services.connectors.zoho import ZohoConnector

logger = logging.getLogger(__name__)

_CONNECTOR_MAP = {
    ERPConnectorType.CUSTOM: CustomERPConnector,
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
        }
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
                is_active=True,
            )
            .first()
        )
        if connection is None:
            logger.debug("ERP connection '%s' not found or inactive", name)
            return None
        return ConnectorFactory.create_from_connection(connection)
