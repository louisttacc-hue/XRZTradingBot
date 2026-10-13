import numpy as np
import pandas as pd
import pytest

from xrz.analysis import indicators as ind
from xrz.analysis.regime import detect_regime, support_resistance
from xrz.backtest.engine import BacktestConfig, run_backtest, score
from xrz.backtest.optimizer import ensemble_backtest, optimize
from xrz.market.data import DemoMarket, normalize_symbol, to_tradingview_symbol
from xrz.strategies import STRATEGIES

NOW = 1_790_000_000.0


@pytest.fixture(scope="module")
def df():
    return DemoMarket().ohlcv("BTC/USDT", "1h", 900, now=NOW)


def test_demo_market_is_deterministic(df):
    again = DemoMarket().ohlcv("BTC/USDT", "1h", 900, now=NOW)
    pd.testing.assert_frame_equal(df, again)
    assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()


def test_normalize_symbol():
    wl = ["BTC/USDT", "ETH/EUR"]
    assert normalize_symbol("BINANCE:BTCUSDT", wl) == "BTC/USDT"
    assert normalize_symbol("eth", wl) == "ETH/EUR"
    assert normalize_symbol("SOLUSDT.P") == "SOL/USDT"
    assert normalize_symbol("AAPL") == "AAPL"
    assert to_tradingview_symbol("BTC/USDT", "BINANCE") == "BINANCE:BTCUSDT"


def test_rsi_bounds_and_known_values():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up, 14).dropna().iloc[-1] == pytest.approx(100.0)
    s = pd.Series(np.sin(np.linspace(0, 20, 300)) * 10 + 100)
    r = ind.rsi(s).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_indicators_have_no_lookahead(df):
    full = ind.add_all(df)
    part = ind.add_all(df.iloc[:600])
    cols = ["ema20", "rsi", "macd", "atr", "adx", "bb_upper", "supertrend_dir"]
    pd.testing.assert_frame_equal(full[cols].iloc[:600], part[cols], check_exact=False, rtol=1e-9)


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_strategies_no_lookahead(df, name):
    strat = STRATEGIES[name]
    full = strat.positions(df)
    for cut in (400, 650):
        part = strat.positions(df.iloc[:cut])
        assert (full.iloc[:cut].to_numpy() == part.to_numpy()).all(), f"{name} nutzt Zukunftsdaten"
    assert set(np.unique(full)) <= {-1.0, 0.0, 1.0}


def test_regime_and_levels(df):
    r = detect_regime(df)
    assert r.trend in ("aufwärts", "abwärts", "seitwärts")
    lv = support_resistance(df)
    price = df["close"].iloc[-1]
    assert all(x < price for x in lv["support"]) and all(x > price for x in lv["resistance"])


def test_backtest_costs_and_stops(df):
    cfg = BacktestConfig(allow_short=False)
    always_long = pd.Series(1.0, index=df.index)
    res = run_backtest(df, always_long, cfg, 200)
    assert res.metrics["trades"] >= 1
    for t in res.trades:
        assert t.exit_idx >= t.entry_idx
        if t.reason == "Stop-Loss":
            assert t.r_multiple <= 0.05  # Stop-Verlust ≈ −1R (plus Kosten), nie großer Gewinn
    # Ohne Signal kein Trade und keine Kosten
    flat = run_backtest(df, pd.Series(0.0, index=df.index), cfg, 200)
    assert flat.metrics["trades"] == 0 and flat.metrics["total_return_pct"] == 0


def test_backtest_short_disabled(df):
    cfg = BacktestConfig(allow_short=False)
    res = run_backtest(df, pd.Series(-1.0, index=df.index), cfg, 200)
    assert res.metrics["trades"] == 0


def test_score_penalizes_few_trades():
    m = {"trades": 1, "sharpe": 5.0, "max_drawdown_pct": 1, "profit_factor": 3}
    assert score(m) == 0.0
    bad = {"trades": 20, "sharpe": 1.0, "max_drawdown_pct": 5, "profit_factor": 0.7}
    assert score(bad) < 0


def test_optimizer_weights_normalized(df):
    reps = optimize(df, BacktestConfig(allow_short=False), max_combos=4)
    assert set(reps) == set(STRATEGIES)
    total = sum(r.weight for r in reps.values())
    assert total == pytest.approx(1.0) or total == 0
    ens = ensemble_backtest(df, reps, BacktestConfig(allow_short=False))
    assert "total_return_pct" in ens
