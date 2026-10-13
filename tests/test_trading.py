import time

import pytest

from conftest import FakeRouter
from xrz.storage import Trade


def _setup(ctx, review=None):
    fake = FakeRouter(review) if review else FakeRouter()
    ctx.brain.router = fake
    ctx.kb.router = None
    return fake


async def test_paper_broker_accounting(ctx):
    paper = ctx.engine.paper
    start = await paper.cash()
    fill = await paper.open("BTC/USDT", "long", 0.01, 50_000)
    t = Trade(mode="paper", symbol="BTC/USDT", side="long", qty=fill.qty, entry_price=fill.price, entry_time=time.time(), fees=fill.fee)
    eq = await paper.equity([t], {"BTC/USDT": 50_000})
    assert eq == pytest.approx(start - fill.fee - fill.qty * (fill.price - 50_000), rel=1e-9)
    close = await paper.close(t, 55_000)
    assert await paper.cash() > start  # Gewinn trotz Gebühren
    # Short: Gewinn wenn Kurs fällt
    f2 = await paper.open("ETH/USDT", "short", 1, 3000)
    s = Trade(mode="paper", symbol="ETH/USDT", side="short", qty=1, entry_price=f2.price, entry_time=time.time())
    before = await paper.cash()
    await paper.close(s, 2700)
    assert await paper.cash() - before > 2700  # Sicherheit + Gewinn zurück
    assert close.price < 55_000  # Slippage zu unseren Ungunsten


async def test_paper_broker_rejects_overspend(ctx):
    from xrz.trading.brokers import BrokerError

    with pytest.raises(BrokerError):
        await ctx.engine.paper.open("BTC/USDT", "long", 10, 50_000)


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
    # Live: harte Obergrenze pro Order, keine Shorts
    d = risk.check_entry(mode="live", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000, cash=10_000, open_trades=[])
    assert d.notional <= s.risk.max_live_order_usd + 1e-9
    assert not risk.check_entry(mode="live", symbol="BTC/USDT", direction=-1, price=100, stop=102, equity=10_000, cash=10_000, open_trades=[]).ok
    assert not risk.check_entry(mode="off", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000, cash=10_000, open_trades=[]).ok
    # max. offene Positionen
    open_t = [Trade(mode="paper", symbol=f"X{i}/USDT", side="long", qty=1, entry_price=1, entry_time=0) for i in range(3)]
    assert not risk.check_entry(mode="paper", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=10_000, cash=10_000, open_trades=open_t).ok


def test_daily_loss_limit_and_drawdown(ctx):
    risk = ctx.engine.risk
    risk.update_equity_marks("paper", 10_000)
    d = risk.check_entry(mode="paper", symbol="BTC/USDT", direction=1, price=100, stop=98, equity=9_600, cash=9_600, open_trades=[])
    assert not d.ok and "Tagesverlust" in d.reason
    assert risk.update_equity_marks("paper", 8_000) is not None  # 20 % Drawdown → Notbremse


def test_trailing_stop_only_tightens(ctx):
    risk = ctx.engine.risk
    t = Trade(mode="paper", symbol="BTC/USDT", side="long", qty=1, entry_price=100, entry_time=0, stop=96, initial_stop=96)
    assert risk.trail(t, 101, 1) is None  # < 1R: nichts
    new = risk.trail(t, 108, 1)  # +2R → Break-Even + Trailing
    assert new is not None and new > 100
    t.stop = new
    assert risk.trail(t, 105, 1) is None  # Kurs fällt → Stop bleibt


async def test_engine_full_paper_cycle(ctx):
    fake = _setup(ctx)
    ctx.cfg.settings.trading.entry_threshold = 0.1
    ctx.cfg.settings.trading.min_active_strategies = 1
    ctx.cfg.settings.trading.max_extension_atr = 99
    await ctx.engine.set_mode("paper")
    res = await ctx.engine.scan_once()
    assert len(res) == 3
    opened = ctx.storage.open_trades("paper")
    assert opened, "Bei so niedriger Schwelle sollte mind. ein Trade eröffnet werden"
    assert any(c[0] == "complete" for c in fake.calls)  # KI hat geprüft
    t = opened[0]
    assert t.stop and t.take_profit and t.entry_reason
    # Stop auslösen
    t.stop = 10**9 if t.side == "long" else 1e-9
    ctx.storage.update_trade(t)
    await ctx.engine.manage_once()
    closed = ctx.storage.get_trade(t.id)
    assert closed.status == "closed" and closed.pnl is not None and closed.exit_reason
    st = await ctx.engine.status()
    assert st["mode"] == "paper" and "paper" in st
    text = await ctx.engine.status_text()
    assert "Paper" in text


async def test_ai_can_veto_trade(ctx):
    _setup(ctx, review='{"approve": false, "reason": "gegen den Trend"}')
    ctx.cfg.settings.trading.entry_threshold = 0.1
    ctx.cfg.settings.trading.min_active_strategies = 1
    ctx.cfg.settings.trading.max_extension_atr = 99
    await ctx.engine.set_mode("paper")
    await ctx.engine.scan_once()
    assert ctx.storage.open_trades("paper") == []


async def test_no_trading_when_off_or_paused(ctx):
    _setup(ctx)
    ctx.cfg.settings.trading.entry_threshold = 0.1
    ctx.cfg.settings.trading.min_active_strategies = 1
    await ctx.engine.set_mode("off")
    await ctx.engine.scan_once()
    assert ctx.storage.open_trades() == []
    await ctx.engine.set_mode("paper")
    await ctx.engine.pause()
    await ctx.engine.scan_once()
    assert ctx.storage.open_trades() == []


async def test_live_requires_unlock_and_code(ctx):
    msg = await ctx.engine.request_live()
    assert "gesperrt" in msg
    with pytest.raises(PermissionError):
        await ctx.engine.set_mode("live")
    assert "keine Live-Anfrage" in await ctx.engine.confirm_live("123456")
    ctx.cfg.settings.live_trading_unlocked = True
    msg = await ctx.engine.request_live()  # Demo-Daten → abgelehnt
    assert "Demo" in msg
    assert ctx.engine.mode != "live"


async def test_tradingview_webhook_flow(ctx):
    _setup(ctx)
    r = await ctx.engine.handle_tradingview({"symbol": "BTCUSDT", "action": "buy"})
    assert not r["ok"]  # keine Passphrase gesetzt
    ctx.cfg.secrets.set("tradingview_passphrase", "geheim123")
    r = await ctx.engine.handle_tradingview({"passphrase": "falsch", "symbol": "BTCUSDT", "action": "buy"})
    assert not r["ok"]
    r = await ctx.engine.handle_tradingview({"passphrase": "geheim123", "symbol": "BINANCE:BTCUSDT", "action": "buy"})
    assert r["ok"]
    assert ctx.storage.latest_signal("tradingview", "BTC/USDT", 60)["action"] == "buy"
    a = ctx.engine.last_analyses["BTC/USDT"]
    assert a.tv_signal.startswith("BUY")


async def test_panic_closes_everything(ctx):
    _setup(ctx)
    await ctx.engine.set_mode("paper")
    fill = await ctx.engine.paper.open("BTC/USDT", "long", 0.01, 60_000)
    ctx.storage.insert_trade(Trade(mode="paper", symbol="BTC/USDT", side="long", qty=fill.qty, entry_price=fill.price,
                                   entry_time=time.time(), stop=50_000, initial_stop=50_000))
    msg = await ctx.engine.panic()
    assert "PANIK" in msg
    assert ctx.storage.open_trades() == [] and ctx.engine.mode == "off"


async def test_reflection_writes_lesson(ctx):
    _setup(ctx)
    t = Trade(mode="paper", symbol="BTC/USDT", side="long", qty=1, entry_price=100, entry_time=time.time() - 3600,
              exit_price=95, exit_time=time.time(), pnl=-5, r_multiple=-1, status="closed", exit_reason="Stop-Loss")
    ctx.storage.insert_trade(t)
    await ctx.brain.reflect_on_trade(t)
    assert any("Test-Lektion" in l for l in ctx.kb.recent_lessons(3))
