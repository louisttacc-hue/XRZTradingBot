"""Sicherheitsprüfungen der TradingView-Steuerung – reine Funktionen ohne Seiteneffekte.

Grundsatz: Im Zweifel NICHT handeln. Jede Funktion liefert entweder ein eindeutiges "passt"
oder einen deutschen Grund, warum abgebrochen wird. Texte aus TradingView gelten als
nicht vertrauenswürdig: Sie werden nur verglichen, nie als Anweisung verstanden.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Iterable

from . import labels
from .models import AccountInfo, AccountKind, OrderRequest

_SYMBOL_RE = re.compile(r"^([A-Z0-9_.!-]{1,30}:)?[A-Z0-9_.!/-]{1,40}$", re.I)
_DASHES = str.maketrans({"\u2212": "-", "\u2012": "-", "\u2013": "-", "\u2010": "-"})
_GROUP_SPACES = str.maketrans("", "", "\u00a0\u202f\u2009 '’")
# "1,000" / "2.500" / "16.667": Tausender- ODER Dezimaltrenner? → ohne Raten nicht lesbar
_AMBIGUOUS_RE = re.compile(r"^-?[1-9]\d{0,2}[.,]\d{3}$")
_NUMBER_TOKEN_RE = re.compile(r"[\u2212-]?\d[\d.,'’\u00a0\u202f]*")


# --------------------------------------------------------------------------- #
# Zahlen und Symbole
# --------------------------------------------------------------------------- #
def _number_text(text: str) -> str | None:
    s = text.strip().translate(_DASHES).translate(_GROUP_SPACES)
    if not s or not re.fullmatch(r"-?[\d.,]+", s) or not re.search(r"\d", s):
        return None
    return s


def parse_number(text: Any) -> float | None:
    """Liest eine Zahl aus TradingView-Text (Unicode-Minus, Tausendertrenner, Dezimalkomma).

    Eindeutig: "1,234.5", "1.234,5", "0,25", "1,000,000", "16.66". Mehrdeutig – und deshalb
    ``None`` – ist genau ein Trenner vor genau drei Ziffern ("1,000", "2.500"): lieber gar
    keine Zahl als eine um den Faktor 1000 falsche.
    """
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text) if math.isfinite(text) else None
    if not isinstance(text, str) or (s := _number_text(text)) is None:
        return None
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        grp = "," if dec == "." else "."
        head, _, tail = s.rpartition(dec)
        if grp in tail or not re.fullmatch(r"-?\d{1,3}(" + re.escape(grp) + r"\d{3})*", head):
            return None
        s = head.replace(grp, "") + "." + tail
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        if s.count(sep) > 1:
            if not re.fullmatch(r"-?\d{1,3}(" + re.escape(sep) + r"\d{3})+", s):
                return None
            s = s.replace(sep, "")
        elif _AMBIGUOUS_RE.match(s):
            return None
        else:
            s = s.replace(sep, ".")
    try:
        val = float(s)
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def number_candidates(text: Any) -> list[float]:
    """Alle möglichen Lesarten (bei "1,000": 1.0 und 1000.0) – nur für Gegenproben eigener Eingaben."""
    val = parse_number(text)
    if val is not None:
        return [val]
    s = _number_text(text) if isinstance(text, str) else None
    if s and _AMBIGUOUS_RE.match(s):
        sep = "." if "." in s else ","
        return [float(s.replace(sep, ".")), float(s.replace(sep, ""))]
    return []


def is_ambiguous_display(value: float) -> bool:
    """Würde TradingView diese Menge mehrdeutig anzeigen (z. B. 16.667 oder 1,000)?"""
    if float(value).is_integer():
        return 1000 <= abs(value) < 1_000_000
    return bool(_AMBIGUOUS_RE.match(format_number(value)))


def unambiguous_qty(qty: float, step: float | None, min_qty: float | None) -> float:
    """Kürzt eine Menge mit genau drei Nachkommastellen (z. B. 16.667 → 16.66), damit sie im
    Absende-Knopf eindeutig lesbar ist – nur wenn das höchstens 1 % ausmacht und erlaubt ist."""
    if not is_ambiguous_display(qty) or float(qty).is_integer():
        return qty
    candidate = floor_to_step(math.floor(qty * 100 + 1e-9) / 100, step)
    if candidate > 0 and candidate >= (min_qty or 0) and (qty - candidate) / qty <= 0.01 \
            and not is_ambiguous_display(candidate):
        return candidate
    return qty


def split_symbol(symbol: str) -> tuple[str, str]:
    """"BINANCE:BTCUSDT" → ("BINANCE", "BTCUSDT"); ohne Börse → ("", "AAPL")."""
    s = (symbol or "").strip().upper()
    if ":" in s:
        prefix, _, ticker = s.rpartition(":")
        return prefix, ticker
    return "", s


def same_symbol(a: str, b: str) -> bool:
    """Gleich, wenn der Ticker gleich ist und – falls beide eine Börse nennen – auch die Börse."""
    pa, ta = split_symbol(a)
    pb, tb = split_symbol(b)
    if not ta or ta != tb:
        return False
    return not pa or not pb or pa == pb


def floor_to_step(qty: float, step: float | None) -> float:
    """Rundet eine Menge auf das Mengenraster des Symbols ab (nie auf)."""
    if not step or step <= 0:
        return qty
    d_step = Decimal(repr(float(step)))
    n = (Decimal(repr(float(qty))) / d_step).to_integral_value(rounding=ROUND_FLOOR)
    return float(n * d_step)


def qty_tolerance(qty: float, step: float | None) -> float:
    """Erlaubte Abweichung beim Rücklesen: halber Mengenschritt bzw. 0,2 % (Anzeige-Rundung)."""
    return max((step or 0.0) / 2, abs(qty) * 0.002) + 1e-12


def format_number(value: float, decimal_comma: bool = False) -> str:
    """Zahl ohne Exponentialschreibweise (zum Eintippen; optional mit Dezimalkomma)."""
    text = format(Decimal(repr(float(value))).normalize(), "f")
    return text.replace(".", ",") if decimal_comma else text


def numbers_in(text: str) -> list[float]:
    """Alle Zahlen (inkl. aller Lesarten mehrdeutiger Schreibweisen) in einem Text."""
    out: list[float] = []
    for tok in _NUMBER_TOKEN_RE.findall(text or ""):
        out.extend(number_candidates(tok.rstrip(".,")))
    return out


# --------------------------------------------------------------------------- #
# Konto
# --------------------------------------------------------------------------- #
def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _summary_values(summary: Any) -> tuple[float | None, float | None]:
    balance = equity = None
    for item in summary if isinstance(summary, list) else []:
        if not isinstance(item, dict):
            continue
        text = _s(item.get("text"))
        value = parse_number(item.get("value"))
        if value is None:
            continue
        if balance is None and labels.BALANCE_RE.search(text):
            balance = value
        elif equity is None and labels.EQUITY_RE.search(text):
            equity = value
    return balance, equity


def classify_account(state: dict[str, Any] | None, paper_account_name: str = "") -> AccountInfo:
    """Ordnet das aktive TradingView-Konto ein (Daten aus ``js.broker_state()``).

    PAPER nur, wenn ALLES passt: Broker-ID exakt "Paper", Kontotyp "demo" oder nicht gemeldet,
    ein Paper-Merkmal im Fenster, kein fremder Broker sichtbar, (falls eingestellt) der richtige
    Kontoname und kein "The Leap"-Wettbewerbskonto. LIVE nur bei verbundenem echtem Broker ohne
    Demo-Kontotyp. Alles andere ist UNKNOWN.
    """
    st = state if isinstance(state, dict) else {}
    dom = st.get("dom") if isinstance(st.get("dom"), dict) else {}
    meta_name, dom_name = _s(st.get("accountMetaName")), _s(dom.get("accountName"))
    info = AccountInfo(
        broker_id=_s(st.get("brokerId")),
        broker_title=_s(st.get("brokerTitle")),
        account_name=meta_name or dom_name,
        account_type=_s(st.get("accountMetaType")) or _s(st.get("accountType")),
        currency=_s(st.get("accountMetaCurrency")),
    )
    info.balance, info.equity = _summary_values(st.get("summary"))
    ev, pr = info.evidence, info.problems
    if not st.get("ok"):
        pr.append(f"Handelsdaten nicht lesbar ({_s(st.get('reason')) or 'keine Antwort'})")
        return info
    status = st.get("connectStatus")
    if status != labels.CONNECTED:
        pr.append(f"Broker nicht verbunden (Status {status!r})")
        return info
    ev.append("Broker verbunden")
    names = [n for n in (meta_name, dom_name) if n]
    if any(labels.LEAP_RE.search(n) for n in names):
        pr.append("„The Leap“-Wettbewerbskonto aktiv – das ist nicht dein Paper-Konto")
        return info
    types = {k: _s(st.get(k)).lower() for k in ("accountMetaType", "accountType") if _s(st.get(k))}
    tables = [str(t) for t in (dom.get("tables") or []) if t]
    paper_tables = [t for t in tables if t.startswith(labels.PAPER_TABLE_PREFIX)]
    foreign_tables = [t for t in tables if not t.startswith(labels.PAPER_TABLE_PREFIX)]

    if info.broker_id == labels.PAPER_BROKER_ID:
        ev.append("Broker-ID „Paper“")
        wrong = {k: v for k, v in types.items() if v != labels.DEMO_ACCOUNT_TYPE}
        if wrong:
            pr.append("Kontotyp " + ", ".join(f"„{v}“" for v in wrong.values()) + " statt „demo“")
        else:
            ev.append("Kontotyp „demo“" if types else "kein Kontotyp gemeldet")
        if dom.get("paperMarker") or paper_tables:
            ev.append("Paper-Trading-Merkmal im Fenster" + (f" ({', '.join(paper_tables)})" if paper_tables else ""))
        else:
            pr.append("kein „Paper Trading“-Merkmal im Fenster sichtbar (bitte unten das Handelspanel einblenden)")
        if foreign_tables:
            pr.append("Tabellen eines anderen Brokers sichtbar: " + ", ".join(foreign_tables))
        wanted = _norm(paper_account_name)
        if wanted:
            if any(_norm(n) == wanted for n in names):
                ev.append(f"Kontoname „{paper_account_name.strip()}“ passt")
            else:
                pr.append(f"aktives Konto „{info.account_name or '?'}“ statt „{paper_account_name.strip()}“")
        if not pr:
            info.kind = AccountKind.PAPER
        return info

    if not info.broker_id:
        pr.append("kein Handelskonto aktiv (in TradingView unten „Paper Trading“ verbinden)")
        return info
    if "paper" in info.broker_id.lower():
        pr.append(f"Broker-ID „{info.broker_id}“ ähnelt „Paper“, ist es aber nicht genau")
        return info
    if any(v == labels.DEMO_ACCOUNT_TYPE for v in types.values()):
        pr.append(f"Demokonto des Brokers „{info.broker_title or info.broker_id}“ – weder Paper Trading noch Echtgeld")
        return info
    if paper_tables:
        pr.append("widersprüchlich: echter Broker aktiv, aber Paper-Tabellen sichtbar")
        return info
    ev.append(f"echter Broker „{info.broker_title or info.broker_id}“ verbunden")
    if types:
        ev.append("Kontotyp " + ", ".join(f"„{v}“" for v in types.values()))
    info.kind = AccountKind.LIVE
    return info


def check_account(expected: AccountKind, info: AccountInfo) -> tuple[bool, str]:
    if expected not in (AccountKind.PAPER, AccountKind.LIVE):
        return False, "Interner Fehler: unklar, welches Konto erwartet wird – abgebrochen."
    if info.kind == AccountKind.UNKNOWN:
        why = "; ".join(info.problems) or "keine eindeutigen Daten"
        return False, f"Ich kann nicht sicher erkennen, welches Konto in TradingView aktiv ist ({why}). Ich handle deshalb nicht."
    if info.kind != expected:
        if expected == AccountKind.PAPER:
            return False, (f"Achtung: In TradingView ist ein ECHTGELD-Konto aktiv ({info.label}), du bist aber im "
                           "Paper-Modus. Ich breche ab – bitte in TradingView auf „Paper Trading“ wechseln.")
        return False, ("In TradingView ist Paper Trading aktiv, du bist aber im Echtgeld-Modus. "
                       "Ich breche ab – bitte den richtigen Broker verbinden oder den Modus wechseln.")
    return True, f"Konto geprüft: {info.label}."


# --------------------------------------------------------------------------- #
# Order-Wunsch und Ticket
# --------------------------------------------------------------------------- #
def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate_request(req: OrderRequest) -> tuple[bool, str]:
    if not isinstance(req.symbol, str) or not _SYMBOL_RE.match(req.symbol.strip()):
        return False, f"Ungültiges Symbol „{req.symbol}“ (erwartet z. B. BINANCE:BTCUSDT)."
    if req.side not in ("buy", "sell"):
        return False, "Richtung muss Kaufen oder Verkaufen sein."
    if req.order_type not in ("market", "limit"):
        return False, "Nur Markt- und Limit-Orders sind erlaubt."
    if not _positive(req.qty):
        return False, "Die Menge muss größer als 0 sein."
    if req.order_type == "limit" and not _positive(req.limit_price):
        return False, "Für eine Limit-Order fehlt ein gültiger Limitpreis."
    for name, value in (("Take-Profit", req.take_profit), ("Stop-Loss", req.stop_loss)):
        if value is not None and not _positive(value):
            return False, f"{name} ist ungültig."
    if req.take_profit and req.stop_loss:
        if req.side == "buy" and not req.take_profit > req.stop_loss:
            return False, "Beim Kauf muss der Take-Profit über dem Stop-Loss liegen."
        if req.side == "sell" and not req.stop_loss > req.take_profit:
            return False, "Beim Verkauf muss der Stop-Loss über dem Take-Profit liegen."
    return True, "ok"


def check_brackets(req: OrderRequest, quote: dict[str, Any] | None) -> tuple[bool, str]:
    """Kauf: TP > Kurs > SL; Verkauf: SL > Kurs > TP (bei Limit-Orders ist der Limitpreis der Bezug)."""
    tp, sl = req.take_profit, req.stop_loss
    if not tp and not sl:
        return True, "Keine Ziele gesetzt."
    q = quote or {}
    if req.order_type == "limit" and _positive(req.limit_price):
        ref, ref_name = float(req.limit_price), "Limitpreis"
    elif req.side == "buy":
        ref, ref_name = q.get("ask") or q.get("last"), "Briefkurs"
    else:
        ref, ref_name = q.get("bid") or q.get("last"), "Geldkurs"
    if not _positive(ref):
        return False, "Kein aktueller Kurs zum Prüfen von Stop-Loss/Take-Profit – abgebrochen."
    if req.side == "buy":
        if tp and not tp > ref:
            return False, f"Take-Profit {tp:g} liegt nicht über dem {ref_name} {ref:g}."
        if sl and not sl < ref:
            return False, f"Stop-Loss {sl:g} liegt nicht unter dem {ref_name} {ref:g}."
    else:
        if tp and not tp < ref:
            return False, f"Take-Profit {tp:g} liegt nicht unter dem {ref_name} {ref:g}."
        if sl and not sl > ref:
            return False, f"Stop-Loss {sl:g} liegt nicht über dem {ref_name} {ref:g}."
    return True, "Stop-Loss/Take-Profit plausibel."


@dataclass
class SubmitInfo:
    side: str  # buy | sell
    qty: float | None  # None = nicht eindeutig lesbar
    qty_text: str
    symbol: str
    order_type: str  # market | limit | stop | stop_limit
    price: float | None = None


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", (text or "").replace("−", "-")).strip()


def parse_submit_text(text: str) -> SubmitInfo | None:
    """Liest "Buy 2 NYSE:CVX MARKET" / "Verkaufen 0,5 BINANCE:BTCUSDT LIMIT" aus dem Absende-Knopf."""
    m = labels.SUBMIT_TEXT_RE.match(_clean(text))
    if not m:
        return None
    type_word = re.sub(r"\s+", " ", m.group("type").lower())
    return SubmitInfo(
        side=labels.SUBMIT_SIDE_WORDS[m.group("side").lower()],
        qty=parse_number(m.group("qty")),
        qty_text=m.group("qty"),
        symbol=m.group("symbol"),
        order_type=labels.SUBMIT_TYPE_WORDS.get(type_word, labels.SUBMIT_TYPE_WORDS.get(type_word.replace(" ", ""), "")),
        price=parse_number(m.group("price")) if m.group("price") else None,
    )


def check_ticket(req: OrderRequest, submit_text: str, qty_step: float | None = None) -> tuple[bool, str]:
    """Vergleicht den Absende-Knopf mit dem Order-Wunsch: Richtung, Menge (im Mengenraster), Symbol, Typ."""
    shown = _clean(submit_text)
    if not shown:
        return False, "Der Absende-Knopf zeigt keinen Text."
    if labels.SUBMIT_NO_SIDE_RE.search(shown):
        return False, "Im Ticket ist noch keine Richtung gewählt."
    info = parse_submit_text(shown)
    if info is None:
        return False, f"Der Absende-Knopf zeigt unerwarteten Text („{shown[:80]}“)."
    if info.side != req.side:
        want = "Kaufen" if req.side == "buy" else "Verkaufen"
        return False, f"Das Ticket steht auf der falschen Richtung („{shown[:80]}“ statt {want})."
    if info.qty is None:
        return False, f"Die Menge im Ticket ist nicht eindeutig lesbar („{info.qty_text}“)."
    if info.qty <= 0:
        return False, "Die Menge im Ticket wurde zu 0 – vermutlich steht das Mengenfeld auf % oder Betrag."
    if abs(info.qty - req.qty) > qty_tolerance(req.qty, qty_step):
        return False, (f"Die Menge im Ticket ({info.qty:g}) passt nicht zur gewünschten Menge ({req.qty:g}) – "
                       "evtl. steht das Mengenfeld auf % oder Betrag.")
    if not same_symbol(info.symbol, req.symbol):
        return False, f"Im Ticket steht das falsche Symbol ({info.symbol} statt {req.symbol})."
    if info.order_type != req.order_type:
        return False, f"Im Ticket ist der falsche Ordertyp gewählt ({info.order_type or '?'} statt {req.order_type})."
    return True, f"Ticket geprüft: {shown[:80]}"


def _mentions_ticker(text: str, symbol: str) -> bool:
    ticker = split_symbol(symbol)[1]
    return bool(ticker) and re.search(r"(?<![A-Z0-9])" + re.escape(ticker) + r"(?![A-Z0-9])", text.upper()) is not None


def check_dialog(text: str, req: OrderRequest, qty_step: float | None = None) -> tuple[bool, str]:
    """Passt ein Vorschau-/Bestätigungsdialog zur Order? Symbol, Richtung UND Menge müssen vorkommen."""
    shown = _clean(text)
    if not shown:
        return False, "Der Bestätigungsdialog ist leer."
    phrase = labels.SUBMIT_TEXT_RE.search(shown)
    if phrase is not None:
        return check_ticket(req, phrase.group(0), qty_step)
    if not _mentions_ticker(shown, req.symbol):
        return False, f"Der Dialog nennt das Symbol {split_symbol(req.symbol)[1]} nicht."
    first = {side: m.start() for side, rx in labels.SIDE_WORD_RE.items() if (m := rx.search(shown))}
    if req.side not in first or min(first, key=first.get) != req.side:
        return False, "Der Dialog nennt nicht (zuerst) die gewünschte Richtung."
    tol = qty_tolerance(req.qty, qty_step)
    if not any(abs(v - req.qty) <= tol for v in numbers_in(shown)):
        return False, f"Der Dialog nennt die Menge {req.qty:g} nicht."
    return True, "Dialog passt zur Order."


def check_close_dialog(text: str, symbol: str) -> tuple[bool, str]:
    shown = _clean(text)
    if not _mentions_ticker(shown, symbol):
        return False, f"Der Schließen-Dialog nennt {split_symbol(symbol)[1]} nicht."
    return True, "Dialog passt."


# --------------------------------------------------------------------------- #
# Kontrolle nach dem Absenden (Vorher/Nachher über die TradingView-API)
# --------------------------------------------------------------------------- #
def net_position(positions: Iterable[dict[str, Any]], symbol: str) -> float:
    total = 0.0
    for p in positions or []:
        if isinstance(p, dict) and same_symbol(str(p.get("symbol") or ""), symbol):
            qty = p.get("qty") if _positive(p.get("qty")) else 0.0
            total += qty * (-1.0 if p.get("side") == labels.SIDE_SELL else 1.0)
    return total


def _close(a: Any, b: Any, tol: float) -> bool:
    return _positive(a) and _positive(b) and abs(float(a) - float(b)) <= tol


def _price_tol(price: float | None, min_tick: float | None) -> float:
    return max(min_tick or 0.0, abs(price or 0.0) * 1e-4)


def evaluate_fill(before: dict[str, Any], after: dict[str, Any], req: OrderRequest, qty: float,
                  qty_step: float | None = None, min_tick: float | None = None) -> dict[str, Any]:
    """Vergleicht Positionen/Orders vor und nach dem Absenden.

    status: filled | partial | working | rejected | none
    """
    side_val = labels.SIDE_BUY if req.side == "buy" else labels.SIDE_SELL
    tol = qty_tolerance(qty, qty_step)
    old_orders = {str(o.get("id")): o.get("status") for o in before.get("orders") or [] if isinstance(o, dict)}
    new_orders = [
        o for o in after.get("orders") or []
        if isinstance(o, dict) and same_symbol(str(o.get("symbol") or ""), req.symbol)
        and not o.get("parentId") and old_orders.get(str(o.get("id")), "neu") != o.get("status")
    ]
    mine = [o for o in new_orders if o.get("side") == side_val]
    out: dict[str, Any] = {"status": "none", "order_id": "", "filled_qty": None, "avg_price": None,
                           "brackets_ok": None, "message": ""}
    for o in mine:
        if o.get("status") == labels.STATUS_REJECTED:
            out.update(status="rejected", order_id=str(o.get("id") or ""), message=str(o.get("message") or ""))
            return out

    delta = net_position(after.get("positions") or [], req.symbol) - net_position(before.get("positions") or [], req.symbol)
    signed = delta * side_val
    filled_order = next((o for o in mine if o.get("status") == labels.STATUS_FILLED), None)
    position = next((p for p in after.get("positions") or [] if isinstance(p, dict)
                     and same_symbol(str(p.get("symbol") or ""), req.symbol) and _positive(p.get("qty"))), None)
    if abs(signed - qty) <= tol or (filled_order and signed <= tol):
        fq = abs(delta) if abs(signed - qty) <= tol else (filled_order.get("filledQty") or filled_order.get("qty"))
        avg = (filled_order or {}).get("avgPrice") or (position or {}).get("avgPrice")
        out.update(status="filled", filled_qty=float(fq) if _positive(fq) else qty,
                   avg_price=float(avg) if _positive(avg) else None,
                   order_id=str((filled_order or {}).get("id") or (position or {}).get("id") or ""))
        out["brackets_ok"] = _brackets_present(req, position, after.get("orders") or [], out["order_id"], min_tick)
        return out
    if req.order_type == "limit":
        ptol = _price_tol(req.limit_price, min_tick)
        for o in mine:
            if o.get("status") in (labels.STATUS_WORKING, labels.STATUS_PLACING, labels.STATUS_INACTIVE) \
                    and abs(float(o.get("qty") or 0) - qty) <= tol:
                out.update(status="working", order_id=str(o.get("id") or ""))
                if not _close(o.get("limitPrice"), req.limit_price, ptol):
                    out["message"] = (f"Limitpreis in TradingView ({o.get('limitPrice')}) weicht vom gewünschten "
                                      f"({req.limit_price:g}) ab")
                out["brackets_ok"] = _brackets_present(req, o, after.get("orders") or [], out["order_id"], min_tick)
                return out
    if 0 < signed < qty - tol:
        out.update(status="partial", filled_qty=signed, avg_price=(position or {}).get("avgPrice"))
    return out


def _brackets_present(req: OrderRequest, holder: dict[str, Any] | None, orders: list[dict[str, Any]],
                      parent_id: str, min_tick: float | None) -> bool | None:
    """Sind gewünschter TP/SL in TradingView angekommen? None = keine gewünscht."""
    if not req.take_profit and not req.stop_loss:
        return None
    holder = holder or {}
    children = [o for o in orders if isinstance(o, dict) and parent_id and str(o.get("parentId") or "") == parent_id
                and o.get("status") not in (labels.STATUS_CANCELED, labels.STATUS_REJECTED)]
    child_prices = [o.get("limitPrice") for o in children] + [o.get("stopPrice") for o in children]
    for want, key in ((req.take_profit, "takeProfit"), (req.stop_loss, "stopLoss")):
        if not want:
            continue
        tol = _price_tol(want, min_tick)
        if not (_close(holder.get(key), want, tol) or any(_close(p, want, tol) for p in child_prices)):
            return False
    return True


def evaluate_close(before: dict[str, Any], after: dict[str, Any], symbol: str) -> dict[str, Any]:
    """status: closed | reduced | none"""
    old = net_position(before.get("positions") or [], symbol)
    new = net_position(after.get("positions") or [], symbol)
    if old and abs(new) < 1e-12:
        return {"status": "closed", "closed_qty": abs(old)}
    if old and abs(new) < abs(old) and new * old > 0:
        return {"status": "reduced", "closed_qty": abs(old) - abs(new)}
    return {"status": "none", "closed_qty": 0.0}
