---
description: "Debug a failing extraction, reconciliation, agent pipeline, ERP resolution, or posting pipeline. Provides a structured diagnostic checklist including Langfuse trace lookup, Celery task state, DB record inspection, and common failure patterns."
agent: agent
argument-hint: "What is failing and any known context (e.g. 'Invoice #1234 stuck in EXTRACTION_IN_PROGRESS for 30 minutes')"
tools: [read, search]
---

Diagnose the reported pipeline failure in the 3-Way PO Reconciliation Platform.

**Step 1 — Identify the Stuck Entity**
- Determine which pipeline is affected: extraction, reconciliation, agent, posting, or case
- Read the relevant model to understand status values and state machine transitions

**Step 2 — Check Common Failure Points**

For **Extraction** failures:
- Check `ExtractionResult.status` and `error_message` for the DocumentUpload
- Check `ProcessingLog` records for the upload ID
- Verify `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`, `AZURE_OPENAI_*` env vars are set
- Check if Celery task `process_invoice_upload_task` completed (look for task result in DB)

For **Reconciliation** failures:
- Check `ReconciliationRun.status` and `error_log`
- Check `ReconciliationResult` records — look for `status=ERROR`
- Verify at least one active `ReconciliationConfig` exists for the tenant
- Check `POLookupService` — does a matching PO exist with the same `po_number`?

For **Agent Pipeline** failures:
- Check `AgentOrchestrationRun.status` — is a RUNNING record blocking re-entry?
- Check `AgentRun.status` for each agent in the orchestration
- Check `DecisionLog` records for the reconciliation result
- Verify `AgentDefinition` records are seeded (run `python manage.py seed_agent_contracts`)

For **Posting** failures:
- Check `PostingRun.status` and `error_code`
- Check `PostingIssue` records with `severity=ERROR`
- Verify ERP reference tables are populated (`/posting/imports/`)
- Check `ERPConnection` has `is_default=True`, `status=ACTIVE`, `is_active=True`

For **ERP Resolution** failures:
- Check `ERPResolutionLog` for the entity
- Check `ERPReferenceCacheRecord` — may be expired
- Verify `ConnectorFactory.get_default_connector()` returns non-None

**Step 3 — Provide Fix**
- Based on diagnosis, provide the exact fix or the management command to run
- Reference the debugging tips in copilot-instructions.md

**Problem**: $input
