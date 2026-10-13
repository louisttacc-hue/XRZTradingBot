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


def _new_indicators(d: pd.DataFrame) -> pd.DataFrame:
    c = d["close"]
    out = {
        "wma": ind.wma(c, 10), "hma": ind.hma(c, 21), "kama": ind.kama(c, 10), "roc": ind.roc(c, 9),
        "linreg": ind.linreg(c, 20), "cci": ind.cci(d, 20), "wr": ind.williams_r(d, 14), "mfi": ind.mfi(d, 14),
        "coppock": ind.coppock(c), "sh": ind.swing_highs(d["high"], 3), "sl": ind.swing_lows(d["low"], 5),
    }
    frames = {
        "aroon": ind.aroon(d, 25), "vortex": ind.vortex(d, 14), "srsi": ind.stoch_rsi(c), "kc": ind.keltner(d),
        "trix": ind.trix(c), "ha": ind.heikin_ashi(d), "psar": ind.parabolic_sar(d), "ichi": ind.ichimoku(d),
        "sqz": ind.squeeze_momentum(d).astype(float), "ce": ind.chandelier_exit(d), "pp": ind.prior_periods(d, 24, 2),
        "piv": ind.pivot_points(d, 24),
    }
    for prefix, f in frames.items():
        for col in f.columns:
            out[f"{prefix}_{col}"] = f[col]
    return pd.DataFrame(out, index=d.index)


def test_new_indicators_have_no_lookahead(df):
    full = _new_indicators(df)
    for cut in (300, 611):
        part = _new_indicators(df.iloc[:cut])
        pd.testing.assert_frame_equal(full.iloc[:cut], part, check_exact=True)


def test_new_indicators_match_naive_formulas(df):
    c = df["close"]
    w = np.arange(1, 11)
    naive_wma = c.rolling(10).apply(lambda x: (x * w).sum() / w.sum(), raw=True)
    assert np.nanmax(np.abs(ind.wma(c, 10) - naive_wma)) < 1e-6

    def lr_end(x):
        b, a = np.polyfit(np.arange(len(x)), x, 1)
        return a + b * (len(x) - 1)

    assert np.nanmax(np.abs(ind.linreg(c, 20) - c.rolling(20).apply(lr_end, raw=True))) < 1e-6
    tp = (df["high"] + df["low"] + df["close"]) / 3
    md = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    naive_cci = (tp - tp.rolling(20).mean()) / (0.015 * md)
    assert np.nanmax(np.abs(ind.cci(df, 20) - naive_cci)) < 1e-6
    naive_up = df["high"].rolling(26).apply(lambda x: 100 * np.argmax(x) / 25, raw=True)
    assert np.nanmax(np.abs(ind.aroon(df, 25)["up"] - naive_up)) == 0
    wr = ind.williams_r(df).dropna()
    assert ((wr >= -100) & (wr <= 0)).all()
    m = ind.mfi(df).dropna()
    assert ((m >= 0) & (m <= 100)).all()
    # monoton steigend → Aroon-Up 100, ROC positiv, Hull-MA steigt
    up = pd.DataFrame({"high": np.arange(1.0, 81) + 0.5, "low": np.arange(1.0, 81) - 0.5, "close": np.arange(1.0, 81)})
    assert ind.aroon(up, 14)["up"].iloc[-1] == 100 and ind.aroon(up, 14)["down"].iloc[-1] == 0
    assert ind.roc(up["close"], 5).iloc[-1] > 0
    assert ind.hma(up["close"], 16).diff().dropna().gt(0).all()


def test_heikin_ashi_psar_ichimoku_semantics(df):
    ha = ind.heikin_ashi(df)
    o, h, l, c = (df[k] for k in ("open", "high", "low", "close"))
    assert ha["close"].iloc[10] == pytest.approx((o.iloc[10] + h.iloc[10] + l.iloc[10] + c.iloc[10]) / 4)
    assert ha["open"].iloc[10] == pytest.approx((ha["open"].iloc[9] + ha["close"].iloc[9]) / 2)
    assert (ha["high"] >= ha[["open", "close"]].max(axis=1)).all()
    ps = ind.parabolic_sar(df)
    d, sar = ps["direction"].to_numpy(), ps["sar"].to_numpy()
    assert set(np.unique(d[2:])) <= {-1.0, 1.0}
    assert (sar[d == 1] <= l.to_numpy()[d == 1] + 1e-9).all()
    assert (sar[d == -1] >= h.to_numpy()[d == -1] - 1e-9).all()
    ic = ind.ichimoku(df, 9, 26, 52, 26)
    pd.testing.assert_series_equal(ic["span_a"], ic["lead_a"].shift(25), check_names=False)
    assert "chikou" not in ic.columns


def test_swing_points_only_after_confirmation():
    high = pd.Series([1, 2, 3, 9, 3, 2, 1, 1, 1], dtype=float)
    sh = ind.swing_highs(high, 3)
    assert sh.notna().sum() == 1 and sh.iloc[6] == 9  # Hoch bei Index 3, bestätigt erst bei Index 6
    assert ind.swing_highs(high.iloc[:6], 3).notna().sum() == 0
    low = -high
    assert ind.swing_lows(low, 3).iloc[6] == -9


def test_prior_periods_use_previous_completed_period(df):
    pp = ind.prior_periods(df, 24, 1)
    day = df.index.floor("D")
    daily = df.groupby(day).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"), open=("open", "first"))
    expect = daily.shift(1).reindex(day)
    valid = pp["high"].notna().to_numpy()
    assert valid.sum() > 500
    for col in ("high", "low", "close"):
        assert np.allclose(pp[col].to_numpy()[valid], expect[col].to_numpy()[valid])
    cur_open = daily["open"].reindex(day).to_numpy()
    assert np.allclose(pp["open"].to_numpy()[valid], cur_open[valid])
    assert pp["high"].iloc[:24].isna().all()  # erste (unvollständige) Periode wird nicht verwendet
    piv = ind.pivot_points(df, 24).dropna()
    assert (piv["r2"] >= piv["r1"]).all() and (piv["r1"] >= piv["s1"]).all() and (piv["s1"] >= piv["s2"]).all()


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
