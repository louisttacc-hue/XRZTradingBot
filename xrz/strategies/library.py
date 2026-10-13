"""Strategie-Bibliothek.

Jede Strategie liefert für jede Kerze eine Zielposition (+1 Long, -1 Short, 0 flach),
berechnet NUR aus Daten bis einschließlich dieser Kerze (kein Blick in die Zukunft).
Der Backtester führt Signale erst zur Eröffnung der NÄCHSTEN Kerze aus.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..analysis import indicators as ind


def state_machine(long_entry, long_exit, short_entry, short_exit) -> np.ndarray:
    le = np.asarray(pd.Series(long_entry).fillna(False), dtype=bool)
    lx = np.asarray(pd.Series(long_exit).fillna(False), dtype=bool)
    se = np.asarray(pd.Series(short_entry).fillna(False), dtype=bool)
    sx = np.asarray(pd.Series(short_exit).fillna(False), dtype=bool)
    pos = np.zeros(len(le))
    cur = 0
    for i in range(len(le)):
        if cur == 1 and lx[i]:
            cur = 0
        elif cur == -1 and sx[i]:
            cur = 0
        if cur <= 0 and le[i] and not se[i]:
            cur = 1
        elif cur >= 0 and se[i] and not le[i]:
            cur = -1
        pos[i] = cur
    return pos


@dataclass
class Strategy:
    name: str
    label: str
    description: str
    defaults: dict[str, Any]
    grid: dict[str, list[Any]]
    fn: Callable[[pd.DataFrame, dict[str, Any]], tuple[np.ndarray, str]]
    good_in: list[str] = field(default_factory=list)  # passende Marktphasen

    def positions(self, df: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.Series:
        p = {**self.defaults, **(params or {})}
        pos, _ = self.fn(df, p)
        return pd.Series(pos, index=df.index, dtype=float)

    def evaluate(self, df: pd.DataFrame, params: dict[str, Any] | None = None) -> tuple[pd.Series, str]:
        p = {**self.defaults, **(params or {})}
        pos, why = self.fn(df, p)
        return pd.Series(pos, index=df.index, dtype=float), why

    def param_combos(self, max_combos: int = 16) -> list[dict[str, Any]]:
        keys = list(self.grid)
        combos = [dict(zip(keys, vals)) for vals in itertools.product(*(self.grid[k] for k in keys))]
        combos = [c for c in combos if _valid(c)]
        if len(combos) > max_combos:
            step = len(combos) / max_combos
            combos = [combos[int(i * step)] for i in range(max_combos)]
        return combos or [dict(self.defaults)]


def _valid(p: dict[str, Any]) -> bool:
    if "fast" in p and "slow" in p and p["fast"] >= p["slow"]:
        return False
    if "entry_n" in p and "exit_n" in p and p["exit_n"] >= p["entry_n"]:
        return False
    return True


def _fmt(x: float, nd: int = 1) -> str:
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def _dir_word(v: float) -> str:
    return "LONG" if v > 0 else "SHORT" if v < 0 else "neutral"


# --------------------------------------------------------------------------- #
# 1) EMA-Trendfolge
# --------------------------------------------------------------------------- #
def _ema_trend(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    f, s, t = ind.ema(c, p["fast"]), ind.ema(c, p["slow"]), ind.ema(c, p["trend"])
    a = ind.adx(df)["adx"]
    pos = state_machine(
        (f > s) & (c > t) & (a > p["adx_min"]),
        f < s,
        (f < s) & (c < t) & (a > p["adx_min"]),
        f > s,
    )
    why = (
        f"EMA{p['fast']} {'über' if f.iloc[-1] > s.iloc[-1] else 'unter'} EMA{p['slow']}, "
        f"Kurs {'über' if c.iloc[-1] > t.iloc[-1] else 'unter'} EMA{p['trend']}, ADX {_fmt(a.iloc[-1])} → {_dir_word(pos[-1])}"
    )
    return pos, why


# --------------------------------------------------------------------------- #
# 2) MACD-Momentum
# --------------------------------------------------------------------------- #
def _macd_momentum(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    m = ind.macd(c, p["fast"], p["slow"], p["signal"])
    t = ind.ema(c, p["trend"])
    cross_up = (m["macd"] > m["signal"]) & (m["macd"].shift(1) <= m["signal"].shift(1))
    cross_dn = (m["macd"] < m["signal"]) & (m["macd"].shift(1) >= m["signal"].shift(1))
    pos = state_machine(cross_up & (c > t), m["macd"] < m["signal"], cross_dn & (c < t), m["macd"] > m["signal"])
    why = f"MACD-Histogramm {_fmt(m['hist'].iloc[-1], 4)}, Kurs {'über' if c.iloc[-1] > t.iloc[-1] else 'unter'} EMA{p['trend']} → {_dir_word(pos[-1])}"
    return pos, why


# --------------------------------------------------------------------------- #
# 3) RSI + Bollinger Mean-Reversion (nur in Seitwärtsphasen)
# --------------------------------------------------------------------------- #
def _rsi_reversion(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    r = ind.rsi(c, 14)
    bb = ind.bollinger(c, 20, 2.0)
    a = ind.adx(df)["adx"]
    ranging = a < p["adx_max"]
    pos = state_machine(
        (r < p["lower"]) & (c < bb["lower"]) & ranging,
        (r > 50) | (c > bb["mid"]),
        (r > p["upper"]) & (c > bb["upper"]) & ranging,
        (r < 50) | (c < bb["mid"]),
    )
    why = f"RSI {_fmt(r.iloc[-1])}, %B {_fmt(bb['pctb'].iloc[-1], 2)}, ADX {_fmt(a.iloc[-1])} → {_dir_word(pos[-1])}"
    return pos, why


# --------------------------------------------------------------------------- #
# 4) Bollinger-Squeeze-Ausbruch
# --------------------------------------------------------------------------- #
def _bb_squeeze(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    bb = ind.bollinger(c, 20, 2.0)
    thresh = bb["width"].rolling(p["lookback"], min_periods=50).quantile(p["q"])
    squeeze = (bb["width"] <= thresh).astype(float).rolling(6, min_periods=1).max() > 0
    vol_ok = df["volume"] > df["volume"].rolling(20).mean() * p["vol_mult"]
    pos = state_machine(
        squeeze & (c > bb["upper"]) & vol_ok,
        c < bb["mid"],
        squeeze & (c < bb["lower"]) & vol_ok,
        c > bb["mid"],
    )
    why = f"Bandbreite {_fmt(bb['width'].iloc[-1] * 100, 2)}% ({'Squeeze' if bool(squeeze.iloc[-1]) else 'kein Squeeze'}) → {_dir_word(pos[-1])}"
    return pos, why


# --------------------------------------------------------------------------- #
# 5) Donchian-/Turtle-Ausbruch
# --------------------------------------------------------------------------- #
def _donchian(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    ent = ind.donchian(df, p["entry_n"])
    ext = ind.donchian(df, p["exit_n"])
    pos = state_machine(c > ent["upper"], c < ext["lower"], c < ent["lower"], c > ext["upper"])
    why = f"{p['entry_n']}-Kerzen-Hoch {_fmt(ent['upper'].iloc[-1], 2)} / -Tief {_fmt(ent['lower'].iloc[-1], 2)} → {_dir_word(pos[-1])}"
    return pos, why


# --------------------------------------------------------------------------- #
# 6) Supertrend
# --------------------------------------------------------------------------- #
def _supertrend(df: pd.DataFrame, p: dict[str, Any]):
    st = ind.supertrend(df, p["n"], p["mult"])
    a = ind.adx(df)["adx"]
    d = st["direction"]
    pos = state_machine((d == 1) & (a > p["adx_min"]), d == -1, (d == -1) & (a > p["adx_min"]), d == 1)
    why = f"Supertrend {'grün' if d.iloc[-1] == 1 else 'rot'} bei {_fmt(st['line'].iloc[-1], 2)} → {_dir_word(pos[-1])}"
    return pos, why


# --------------------------------------------------------------------------- #
# 7) Trend-Pullback (Rücksetzer im Trend kaufen)
# --------------------------------------------------------------------------- #
def _trend_pullback(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    t = ind.ema(c, p["trend"])
    r = ind.rsi(c, 14)
    rising = t > t.shift(5)
    falling = t < t.shift(5)
    up_cross = (r.shift(1) < p["dip"]) & (r >= p["dip"])
    dn_cross = (r.shift(1) > 100 - p["dip"]) & (r <= 100 - p["dip"])
    pos = state_machine(
        (c > t) & rising & up_cross,
        (r > 75) | (c < t * 0.995),
        (c < t) & falling & dn_cross,
        (r < 25) | (c > t * 1.005),
    )
    why = f"EMA{p['trend']} {'steigt' if bool(rising.iloc[-1]) else 'fällt' if bool(falling.iloc[-1]) else 'flach'}, RSI {_fmt(r.iloc[-1])} → {_dir_word(pos[-1])}"
    return pos, why


STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in [
        Strategy(
            "ema_trend", "EMA-Trendfolge",
            "Folgt dem Trend, wenn schnelle EMA über langsamer EMA liegt und ADX einen echten Trend bestätigt.",
            {"fast": 12, "slow": 26, "trend": 200, "adx_min": 20},
            {"fast": [9, 12, 20], "slow": [26, 50], "trend": [200], "adx_min": [15, 22]},
            _ema_trend, ["aufwärts", "abwärts"],
        ),
        Strategy(
            "macd_momentum", "MACD-Momentum",
            "Steigt ein, wenn MACD die Signallinie in Trendrichtung kreuzt.",
            {"fast": 12, "slow": 26, "signal": 9, "trend": 200},
            {"fast": [8, 12], "slow": [21, 26], "signal": [9], "trend": [100, 200]},
            _macd_momentum, ["aufwärts", "abwärts"],
        ),
        Strategy(
            "rsi_reversion", "RSI-Rückkehr zur Mitte",
            "Kauft Überverkauft / verkauft Überkauft – nur in Seitwärtsphasen (niedriger ADX).",
            {"lower": 30, "upper": 70, "adx_max": 25},
            {"lower": [25, 30], "upper": [70, 75], "adx_max": [25, 35]},
            _rsi_reversion, ["seitwärts"],
        ),
        Strategy(
            "bb_squeeze", "Bollinger-Squeeze-Ausbruch",
            "Handelt den Ausbruch nach einer Phase sehr geringer Volatilität (mit Volumenbestätigung).",
            {"lookback": 150, "q": 0.2, "vol_mult": 1.0},
            {"lookback": [100, 200], "q": [0.15, 0.25], "vol_mult": [1.0, 1.3]},
            _bb_squeeze, ["seitwärts"],
        ),
        Strategy(
            "donchian", "Donchian-/Turtle-Ausbruch",
            "Kauft neue N-Kerzen-Hochs, verkauft neue N-Kerzen-Tiefs (klassisches Turtle-System).",
            {"entry_n": 20, "exit_n": 10},
            {"entry_n": [20, 40, 55], "exit_n": [10, 20]},
            _donchian, ["aufwärts", "abwärts"],
        ),
        Strategy(
            "supertrend", "Supertrend",
            "ATR-basierter Trendfilter; Richtung wechselt, wenn der Kurs das Band durchbricht.",
            {"n": 10, "mult": 3.0, "adx_min": 15},
            {"n": [10, 14], "mult": [2.5, 3.0, 4.0], "adx_min": [0, 18]},
            _supertrend, ["aufwärts", "abwärts"],
        ),
        Strategy(
            "trend_pullback", "Trend-Pullback",
            "Kauft Rücksetzer in einem intakten Aufwärtstrend, wenn der RSI wieder dreht.",
            {"trend": 50, "dip": 40},
            {"trend": [50, 100], "dip": [35, 40, 45]},
            _trend_pullback, ["aufwärts", "abwärts"],
        ),
    ]
}


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"Unbekannte Strategie: {name}")
    return STRATEGIES[name]
