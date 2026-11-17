# EF-Untis-WhatsApp-Bot · Gymnasium Jüchen

Ein kostenloser WhatsApp-Bot für die **EF** – er läuft dauerhaft auf einem eigenen Server,
überwacht WebUntis **alle 5 Minuten** und meldet in der Klassengruppe automatisch:

- ❌ **Entfall**
- 🔄 **Vertretungen** (anderer Lehrer)
- 🚪 **Raumwechsel**
- ⚠️ Verlegungen, Zusatz- und Sonderstunden
- ➕➖↪️✏️ **Stundenplanänderungen** (neue/entfernte/verschobene/geänderte Stunden, auch ohne Untis-Markierung)
- ✅ **Rücknahmen** („findet doch statt“)

Getrackt werden **alle Kurse der EF** (Klassen-Stundenplan), nicht nur die eines einzelnen Schülers.
**Keine zweite SIM nötig:** Standardmäßig läuft der Bot als verknüpftes Gerät auf deiner eigenen
WhatsApp-Nummer (wie WhatsApp Web) – komplett kostenlos. Seine Nachrichten beginnen mit 🤖.
Jede Änderung wird **genau einmal** gemeldet – auch über Neustarts, Verbindungsabbrüche und
Untis-Neuimporte hinweg.

```
🤖 🔔 *Vertretungsplan EF* – 4 Änderungen

📅 *Do, 19.11.*
❌ *1.–2. Std* · Deutsch (D-GK1) · MÜL — *Entfall*
     _„Aufgaben auf Moodle“_
🔄 *3. Std* · Mathematik (M-GK2) · *Vertretung:* NEU statt ~SCH~ · R204
🚪 *5. Std* · Englisch (E-GK1) · KRA · *Raum:* A001 statt ~B105~

📅 *Fr, 20.11.*
✅ *4. Std* · Biologie (BI-GK1) · WEB · NW2 — *findet doch statt*
```
*(echte Ausgabe des Bots; WhatsApp zeigt `*…*` fett und `~…~` durchgestrichen an)*

## Befehle in der Gruppe

| Befehl | Antwort |
|---|---|
| `/heute` | Plan von heute – alle EF-Kurse, Doppelstunden zusammengefasst, Änderungen markiert |
| `/morgen` | Plan für morgen; am Wochenende/in Ferien automatisch der nächste Schultag |
| `/woche` | Schulwoche (Mo–Fr) mit allen Änderungen je Tag |
| `/entfall` | Alle bekannten Entfälle der nächsten 14 Tage |
| `/kurse` | Alle Kurse der EF mit Lehrkraft und Wochenstunden |
| `/status` | WhatsApp-Verbindung, letzter Untis-Abruf, Fehler, Statistik, Laufzeit |
| `/hilfe` | Übersicht der Befehle |

Befehle beantwortet der Bot aus dem zuletzt gespeicherten Untis-Stand (schnell, keine Extra-Last
für Untis). Derselbe Befehl wird pro Chat höchstens alle 30 Sekunden beantwortet (Spam-Schutz).
Auf deiner eigenen Nummer funktionieren die Befehle auch, wenn **du** sie schreibst – und privat im Chat
**„Nachricht an mich selbst“** (dort landen auch Fehlermeldungen des Bots).

---

## ⚠️ Bevor du startest: Erlaubnis, Datenschutz, Sperrrisiko

**Untis-Zugang – nur berechtigt:** Der Bot liest ausschließlich, was der eingetragene Untis-Account
ohnehin in WebUntis/Untis Mobile sehen darf (normaler Login, offizielle Schnittstelle der Untis-Apps,
kein Scraping, keine fremden Konten). Nutze deinen **eigenen** Schüler-Account oder den öffentlichen
Zugang der Schule. Im Zweifel kurz bei der Schule/Stufenleitung nachfragen, ob ein solcher Bot okay ist.
Darf der Account den EF-Klassenplan nicht sehen, meldet der Bot das klar, statt es zu umgehen.

**Datenschutz:** In die Gruppe gehen nur Plandaten, die alle EF-Schüler ohnehin in Untis sehen
(Fach, Kurs, Lehrerkürzel, Raum). Keine Noten, Abwesenheiten oder Schülernamen. Sag der Gruppe Bescheid,
dass ein Bot mitliest – er reagiert nur auf Nachrichten, die mit `/` beginnen, und speichert keine
Chat-Inhalte.

### Eigene Nummer (Standard) – was das bedeutet

Der Bot verbindet sich wie **WhatsApp Web** als verknüpftes Gerät mit deinem Konto. Dein Handy
funktioniert ganz normal weiter (der Bot bleibt „offline“, du bekommst weiter Benachrichtigungen).

- **Für die Gruppe** sehen Bot-Nachrichten so aus, als hättest du sie geschrieben – deshalb beginnen sie
  alle mit **🤖**, und beim ersten Start erklärt eine Begrüßung, dass über deinen Account ein Bot läuft.
- **Postet nur in die eine Gruppe**, die du im Setup ausdrücklich auswählst. Ohne Auswahl postet und
  antwortet er nirgends (nichts landet versehentlich in der Familiengruppe). Deine privaten Chats ignoriert er.
- **Privatsphäre:** Wie WhatsApp Web empfängt der Server technisch alle deine Nachrichten. Der Bot wertet
  nur Befehle aus und speichert **keine** Chat-Inhalte – aber wer Zugriff auf den Server bzw. den Ordner
  `data/auth` hat, könnte dein WhatsApp mitlesen. Also nur auf einem Rechner betreiben, der dir gehört bzw.
  den nur du verwaltest, und `data/auth` niemals weitergeben.
- **Jederzeit abschaltbar:** Handy → *Verknüpfte Geräte* → Gerät „Ubuntu/Chrome“ abmelden. Damit ist der Bot
  sofort raus.

**Sperrrisiko ehrlich:** Baileys ist eine **inoffizielle** WhatsApp-Web-Schnittstelle. WhatsApp kann
Konten, die automatisiert schreiben, einschränken oder sperren – auf der eigenen Nummer würde das **dein**
Konto treffen. Der Bot verhält sich deshalb bewusst unauffällig, ähnlich normaler Nutzung: nur **eine**
Gruppe, in der du ohnehin Mitglied bist, nur wenige Nachrichten am Tag (alle Änderungen eines Abrufs in
**einer** Nachricht, bei Massenänderungen nur eine Zusammenfassung), Nachtruhe 22–6 Uhr, gedrosselte
Antworten, keine Nachrichten an Fremde, kein aggressives Neuverbinden, nur eine Instanz gleichzeitig.
Das Risiko ist dadurch gering, aber **nicht null**. Empfehlungen: Intervall nicht unter 5 Minuten setzen,
den Bot nicht in weitere Gruppen holen – und falls WhatsApp jemals eine Warnung zeigt, den Bot sofort
stoppen (`sudo systemctl stop ef-untis-bot`) und das verknüpfte Gerät abmelden.

**Kostenlose Alternative mit Trennung vom Privat-Konto:** Wenn ihr zu Hause einen **Festnetzanschluss**
habt, kann man darauf die kostenlose App *WhatsApp Business* registrieren (Bestätigung per Anruf statt SMS)
und den Bot damit betreiben: `WHATSAPP_OWN_NUMBER=false` bzw. im Installer Option 2.

Einen offiziellen, sperrsicheren Weg (WhatsApp Business Cloud API) gibt es für Gruppen nicht kostenlos –
daher dieser Kompromiss. Der Bot kommt ohne Gewähr; maßgeblich ist immer WebUntis.

---

## Installation (Linux-Server, Raspberry Pi, vServer)

**Du brauchst:** einen Linux-Rechner mit systemd (Debian/Ubuntu/Raspberry Pi OS), Internet,
dein Handy (nur für den einmaligen QR-Scan) und deine Untis-Zugangsdaten.
Node.js 22 installiert das Skript bei Bedarf selbst.

### Kostenlos einen Rechner finden, der dauerhaft läuft

- **Oracle Cloud „Always Free“:** kostenlose Ubuntu-VM, die dauerhaft läuft (bei der Anmeldung wird eine
  Kreditkarte nur zur Identitätsprüfung verlangt; es wird nichts abgebucht, solange man bei den
  „Always Free“-Ressourcen bleibt). Ideal für diesen Bot.
- **Alter Laptop/PC zu Hause** mit Ubuntu, der durchläuft (Strom kostet etwas).
- **Windows-PC:** über WSL2 mit Ubuntu möglich – dann muss der PC aber an bleiben.
- Ein **Raspberry Pi** ist die sparsamste Lösung, kostet aber einmalig Geld.

*(Angebote können sich ändern – Stand Ende 2026.)*

```bash
git clone https://github.com/louisttacc-hue/XRZTradingBot.git ef-untis-bot
cd ef-untis-bot
sudo ./install.sh
```

Das Installationsskript

1. prüft/installiert Node.js 22 und Build-Werkzeuge,
2. legt einen eigenen Systembenutzer `efbot` ohne Login an und installiert nach `/opt/ef-untis-bot`,
3. baut den Bot,
4. fragt die Zugangsdaten ab (inkl. **Untis-Schulsuche** – einfach „Jüchen“ eingeben) und speichert sie in
   `/opt/ef-untis-bot/.env` mit Rechten `600`,
5. **testet den Untis-Zugang** und zeigt die gefundenen EF-Kurse,
6. zeigt den **WhatsApp-QR-Code** – mit deinem Handy scannen:
   *WhatsApp → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen*,
7. lässt dich die **Klassengruppe auswählen** (bei vielen Gruppen per Suchbegriff, z. B. „EF“),
8. richtet den **systemd-Dienst** ein: startet sofort und nach jedem Server-Neustart automatisch,
   startet nach Abstürzen neu.

> **Eigene Nummer:** Du bist ja schon in der Klassengruppe – einfach im Setup auswählen. Fertig.
> **Separate Bot-Nummer:** Ein Gruppen-Admin fügt die Nummer hinzu (danach `sudo ./install.sh --relink`),
> oder du trägst im Assistenten den **Einladungslink** ein – dann tritt der Bot einmalig selbst bei.

> **Kein QR-Scan möglich** (z. B. Server-Konsole zeigt den Code schlecht an)? Im Assistenten deine Nummer
> bei „Kopplungscode“ eintragen. Dann erscheint ein 8-stelliger Code:
> *Verknüpfte Geräte → Gerät hinzufügen → Stattdessen mit Telefonnummer verknüpfen*.

### Schul-Login über Microsoft/IServ (kein Untis-Passwort)

Login-Art **2** wählen. In WebUntis: *Profil → Freigaben → Zugriff über Untis Mobile → Anzeigen*. Den Link
hinter dem QR-Code (`untis://setschool?…&user=…&key=…`) komplett einfügen – Benutzer und Schlüssel werden
automatisch übernommen.

## Betrieb

```bash
systemctl status ef-untis-bot          # läuft er?
journalctl -fu ef-untis-bot            # Live-Log (zeigt bei Bedarf auch den QR-Code)
sudo systemctl restart ef-untis-bot    # Neustart
sudo ./install.sh --reconfigure        # Zugangsdaten/Einstellungen ändern
sudo ./install.sh --relink             # WhatsApp neu koppeln / andere Gruppe wählen
git pull && sudo ./install.sh          # Update (Daten & Einstellungen bleiben)
sudo ./install.sh --uninstall          # Dienst entfernen (Daten bleiben)
```

- **Neustart/Stromausfall:** systemd startet den Bot automatisch. Bereits gemeldete Änderungen und
  noch nicht zugestellte Nachrichten liegen in der SQLite-Datenbank – es geht nichts verloren, nichts kommt doppelt.
- **Handy aus/kein Netz:** egal – der Bot ist ein eigenständig verknüpftes Gerät. Nach ~14 Tagen ohne
  Handy-Verbindung meldet WhatsApp verknüpfte Geräte aber ab; dann `sudo ./install.sh --relink`.
- **Bot kurz pausieren:** `sudo systemctl stop ef-untis-bot` (wieder an: `start`). Komplett trennen:
  am Handy unter *Verknüpfte Geräte* abmelden.
- **WhatsApp abgemeldet** (Gerät am Handy entfernt): Der Bot erkennt das, löscht die alte Sitzung und zeigt
  im Log einen neuen QR-Code (zusätzlich als Bild `data/qr.png`). Einfacher: `sudo ./install.sh --relink`.
- **Untis gestört:** Der Bot versucht es mit wachsendem Abstand weiter (bei Login-Fehlern nur alle 30 Min.,
  um keine Account-Sperre auszulösen). Bei anhaltenden Fehlern – und wenn es wieder läuft – bekommst du
  eine Nachricht in **„Nachricht an mich selbst“** (bzw. an `ADMIN_NUMBERS` bei separater Bot-Nummer).
  Fehler werden nie in die Klassengruppe gepostet.
- **Backup:** Ordner `/opt/ef-untis-bot/data` und Datei `.env` sichern. **Achtung:** `data/auth` enthält
  die WhatsApp-Sitzung (= Zugriff auf das Bot-Konto) – nicht weitergeben, nicht in Git.

Ohne systemd (z. B. Windows/macOS-Testbetrieb): `npm ci && npm run build && npm run setup && npm start`
oder dauerhaft mit pm2: `pm2 start dist/index.js --name ef-untis-bot && pm2 save && pm2 startup`.

## Konfiguration (`.env`)

Alle Optionen mit Erklärung stehen in [`.env.example`](.env.example). Die wichtigsten:

| Variable | Standard | Bedeutung |
|---|---|---|
| `UNTIS_SERVER` / `UNTIS_SCHOOL` | – | aus der Schulsuche: `npm run find-school -- Jüchen` |
| `UNTIS_AUTH` | `password` | `password`, `secret` (QR-Link) oder `anonymous` |
| `UNTIS_CLASSES` | `EF` | überwachte Klasse(n); leer = eigene Klasse des Accounts |
| `WHATSAPP_OWN_NUMBER` | `true` | Bot läuft auf deiner Nummer (🤖-Kennzeichnung); `false` = separate Bot-Nummer |
| `WHATSAPP_GROUP_INVITE` | – | Einladungslink der Klassengruppe (optional) |
| `ADMIN_NUMBERS` | – | nur bei separater Bot-Nummer: deine Nummer für Fehlermeldungen |
| `POLL_INTERVAL_MINUTES` | `5` | Abrufintervall (min. 3; nachts automatisch 30) |
| `LOOKAHEAD_DAYS` | `14` | wie weit vorausgeschaut wird |
| `QUIET_HOURS` | `22:00-06:00` | Nachtruhe; Meldungen werden bis zum Ende gesammelt (leer = aus) |
| `MAX_EVENTS_PER_MESSAGE` | `25` | darüber nur eine Zusammenfassung (z. B. neuer Stundenplan) |

Nach Änderungen: `sudo systemctl restart ef-untis-bot`.

## So funktioniert die Erkennung (und warum nichts doppelt kommt)

1. **Abruf:** Pro Durchlauf Login → Klassen-Stundenplan der EF für heute + 13 Tage → **Logout**
   (Untis bittet ausdrücklich darum; die webuntis-Bibliothek sendet dabei kein Session-Cookie, deshalb
   macht der Bot das Logout selbst korrekt). Der Zeitraum wird automatisch auf das Schuljahr begrenzt.
2. **Untis-Markierungen** (Entfall, Vertretung, Raumwechsel, irregulär) werden mit einem
   *Fingerabdruck* je Stunde in der Datenbank vermerkt. Gemeldet wird nur, was neu ist oder sich
   geändert hat; verschwindet eine Markierung, kommt „findet doch statt“. Ändert sich nur der Hinweistext,
   gibt es keine neue Meldung.
3. **Stiller Vergleich** mit dem vorherigen Stand (nur für Tage, die in beiden Abrufen enthalten sind):
   neue, entfernte, verschobene oder still geänderte Stunden. Stunden werden über Datum, Beginn,
   Untis-Unterrichtsnummer und ursprüngliches Fach identifiziert – so überlebt die Erkennung auch einen
   Untis-Neuimport mit neuen IDs.
4. **Atomar gespeichert:** Neuer Stand, Fingerabdrücke und ausgehende Nachrichten landen in **einer**
   SQLite-Transaktion. Jede Nachricht bekommt vor dem Senden eine feste WhatsApp-Nachrichten-ID; ein
   erneuter Versuch nach Absturz/Timeout benutzt dieselbe ID.
5. **Schutzmechanismen:** Beim allerersten Start wird der Ist-Zustand still übernommen (keine Flut alter
   Meldungen, nur eine Begrüßung). Liefert Untis plötzlich einen komplett leeren Plan, wird das erst nach
   dreifacher Bestätigung geglaubt. Bereits vergangene Stunden werden nicht mehr gemeldet.

## Entwicklung

```bash
npm ci
npm test              # über 100 Unit-, Integrations- und E2E-Tests (Vitest)
npm run typecheck
npm run build
npm run check:untis   # Untis-Zugang mit der .env prüfen (ohne WhatsApp)
npm run dev           # Bot direkt aus den Quellen starten (tsx)
```

```
src/
  index.ts            Dauerbetrieb (systemd), Signale, Absturzbehandlung
  app.ts              Verdrahtung aller Teile (auch vom E2E-Test genutzt)
  setup.ts            Einrichtungsassistent: QR/Kopplungscode, Gruppenwahl
  config.ts           .env-Validierung (zod), Nachtruhe
  domain/             reine Logik: Typen, Zeit (Europe/Berlin), Änderungserkennung
  untis/              webuntis-Adapter (Passwort, QR-Secret, anonym), Normalisierung
  db/store.ts         SQLite: Snapshot, Fingerabdrücke, Outbox, Meta
  service/monitor.ts  Abrufschleife, Backoff, Admin-Alarm
  format/messages.ts  WhatsApp-Texte
  bot/                Befehle, Routing, Outbox-Versand, Baileys-Verbindung
tests/                Unit-, Integrations- (Fake-Untis-Server) und End-to-End-Tests
deploy/               systemd-Dienst (gehärtet)
install.sh            Installation/Update/Einrichtung
```

Die Tests laufen gegen einen nachgebauten WebUntis-JSON-RPC-Server mit der **echten** webuntis-Bibliothek
sowie einen End-to-End-Durchlauf (Untis → Datenbank → WhatsApp-Bot → Gruppe, inkl. Befehlen,
Reconnect, Neustart und Abmeldung) mit simuliertem WhatsApp-Socket.

## Grundlagen & Dank

- [WhiskeySockets/Baileys](https://github.com/WhiskeySockets/Baileys) – WhatsApp-Web-Anbindung (v7)
- [SchoolUtils/WebUntis](https://github.com/SchoolUtils/WebUntis) – Untis-Client (Passwort-, QR- und anonymer Login)
- [4biddencode/UntAPI](https://github.com/4biddencode/UntAPI) – Vorbild für Fehlercodes/Schulsuche
- [TheBeaconCrafter/untis-notifier](https://github.com/TheBeaconCrafter/untis-notifier) – Vorbild für Diff-Ansatz und Betrieb
- Arbeitsweise nach [obra/superpowers](https://github.com/obra/superpowers) (TDD, Verifikation vor Fertigmeldung)
  und [mattpocock/skills](https://github.com/mattpocock/skills) (Tests an öffentlichen Schnittstellen, „deep modules“)

Lizenz: MIT. Kein offizielles Produkt von Untis GmbH oder WhatsApp/Meta.
