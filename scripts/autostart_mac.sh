#!/bin/bash
# Startet den XRZ Trading Bot automatisch bei der Anmeldung (launchd) und
# startet ihn neu, falls er abstürzt.
#   Einschalten:  bash scripts/autostart_mac.sh an
#   Ausschalten:  bash scripts/autostart_mac.sh aus
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LABEL="de.xrz.tradingbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs/XRZTradingBot"

case "${1:-an}" in
  an|on)
    mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$ROOT/.venv/bin/python</string><string>-m</string><string>xrz</string><string>--no-browser</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/bot.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/bot.err.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "✅ Autostart aktiv. Logs: $LOGDIR"
    ;;
  aus|off)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Autostart entfernt."
    ;;
  *)
    echo "Benutzung: bash scripts/autostart_mac.sh an|aus"
    ;;
esac
