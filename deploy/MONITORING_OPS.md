# =============================================================================
# Finance Agents Platform — Monitoring & Observability Operations Guide
# =============================================================================

## Table of Contents

1. [Service Status Quick Reference](#1-service-status-quick-reference)
2. [Health Check Endpoints](#2-health-check-endpoints)
3. [Flower — Celery Monitoring Dashboard](#3-flower--celery-monitoring-dashboard)
4. [Gunicorn Observability](#4-gunicorn-observability)
5. [Celery Worker Observability](#5-celery-worker-observability)
6. [Celery Queue Monitoring](#6-celery-queue-monitoring)
7. [Redis Monitoring](#7-redis-monitoring)
8. [Nginx Monitoring](#8-nginx-monitoring)
9. [Application Logging Strategy](#9-application-logging-strategy)
10. [Agent Platform Observability](#10-agent-platform-observability)
11. [System Resource Monitoring](#11-system-resource-monitoring)
12. [Alerting Watchlist](#12-alerting-watchlist)
13. [Troubleshooting Playbooks](#13-troubleshooting-playbooks)
14. [Azure Monitor Next Steps](#14-azure-monitor-next-steps)
15. [Operations Cheat Sheet](#15-operations-cheat-sheet)

---

## 1. Service Status Quick Reference

```bash
ssh finance-agents

# All services at a glance
for svc in finance-agents-gunicorn finance-agents-celery finance-agents-celerybeat finance-agents-flower nginx redis-server; do
  printf "%-38s %s\n" "$svc" "$(systemctl is-active $svc)"
done
```

Expected output — all should show `active`:
```
finance-agents-gunicorn                active
finance-agents-celery                  active
finance-agents-celerybeat              active
finance-agents-flower                  active
nginx                                  active
redis-server                           active
```

---

## 2. Health Check Endpoints

| Endpoint | Purpose | Expected Response |
|---|---|---|
| `/health/` | Liveness — Django process running | `{"status": "ok", "env": "production"}` |
| `/health/live/` | K8s-style liveness probe | `{"status": "ok"}` |
| `/health/ready/` | Readiness — DB + Redis + queue depths | Full JSON with checks + timings |

### Usage

```bash
# Quick liveness
curl -s http://localhost/health/

# Full readiness with timings and queue depths
curl -s http://localhost/health/ready/ | python3 -m json.tool

# External (from your machine)
curl -s http://20.244.26.58/health/ready/ | python3 -m json.tool
```

### Readiness response example
```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "celery_queues": {
      "default": 0,
      "extraction": 0,
      "reconciliation": 0,
      "agents": 0,
      "scheduled": 0
    }
  },
  "timings": {
    "database_ms": 12.3,
    "redis_ms": 1.1,
    "celery_queues_ms": 0.8
  },
  "env": "production",
  "debug": false
}
```

**Watch for:** `status: "degraded"` → returns HTTP 503.

---

## 3. Flower — Celery Monitoring Dashboard

**URL:** `http://20.244.26.58/flower/`
**Auth:** `admin` / `FinanceAgents2026!` (change in service file)

### What Flower shows

| Tab | What to watch |
|---|---|
| **Dashboard** | Active workers, task success/failure rates |
| **Workers** | Worker status, concurrency, processed count |
| **Tasks** | Task history, runtime, state (SUCCESS/FAILURE/RETRY) |
| **Broker** | Queue lengths for all 5 queues |
| **Monitor** | Real-time task execution graphs |

### Key things to check in Flower

1. **Workers tab**: All workers should show `Online` status
2. **Broker tab**: Queue lengths should be < 50 (agents/extraction may spike during batch)
3. **Tasks tab**: Filter by `state=FAILURE` to see recurring failures
4. **Tasks tab**: Sort by runtime to find slow tasks (agent LLM calls can be 30-120s)

### Service management

```bash
sudo systemctl status finance-agents-flower
sudo systemctl restart finance-agents-flower
sudo journalctl -u finance-agents-flower -n 50 --no-pager
```

---

## 4. Gunicorn Observability

### Service status
```bash
sudo systemctl status finance-agents-gunicorn
```

### Verify workers are alive
```bash
# Check worker processes (should see 4 workers + 1 arbiter)
ps aux | grep gunicorn | grep -v grep
```

### Socket exists and is usable
```bash
ls -la /opt/finance-agents/run/gunicorn.sock
# Should show srwxrwxrwx

# Test socket directly
curl --unix-socket /opt/finance-agents/run/gunicorn.sock http://localhost/health/
```

### Logs
```bash
# Access log (request-level)
tail -f /opt/finance-agents/logs/gunicorn-access.log

# Error log (startup failures, worker crashes)
tail -f /opt/finance-agents/logs/gunicorn-error.log

# Systemd journal
sudo journalctl -u finance-agents-gunicorn -f --no-pager
```

### Key metrics to watch
- Worker crash/restart count in journal
- Response times in access log (4th column)
- 5xx errors (grep for ` 500 ` or ` 502 `)

```bash
# Count 5xx responses in last 1000 lines
tail -1000 /opt/finance-agents/logs/gunicorn-access.log | grep -cE '" [5][0-9]{2} '
```

---

## 5. Celery Worker Observability

### Service status
```bash
sudo systemctl status finance-agents-celery
```

### Inspect active workers
```bash
cd /opt/finance-agents && source venv/bin/activate

# List registered workers
celery -A config inspect active

# Worker statistics (processed count, uptime, prefetch)
celery -A config inspect stats

# Which task types are registered
celery -A config inspect registered

# Currently active tasks
celery -A config inspect active

# Tasks waiting to execute (prefetched)
celery -A config inspect reserved

# Scheduled (ETA/countdown) tasks
celery -A config inspect scheduled

# Revoked tasks
celery -A config inspect revoked
```

### Worker logs
```bash
tail -f /opt/finance-agents/logs/celery-worker.log
sudo journalctl -u finance-agents-celery -f --no-pager
```

### Watch for
- `[ERROR]` entries in worker log
- Tasks stuck in `STARTED` state for > 10 minutes
- `WorkerLostError` — worker process died during task execution
- `SoftTimeLimitExceeded` — task exceeded 9-minute soft limit
- `TimeLimitExceeded` — task hard-killed at 10 minutes

---

## 6. Celery Queue Monitoring

### Queue architecture

| Queue | Purpose | Typical tasks |
|---|---|---|
| `default` | General work, review assignments | Misc tasks |
| `extraction` | Invoice OCR + LLM extraction | `process_document_task` |
| `reconciliation` | PO/GRN matching | `run_reconciliation_task` |
| `agents` | AI agent orchestration | `run_agent_pipeline_task` |
| `scheduled` | Periodic/beat tasks | Future cron jobs |

### Why separate queues matter

- **Isolation**: A flood of extraction tasks doesn't block agent responses
- **Visibility**: You can see which workload is backed up
- **Scaling**: Add dedicated workers per queue if needed

### Check queue lengths (Redis)
```bash
redis-cli LLEN default
redis-cli LLEN extraction
redis-cli LLEN reconciliation
redis-cli LLEN agents
redis-cli LLEN scheduled
```

Or all at once:
```bash
for q in default extraction reconciliation agents scheduled; do
  printf "%-20s %s\n" "$q" "$(redis-cli LLEN $q)"
done
```

**The `/health/ready/` endpoint also returns queue lengths.**

### Detecting a stuck queue

A queue is stuck if:
1. Length keeps growing (> 100 pending tasks)
2. Workers show `active` but no tasks are being consumed
3. Tasks appear in `reserved` but never move to `active`

```bash
# Watch queue lengths over time
watch -n 5 'for q in default extraction reconciliation agents scheduled; do printf "%-20s %s\n" "$q" "$(redis-cli LLEN $q)"; done'
```

### Detecting workers not consuming a queue

```bash
# Check which queues each worker is consuming
celery -A config inspect active_queues
```

If the `agents` queue has tasks but no worker lists `agents`, the queue is orphaned.

---

## 7. Redis Monitoring

### Basic health
```bash
redis-cli ping
# Expected: PONG

redis-cli info server | head -5
```

### Memory usage
```bash
redis-cli info memory | grep -E 'used_memory_human|maxmemory_human|mem_fragmentation_ratio'
```

**Watch for:** Memory over 80% of available, fragmentation ratio > 1.5.

### Connected clients
```bash
redis-cli info clients | grep connected_clients
```

### Keyspace / queue visibility
```bash
redis-cli info keyspace
redis-cli DBSIZE

# List all keys matching queue pattern
redis-cli KEYS '*'
```

### Full diagnostics
```bash
redis-cli info | grep -E 'redis_version|connected_clients|used_memory_human|total_commands_processed|keyspace'
```

---

## 8. Nginx Monitoring

### Config validation
```bash
sudo nginx -t
```

### Status
```bash
sudo systemctl status nginx
```

### Logs
```bash
# Access log
tail -f /opt/finance-agents/logs/nginx-access.log

# Error log (upstream failures, 502/504 errors)
tail -f /opt/finance-agents/logs/nginx-error.log
```

### Detect upstream failures
```bash
# 502 Bad Gateway = Gunicorn down or socket missing
grep ' 502 ' /opt/finance-agents/logs/nginx-access.log | tail -20

# 504 Gateway Timeout = Gunicorn too slow (agent LLM calls)
grep ' 504 ' /opt/finance-agents/logs/nginx-access.log | tail -20

# Upstream connection refused
grep 'connect() failed' /opt/finance-agents/logs/nginx-error.log | tail -20
```

### Request rate
```bash
# Requests in last 5 minutes
awk -v d="$(date -d '5 minutes ago' '+%d/%b/%Y:%H:%M')" '$0 ~ d' /opt/finance-agents/logs/nginx-access.log | wc -l
```

### Listening sockets
```bash
sudo ss -tlnp | grep nginx
# Should show:
# LISTEN  0  511  0.0.0.0:80  *:*  users:(("nginx",...))
```

---

## 9. Application Logging Strategy

### Log directory layout

```
/opt/finance-agents/logs/
├── gunicorn-access.log     # HTTP request log
├── gunicorn-error.log      # Gunicorn startup/worker errors
├── celery-worker.log       # Celery task execution log
├── celerybeat.log          # Celery Beat scheduler log
├── flower.log              # Flower monitoring tool (via journal)
├── nginx-access.log        # Nginx HTTP access log
├── nginx-error.log         # Nginx errors, upstream failures
└── po_recon.log            # Django application log (structured JSON)
```

### Structured JSON logging

The app already uses `JSONLogFormatter` in production. The `po_recon.log` file
contains structured JSON entries with trace IDs, RBAC context, and PII redaction:

```json
{"timestamp": "2026-03-16T03:28:50.227", "level": "INFO", "logger": "apps.reconciliation", "message": "Reconciliation complete", "trace_id": "abc123", "span_id": "def456"}
```

### Logrotate

Already configured at `/etc/logrotate.d/finance-agents`:
- Daily rotation, 14 days retention
- Compressed after 1 day
- Gunicorn reloaded after rotation
- Celery sent HUP signal

### Quick log inspection

```bash
# Last 100 Django app log entries
tail -100 /opt/finance-agents/logs/po_recon.log

# Filter errors only
grep '"level": "ERROR"' /opt/finance-agents/logs/po_recon.log | tail -20

# Filter by trace ID
grep 'abc123' /opt/finance-agents/logs/po_recon.log

# Last 50 gunicorn errors
tail -50 /opt/finance-agents/logs/gunicorn-error.log

# Celery failures
grep -i 'error\|traceback\|exception' /opt/finance-agents/logs/celery-worker.log | tail -30
```

### Audit/security log stream

The `AuditEvent` model records all sensitive operations with RBAC context.
Query the governance API for audit history:

```bash
# Via API (requires auth cookie)
curl -s http://localhost/api/v1/governance/audit-history/ | python3 -m json.tool

# Or Django shell
cd /opt/finance-agents && source venv/bin/activate
python manage.py shell -c "from apps.auditlog.models import AuditEvent; print(AuditEvent.objects.filter(event_type__startswith='GUARDRAIL_DENIED').count())"
```

---

## 10. Agent Platform Observability

This is an AI agent platform — standard web monitoring is necessary but not sufficient.

### Agent-specific checks

#### Agent run counts and status
```bash
cd /opt/finance-agents && source venv/bin/activate
python manage.py shell -c "
from apps.agents.models import AgentRun
from django.db.models import Count
runs = AgentRun.objects.values('status').annotate(c=Count('id'))
for r in runs: print(f\"  {r['status']}: {r['c']}\")
"
```

#### Failed agent runs (last 24h)
```bash
python manage.py shell -c "
from apps.agents.models import AgentRun
from django.utils import timezone
from datetime import timedelta
cutoff = timezone.now() - timedelta(hours=24)
failed = AgentRun.objects.filter(status='FAILED', created_at__gte=cutoff)
print(f'Failed agent runs (24h): {failed.count()}')
for r in failed[:10]:
    print(f'  [{r.agent_type}] {r.error_message[:80] if r.error_message else \"no error msg\"}')
"
```

#### Long-running agent tasks
```bash
python manage.py shell -c "
from apps.agents.models import AgentRun
from django.utils import timezone
from datetime import timedelta
cutoff = timezone.now() - timedelta(minutes=15)
stuck = AgentRun.objects.filter(status='RUNNING', started_at__lt=cutoff)
print(f'Stuck agent runs (>15 min): {stuck.count()}')
for r in stuck: print(f'  [{r.agent_type}] started={r.started_at}')
"
```

#### Guardrail denials (security events)
```bash
python manage.py shell -c "
from apps.auditlog.models import AuditEvent
denied = AuditEvent.objects.filter(event_type__in=['GUARDRAIL_DENIED','TOOL_CALL_DENIED','RECOMMENDATION_DENIED'])
print(f'Total guardrail denials: {denied.count()}')
for e in denied.order_by('-created_at')[:5]:
    print(f'  [{e.event_type}] {e.event_description[:80]}')
"
```

### Agent operations health checklist

Run periodically (daily or after batch processing):

```bash
#!/usr/bin/env bash
# agent_health_check.sh
cd /opt/finance-agents && source venv/bin/activate

echo "=== Agent Platform Health ==="

echo ""
echo "1. Queue depths:"
for q in default extraction reconciliation agents scheduled; do
  printf "   %-20s %s\n" "$q" "$(redis-cli LLEN $q)"
done

echo ""
echo "2. Celery workers:"
celery -A config inspect ping --timeout 5 2>/dev/null | grep -c 'pong' | xargs -I{} echo "   {} worker(s) responding"

echo ""
echo "3. Agent runs (last 24h):"
python manage.py shell -c "
from apps.agents.models import AgentRun
from django.utils import timezone
from datetime import timedelta
cutoff = timezone.now() - timedelta(hours=24)
runs = AgentRun.objects.filter(created_at__gte=cutoff)
print(f'   Total: {runs.count()}')
print(f'   Completed: {runs.filter(status=\"COMPLETED\").count()}')
print(f'   Failed: {runs.filter(status=\"FAILED\").count()}')
print(f'   Running: {runs.filter(status=\"RUNNING\").count()}')
"

echo ""
echo "4. Extraction tasks (last 24h):"
python manage.py shell -c "
from apps.extraction.models import ExtractionResult
from django.utils import timezone
from datetime import timedelta
cutoff = timezone.now() - timedelta(hours=24)
results = ExtractionResult.objects.filter(created_at__gte=cutoff)
print(f'   Total: {results.count()}')
"

echo ""
echo "5. Health endpoint:"
curl -s http://localhost/health/ready/ | python3 -m json.tool

echo ""
echo "6. Stuck tasks (>15 min):"
python manage.py shell -c "
from apps.agents.models import AgentRun
from django.utils import timezone
from datetime import timedelta
cutoff = timezone.now() - timedelta(minutes=15)
stuck = AgentRun.objects.filter(status='RUNNING', started_at__lt=cutoff)
if stuck.exists():
    print(f'   WARNING: {stuck.count()} stuck tasks!')
    for r in stuck: print(f'     [{r.agent_type}] since {r.started_at}')
else:
    print('   None (OK)')
"
```

### Existing observability features in the app

The codebase already includes:

| Feature | Location | How to access |
|---|---|---|
| MetricsService | `apps/core/metrics.py` | In-process counters (RBAC, extraction, recon, agent) |
| TraceContext | `apps/core/trace.py` | Distributed tracing with trace_id propagation |
| AuditEvent | `apps/auditlog/models.py` | 38+ event types with RBAC fields |
| CaseTimelineService | `apps/auditlog/timeline_service.py` | Unified chronological timeline per invoice |
| Governance API | `/api/v1/governance/` | 9 endpoints (audit, trace, timeline, performance) |
| Agent Governance Dashboard | `/governance/` | UI for audit events, invoice governance |
| Dashboard Analytics | `/api/v1/dashboard/` | 7 API endpoints including mode-breakdown |
| Session status page | `/api/v1/dashboard/agents/performance/` | Agent performance metrics |

---

## 11. System Resource Monitoring

### Quick system snapshot
```bash
echo "=== CPU ===" && uptime
echo "=== Memory ===" && free -h
echo "=== Disk ===" && df -h /
echo "=== Inodes ===" && df -i /
echo "=== Load ===" && cat /proc/loadavg
echo "=== Open files ===" && cat /proc/sys/fs/file-nr
echo "=== Sockets ===" && ss -s
```

### Real-time monitoring
```bash
# Process-level view
htop

# Memory over time
vmstat 5

# Disk I/O
iostat -x 5       # requires sysstat

# Historical CPU/memory (if sysstat enabled)
sar -u 1 10       # CPU utilization, 10 samples
sar -r 1 10       # Memory utilization
```

### Per-service resource usage
```bash
# Memory per service
systemctl status finance-agents-gunicorn | grep Memory
systemctl status finance-agents-celery | grep Memory
systemctl status finance-agents-flower | grep Memory
systemctl status redis-server | grep Memory
```

### Disk usage by directory
```bash
du -sh /opt/finance-agents/{logs,media,static,venv,run}
```

### Optional: Netdata (lightweight, real-time dashboard)

For a comprehensive real-time dashboard without Prometheus/Grafana overhead:

```bash
# Install Netdata (optional — adds ~100MB RAM)
wget -O /tmp/netdata-kickstart.sh https://get.netdata.cloud/kickstart.sh
bash /tmp/netdata-kickstart.sh --dont-wait

# Access at http://20.244.26.58:19999
# Add UFW rule: sudo ufw allow 19999/tcp
```

This is optional — skip if memory is tight on the VM.

---

## 12. Alerting Watchlist

### Phase 1 — Manual checks (today)

Run these checks daily or after batch processing:

| Check | Command | Alert if |
|---|---|---|
| Gunicorn alive | `systemctl is-active finance-agents-gunicorn` | Not `active` |
| Celery alive | `systemctl is-active finance-agents-celery` | Not `active` |
| Beat alive | `systemctl is-active finance-agents-celerybeat` | Not `active` |
| Flower alive | `systemctl is-active finance-agents-flower` | Not `active` |
| Nginx alive | `systemctl is-active nginx` | Not `active` |
| Redis alive | `redis-cli ping` | Not `PONG` |
| DB reachable | `curl -s localhost/health/ready/ \| jq .checks.database` | Not `ok` |
| Queue backlog | `redis-cli LLEN agents` | > 100 |
| Failed tasks (24h) | Check agent runs with status=FAILED | > 5 |
| Disk usage | `df -h / \| awk '{print $5}'` | > 85% |
| Memory | `free \| awk '/Mem/{printf "%d", $3/$2*100}'` | > 90% |
| 5xx errors | `grep -c ' 5[0-9][0-9] ' nginx-access.log` | > 10/hour |
| Stuck tasks | AgentRun RUNNING > 15 min | Any |

### Phase 2 — Automated (future)

Integrate with Azure Monitor or a simple cron-based watchdog:

```bash
# Example: /etc/cron.d/finance-agents-watchdog
*/5 * * * * root /opt/finance-agents/deploy/health_watchdog.sh >> /opt/finance-agents/logs/watchdog.log 2>&1
```

---

## 13. Troubleshooting Playbooks

### App returns 502 Bad Gateway

```bash
# 1. Is Gunicorn running?
sudo systemctl status finance-agents-gunicorn

# 2. Does the socket exist?
ls -la /opt/finance-agents/run/gunicorn.sock

# 3. Check Gunicorn errors
sudo journalctl -u finance-agents-gunicorn -n 50 --no-pager

# 4. Restart Gunicorn
sudo systemctl restart finance-agents-gunicorn
```

### Tasks not being processed

```bash
# 1. Is Celery running?
sudo systemctl status finance-agents-celery

# 2. Is Redis running?
redis-cli ping

# 3. Are queues growing?
for q in default extraction reconciliation agents scheduled; do
  echo "$q: $(redis-cli LLEN $q)"
done

# 4. What's the worker doing?
celery -A config inspect active

# 5. Check for errors
tail -100 /opt/finance-agents/logs/celery-worker.log | grep -i error
```

### Agent tasks timing out

```bash
# 1. Check soft time limit hits
grep 'SoftTimeLimitExceeded' /opt/finance-agents/logs/celery-worker.log

# 2. Check active agent tasks
celery -A config inspect active | grep -A5 agent

# 3. Check agent run table
python manage.py shell -c "
from apps.agents.models import AgentRun
for r in AgentRun.objects.filter(status='RUNNING').order_by('started_at')[:5]:
    print(f'{r.agent_type}: started {r.started_at}')
"
```

### Redis memory pressure

```bash
redis-cli info memory | grep used_memory_human
redis-cli info memory | grep maxmemory

# If needed, flush task results (not queues!)
redis-cli SELECT 0
redis-cli KEYS 'celery-task-meta-*' | head -20
```

### Disk space issues

```bash
df -h /
du -sh /opt/finance-agents/logs/*
du -sh /opt/finance-agents/media/*

# Force logrotate
sudo logrotate -f /etc/logrotate.d/finance-agents

# Clean old celery results from DB
python manage.py shell -c "
from django_celery_results.models import TaskResult
from django.utils import timezone
from datetime import timedelta
old = TaskResult.objects.filter(date_done__lt=timezone.now()-timedelta(days=30))
print(f'Deleting {old.count()} old task results')
old.delete()
"
```

---

## 14. Azure Monitor Next Steps

### Recommended Azure-native integrations (Phase 2)

#### 1. Azure Monitor Agent

Install the Azure Monitor Agent to collect:
- VM performance metrics (CPU, memory, disk, network)
- Syslog and systemd journal entries
- Custom log files (nginx, gunicorn, celery)

```bash
# Install via Azure CLI (from local machine)
az vm extension set \
  --resource-group <RESOURCE_GROUP> \
  --vm-name <VM_NAME> \
  --name AzureMonitorLinuxAgent \
  --publisher Microsoft.Azure.Monitor
```

#### 2. Log Analytics Workspace

Create a Log Analytics workspace to aggregate:
- Application logs (`po_recon.log` — JSON structured)
- Nginx access/error logs
- Celery worker logs
- System metrics

#### 3. VM Insights

Enable VM insights for:
- Real-time CPU, memory, disk, network graphs
- Process dependency mapping
- Performance trending

```bash
az monitor vm-insights enable \
  --resource-group <RESOURCE_GROUP> \
  --name <VM_NAME> \
  --workspace <WORKSPACE_ID>
```

#### 4. Alert Rules

Create alerts for:
- **VM CPU > 85% for 5 minutes**
- **VM available memory < 500 MB**
- **Disk used > 85%**
- **HTTP 5xx rate > 10/minute** (via nginx log)
- **Heartbeat missing** (VM down)

#### 5. Dashboard Ideas

Create an Azure Dashboard with:
- VM performance tiles
- Service health status (via health endpoint polling)
- Agent run success/failure chart (via governance API)
- Queue depth chart (via health/ready endpoint)

---

## 15. Operations Cheat Sheet

### SSH Access
```bash
ssh finance-agents
```

### Service Management
```bash
# Status of all services
sudo systemctl status finance-agents-gunicorn finance-agents-celery finance-agents-celerybeat finance-agents-flower

# Restart all
sudo bash /opt/finance-agents/deploy/restart_services.sh

# Restart individual
sudo systemctl restart finance-agents-gunicorn
sudo systemctl restart finance-agents-celery
sudo systemctl restart finance-agents-celerybeat
sudo systemctl restart finance-agents-flower

# Stop all (maintenance)
sudo systemctl stop finance-agents-gunicorn finance-agents-celery finance-agents-celerybeat finance-agents-flower
```

### Log Viewing
```bash
# Journal logs (real-time)
sudo journalctl -u finance-agents-gunicorn -f --no-pager
sudo journalctl -u finance-agents-celery -f --no-pager
sudo journalctl -u finance-agents-celerybeat -f --no-pager
sudo journalctl -u finance-agents-flower -f --no-pager

# File logs
tail -f /opt/finance-agents/logs/po_recon.log
tail -f /opt/finance-agents/logs/gunicorn-error.log
tail -f /opt/finance-agents/logs/celery-worker.log
tail -f /opt/finance-agents/logs/nginx-access.log
tail -f /opt/finance-agents/logs/nginx-error.log
```

### Health Checks
```bash
curl -s http://localhost/health/
curl -s http://localhost/health/live/
curl -s http://localhost/health/ready/ | python3 -m json.tool
```

### Redis
```bash
redis-cli ping
redis-cli info memory | grep used_memory_human
redis-cli info clients | grep connected_clients
for q in default extraction reconciliation agents scheduled; do
  echo "$q: $(redis-cli LLEN $q)"
done
```

### Celery Inspection
```bash
cd /opt/finance-agents && source venv/bin/activate
celery -A config inspect ping
celery -A config inspect active
celery -A config inspect stats
celery -A config inspect registered
celery -A config inspect reserved
celery -A config inspect scheduled
celery -A config inspect active_queues
```

### Nginx
```bash
sudo nginx -t
sudo systemctl reload nginx
grep ' 502 \| 504 ' /opt/finance-agents/logs/nginx-access.log | tail -20
```

### Gunicorn Socket
```bash
ls -la /opt/finance-agents/run/gunicorn.sock
curl --unix-socket /opt/finance-agents/run/gunicorn.sock http://localhost/health/
```

### Django Management
```bash
cd /opt/finance-agents
source venv/bin/activate
python manage.py check --deploy
python manage.py showmigrations
python manage.py shell
```

### Deploy Update
```bash
sudo bash /opt/finance-agents/deploy/update_app.sh
```

### Flower
```bash
# Browser: http://20.244.26.58/flower/
# Credentials: admin / FinanceAgents2026!
curl -s http://localhost/flower/api/workers | python3 -m json.tool
curl -s http://localhost/flower/api/queues/length | python3 -m json.tool
```

---

## Files Reference

| File | Location | Purpose |
|---|---|---|
| `setup_monitoring.sh` | `deploy/` | One-time monitoring setup |
| `finance-agents-flower.service` | `deploy/` → `/etc/systemd/system/` | Flower systemd unit |
| `nginx.conf` | `deploy/` → `/etc/nginx/sites-available/finance-agents` | Includes /flower/ proxy |
| `logrotate-finance-agents` | `deploy/` → `/etc/logrotate.d/finance-agents` | Log rotation |
| `health.py` | `apps/core/` | /health/, /health/live/, /health/ready/ |
| `MONITORING_OPS.md` | `deploy/` | This guide |

---

## Placeholders to Replace

| Placeholder | Current Value | Where |
|---|---|---|
| Flower password | `FinanceAgents2026!` | `finance-agents-flower.service` (--basic_auth) |
| Server IP | `20.244.26.58` | URLs in this guide |
| Azure Resource Group | `<RESOURCE_GROUP>` | Azure CLI commands |
| VM Name | `<VM_NAME>` | Azure CLI commands |
| Log Analytics Workspace | `<WORKSPACE_ID>` | Azure Monitor setup |
