---
description: "Write tests for a service, view, agent, or model in the platform. Covers tenant isolation tests, RBAC denial tests, status transition tests, and follows the project's pytest + conftest fixture patterns."
agent: agent
argument-hint: "What to test (e.g. 'ReconciliationRunnerService.run() for a 3-way match with GRN quantity mismatch')"
tools: [read, edit, search]
---

Write tests for the specified component following the 3-Way PO Reconciliation Platform test conventions.

**Step 1 — Read Existing Tests**
- Find the relevant test file in the app's `tests/` directory or the root `All_Testing/` directory
- Read `conftest.py` for available fixtures (tenant, user, invoice, PO, GRN, etc.)
- Read an existing test module for the target app to understand pytest + Django patterns used

**Step 2 — Plan Test Cases**
For every new test, include:
- **Happy path** — expected successful outcome
- **Tenant isolation** — assert that a different tenant cannot see/modify the data
- **RBAC denial** — assert that a user without the required permission gets 403/PermissionDenied
- **Status transitions** — for state machines, test invalid transitions raise errors
- **Edge cases** — null values, empty querysets, tolerance boundary conditions

**Step 3 — Write Tests**
- Use `pytest` with `@pytest.mark.django_db`
- Use factories or fixtures from `conftest.py` — do NOT create raw DB records in test bodies
- Use `assert` with descriptive messages
- For API tests: use DRF's `APIClient` with `force_authenticate(user=user)`
- For service tests: call the service directly with fixture objects

**Step 4 — Verify Coverage**
- List which code paths are NOT covered by the new tests and note why

**Target**: $input
