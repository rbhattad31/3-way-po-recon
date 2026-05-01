---
description: "Scaffold a new Django model with all required wiring: BaseModel inheritance, tenant FK, enums, admin registration, serializer, ViewSet, and API URL. Provide the app name and model name."
agent: agent
argument-hint: "App name and model description (e.g. 'reconciliation app, MatchOverrideRecord model for manual match overrides')"
tools: [read, edit, search]
---

Scaffold a complete new Django model for the 3-Way PO Reconciliation Platform.

Use the `new-model` agent to:

1. Read `apps/core/models.py` to confirm `BaseModel` inheritance chain
2. Read `apps/core/enums.py` to check existing enums relevant to this model
3. Read `apps/<app>/models.py` to understand existing model conventions in the target app
4. Create the model class with:
   - `BaseModel` inheritance
   - `tenant` FK to `CompanyProfile`
   - `is_active` field via `SoftDeleteMixin` (if it's a business entity)
   - Proper `__str__` and `Meta` class with `ordering`
5. Add any new enums to `apps/core/enums.py`
6. Register in `apps/<app>/admin.py`
7. Create serializer in `apps/<app>/serializers.py`
8. Create ViewSet in `apps/<app>/views.py` with `TenantQuerysetMixin` and `HasPermissionCode`
9. Register in `apps/<app>/api_urls.py`
10. Print the `makemigrations` command to run

**Target**: $input
