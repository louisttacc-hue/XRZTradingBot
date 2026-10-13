#!/bin/bash
# XRZ Trading Bot – Installation für macOS
# Aufruf im Terminal:  cd <Projektordner> && bash install_mac.sh
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"

bold() { printf "\n\033[1m%s\033[0m\n" "$1"; }
bold "=== XRZ Trading Bot – Installation (macOS) ==="

# 1) Homebrew ---------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  bold "1/5 Homebrew wird installiert (dein Mac-Passwort wird abgefragt) …"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
[ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"

# 2) Python -----------------------------------------------------------------
bold "2/5 Python 3.12 …"
if ! command -v python3.12 >/dev/null 2>&1; then
  brew install python@3.12
fi
PY="$(command -v python3.12)"
echo "Python: $PY"

# 3) Programm-Abhängigkeiten --------------------------------------------------
bold "3/5 Programm-Pakete installieren …"
if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

# 4) Lokale KI (Ollama) -------------------------------------------------------
bold "4/5 Lokale KI (Ollama) …"
if ! command -v ollama >/dev/null 2>&1; then
  brew install ollama
fi
if ! curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
  if [ -d /Applications/Ollama.app ]; then
    open -a Ollama
  else
    brew services start ollama || (nohup ollama serve >/dev/null 2>&1 &)
  fi
  for i in $(seq 1 20); do
    curl -s http://localhost:11434/api/version >/dev/null 2>&1 && break
    sleep 1
  done
fi

RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [ "$RAM_GB" -ge 32 ]; then MODEL="qwen3:14b"
elif [ "$RAM_GB" -ge 16 ]; then MODEL="qwen3:8b"
else MODEL="qwen3:4b"; fi
echo "Arbeitsspeicher: ${RAM_GB} GB → KI-Modell: $MODEL"
ollama pull "$MODEL"
ollama pull nomic-embed-text   # für die semantische Wissenssuche

.venv/bin/python - <<PYEOF
from xrz.config import ConfigManager
c = ConfigManager()
c.settings.ai.ollama_model = "$MODEL"
c.save()
print("Einstellungen gespeichert in:", c.data_dir)
PYEOF

# 5) Starter auf dem Schreibtisch ------------------------------------------------
bold "5/5 Starter anlegen …"
chmod +x start_mac.command scripts/*.sh 2>/dev/null || true
LAUNCHER="$HOME/Desktop/XRZ Trading Bot.command"
cat > "$LAUNCHER" <<EOF
#!/bin/bash
cd "$ROOT" && ./start_mac.command
EOF
chmod +x "$LAUNCHER"

bold "✅ Fertig!"
cat <<TXT
Start: Doppelklick auf "XRZ Trading Bot" auf dem Schreibtisch
       (oder im Terminal:  ./start_mac.command)

Danach öffnet sich das Dashboard im Browser (http://127.0.0.1:8765):
  1. Einstellungen → Telegram-Bot-Token eintragen (von @BotFather)
  2. Deinem Bot in Telegram  /start <CODE>  schicken (Code steht im Dashboard)
  3. Optional: Anthropic- oder OpenAI-API-Key eintragen (noch schlauere KI)
  4. /paper  → Paper-Trading starten

macOS-Berechtigungen (Systemeinstellungen → Datenschutz & Sicherheit):
  • Bildschirmaufnahme → Terminal erlauben (für /chart und /screenshot)
  • Bedienungshilfen   → Terminal erlauben (nur für TradingView-App-Steuerung)
TXT
