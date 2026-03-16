# =============================================================================
# Finance Agents Platform — Production Deployment Guide
# =============================================================================

## Architecture Overview

```
┌──────────┐     ┌─────────┐     ┌──────────────┐     ┌───────┐
│  Client   │────▶│  Nginx  │────▶│   Gunicorn   │────▶│ Django│
│ (Browser) │     │  :80    │     │ (Unix Socket) │     │  App  │
└──────────┘     └─────────┘     └──────────────┘     └───┬───┘
                                                          │
                                    ┌─────────────────────┼────────────┐
                                    │                     │            │
                                    ▼                     ▼            ▼
                              ┌──────────┐         ┌──────────┐  ┌─────────┐
                              │  Redis   │         │  MySQL   │  │ Azure   │
                              │  :6379   │         │  :3306   │  │ OpenAI  │
                              └────┬─────┘         └──────────┘  └─────────┘
                                   │
                              ┌────┴─────┐
                              │  Celery  │
                              │  Worker  │
                              │ + Beat   │
                              └──────────┘
```

---

## Prerequisites

- Azure Ubuntu 22.04+ VM (recommended: Standard_B2ms — 2 vCPU, 8 GB RAM)
- SSH access configured: `ssh finance-agents`
- Code already cloned at `/opt/finance-agents`
- Azure MySQL Flexible Server (or local MySQL 8.0+)
- Azure OpenAI resource provisioned

---

## Step 1: Bootstrap the Server

SSH into the server and run the bootstrap script:

```bash
ssh finance-agents
sudo bash /opt/finance-agents/deploy/bootstrap_server.sh
```

This installs all system packages, creates the `financeagents` service user,
sets up directories, enables Nginx and Redis, and configures the firewall.

---

## Step 2: Configure the Database

### Create a separate production database

Using the **same MySQL server** but a **dedicated schema** protects production data:

- Development/staging writes cannot corrupt production tables
- Separate credentials limit blast radius if dev credentials leak
- Enables independent backup and restore per environment

Connect to MySQL and run:

```sql
CREATE DATABASE finance_agents_prod
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'finance_agents_prod_user'@'%'
  IDENTIFIED BY 'CHANGE_ME_strong_password_here';

GRANT ALL PRIVILEGES
  ON finance_agents_prod.*
  TO 'finance_agents_prod_user'@'%';

FLUSH PRIVILEGES;
```

> **Note:** For Azure MySQL Flexible Server, replace `@'%'` with the
> appropriate host value if network restrictions are in place.

---

## Step 3: Configure Environment Variables

```bash
ssh finance-agents
cd /opt/finance-agents
cp deploy/.env.production.example .env.production
chmod 600 .env.production
nano .env.production   # Fill in all values
```

### Required values to set:

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Generate with: `python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DJANGO_ALLOWED_HOSTS` | `20.244.26.58,your-domain.com` |
| `CSRF_TRUSTED_ORIGINS` | `http://20.244.26.58,https://your-domain.com` |
| `DB_HOST` | MySQL server hostname |
| `DB_PASSWORD` | Production database password |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name (e.g., `gpt-4o`) |
| `AZURE_DI_ENDPOINT` | Azure Document Intelligence endpoint |
| `AZURE_DI_KEY` | Azure Document Intelligence key |

---

## Step 4: Deploy the Application

```bash
ssh finance-agents
sudo bash /opt/finance-agents/deploy/update_app.sh
```

This will:
1. Pull latest code from `main`
2. Create/update the virtualenv
3. Install Python dependencies
4. Run `manage.py check --deploy`
5. Run database migrations
6. Collect static files
7. Restart all services

---

## Step 5: Install systemd Services

```bash
ssh finance-agents

# Copy service files
sudo cp /opt/finance-agents/deploy/finance-agents-gunicorn.service /etc/systemd/system/
sudo cp /opt/finance-agents/deploy/finance-agents-celery.service /etc/systemd/system/
sudo cp /opt/finance-agents/deploy/finance-agents-celerybeat.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable services (auto-start on boot)
sudo systemctl enable finance-agents-gunicorn
sudo systemctl enable finance-agents-celery
sudo systemctl enable finance-agents-celerybeat

# Start services
sudo systemctl start finance-agents-gunicorn
sudo systemctl start finance-agents-celery
sudo systemctl start finance-agents-celerybeat
```

---

## Step 6: Install Nginx Configuration

```bash
ssh finance-agents

sudo cp /opt/finance-agents/deploy/nginx.conf /etc/nginx/sites-available/finance-agents
sudo ln -sf /etc/nginx/sites-available/finance-agents /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test and reload
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 7: Install Logrotate

```bash
sudo cp /opt/finance-agents/deploy/logrotate-finance-agents /etc/logrotate.d/finance-agents
```

---

## Step 8: Seed Initial Data

```bash
ssh finance-agents
cd /opt/finance-agents
source venv/bin/activate
source .env.production

python manage.py seed_rbac --sync-users
python manage.py seed_config
python manage.py seed_prompts
```

---

## Step 9: Create Admin Superuser

```bash
python manage.py createsuperuser
```

---

## Step 10: Verify Deployment

```bash
# Health checks
curl http://20.244.26.58/health/
curl http://20.244.26.58/health/ready/

# Check service status
sudo systemctl status finance-agents-gunicorn
sudo systemctl status finance-agents-celery
sudo systemctl status finance-agents-celerybeat
sudo systemctl status nginx
sudo systemctl status redis-server
```

Open in browser: `http://20.244.26.58/`

---

## Operations Reference

### Service Management

```bash
# Restart all services
sudo bash /opt/finance-agents/deploy/restart_services.sh

# Restart individual service
sudo systemctl restart finance-agents-gunicorn
sudo systemctl restart finance-agents-celery
sudo systemctl restart finance-agents-celerybeat

# Stop all
sudo systemctl stop finance-agents-gunicorn finance-agents-celery finance-agents-celerybeat
```

### View Logs

```bash
# Systemd journal logs
sudo journalctl -u finance-agents-gunicorn -f --no-pager
sudo journalctl -u finance-agents-celery -f --no-pager
sudo journalctl -u finance-agents-celerybeat -f --no-pager

# Application logs
tail -f /opt/finance-agents/logs/gunicorn-access.log
tail -f /opt/finance-agents/logs/gunicorn-error.log
tail -f /opt/finance-agents/logs/celery-worker.log
tail -f /opt/finance-agents/logs/nginx-access.log
tail -f /opt/finance-agents/logs/nginx-error.log
```

### Nginx

```bash
sudo nginx -t                    # Test config
sudo systemctl reload nginx      # Reload config
sudo systemctl restart nginx     # Full restart
```

### Redis

```bash
redis-cli ping                   # Check connectivity
redis-cli info memory            # Memory usage
sudo systemctl status redis-server
```

### Celery

```bash
# Check active workers
cd /opt/finance-agents && source venv/bin/activate
celery -A config inspect active
celery -A config inspect stats
celery -A config inspect scheduled
```

### Django Management

```bash
ssh finance-agents
cd /opt/finance-agents
source venv/bin/activate
set -a && source .env.production && set +a

python manage.py check --deploy
python manage.py showmigrations
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py shell
```

---

## Subsequent Deployments

For routine code updates:

```bash
ssh finance-agents
sudo bash /opt/finance-agents/deploy/update_app.sh
```

This is idempotent — safe to run multiple times.

---

## Django Production Settings Recommendations

The existing [config/settings.py](../config/settings.py) already reads most settings
from environment variables. When `DJANGO_DEBUG=False` in `.env.production`, these
take effect. Additional recommended settings to add for production hardening:

```python
# In config/settings.py — these are recommended additions for production:

# Secure proxy headers (Nginx sets X-Forwarded-Proto)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Session security
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 28800  # 8 hours

# HSTS (enable after HTTPS is confirmed working)
# SECURE_HSTS_SECONDS = 31536000
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True
```

---

## Celery Best Practices for This System

This platform runs diverse workloads. Recommended queue routing:

| Queue | Workloads |
|---|---|
| `default` | General tasks, review assignments |
| `extraction` | Invoice extraction, OCR processing |
| `reconciliation` | PO matching, GRN matching, tolerance checks |
| `agents` | AI agent orchestration, LLM calls |
| `scheduled` | Celery Beat periodic tasks |

Recommended Celery tuning (set in `config/settings.py`):

```python
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 600       # Hard kill after 10 min
CELERY_TASK_SOFT_TIME_LIMIT = 540  # SoftTimeLimitExceeded at 9 min
CELERY_WORKER_MAX_TASKS_PER_CHILD = 200  # Recycle workers to prevent memory leaks
```

---

## Security Checklist

- [ ] SSH key-only authentication (password login disabled)
- [ ] UFW firewall enabled (only SSH + HTTP/HTTPS open)
- [ ] fail2ban active
- [ ] `.env.production` has `chmod 600` (owner-only read)
- [ ] `DJANGO_DEBUG=False` in production
- [ ] Strong `DJANGO_SECRET_KEY` (50+ chars, random)
- [ ] Redis bound to `127.0.0.1` (default on Ubuntu)
- [ ] Gunicorn uses Unix socket (not TCP port)
- [ ] Nginx security headers configured
- [ ] Database uses dedicated production user
- [ ] HTTPS configured (via Certbot/Let's Encrypt or Azure Front Door)
- [ ] `CELERY_TASK_ALWAYS_EAGER=False` in production

---

## Files Reference

| File | Purpose |
|---|---|
| `deploy/bootstrap_server.sh` | One-time server setup |
| `deploy/update_app.sh` | Pull + install + migrate + restart |
| `deploy/restart_services.sh` | Restart all services |
| `deploy/nginx.conf` | Nginx reverse proxy config |
| `deploy/finance-agents-gunicorn.service` | Gunicorn systemd unit |
| `deploy/finance-agents-celery.service` | Celery worker systemd unit |
| `deploy/finance-agents-celerybeat.service` | Celery Beat systemd unit |
| `deploy/.env.production.example` | Environment variable template |
| `deploy/logrotate-finance-agents` | Log rotation config |
| `deploy/connect.ps1` | Windows SSH connect helper |
| `deploy/scp_upload.ps1` | Windows SCP upload helper |
| `deploy/ssh_config` | SSH config template |

---

## Assumptions & Placeholders

| Item | Assumption | Action Required |
|---|---|---|
| Server IP | `20.244.26.58` | Update if IP changes |
| SSH user | `azureuser` | — |
| Service user | `financeagents` | Created by bootstrap |
| MySQL host | `127.0.0.1` | Update if using Azure MySQL |
| Domain name | `finance-agents.yourdomain.com` | Replace with real domain |
| HTTPS | Not configured | Add Certbot or Azure Front Door |
| Git branch | `main` | Change in `update_app.sh` if different |
| Python version | `python3` (3.10+) | Ubuntu 22.04 ships 3.10 |
