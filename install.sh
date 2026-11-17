#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  EF-Untis-WhatsApp-Bot – Installation, Update und Einrichtung
#
#    sudo ./install.sh                 Installieren bzw. aktualisieren (Wizard beim ersten Mal)
#    sudo ./install.sh --reconfigure   Zugangsdaten/Einstellungen neu eingeben
#    sudo ./install.sh --relink        WhatsApp neu koppeln (QR-Code) und Gruppe wählen
#    sudo ./install.sh --uninstall     Dienst entfernen (Daten bleiben erhalten)
#
#  Optionen: --dir /opt/ef-untis-bot  --user efbot  --no-service  --non-interactive
#  Getestet für Debian/Ubuntu/Raspberry Pi OS mit systemd.
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

APP="ef-untis-bot"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/${APP}"
SERVICE_USER="efbot"
NODE_MIN_MAJOR=22
NODE_MIN_MINOR=12
RECONFIGURE=0
RELINK=0
UNINSTALL=0
WITH_SERVICE=1
INTERACTIVE=1
[[ -t 0 ]] || INTERACTIVE=0

c_blue=$'\e[1;34m'; c_green=$'\e[1;32m'; c_yellow=$'\e[1;33m'; c_red=$'\e[1;31m'; c_off=$'\e[0m'
step() { printf '\n%s▶ %s%s\n' "$c_blue" "$*" "$c_off"; }
ok()   { printf '%s✔ %s%s\n' "$c_green" "$*" "$c_off"; }
warn() { printf '%s⚠ %s%s\n' "$c_yellow" "$*" "$c_off" >&2; }
die()  { printf '%s✖ %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }
trap 'die "Abbruch in Zeile $LINENO (Befehl: $BASH_COMMAND)"' ERR

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="$(realpath -m "$2")"; shift ;;
    --user) SERVICE_USER="$2"; shift ;;
    --reconfigure) RECONFIGURE=1 ;;
    --relink) RELINK=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --no-service) WITH_SERVICE=0 ;;
    --non-interactive|--yes) INTERACTIVE=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "Unbekannte Option: $1 (siehe --help)" ;;
  esac
  shift
done

UNIT_FILE="/etc/systemd/system/${APP}.service"
ENV_FILE="${INSTALL_DIR}/.env"

[[ $EUID -eq 0 ]] || die "Bitte mit sudo ausführen:  sudo ./install.sh"

# ── Hilfsfunktionen ─────────────────────────────────────────────────────────

# Befehl als Dienst-Benutzer im Installationsordner ausführen
run_as() {
  (cd "$INSTALL_DIR" && runuser -u "$SERVICE_USER" -- env HOME="$INSTALL_DIR" npm_config_cache="$INSTALL_DIR/.npm" \
    npm_config_update_notifier=false PATH="$PATH" "$@")
}

ask() { # ask "Frage" "Standard"
  local answer
  if [[ $INTERACTIVE -eq 0 ]]; then printf '%s' "${2:-}"; return; fi
  read -r -p "  $1${2:+ [$2]}: " answer
  printf '%s' "${answer:-${2:-}}"
}

ask_secret() { # Eingabe ohne Anzeige
  local answer
  if [[ $INTERACTIVE -eq 0 ]]; then printf '%s' "${2:-}"; return; fi
  read -r -s -p "  $1: " answer
  printf '\n' >&2
  printf '%s' "${answer:-${2:-}}"
}

confirm() { # confirm "Frage" j|n
  local def="${2:-j}" answer
  if [[ $INTERACTIVE -eq 0 ]]; then [[ "$def" == "j" ]]; return; fi
  read -r -p "  $1 [$( [[ $def == j ]] && echo 'J/n' || echo 'j/N')]: " answer
  answer="${answer:-$def}"
  [[ "${answer,,}" == j* || "${answer,,}" == y* ]]
}

env_get() { # Wert aus bestehender .env lesen (ohne Anführungszeichen)
  [[ -f "$ENV_FILE" ]] || return 0
  local line
  line="$(grep -E "^$1=" "$ENV_FILE" | tail -n1 || true)"
  line="${line#*=}"
  line="${line%\'}"; line="${line#\'}"; line="${line%\"}"; line="${line#\"}"
  printf '%s' "$line"
}

env_line() { # sicheres Quoting für dotenv (Passwörter mit Sonderzeichen)
  local key="$1" val="$2"
  if [[ "$val" != *"'"* ]]; then printf "%s='%s'\n" "$key" "$val"
  elif [[ "$val" != *'"'* ]]; then printf '%s="%s"\n' "$key" "$val"
  else printf '%s=`%s`\n' "$key" "$val"; fi
}

version_ok() { # Node-Version prüfen
  local v major minor
  v="$(node -p 'process.versions.node' 2>/dev/null)" || return 1
  major="${v%%.*}"; minor="$(cut -d. -f2 <<<"$v")"
  (( major > NODE_MIN_MAJOR || (major == NODE_MIN_MAJOR && minor >= NODE_MIN_MINOR) ))
}

service_active() { [[ $WITH_SERVICE -eq 1 ]] && systemctl is-active --quiet "$APP" 2>/dev/null; }

# ── Deinstallation ──────────────────────────────────────────────────────────

if [[ $UNINSTALL -eq 1 ]]; then
  step "Entferne Dienst ${APP}"
  systemctl disable --now "$APP" 2>/dev/null || true
  rm -f "$UNIT_FILE"
  systemctl daemon-reload 2>/dev/null || true
  ok "Dienst entfernt. Daten liegen weiterhin in ${INSTALL_DIR} (zum Löschen: sudo rm -rf ${INSTALL_DIR})."
  exit 0
fi

echo "${c_blue}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   EF-Untis-WhatsApp-Bot · Installation            ║"
echo "  ╚══════════════════════════════════════════════════╝${c_off}"
echo "  Ziel: ${INSTALL_DIR}   Benutzer: ${SERVICE_USER}"

# ── 1. Systempakete & Node.js ───────────────────────────────────────────────

step "Prüfe Systemvoraussetzungen"
if [[ $WITH_SERVICE -eq 1 ]] && ! command -v systemctl >/dev/null; then
  die "systemd wurde nicht gefunden. Ohne systemd: --no-service nutzen und den Bot z. B. mit pm2 starten (siehe README)."
fi

if command -v apt-get >/dev/null; then
  missing=()
  for pkg in ca-certificates curl python3 make g++; do
    command -v "$pkg" >/dev/null 2>&1 || [[ "$pkg" == ca-certificates && -d /etc/ssl/certs ]] || missing+=("$pkg")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    step "Installiere Pakete: ${missing[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" build-essential >/dev/null
  fi
fi

if ! version_ok; then
  warn "Node.js >= ${NODE_MIN_MAJOR}.${NODE_MIN_MINOR} wird benötigt (gefunden: $(node -v 2>/dev/null || echo keins))."
  if command -v apt-get >/dev/null && confirm "Node.js ${NODE_MIN_MAJOR} LTS jetzt über NodeSource installieren?" j; then
    tmp="$(mktemp)"
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MIN_MAJOR}.x" -o "$tmp"
    bash "$tmp" >/dev/null
    rm -f "$tmp"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs >/dev/null
  fi
  version_ok || die "Bitte Node.js ${NODE_MIN_MAJOR} LTS installieren: https://nodejs.org/de/download"
fi
NODE_BIN="$(command -v node)"
ok "Node.js $(node -v) (${NODE_BIN})"

# ── 2. Benutzer & Dateien ───────────────────────────────────────────────────

step "Richte Dienst-Benutzer und Programmordner ein"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$INSTALL_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  ok "Benutzer ${SERVICE_USER} angelegt (ohne Login)"
fi

was_running=0
if service_active; then was_running=1; systemctl stop "$APP"; fi

mkdir -p "$INSTALL_DIR"
if [[ "$SRC_DIR" != "$INSTALL_DIR" ]]; then
  # Alte Programmdateien entfernen – Daten, .env und Abhängigkeiten bleiben
  find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name data ! -name .env ! -name node_modules ! -name .npm -exec rm -rf {} +
  tar -C "$SRC_DIR" --exclude=./node_modules --exclude=./dist --exclude=./data --exclude=./.env --exclude=./.git --exclude=./.npm -cf - . \
    | tar -C "$INSTALL_DIR" -xf -
fi
mkdir -p "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/data"
ok "Programmdateien in ${INSTALL_DIR}"

# ── 3. Abhängigkeiten & Build ───────────────────────────────────────────────

step "Installiere Abhängigkeiten und baue den Bot (dauert beim ersten Mal 1–3 Minuten)"
run_as npm ci --no-audit --no-fund --loglevel=error
run_as npm run build --silent
run_as npm prune --omit=dev --no-audit --no-fund --loglevel=error
ok "Build erfolgreich"

# ── 4. Konfiguration (.env) ─────────────────────────────────────────────────

write_env() {
  step "Konfiguration (Zugangsdaten werden nur lokal in ${ENV_FILE} gespeichert, Rechte 600)"
  if [[ $INTERACTIVE -eq 0 ]]; then
    [[ -f "$ENV_FILE" ]] || die "Keine .env vorhanden. Interaktiv ausführen oder .env aus .env.example anlegen."
    return
  fi
  declare -A V
  echo "  Tipp: Server und Schulname findet die Untis-Schulsuche."
  local query result
  query="$(ask "Schule suchen (Enter = überspringen)" "")"
  if [[ -n "$query" ]]; then
    result="$(run_as node dist/scripts/find-school.js "$query" 2>&1 || true)"
    echo "$result" | sed 's/^/    /'
  fi
  local def_server def_school
  def_server="$(env_get UNTIS_SERVER)"; def_school="$(env_get UNTIS_SCHOOL)"
  [[ -z "$def_server" && -n "${result:-}" ]] && def_server="$(grep -m1 -oP 'UNTIS_SERVER=\K\S+' <<<"$result" || true)"
  [[ -z "$def_school" && -n "${result:-}" ]] && def_school="$(grep -m1 -oP 'UNTIS_SCHOOL=\K\S+' <<<"$result" || true)"
  V[UNTIS_SERVER]="$(ask "Untis-Server (z. B. nessa.webuntis.com)" "$def_server")"
  V[UNTIS_SCHOOL]="$(ask "Untis-Schulname" "$def_school")"

  echo "  Login-Art:  1) Untis-Benutzername + Passwort"
  echo "              2) Schul-Login (Microsoft/IServ): Link aus dem Untis-QR-Code (Profil → Freigaben)"
  echo "              3) Öffentlicher Zugang (nur falls von der Schule freigeschaltet)"
  local def_mode=1
  case "$(env_get UNTIS_AUTH)" in secret) def_mode=2 ;; anonymous) def_mode=3 ;; esac
  case "$(ask "Auswahl" "$def_mode")" in
    2) V[UNTIS_AUTH]=secret
       V[UNTIS_USERNAME]="$(ask "Untis-Benutzername (leer = aus dem Link)" "$(env_get UNTIS_USERNAME)")"
       V[UNTIS_SECRET]="$(ask_secret "QR-Link oder Schlüssel einfügen (Enter = unverändert)" "$(env_get UNTIS_SECRET)")"
       V[UNTIS_PASSWORD]="" ;;
    3) V[UNTIS_AUTH]=anonymous; V[UNTIS_USERNAME]=""; V[UNTIS_PASSWORD]=""; V[UNTIS_SECRET]="" ;;
    *) V[UNTIS_AUTH]=password
       V[UNTIS_USERNAME]="$(ask "Untis-Benutzername" "$(env_get UNTIS_USERNAME)")"
       V[UNTIS_PASSWORD]="$(ask_secret "Untis-Passwort (Enter = unverändert)" "$(env_get UNTIS_PASSWORD)")"
       V[UNTIS_SECRET]="" ;;
  esac
  V[UNTIS_CLASSES]="$(ask "Überwachte Klasse(n)" "$(env_get UNTIS_CLASSES || true)")"
  [[ -n "${V[UNTIS_CLASSES]}" ]] || V[UNTIS_CLASSES]="EF"
  echo "  WhatsApp:   1) Eigene Nummer – der Bot läuft als verknüpftes Gerät auf deinem WhatsApp (kostenlos, keine 2. SIM)"
  echo "              2) Separate Bot-Nummer (zweite SIM/Festnetz-WhatsApp-Business)"
  local def_wa=1
  [[ "$(env_get WHATSAPP_OWN_NUMBER)" == "false" ]] && def_wa=2
  if [[ "$(ask "Auswahl" "$def_wa")" == "2" ]]; then
    V[WHATSAPP_OWN_NUMBER]=false
    V[ADMIN_NUMBERS]="$(ask "Deine Handynummer für Fehlermeldungen, z. B. 4915112345678 (optional)" "$(env_get ADMIN_NUMBERS)")"
  else
    V[WHATSAPP_OWN_NUMBER]=true
    V[ADMIN_NUMBERS]=""   # Fehlermeldungen landen in "Nachricht an mich selbst"
  fi
  V[WHATSAPP_GROUP_INVITE]="$(ask "Einladungslink der Klassengruppe (optional, sonst Auswahl aus deinen Gruppen)" "$(env_get WHATSAPP_GROUP_INVITE)")"
  V[WHATSAPP_PAIRING_NUMBER]="$(ask "Nummer für Kopplungscode statt QR, z. B. 4915112345678 (optional, leer = QR-Code)" "$(env_get WHATSAPP_PAIRING_NUMBER)")"

  # .env aus der Vorlage erzeugen; vorhandene Zusatz-Einstellungen bleiben erhalten
  local tmp key line
  tmp="$(mktemp "${INSTALL_DIR}/.env.XXXXXX")"
  chmod 600 "$tmp"
  while IFS= read -r line || [[ -n "$line" ]]; do
    key="${line%%=*}"
    if [[ "$line" =~ ^[A-Z_]+= ]]; then
      if [[ -v "V[$key]" ]]; then env_line "$key" "${V[$key]}"
      elif [[ -f "$ENV_FILE" ]] && grep -qE "^${key}=" "$ENV_FILE"; then grep -E "^${key}=" "$ENV_FILE" | tail -n1
      else printf '%s\n' "$line"; fi
    else
      printf '%s\n' "$line"
    fi
  done < "${INSTALL_DIR}/.env.example" > "$tmp"
  mv "$tmp" "$ENV_FILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "Konfiguration gespeichert"
}

if [[ ! -f "$ENV_FILE" || $RECONFIGURE -eq 1 ]]; then write_env; fi
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"

# ── 5. Untis-Zugang testen ──────────────────────────────────────────────────

step "Teste den Untis-Zugang"
until run_as node dist/scripts/check-untis.js; do
  if [[ $INTERACTIVE -eq 1 ]] && confirm "Zugangsdaten neu eingeben?" j; then
    write_env
  else
    warn "Untis-Test fehlgeschlagen – der Bot versucht es im Betrieb automatisch weiter. Korrigieren: sudo ./install.sh --reconfigure"
    break
  fi
done

# ── 6. WhatsApp koppeln ─────────────────────────────────────────────────────

if [[ ! -f "$INSTALL_DIR/data/auth/creds.json" || $RELINK -eq 1 ]]; then
  step "WhatsApp verknüpfen (QR-Code mit dem Handy scannen)"
  if [[ "$(env_get WHATSAPP_OWN_NUMBER)" == "false" ]]; then
    echo "  Mit dem Handy der BOT-Nummer scannen. Die Nummer muss in der Klassengruppe sein (oder Einladungslink)."
  else
    echo "  Mit DEINEM Handy scannen: WhatsApp → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen."
    echo "  Der Bot läuft dann wie WhatsApp Web auf deinem Konto; seine Nachrichten beginnen mit 🤖."
  fi
  if [[ $INTERACTIVE -eq 1 ]]; then
    if service_active; then systemctl stop "$APP"; fi
    run_as node dist/setup.js || warn "WhatsApp-Einrichtung nicht abgeschlossen – später: sudo ./install.sh --relink"
  else
    warn "Nicht interaktiv: WhatsApp später koppeln mit  sudo ./install.sh --relink  (oder QR-Code im Log: journalctl -fu ${APP})"
  fi
fi

# ── 7. systemd-Dienst ───────────────────────────────────────────────────────

if [[ $WITH_SERVICE -eq 1 ]]; then
  step "Richte Autostart (systemd) ein"
  sed -e "s#@INSTALL_DIR@#${INSTALL_DIR}#g" -e "s#@SERVICE_USER@#${SERVICE_USER}#g" -e "s#@NODE_BIN@#${NODE_BIN}#g" \
    "$INSTALL_DIR/deploy/${APP}.service" > "$UNIT_FILE"
  chmod 644 "$UNIT_FILE"
  systemctl daemon-reload
  systemctl enable "$APP" >/dev/null 2>&1
  systemctl restart "$APP"
  sleep 3
  if systemctl is-active --quiet "$APP"; then ok "Dienst läuft und startet nach jedem Neustart automatisch"
  else warn "Dienst läuft nicht – Logs ansehen: journalctl -u ${APP} -n 50"; fi
else
  if [[ $was_running -eq 1 ]]; then systemctl start "$APP"; fi
  ok "Ohne systemd-Dienst installiert. Start: cd ${INSTALL_DIR} && sudo -u ${SERVICE_USER} node dist/index.js"
fi

cat <<EOF

${c_green}Fertig!${c_off} Nützliche Befehle:
  Status:          systemctl status ${APP}
  Live-Log:        journalctl -fu ${APP}
  Neustart:        sudo systemctl restart ${APP}
  Einstellungen:   sudo ./install.sh --reconfigure
  WhatsApp neu:    sudo ./install.sh --relink
  Update:          git pull && sudo ./install.sh
  In der Gruppe:   /hilfe  /status  /heute  /morgen  /woche  /entfall  /kurse
EOF
