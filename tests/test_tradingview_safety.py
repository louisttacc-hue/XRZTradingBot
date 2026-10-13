"""Sicherheitsprüfungen der TradingView-Steuerung: Konto-Erkennung, Ticket, Dialog, Ausführung."""
import pytest

from xrz.tradingview import labels
from xrz.tradingview.models import AccountKind, OrderRequest
from xrz.tradingview.safety import (
    check_account, check_brackets, check_dialog, check_ticket, classify_account, evaluate_close, evaluate_fill,
    floor_to_step, parse_number, same_symbol, unambiguous_qty, validate_request,
)


def _state(**kw):
    st = {"ok": True, "connectStatus": labels.CONNECTED, "brokerId": "Paper", "brokerTitle": "Paper Trading",
          "accountMetaType": "demo", "dom": {"paperMarker": True, "tables": ["Paper.positions-table"]}}
    st.update(kw)
    return st


# ------------------------------------------------------------------ Zahlen
@pytest.mark.parametrize("text,val", [
    ("1,234.5", 1234.5), ("1.234,5", 1234.5), ("0,25", 0.25), ("1,000,000", 1e6), ("16.66", 16.66),
    ("−0.5", -0.5), ("1 234,5", 1234.5), ("1,000", None), ("2.500", None), ("abc", None), ("", None),
])
def test_parse_number(text, val):
    assert parse_number(text) == (pytest.approx(val) if val is not None else None)


def test_qty_helpers():
    assert floor_to_step(0.123456, 0.001) == pytest.approx(0.123)
    assert floor_to_step(5, None) == 5
    assert unambiguous_qty(16.667, 0.001, 0.001) == pytest.approx(16.66)
    assert unambiguous_qty(0.5, 0.001, 0.001) == 0.5
    assert same_symbol("BINANCE:BTCUSDT", "BTCUSDT") and not same_symbol("BINANCE:BTCUSDT", "COINBASE:BTCUSDT")


# ------------------------------------------------------------------ Konto
def test_paper_account_needs_all_evidence():
    assert classify_account(_state()).kind == AccountKind.PAPER
    assert classify_account(_state(dom={"paperMarker": False, "tables": []})).kind == AccountKind.UNKNOWN
    assert classify_account(_state(accountMetaType="real")).kind == AccountKind.UNKNOWN
    assert classify_account(_state(accountMetaName="The Leap 2026")).kind == AccountKind.UNKNOWN
    assert classify_account(_state(connectStatus=0)).kind == AccountKind.UNKNOWN
    assert classify_account({"ok": False}).kind == AccountKind.UNKNOWN
    info = classify_account(_state(accountMetaName="Mein Konto"), paper_account_name="Anderes Konto")
    assert info.kind == AccountKind.UNKNOWN and info.problems


def test_live_account_and_lookalikes():
    live = _state(brokerId="Tradovate", brokerTitle="Tradovate", accountMetaType="real", dom={"tables": ["Tradovate.positions-table"]})
    assert classify_account(live).kind == AccountKind.LIVE
    assert classify_account({**live, "brokerId": "PaperX"}).kind == AccountKind.UNKNOWN
    assert classify_account({**live, "accountMetaType": "demo"}).kind == AccountKind.UNKNOWN  # Broker-Demokonto
    assert classify_account({**live, "dom": {"tables": ["Paper.positions-table"]}}).kind == AccountKind.UNKNOWN


def test_check_account_blocks_mismatch():
    paper = classify_account(_state())
    live = classify_account(_state(brokerId="IBKR", brokerTitle="Interactive Brokers", accountMetaType="real", dom={}))
    assert check_account(AccountKind.PAPER, paper)[0]
    assert check_account(AccountKind.LIVE, live)[0]
    ok, why = check_account(AccountKind.PAPER, live)
    assert not ok and "ECHTGELD" in why
    assert not check_account(AccountKind.LIVE, paper)[0]
    assert not check_account(AccountKind.UNKNOWN, paper)[0]


# ------------------------------------------------------------------ Order-Wunsch
def test_validate_request_and_brackets():
    good = OrderRequest("BINANCE:BTCUSDT", "buy", 0.01, take_profit=110, stop_loss=95)
    assert validate_request(good)[0]
    assert not validate_request(OrderRequest("BINANCE:BTCUSDT", "buy", 0))[0]
    assert not validate_request(OrderRequest("BINANCE:BTCUSDT", "buy", 1, take_profit=90, stop_loss=95))[0]
    assert not validate_request(OrderRequest("BTC USDT;", "buy", 1))[0]
    assert not validate_request(OrderRequest("BINANCE:BTCUSDT", "buy", 1, order_type="limit"))[0]
    assert check_brackets(good, {"ask": 100})[0]
    assert not check_brackets(good, {"ask": 112})[0]
    assert not check_brackets(good, None)[0]
    short = OrderRequest("BINANCE:BTCUSDT", "sell", 1, take_profit=90, stop_loss=105)
    assert check_brackets(short, {"bid": 100})[0] and not check_brackets(short, {"bid": 106})[0]


# ------------------------------------------------------------------ Ticket & Dialog
def test_check_ticket_reads_submit_button():
    req = OrderRequest("BINANCE:BTCUSDT", "buy", 0.5)
    assert check_ticket(req, "Buy 0.5 BINANCE:BTCUSDT MKT", 0.001)[0]
    assert check_ticket(req, "Kaufen 0,5 BINANCE:BTCUSDT MARKT", 0.001)[0]
    assert not check_ticket(req, "Sell 0.5 BINANCE:BTCUSDT MKT", 0.001)[0]
    assert not check_ticket(req, "Buy 5 BINANCE:BTCUSDT MKT", 0.001)[0]
    assert not check_ticket(req, "Buy 0.5 BINANCE:ETHUSDT MKT", 0.001)[0]
    assert not check_ticket(req, "Buy 0.5 BINANCE:BTCUSDT LMT", 0.001)[0]
    assert not check_ticket(req, "Start creating order", 0.001)[0]
    assert not check_ticket(OrderRequest("BINANCE:BTCUSDT", "buy", 1000), "Buy 1,000 BINANCE:BTCUSDT MKT", 1)[0]


def test_check_dialog():
    req = OrderRequest("BINANCE:BTCUSDT", "sell", 2)
    assert check_dialog("Sell 2 BINANCE:BTCUSDT MKT", req)[0]
    assert check_dialog("Bitte bestätigen: Verkaufen BTCUSDT Menge 2", req)[0]
    assert not check_dialog("Kaufen BTCUSDT Menge 2", req)[0]
    assert not check_dialog("Verkaufen ETHUSDT Menge 2", req)[0]
    assert not check_dialog("", req)[0]


# ------------------------------------------------------------------ Nach dem Absenden
def test_evaluate_fill_and_close():
    req = OrderRequest("BINANCE:BTCUSDT", "buy", 1, take_profit=110, stop_loss=95)
    before = {"positions": [], "orders": []}
    after = {
        "positions": [{"id": "p1", "symbol": "BINANCE:BTCUSDT", "side": labels.SIDE_BUY, "qty": 1, "avgPrice": 100,
                       "takeProfit": 110, "stopLoss": 95}],
        "orders": [{"id": "o1", "symbol": "BINANCE:BTCUSDT", "side": labels.SIDE_BUY, "status": labels.STATUS_FILLED,
                    "qty": 1, "avgPrice": 100}],
    }
    res = evaluate_fill(before, after, req, 1, 0.001, 0.01)
    assert res["status"] == "filled" and res["avg_price"] == 100 and res["brackets_ok"] is True
    rejected = {"positions": [], "orders": [{"id": "o2", "symbol": "BINANCE:BTCUSDT", "side": labels.SIDE_BUY,
                                             "status": labels.STATUS_REJECTED, "message": "zu wenig Guthaben"}]}
    assert evaluate_fill(before, rejected, req, 1)["status"] == "rejected"
    assert evaluate_fill(before, before, req, 1)["status"] == "none"
    assert evaluate_close(after, before, "BINANCE:BTCUSDT")["status"] == "closed"
