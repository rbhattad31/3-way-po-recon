#!/usr/bin/env bash
# =============================================================================
# Finance Agents Platform — Server Bootstrap Script
# =============================================================================
# Run ONCE on a fresh Azure Ubuntu VM to install all system dependencies.
#
# Usage:
#   sudo bash /opt/finance-agents/deploy/bootstrap_server.sh
# =============================================================================

set -euo pipefail

APP_DIR="/opt/finance-agents"
APP_USER="financeagents"
APP_GROUP="financeagents"

echo "============================================="
echo " Finance Agents — Server Bootstrap"
echo "============================================="

# ------------------------------------------------------------------
# 1. System packages
# ------------------------------------------------------------------
echo "[1/8] Updating system packages..."
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

echo "[2/8] Installing required packages..."
apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    nginx \
    redis-server \
    git \
    curl \
    wget \
    unzip \
    mysql-client \
    libmysqlclient-dev \
    pkg-config \
    libssl-dev \
    libffi-dev \
    supervisor \
    logrotate \
    ufw \
    fail2ban

# ------------------------------------------------------------------
# 2. Create application user (no login shell)
# ------------------------------------------------------------------
echo "[3/8] Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

# ------------------------------------------------------------------
# 3. Create application directories
# ------------------------------------------------------------------
echo "[4/8] Creating application directories..."
mkdir -p "$APP_DIR"/{venv,run,logs,static,media,deploy}

# ------------------------------------------------------------------
# 4. Set permissions
# ------------------------------------------------------------------
echo "[5/8] Setting permissions..."
chown -R "$APP_USER":"$APP_GROUP" "$APP_DIR"/{run,logs,static,media}
# The code directory stays owned by the deploy user (azureuser),
# but the service user can read it.
chmod 755 "$APP_DIR"

# ------------------------------------------------------------------
# 5. Create Python virtualenv
# ------------------------------------------------------------------
echo "[6/8] Creating Python virtualenv..."
if [ ! -d "$APP_DIR/venv/bin" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip setuptools wheel

# ------------------------------------------------------------------
# 6. Enable system services
# ------------------------------------------------------------------
echo "[7/8] Enabling system services..."
systemctl enable nginx
systemctl start nginx

systemctl enable redis-server
systemctl start redis-server

# ------------------------------------------------------------------
# 7. Firewall (UFW)
# ------------------------------------------------------------------
echo "[8/8] Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 'Nginx Full'
# Allow only if you need direct MySQL access; otherwise keep closed
# ufw allow 3306/tcp
ufw --force enable

# ------------------------------------------------------------------
# 8. Fail2ban
# ------------------------------------------------------------------
systemctl enable fail2ban
systemctl start fail2ban

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "============================================="
echo " Bootstrap complete!"
echo "============================================="
echo ""
echo " Next steps:"
echo "  1. Copy .env.production.example → .env.production and fill in values"
echo "  2. Run: sudo bash $APP_DIR/deploy/update_app.sh"
echo "  3. Install systemd services:"
echo "     sudo cp $APP_DIR/deploy/finance-agents-*.service /etc/systemd/system/"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable finance-agents-gunicorn"
echo "     sudo systemctl enable finance-agents-celery"
echo "     sudo systemctl enable finance-agents-celerybeat"
echo "  4. Install nginx config:"
echo "     sudo cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/finance-agents"
echo "     sudo ln -sf /etc/nginx/sites-available/finance-agents /etc/nginx/sites-enabled/"
echo "     sudo rm -f /etc/nginx/sites-enabled/default"
echo "     sudo nginx -t && sudo systemctl reload nginx"
echo "  5. Start services:"
echo "     sudo bash $APP_DIR/deploy/restart_services.sh"
echo ""
