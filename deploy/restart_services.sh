#!/usr/bin/env bash
# =============================================================================
# Finance Agents Platform — Restart All Services
# =============================================================================
# Usage:
#   sudo bash /opt/finance-agents/deploy/restart_services.sh
# =============================================================================

set -euo pipefail

echo "============================================="
echo " Finance Agents — Restarting Services"
echo "============================================="

echo "[1/5] Reloading systemd daemon..."
systemctl daemon-reload

echo "[2/5] Restarting Gunicorn..."
systemctl restart finance-agents-gunicorn
systemctl status finance-agents-gunicorn --no-pager -l || true

echo "[3/5] Restarting Celery worker..."
systemctl restart finance-agents-celery
systemctl status finance-agents-celery --no-pager -l || true

echo "[4/5] Restarting Celery Beat..."
systemctl restart finance-agents-celerybeat
systemctl status finance-agents-celerybeat --no-pager -l || true

echo "[5/5] Reloading Nginx..."
nginx -t && systemctl reload nginx
systemctl status nginx --no-pager -l || true

echo ""
echo "============================================="
echo " All services restarted."
echo "============================================="
echo ""
echo "Quick health check:"
echo "  curl -s http://localhost/health/ | python3 -m json.tool"
echo ""
