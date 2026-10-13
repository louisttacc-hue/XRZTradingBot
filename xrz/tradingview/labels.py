"""Alle Selektoren, Texte und Muster der TradingView-Oberfläche an EINER Stelle.

TradingView dokumentiert weder seine DOM-Struktur noch die interne JS-Schnittstelle. Alles hier
stammt aus Beobachtungen Dritter (Recherche Okt. 2026) und kann sich mit jedem Auto-Update ändern.
Deshalb: Selektoren nur hier pflegen, beim Start per ``selector_health()`` prüfen und im Zweifel
NICHT handeln.

Quellen-Kürzel:
  E = eltonaguiar (TradingView Desktop 3.1.0, Apr–Mai 2026)
  Y = yardenrippin/slave-client (tradingview.com, Apr 2026)
  T = TradingView-Trading-Platform-Bundle (faroukBakari/trader-pro)
  X = share-market-tools-Erweiterung
  P = TradingviewPlus-Erweiterung
  M = Miha21222 (TradingView Desktop 3.4.1, Sep 2026)
  O = opencli-plugin-tradingview / mkajnar/mcp_tv (interne Broker-API)

UNVERIFIZIERT = nur eine Quelle, nur Web-Version oder geraten (v. a. alle deutschen Texte –
eine deutsche TradingView-Sprachdatei wurde nicht gefunden).

Reguläre Ausdrücke, die auch im Browser laufen (``*_RE``-Texte), nutzen nur Syntax, die
JavaScript und Python gleich verstehen; Groß-/Kleinschreibung wird jeweils ignoriert.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# App / Prozess / CDP
# --------------------------------------------------------------------------- #
APP_NAME = "TradingView.app"
# Hauptprozess (nicht die "TradingView Helper"-Prozesse) – Muster für `pgrep -f`
MAIN_PROCESS_PATTERN = "TradingView.app/Contents/MacOS/TradingView"
# Erwartete Bundle-ID (UNVERIFIZIERT, doesitarm); maßgeblich ist immer die Info.plist
DEFAULT_BUNDLE_ID = "com.tradingview.tradingviewapp.desktop"
# Chart-Seite als CDP-Ziel (M, tradingview-mcp)
CHART_URL_RE = re.compile(r"^https://([a-z]{2,3}\.|www\.)?tradingview\.com/chart", re.I)
# Umgebungsvariablen, die den Start mit Steuer-Port verhindern ("bad option", Exit 9)
ENV_BLOCKLIST_PREFIXES = ("ELECTRON_",)
ENV_BLOCKLIST = ("NODE_OPTIONS", "NODE_EXTRA_CA_CERTS")

# --------------------------------------------------------------------------- #
# Interne JS-API (O, M) – Namen der Broker-Methoden
# --------------------------------------------------------------------------- #
PAPER_BROKER_ID = "Paper"  # metainfo().id bei Paper Trading (O) – exakter Vergleich
DEMO_ACCOUNT_TYPE = "demo"  # accountMetainfo().type / currentAccountType() (O)
BROKER_METHODS = (
    "metainfo", "accountMetainfo", "currentAccountType", "currentAccount", "accountManagerInfo",
    "positions", "orders", "symbolInfo", "quotesSnapshot", "previewOrder", "placeOrder", "closePosition",
)
# Ohne diese Teile wird gar nicht gehandelt (selector_health → trading_ok=False)
REQUIRED_API = (
    "TradingViewApi", "chart.symbol", "chart.setSymbol", "trading", "activeBroker",
    "broker.metainfo", "broker.positions", "broker.orders",
)
# Öffentliche Enums aus broker-api.d.ts (T)
SIDE_BUY, SIDE_SELL = 1, -1
ORDER_TYPE = {"limit": 1, "market": 2, "stop": 3, "stop_limit": 4}
STATUS_CANCELED, STATUS_FILLED, STATUS_INACTIVE, STATUS_PLACING, STATUS_REJECTED, STATUS_WORKING = 1, 2, 3, 4, 5, 6
CONNECTED = 1  # ConnectionStatus.Connected

# Kontoübersicht accountManagerInfo().summary[].text – lokalisiert (deutsch UNVERIFIZIERT)
BALANCE_RE = re.compile(r"^(account\s+)?balance\b|kontostand|guthaben|saldo", re.I)
EQUITY_RE = re.compile(r"^equity\b|eigenkapital|nettoliquidit", re.I)
# TradingView-Wettbewerbskonten ("The Leap …") sind nie das gewünschte Paper-Konto (E)
LEAP_RE = re.compile(r"\bleap\b", re.I)

# --------------------------------------------------------------------------- #
# Paper-Merkmale / Kontoverwaltung
# --------------------------------------------------------------------------- #
PAPER_MARKERS = ('[data-name="paper-trading"]', "button#paper-trading", "#paper-trading")  # Y
POSITION_TABLES = ('[data-name$=".positions-table"]',)  # E: "Paper.positions-table"
ORDER_TABLES = ('[data-name$=".orders-table"]',)
PAPER_TABLE_PREFIX = "Paper."
TABLE_ROWS = ("tr.ka-row", "tr.ka-tr")  # E, T
POSITIONS_TAB = ("button#positions",)  # Y – vor dem Lesen der Tabelle auswählen (History nutzt dieselbe Tabelle)
POSITION_CLOSE_BUTTON = ('[data-name="close-settings-cell-button"]',)  # E – erscheint erst beim Darüberfahren
ACCOUNT_NAME = ('span[class*="accountName-"]',)  # E – Hash-Klasse, nur Präfix vergleichen

# --------------------------------------------------------------------------- #
# Chart / Symbol
# --------------------------------------------------------------------------- #
SYMBOL_HEADER = ("#header-toolbar-symbol-search",)  # Y, M
LOADERS = ('[data-name="loading"]', '[class*="loader-"]')  # Y (Spinner)

# --------------------------------------------------------------------------- #
# Order-Ticket
# --------------------------------------------------------------------------- #
TICKET_CONTAINERS = (
    '[data-name="order-panel"]',  # angedocktes Panel (Y, T)
    '[data-name="order-dialog-popup"]',  # schwebendes Fenster (Y, T)
    '[class*="orderTicket-"]',  # Hash-Klasse (E)
)
# Ticket öffnen: Shift+T (offizielle Paper-Trading-Masterclass) – als "rawKeyDown", damit kein
# Buchstabe getippt wird (UNVERIFIZIERT, ob TradingView auf rawKeyDown reagiert)
OPEN_TICKET_KEY = {"key": "T", "code": "KeyT", "key_code": 84, "modifiers": 8}
# Fallback-Knöpfe (UNVERIFIZIERT auf Desktop 3.x)
OPEN_TICKET_BUTTONS = ('[data-name="trading-button"]', '[aria-label="Trading Panel"]')
OPEN_TICKET_TEXT_RE = r"^(Trade|Handeln|Handel)$"  # UNVERIFIZIERT
# Kopf des Panels: "Order" | "DOM" (P)
TICKET_MODE_RADIOS = ('button[role="radio"]',)
TICKET_ORDER_MODE_RE = r"^(Order|Auftrag)$"  # deutsch UNVERIFIZIERT

SIDE_CONTROLS = {
    "buy": ('[data-name="side-control-buy"]',),  # DIVs mit onClick (T, E, Y, X)
    "sell": ('[data-name="side-control-sell"]',),
}
SIDE_TEXT_RE = {"buy": r"^(Buy|Kaufen)\b", "sell": r"^(Sell|Verkaufen)\b"}  # deutsch UNVERIFIZIERT

ORDER_TYPE_TABS = {
    "market": ('[role="tab"]#Market',),  # Y, P (aria-selected)
    "limit": ('[role="tab"]#Limit',),
}
ORDER_TYPE_TEXT_RE = {"market": r"^(Market|Markt)$", "limit": r"^(Limit)$"}  # deutsch UNVERIFIZIERT

QUANTITY_INPUT = ("#quantity-field", "#order-ticket-quantity-input")  # E, Y, X (zweiter: ältere Versionen)
QUANTITY_UNIT_BUTTON = ('button[data-qa-id="quantity-input-equivalent-dropdown-button"]',)  # X
# Limitpreis: kein stabiler Selektor bekannt (X, UNVERIFIZIERT) → zusätzlich über die Beschriftung
LIMIT_PRICE_INPUT = ('input[data-qa-id*="limit-price"]', 'input[data-qa-id*="price-input"]')
LIMIT_PRICE_LABEL_RE = r"^(Limit price|Price|Limitpreis|Limit-Preis|Preis)\b"  # UNVERIFIZIERT

EXITS_TEXT_RE = r"^(Exits|Ausstiege|Ausstieg)$"  # E (aria-expanded); deutsch UNVERIFIZIERT
TP_TOGGLE = (
    '[data-qa-id="order-ticket-take-profit-checkbox-bracket"]',  # Y (aria-checked)
    '[data-name="order-ticket-profit-checkbox-bracket"]',  # T
)
SL_TOGGLE = (
    '[data-qa-id="order-ticket-stop-loss-checkbox-bracket"]',
    '[data-name="order-ticket-loss-checkbox-bracket"]',
)
TP_INPUT = ('[data-qa-id="ui-lib-Input-input order-ticket-take-profit-input"]', 'input[data-qa-id*="take-profit-input"]')  # Y, X
SL_INPUT = ('[data-qa-id="ui-lib-Input-input order-ticket-stop-loss-input"]', 'input[data-qa-id*="stop-loss-input"]')

# Absenden – derselbe data-name wird auch für "Confirm"/"Modify" benutzt → Text immer lesen (E, Y, X, T)
SUBMIT_BUTTON = ('[data-name="place-and-modify-button"]',)
DISCARD_BUTTON = ('[data-name="button-back"]',)  # E
TICKET_CLOSE_BUTTON = ('[data-qa-id="button-close"]', '[data-name="close"]')  # Y; zweiter UNVERIFIZIERT

# Text des Absende-Knopfs, z. B. "Buy 2 NYSE:CVX MARKET" (E). "MKT/LMT" und alle deutschen Formen UNVERIFIZIERT.
SUBMIT_SIDE_WORDS = {"buy": "buy", "kaufen": "buy", "kauf": "buy", "sell": "sell", "verkaufen": "sell", "verkauf": "sell"}
SUBMIT_TYPE_WORDS = {
    "market": "market", "mkt": "market", "markt": "market",
    "limit": "limit", "lmt": "limit",
    "stop": "stop", "stp": "stop",
    "stoplimit": "stop_limit", "stop limit": "stop_limit", "stop-limit": "stop_limit", "stop_limit": "stop_limit",
}
SUBMIT_TEXT_RE = re.compile(
    r"^(?P<side>Buy|Sell|Kaufen|Verkaufen|Kauf|Verkauf)\s+"
    r"(?P<qty>\d[\d.,'’  ]*)\s+"
    r"(?P<symbol>[^\s@]+)"
    r"(?:\s*@\s*(?P<price>[\d.,'’  −-]+))?\s+"
    r"(?P<type>STOP[\s_-]?LIMIT|MARKET|MKT|MARKT|LIMIT|LMT|STOP|STP)\b",
    re.I,
)
# Text, wenn noch keine Richtung gewählt ist (E)
SUBMIT_NO_SIDE_RE = re.compile(r"start creating order|order erstellen", re.I)
# Ticket zeigt eine Vorschau/Bestätigung im selben Fenster (UNVERIFIZIERT)
SUBMIT_CONFIRM_RE = r"^(Confirm|Place order|Send order|Bestätigen|Order platzieren|Order senden|Auftrag senden)\b"

# --------------------------------------------------------------------------- #
# Dialoge (Vorschau/Bestätigung) – T-Namen; tradingview.com UNVERIFIZIERT
# --------------------------------------------------------------------------- #
DIALOGS = (
    '[data-name="trading-confirm-dialog"]',
    '[data-name="confirm-dialog"]',
    '[data-name="simple-confirm-dialog"]',
    '[data-name="position-dialog-popup"]',
    '[data-name="confirm-inputs-dialog"]',
    "[data-dialog-name]",
    '[role="dialog"]',
    '[role="alertdialog"]',
)
DIALOG_BUTTONS = ("button", '[role="button"]', '[data-name="place-and-modify-button"]')
CONFIRM_BUTTON_RE = r"^(Yes|Confirm|OK|Place|Place order|Send order|Ja|Bestätigen|Platzieren|Order platzieren|Order senden|Senden)$"  # Y; deutsch UNVERIFIZIERT
CLOSE_CONFIRM_BUTTON_RE = r"^(Close Position|Close position|Close|Yes|Confirm|OK|Position schließen|Schließen|Ja|Bestätigen)$"  # Y; deutsch UNVERIFIZIERT
# Bewusst OHNE "Close"/"Schließen": bei "Position schließen?" wäre das die Bestätigung!
CANCEL_BUTTON_RE = r"^(Cancel|No|Abbrechen|Nein)$"
CANCEL_BUTTONS = ('[data-name="cancel-button"]', '[data-qa-id="button-close"]', '[data-name="close"]')  # UNVERIFIZIERT
SIDE_WORD_RE = {"buy": re.compile(r"\b(buy|long|kauf|kaufen)\b", re.I), "sell": re.compile(r"\b(sell|short|verkauf|verkaufen)\b", re.I)}

# --------------------------------------------------------------------------- #
# Niemals benutzen
# --------------------------------------------------------------------------- #
# One-Click-Kaufen/Verkaufen im Chart: lösen je nach Einstellung SOFORT eine Market-Order aus (E)
FORBIDDEN_CLICK = ('[data-name="buy-order-button"]', '[data-name="sell-order-button"]')
# Shift+B / Shift+S = Market-Order per Tastatur (Charting-Library-Doku)
FORBIDDEN_KEYS = (("B", 8), ("S", 8))

# Allgemeine Elemente für die Text-Suche, falls kein Selektor passt
TEXT_FALLBACK_TAGS = ("button", '[role="button"]', '[role="tab"]', '[role="radio"]', '[role="switch"]', '[role="checkbox"]', "label", "a")
INPUT_FALLBACK_TAGS = ("input",)

# Gesundheitscheck: DOM-Teile, die (sofern sichtbar) gezählt werden
HEALTH_DOM = {
    "symbol_header": SYMBOL_HEADER,
    "paper_marker": PAPER_MARKERS,
    "position_tables": POSITION_TABLES,
    "account_name": ACCOUNT_NAME,
    "ticket": TICKET_CONTAINERS,
    "side_buy": SIDE_CONTROLS["buy"],
    "side_sell": SIDE_CONTROLS["sell"],
    "order_type_market": ORDER_TYPE_TABS["market"],
    "order_type_limit": ORDER_TYPE_TABS["limit"],
    "quantity": QUANTITY_INPUT,
    "submit": SUBMIT_BUTTON,
    "forbidden_one_click": FORBIDDEN_CLICK,
}
# Nur prüfbar, wenn das Ticket offen ist
HEALTH_TICKET_REQUIRED = ("side_buy", "side_sell", "order_type_market", "quantity", "submit")
