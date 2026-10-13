# XRZ Trading Bot 🤖📈

Ein lokaler KI-Trading-Assistent für den Mac. Er analysiert Märkte mit 7 Strategien,
testet sie laufend per Walk-Forward-Backtest, handelt auf Zuruf im **Paper-Trading** (Spielgeld)
oder **Live** (Echtgeld), lässt sich komplett per **Telegram** steuern, öffnet **TradingView**
auf deinem Mac und **lernt mit jedem Trade und allem, was du ihm beibringst**, dazu.

> ⚠️ **Ehrlich vorweg:** Kein Bot und keine KI kann „immer sicheres Geld“ verdienen. Wer das
> verspricht, lügt. Dieser Bot ist auf **Risikokontrolle** gebaut: Er handelt nur Märkte, in denen
> seine Strategien im Backtest einen Vorteil gezeigt haben, begrenzt jeden Verlust per Stop-Loss
> und hat mehrere Notbremsen. Verluste sind trotzdem möglich. **Erst wochenlang Paper-Trading,
> dann live mit kleinen Beträgen.**

---

## Was der Bot kann

| Bereich | Funktionen |
|---|---|
| 📊 **Analyse** | 20+ Indikatoren (RSI, MACD, ATR, ADX, Bollinger, Supertrend, Stochastik, VWAP, OBV …), Marktphasen-Erkennung, Unterstützungen/Widerstände, übergeordneter Trend, Fear-&-Greed-Index, News-Schlagzeilen |
| 🧠 **Strategien** | 7 Strategien (EMA-Trend, MACD-Momentum, RSI-Mean-Reversion, Bollinger-Squeeze, Donchian/Turtle, Supertrend, Trend-Pullback) → gewichteter Ensemble-Score |
| 🧪 **Backtests** | Walk-Forward-Optimierung (Parameter auf alten Daten wählen, auf neuen prüfen) mit Gebühren, Slippage, Stop-Loss, Take-Profit und Trailing-Stop – keine Zukunftsdaten |
| 💼 **Trading** | Paper-Trading mit echten Kursen · Live-Trading über 100+ Börsen (ccxt: Binance, Kraken, Bybit, OKX, Coinbase …) · Aktien/ETFs/Forex als Daten über yfinance |
| 🛡 **Risiko** | Positionsgröße nach Risiko, ATR-Stops, Break-Even, Trailing-Stop, Tagesverlust-Limit, Drawdown-Notbremse, Abkühlphase nach Verlust, Max.-Betrag pro Live-Order, Stops zusätzlich an der Börse |
| 🤖 **KI** | Lokal & kostenlos über **Ollama** (z. B. Qwen3) · optional **Claude** (Anthropic) oder **OpenAI** per API-Key für deutlich mehr Intelligenz · automatisches Ausweichen auf die lokale KI |
| 📚 **Lernen** | Wissensdatenbank mit Grundwissen · `/lerne` · Dokumente (PDF/TXT) und Webseiten einlesen · nach jedem Trade eine Lektion · Strategien werden nach echten Ergebnissen höher/niedriger gewichtet |
| 💬 **Telegram** | Alle Befehle auf Deutsch, freier Chat („Wie sieht Bitcoin aus?“, „Starte Paper-Trading“), Trade-Meldungen, Tagesbericht, Fotos von Charts analysieren |
| 🖥 **Mac** | Dashboard im Browser, TradingView öffnen (Browser oder App), Screenshots + KI-Chartanalyse, Mitteilungen, Sprachausgabe, Autostart |
| 📡 **TradingView** | Webhook für TradingView-Alerts + fertiges Pine-Script |

---

## Installation (MacBook)

1. **Projekt herunterladen** – auf GitHub „Code → Download ZIP“ und entpacken, oder im Terminal:
   ```bash
   git clone https://github.com/louisttacc-hue/XRZTradingBot.git
   cd XRZTradingBot
   ```
2. **Installieren** (installiert Homebrew, Python 3.12, alle Pakete, Ollama und ein passendes KI-Modell je nach Arbeitsspeicher):
   ```bash
   bash install_mac.sh
   ```
3. **Starten** – Doppelklick auf **„XRZ Trading Bot“** auf dem Schreibtisch
   (oder `./start_mac.command`). Das Dashboard öffnet sich unter <http://127.0.0.1:8765>.

> Ohne Internet/Börse ausprobieren: `./start_mac.command --demo` (künstliche Kurse).

### Erste Schritte

1. **Telegram:** In Telegram `@BotFather` öffnen → `/newbot` → Namen vergeben → Token kopieren.
   Im Dashboard unter **Einstellungen → API-Keys & Tokens** einfügen und speichern.
2. **Koppeln:** Deinem neuen Bot in Telegram `/start 123456` schicken – den Code zeigt das
   Dashboard (Übersicht/Einrichtung). Danach gehorcht der Bot **nur noch deinem Chat**.
3. **Optional schlauer machen:** Anthropic-API-Key (Claude) oder OpenAI-Key eintragen.
4. **Paper-Trading starten:** `/paper` in Telegram oder Button im Dashboard.
5. Beobachten, Fragen stellen, Wissen beibringen – und erst nach Wochen mit guten
   Ergebnissen über Live-Trading nachdenken.

### macOS-Berechtigungen
*Systemeinstellungen → Datenschutz & Sicherheit:*
- **Bildschirmaufnahme** → Terminal erlauben (für `/chart` und `/screenshot`)
- **Bedienungshilfen** → Terminal erlauben (nur wenn TradingView in der **App** statt im Browser gesteuert werden soll)

### Autostart (optional)
```bash
bash scripts/autostart_mac.sh an    # startet den Bot bei jeder Anmeldung, auch nach Abstürzen
bash scripts/autostart_mac.sh aus
```
Der Bot hält den Mac mit `caffeinate` wach – er muss laufen, um Stops zu überwachen.

---

## Die KI: lokal oder Cloud

| Einstellung `ai.provider` | Verhalten |
|---|---|
| `auto` (Standard) | Claude, wenn ein Anthropic-Key eingetragen ist → sonst OpenAI, wenn Key → sonst lokale KI. Fällt ein Cloud-Dienst aus, springt automatisch die lokale KI ein. |
| `ollama` | Immer lokal (kostenlos, privat, offline) |
| `anthropic` / `openai` | Bevorzugt diesen Anbieter |

- **Lokal:** Ollama mit `qwen3:4b` (< 16 GB RAM), `qwen3:8b` (16 GB) oder `qwen3:14b` (≥ 32 GB).
  Für Chart-Bilder optional ein Bild-Modell eintragen, z. B. `qwen2.5vl:7b` (`ollama pull qwen2.5vl:7b`).
- **Claude:** Standardmodell `claude-opus-5-5` mit einstellbarem Denk-Aufwand (`low` … `max`, Standard `medium`).
  Günstiger: `claude-sonnet-5-5` oder `claude-haiku-5-5` eintragen. Bei Sicherheitsablehnungen
  springt die Anthropic-API serverseitig automatisch auf ein Ersatzmodell („fallbacks“) – das ist
  eingeschaltet. API-Nutzung kostet Geld (Abrechnung bei Anthropic/OpenAI).
- **OpenAI-kompatibel:** auch OpenRouter, Groq, DeepSeek, LM Studio usw. über `ai.openai_base_url`.

Die KI **entscheidet nicht allein**: Sie prüft jeden Trade-Kandidaten und darf ihn **ablehnen
oder verkleinern, aber nie vergrößern**. Das Risiko-Management hat immer das letzte Wort.

---

## Wie eine Entscheidung entsteht

```
Kursdaten (1h + 4h)  ──►  Indikatoren · Marktphase · Unterstützung/Widerstand
                               │
7 Strategien ──► Gewichte aus Walk-Forward-Backtest × Lernfaktor aus echten Trades
                               │
               Ensemble-Score −1 … +1  (+ TradingView-Alert, gefiltert durch 4h-Trend)
                               │
         Score ≥ Schwelle & genug Strategien einig & Kurs nicht überdehnt?
                               │
             KI-Prüfung (Stimmung, Wissen, Lektionen) → ok / ablehnen / kleiner
                               │
  Risiko-Management: Positionsgröße, Limits, Notbremsen → Order → Stop & Ziel
                               │
          Trade geschlossen → Ergebnis → Lektion in die Wissensdatenbank
```

Märkte ohne nachweisbaren Vorteil im Backtest werden **nur analysiert, nie gehandelt**.

---

## Live-Trading (Echtgeld)

Mehrere Sicherheitsstufen – Live-Trading kann **nicht aus Versehen** starten:

1. Börsen-API-Key **ohne Auszahlungsrechte** erstellen (am besten mit IP-Beschränkung) und im Dashboard eintragen.
2. Optional erst mit dem **Testnet** der Börse üben (`exchange.sandbox`).
3. Im Dashboard **„Live-Trading freischalten“** anhaken (bewusste Entscheidung am Mac).
4. In Telegram `/live` → der Bot zeigt Kontostand und Limits → mit `/bestaetigen CODE` innerhalb von 5 Minuten bestätigen.

Schutzmechanismen im Live-Modus: nur Spot-Long (kein Hebel, keine Shorts) · harte Obergrenze pro Order
(`risk.max_live_order_usd`, Standard 100) · Tagesverlust-Limit · Drawdown-Notbremse · Stop-Loss zusätzlich
an der Börse · nach einem Neustart pausieren neue Einstiege, bis du `/weiter` sendest · `/panik` schließt alles.

---

## TradingView

**Charts auf dem Mac:** `/chart BTC` öffnet TradingView (Browser oder App), macht einen Screenshot,
schickt ihn dir und lässt die KI den Chart zusammen mit der berechneten Analyse bewerten.

**Alerts an den Bot schicken (Webhook):**
1. Dashboard → Einstellungen: *TradingView-Webhook aktiv* + *Passwort für TradingView-Webhooks* setzen → Bot neu starten.
2. Port ins Internet freigeben, z. B. `brew install cloudflared` und `cloudflared tunnel --url http://localhost:8766`.
3. In TradingView das Pine-Script [`tradingview/xrz_signale.pine`](tradingview/xrz_signale.pine) zum Chart hinzufügen
   (Passphrase eintragen) und einen Alarm mit „Any alert() function call“ und der Webhook-URL
   `https://DEINE-TUNNEL-ADRESSE/webhook/tradingview` anlegen.
4. Eigene Alerts gehen auch: Nachricht `{"passphrase":"…","symbol":"{{ticker}}","action":"buy"}` (`buy` / `sell` / `close`).

`tradingview.alert_mode`: **signal** (Standard) = der Alert fließt als starkes Signal in die Gesamtentscheidung ein ·
**execute** = der Bot handelt den Alert direkt (immer mit Risiko-Management und KI-Prüfung).

---

## Telegram-Befehle

| Befehl | Was passiert |
|---|---|
| `/status` | Konto, offene Positionen, letzte Signale |
| `/markt` | Alle Märkte der Watchlist + Stimmung |
| `/analyse BTC` · `/analyse ETH 4h` | Komplette Analyse |
| `/backtest SOL` | Walk-Forward-Backtest aller Strategien |
| `/chart BTC` · `/screenshot` | TradingView am Mac öffnen + Screenshot + KI-Analyse |
| `/paper` · `/stop` · `/pause` · `/weiter` | Paper-Trading starten / ausschalten / pausieren / fortsetzen |
| `/live` → `/bestaetigen CODE` | Live-Trading starten |
| `/panik` | Alle Positionen sofort schließen |
| `/positionen` · `/trades` · `/schliessen BTC` | Positionen & Historie |
| `/lerne <Text>` · `/wissen <Suche>` · `/url <Link>` | Wissen beibringen, durchsuchen, Webseite lernen |
| 📎 Dokument · 🖼 Foto | PDF/TXT wird gelernt · Chartbild wird analysiert |
| `/watchlist` · `/add SOL` · `/remove XRP` | Watchlist verwalten |
| `/einstellungen` · `/set risk.risk_per_trade_pct 0.5` | Einstellungen ansehen/ändern |
| `/bericht` · `/ki` · `/neu` | Tagesbericht · KI-Status · Chatverlauf leeren |

Oder einfach schreiben: *„Wie sieht der Markt aus?“*, *„Merk dir: Ich will keine Trades vor FED-Terminen“*,
*„Warum hast du ETH gekauft?“*, *„Starte Paper-Trading“*.

---

## Sicherheit

- Dashboard nur auf `127.0.0.1` erreichbar, mit internem Zugriffstoken und Host-Prüfung (Schutz gegen fremde Webseiten).
- API-Keys liegen im **macOS-Schlüsselbund** (sonst in `secrets.json` mit Rechten 600) – nie im Projektordner, nie in Git.
- Telegram reagiert nur auf gekoppelte Chats; falsche Kopplungscodes werden nach 5 Versuchen ungültig.
- **Keine beliebigen Shell-Befehle per Telegram:** Die Mac-Steuerung ist auf sichere Aktionen begrenzt
  (TradingView, Screenshots, Mitteilungen). Wer sonst deinen Telegram-Account oder Bot-Token erbeutet, hätte deinen ganzen Mac.
- Webhook läuft auf eigenem Port mit Passphrase und Rate-Limit und kennt keine Dashboard-Funktionen.

---

## Daten & Kommandozeile

Alle Daten (Einstellungen, Datenbank mit Trades und Wissen, Logs, Screenshots) liegen in
`~/Library/Application Support/XRZTradingBot` (`python -m xrz where` zeigt den Pfad).

```bash
.venv/bin/python -m xrz                     # Bot starten
.venv/bin/python -m xrz --demo              # mit künstlichen Kursen
.venv/bin/python -m xrz analyze BTC 4h      # einmalige Analyse im Terminal
.venv/bin/python -m xrz backtest ETH        # Walk-Forward-Backtest
```

## Projektstruktur

```
xrz/
├── app.py               Start, verbindet alles
├── config.py            Einstellungen + Geheimnisse (Schlüsselbund)
├── storage.py           SQLite: Trades, Kontoverlauf, Wissen, Chat
├── market/              Kursdaten (ccxt, yfinance, Demo) + Stimmung
├── analysis/            Indikatoren, Marktphase, Unterstützung/Widerstand
├── strategies/          7 Strategien
├── backtest/            Backtester + Walk-Forward-Optimierung
├── trading/             Analyzer (Ensemble), Risiko, Paper/Live-Broker, Engine
├── ai/                  KI-Anbieter, Gehirn mit Werkzeugen, Wissensdatenbank
├── interfaces/          Telegram-Bot
├── mac/                 Mac-Steuerung (TradingView, Screenshots, Mitteilungen)
└── web/                 Dashboard + TradingView-Webhook
tradingview/xrz_signale.pine   Pine-Script für Alerts
install_mac.sh · start_mac.command · scripts/autostart_mac.sh
```

## Entwicklung & Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Häufige Probleme

- **„KI nicht erreichbar“** → Ollama-App öffnen bzw. `brew services start ollama`; Modell fehlt → `ollama pull qwen3:8b`.
- **Screenshot schwarz/leer** → Bildschirmaufnahme-Berechtigung für Terminal erteilen, Terminal neu starten.
- **Börse liefert keine Daten** (z. B. Binance in deinem Land gesperrt) → unter Einstellungen eine andere Börse wählen (`kraken`, `bybit`, `okx`, `coinbase` …) und die Watchlist anpassen (z. B. `BTC/EUR`).
- **Aktien** → Symbol ohne Schrägstrich in die Watchlist (`AAPL`, `SPY`) – Daten via yfinance (verzögert), nur Paper-Trading.

## Windows

Der Code ist plattformunabhängig geschrieben (Mac-spezifische Funktionen fallen auf Windows weich zurück).
Installations- und Startskripte für Windows folgen in einem nächsten Schritt.

---

*Keine Anlageberatung. Nutzung auf eigenes Risiko.*
