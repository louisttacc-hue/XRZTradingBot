#!/bin/bash
# XRZ Trading Bot starten (Doppelklick im Finder oder ./start_mac.command)
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Noch nicht installiert – starte Installation …"
  bash install_mac.sh || exit 1
fi

# Lokale KI starten, falls sie nicht läuft
if command -v ollama >/dev/null 2>&1 && ! curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
  if [ -d /Applications/Ollama.app ]; then
    open -a Ollama
  else
    brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
  fi
  sleep 3
fi

# caffeinate verhindert, dass der Mac einschläft, solange der Bot läuft
# (wichtig: der Bot überwacht Stop-Loss und Take-Profit)
exec caffeinate -i .venv/bin/python -m xrz "$@"
