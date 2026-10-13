#!/bin/bash
# XRZ Vincent – Installation & Build für macOS (Apple Silicon oder Intel, macOS 13+)
# Aufruf im Terminal:  cd <Projektordner> && bash install_mac.sh
# Ergebnis: /Applications/XRZ Vincent.app
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
APP_NAME="XRZ Vincent"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
VENV="$SUPPORT/venv"

bold() { printf "\n\033[1m%s\033[0m\n" "$1"; }
bold "=== $APP_NAME – Installation ==="

# 1) Homebrew ------------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  bold "1/7 Homebrew wird installiert (dein Mac-Passwort wird abgefragt) …"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
[ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"

# 2) Python, Node, Ollama, ffmpeg -------------------------------------------------
bold "2/7 Python 3.12, Node.js, Ollama …"
command -v python3.12 >/dev/null 2>&1 || brew install python@3.12
command -v node >/dev/null 2>&1 || brew install node
command -v ollama >/dev/null 2>&1 || brew install ollama
PY="$(command -v python3.12)"

# 3) Backend (Python) --------------------------------------------------------------
bold "3/7 Vincents Gehirn (Python-Backend) …"
mkdir -p "$SUPPORT"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel >/dev/null
"$VENV/bin/pip" install -r requirements.txt
"$VENV/bin/pip" install --upgrade --no-deps .
"$VENV/bin/python" -c "import xrz, sys; print('Backend', xrz.__version__, 'mit Python', sys.version.split()[0])"

# 4) Lokale KI ---------------------------------------------------------------------
bold "4/7 Lokale KI (Ollama) …"
if ! curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
  brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
  for _ in $(seq 1 20); do curl -s http://localhost:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
fi
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [ "$RAM_GB" -ge 32 ]; then MODEL="qwen3:14b"
elif [ "$RAM_GB" -ge 16 ]; then MODEL="qwen3:8b"
else MODEL="qwen3:4b"; fi
echo "Arbeitsspeicher: ${RAM_GB} GB → lokales KI-Modell: $MODEL"
ollama pull "$MODEL"
ollama pull nomic-embed-text
"$VENV/bin/python" - <<PYEOF
from xrz.config import ConfigManager
c = ConfigManager()
c.settings.ai.ollama_model = "$MODEL"
c.save()
print("Einstellungen:", c.data_dir)
PYEOF

# 5) Sprachmodelle vorab laden (sonst beim ersten Start) -----------------------------
bold "5/7 Spracherkennung & Stimme vorbereiten (einmalig, kann ein paar Minuten dauern) …"
"$VENV/bin/python" -m xrz prefetch || echo "Hinweis: Modelle werden beim ersten Start nachgeladen."

# 6) App bauen ------------------------------------------------------------------------
bold "6/7 App „$APP_NAME“ bauen …"
mkdir -p app/build
cat > app/build/backend.json <<JSON
{"python": "$VENV/bin/python", "cwd": "$SUPPORT"}
JSON
cd app
npm install --no-audit --no-fund
npx electron-builder --mac dir
APP_SRC="$(ls -d dist/mac*/"$APP_NAME.app" | head -n 1)"
[ -d "$APP_SRC" ] || { echo "Build fehlgeschlagen – keine App gefunden."; exit 1; }
# Ad-hoc-Signatur mit Mikrofon-Berechtigung (ohne Apple-Entwicklerkonto)
codesign --force --deep --sign - --options runtime --entitlements build/entitlements.mac.plist "$APP_SRC"
cd "$ROOT"

# 7) Installieren ------------------------------------------------------------------------
bold "7/7 Nach /Applications kopieren …"
osascript -e "tell application \"$APP_NAME\" to quit" >/dev/null 2>&1 || true
rm -rf "/Applications/$APP_NAME.app"
cp -R "app/$APP_SRC" "/Applications/"
xattr -cr "/Applications/$APP_NAME.app" || true

bold "✅ Fertig!"
cat <<TXT
Starte „$APP_NAME“ aus dem Programme-Ordner (oder Spotlight: ⌘ Leertaste → Vincent).

Beim ersten Start:
  • Mikrofon erlauben (für „Hey Vincent“)
  • Zentrale → Einstellungen: optional Claude-, OpenAI- oder ElevenLabs-Key eintragen
    (ElevenLabs = sehr realistische Stimme, Claude = deutlich schlauer + Mac-Steuerung)
  • Zentrale → TradingView → „Verbinden“ (Vincent startet die TradingView-App einmal mit Steuer-Port neu)
  • Für Mac-Steuerung: Systemeinstellungen → Datenschutz & Sicherheit →
      Bedienungshilfen und Bildschirmaufnahme → „$APP_NAME“ erlauben

Sprich: „Hey Vincent, wie sieht der Markt aus?“  (oder ⌥ Leertaste zum Sprechen)
TXT
