"""Realistischer Backtester: Ausführung zur nächsten Kerze, Gebühren, Slippage,
ATR-Stop-Loss, Take-Profit, Break-Even und Trailing-Stop – dieselbe Logik wie live."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..analysis import indicators as ind


@dataclass
class BacktestConfig:
    fee_pct: float = 0.1
    slippage_pct: float = 0.05
    stop_atr_mult: float = 2.0
    take_profit_r: float = 2.5
    trailing_atr_mult: float = 2.5
    trailing_activate_r: float = 1.0
    breakeven_at_r: float = 1.0
    allow_short: bool = True
    use_stops: bool = True
    bars_per_year: float = 8760.0  # 1h-Kerzen, 24/7

    @classmethod
    def from_settings(cls, risk: Any, allow_short: bool, bars_per_year: float) -> "BacktestConfig":
        return cls(
            fee_pct=risk.fee_pct,
            slippage_pct=risk.slippage_pct,
            stop_atr_mult=risk.stop_atr_mult,
            take_profit_r=risk.take_profit_r,
            trailing_atr_mult=risk.trailing_atr_mult,
            trailing_activate_r=risk.trailing_activate_r,
            breakeven_at_r=risk.breakeven_at_r,
            allow_short=allow_short,
            bars_per_year=bars_per_year,
        )


@dataclass
class BTTrade:
    direction: int
    entry_idx: int
    entry: float
    exit_idx: int = -1
    exit: float = 0.0
    ret_pct: float = 0.0
    r_multiple: float = 0.0
    reason: str = ""


@dataclass
class BacktestResult:
    metrics: dict[str, float]
    trades: list[BTTrade] = field(default_factory=list)
    equity: pd.Series | None = None


def run_backtest(
    df: pd.DataFrame,
    positions: pd.Series,
    cfg: BacktestConfig,
    start: int = 0,
    end: int | None = None,
    atr_series: pd.Series | None = None,
) -> BacktestResult:
    end = len(df) if end is None else min(end, len(df))
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a = (atr_series if atr_series is not None else ind.atr(df, 14)).to_numpy(float)
    target = positions.to_numpy(float)
    if not cfg.allow_short:
        target = np.where(target < 0, 0, target)
    cost = (cfg.fee_pct + cfg.slippage_pct) / 100.0

    equity = 1.0
    eq_curve = np.full(end - start, np.nan)
    trades: list[BTTrade] = []
    pos = 0
    entry = stop = tp = risk = 0.0
    eq_at_entry = 1.0
    blocked = 0  # nach Stop/TP: kein Wiedereinstieg in dieselbe Richtung bis Signal wechselt
    cur: BTTrade | None = None
    bars_in = 0

    def close_trade(i: int, price: float, reason: str) -> None:
        nonlocal pos, equity, cur
        assert cur is not None
        gross = pos * (price / entry - 1.0)
        ret = gross - 2 * cost
        equity = eq_at_entry * (1.0 + ret)
        cur.exit_idx, cur.exit, cur.ret_pct, cur.reason = i, price, ret * 100, reason
        cur.r_multiple = (pos * (price - entry) / risk) if risk > 0 else 0.0
        trades.append(cur)
        cur = None
        pos = 0

    for i in range(max(start, 1), end):
        sig = int(np.sign(target[i - 1])) if not np.isnan(target[i - 1]) else 0
        if blocked and sig != blocked:
            blocked = 0
        # 1) Signal-Ausstieg / Umkehr zur Eröffnung
        if pos != 0 and sig != pos:
            close_trade(i, o[i], "Signal")
        # 2) Einstieg zur Eröffnung
        if pos == 0 and sig != 0 and sig != blocked and not np.isnan(a[i - 1]) and a[i - 1] > 0:
            pos = sig
            entry = o[i]
            eq_at_entry = equity
            risk = a[i - 1] * cfg.stop_atr_mult
            stop = entry - pos * risk if cfg.use_stops else (0.0 if pos > 0 else math.inf)
            tp = entry + pos * risk * cfg.take_profit_r if (cfg.use_stops and cfg.take_profit_r > 0) else (math.inf if pos > 0 else 0.0)
            cur = BTTrade(direction=pos, entry_idx=i, entry=entry)
            bars_in = 0
        # 3) Stop / Take-Profit innerhalb der Kerze (Stop zuerst = konservativ)
        if pos != 0 and cfg.use_stops:
            if pos > 0:
                if o[i] <= stop and bars_in > 0:
                    close_trade(i, o[i], "Stop-Loss"); blocked = 1
                elif l[i] <= stop:
                    close_trade(i, stop, "Stop-Loss"); blocked = 1
                elif h[i] >= tp:
                    close_trade(i, max(tp, o[i]), "Take-Profit"); blocked = 1
            else:
                if o[i] >= stop and bars_in > 0:
                    close_trade(i, o[i], "Stop-Loss"); blocked = -1
                elif h[i] >= stop:
                    close_trade(i, stop, "Stop-Loss"); blocked = -1
                elif l[i] <= tp:
                    close_trade(i, min(tp, o[i]), "Take-Profit"); blocked = -1
        # 4) Break-Even / Trailing zum Schlusskurs
        if pos != 0 and cfg.use_stops and risk > 0:
            r_now = pos * (c[i] - entry) / risk
            if r_now >= cfg.breakeven_at_r:
                stop = max(stop, entry) if pos > 0 else min(stop, entry)
            if r_now >= cfg.trailing_activate_r and not np.isnan(a[i]):
                trail = c[i] - pos * a[i] * cfg.trailing_atr_mult
                stop = max(stop, trail) if pos > 0 else min(stop, trail)
            bars_in += 1
        # 5) Bewertung
        if pos != 0:
            eq_curve[i - start] = eq_at_entry * (1.0 + pos * (c[i] / entry - 1.0) - cost)
        else:
            eq_curve[i - start] = equity
    if pos != 0 and cur is not None:
        close_trade(end - 1, c[end - 1], "Ende")
        eq_curve[-1] = equity

    eq = pd.Series(eq_curve, index=df.index[start:end]).ffill().fillna(1.0)
    return BacktestResult(metrics=compute_metrics(eq, trades, cfg, c[start:end]), trades=trades, equity=eq)


def compute_metrics(eq: pd.Series, trades: list[BTTrade], cfg: BacktestConfig, closes: np.ndarray) -> dict[str, float]:
    n = len(trades)
    rets = eq.pct_change().dropna()
    sd = float(rets.std()) if len(rets) > 2 else 0.0
    sharpe = float(rets.mean() / sd * math.sqrt(cfg.bars_per_year)) if sd > 0 else 0.0
    downside = rets[rets < 0]
    dsd = float(downside.std()) if len(downside) > 2 else 0.0
    sortino = float(rets.mean() / dsd * math.sqrt(cfg.bars_per_year)) if dsd > 0 else 0.0
    peak = eq.cummax()
    max_dd = float(((eq - peak) / peak).min() * -100) if len(eq) else 0.0
    wins = [t for t in trades if t.ret_pct > 0]
    gross_win = sum(t.ret_pct for t in wins)
    gross_loss = -sum(t.ret_pct for t in trades if t.ret_pct < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else (10.0 if gross_win > 0 else 0.0)
    exposure = sum(max(0, t.exit_idx - t.entry_idx) for t in trades) / max(1, len(eq)) * 100
    bh = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 else 0.0
    return {
        "total_return_pct": float((eq.iloc[-1] - 1) * 100) if len(eq) else 0.0,
        "buy_hold_pct": float(bh),
        "trades": float(n),
        "win_rate_pct": float(len(wins) / n * 100) if n else 0.0,
        "profit_factor": float(min(pf, 10.0)),
        "avg_trade_pct": float(np.mean([t.ret_pct for t in trades])) if n else 0.0,
        "avg_r": float(np.mean([t.r_multiple for t in trades])) if n else 0.0,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "exposure_pct": float(exposure),
    }


def score(m: dict[str, float]) -> float:
    """Bewertung einer Strategie: risikoadjustiert und mit Mindestanzahl Trades."""
    n = m["trades"]
    if n < 3:
        return min(0.0, m["sharpe"]) * 0.5
    s = m["sharpe"] * min(1.0, math.sqrt(n / 12))
    if m["max_drawdown_pct"] > 25:
        s -= (m["max_drawdown_pct"] - 25) / 25
    if m["profit_factor"] < 1:
        s = min(s, 0.0) - (1 - m["profit_factor"])
    return float(s)
