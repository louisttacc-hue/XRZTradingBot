import time

import pytest

from conftest import FakeRouter, loosen
from xrz.ai.brain import classify_confirmation
from xrz.storage import Trade
from xrz.trading.engine import _round_qty


async def _first_proposal(ctx):
    loosen(ctx)
    await ctx.engine.scan_once()
    props = ctx.engine.list_proposals(True)
    assert props, "Bei so niedriger Schwelle sollte ein Vorschlag entstehen"
    return props[0]


# ----------------------------------------------------------------- Risiko
def test_risk_sizing_and_limits(ctx):
    risk = ctx.engine.risk
    s = ctx.cfg.settings
    s.risk.risk_per_trade_pct = 1.0
    s.risk.max_position_pct = 100
    d = risk.check_entry(mode="paper", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000, cash=10_000, open_trades=[])
    assert d.ok and d.qty == pytest.approx(50) and d.risk_amount == pytest.approx(100)
    s.risk.max_position_pct = 25
    d = risk.check_entry(mode="paper", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000, cash=10_000, open_trades=[])
    assert d.notional == pytest.approx(2500)
    d = risk.check_entry(mode="live", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000, cash=10_000, open_trades=[])
    assert d.notional <= s.risk.max_live_order_usd + 1e-9
    s.trading.allow_short = False
    assert not risk.check_entry(mode="paper", symbol="BTC/USDT", direction=-1, price=100, stop=102, equity=10_000,
                                cash=10_000, open_trades=[]).ok
    open_t = [Trade(mode="paper", symbol=f"X{i}/USDT", side="long", qty=1, entry_price=1, entry_time=0) for i in range(3)]
    assert not risk.check_entry(mode="paper", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000,
                                cash=10_000, open_trades=open_t).ok


def test_daily_loss_limit_and_drawdown(ctx):
    risk = ctx.engine.risk
    risk.update_equity_marks("paper", 10_000)
    d = risk.check_entry(mode="paper", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=9_600, cash=9_600, open_trades=[])
    assert not d.ok and "Tagesverlust" in d.reason
    assert risk.update_equity_marks("paper", 8_000) is not None


def test_round_qty():
    assert _round_qty(0) == 0
    assert _round_qty(1.23456789) == pytest.approx(1.23457)
    assert _round_qty(0.000123456789) == pytest.approx(0.000123457)
    assert _round_qty(12345.678) == pytest.approx(12345.7, rel=1e-6)


# ------------------------------------------------------------ Vorschläge
async def test_paper_ask_mode_waits_for_approval(ctx):
    await ctx.engine.set_mode("paper")
    p = await _first_proposal(ctx)
    assert p["status"] == "pending" and p["account_kind"] == "paper"
    assert ctx.tradingview.orders == []  # nichts ohne „Ja“
    assert p["stop_loss"] and p["take_profit"] and p["tv_symbol"].startswith("BINANCE:")
    text = await ctx.engine.approve(p["id"], source="test")
    assert "ausgeführt" in text
    req, expected = ctx.tradingview.orders[0]
    assert expected.value == "paper" and req.stop_loss and req.take_profit
    trades = ctx.storage.open_trades("paper")
    assert len(trades) == 1 and trades[0].meta["tv_symbol"] == p["tv_symbol"]
    assert any(v.startswith("Ich würde") or "Soll ich" in v for v in ctx.voice.said)  # Vorschlag wurde angesagt


async def test_paper_auto_mode_executes(ctx):
    await ctx.engine.set_autonomy("auto")
    await ctx.engine.set_mode("paper")
    loosen(ctx)
    await ctx.engine.scan_once()
    assert ctx.tradingview.orders, "Im Auto-Modus (Paper) wird direkt ausgeführt"


async def test_watch_mode_never_executes(ctx):
    await ctx.engine.set_mode("watch")
    p = await _first_proposal(ctx)
    assert p["account_kind"] == "watch"
    msg = await ctx.engine.approve(p["id"])
    assert "Beobachten" in msg and ctx.tradingview.orders == []


async def test_wrong_account_aborts(ctx):
    await ctx.engine.set_mode("paper")
    p = await _first_proposal(ctx)
    ctx.tradingview.kind = "live"  # Nutzer hat in TradingView auf Echtgeld umgeschaltet
    msg = await ctx.engine.approve(p["id"])
    assert "Nicht ausgeführt" in msg and ctx.tradingview.orders == []
    assert ctx.engine.proposals[p["id"]].status == "failed"
    assert ctx.storage.open_trades() == []


async def test_reject_and_expiry(ctx):
    await ctx.engine.set_mode("paper")
    p = await _first_proposal(ctx)
    assert "lasse" in await ctx.engine.reject(p["id"])
    assert ctx.engine.list_proposals(True) == []
    p2 = next(iter(ctx.engine.proposals.values()))
    p2.status, p2.expires = "pending", time.time() - 1
    assert "abgelaufen" in await ctx.engine.approve(p2.id)


async def test_ai_veto_blocks_proposal(ctx):
    ctx.brain.router = FakeRouter(review='{"approve": false, "reason": "gegen den Trend"}')
    await ctx.engine.set_mode("paper")
    loosen(ctx)
    await ctx.engine.scan_once()
    assert ctx.engine.list_proposals(True) == []


async def test_off_and_paused_make_no_proposals(ctx):
    loosen(ctx)
    await ctx.engine.set_mode("off")
    await ctx.engine.scan_once(list(ctx.cfg.settings.trading.watchlist))
    assert ctx.engine.list_proposals(True) == []
    await ctx.engine.set_mode("paper")
    await ctx.engine.pause()
    await ctx.engine.scan_once()
    assert ctx.engine.list_proposals(True) == []


async def test_live_requires_unlock_code_and_live_account(ctx):
    assert "gesperrt" in (await ctx.engine.request_live())["text"]
    with pytest.raises(PermissionError):
        await ctx.engine.set_mode("live")
    ctx.cfg.settings.live_trading_unlocked = True
    ctx.cfg.settings.exchange.data_source = "exchange"
    res = await ctx.engine.request_live()
    assert not res["needs_confirmation"] and "kein Echtgeld-Broker" in res["text"]  # TradingView zeigt Paper
    ctx.tradingview.kind = "live"
    res = await ctx.engine.request_live()
    assert res["needs_confirmation"]
    assert "Falscher Code" in await ctx.engine.confirm_live("000000" if res["code"] != "000000" else "111111")
    assert "AKTIV" in await ctx.engine.confirm_live(res["code"])
    assert ctx.engine.mode == "live"
    assert "immer" in await ctx.engine.set_autonomy("auto")
    assert ctx.engine.autonomy == "ask"


async def test_reconcile_closes_journal_when_tradingview_closed(ctx):
    await ctx.engine.set_mode("paper")
    p = await _first_proposal(ctx)
    await ctx.engine.approve(p["id"])
    ctx.tradingview.held.clear()  # TradingView hat per Stop/Ziel geschlossen
    await ctx.engine.manage_once()
    t = ctx.storage.closed_trades("paper")[0]
    assert t.status == "closed" and "TradingView" in t.exit_reason and t.pnl is not None


async def test_close_symbol_and_panic(ctx):
    await ctx.engine.set_mode("paper")
    p = await _first_proposal(ctx)
    await ctx.engine.approve(p["id"])
    text = await ctx.engine.close_symbol(p["symbol"])
    assert "geschlossen" in text and ctx.storage.open_trades() == []
    p2 = await _first_proposal(ctx)
    await ctx.engine.approve(p2["id"])
    msg = await ctx.engine.panic()
    assert "PANIK" in msg and ctx.storage.open_trades() == [] and ctx.engine.mode == "off"


async def test_tradingview_webhook(ctx):
    r = await ctx.engine.handle_tradingview({"symbol": "BTCUSDT", "action": "buy"})
    assert not r["ok"]
    ctx.cfg.secrets.set("tradingview_passphrase", "geheim123")
    assert not (await ctx.engine.handle_tradingview({"passphrase": "x", "symbol": "BTCUSDT", "action": "buy"}))["ok"]
    r = await ctx.engine.handle_tradingview({"passphrase": "geheim123", "symbol": "BINANCE:BTCUSDT", "action": "buy"})
    assert r["ok"] and ctx.storage.latest_signal("tradingview", "BTC/USDT", 60)["action"] == "buy"


# ---------------------------------------------------- Bestätigung per Sprache
@pytest.mark.parametrize("text,expected", [
    ("Ja", "yes"), ("ja, mach", "yes"), ("Okay", "yes"), ("Mach es", "yes"), ("ausführen", "yes"), ("Ja, mit Echtgeld", "yes"),
    ("Nein", "no"), ("nee lass mal", "no"), ("Stopp", "no"), ("abbrechen", "no"),
    ("Ja, aber wie steht eigentlich der Euro gegen den Dollar heute?", None), ("Was meinst du?", None),
    ("neue Frage zu Bitcoin", None), ("golden cross?", None), ("", None),
])
def test_classify_confirmation(text, expected):
    assert classify_confirmation(text) == expected


async def test_chat_yes_executes_paper_proposal(ctx):
    await ctx.engine.set_mode("paper")
    await _first_proposal(ctx)
    reply = await ctx.brain.chat("app", "Ja, mach", source="text")
    assert "ausgeführt" in reply and ctx.tradingview.orders


async def test_chat_live_needs_echtgeld_phrase(ctx):
    ctx.cfg.settings.live_trading_unlocked = True
    ctx.cfg.settings.exchange.data_source = "exchange"
    ctx.tradingview.kind = "live"
    res = await ctx.engine.request_live()
    await ctx.engine.confirm_live(res["code"])
    ctx.cfg.settings.exchange.data_source = "demo"
    p = await _first_proposal(ctx)
    assert p["account_kind"] == "live"
    reply = await ctx.brain.chat("voice", "Ja", source="voice")
    assert "Echtgeld" in reply and ctx.tradingview.orders == []
    reply = await ctx.brain.chat("voice", "Ja, mit Echtgeld", source="voice")
    assert "ausgeführt" in reply and ctx.tradingview.orders[0][1].value == "live"


async def test_chat_no_rejects_and_stop_pauses(ctx):
    await ctx.engine.set_mode("paper")
    await _first_proposal(ctx)
    assert "lasse" in await ctx.brain.chat("app", "Nein", source="text")
    assert ctx.tradingview.orders == []
    reply = await ctx.brain.chat("app", "Stopp", source="text")
    assert ctx.engine.entries_paused and ctx.voice.stopped >= 1 and reply


async def test_chat_without_pending_goes_to_llm_and_remembers(ctx):
    reply = await ctx.brain.chat("app", "Wie ist der Status?", source="text")
    assert reply.startswith("Status:")
    assert ctx.storage.chat_history("app", 4)[-1]["content"] == reply


async def test_reflection_writes_lesson(ctx):
    t = Trade(mode="paper", symbol="BTC/USDT", side="long", qty=1, entry_price=100, entry_time=time.time() - 3600,
              exit_price=95, exit_time=time.time(), pnl=-5, r_multiple=-1, status="closed", exit_reason="Stop-Loss")
    ctx.storage.insert_trade(t)
    await ctx.brain.reflect_on_trade(t)
    assert any("Test-Lektion" in l for l in ctx.memory.recent_lessons(3))
