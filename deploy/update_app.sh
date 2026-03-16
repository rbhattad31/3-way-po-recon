#!/usr/bin/env bash
# =============================================================================
# Finance Agents Platform — Application Update Script
# =============================================================================
# Pull latest code, install deps, run migrations, collect static, restart.
#
# Usage:
#   sudo bash /opt/finance-agents/deploy/update_app.sh
# =============================================================================

set -euo pipefail

APP_DIR="/opt/finance-agents"
VENV_DIR="$APP_DIR/venv"
ENV_FILE="$APP_DIR/.env.production"
APP_USER="financeagents"

echo "============================================="
echo " Finance Agents — Application Update"
echo "============================================="

# ------------------------------------------------------------------
# 1. Validate env file
# ------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found."
    echo "Copy .env.production.example and fill in values first."
    exit 1
fi

# ------------------------------------------------------------------
# 2. Pull latest code
# ------------------------------------------------------------------
echo "[1/7] Pulling latest code..."
cd "$APP_DIR"
git pull origin main

# ------------------------------------------------------------------
# 3. Create/verify virtualenv
# ------------------------------------------------------------------
echo "[2/7] Verifying virtualenv..."
if [ ! -d "$VENV_DIR/bin" ]; then
    python3 -m venv "$VENV_DIR"
fi

# ------------------------------------------------------------------
# 4. Install dependencies
# ------------------------------------------------------------------
echo "[3/7] Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
"$VENV_DIR/bin/pip" install gunicorn

# ------------------------------------------------------------------
# 5. Load env and run Django management commands
# ------------------------------------------------------------------
echo "[4/7] Running Django deployment checks..."
set -a
source "$ENV_FILE"
set +a

"$VENV_DIR/bin/python" manage.py check --deploy 2>&1 || true

echo "[5/7] Running database migrations..."
"$VENV_DIR/bin/python" manage.py migrate --noinput

echo "[6/7] Collecting static files..."
"$VENV_DIR/bin/python" manage.py collectstatic --noinput

# ------------------------------------------------------------------
# 6. Fix permissions
# ------------------------------------------------------------------
echo "[7/7] Fixing permissions..."
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"/{run,logs,static,media}

# ------------------------------------------------------------------
# 7. Restart services
# ------------------------------------------------------------------
echo "Restarting services..."
bash "$APP_DIR/deploy/restart_services.sh"

echo ""
echo "============================================="
echo " Update complete!"
echo "============================================="
