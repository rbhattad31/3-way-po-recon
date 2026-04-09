# 16 — Operational Runbook Summary

**Generated**: 2026-04-09 | **Method**: Inferred from settings, tasks, celery config, deploy docs, and code  
**Confidence**: Medium — derived from code; verify against `deploy/DEPLOYMENT.md` and `deploy/MONITORING_OPS.md`

---

## 1. Process Requirements

To run the platform in full async mode, the following processes must be running:

```bash
# Django app server
gunicorn config.wsgi:application --workers 4 --bind 0.0.0.0:8000

# Celery worker (processes extraction, reconciliation, agent, case tasks)
celery -A config worker -l info -Q default --concurrency 4

# Celery Beat (processes learning actions every 30 minutes)
celery -A config beat -l info

# Redis (message broker)
redis-server --requirepass <password>

# MySQL (primary database)
# External — managed separately
```

**Dev mode** (no Redis needed):
```bash
# Set in .env:
CELERY_TASK_ALWAYS_EAGER=True
python manage.py runserver
```

---

## 2. Required Environment Variables

### Must Set (platform will error/fail without these)
```
DJANGO_SECRET_KEY
DB_PASSWORD
AZURE_OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_DEPLOYMENT
AZURE_DI_ENDPOINT
AZURE_DI_KEY
AZURE_BLOB_CONNECTION_STRING
CELERY_BROKER_URL=rediss://:password@host:6380/0  (production TLS)
```

### Strongly Recommended
```
LANGFUSE_PUBLIC_KEY          # LLM tracing (invisible without this)
LANGFUSE_SECRET_KEY
LOKI_ENABLED=true            # Centralized logging
LOKI_URL                     # Loki endpoint
DJANGO_DEBUG=False           # Production mode
DJANGO_ALLOWED_HOSTS=your.domain.com
```

---

## 3. Database Setup

```bash
# Run all migrations
python manage.py migrate

# Seed initial data (recommended order)
python manage.py seed_config --flush          # ReconciliationConfig defaults
python manage.py seed_rbac --sync-users       # Roles, permissions, system-agent user
python manage.py seed_prompts --force         # PromptTemplate records
python manage.py push_prompts_to_langfuse     # Sync 18 prompts to Langfuse

# Demo data (optional)
python manage.py seed_ap_data --reset --summary
```

---

## 4. Health Checks

| Endpoint | Purpose | Expected Response |
|----------|---------|-----------------|
| `/health/` | Basic health | 200 OK |
| `/health/live/` | Liveness (process alive) | 200 OK |
| `/health/ready/` | Readiness (DB + deps ready) | 200 OK |

All three are login-exempt — safe for load balancer health checks.

---

## 5. Common Operational Tasks

### Trigger Manual Reconciliation
```
POST /api/v1/reconciliation/run/
Body: {"invoice_ids": [123, 456], "config_id": 1}
```
Or via UI: `/reconciliation/` → select invoices → Run Reconciliation

### Reprocess a Failed Case from a Stage
```
Via UI: Case Console → Actions → Reprocess from Stage
OR
Python: reprocess_case_from_stage_task.delay(tenant_id, case_id, "RECONCILIATION")
```

### Check Agent Pipeline Status
```
Admin: /admin/agents/agentorchestrationrun/
API: /api/v1/cases/<case_id>/agent-runs/
```

### View Audit Trail for an Invoice
```
Admin: /admin/auditlog/auditevent/?invoice_id=<id>
UI: /governance/ → search by invoice
```

### Reset ERP Cache for a Connector
```
Via admin or API: /erp/ → connector → Cache → Flush
```

---

## 6. Monitoring Indicators

| Metric | Where to check | Alert threshold |
|--------|---------------|----------------|
| Celery task failure rate | django_celery_results table or Flower | >5% failure rate |
| Extraction pipeline duration | Langfuse trace durations | >3 min per extraction |
| Agent pipeline duration | Langfuse agent spans | >10 min per pipeline |
| LLM cost per day | `AgentRun.actual_cost_usd` aggregate | Business-defined budget |
| Unresolved review queue depth | `ReviewAssignment.status=PENDING` count | SLA-defined threshold |
| `AuditEvent.access_granted=False` rate | auditlog_audit_event table | Any unexpected spikes |

---

## 7. Known Operational Gaps

| Gap | Impact | Workaround |
|-----|--------|-----------|
| No email notifications | Reviewers not notified of new cases | Manual polling of review queue or future email integration |
| No scheduled reconciliation | Invoices only reconciled on manual trigger | AP Processor must trigger daily runs |
| No Celery worker autoscaling | Fixed worker count | Manual scaling; monitor queue depth |
| No scheduled ERP data sync | Mirror tables only refreshed on-demand | Enable `ERP_ENABLE_LIVE_REFRESH_ON_MISS=true` or implement external sync job |
| Redis unauthenticated in dev | Security risk if mistakenly deployed to prod | Production CELERY_BROKER_URL must use rediss:// with auth |
| No Docker container | Complex deployment setup | Follow `deploy/DEPLOYMENT.md` (Nginx + Gunicorn + Systemd) |

---

## 8. Seed Management Commands (Reference)

```bash
python manage.py seed_config            # ReconciliationConfig, policies
python manage.py seed_rbac              # Roles, permissions, user assignments
python manage.py seed_prompts           # PromptTemplate records
python manage.py push_prompts_to_langfuse  # Sync prompts to Langfuse
python manage.py seed_ap_data           # Demo AP data (POs, GRNs, invoices)
python manage.py flush_invoices         # Flush invoice data (dev/test)
python manage.py flush_test_data        # Flush test data (dev/test)
```

---

## 9. Log Access

| Channel | Location | Notes |
|---------|----------|-------|
| File | `logs/po_recon.log` | JSON format; rotates at 10MB; 5 backups |
| Console | stdout | `dev_traced` format in DEBUG; JSON in prod |
| Loki | Grafana (via `docker-compose.loki.yml`) | Only when `LOKI_ENABLED=true` |
| Django admin | ProcessingLog table | `auditlog_processing_log` via `/admin/auditlog/processinglog/` |
