---
mode: agent
description: "Diagnose and fix a bug with root cause analysis, safe fix, and regression test"
---

# Bug Fix

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/current_system_review/15_Test_Coverage_and_Behavioral_Confidence.md` -- existing test infrastructure, coverage gaps, test limitations
- `docs/current_system_review/03_Configuration_and_Environment.md` -- settings, feature flags, environment variables that might affect behavior
- `docs/current_system_review/14_Deployment_and_Operations.md` -- deployment context, logging configuration

### Source Files
- `conftest.py` -- shared pytest fixtures (tenant, user, role fixtures)
- `config/test_settings.py` -- test-specific settings (`CELERY_TASK_ALWAYS_EAGER=True`, test DB config)
- `apps/core/decorators.py` -- `@observed_service`, `@observed_action` (check if tracing is masking errors)
- `apps/core/logging_utils.py` -- structured logging format, PII redaction patterns

### Comprehension Check
1. Tests run with `CELERY_TASK_ALWAYS_EAGER=True` -- Celery tasks execute synchronously
2. Tenant isolation is pervasive -- a missing `tenant=` filter is a common bug source
3. Enums are in `apps/core/enums.py` (or `apps/erp_integration/enums.py` for ERP) -- check for mismatched values
4. Status transitions follow documented flows -- check the relevant status enum for valid transitions
5. `@observed_service` catches exceptions and logs them -- verify the error is not being silently swallowed

---

## Bug Fix Procedure

### 1. Reproduce the Bug

- Identify the minimal reproduction path
- Check logs for the error (look in `logs/` directory or Django console output)
- If the bug is in a pipeline, identify which stage/service failed
- If the bug involves LLM calls, check for `AZURE_OPENAI_*` env vars

### 2. Root Cause Analysis

- Read the failing code path end-to-end before making changes
- Check for these common causes:
  - **Missing tenant filter**: ORM query without `.filter(tenant=tenant)`
  - **Enum mismatch**: status value not in the enum's `TextChoices`
  - **Null FK**: model saved without required FK (e.g. `vendor=None` when vendor is expected)
  - **Race condition**: Celery task accessing a record not yet committed (use `transaction.on_commit()`)
  - **Unicode in LLM output**: agent-generated text containing non-ASCII characters (apply `_sanitise_text()`)
  - **Langfuse propagation**: Langfuse error not caught by `try/except` (check all Langfuse calls are guarded)
  - **Permission denial**: RBAC check failing due to missing permission in `seed_rbac.py`

### 3. Write a Failing Test First

Before fixing, write a test that reproduces the bug:

```python
class TestBugFix(TestCase):
    def test_description_of_the_bug(self):
        """Regression test for [bug description]."""
        # Setup: create the conditions that trigger the bug
        # Act: call the method/endpoint that fails
        # Assert: verify the expected behavior (this should FAIL before the fix)
```

### 4. Apply the Minimal Fix

- Change only the code necessary to fix the root cause
- Do not refactor surrounding code, add features, or "improve" unrelated logic
- Verify the fix handles edge cases (null values, empty strings, missing FKs)
- If the fix involves a status transition, verify it follows the documented flow

### 5. Verify the Fix

- Run the failing test -- it should now pass
- Run the full test suite for the affected app: `pytest apps/<app>/tests/ -v`
- If the bug was in a view/API, test manually via browser or API client
- Check that Langfuse tracing still works (if the affected code has Langfuse spans)

### 6. Check for Related Occurrences

- Search the codebase for the same pattern that caused the bug
- If the same mistake exists elsewhere, fix those too (but in separate, minimal changes)
- If the pattern is common, consider whether a utility function in `apps/core/utils.py` would prevent recurrence

---

## Constraints

- ASCII only in all fix code, test code, comments
- Never change test infrastructure (conftest, test_settings) unless the bug is in the test infrastructure
- Fix only the bug -- no opportunistic refactoring
- Always write a regression test before declaring the bug fixed
- If the fix changes a public API response, verify downstream consumers are not broken
