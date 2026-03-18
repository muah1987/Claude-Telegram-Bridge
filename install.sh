#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Claude Telegram Bridge — Install Script
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

CTG_DIR="$HOME/.claude-telegram"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "════════════════════════════════════════════════"
echo "  Claude Telegram Bridge — Installatie"
echo "════════════════════════════════════════════════"
echo

# ── 1. Check Python ──────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 niet gevonden."
    exit 1
fi
echo "✅ Python3: $(python3 --version)"

# ── 2. Check Claude Code CLI ─────────────────────────────────────────────────
if ! command -v claude &>/dev/null; then
    echo "⚠️  'claude' CLI niet gevonden in PATH."
    echo "   Installeer Claude Code CLI: https://claude.ai/code"
else
    echo "✅ Claude Code CLI: $(claude --version 2>/dev/null || echo 'aanwezig')"
fi

# ── 3. Install Python dependencies ───────────────────────────────────────────
echo
echo "📦 Python dependencies installeren…"
pip3 install --quiet --user \
    "python-telegram-bot>=21.0" \
    aiofiles

echo "✅ Dependencies geïnstalleerd"

# ── 4. Create ~/.claude-telegram directory ───────────────────────────────────
echo
echo "📁 Mappen aanmaken…"
mkdir -p "$CTG_DIR/hooks"
mkdir -p "$CTG_DIR/logs"

# ── 5. Copy source files ─────────────────────────────────────────────────────
cp "$SCRIPT_DIR/src/daemon.py"               "$CTG_DIR/daemon.py"
cp "$SCRIPT_DIR/src/hooks/pre_tool.py"       "$CTG_DIR/hooks/pre_tool.py"
cp "$SCRIPT_DIR/src/hooks/post_tool.py"      "$CTG_DIR/hooks/post_tool.py"
cp "$SCRIPT_DIR/src/hooks/stop.py"           "$CTG_DIR/hooks/stop.py"
cp "$SCRIPT_DIR/src/hooks/notification.py"   "$CTG_DIR/hooks/notification.py"

chmod +x "$CTG_DIR/daemon.py"
chmod +x "$CTG_DIR/hooks/"*.py
echo "✅ Bestanden gekopieerd naar $CTG_DIR"

# ── 6. Install claude-tg binary ──────────────────────────────────────────────
echo
echo "🔧 claude-tg installeren…"

INSTALL_DIR="$HOME/.local/bin"
mkdir -p "$INSTALL_DIR"

# Update paths in wrapper to point to installed location
sed "s|Path(__file__).parent.parent / \"src\"|Path('$CTG_DIR')|g" \
    "$SCRIPT_DIR/bin/claude-tg" > "$INSTALL_DIR/claude-tg"
chmod +x "$INSTALL_DIR/claude-tg"

echo "✅ claude-tg geïnstalleerd: $INSTALL_DIR/claude-tg"

# ── 7. Add to PATH if needed ─────────────────────────────────────────────────
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo
    echo "⚠️  $INSTALL_DIR staat niet in je PATH."
    echo "   Voeg dit toe aan je ~/.bashrc of ~/.zshrc:"
    echo
    echo '   export PATH="$HOME/.local/bin:$PATH"'
fi

# ── 8. Auto-start daemon (systemd or launchd) ─────────────────────────────────
echo
echo "🔄 Auto-start daemon configureren…"

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS: LaunchAgent
    PLIST_DIR="$HOME/Library/LaunchAgents"
    mkdir -p "$PLIST_DIR"
    cat > "$PLIST_DIR/com.claude-telegram.daemon.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-telegram.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(command -v python3)</string>
        <string>$CTG_DIR/daemon.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$CTG_DIR/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>$CTG_DIR/daemon.log</string>
</dict>
</plist>
EOF
    launchctl load "$PLIST_DIR/com.claude-telegram.daemon.plist" 2>/dev/null || true
    echo "✅ LaunchAgent aangemaakt (macOS)"

elif command -v systemctl &>/dev/null && [[ -d "$HOME/.config/systemd/user" ]] || mkdir -p "$HOME/.config/systemd/user" 2>/dev/null; then
    # Linux: systemd user service
    cat > "$HOME/.config/systemd/user/claude-telegram.service" << EOF
[Unit]
Description=Claude Telegram Bridge Daemon
After=network.target

[Service]
Type=simple
ExecStart=$(command -v python3) $CTG_DIR/daemon.py
Restart=always
RestartSec=5
StandardOutput=append:$CTG_DIR/daemon.log
StandardError=append:$CTG_DIR/daemon.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable claude-telegram.service
    echo "✅ Systemd user service aangemaakt (Linux)"
    echo "   Start nu met: systemctl --user start claude-telegram"
fi

# ── 9. Shell integration (optional) ──────────────────────────────────────────
echo
echo "🐚 Shell integratie (optioneel)…"
echo "   Voeg dit toe aan ~/.bashrc of ~/.zshrc om claude automatisch"
echo "   via Telegram te integreren bij elke sessie:"
echo
echo '   # Claude Telegram Bridge'
echo '   alias claude="claude-tg"'
echo '   alias ctg="claude-tg"'
echo

# ── 10. Done ─────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════"
echo "  ✅ Installatie voltooid!"
echo "════════════════════════════════════════════════"
echo
echo "Volgende stappen:"
echo "  1. claude-tg --setup      (Telegram bot configureren)"
echo "  2. claude-tg --daemon     (daemon starten)"
echo "  3. claude-tg              (sessie starten)"
echo
echo "Meer info: claude-tg --help"
