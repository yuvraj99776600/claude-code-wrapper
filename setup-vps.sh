#!/bin/bash
# =============================================================
#  Claude Code Wrapper — VPS Setup Script (Ubuntu 25.04)
#  Run as root:  bash setup-vps.sh
# =============================================================
set -euo pipefail

echo "============================================"
echo "  Claude Code Wrapper — VPS Setup"
echo "============================================"

# ---- 1. System packages ----
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git curl wget \
    tigervnc-standalone-server tigervnc-xorg-extension \
    xfce4 xfce4-terminal dbus-x11 \
    fonts-liberation libnss3 libatk-bridge2.0-0 \
    libdrm2 libxkbcommon0 libgbm1 libasound2t64 \
    > /dev/null 2>&1

echo "    Done."

# ---- 2. Create service user ----
echo "[2/7] Creating 'claude' service user..."
if id "claude" &>/dev/null; then
    echo "    User 'claude' already exists, skipping."
else
    useradd -m -s /bin/bash claude
    echo "    Created user 'claude'."
fi

# ---- 3. Clone repo ----
echo "[3/7] Cloning repo..."
REPO_DIR="/home/claude/claude-code-wrapper"
if [ -d "$REPO_DIR" ]; then
    echo "    Repo exists, pulling latest..."
    sudo -u claude git -C "$REPO_DIR" pull
else
    sudo -u claude git clone https://github.com/yuvraj99776600/claude-code-wrapper.git "$REPO_DIR"
fi
echo "    Done."

# ---- 4. Python environment + install ----
echo "[4/7] Setting up Python environment..."
sudo -u claude python3 -m venv /home/claude/venv
sudo -u claude /home/claude/venv/bin/pip install --upgrade pip setuptools -q
sudo -u claude /home/claude/venv/bin/pip install -e "$REPO_DIR" -q
echo "    Done."

# ---- 5. Install Playwright + Chromium ----
echo "[5/7] Installing Playwright Chromium..."
sudo -u claude /home/claude/venv/bin/playwright install chromium
sudo -u claude /home/claude/venv/bin/playwright install-deps chromium 2>/dev/null || true
echo "    Done."

# ---- 6. VNC setup (for one-time login) ----
echo "[6/7] Setting up VNC..."
sudo -u claude mkdir -p /home/claude/.vnc

# Create VNC startup script
sudo -u claude tee /home/claude/.vnc/xstartup > /dev/null << 'VNCEOF'
#!/bin/bash
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec startxfce4
VNCEOF
chmod +x /home/claude/.vnc/xstartup

echo ""
echo "    ┌─────────────────────────────────────────┐"
echo "    │  Set a VNC password for the claude user: │"
echo "    └─────────────────────────────────────────┘"
sudo -u claude vncpasswd /home/claude/.vnc/passwd
echo "    VNC configured."

# ---- 7. Proxy config placeholder ----
echo "[7/7] Creating proxy config..."
PROXY_FILE="/home/claude/.claude-proxy"
if [ ! -f "$PROXY_FILE" ]; then
    sudo -u claude tee "$PROXY_FILE" > /dev/null << 'PROXYEOF'
# Paste your proxy URL below (one line, no spaces):
# Format: http://username:password@host:port
#
# Example: http://user123:pass456@proxy.example.com:8080
#
# Leave empty or commented out for no proxy.
PROXY_URL=

# Timezone to spoof (match your proxy's location)
# Example: America/New_York, Europe/London, Asia/Kolkata
TIMEZONE=

# Locale to spoof (match your proxy's region)
# Example: en-US, en-GB, hi-IN
LOCALE=
PROXYEOF
fi
echo "    Edit /home/claude/.claude-proxy to set your proxy."

# ---- 8. Systemd service ----
echo "[+] Creating systemd service..."
cat > /etc/systemd/system/claude-code.service << 'SVCEOF'
[Unit]
Description=Claude Code Wrapper API
After=network.target

[Service]
User=claude
Group=claude
WorkingDirectory=/home/claude
EnvironmentFile=/home/claude/.claude-proxy
ExecStart=/bin/bash -c 'source /home/claude/.claude-proxy && /home/claude/venv/bin/claude-code serve --slots 3 --port 5050 ${PROXY_URL:+--proxy "$PROXY_URL"} ${TIMEZONE:+--timezone "$TIMEZONE"} ${LOCALE:+--locale "$LOCALE"}'
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable claude-code.service
echo "    Service created (will start after first login)."

# ---- Done ----
echo ""
echo "============================================"
echo "  SETUP COMPLETE!"
echo "============================================"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Set your proxy:"
echo "     nano /home/claude/.claude-proxy"
echo "     # Set PROXY_URL=http://user:pass@host:port"
echo ""
echo "  2. Start VNC (for one-time Claude login):"
echo "     sudo -u claude vncserver :1 -geometry 1280x900 -depth 24"
echo ""
echo "  3. Connect with VNC viewer to:  YOUR_VPS_IP:5901"
echo "     Open a terminal in the VNC desktop and run:"
echo ""
echo "     export PROXY_URL=\$(grep '^PROXY_URL=' ~/.claude-proxy | cut -d= -f2)"
echo "     ~/venv/bin/claude-code serve --visible --slots 3 --proxy \"\$PROXY_URL\""
echo ""
echo "  4. Log into claude.ai in the browser that opens."
echo "     Once logged in, Ctrl+C to stop the server."
echo ""
echo "  5. Kill VNC — you won't need it again:"
echo "     sudo -u claude vncserver -kill :1"
echo ""
echo "  6. Start the headless service:"
echo "     systemctl start claude-code.service"
echo ""
echo "  7. Check it's running:"
echo "     systemctl status claude-code.service"
echo "     journalctl -u claude-code.service -f"
echo ""
echo "  Your API will be at: http://127.0.0.1:5050/v1/messages"
echo "  API keys are printed in the service logs."
echo "============================================"
