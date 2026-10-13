#!/bin/bash
# Startet XRZ Vincent automatisch bei der Anmeldung (macOS-Anmeldeobjekt).
#   Einschalten:  bash scripts/autostart_mac.sh an
#   Ausschalten:  bash scripts/autostart_mac.sh aus
set -e
APP="/Applications/XRZ Vincent.app"
case "${1:-an}" in
  an|on)
    [ -d "$APP" ] || { echo "Bitte zuerst installieren: bash install_mac.sh"; exit 1; }
    osascript -e "tell application \"System Events\" to delete (every login item whose name is \"XRZ Vincent\")" >/dev/null 2>&1 || true
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:false}" >/dev/null
    echo "✅ Vincent startet ab jetzt automatisch bei der Anmeldung."
    ;;
  aus|off)
    osascript -e "tell application \"System Events\" to delete (every login item whose name is \"XRZ Vincent\")" >/dev/null 2>&1 || true
    echo "Autostart entfernt."
    ;;
  *)
    echo "Benutzung: bash scripts/autostart_mac.sh an|aus"
    ;;
esac
