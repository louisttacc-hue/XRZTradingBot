"""Technische Indikatoren – reine pandas/numpy-Implementierungen (ohne Zukunftsdaten)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder-Glättung (wie in RSI/ATR/ADX)."""
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = rma(delta.clip(lower=0), n)
    down = rma(-delta.clip(upper=0), n)
    rs = up / down.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(down != 0, 100.0).where(up.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "width": (upper - lower) / mid, "pctb": (close - lower) / (upper - lower)}
    )


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = rma(true_range(df), n)
    plus_di = 100 * rma(plus_dm, n) / tr
    minus_di = 100 * rma(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": rma(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3) -> pd.DataFrame:
    ll = df["low"].rolling(k, min_periods=k).min()
    hh = df["high"].rolling(k, min_periods=k).max()
    pk = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    return pd.DataFrame({"k": pk, "d": pk.rolling(d).mean()})


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Kanal der VORHERIGEN n Kerzen (aktuelle Kerze nicht enthalten → kein Lookahead)."""
    hh = df["high"].rolling(n, min_periods=n).max().shift(1)
    ll = df["low"].rolling(n, min_periods=n).min().shift(1)
    return pd.DataFrame({"upper": hh, "lower": ll, "mid": (hh + ll) / 2})


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper_basic = hl2 + mult * a
    lower_basic = hl2 - mult * a
    size = len(df)
    upper = np.full(size, np.nan)
    lower = np.full(size, np.nan)
    direction = np.zeros(size)
    line = np.full(size, np.nan)
    for i in range(size):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(upper[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction[i] = 1 if close[i] >= hl2[i] else -1
        else:
            upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
            lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
            if direction[i - 1] == 1:
                direction[i] = -1 if close[i] < lower[i] else 1
            else:
                direction[i] = 1 if close[i] > upper[i] else -1
        line[i] = lower[i] if direction[i] == 1 else upper[i]
    return pd.DataFrame({"line": line, "direction": direction}, index=df.index)


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0)
    return (sign * df["volume"]).cumsum()


def rolling_vwap(df: pd.DataFrame, n: int = 48) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(n, min_periods=n).sum()
    v = df["volume"].rolling(n, min_periods=n).sum()
    return pv / v.replace(0, np.nan)


def zscore(s: pd.Series, n: int = 50) -> pd.Series:
    m = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return (s - m) / sd.replace(0, np.nan)


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Fügt den Standard-Indikatorensatz hinzu (für Analyse-Berichte & KI)."""
    out = df.copy()
    c = out["close"]
    for n in (20, 50, 200):
        out[f"ema{n}"] = ema(c, n)
    out["rsi"] = rsi(c, 14)
    m = macd(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]
    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / c * 100
    bb = bollinger(c)
    out["bb_upper"], out["bb_mid"], out["bb_lower"], out["bb_width"], out["bb_pctb"] = (
        bb["upper"], bb["mid"], bb["lower"], bb["width"], bb["pctb"]
    )
    a = adx(out)
    out["adx"], out["plus_di"], out["minus_di"] = a["adx"], a["plus_di"], a["minus_di"]
    st = stochastic(out)
    out["stoch_k"], out["stoch_d"] = st["k"], st["d"]
    out["obv"] = obv(out)
    out["vwap"] = rolling_vwap(out)
    out["vol_z"] = zscore(out["volume"], 50)
    sup = supertrend(out)
    out["supertrend"], out["supertrend_dir"] = sup["line"], sup["direction"]
    return out
