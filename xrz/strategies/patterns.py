"""Kerzenmuster (Candlestick-Patterns) – rein geometrische Erkennung ohne Zukunftsdaten.

Jedes Muster wird an der LETZTEN Kerze des Musters gemeldet und nutzt nur diese und
ältere Kerzen. Der Marktkontext (vorheriger Trend, lokales Hoch/Tief) wird bewusst
nicht hier geprüft, sondern in den Kerzenmuster-Strategien (Trendfilter).

Begriffe je Kerze: Spanne = Hoch − Tief, Körper = |Schluss − Eröffnung|,
oberer/unterer Docht = Abstand des Körpers zum Hoch/Tief.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class _Candles:
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    body: np.ndarray
    rng: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    bull: np.ndarray
    bear: np.ndarray


def _parts(df: pd.DataFrame) -> _Candles:
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    top, bottom = np.maximum(o, c), np.minimum(o, c)
    return _Candles(o, h, l, c, top - bottom, h - l, h - top, bottom - l, c > o, c < o)


def _prev(a: np.ndarray, k: int) -> np.ndarray:
    """Wert von vor k Kerzen (am Anfang NaN bzw. False)."""
    if k <= 0:
        return a
    fill = False if a.dtype == bool else np.nan
    out = np.full(len(a), fill, dtype=a.dtype)
    out[k:] = a[:-k]
    return out


def _series(mask: np.ndarray, df: pd.DataFrame) -> pd.Series:
    return pd.Series(np.asarray(mask, dtype=bool), index=df.index)


# --------------------------------------------------------------------------- #
# Zwei-Kerzen-Muster
# --------------------------------------------------------------------------- #
def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Bullishes Engulfing: rote Kerze, danach grüne Kerze, deren Körper den roten Körper ganz umschließt."""
    k = _parts(df)
    o1, c1, body1, bear1 = _prev(k.o, 1), _prev(k.c, 1), _prev(k.body, 1), _prev(k.bear, 1)
    m = bear1 & k.bull & (k.o <= c1) & (k.c >= o1) & (k.body > body1)
    return _series(m, df)


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Bärisches Engulfing: grüne Kerze, danach rote Kerze, deren Körper den grünen Körper ganz umschließt."""
    k = _parts(df)
    o1, c1, body1, bull1 = _prev(k.o, 1), _prev(k.c, 1), _prev(k.body, 1), _prev(k.bull, 1)
    m = bull1 & k.bear & (k.o >= c1) & (k.c <= o1) & (k.body > body1)
    return _series(m, df)


def inside_bar(df: pd.DataFrame) -> pd.Series:
    """Innenstab: Hoch und Tief liegen innerhalb der Vorkerze (Mutterkerze), mindestens eine Seite echt."""
    k = _parts(df)
    h1, l1 = _prev(k.h, 1), _prev(k.l, 1)
    m = (k.h <= h1) & (k.l >= l1) & ((k.h < h1) | (k.l > l1))
    return _series(m, df)


# --------------------------------------------------------------------------- #
# Ein-Kerzen-Muster (Dochte)
# --------------------------------------------------------------------------- #
def hammer(df: pd.DataFrame) -> pd.Series:
    """Hammer-Form: unterer Docht ≥ 2× Körper und ≥ 60 % der Spanne, oberer Docht ≤ 15 % der Spanne."""
    k = _parts(df)
    m = (k.rng > 0) & (k.lower >= 2 * k.body) & (k.lower >= 0.6 * k.rng) & (k.upper <= 0.15 * k.rng)
    return _series(m, df)


def shooting_star(df: pd.DataFrame) -> pd.Series:
    """Shooting-Star-Form: oberer Docht ≥ 2× Körper und ≥ 60 % der Spanne, unterer Docht ≤ 15 % der Spanne."""
    k = _parts(df)
    m = (k.rng > 0) & (k.upper >= 2 * k.body) & (k.upper >= 0.6 * k.rng) & (k.lower <= 0.15 * k.rng)
    return _series(m, df)


def bullish_pin_bar(df: pd.DataFrame) -> pd.Series:
    """Bullischer Pin-Bar: unterer Docht ≥ ⅔ der Spanne, der Docht sticht unter das Tief der
    Vorkerze, der Körper schließt oberhalb dieses Tiefs (Ablehnung tieferer Kurse)."""
    k = _parts(df)
    l1 = _prev(k.l, 1)
    m = (k.rng > 0) & (k.lower >= 2 / 3 * k.rng) & (k.l < l1) & (np.minimum(k.o, k.c) > l1)
    return _series(m, df)


def bearish_pin_bar(df: pd.DataFrame) -> pd.Series:
    """Bärischer Pin-Bar: oberer Docht ≥ ⅔ der Spanne, sticht über das Hoch der Vorkerze,
    der Körper schließt unterhalb dieses Hochs."""
    k = _parts(df)
    h1 = _prev(k.h, 1)
    m = (k.rng > 0) & (k.upper >= 2 / 3 * k.rng) & (k.h > h1) & (np.maximum(k.o, k.c) < h1)
    return _series(m, df)


# --------------------------------------------------------------------------- #
# Drei-Kerzen-Muster
# --------------------------------------------------------------------------- #
def morning_star(df: pd.DataFrame) -> pd.Series:
    """Morgenstern: lange rote Kerze (Körper ≥ 60 % der Spanne), kleiner Stern (Körper ≤ 30 % des
    ersten Körpers, Körpermitte unter dem ersten Schluss), dann grüne Kerze, die über die Mitte
    des ersten Körpers schließt."""
    k = _parts(df)
    o2, c2, body2, rng2, bear2 = (_prev(a, 2) for a in (k.o, k.c, k.body, k.rng, k.bear))
    o1, c1, body1 = (_prev(a, 1) for a in (k.o, k.c, k.body))
    m = (
        bear2 & (body2 >= 0.6 * rng2) & (body2 > 0)
        & (body1 <= 0.3 * body2) & ((o1 + c1) / 2 < c2)
        & k.bull & (k.c > (o2 + c2) / 2)
    )
    return _series(m, df)


def evening_star(df: pd.DataFrame) -> pd.Series:
    """Abendstern: lange grüne Kerze, kleiner Stern mit Körpermitte über dem ersten Schluss,
    dann rote Kerze, die unter die Mitte des ersten Körpers schließt."""
    k = _parts(df)
    o2, c2, body2, rng2, bull2 = (_prev(a, 2) for a in (k.o, k.c, k.body, k.rng, k.bull))
    o1, c1, body1 = (_prev(a, 1) for a in (k.o, k.c, k.body))
    m = (
        bull2 & (body2 >= 0.6 * rng2) & (body2 > 0)
        & (body1 <= 0.3 * body2) & ((o1 + c1) / 2 > c2)
        & k.bear & (k.c < (o2 + c2) / 2)
    )
    return _series(m, df)


def _soldier_like(k: _Candles, bullish: bool) -> np.ndarray:
    """Kräftige Kerze in eine Richtung: Körper ≥ 50 % der Spanne, Docht auf der Schlussseite ≤ 25 %."""
    if bullish:
        return k.bull & (k.body >= 0.5 * k.rng) & (k.upper <= 0.25 * k.rng)
    return k.bear & (k.body >= 0.5 * k.rng) & (k.lower <= 0.25 * k.rng)


def three_white_soldiers(df: pd.DataFrame) -> pd.Series:
    """Drei weiße Soldaten: drei kräftige grüne Kerzen mit steigenden Schlusskursen; jede eröffnet
    innerhalb des Körpers der vorherigen."""
    k = _parts(df)
    s = _soldier_like(k, True)
    m = s & _prev(s, 1) & _prev(s, 2)
    for lag in (0, 1):
        o, c_prev, o_prev = _prev(k.o, lag), _prev(k.c, lag + 1), _prev(k.o, lag + 1)
        m &= (o >= o_prev) & (o <= c_prev) & (_prev(k.c, lag) > c_prev)
    return _series(m, df)


def three_black_crows(df: pd.DataFrame) -> pd.Series:
    """Drei schwarze Krähen: drei kräftige rote Kerzen mit fallenden Schlusskursen; jede eröffnet
    innerhalb des Körpers der vorherigen."""
    k = _parts(df)
    s = _soldier_like(k, False)
    m = s & _prev(s, 1) & _prev(s, 2)
    for lag in (0, 1):
        o, c_prev, o_prev = _prev(k.o, lag), _prev(k.c, lag + 1), _prev(k.o, lag + 1)
        m &= (o <= o_prev) & (o >= c_prev) & (_prev(k.c, lag) < c_prev)
    return _series(m, df)


def inside_bar_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """Ausbruch aus einem Innenstab: Die Vorkerze war ein Innenstab, die aktuelle Kerze schließt
    über dem Hoch (`bull`) bzw. unter dem Tief (`bear`) der Mutterkerze."""
    k = _parts(df)
    was_inside = _prev(inside_bar(df).to_numpy(), 1)
    h2, l2 = _prev(k.h, 2), _prev(k.l, 2)
    return pd.DataFrame({"bull": was_inside & (k.c > h2), "bear": was_inside & (k.c < l2)}, index=df.index)


# --------------------------------------------------------------------------- #
# Register
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PatternInfo:
    name: str
    label: str
    direction: int  # +1 bullisch, −1 bärisch
    bars: int  # Anzahl Kerzen des Musters
    fn: Callable[[pd.DataFrame], pd.Series]


def _ib_bull(df: pd.DataFrame) -> pd.Series:
    return inside_bar_breakout(df)["bull"]


def _ib_bear(df: pd.DataFrame) -> pd.Series:
    return inside_bar_breakout(df)["bear"]


PATTERNS: dict[str, PatternInfo] = {
    p.name: p
    for p in [
        PatternInfo("bullish_engulfing", "Bullishes Engulfing", 1, 2, bullish_engulfing),
        PatternInfo("bearish_engulfing", "Bärisches Engulfing", -1, 2, bearish_engulfing),
        PatternInfo("hammer", "Hammer", 1, 1, hammer),
        PatternInfo("shooting_star", "Shooting Star", -1, 1, shooting_star),
        PatternInfo("morning_star", "Morgenstern", 1, 3, morning_star),
        PatternInfo("evening_star", "Abendstern", -1, 3, evening_star),
        PatternInfo("three_white_soldiers", "Drei weiße Soldaten", 1, 3, three_white_soldiers),
        PatternInfo("three_black_crows", "Drei schwarze Krähen", -1, 3, three_black_crows),
        PatternInfo("inside_bar_breakout_bull", "Innenstab-Ausbruch nach oben", 1, 3, _ib_bull),
        PatternInfo("inside_bar_breakout_bear", "Innenstab-Ausbruch nach unten", -1, 3, _ib_bear),
        PatternInfo("bullish_pin_bar", "Bullischer Pin-Bar", 1, 2, bullish_pin_bar),
        PatternInfo("bearish_pin_bar", "Bärischer Pin-Bar", -1, 2, bearish_pin_bar),
    ]
}


def detect_all(df: pd.DataFrame) -> pd.DataFrame:
    """Alle registrierten Muster als boolesche Spalten (Spaltenname = Mustername)."""
    return pd.DataFrame({name: p.fn(df).to_numpy(dtype=bool) for name, p in PATTERNS.items()}, index=df.index)
