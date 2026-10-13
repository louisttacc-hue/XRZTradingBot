"""Marktphase (Trend/Seitwärts/Volatil) sowie Unterstützungen & Widerstände."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Regime:
    trend: str  # "aufwärts" | "abwärts" | "seitwärts"
    strength: float  # 0..1
    volatility: str  # "niedrig" | "normal" | "hoch"
    vol_percentile: float
    adx: float
    label: str
    details: dict[str, float] = field(default_factory=dict)

    @property
    def trend_dir(self) -> int:
        return {"aufwärts": 1, "abwärts": -1}.get(self.trend, 0)


def detect_regime(df: pd.DataFrame) -> Regime:
    c = df["close"]
    e50 = ind.ema(c, 50)
    e200 = ind.ema(c, min(200, max(50, len(df) // 2)))
    a = ind.adx(df)["adx"]
    atr_pct = ind.atr(df) / c
    last = len(df) - 1
    adx_v = float(a.iloc[last]) if not np.isnan(a.iloc[last]) else 0.0
    slope = float((e50.iloc[last] - e50.iloc[max(0, last - 10)]) / c.iloc[last]) if last >= 10 else 0.0
    above = c.iloc[last] > e50.iloc[last] > e200.iloc[last]
    below = c.iloc[last] < e50.iloc[last] < e200.iloc[last]
    if above and slope > 0 and adx_v >= 18:
        trend = "aufwärts"
    elif below and slope < 0 and adx_v >= 18:
        trend = "abwärts"
    else:
        trend = "seitwärts"
    strength = float(min(1.0, adx_v / 50))
    window = atr_pct.dropna().tail(300)
    pct = float((window < window.iloc[-1]).mean() * 100) if len(window) > 20 else 50.0
    vol = "hoch" if pct > 80 else "niedrig" if pct < 20 else "normal"
    label = {"aufwärts": "Aufwärtstrend", "abwärts": "Abwärtstrend"}.get(trend, "Seitwärtsphase")
    if vol == "hoch":
        label += " (hohe Volatilität)"
    elif vol == "niedrig":
        label += " (ruhig, evtl. Ausbruch voraus)"
    return Regime(
        trend=trend,
        strength=strength,
        volatility=vol,
        vol_percentile=pct,
        adx=adx_v,
        label=label,
        details={"ema50_slope_pct": slope * 100, "atr_pct": float(atr_pct.iloc[last] * 100)},
    )


def support_resistance(df: pd.DataFrame, lookback: int = 300, wing: int = 3, max_levels: int = 4) -> dict[str, list[float]]:
    """Findet Swing-Hochs/-Tiefs und bündelt sie zu Zonen (innerhalb 0,5 ATR)."""
    d = df.tail(lookback)
    if len(d) < wing * 2 + 5:
        return {"support": [], "resistance": []}
    highs, lows = d["high"].to_numpy(), d["low"].to_numpy()
    tol = float(ind.atr(df).iloc[-1]) * 0.5
    pivots: list[float] = []
    for i in range(wing, len(d) - wing):
        if highs[i] == highs[i - wing : i + wing + 1].max():
            pivots.append(highs[i])
        if lows[i] == lows[i - wing : i + wing + 1].min():
            pivots.append(lows[i])
    pivots.sort()
    clusters: list[list[float]] = []
    for p in pivots:
        if clusters and abs(p - np.mean(clusters[-1])) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    # stärkste Zonen = am häufigsten getestet
    zones = sorted(((len(c), float(np.mean(c))) for c in clusters), reverse=True)
    price = float(df["close"].iloc[-1])
    sup = sorted([z for n, z in zones if z < price and n >= 2], reverse=True)[:max_levels]
    res = sorted([z for n, z in zones if z > price and n >= 2])[:max_levels]
    return {"support": sup, "resistance": res}
