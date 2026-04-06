"""Tests for ERP ConnectorFactory -- creates connector instances from ERPConnection records."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from apps.erp_integration.enums import (
    ERPConnectionStatus,
    ERPConnectorType,
)


@pytest.fixture
def _erp_connection(db):
    """Create a minimal ERPConnection record."""
    from apps.erp_integration.models import ERPConnection
    return ERPConnection.objects.create(
        name="test-erp-conn",
        connector_type=ERPConnectorType.CUSTOM,
        status=ERPConnectionStatus.ACTIVE,
        is_default=True,
        base_url="https://erp.example.com/api",
        timeout_seconds=30,
    )


class TestCreateFromConnection:
    """CF-01 to CF-03: create_from_connection()."""

    @pytest.mark.django_db
    def test_custom_connector(self, _erp_connection):
        """CF-01: CUSTOM type returns CustomERPConnector."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        from apps.erp_integration.services.connectors.custom_erp import CustomERPConnector
        connector = ConnectorFactory.create_from_connection(_erp_connection)
        assert isinstance(connector, CustomERPConnector)

    @pytest.mark.django_db
    def test_unknown_type_raises(self, _erp_connection):
        """CF-02: Unknown connector_type raises ValueError."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        _erp_connection.connector_type = "UNKNOWN_TYPE"
        _erp_connection.save()
        with pytest.raises(ValueError, match="[Uu]nknown"):
            ConnectorFactory.create_from_connection(_erp_connection)

    @pytest.mark.django_db
    def test_config_dict_populated(self, _erp_connection):
        """CF-03: Connection fields flow through to connector config."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.create_from_connection(_erp_connection)
        # BaseERPConnector stores config dict on self.config
        assert connector.config.get("base_url") == "https://erp.example.com/api"
        assert connector.config.get("timeout_seconds") == 30


class TestCreateFromConfig:
    """CFC-01 to CFC-02: create_from_config()."""

    def test_valid_config(self):
        """CFC-01: Dict with valid connector_type returns connector."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        from apps.erp_integration.services.connectors.custom_erp import CustomERPConnector
        config = {"connector_type": ERPConnectorType.CUSTOM, "base_url": "http://test"}
        connector = ConnectorFactory.create_from_config(config)
        assert isinstance(connector, CustomERPConnector)

    def test_invalid_type_raises(self):
        """CFC-02: Unknown connector_type raises ValueError."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        with pytest.raises(ValueError):
            ConnectorFactory.create_from_config({"connector_type": "BANANA"})


class TestGetDefaultConnector:
    """GDC-01 to GDC-03: get_default_connector()."""

    @pytest.mark.django_db
    def test_returns_connector(self, _erp_connection):
        """GDC-01: Active default connection returns a connector instance."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.get_default_connector()
        assert connector is not None

    @pytest.mark.django_db
    def test_no_default_returns_none(self, db):
        """GDC-02: No default connection returns None."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.get_default_connector()
        assert connector is None

    @pytest.mark.django_db
    def test_inactive_default_returns_none(self):
        """GDC-03: Inactive default connection returns None."""
        from apps.erp_integration.models import ERPConnection
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        ERPConnection.objects.create(
            name="inactive-conn",
            connector_type=ERPConnectorType.CUSTOM,
            status=ERPConnectionStatus.INACTIVE,
            is_default=True,
        )
        connector = ConnectorFactory.get_default_connector()
        assert connector is None


class TestGetConnectorByName:
    """GCN-01 to GCN-02: get_connector_by_name()."""

    @pytest.mark.django_db
    def test_found_by_name(self, _erp_connection):
        """GCN-01: Retrieves connector by name."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.get_connector_by_name("test-erp-conn")
        assert connector is not None

    @pytest.mark.django_db
    def test_not_found(self, db):
        """GCN-02: Non-existent name returns None."""
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.get_connector_by_name("does-not-exist")
        assert connector is None
