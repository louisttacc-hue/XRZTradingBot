"""Strategie-Bibliothek.

Jede Strategie liefert für jede Kerze eine Zielposition (+1 Long, -1 Short, 0 flach),
berechnet NUR aus Daten bis einschließlich dieser Kerze (kein Blick in die Zukunft).
Der Backtester führt Signale erst zur Eröffnung der NÄCHSTEN Kerze aus.

Bewusst NICHT enthalten bzw. lookahead-frei umgebaut:
- Chikou-Spanne (Ichimoku) → ersetzt durch "Schluss über Kurs vor Kijun-Länge",
- zentrierte Pivots / ZigZag → Swing-Punkte gelten erst nach `wing` Bestätigungskerzen,
- Detrended Price Oscillator (zentrierter Durchschnitt) → weggelassen,
- Tages-/Perioden-Niveaus (Pivots, Range) kommen immer aus der VORHERIGEN abgeschlossenen Periode.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..analysis import indicators as ind
from . import patterns as pat

CATEGORIES: tuple[str, ...] = ("Trend", "Momentum", "Mean-Reversion", "Ausbruch", "Volumen", "Kerzenmuster")

TREND = ["aufwärts", "abwärts"]
RANGE = ["seitwärts"]
ALL_REGIMES = ["aufwärts", "abwärts", "seitwärts"]


# --------------------------------------------------------------------------- #
# Positions-Helfer
# --------------------------------------------------------------------------- #
def _bool(x: Any) -> np.ndarray:
    """Maske (Series/Array) → bool-Array, NaN → False."""
    a = np.asarray(x.to_numpy() if isinstance(x, pd.Series) else x)
    if a.dtype != bool:
        a = np.asarray(pd.Series(a).fillna(False), dtype=bool)
    return a


def _float(x: Any, size: int) -> np.ndarray:
    if x is None:
        return np.full(size, np.nan)
    return np.asarray(x.to_numpy() if isinstance(x, pd.Series) else x, dtype=float)


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


def managed_positions(
    long_entry,
    short_entry,
    close,
    *,
    long_stop=None,
    long_target=None,
    short_stop=None,
    short_target=None,
    trail=None,
    max_hold: int = 0,
    long_exit=None,
    short_exit=None,
) -> np.ndarray:
    """Positionsverwaltung zum Schlusskurs (pfadabhängig, daher als Schleife über numpy-Arrays).

    - `long_stop`/`short_stop`, `long_target`/`short_target`: Preisniveaus, die zum Einstiegszeitpunkt
      eingefroren werden (z. B. Swing-Tief, Pivot-Linie).
    - `trail`: Abstand (z. B. k·ATR) für einen am Schlusskurs nachgezogenen Stop; ohne festen Stop
      ist er auch der Anfangsstop.
    - `max_hold`: maximale Haltedauer in Kerzen (0 = unbegrenzt).
    - `long_exit`/`short_exit`: zusätzliche Ausstiegssignale.
    Ein Einstieg wird nur genommen, wenn Stop < Kurs < Ziel gilt.
    """
    c = _float(close, len(close))
    size = len(c)
    le, se = _bool(long_entry), _bool(short_entry)
    lx = _bool(long_exit) if long_exit is not None else np.zeros(size, bool)
    sx = _bool(short_exit) if short_exit is not None else np.zeros(size, bool)
    ls, lt, ss, st = (_float(x, size) for x in (long_stop, long_target, short_stop, short_target))
    tr = _float(trail, size)
    has_ls, has_lt, has_ss, has_st = (x is not None for x in (long_stop, long_target, short_stop, short_target))
    use_trail = trail is not None

    pos = np.zeros(size)
    cur, held = 0, 0
    stop, target = -np.inf, np.inf
    for i in range(size):
        ci = c[i]
        if cur == 1:
            held += 1
            if ci <= stop or ci >= target or lx[i] or (max_hold and held >= max_hold):
                cur = 0
            elif use_trail and tr[i] == tr[i] and ci - tr[i] > stop:
                stop = ci - tr[i]
        elif cur == -1:
            held += 1
            if ci >= stop or ci <= target or sx[i] or (max_hold and held >= max_hold):
                cur = 0
            elif use_trail and tr[i] == tr[i] and ci + tr[i] < stop:
                stop = ci + tr[i]
        if cur <= 0 and le[i] and not se[i]:
            s0 = ls[i] if has_ls else (ci - tr[i] if use_trail else -np.inf)
            t0 = lt[i] if has_lt else np.inf
            if s0 < ci < t0:
                cur, stop, target, held = 1, s0, t0, 0
        elif cur >= 0 and se[i] and not le[i]:
            s0 = ss[i] if has_ss else (ci + tr[i] if use_trail else np.inf)
            t0 = st[i] if has_st else -np.inf
            if t0 < ci < s0:
                cur, stop, target, held = -1, s0, t0, 0
        pos[i] = cur
    return pos


def _rising(s: pd.Series, bars: int = 1) -> pd.Series:
    return s > s.shift(bars)


def _falling(s: pd.Series, bars: int = 1) -> pd.Series:
    return s < s.shift(bars)


def _cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a > b) & (a.shift(1) <= b_prev)


def _cross_down(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a < b) & (a.shift(1) >= b_prev)


def _consecutive(mask: pd.Series, k: int) -> pd.Series:
    """True, wenn die Bedingung in den letzten k Kerzen (inkl. aktueller) durchgehend galt."""
    m = mask.fillna(False).astype(float)
    return m.rolling(k, min_periods=k).sum() >= k


def _recent(mask: pd.Series, k: int) -> pd.Series:
    """True, wenn die Bedingung in den letzten k Kerzen (inkl. aktueller) mindestens einmal galt."""
    return mask.fillna(False).astype(float).rolling(k, min_periods=1).max() > 0


def _trend_ok(close: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """Trendfilter: EMA(n) steigt (Long erlaubt) bzw. fällt (Short erlaubt) über 5 Kerzen; n = 0 → aus."""
    if n <= 0:
        on = pd.Series(True, index=close.index)
        return on, on
    e = ind.ema(close, n)
    return _rising(e, 5), _falling(e, 5)


def _threshold(s: pd.Series, th: float) -> pd.Series:
    """s > th; th ≤ 0 schaltet den Filter ab."""
    if th <= 0:
        return pd.Series(True, index=s.index)
    return s > th


# --------------------------------------------------------------------------- #
# Strategie-Objekt
# --------------------------------------------------------------------------- #
@dataclass
class Strategy:
    name: str
    label: str
    description: str
    defaults: dict[str, Any]
    grid: dict[str, list[Any]]
    fn: Callable[[pd.DataFrame, dict[str, Any]], tuple[np.ndarray, str]]
    good_in: list[str] = field(default_factory=list)  # passende Marktphasen
    category: str = "Trend"  # eine von CATEGORIES

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"Unbekannte Kategorie '{self.category}' für Strategie {self.name}")

    def positions(self, df: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.Series:
        return self.evaluate(df, params)[0]

    def evaluate(self, df: pd.DataFrame, params: dict[str, Any] | None = None) -> tuple[pd.Series, str]:
        p = {**self.defaults, **(params or {})}
        if len(df) == 0:
            return pd.Series(dtype=float, index=df.index), "Keine Kursdaten vorhanden."
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

    def to_dict(self) -> dict[str, Any]:
        """Beschreibung für API/Oberfläche."""
        return {
            "name": self.name, "label": self.label, "description": self.description, "category": self.category,
            "good_in": list(self.good_in), "params": dict(self.defaults), "grid": {k: list(v) for k, v in self.grid.items()},
        }


def _valid(p: dict[str, Any]) -> bool:
    if "fast" in p and "slow" in p and p["fast"] >= p["slow"]:
        return False
    if "fast" in p and "mid" in p and p["fast"] >= p["mid"]:
        return False
    if "mid" in p and "slow" in p and p["mid"] >= p["slow"]:
        return False
    if "entry_n" in p and "exit_n" in p and p["exit_n"] >= p["entry_n"]:
        return False
    if "tenkan" in p and "kijun" in p and p["tenkan"] >= p["kijun"]:
        return False
    return True


def _fmt(x: float, nd: int = 1) -> str:
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def _dir_word(v: float) -> str:
    return "LONG" if v > 0 else "SHORT" if v < 0 else "neutral"


def _last(s: pd.Series | np.ndarray) -> float:
    v = s.iloc[-1] if isinstance(s, pd.Series) else s[-1]
    return float(v) if v is not None and not pd.isna(v) else float("nan")


def _ago(bars: int) -> str:
    return "auf der aktuellen Kerze" if bars == 0 else "vor 1 Kerze" if bars == 1 else f"vor {bars} Kerzen"


def _rel(a: float, b: float) -> str:
    if np.isnan(a) or np.isnan(b):
        return "–"
    return "über" if a > b else "unter" if a < b else "auf"


# =========================================================================== #
# Bestehende Strategien
# =========================================================================== #
# 1) EMA-Trendfolge
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


# 2) MACD-Momentum
def _macd_momentum(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    m = ind.macd(c, p["fast"], p["slow"], p["signal"])
    t = ind.ema(c, p["trend"])
    cross_up = (m["macd"] > m["signal"]) & (m["macd"].shift(1) <= m["signal"].shift(1))
    cross_dn = (m["macd"] < m["signal"]) & (m["macd"].shift(1) >= m["signal"].shift(1))
    pos = state_machine(cross_up & (c > t), m["macd"] < m["signal"], cross_dn & (c < t), m["macd"] > m["signal"])
    why = f"MACD-Histogramm {_fmt(m['hist'].iloc[-1], 4)}, Kurs {'über' if c.iloc[-1] > t.iloc[-1] else 'unter'} EMA{p['trend']} → {_dir_word(pos[-1])}"
    return pos, why


# 3) RSI + Bollinger Mean-Reversion (nur in Seitwärtsphasen)
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


# 4) Bollinger-Squeeze-Ausbruch
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


# 5) Donchian-/Turtle-Ausbruch
def _donchian(df: pd.DataFrame, p: dict[str, Any]):
    c = df["close"]
    ent = ind.donchian(df, p["entry_n"])
    ext = ind.donchian(df, p["exit_n"])
    pos = state_machine(c > ent["upper"], c < ext["lower"], c < ent["lower"], c > ext["upper"])
    why = f"{p['entry_n']}-Kerzen-Hoch {_fmt(ent['upper'].iloc[-1], 2)} / -Tief {_fmt(ent['lower'].iloc[-1], 2)} → {_dir_word(pos[-1])}"
    return pos, why


# 6) Supertrend
def _supertrend(df: pd.DataFrame, p: dict[str, Any]):
    st = ind.supertrend(df, p["n"], p["mult"])
    a = ind.adx(df)["adx"]
    d = st["direction"]
    pos = state_machine((d == 1) & (a > p["adx_min"]), d == -1, (d == -1) & (a > p["adx_min"]), d == 1)
    why = f"Supertrend {'grün' if d.iloc[-1] == 1 else 'rot'} bei {_fmt(st['line'].iloc[-1], 2)} → {_dir_word(pos[-1])}"
    return pos, why


# 7) Trend-Pullback (Rücksetzer im Trend kaufen)
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


# =========================================================================== #
# Trend
# =========================================================================== #
def _ichimoku(df: pd.DataFrame, p: dict[str, Any]):
    """Long: Schluss über der Wolke, Tenkan > Kijun und Schluss > Kurs vor Kijun-Länge
    (lookahead-freier Chikou-Ersatz). Ausstieg: Schluss unter Kijun. Short spiegelbildlich."""
    c = df["close"]
    k = p["kijun"]
    ic = ind.ichimoku(df, p["tenkan"], k, 2 * k, k)
    top = np.maximum(ic["span_a"], ic["span_b"])
    bot = np.minimum(ic["span_a"], ic["span_b"])
    lag = c.shift(k)
    pos = state_machine(
        (c > top) & (ic["tenkan"] > ic["kijun"]) & (c > lag),
        c < ic["kijun"],
        (c < bot) & (ic["tenkan"] < ic["kijun"]) & (c < lag),
        c > ic["kijun"],
    )
    cl, t, b = _last(c), _last(top), _last(bot)
    where = "über" if cl > t else "unter" if cl < b else "in"
    why = (
        f"Kurs {where} der Wolke ({_fmt(b, 2)}–{_fmt(t, 2)}), Tenkan {_rel(_last(ic['tenkan']), _last(ic['kijun']))} "
        f"Kijun {_fmt(_last(ic['kijun']), 2)} → {_dir_word(pos[-1])}"
    )
    return pos, why


def _parabolic_sar(df: pd.DataFrame, p: dict[str, Any]):
    """Position folgt der SAR-Richtung; optional nur bei ADX über Schwelle einsteigen."""
    ps = ind.parabolic_sar(df, p["step"], p["max_step"])
    d = ps["direction"]
    strong = _threshold(ind.adx(df)["adx"], p["adx_min"])
    pos = state_machine((d == 1) & strong, d == -1, (d == -1) & strong, d == 1)
    why = (
        f"SAR {_fmt(_last(ps['sar']), 2)} {'unter' if _last(d) > 0 else 'über'} dem Kurs "
        f"(AF {p['step']}–{p['max_step']}) → {_dir_word(pos[-1])}"
    )
    return pos, why


def _hull_ma(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn die Hull-MA `confirm` Kerzen in Folge steigt und der Kurs darüber liegt; raus, sobald sie fällt."""
    c = df["close"]
    h = ind.hma(c, p["n"])
    up, dn = _rising(h), _falling(h)
    pos = state_machine(
        _consecutive(up, p["confirm"]) & (c > h), dn,
        _consecutive(dn, p["confirm"]) & (c < h), up,
    )
    slope = "steigt" if bool(up.iloc[-1]) else "fällt" if bool(dn.iloc[-1]) else "flach"
    why = f"HMA{p['n']} {_fmt(_last(h), 2)} {slope}, Kurs {_rel(_last(c), _last(h))} HMA → {_dir_word(pos[-1])}"
    return pos, why


def _triple_ema(df: pd.DataFrame, p: dict[str, Any]):
    """Long bei Fächer EMA schnell > mittel > langsam und Kurs über der schnellen EMA; raus, wenn schnell < mittel."""
    c = df["close"]
    f, m, s = ind.ema(c, p["fast"]), ind.ema(c, p["mid"]), ind.ema(c, p["slow"])
    pos = state_machine((f > m) & (m > s) & (c > f), f < m, (f < m) & (m < s) & (c < f), f > m)
    fl, ml, sl = _last(f), _last(m), _last(s)
    order = "aufwärts geordnet" if fl > ml > sl else "abwärts geordnet" if fl < ml < sl else "gemischt"
    why = f"EMA{p['fast']}/{p['mid']}/{p['slow']} {order} → {_dir_word(pos[-1])}"
    return pos, why


def _golden_cross(df: pd.DataFrame, p: dict[str, Any]):
    """Golden Cross (SMA schnell > SMA langsam) = Long, Death Cross = Short."""
    c = df["close"]
    f, s = ind.sma(c, p["fast"]), ind.sma(c, p["slow"])
    pos = state_machine(f > s, f < s, f < s, f > s)
    state = "Golden Cross" if _last(f) > _last(s) else "Death Cross" if _last(f) < _last(s) else "kein Kreuz"
    why = f"SMA{p['fast']} {_fmt(_last(f), 2)} {_rel(_last(f), _last(s))} SMA{p['slow']} {_fmt(_last(s), 2)} ({state}) → {_dir_word(pos[-1])}"
    return pos, why


def _adx_dmi(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn +DI > −DI und ADX über Schwelle und steigend; raus, wenn −DI > +DI."""
    d = ind.adx(df, p["n"])
    strong = (d["adx"] > p["adx_min"]) & _rising(d["adx"])
    pos = state_machine(
        (d["plus_di"] > d["minus_di"]) & strong, d["minus_di"] > d["plus_di"],
        (d["minus_di"] > d["plus_di"]) & strong, d["plus_di"] > d["minus_di"],
    )
    why = f"+DI {_fmt(_last(d['plus_di']))} / −DI {_fmt(_last(d['minus_di']))}, ADX {_fmt(_last(d['adx']))} → {_dir_word(pos[-1])}"
    return pos, why


def _aroon(df: pd.DataFrame, p: dict[str, Any]):
    """Long bei Aroon-Up ≥ Level und Aroon-Down ≤ 100 − Level; raus, wenn Down > Up."""
    ar = ind.aroon(df, p["n"])
    lvl = p["level"]
    pos = state_machine(
        (ar["up"] >= lvl) & (ar["down"] <= 100 - lvl), ar["down"] > ar["up"],
        (ar["down"] >= lvl) & (ar["up"] <= 100 - lvl), ar["up"] > ar["down"],
    )
    why = f"Aroon({p['n']}) Up {_fmt(_last(ar['up']), 0)} / Down {_fmt(_last(ar['down']), 0)} → {_dir_word(pos[-1])}"
    return pos, why


def _kama_trend(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn KAMA steigt und der Kurs mehr als band·ATR darüber liegt; raus unter KAMA − band·ATR."""
    c = df["close"]
    k = ind.kama(c, p["n"], 2, 30)
    band = p["band"] * ind.atr(df)
    pos = state_machine(
        (c > k + band) & _rising(k), c < k - band,
        (c < k - band) & _falling(k), c > k + band,
    )
    why = f"KAMA{p['n']} {_fmt(_last(k), 2)} {'steigt' if bool(_rising(k).iloc[-1]) else 'fällt/flach'}, Kurs {_rel(_last(c), _last(k))} KAMA → {_dir_word(pos[-1])}"
    return pos, why


def _ma_ribbon(df: pd.DataFrame, p: dict[str, Any]):
    """Sechs EMAs (base·1 … base·6). Long, wenn alle aufwärts gefächert sind; raus, wenn der
    Ordnungs-Score (−1 … +1) unter `exit` fällt. Short spiegelbildlich."""
    c = df["close"]
    emas = [ind.ema(c, p["base"] * i).to_numpy() for i in range(1, 7)]
    with np.errstate(invalid="ignore"):
        score = np.mean([np.sign(emas[i] - emas[i + 1]) for i in range(5)], axis=0)
    sc = pd.Series(score, index=df.index)
    pos = state_machine(sc >= 1, sc < p["exit"], sc <= -1, sc > -p["exit"])
    why = f"EMA-Band (Basis {p['base']}) Ordnung {_fmt(_last(sc), 1)} (−1 … +1) → {_dir_word(pos[-1])}"
    return pos, why


def _chandelier(df: pd.DataFrame, p: dict[str, Any]):
    """Immer in Richtung des Chandelier-Exits: Long bis der Schluss unter den Long-Stop fällt, dann Short."""
    ce = ind.chandelier_exit(df, p["n"], p["mult"])
    d = ce["direction"]
    pos = state_machine(d == 1, d == -1, d == -1, d == 1)
    stop = ce["long_stop"] if _last(d) > 0 else ce["short_stop"]
    why = f"Chandelier-Exit ({p['n']}, {p['mult']}×ATR) Richtung {'aufwärts' if _last(d) > 0 else 'abwärts'}, Stop {_fmt(_last(stop), 2)} → {_dir_word(pos[-1])}"
    return pos, why


def _heikin_ashi(df: pd.DataFrame, p: dict[str, Any]):
    """Long nach `k` grünen Heikin-Ashi-Kerzen in Folge (optional nur bei steigender EMA); raus bei der ersten roten."""
    ha = ind.heikin_ashi(df)
    bull, bear = ha["close"] > ha["open"], ha["close"] < ha["open"]
    up, down = _trend_ok(df["close"], p["trend"])
    pos = state_machine(_consecutive(bull, p["k"]) & up, bear, _consecutive(bear, p["k"]) & down, bull)
    run = 0
    colors = np.sign(ha["close"].to_numpy() - ha["open"].to_numpy())
    for v in colors[::-1]:
        if v != colors[-1] or v == 0:
            break
        run += 1
    color = "grüne" if colors[-1] > 0 else "rote" if colors[-1] < 0 else "neutrale"
    why = f"{run} {color} Heikin-Ashi-Kerze(n) in Folge → {_dir_word(pos[-1])}"
    return pos, why


def _vortex(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn VI+ um mehr als `margin` über VI− liegt; raus, wenn VI− > VI+."""
    v = ind.vortex(df, p["n"])
    pos = state_machine(
        v["plus"] - v["minus"] > p["margin"], v["minus"] > v["plus"],
        v["minus"] - v["plus"] > p["margin"], v["plus"] > v["minus"],
    )
    why = f"Vortex({p['n']}) VI+ {_fmt(_last(v['plus']), 2)} / VI− {_fmt(_last(v['minus']), 2)} → {_dir_word(pos[-1])}"
    return pos, why


def _fib_pullback(df: pd.DataFrame, p: dict[str, Any]):
    """Fibonacci-Rücksetzer: Aufwärts-Bein = bestätigtes Swing-Tief vor bestätigtem Swing-Hoch
    (Bein ≥ min_leg_atr·ATR, seither kein höheres Hoch). Long, wenn das Tief das Retracement-Niveau
    `level` berührt und die Kerze grün darüber schließt. Stop: 78,6-%-Retracement, Ziel: Swing-Hoch.
    Swing-Punkte gelten erst nach `wing` Bestätigungskerzen (kein ZigZag/Lookahead)."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    w = p["wing"]
    a = ind.atr(df)
    pos_idx = pd.Series(np.arange(len(df), dtype=float), index=df.index)
    sh, sl = ind.swing_highs(h, w), ind.swing_lows(l, w)
    sh_val, sl_val = sh.ffill(), sl.ffill()
    sh_at = (pos_idx - w).where(sh.notna()).ffill()
    sl_at = (pos_idx - w).where(sl.notna()).ffill()
    leg = sh_val - sl_val
    big = leg >= p["min_leg_atr"] * a
    # Extremwerte seit der letzten Bestätigung (Bein noch intakt?)
    hh_since = h.groupby(sh.notna().cumsum().to_numpy()).cummax()
    ll_since = l.groupby(sl.notna().cumsum().to_numpy()).cummin()

    up_leg = (sl_at < sh_at) & big & (hh_since <= sh_val)
    lvl_l = sh_val - p["level"] * leg
    stop_l = sh_val - 0.786 * leg
    le = up_leg & (l <= lvl_l) & (c > lvl_l) & (c > o)

    dn_leg = (sh_at < sl_at) & big & (ll_since >= sl_val)
    lvl_s = sl_val + p["level"] * leg
    stop_s = sl_val + 0.786 * leg
    se = dn_leg & (h >= lvl_s) & (c < lvl_s) & (c < o)

    pos = managed_positions(
        le, se, c, long_stop=stop_l, long_target=sh_val, short_stop=stop_s, short_target=sl_val, max_hold=p["hold"]
    )
    if bool(up_leg.iloc[-1]):
        ctx = f"Aufwärts-Bein {_fmt(_last(sl_val), 2)} → {_fmt(_last(sh_val), 2)}, {p['level']:.3g}-Retracement bei {_fmt(_last(lvl_l), 2)}"
    elif bool(dn_leg.iloc[-1]):
        ctx = f"Abwärts-Bein {_fmt(_last(sh_val), 2)} → {_fmt(_last(sl_val), 2)}, {p['level']:.3g}-Retracement bei {_fmt(_last(lvl_s), 2)}"
    else:
        ctx = "kein intaktes Swing-Bein"
    return pos, f"{ctx} → {_dir_word(pos[-1])}"


# =========================================================================== #
# Momentum
# =========================================================================== #
def _elder_impulse(df: pd.DataFrame, p: dict[str, Any]):
    """Elder-Impuls: grün = EMA und MACD-Histogramm steigen, rot = beide fallen.
    Long nach `confirm` grünen Kerzen, raus bei rot (Short spiegelbildlich)."""
    c = df["close"]
    e = ind.ema(c, p["ema_n"])
    hist = ind.macd(c)["hist"]
    green = _rising(e) & _rising(hist)
    red = _falling(e) & _falling(hist)
    pos = state_machine(_consecutive(green, p["confirm"]), red, _consecutive(red, p["confirm"]), green)
    color = "grün" if bool(green.iloc[-1]) else "rot" if bool(red.iloc[-1]) else "blau"
    why = f"Elder-Impuls {color} (EMA{p['ema_n']} & MACD-Histogramm) → {_dir_word(pos[-1])}"
    return pos, why


def _stoch_rsi(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn %K den %D aus der überverkauften Zone (< os) nach oben kreuzt; raus, wenn %K oberhalb
    50 nach unten kreuzt. Optionaler Trendfilter (EMA steigt/fällt)."""
    c = df["close"]
    st = ind.stoch_rsi(c, p["rsi_n"], p["rsi_n"], 3, 3)
    k, d = st["k"], st["d"]
    up, down = _trend_ok(c, p["trend"])
    os_, ob = p["os"], 100 - p["os"]
    pos = state_machine(
        _cross_up(k, d) & (k.shift(1) < os_) & up, _cross_down(k, d) & (k.shift(1) > 50),
        _cross_down(k, d) & (k.shift(1) > ob) & down, _cross_up(k, d) & (k.shift(1) < 50),
    )
    why = f"Stoch-RSI %K {_fmt(_last(k))} / %D {_fmt(_last(d))} → {_dir_word(pos[-1])}"
    return pos, why


def _cci_momentum(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn der CCI über +Level steigt; raus, wenn er unter 0 fällt. Short spiegelbildlich."""
    cc = ind.cci(df, p["n"])
    lvl = p["level"]
    pos = state_machine(_cross_up(cc, lvl), cc < 0, _cross_down(cc, -lvl), cc > 0)
    why = f"CCI({p['n']}) {_fmt(_last(cc), 0)} (Schwelle ±{lvl}) → {_dir_word(pos[-1])}"
    return pos, why


def _roc_momentum(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn ROC(n) positiv ist und über seiner Signallinie (SMA) liegt; raus, wenn ROC negativ wird."""
    r = ind.roc(df["close"], p["n"])
    s = ind.sma(r, p["signal"])
    pos = state_machine((r > 0) & (r > s), r < 0, (r < 0) & (r < s), r > 0)
    why = f"ROC({p['n']}) {_fmt(_last(r), 2)}% (Signal {_fmt(_last(s), 2)}%) → {_dir_word(pos[-1])}"
    return pos, why


def _trix(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn TRIX über 0 und über seiner Signallinie liegt; raus, wenn TRIX unter die Signallinie fällt."""
    t = ind.trix(df["close"], p["n"], p["signal"])
    tr, sg = t["trix"], t["signal"]
    pos = state_machine((tr > 0) & (tr > sg), tr < sg, (tr < 0) & (tr < sg), tr > sg)
    why = f"TRIX({p['n']}) {_fmt(_last(tr), 2)} {_rel(_last(tr), _last(sg))} Signal {_fmt(_last(sg), 2)} → {_dir_word(pos[-1])}"
    return pos, why


def _coppock(df: pd.DataFrame, p: dict[str, Any]):
    """Coppock-Kurve (auf Kerzen übertragen): Long, wenn sie unter 0 nach oben dreht; raus nach zwei
    fallenden Werten. Short spiegelbildlich (dreht über 0 nach unten)."""
    roc_short = max(2, int(round(p["roc_long"] * 11 / 14)))
    cp = ind.coppock(df["close"], p["roc_long"], roc_short, p["wma_n"])
    turn_up = _rising(cp) & (cp.shift(1) <= cp.shift(2))
    turn_dn = _falling(cp) & (cp.shift(1) >= cp.shift(2))
    two_dn = _falling(cp) & (cp.shift(1) < cp.shift(2))
    two_up = _rising(cp) & (cp.shift(1) > cp.shift(2))
    pos = state_machine(turn_up & (cp < 0), two_dn, turn_dn & (cp > 0), two_up)
    why = f"Coppock {_fmt(_last(cp), 2)} {'steigt' if bool(_rising(cp).iloc[-1]) else 'fällt'} → {_dir_word(pos[-1])}"
    return pos, why


# =========================================================================== #
# Mean-Reversion
# =========================================================================== #
def _williams_r(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn %R die überverkaufte Zone (< −os) verlässt und der Kurs über der SMA(trend) liegt;
    raus in der überkauften Zone oder unter der SMA. Short spiegelbildlich."""
    c = df["close"]
    wr = ind.williams_r(df, p["n"])
    t = ind.sma(c, p["trend"])
    lo, hi = -p["os"], -(100 - p["os"])
    pos = state_machine(
        _cross_up(wr, lo) & (c > t), (wr > hi) | (c < t),
        _cross_down(wr, hi) & (c < t), (wr < lo) | (c > t),
    )
    why = f"Williams %R({p['n']}) {_fmt(_last(wr), 0)}, Kurs {_rel(_last(c), _last(t))} SMA{p['trend']} → {_dir_word(pos[-1])}"
    return pos, why


def _zscore_reversion(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn der Z-Score des Kurses (n Kerzen) unter −entry fällt; raus, sobald er über −exit steigt."""
    z = ind.zscore(df["close"], p["n"])
    pos = state_machine(z < -p["entry"], z > -p["exit"], z > p["entry"], z < p["exit"])
    why = f"Z-Score({p['n']}) {_fmt(_last(z), 2)} (Einstieg ±{p['entry']}) → {_dir_word(pos[-1])}"
    return pos, why


def _vwap_reversion(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn der Kurs mehr als k·ATR unter dem rollierenden VWAP liegt; raus bei Rückkehr zum VWAP."""
    c = df["close"]
    v = ind.rolling_vwap(df, p["n"])
    dev = (c - v) / ind.atr(df).replace(0, np.nan)
    pos = state_machine(dev < -p["k"], c >= v, dev > p["k"], c <= v)
    why = f"Abstand zum VWAP({p['n']}) {_fmt(_last(dev), 2)} ATR (Schwelle ±{p['k']}) → {_dir_word(pos[-1])}"
    return pos, why


def _connors_rsi2(df: pd.DataFrame, p: dict[str, Any]):
    """Connors RSI(2): Long, wenn Kurs über SMA(trend) und RSI(2) < entry; raus, wenn Schluss über SMA(exit_ma)."""
    c = df["close"]
    r2 = ind.rsi(c, 2)
    t, x = ind.sma(c, p["trend"]), ind.sma(c, p["exit_ma"])
    pos = state_machine((c > t) & (r2 < p["entry"]), c > x, (c < t) & (r2 > 100 - p["entry"]), c < x)
    why = f"RSI(2) {_fmt(_last(r2))}, Kurs {_rel(_last(c), _last(t))} SMA{p['trend']} → {_dir_word(pos[-1])}"
    return pos, why


def _rsi_divergence(df: pd.DataFrame, p: dict[str, Any]):
    """Bullische Divergenz: zwei bestätigte Swing-Tiefs (≤ max_gap Kerzen auseinander), Kurs macht ein
    tieferes Tief, RSI ein höheres (erstes RSI-Tief < 40). Einstieg an der Bestätigungskerze, Stop unter
    dem Swing-Tief, Ausstieg bei RSI > exit. Bärisch spiegelbildlich."""
    c = df["close"]
    r = ind.rsi(c, 14)
    rv = r.to_numpy()
    w, size = p["wing"], len(df)
    entries = {1: np.zeros(size, bool), -1: np.zeros(size, bool)}
    stops = {1: np.full(size, np.nan), -1: np.full(size, np.nan)}
    last_div = ""
    for side, piv in ((1, ind.swing_lows(df["low"], w)), (-1, ind.swing_highs(df["high"], w))):
        vals = piv.to_numpy()
        conf = np.flatnonzero(~np.isnan(vals))
        if len(conf) < 2:
            continue
        at = conf - w
        price, rr = vals[conf], rv[at]
        gap = at[1:] - at[:-1]
        ok = (gap >= w) & (gap <= p["max_gap"])
        with np.errstate(invalid="ignore"):
            if side == 1:
                div = ok & (price[1:] < price[:-1]) & (rr[1:] > rr[:-1]) & (rr[:-1] < 40)
            else:
                div = ok & (price[1:] > price[:-1]) & (rr[1:] < rr[:-1]) & (rr[:-1] > 60)
        hit = conf[1:][div]
        entries[side][hit] = True
        stops[side][hit] = price[1:][div]
        if len(hit) and size - 1 - hit[-1] <= 10:
            last_div = f"{'bullische' if side == 1 else 'bärische'} Divergenz {_ago(size - 1 - hit[-1])}, "
    pos = managed_positions(
        entries[1], entries[-1], c, long_stop=stops[1], short_stop=stops[-1],
        long_exit=r > p["exit"], short_exit=r < 100 - p["exit"], max_hold=p["hold"],
    )
    return pos, f"{last_div or 'keine frische Divergenz, '}RSI {_fmt(_last(r))} → {_dir_word(pos[-1])}"


def _pivot_bounce(df: pd.DataFrame, p: dict[str, Any]):
    """Pivot-Abprall: Long, wenn das Tief S1 (± tol·ATR) berührt und der Schluss wieder über S1 liegt
    (unter P). Ziel P, Stop S2. Pivots aus der vorherigen abgeschlossenen Periode."""
    c = df["close"]
    pv = ind.pivot_points(df, p["period_bars"])
    tol = p["tol_atr"] * ind.atr(df)
    le = (df["low"] <= pv["s1"] + tol) & (c > pv["s1"]) & (c < pv["p"])
    se = (df["high"] >= pv["r1"] - tol) & (c < pv["r1"]) & (c > pv["p"])
    pos = managed_positions(
        le, se, c, long_stop=pv["s2"], long_target=pv["p"], short_stop=pv["r2"], short_target=pv["p"],
        max_hold=p["period_bars"],
    )
    why = (
        f"Pivot {_fmt(_last(pv['p']), 2)}, S1 {_fmt(_last(pv['s1']), 2)}, R1 {_fmt(_last(pv['r1']), 2)} "
        f"(Periode {p['period_bars']} Kerzen) → {_dir_word(pos[-1])}"
    )
    return pos, why


# =========================================================================== #
# Ausbruch
# =========================================================================== #
def _keltner_breakout(df: pd.DataFrame, p: dict[str, Any]):
    """Long bei Schluss über dem oberen Keltner-Band (EMA ± mult·ATR); raus unter der Mittellinie."""
    c = df["close"]
    kc = ind.keltner(df, p["n"], p["mult"])
    pos = state_machine(c > kc["upper"], c < kc["mid"], c < kc["lower"], c > kc["mid"])
    why = f"Keltner({p['n']}, {p['mult']}) {_fmt(_last(kc['lower']), 2)}–{_fmt(_last(kc['upper']), 2)}, Kurs {_fmt(_last(c), 2)} → {_dir_word(pos[-1])}"
    return pos, why


def _squeeze_momentum(df: pd.DataFrame, p: dict[str, Any]):
    """Squeeze Momentum: Nach einem Squeeze (BB im Keltner-Kanal), der in den letzten `recent` Kerzen
    endete, Long bei positivem, steigendem Momentum; raus, wenn es unter 0 fällt oder zwei Kerzen sinkt."""
    sq = ind.squeeze_momentum(df, p["n"], 2.0, p["kc_mult"])
    on = sq["squeeze_on"].to_numpy(dtype=bool)
    released = pd.Series(np.r_[False, on[:-1] & ~on[1:]], index=df.index)
    fresh = _recent(released, p["recent"])
    m = sq["momentum"]
    up, dn = _rising(m), _falling(m)
    pos = state_machine(
        fresh & (m > 0) & up, (m < 0) | (dn & dn.shift(1, fill_value=False)),
        fresh & (m < 0) & dn, (m > 0) | (up & up.shift(1, fill_value=False)),
    )
    state = "Squeeze aktiv" if on[-1] else "Squeeze gelöst" if bool(fresh.iloc[-1]) else "kein Squeeze"
    why = f"{state}, Momentum {_fmt(_last(m), 2)} {'steigt' if bool(up.iloc[-1]) else 'fällt'} → {_dir_word(pos[-1])}"
    return pos, why


def _lw_breakout(df: pd.DataFrame, p: dict[str, Any]):
    """Larry-Williams-Volatilitätsausbruch: Long, wenn der Schluss ≥ Eröffnung + k·(Spanne der Vorkerze);
    Ausstieg nach `hold` Kerzen oder am 2-ATR-Trailing-Stop. Short spiegelbildlich."""
    o, c = df["open"], df["close"]
    prev_range = (df["high"] - df["low"]).shift(1)
    le = c >= o + p["k"] * prev_range
    se = c <= o - p["k"] * prev_range
    pos = managed_positions(le & (prev_range > 0), se & (prev_range > 0), c, trail=2.0 * ind.atr(df), max_hold=p["hold"])
    why = f"Ausbruchsmarke {_fmt(_last(o + p['k'] * prev_range), 2)} / {_fmt(_last(o - p['k'] * prev_range), 2)} (k={p['k']}) → {_dir_word(pos[-1])}"
    return pos, why


def _dual_thrust(df: pd.DataFrame, p: dict[str, Any]):
    """Dual Thrust (24/7-Variante): Range = max(HH − LC, HC − LL) der letzten n Perioden; Long über
    Periodeneröffnung + k·Range, Short darunter − k·Range; raus bei Rückkehr zur Periodeneröffnung."""
    c = df["close"]
    pp = ind.prior_periods(df, p["period_bars"], p["n"])
    rng = np.maximum(pp["high"] - pp["lc"], pp["hc"] - pp["low"])
    upper, lower = pp["open"] + p["k"] * rng, pp["open"] - p["k"] * rng
    pos = state_machine(c > upper, c < pp["open"], c < lower, c > pp["open"])
    why = f"Dual-Thrust-Band {_fmt(_last(lower), 2)}–{_fmt(_last(upper), 2)} (Periodeneröffnung {_fmt(_last(pp['open']), 2)}) → {_dir_word(pos[-1])}"
    return pos, why


def _range_breakout(df: pd.DataFrame, p: dict[str, Any]):
    """Perioden-Range-Ausbruch (Tageshoch/-tief, auf 24/7 übertragen): Long über dem Hoch der
    vorherigen Periode (+ buffer·ATR); raus unter deren Mitte. Short spiegelbildlich."""
    c = df["close"]
    pp = ind.prior_periods(df, p["period_bars"], 1)
    buf = p["buffer_atr"] * ind.atr(df)
    mid = (pp["high"] + pp["low"]) / 2
    pos = state_machine(c > pp["high"] + buf, c < mid, c < pp["low"] - buf, c > mid)
    why = f"Vorperiode ({p['period_bars']} Kerzen) Hoch {_fmt(_last(pp['high']), 2)} / Tief {_fmt(_last(pp['low']), 2)} → {_dir_word(pos[-1])}"
    return pos, why


def _pivot_breakout(df: pd.DataFrame, p: dict[str, Any]):
    """Pivot-Ausbruch: Long, wenn der Schluss R1 (+ buffer·ATR) überwindet; raus unter dem Pivot P."""
    c = df["close"]
    pv = ind.pivot_points(df, p["period_bars"])
    buf = p["buffer_atr"] * ind.atr(df)
    up, dn = pv["r1"] + buf, pv["s1"] - buf
    pos = state_machine(c > up, c < pv["p"], c < dn, c > pv["p"])
    why = f"R1 {_fmt(_last(pv['r1']), 2)} / P {_fmt(_last(pv['p']), 2)} / S1 {_fmt(_last(pv['s1']), 2)} → {_dir_word(pos[-1])}"
    return pos, why


# =========================================================================== #
# Volumen
# =========================================================================== #
def _mfi_reversal(df: pd.DataFrame, p: dict[str, Any]):
    """Long, wenn der Money Flow Index die überverkaufte Zone (< os) nach oben verlässt; raus über `exit`."""
    m = ind.mfi(df, p["n"])
    pos = state_machine(_cross_up(m, p["os"]), m > p["exit"], _cross_down(m, 100 - p["os"]), m < 100 - p["exit"])
    why = f"MFI({p['n']}) {_fmt(_last(m))} (Zonen {p['os']}/{100 - p['os']}) → {_dir_word(pos[-1])}"
    return pos, why


def _obv_trend(df: pd.DataFrame, p: dict[str, Any]):
    """Trend mit Volumenbestätigung: Long, wenn Kurs über EMA(n) UND OBV über seiner EMA(obv_n); raus,
    sobald eine der beiden Bedingungen bricht."""
    c = df["close"]
    e = ind.ema(c, p["n"])
    o = ind.obv(df)
    oe = ind.ema(o, p["obv_n"])
    pos = state_machine((c > e) & (o > oe), (c < e) | (o < oe), (c < e) & (o < oe), (c > e) | (o > oe))
    why = f"Kurs {_rel(_last(c), _last(e))} EMA{p['n']}, OBV {_rel(_last(o), _last(oe))} seinem Durchschnitt → {_dir_word(pos[-1])}"
    return pos, why


def _volume_breakout(df: pd.DataFrame, p: dict[str, Any]):
    """Volumen-Ausbruch: Volumen-Z-Score ≥ z, Kerzenspanne ≥ range_mult·ATR (der Vorkerze) und Schluss im
    oberen 30 % der Kerze → Long; Ausstieg per 2-ATR-Trailing-Stop oder nach `hold` Kerzen."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    a = ind.atr(df)
    vz = ind.zscore(df["volume"], p["n"])
    rng = h - l
    wide = (vz >= p["z"]) & (rng >= p["range_mult"] * a.shift(1))
    le = wide & (c > o) & (c >= l + 0.7 * rng)
    se = wide & (c < o) & (c <= h - 0.7 * rng)
    pos = managed_positions(le, se, c, trail=2.0 * a, max_hold=p["hold"])
    why = f"Volumen-Z-Score {_fmt(_last(vz), 1)}, Spanne {_fmt(_last(rng / a.shift(1)), 1)}×ATR → {_dir_word(pos[-1])}"
    return pos, why


# =========================================================================== #
# Kerzenmuster
# =========================================================================== #
def _candle_strategy(bull: tuple[str, ...], bear: tuple[str, ...], reversal: bool) -> Callable:
    """Baut eine Kerzenmuster-Strategie: Muster + Trendfilter (EMA(trend) steigt/fällt) + ATR-Ausstieg
    (am Schlusskurs nachgezogener Stop stop_atr·ATR, max. `hold` Kerzen, Gegenmuster beendet).
    Umkehrmuster zählen nur, wenn sie das 10-Kerzen-Tief (bzw. -Hoch) markieren."""
    bars = max(pat.PATTERNS[n].bars for n in bull + bear)

    def fn(df: pd.DataFrame, p: dict[str, Any]):
        c = df["close"]
        size = len(df)
        if reversal:
            at_low = (df["low"].rolling(bars, min_periods=1).min() <= df["low"].rolling(10, min_periods=10).min()).to_numpy()
            at_high = (df["high"].rolling(bars, min_periods=1).max() >= df["high"].rolling(10, min_periods=10).max()).to_numpy()
        else:
            at_low = at_high = np.ones(size, bool)
        hits = {n: pat.PATTERNS[n].fn(df).to_numpy(dtype=bool) & at_low for n in bull}
        hits.update({n: pat.PATTERNS[n].fn(df).to_numpy(dtype=bool) & at_high for n in bear})
        bull_m = np.logical_or.reduce([hits[n] for n in bull])
        bear_m = np.logical_or.reduce([hits[n] for n in bear])
        up, down = _trend_ok(c, p["trend"])
        le = bull_m & up.to_numpy(dtype=bool)
        se = bear_m & down.to_numpy(dtype=bool)
        pos = managed_positions(
            le, se, c, trail=p["stop_atr"] * ind.atr(df), max_hold=p["hold"], long_exit=bear_m, short_exit=bull_m
        )
        recent = [(int(np.flatnonzero(m)[-1]), n) for n, m in hits.items() if m.any()]
        if recent and size - 1 - max(recent)[0] <= 20:
            i, n = max(recent)
            seen = f"{pat.PATTERNS[n].label} {_ago(size - 1 - i)}"
        else:
            seen = "kein Muster in den letzten 20 Kerzen"
        if p["trend"] <= 0:
            trend = "Trendfilter aus"
        else:
            slope = "steigt" if bool(up.iloc[-1]) else "fällt" if bool(down.iloc[-1]) else "flach"
            trend = f"EMA{p['trend']} {slope}"
        return pos, f"{seen}; {trend} → {_dir_word(pos[-1])}"

    return fn


# =========================================================================== #
# Register
# =========================================================================== #
_CANDLE_DEFAULTS = {"trend": 50, "stop_atr": 2.0, "hold": 24}
_CANDLE_GRID = {"trend": [0, 50], "stop_atr": [1.5, 2.5], "hold": [12, 36]}

STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in [
        # ------------------------------------------------------------- Trend
        Strategy(
            "ema_trend", "EMA-Trendfolge",
            "Folgt dem Trend, wenn schnelle EMA über langsamer EMA liegt und ADX einen echten Trend bestätigt.",
            {"fast": 12, "slow": 26, "trend": 200, "adx_min": 20},
            {"fast": [9, 12, 20], "slow": [26, 50], "trend": [200], "adx_min": [15, 22]},
            _ema_trend, TREND, "Trend",
        ),
        Strategy(
            "supertrend", "Supertrend",
            "ATR-basierter Trendfilter; Richtung wechselt, wenn der Kurs das Band durchbricht.",
            {"n": 10, "mult": 3.0, "adx_min": 15},
            {"n": [10, 14], "mult": [2.5, 3.0, 4.0], "adx_min": [0, 18]},
            _supertrend, TREND, "Trend",
        ),
        Strategy(
            "trend_pullback", "Trend-Pullback",
            "Kauft Rücksetzer in einem intakten Aufwärtstrend, wenn der RSI wieder dreht.",
            {"trend": 50, "dip": 40},
            {"trend": [50, 100], "dip": [35, 40, 45]},
            _trend_pullback, TREND, "Trend",
        ),
        Strategy(
            "ichimoku", "Ichimoku-Wolke",
            "Long über der Wolke bei Tenkan > Kijun und Schluss über dem Kurs vor Kijun-Länge Kerzen (Chikou-Ersatz "
            "ohne Zukunftsdaten); Ausstieg unter Kijun.",
            {"tenkan": 9, "kijun": 26},
            {"tenkan": [7, 9, 12], "kijun": [22, 26, 34]},
            _ichimoku, TREND, "Trend",
        ),
        Strategy(
            "parabolic_sar", "Parabolic SAR",
            "Folgt der Richtung des Parabolic SAR (Wilder); optional nur bei ADX über Schwelle.",
            {"step": 0.02, "max_step": 0.2, "adx_min": 20},
            {"step": [0.02, 0.03], "max_step": [0.2, 0.3], "adx_min": [0, 20]},
            _parabolic_sar, TREND, "Trend",
        ),
        Strategy(
            "hull_ma", "Hull-MA-Trend",
            "Long, solange die Hull-MA mehrere Kerzen steigt und der Kurs darüber liegt; raus, sobald sie dreht.",
            {"n": 34, "confirm": 2},
            {"n": [21, 34, 55], "confirm": [1, 2]},
            _hull_ma, TREND, "Trend",
        ),
        Strategy(
            "triple_ema", "Dreifach-EMA-Fächer",
            "Long bei geordnetem Fächer EMA schnell > mittel > langsam; Ausstieg, wenn schnelle unter mittlere EMA fällt.",
            {"fast": 8, "mid": 21, "slow": 55},
            {"fast": [5, 8], "mid": [13, 21], "slow": [34, 55]},
            _triple_ema, TREND, "Trend",
        ),
        Strategy(
            "golden_cross", "Golden/Death Cross",
            "Klassisches Kreuz der gleitenden Durchschnitte SMA50/SMA200: darüber Long, darunter Short.",
            {"fast": 50, "slow": 200},
            {"fast": [20, 50], "slow": [100, 200]},
            _golden_cross, TREND, "Trend",
        ),
        Strategy(
            "adx_dmi", "ADX/DMI-Richtung",
            "Long, wenn +DI über −DI liegt und der ADX über der Schwelle steigt; raus beim DI-Kreuz.",
            {"n": 14, "adx_min": 20},
            {"n": [14, 20], "adx_min": [20, 25]},
            _adx_dmi, TREND, "Trend",
        ),
        Strategy(
            "aroon", "Aroon-Trend",
            "Long, wenn das jüngste Hoch frisch ist (Aroon-Up hoch) und das Tief lange zurückliegt.",
            {"n": 25, "level": 70},
            {"n": [14, 25, 50], "level": [70, 80]},
            _aroon, TREND, "Trend",
        ),
        Strategy(
            "kama_trend", "KAMA-Trend (Kaufman)",
            "Adaptiver Durchschnitt, der in Seitwärtsphasen fast stillsteht; Long über steigender KAMA.",
            {"n": 10, "band": 0.0},
            {"n": [10, 20], "band": [0.0, 0.5]},
            _kama_trend, TREND, "Trend",
        ),
        Strategy(
            "ma_ribbon", "EMA-Band (Ribbon)",
            "Sechs EMAs als Band: Long, wenn alle sauber aufwärts gefächert sind; raus, wenn die Ordnung zerfällt.",
            {"base": 8, "exit": 0.0},
            {"base": [5, 8, 12], "exit": [0.0, 0.5]},
            _ma_ribbon, TREND, "Trend",
        ),
        Strategy(
            "chandelier", "Chandelier-Exit-Trend",
            "Immer in Trendrichtung; gewechselt wird, wenn der Kurs den ATR-Stop vom n-Kerzen-Hoch/-Tief bricht.",
            {"n": 22, "mult": 3.0},
            {"n": [14, 22], "mult": [2.0, 3.0]},
            _chandelier, TREND, "Trend",
        ),
        Strategy(
            "heikin_ashi", "Heikin-Ashi-Trend",
            "Long nach mehreren grünen Heikin-Ashi-Kerzen in Folge, raus bei der ersten roten.",
            {"k": 3, "trend": 50},
            {"k": [2, 3, 4], "trend": [0, 50]},
            _heikin_ashi, TREND, "Trend",
        ),
        Strategy(
            "vortex", "Vortex-Indikator",
            "Long, wenn VI+ deutlich über VI− liegt; raus beim Kreuz.",
            {"n": 14, "margin": 0.05},
            {"n": [14, 21, 28], "margin": [0.0, 0.05]},
            _vortex, TREND, "Trend",
        ),
        Strategy(
            "fib_pullback", "Fibonacci-Rücksetzer",
            "Kauft den Rücksetzer auf das 50-/61,8-%-Retracement eines bestätigten Swing-Beins; Ziel altes Hoch, "
            "Stop am 78,6-%-Niveau.",
            {"wing": 3, "level": 0.5, "min_leg_atr": 2.0, "hold": 48},
            {"wing": [3, 5], "level": [0.5, 0.618], "min_leg_atr": [2.0, 3.0]},
            _fib_pullback, TREND, "Trend",
        ),
        # ---------------------------------------------------------- Momentum
        Strategy(
            "macd_momentum", "MACD-Momentum",
            "Steigt ein, wenn MACD die Signallinie in Trendrichtung kreuzt.",
            {"fast": 12, "slow": 26, "signal": 9, "trend": 200},
            {"fast": [8, 12], "slow": [21, 26], "signal": [9], "trend": [100, 200]},
            _macd_momentum, TREND, "Momentum",
        ),
        Strategy(
            "elder_impulse", "Elder-Impuls-System",
            "Grüne Impulskerzen (EMA und MACD-Histogramm steigen) → Long, rote → Short.",
            {"ema_n": 13, "confirm": 1},
            {"ema_n": [13, 21], "confirm": [1, 2]},
            _elder_impulse, TREND, "Momentum",
        ),
        Strategy(
            "stoch_rsi", "Stochastic-RSI-Kreuz",
            "Long, wenn %K den %D aus der überverkauften Zone nach oben kreuzt (optional mit Trendfilter).",
            {"rsi_n": 14, "os": 20, "trend": 100},
            {"rsi_n": [14], "os": [10, 20, 30], "trend": [0, 100]},
            _stoch_rsi, ALL_REGIMES, "Momentum",
        ),
        Strategy(
            "cci_momentum", "CCI-Momentum",
            "Long, wenn der CCI über +100 ausbricht; raus, wenn er unter 0 fällt.",
            {"n": 20, "level": 100},
            {"n": [14, 20, 30], "level": [100, 150]},
            _cci_momentum, TREND, "Momentum",
        ),
        Strategy(
            "roc_momentum", "ROC-Momentum",
            "Long bei positiver Kursänderungsrate über ihrer Signallinie; raus, wenn das Momentum negativ wird.",
            {"n": 14, "signal": 9},
            {"n": [9, 14, 21], "signal": [5, 9]},
            _roc_momentum, TREND, "Momentum",
        ),
        Strategy(
            "trix", "TRIX",
            "Dreifach geglättetes Momentum: Long über 0 und über der Signallinie.",
            {"n": 15, "signal": 9},
            {"n": [9, 15, 18], "signal": [7, 9]},
            _trix, TREND, "Momentum",
        ),
        Strategy(
            "coppock", "Coppock-Kurve",
            "Long, wenn die Coppock-Kurve unter der Nulllinie nach oben dreht (auf Kerzen übertragen).",
            {"roc_long": 14, "wma_n": 10},
            {"roc_long": [14, 28], "wma_n": [10, 20]},
            _coppock, ALL_REGIMES, "Momentum",
        ),
        # ---------------------------------------------------- Mean-Reversion
        Strategy(
            "rsi_reversion", "RSI-Rückkehr zur Mitte",
            "Kauft Überverkauft / verkauft Überkauft – nur in Seitwärtsphasen (niedriger ADX).",
            {"lower": 30, "upper": 70, "adx_max": 25},
            {"lower": [25, 30], "upper": [70, 75], "adx_max": [25, 35]},
            _rsi_reversion, RANGE, "Mean-Reversion",
        ),
        Strategy(
            "williams_r", "Williams %R",
            "Long, wenn %R die überverkaufte Zone im Aufwärtstrend verlässt; raus in der überkauften Zone.",
            {"n": 14, "os": 80, "trend": 200},
            {"n": [14, 21], "os": [80, 90], "trend": [100, 200]},
            _williams_r, ALL_REGIMES, "Mean-Reversion",
        ),
        Strategy(
            "zscore_reversion", "Z-Score-Rückkehr",
            "Handelt starke statistische Abweichungen vom gleitenden Mittel zurück zur Mitte.",
            {"n": 50, "entry": 2.0, "exit": 0.0},
            {"n": [20, 50], "entry": [2.0, 2.5], "exit": [0.0, 0.5]},
            _zscore_reversion, RANGE, "Mean-Reversion",
        ),
        Strategy(
            "vwap_reversion", "VWAP-Rückkehr",
            "Long, wenn der Kurs weit (in ATR) unter dem rollierenden VWAP liegt; Ziel ist der VWAP.",
            {"n": 48, "k": 2.0},
            {"n": [24, 48, 96], "k": [1.5, 2.0, 2.5]},
            _vwap_reversion, RANGE, "Mean-Reversion",
        ),
        Strategy(
            "connors_rsi2", "Connors RSI(2)",
            "Kauft extreme Kurzfrist-Schwäche (RSI(2) sehr tief) über der SMA200; Ausstieg über der SMA5.",
            {"entry": 10, "trend": 200, "exit_ma": 5},
            {"entry": [5, 10], "trend": [100, 200], "exit_ma": [5, 10]},
            _connors_rsi2, ALL_REGIMES, "Mean-Reversion",
        ),
        Strategy(
            "rsi_divergence", "RSI-Divergenz",
            "Tieferes Kurstief bei höherem RSI-Tief (bestätigte Swing-Punkte) → Long; spiegelbildlich Short.",
            {"wing": 3, "exit": 60, "max_gap": 60, "hold": 48},
            {"wing": [3, 5], "exit": [60, 70]},
            _rsi_divergence, ALL_REGIMES, "Mean-Reversion",
        ),
        Strategy(
            "pivot_bounce", "Pivot-Abprall",
            "Kauft den Abprall an S1 der Vorperiode mit Ziel Pivot-Linie (Stop S2); verkauft an R1.",
            {"period_bars": 24, "tol_atr": 0.0},
            {"period_bars": [12, 24, 48], "tol_atr": [0.0, 0.25]},
            _pivot_bounce, RANGE, "Mean-Reversion",
        ),
        # ---------------------------------------------------------- Ausbruch
        Strategy(
            "bb_squeeze", "Bollinger-Squeeze-Ausbruch",
            "Handelt den Ausbruch nach einer Phase sehr geringer Volatilität (mit Volumenbestätigung).",
            {"lookback": 150, "q": 0.2, "vol_mult": 1.0},
            {"lookback": [100, 200], "q": [0.15, 0.25], "vol_mult": [1.0, 1.3]},
            _bb_squeeze, RANGE, "Ausbruch",
        ),
        Strategy(
            "donchian", "Donchian-/Turtle-Ausbruch",
            "Kauft neue N-Kerzen-Hochs, verkauft neue N-Kerzen-Tiefs (klassisches Turtle-System).",
            {"entry_n": 20, "exit_n": 10},
            {"entry_n": [20, 40, 55], "exit_n": [10, 20]},
            _donchian, TREND, "Ausbruch",
        ),
        Strategy(
            "keltner_breakout", "Keltner-Ausbruch",
            "Long bei Schluss über dem oberen Keltner-Band (EMA + k·ATR); raus unter der Mittellinie.",
            {"n": 20, "mult": 2.0},
            {"n": [20, 30], "mult": [1.5, 2.0, 2.5]},
            _keltner_breakout, TREND, "Ausbruch",
        ),
        Strategy(
            "squeeze_momentum", "Squeeze-Momentum",
            "Bollinger-Bänder im Keltner-Kanal = Squeeze; nach dessen Ende in Richtung des Regressions-Momentums.",
            {"n": 20, "kc_mult": 1.5, "recent": 6},
            {"n": [20, 30], "kc_mult": [1.5, 2.0], "recent": [6, 12]},
            _squeeze_momentum, ALL_REGIMES, "Ausbruch",
        ),
        Strategy(
            "lw_breakout", "Larry-Williams-Ausbruch",
            "Volatilitätsausbruch: Schluss über Eröffnung + k × Spanne der Vorkerze; kurze Haltedauer.",
            {"k": 0.75, "hold": 6},
            {"k": [0.5, 0.75, 1.0], "hold": [3, 6, 12]},
            _lw_breakout, ALL_REGIMES, "Ausbruch",
        ),
        Strategy(
            "dual_thrust", "Dual Thrust",
            "Ausbruch über/unter Periodeneröffnung ± k × Range der letzten n Perioden (24/7-Variante).",
            {"period_bars": 24, "n": 2, "k": 0.5},
            {"period_bars": [24], "n": [1, 2, 4], "k": [0.4, 0.7]},
            _dual_thrust, ALL_REGIMES, "Ausbruch",
        ),
        Strategy(
            "range_breakout", "Perioden-Range-Ausbruch",
            "Tageshoch/-tief-Ausbruch für 24/7-Märkte: Long über dem Hoch der vorherigen Periode, raus unter deren Mitte.",
            {"period_bars": 24, "buffer_atr": 0.0},
            {"period_bars": [12, 24, 48], "buffer_atr": [0.0, 0.25]},
            _range_breakout, TREND, "Ausbruch",
        ),
        Strategy(
            "pivot_breakout", "Pivot-Ausbruch",
            "Long, wenn der Kurs R1 der Vorperiode überwindet; raus unter dem Pivot. Short unter S1.",
            {"period_bars": 24, "buffer_atr": 0.0},
            {"period_bars": [12, 24, 48], "buffer_atr": [0.0, 0.25]},
            _pivot_breakout, TREND, "Ausbruch",
        ),
        # ----------------------------------------------------------- Volumen
        Strategy(
            "mfi_reversal", "MFI-Umkehr",
            "Money Flow Index verlässt die überverkaufte Zone → Long (volumengewichteter RSI).",
            {"n": 14, "os": 20, "exit": 70},
            {"n": [10, 14], "os": [20, 30], "exit": [50, 70]},
            _mfi_reversal, RANGE, "Volumen",
        ),
        Strategy(
            "obv_trend", "OBV-Trendbestätigung",
            "Trend nur mit Volumen: Kurs über EMA und On-Balance-Volume über seinem Durchschnitt.",
            {"n": 50, "obv_n": 20},
            {"n": [20, 50], "obv_n": [10, 20, 30]},
            _obv_trend, TREND, "Volumen",
        ),
        Strategy(
            "volume_breakout", "Volumen-Ausbruch",
            "Ungewöhnlich hohes Volumen + weite Kerze mit starkem Schluss → Einstieg in Ausbruchsrichtung.",
            {"n": 50, "z": 2.0, "range_mult": 1.2, "hold": 24},
            {"z": [1.5, 2.0, 2.5], "range_mult": [1.0, 1.5]},
            _volume_breakout, ALL_REGIMES, "Volumen",
        ),
        # ------------------------------------------------------ Kerzenmuster
        Strategy(
            "candle_engulfing", "Engulfing-Muster",
            "Bullishes/bärisches Engulfing am 10-Kerzen-Tief/-Hoch, mit EMA-Trendfilter und ATR-Ausstieg.",
            dict(_CANDLE_DEFAULTS), dict(_CANDLE_GRID),
            _candle_strategy(("bullish_engulfing",), ("bearish_engulfing",), True), ALL_REGIMES, "Kerzenmuster",
        ),
        Strategy(
            "candle_hammer", "Hammer / Shooting Star",
            "Hammer am lokalen Tief (Long) bzw. Shooting Star am lokalen Hoch (Short), mit Trendfilter und ATR-Ausstieg.",
            dict(_CANDLE_DEFAULTS), dict(_CANDLE_GRID),
            _candle_strategy(("hammer",), ("shooting_star",), True), ALL_REGIMES, "Kerzenmuster",
        ),
        Strategy(
            "candle_star", "Morgen-/Abendstern",
            "Dreikerzen-Umkehr (Morgenstern/Abendstern) am lokalen Extrem, mit Trendfilter und ATR-Ausstieg.",
            dict(_CANDLE_DEFAULTS), dict(_CANDLE_GRID),
            _candle_strategy(("morning_star",), ("evening_star",), True), ALL_REGIMES, "Kerzenmuster",
        ),
        Strategy(
            "candle_soldiers", "Drei Soldaten / Drei Krähen",
            "Drei kräftige Kerzen in Folge als Fortsetzungssignal, mit Trendfilter und ATR-Ausstieg.",
            dict(_CANDLE_DEFAULTS), dict(_CANDLE_GRID),
            _candle_strategy(("three_white_soldiers",), ("three_black_crows",), False), TREND, "Kerzenmuster",
        ),
        Strategy(
            "inside_bar", "Innenstab-Ausbruch",
            "Schluss über dem Hoch (unter dem Tief) der Mutterkerze nach einem Innenstab, mit Trendfilter.",
            dict(_CANDLE_DEFAULTS), dict(_CANDLE_GRID),
            _candle_strategy(("inside_bar_breakout_bull",), ("inside_bar_breakout_bear",), False), TREND, "Kerzenmuster",
        ),
        Strategy(
            "pin_bar", "Pin-Bar-Ablehnung",
            "Pin-Bar mit langem Docht, der das Vorkerzen-Tief unterschreitet und abgelehnt wird, am lokalen Tief.",
            dict(_CANDLE_DEFAULTS), dict(_CANDLE_GRID),
            _candle_strategy(("bullish_pin_bar",), ("bearish_pin_bar",), True), ALL_REGIMES, "Kerzenmuster",
        ),
    ]
}


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"Unbekannte Strategie: {name}")
    return STRATEGIES[name]
