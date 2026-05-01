---
description: "Add a new ERP connector to the platform. Creates the connector class, registers it in ConnectorFactory, adds the ERPConnectorType enum value, and provides the ERPConnection seed record."
agent: agent
argument-hint: "ERP system name and capabilities (e.g. 'SAP S/4HANA connector with vendor lookup, PO lookup, and invoice submission')"
tools: [read, edit, search]
---

Add a new ERP connector to the 3-Way PO Reconciliation Platform.

Use the `erp-integration` agent to:

**Step 1 — Enum**
- Read `apps/erp_integration/enums.py`
- Add the new `ERPConnectorType` value

**Step 2 — Connector Class**
- Read `apps/erp_integration/services/connectors/base.py` for `BaseERPConnector`, `ERPResolutionResult`, `ERPSubmissionResult`
- Read an existing connector (e.g., `dynamics_connector.py`) as a pattern reference
- Create `apps/erp_integration/services/connectors/<name>_connector.py`
- Override ALL `supports_*()` capability flags
- Implement lookup methods only for declared capabilities
- Use `sanitize_erp_metadata()` before logging any response metadata

**Step 3 — Factory Registration**
- Read `apps/erp_integration/services/connector_factory.py`
- Add to `_CONNECTOR_MAP` dict

**Step 4 — ERPConnection Seed**
- Provide the `ERPConnection` model field values needed to create a record for this connector

**Step 5 — Langfuse Tracing**
- Confirm the connector uses `start_erp_span()` / `end_erp_span()` from `apps.erp_integration.services.langfuse_helpers`
- Add ERP observation scores per `trace_erp_live_lookup` pattern

**Target ERP system**: $input
