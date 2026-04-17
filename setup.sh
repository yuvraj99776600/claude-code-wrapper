#!/bin/bash
# =============================================================
#  Claude Code Wrapper — Setup (Ubuntu 24.04 / 25.04)
#  Run as root:  sudo bash setup.sh
# =============================================================
set -euo pipefail

USER_NAME="${USER_NAME:-claude}"
REPO_URL="${REPO_URL:-https://github.com/yuvraj99776600/claude-code-wrapper.git}"

echo "================================================"
echo "  Claude Code Wrapper — Setup"
echo "================================================"

# ---- 1. System packages ----
echo "[1/5] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl

# ---- 2. Node.js (required for `claude` CLI) ----
if ! command -v node >/dev/null 2>&1; then
    echo "[2/5] Installing Node.js LTS..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
else
    echo "[2/5] Node.js already installed: $(node -v)"
fi

# ---- 3. Claude Code CLI ----
if ! command -v claude >/dev/null 2>&1; then
    echo "[3/5] Installing @anthropic-ai/claude-code..."
    npm install -g @anthropic-ai/claude-code
else
    echo "[3/5] claude CLI already installed: $(claude --version 2>/dev/null || echo unknown)"
fi

# ---- 4. User + venv + repo ----
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    echo "[4/5] Creating user '$USER_NAME'..."
    useradd -m -s /bin/bash "$USER_NAME"
fi

HOME_DIR="/home/$USER_NAME"
REPO_DIR="$HOME_DIR/claude-code-wrapper"

if [ ! -d "$REPO_DIR/.git" ]; then
    sudo -u "$USER_NAME" git clone "$REPO_URL" "$REPO_DIR"
else
    sudo -u "$USER_NAME" git -C "$REPO_DIR" pull --ff-only
fi

if [ ! -d "$HOME_DIR/venv" ]; then
    sudo -u "$USER_NAME" python3 -m venv "$HOME_DIR/venv"
fi
sudo -u "$USER_NAME" "$HOME_DIR/venv/bin/pip" install -U pip
sudo -u "$USER_NAME" "$HOME_DIR/venv/bin/pip" install "$REPO_DIR"

# ---- 5. systemd service ----
echo "[5/5] Creating systemd service..."

cat > /etc/systemd/system/claude-code.service <<EOF
[Unit]
Description=Claude Code Wrapper API
After=network.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$HOME_DIR
ExecStart=$HOME_DIR/venv/bin/claude-code serve --host 0.0.0.0 --port 5050 --slots 3
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

cat <<DONE

================================================
  Setup complete.

  Next steps:
    1. Log in to Claude (one-time, as the service user):
         sudo -u $USER_NAME $HOME_DIR/venv/bin/python -c ''
         sudo -iu $USER_NAME
         claude   # follow the OAuth prompt in your browser
         exit

    2. Start the service:
         systemctl enable --now claude-code.service
         journalctl -u claude-code.service -f

    3. Your API keys are printed in the logs on startup.
================================================
DONE
