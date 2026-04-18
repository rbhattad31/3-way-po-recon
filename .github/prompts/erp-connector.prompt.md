---
mode: agent
description: "Add a new ERP connector with capability flags, factory registration, and resolver wiring"
---

# Add a New ERP Connector

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/ERP_INTEGRATION.md` -- full architecture, resolution chain (cache -> API -> DB fallback), connector types, adding new connectors (Sections 4, 5, 18)
- `docs/current_system_review/10_Integrations_and_External_Dependencies.md` -- ERP framework architecture, 6 connector types, live refresh policy, cache TTL
- `docs/POSTING_AGENT.md` -- how PostingMappingEngine uses connectors via `connector=` kwarg

### Source Files
- `apps/erp_integration/services/connectors/base.py` -- `BaseERPConnector`, `ERPResolutionResult`, `ERPSubmissionResult` (study capability flags and method signatures)
- `apps/erp_integration/services/connectors/custom_connector.py` -- example connector implementation (HTTP-based)
- `apps/erp_integration/services/connectors/dynamics_connector.py` -- example with OAuth token refresh
- `apps/erp_integration/services/connectors/sqlserver_connector.py` -- example with direct DB queries
- `apps/erp_integration/services/connector_factory.py` -- `ConnectorFactory`, `_CONNECTOR_MAP`, `get_default_connector()`, `get_connector_by_name()`
- `apps/erp_integration/enums.py` -- `ERPConnectorType`, `ERPConnectionStatus`, `ERPSourceType` (note: ERP enums live here, not in `apps/core/enums.py`)
- `apps/erp_integration/models.py` -- `ERPConnection` model (stores credentials, endpoint, connector_type)
- `apps/erp_integration/services/langfuse_helpers.py` -- `sanitize_erp_metadata()` (redacts API keys/tokens), `start_erp_span()`, `end_erp_span()`

### Comprehension Check
1. Every connector extends `BaseERPConnector` and overrides capability flags (`supports_vendor_lookup()`, etc.)
2. Capability flags return `bool` -- the resolver checks before calling the lookup method
3. Lookup methods return `ERPResolutionResult(found=bool, data=dict, source="erp_api", confidence=float)`
4. The factory maps `ERPConnectorType` enum values to connector classes in `_CONNECTOR_MAP`
5. Credentials/endpoints come from `ERPConnection.config_json` -- never hardcoded
6. `sanitize_erp_metadata()` must be applied before logging any connector config or response

---

## Steps

### 1. Add Enum Value

In `apps/erp_integration/enums.py`, add to `ERPConnectorType`:

```python
class ERPConnectorType(models.TextChoices):
    # ... existing values ...
    MY_ERP = "my_erp", "My ERP System"
```

### 2. Create Connector Class

In `apps/erp_integration/services/connectors/my_erp_connector.py`:

```python
import logging
from .base import BaseERPConnector, ERPResolutionResult, ERPSubmissionResult

logger = logging.getLogger(__name__)


class MyERPConnector(BaseERPConnector):
    """Connector for My ERP System."""

    def supports_vendor_lookup(self) -> bool:
        return True  # Set True for each capability this ERP exposes

    def supports_po_lookup(self) -> bool:
        return True

    # Override each supported lookup method:
    def lookup_vendor(self, vendor_id: str = None, vendor_name: str = None, **kwargs) -> ERPResolutionResult:
        endpoint = self.connection.config_json.get("api_endpoint", "")
        api_key = self.connection.config_json.get("api_key", "")
        # ... make API call ...
        # Return standardized result:
        return ERPResolutionResult(
            found=True,
            data={"vendor_code": "V001", "vendor_name": "Acme Corp"},
            source="erp_api",
            confidence=1.0,
        )
```

### 3. Register in Factory

In `apps/erp_integration/services/connector_factory.py`, add to `_CONNECTOR_MAP`:

```python
from apps.erp_integration.enums import ERPConnectorType
from apps.erp_integration.services.connectors.my_erp_connector import MyERPConnector

_CONNECTOR_MAP = {
    # ... existing entries ...
    ERPConnectorType.MY_ERP: MyERPConnector,
}
```

### 4. Create ERPConnection Record

Via admin or seed migration, create an `ERPConnection` record:
- `name`: "My ERP Production"
- `connector_type`: `ERPConnectorType.MY_ERP`
- `config_json`: `{"api_endpoint": "...", "api_key": "..."}` (credentials stored here)
- `is_default`: True (if this should be the primary connector)
- `status`: `ERPConnectionStatus.ACTIVE`

### 5. Add Langfuse Tracing

Use the ERP-specific helpers for fail-silent tracing:

```python
from apps.erp_integration.services.langfuse_helpers import (
    sanitize_erp_metadata, start_erp_span, end_erp_span,
)
```

All metadata passed to spans should go through `sanitize_erp_metadata()` to redact sensitive fields.

### 6. Write Tests

- Connection test: `test_connection()` returns True for valid config
- Each capability: lookup returns `ERPResolutionResult` with expected fields
- Missing data: lookup returns `found=False` gracefully
- Auth failure: connector handles 401/403 without crashing
- Factory registration: `ConnectorFactory.get_connector_by_name()` returns the right class
- Metadata sanitization: verify API keys are redacted in Langfuse span output

---

## Constraints

- ERP connector enums live in `apps/erp_integration/enums.py`, NOT `apps/core/enums.py`
- Never hardcode credentials -- always read from `ERPConnection.config_json`
- Never log raw API keys/tokens -- use `sanitize_erp_metadata()` before logging
- All connector methods must be fail-safe -- catch exceptions and return `ERPResolutionResult(found=False, ...)`
- ASCII only in all connector output strings and log messages
