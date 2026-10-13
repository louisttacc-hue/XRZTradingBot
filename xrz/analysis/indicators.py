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


# --------------------------------------------------------------------------- #
# Erweiterte Indikatoren (für die Strategie-Bibliothek)
#
# Alle Fensterberechnungen sind "nachlaufend": der Wert einer Kerze hängt nur von
# dieser und älteren Kerzen ab. Gewichtete Fenstersummen werden bewusst elementweise
# aufaddiert (statt per Matrixprodukt/BLAS), damit ein Datenausschnitt exakt dieselben
# Zahlen liefert wie die volle Historie – sonst könnten winzige Rundungsunterschiede
# Schwellen-Signale kippen.
# --------------------------------------------------------------------------- #
def _window_sum(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Σ weights[k] · x[t−n+1+k] für jedes t (NaN, solange das Fenster nicht voll ist)."""
    n, size = len(weights), len(x)
    out = np.full(size, np.nan)
    if size >= n:
        acc = np.zeros(size - n + 1)
        for k, w in enumerate(weights):
            acc += w * x[k : size - n + 1 + k]
        out[n - 1 :] = acc
    return out


def wma(s: pd.Series, n: int) -> pd.Series:
    """Linear gewichteter Durchschnitt (jüngste Kerze hat Gewicht n)."""
    w = np.arange(1, n + 1, dtype=float)
    return pd.Series(_window_sum(s.to_numpy(dtype=float), w / w.sum()), index=s.index)


def hma(s: pd.Series, n: int) -> pd.Series:
    """Hull Moving Average: WMA(2·WMA(n/2) − WMA(n), √n)."""
    half, root = max(1, n // 2), max(1, int(round(np.sqrt(n))))
    return wma(2 * wma(s, half) - wma(s, n), root)


def kama(close: pd.Series, n: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    """Kaufman Adaptive Moving Average (Effizienz-Ratio steuert die Glättung)."""
    c = close.to_numpy(dtype=float)
    change = (close - close.shift(n)).abs()
    noise = close.diff().abs().rolling(n, min_periods=n).sum()
    er = (change / noise.replace(0, np.nan)).fillna(0.0).where(noise.notna()).to_numpy()
    fast_sc, slow_sc = 2 / (fast + 1), 2 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    out = np.full(len(c), np.nan)
    prev = np.nan
    for i in range(len(c)):
        if np.isnan(sc[i]) or np.isnan(c[i]):
            continue
        prev = c[i] if np.isnan(prev) else prev + sc[i] * (c[i] - prev)
        out[i] = prev
    return pd.Series(out, index=close.index)


def roc(s: pd.Series, n: int) -> pd.Series:
    """Rate of Change in Prozent gegenüber vor n Kerzen."""
    return (s / s.shift(n) - 1) * 100


def linreg(s: pd.Series, n: int) -> pd.Series:
    """Wert der linearen Regression über die letzten n Kerzen am Fensterende (wie Pine `linreg(src, n, 0)`)."""
    y = s.to_numpy(dtype=float)
    x = np.arange(n, dtype=float)
    sy = _window_sum(y, np.ones(n))
    sxy = _window_sum(y, x)
    sx, sxx = x.sum(), (x * x).sum()
    denom = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / denom if denom else np.zeros_like(sy)
    intercept = (sy - slope * sx) / n
    return pd.Series(intercept + slope * (n - 1), index=s.index)


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Commodity Channel Index: (TP − SMA(TP)) / (0,015 · mittlere absolute Abweichung)."""
    tp = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy(dtype=float)
    size = len(tp)
    mean = sma(pd.Series(tp), n).to_numpy()
    md = np.full(size, np.nan)
    if size >= n:
        acc = np.zeros(size - n + 1)
        m = mean[n - 1 :]
        for k in range(n):
            acc += np.abs(tp[k : size - n + 1 + k] - m)
        md[n - 1 :] = acc / n
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(md > 0, (tp - mean) / (0.015 * md), np.nan)
    return pd.Series(out, index=df.index)


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Williams %R (−100 … 0): Lage des Schlusskurses in der n-Kerzen-Spanne."""
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    return (-100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)).clip(-100.0, 0.0)


def aroon(df: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    """Aroon Up/Down (0 … 100): wie lange liegt das n-Kerzen-Hoch/-Tief zurück?"""
    from numpy.lib.stride_tricks import sliding_window_view

    h, l = df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float)
    up = np.full(len(h), np.nan)
    down = np.full(len(h), np.nan)
    if len(h) > n:
        up[n:] = 100 * sliding_window_view(h, n + 1).argmax(axis=1) / n
        down[n:] = 100 * sliding_window_view(l, n + 1).argmin(axis=1) / n
    return pd.DataFrame({"up": up, "down": down, "osc": up - down}, index=df.index)


def vortex(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Vortex-Indikator VI+ / VI−."""
    vm_plus = (df["high"] - df["low"].shift(1)).abs()
    vm_minus = (df["low"] - df["high"].shift(1)).abs()
    tr = true_range(df).rolling(n, min_periods=n).sum().replace(0, np.nan)
    return pd.DataFrame(
        {"plus": vm_plus.rolling(n, min_periods=n).sum() / tr, "minus": vm_minus.rolling(n, min_periods=n).sum() / tr}
    )


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Money Flow Index (volumengewichteter RSI, 0 … 100)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    prev = tp.shift(1)
    pos = flow.where(tp > prev, 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(tp < prev, 0.0).rolling(n, min_periods=n).sum()
    out = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    return out.where(neg != 0, 100.0).where(pos.notna())


def stoch_rsi(close: pd.Series, rsi_n: int = 14, stoch_n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stochastic RSI: Stochastik angewandt auf den RSI, %K und %D geglättet (0 … 100)."""
    r = rsi(close, rsi_n)
    lo = r.rolling(stoch_n, min_periods=stoch_n).min()
    hi = r.rolling(stoch_n, min_periods=stoch_n).max()
    raw = (100 * (r - lo) / (hi - lo).replace(0, np.nan)).clip(0.0, 100.0)
    pk = sma(raw, k)
    return pd.DataFrame({"k": pk, "d": sma(pk, d)})


def keltner(df: pd.DataFrame, n: int = 20, mult: float = 2.0, atr_n: int | None = None) -> pd.DataFrame:
    """Keltner-Kanal: EMA(n) ± mult · ATR."""
    mid = ema(df["close"], n)
    width = mult * atr(df, atr_n or n)
    return pd.DataFrame({"mid": mid, "upper": mid + width, "lower": mid - width})


def trix(close: pd.Series, n: int = 15, signal: int = 9) -> pd.DataFrame:
    """TRIX: Änderung der dreifach geglätteten EMA des log-Kurses (×10 000) plus Signallinie."""
    e3 = ema(ema(ema(np.log(close), n), n), n)
    t = 10_000 * e3.diff()
    return pd.DataFrame({"trix": t, "signal": ema(t, signal)})


def coppock(close: pd.Series, roc_long: int = 14, roc_short: int = 11, wma_n: int = 10) -> pd.Series:
    """Coppock-Kurve: WMA(ROC lang + ROC kurz)."""
    return wma(roc(close, roc_long) + roc(close, roc_short), wma_n)


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi-Kerzen. HA-Open[t] = (HA-Open[t−1] + HA-Close[t−1]) / 2 (rekursiv ab der ersten Kerze)."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    ha_close = (o + h + l + c) / 4
    ha_open = np.empty(len(o))
    if len(o):
        ha_open[0] = (o[0] + c[0]) / 2
        for i in range(1, len(o)):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
    return pd.DataFrame(
        {
            "open": ha_open,
            "high": np.maximum(h, np.maximum(ha_open, ha_close)),
            "low": np.minimum(l, np.minimum(ha_open, ha_close)),
            "close": ha_close,
        },
        index=df.index,
    )


def parabolic_sar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.DataFrame:
    """Parabolic SAR (Wilder). `direction` +1 = SAR unter dem Kurs (Aufwärtstrend), −1 = darüber."""
    h, l, c = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    size = len(c)
    sar_out = np.full(size, np.nan)
    dir_out = np.zeros(size)
    if size < 3:
        return pd.DataFrame({"sar": sar_out, "direction": dir_out}, index=df.index)
    up = c[1] >= c[0]
    sar = l[0] if up else h[0]
    ep = h[1] if up else l[1]
    af = step
    for i in range(2, size):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            sar = max(sar, h[i - 1], h[i - 2])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        sar_out[i] = sar
        dir_out[i] = 1 if up else -1
    return pd.DataFrame({"sar": sar_out, "direction": dir_out}, index=df.index)


def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26) -> pd.DataFrame:
    """Ichimoku ohne Zukunftsdaten.

    `span_a`/`span_b` sind die Wolke, die an der AKTUELLEN Kerze gezeichnet ist – also die
    Werte von vor `displacement − 1` Kerzen (wie TradingView). `lead_a`/`lead_b` sind die
    gerade berechneten Werte (= Wolke der Zukunft, aber nur aus Vergangenheitsdaten).
    Die Chikou-Spanne (Schlusskurs in die Vergangenheit verschoben) wird bewusst nicht
    geliefert; ihr lookahead-freies Äquivalent ist der Vergleich Schlusskurs vs. Kurs vor
    `kijun` Kerzen.
    """
    def mid(n: int) -> pd.Series:
        return (df["high"].rolling(n, min_periods=n).max() + df["low"].rolling(n, min_periods=n).min()) / 2

    t, k = mid(tenkan), mid(kijun)
    lead_a, lead_b = (t + k) / 2, mid(senkou_b)
    shift = max(0, displacement - 1)
    return pd.DataFrame(
        {"tenkan": t, "kijun": k, "lead_a": lead_a, "lead_b": lead_b,
         "span_a": lead_a.shift(shift), "span_b": lead_b.shift(shift)}
    )


def squeeze_momentum(df: pd.DataFrame, n: int = 20, bb_mult: float = 2.0, kc_mult: float = 1.5) -> pd.DataFrame:
    """Squeeze Momentum (LazyBear): Squeeze = Bollinger-Bänder innerhalb des Keltner-Kanals;
    Momentum = lineare Regression von Kurs − Mitte(Donchian-Mitte, SMA)."""
    c = df["close"]
    basis = sma(c, n)
    dev = bb_mult * c.rolling(n, min_periods=n).std(ddof=0)
    rng = sma(true_range(df), n)
    squeeze_on = ((basis - dev) > (basis - kc_mult * rng)) & ((basis + dev) < (basis + kc_mult * rng))
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    mom = linreg(c - ((hh + ll) / 2 + basis) / 2, n)
    return pd.DataFrame({"squeeze_on": squeeze_on, "momentum": mom})


def chandelier_exit(df: pd.DataFrame, n: int = 22, mult: float = 3.0) -> pd.DataFrame:
    """Chandelier Exit: Long-Stop = n-Hoch − mult·ATR, Short-Stop = n-Tief + mult·ATR (beide nur
    nachgezogen). `direction` wechselt, wenn der Schlusskurs den jeweils letzten Stop durchbricht."""
    a = (atr(df, n) * mult).to_numpy()
    c = df["close"].to_numpy(dtype=float)
    ls_raw = df["high"].rolling(n, min_periods=n).max().to_numpy() - a
    ss_raw = df["low"].rolling(n, min_periods=n).min().to_numpy() + a
    size = len(c)
    long_stop = np.full(size, np.nan)
    short_stop = np.full(size, np.nan)
    direction = np.zeros(size)
    d = 1
    for i in range(size):
        if np.isnan(ls_raw[i]) or np.isnan(ss_raw[i]):
            continue
        lp = long_stop[i - 1] if i > 0 and not np.isnan(long_stop[i - 1]) else ls_raw[i]
        sp = short_stop[i - 1] if i > 0 and not np.isnan(short_stop[i - 1]) else ss_raw[i]
        long_stop[i] = max(ls_raw[i], lp) if i > 0 and c[i - 1] > lp else ls_raw[i]
        short_stop[i] = min(ss_raw[i], sp) if i > 0 and c[i - 1] < sp else ss_raw[i]
        if c[i] > sp:
            d = 1
        elif c[i] < lp:
            d = -1
        direction[i] = d
    return pd.DataFrame({"long_stop": long_stop, "short_stop": short_stop, "direction": direction}, index=df.index)


def swing_highs(high: pd.Series, wing: int = 3) -> pd.Series:
    """Bestätigte Swing-Hochs ohne Zukunftsdaten: Ein Hoch zählt erst, wenn `wing` Kerzen
    danach kein höheres Hoch kam. Der Wert erscheint an der BESTÄTIGUNGS-Kerze (Hoch-Kerze + wing),
    sonst NaN."""
    cand = high.shift(wing)
    return cand.where(cand == high.rolling(2 * wing + 1, min_periods=2 * wing + 1).max())


def swing_lows(low: pd.Series, wing: int = 3) -> pd.Series:
    """Bestätigte Swing-Tiefs (Gegenstück zu `swing_highs`)."""
    cand = low.shift(wing)
    return cand.where(cand == low.rolling(2 * wing + 1, min_periods=2 * wing + 1).min())


def period_ids(index: pd.Index, period_bars: int) -> np.ndarray:
    """Teilt die Kerzen in feste Perioden zu je `period_bars` Kerzen ein – an der Uhrzeit (UTC)
    verankert, damit die Grenzen nicht vom Beginn des Datenausschnitts abhängen (bei 1h-Kerzen
    und 24 → Kalendertage). Ohne Zeitindex: nach Position."""
    size = len(index)
    if isinstance(index, pd.DatetimeIndex) and size > 1:
        ts = index.as_unit("s").asi8
        diffs = np.diff(ts[:65])
        diffs = diffs[diffs > 0]
        if len(diffs):
            bar = int(np.median(diffs))
            return ts // (bar * period_bars)
    return np.arange(size) // period_bars


def prior_periods(df: pd.DataFrame, period_bars: int, n: int = 1) -> pd.DataFrame:
    """Kennzahlen der letzten n ABGESCHLOSSENEN Perioden für jede Kerze (siehe `period_ids`).

    Spalten: `open` (Eröffnung der laufenden Periode), `high`/`low` (Hoch/Tief der n Vorperioden),
    `close` (Schluss der Vorperiode), `hc`/`lc` (höchster/tiefster Periodenschluss der n Vorperioden).
    Die erste Periode im Datensatz ist meist unvollständig und wird verworfen.
    """
    ids = period_ids(df.index, period_bars)
    g = pd.DataFrame(
        {"id": ids, "open": df["open"].to_numpy(), "high": df["high"].to_numpy(),
         "low": df["low"].to_numpy(), "close": df["close"].to_numpy()}
    )
    agg = g.groupby("id", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")
    )
    if len(agg):
        agg.iloc[0] = np.nan
    prev = pd.DataFrame(
        {
            "open": agg["open"],
            "high": agg["high"].rolling(n, min_periods=n).max().shift(1),
            "low": agg["low"].rolling(n, min_periods=n).min().shift(1),
            "close": agg["close"].shift(1),
            "hc": agg["close"].rolling(n, min_periods=n).max().shift(1),
            "lc": agg["close"].rolling(n, min_periods=n).min().shift(1),
        }
    )
    out = prev.reindex(ids)
    out.index = df.index
    return out


def pivot_points(df: pd.DataFrame, period_bars: int = 24) -> pd.DataFrame:
    """Klassische Floor-Pivots aus Hoch/Tief/Schluss der VORHERIGEN abgeschlossenen Periode."""
    pp = prior_periods(df, period_bars, 1)
    h, l, c = pp["high"], pp["low"], pp["close"]
    p = (h + l + c) / 3
    return pd.DataFrame(
        {"p": p, "r1": 2 * p - l, "s1": 2 * p - h, "r2": p + (h - l), "s2": p - (h - l),
         "r3": h + 2 * (p - l), "s3": l - 2 * (h - p)}
    )


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
