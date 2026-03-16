#!/usr/bin/env bash
# =============================================================================
# Finance Agents Platform — Monitoring Setup Script
# =============================================================================
# Sets up Flower, logrotate, and optional system monitoring tools.
#
# Usage:
#   sudo bash /opt/finance-agents/deploy/setup_monitoring.sh
# =============================================================================

set -euo pipefail

APP_DIR="/opt/finance-agents"
VENV_DIR="$APP_DIR/venv"
APP_USER="financeagents"

echo "============================================="
echo " Finance Agents — Monitoring Setup"
echo "============================================="

# ------------------------------------------------------------------
# 1. Install Flower into virtualenv
# ------------------------------------------------------------------
echo "[1/7] Installing Flower..."
"$VENV_DIR/bin/pip" install flower --quiet
echo "  Flower installed: $($VENV_DIR/bin/celery --version)"

# ------------------------------------------------------------------
# 2. Install Flower systemd service
# ------------------------------------------------------------------
echo "[2/7] Installing Flower systemd service..."
cp "$APP_DIR/deploy/finance-agents-flower.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable finance-agents-flower
systemctl restart finance-agents-flower
echo "  Flower service installed and started."

# ------------------------------------------------------------------
# 3. Update Nginx config (Flower proxy block)
# ------------------------------------------------------------------
echo "[3/7] Updating Nginx configuration..."
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/finance-agents
ln -sf /etc/nginx/sites-available/finance-agents /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
echo "  Nginx updated with /flower/ proxy."

# ------------------------------------------------------------------
# 4. Install logrotate configuration
# ------------------------------------------------------------------
echo "[4/7] Installing logrotate configuration..."
cp "$APP_DIR/deploy/logrotate-finance-agents" /etc/logrotate.d/finance-agents
echo "  Logrotate config installed."

# ------------------------------------------------------------------
# 5. Ensure log directory permissions
# ------------------------------------------------------------------
echo "[5/7] Fixing log directory permissions..."
mkdir -p "$APP_DIR/logs"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/logs"
chmod 775 "$APP_DIR/logs"

# ------------------------------------------------------------------
# 6. Install optional system monitoring tools
# ------------------------------------------------------------------
echo "[6/7] Installing system monitoring tools..."
apt-get install -y -qq sysstat htop iotop net-tools > /dev/null 2>&1 || true
# Enable sysstat collection (sar)
if [ -f /etc/default/sysstat ]; then
    sed -i 's/ENABLED="false"/ENABLED="true"/' /etc/default/sysstat
    systemctl enable sysstat 2>/dev/null || true
    systemctl restart sysstat 2>/dev/null || true
fi
echo "  System tools installed (htop, sysstat, iotop, net-tools)."

# ------------------------------------------------------------------
# 7. Verification
# ------------------------------------------------------------------
echo ""
echo "[7/7] Verifying setup..."
echo ""

echo "=== Service Status ==="
for svc in finance-agents-gunicorn finance-agents-celery finance-agents-celerybeat finance-agents-flower nginx redis-server; do
    status=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
    printf "  %-38s %s\n" "$svc" "$status"
done

echo ""
echo "=== Health Endpoints ==="
echo "  /health/:       $(curl -s -o /dev/null -w '%{http_code}' http://localhost/health/)"
echo "  /health/live/:  $(curl -s -o /dev/null -w '%{http_code}' http://localhost/health/live/)"
echo "  /health/ready/: $(curl -s -o /dev/null -w '%{http_code}' http://localhost/health/ready/)"
echo "  /flower/:       $(curl -s -o /dev/null -w '%{http_code}' http://localhost/flower/)"

echo ""
echo "=== Log Files ==="
ls -lh "$APP_DIR/logs/"

echo ""
echo "============================================="
echo " Monitoring setup complete!"
echo "============================================="
echo ""
echo " Flower UI:  http://$(hostname -I | awk '{print $1}')/flower/"
echo " Flower auth: admin / FinanceAgents2026!"
echo "   (Change password in finance-agents-flower.service)"
echo ""
echo " Health endpoints:"
echo "   http://$(hostname -I | awk '{print $1}')/health/"
echo "   http://$(hostname -I | awk '{print $1}')/health/live/"
echo "   http://$(hostname -I | awk '{print $1}')/health/ready/"
echo ""
