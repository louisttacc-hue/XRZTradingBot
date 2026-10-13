# XRZ Vincent 🟣

**Vincent** ist eine kleine KI, die als animierte Figur am Rand deines MacBook-Bildschirms lebt.
Du sprichst einfach mit ihr – **„Hey Vincent, wie sieht Bitcoin aus?“** – und sie antwortet mit
realistischer Stimme. Vincent analysiert den Markt mit über 35 Strategien, merkt sich alles, was du
ihm erzählst, wird mit jedem Gespräch und jedem Trade schlauer und **bedient für dich die
TradingView-App**: Paper Trading im TradingView-Paper-Konto oder – nur nach ausdrücklicher
Freigabe – mit Echtgeld über deinen in TradingView verbundenen Broker.

> ⚠️ **Ehrlich vorweg:** Keine KI kann „immer sicheres Geld“ verdienen. Vincent ist auf
> **Risikokontrolle** gebaut: Er handelt nur nach deinem „Ja“, prüft vor jedem Klick, ob in TradingView
> wirklich das richtige Konto (Paper oder Echtgeld) aktiv ist, setzt Stop-Loss und Take-Profit direkt
> im Order-Ticket und hat mehrere Notbremsen. Verluste sind trotzdem möglich. **Erst wochenlang Paper
> Trading, dann – wenn überhaupt – klein mit Echtgeld.**

---

## Was Vincent kann

| | |
|---|---|
| 🟣 **Kleine KI am Bildschirmrand** | Immer im Vordergrund, auf allen Schreibtischen, stört nicht beim Klicken (durchklickbar). Reagiert sichtbar: hört zu, denkt, spricht (Mund/Wellen bewegen sich mit der Stimme). |
| 🎨 **Skins** | Nebula, Hologramm, Neon, Glas, Roboter – oder **dein eigenes Bild**. Farbe, Größe und Seite (links/rechts) frei wählbar. |
| 🗣 **Reden wie mit einem Menschen** | Wake-Word **„Hey Vincent“** (offline), danach einfach weiterreden; Nachfragen ohne erneutes „Hey Vincent“. Alternativ **⌥ Leertaste**. |
| 🔊 **Realistische Stimme** | ElevenLabs (sehr realistisch, mit API-Key) → OpenAI → Piper (offline) → macOS-Stimme. |
| 🧠 **Gedächtnis, das wächst** | Merkt sich Fakten, Vorlieben und Regeln aus jedem Gespräch, lernt aus jedem Trade. Nachts „schläft“ er: fasst Ähnliches zusammen, vergisst Unwichtiges – so bleibt das Gedächtnis schlau statt voll. Alles einsehbar und editierbar. |
| 📊 **Marktanalyse** | 35+ Strategien (Trend, Momentum, Mean-Reversion, Ausbrüche, Volumen, Kerzenmuster), Marktphasen, übergeordneter Trend, Fear & Greed, News. Walk-Forward-Backtests entscheiden, welche Strategie in welchem Markt zählt. |
| 💼 **Handeln in TradingView** | Vincent öffnet das Order-Ticket in der TradingView-App, wählt Kauf/Verkauf, Menge, Stop-Loss und Take-Profit, kontrolliert alles und klickt erst nach deinem „Ja“. |
| 🖥 **Zugriff auf deinen Mac** | Programme und Webseiten öffnen, auf den Bildschirm schauen („Was siehst du gerade?“) und – mit Claude-API-Key – den Mac mit Maus und Tastatur bedienen. |
| 💬 **Telegram (optional)** | Von unterwegs fragen, Vorschläge mit `/ja` oder `/nein` beantworten. |

---

## Installation (MacBook, macOS 13 oder neuer)

Voraussetzung: **TradingView Desktop** ist installiert (tradingview.com/desktop) und du bist eingeloggt.

```bash
git clone -b claude/inspiring-feynman-ey07gc https://github.com/louisttacc-hue/XRZTradingBot.git
cd XRZTradingBot
bash install_mac.sh
```

Das Skript installiert alles Nötige (Homebrew, Python 3.12, Node.js, Ollama + lokales KI-Modell
passend zu deinem Arbeitsspeicher, Spracherkennung, Offline-Stimme), baut die App und legt
**„XRZ Vincent“** in den Programme-Ordner. Danach startest du Vincent wie jede andere App.
Autostart bei der Anmeldung: `bash scripts/autostart_mac.sh an`.

### Beim ersten Start
1. **Mikrofon erlauben** – damit Vincent „Hey Vincent“ hört.
2. **Zentrale öffnen** (Klick auf Vincent → Zentrale, oder Dock-Symbol).
3. **Einstellungen → API-Keys (optional, aber empfohlen):**
   - **ElevenLabs** → sehr realistische deutsche Stimme
   - **Anthropic (Claude)** → deutlich schlauer + Vincent kann den Mac bedienen
   - **OpenAI** → Alternative für KI und Stimme
   Ohne Keys läuft alles lokal und kostenlos (Ollama + Piper-Stimme) – nur weniger schlau und weniger natürlich.
4. **TradingView → „Verbinden“:** Vincent startet die TradingView-App einmal mit einem lokalen
   Steuer-Port neu (er fragt vorher). Danach siehst du, welches Konto aktiv ist: **Paper Trading** oder **Echtgeld**.
5. **Für Mac-Steuerung:** Systemeinstellungen → Datenschutz & Sicherheit → **Bedienungshilfen** und
   **Bildschirmaufnahme** → „XRZ Vincent“ erlauben.

---

## So sprichst du mit Vincent

- „**Hey Vincent**, wie sieht der Markt heute aus?“
- „Analysier mal Solana auf dem 4-Stunden-Chart.“
- „**Starte Paper Trading.**“ → Vincent analysiert laufend und meldet sich mit Vorschlägen:
  *„Ich würde Bitcoin kaufen, im Paper-Konto. Stop-Loss bei 63.200, Ziel bei 67.900. Soll ich?“*
  → „**Ja**“ / „**Nein**“
- „Du darfst im Paper-Modus selbst handeln.“ → Vincent handelt im Paper-Konto ohne Rückfrage (nie bei Echtgeld).
- „**Merk dir:** Ich will keine Trades vor FED-Terminen.“ → wird zur festen Regel, die Vincent bei jeder Trade-Prüfung beachtet.
- „Was weißt du über mich?“ · „Vergiss die Sache mit …“
- „Öffne Safari.“ · „Was siehst du gerade auf meinem Bildschirm?“
- „**Stopp**“ → keine neuen Trades, Vincent hört auf zu reden. „**Panik**“ → alle Positionen in TradingView schließen.

---

## Modi & Sicherheit

| Modus | Was passiert |
|---|---|
| **Aus** | Nur Gespräche, keine Vorschläge. |
| **Beobachten** | Vincent analysiert und nennt Ideen, führt aber nichts aus. |
| **Paper** | Orders im **TradingView-Paper-Trading-Konto** – nach deinem „Ja“ (oder automatisch, wenn du es erlaubst). |
| **Echtgeld** | Orders über deinen in TradingView verbundenen Broker. Freischaltung in der Zentrale + Bestätigungscode + **jeder Trade** braucht „Ja, mit Echtgeld“. |

Vor **jeder** Order prüft Vincent in TradingView (über die interne Broker-Schnittstelle und die Oberfläche),
ob das aktive Konto zum Modus passt. Bei Paper muss es das echte „Paper Trading“-Konto sein – sonst bricht er ab.
Nach dem Ausfüllen des Order-Tickets liest er alles zurück (Seite, Menge, Symbol, Stop, Ziel) und klickt nur,
wenn alles stimmt. Keine Order wird je automatisch wiederholt (keine Doppel-Orders).

Weitere Schutzschichten: Positionsgröße nach Risiko (Standard 1 % pro Trade), max. Betrag pro Echtgeld-Order,
Tagesverlust-Limit, Drawdown-Notbremse, Pause nach Verlusten, die KI darf Trades nur ablehnen oder verkleinern.

> **Hinweis zu TradingView:** Die Steuerung nutzt die Oberfläche und eine interne, nicht offiziell
> dokumentierte Schnittstelle der TradingView-App. Nach TradingView-Updates kann sie ausfallen – Vincent
> erkennt das (Selbsttest) und stoppt dann, statt falsch zu klicken. TradingViews Nutzungsbedingungen
> erlauben automatisierte Orders nicht ausdrücklich; nutze die Funktion verantwortungsvoll und auf eigenes Risiko.

---

## Die Zentrale

Übersicht (Modus, Konto, Vorschläge, Positionen, Signale) · Markt (Chart, Analyse, Backtest) ·
Strategien (alle 35+ mit Erklärung) · Gedächtnis (Kernprofil, Erinnerungen bearbeiten/anheften/löschen,
„Jetzt verdichten“) · Stimme & Aussehen (Skins, Farbe, Stimmen probehören) · TradingView (Verbindung,
Konto-Erkennung, Test-Order ohne Absenden) · Einstellungen · Protokoll.

## Telegram (optional)

Bot bei `@BotFather` anlegen, Token in der Zentrale eintragen, deinem Bot `/start <Code>` schicken
(Code steht in der Zentrale). Befehle: `/status`, `/markt`, `/analyse BTC`, `/paper`, `/beobachten`,
`/ja`, `/nein`, `/auto`, `/fragen`, `/pause`, `/weiter`, `/stop`, `/panik`, `/lerne …`, `/wissen …`, `/hilfe`.

## Daten & Datenschutz

Alles bleibt auf deinem Mac: `~/Library/Application Support/XRZ Vincent/` (Einstellungen, Datenbank mit
Gedächtnis, Trades und Gesprächen, Logs, Modelle). API-Keys liegen im macOS-Schlüsselbund. Die lokale
Schnittstelle ist nur auf diesem Mac erreichbar und durch einen Zufalls-Schlüssel pro Start geschützt.
Cloud-Dienste (Claude, OpenAI, ElevenLabs) werden nur genutzt, wenn du einen Key einträgst.

## Für Entwickler

```
app/        Electron-App (Overlay-Avatar, Zentrale, Tray, Kürzel, startet das Backend)
xrz/        Python-Backend: server/ (API + WebSocket), trading/ (Engine, Risiko, Analyse),
            strategies/ (35+), backtest/, tradingview/ (App-Steuerung), voice/ (Wake-Word, Spracherkennung,
            Stimme), memory/ (Gedächtnis), ai/ (KI-Anbieter, Vincents Gehirn), mac/ (Mac-Steuerung),
            interfaces/ (Telegram)
```

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                 # Backend-Tests
cd app && npm install && npm test          # App-Tests
.venv/bin/python -m xrz --demo serve       # Backend mit künstlichen Kursen
cd app && VINCENT_DEMO=1 npm start         # App im Entwicklungsmodus
```

*Keine Anlageberatung. Nutzung auf eigenes Risiko.*
