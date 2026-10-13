#!/bin/bash
# XRZ Vincent starten (Doppelklick im Finder). Beim ersten Mal wird alles installiert.
cd "$(dirname "$0")"
if [ ! -d "/Applications/XRZ Vincent.app" ]; then
  echo "XRZ Vincent ist noch nicht installiert – starte Installation …"
  bash install_mac.sh || exit 1
fi
open -a "XRZ Vincent"
