"""Tests der Strategie-Bibliothek und der Kerzenmuster."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from xrz.backtest.engine import BacktestConfig
from xrz.backtest.optimizer import optimize
from xrz.market.data import DemoMarket
from xrz.strategies import CATEGORIES, STRATEGIES, Strategy, get_strategy
from xrz.strategies import patterns as pat
from xrz.strategies.library import managed_positions, state_machine

NOW = 1_790_000_000.0
NAMES = list(STRATEGIES)


@pytest.fixture(scope="module")
def df2000() -> pd.DataFrame:
    return DemoMarket().ohlcv("BTC/USDT", "1h", 2000, now=NOW)


@pytest.fixture(scope="module")
def df1000() -> pd.DataFrame:
    return DemoMarket().ohlcv("ETH/USDT", "1h", 1000, now=NOW)


@pytest.fixture(scope="module")
def df4h() -> pd.DataFrame:
    return DemoMarket().ohlcv("SOL/USDT", "4h", 700, now=NOW)


# --------------------------------------------------------------------------- #
# Register / Metadaten
# --------------------------------------------------------------------------- #
def test_registry_size_names_and_categories():
    assert len(STRATEGIES) >= 35
    assert all(key == s.name for key, s in STRATEGIES.items())
    assert len({s.label for s in STRATEGIES.values()}) == len(STRATEGIES), "Labels müssen eindeutig sein"
    for s in STRATEGIES.values():
        assert s.category in CATEGORIES
        assert s.label and s.description
        assert s.good_in and set(s.good_in) <= {"aufwärts", "abwärts", "seitwärts"}
    used = {s.category for s in STRATEGIES.values()}
    assert used == set(CATEGORIES), f"Nicht alle Kategorien belegt: {set(CATEGORIES) - used}"
    assert get_strategy("ichimoku").category == "Trend"
    with pytest.raises(KeyError):
        get_strategy("gibt_es_nicht")


@pytest.mark.parametrize("name", NAMES)
def test_grid_is_small_and_consistent(name):
    s = STRATEGIES[name]
    assert set(s.grid) <= set(s.defaults), "Grid-Parameter brauchen Standardwerte"
    assert all(1 <= len(v) <= 3 for v in s.grid.values())
    combos = s.param_combos()
    assert 1 <= len(combos) <= 12
    assert len({tuple(sorted(c.items())) for c in combos}) == len(combos)


def test_strategy_category_default_and_validation():
    def fn(df, p):
        return np.zeros(len(df)), "nichts"

    legacy = Strategy("x", "X", "alt", {}, {}, fn, ["seitwärts"])  # alter Konstruktoraufruf bleibt gültig
    assert legacy.category in CATEGORIES
    with pytest.raises(ValueError):
        Strategy("y", "Y", "kaputt", {}, {}, fn, [], "Unsinn")
    d = STRATEGIES["pivot_bounce"].to_dict()
    assert {"name", "label", "description", "category", "good_in", "params", "grid"} <= set(d)
    assert d["params"] == STRATEGIES["pivot_bounce"].defaults


# --------------------------------------------------------------------------- #
# Kein Blick in die Zukunft
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", NAMES)
def test_no_lookahead_defaults(df2000, name):
    s = STRATEGIES[name]
    full = s.positions(df2000).to_numpy()
    for cut in (250, 513, 999, 1601):
        part = s.positions(df2000.iloc[:cut]).to_numpy()
        diff = np.flatnonzero(part != full[:cut])
        assert len(diff) == 0, f"{name} nutzt Zukunftsdaten (Abweichung ab Kerze {diff[:3]} bei Schnitt {cut})"


@pytest.mark.parametrize("name", NAMES)
def test_no_lookahead_other_params_and_timeframe(df4h, name):
    s = STRATEGIES[name]
    params = s.param_combos()[-1]
    full = s.positions(df4h, params).to_numpy()
    for cut in (321, 555):
        part = s.positions(df4h.iloc[:cut], params).to_numpy()
        assert (part == full[:cut]).all(), f"{name} {params} nutzt Zukunftsdaten (Schnitt {cut})"


@pytest.mark.parametrize("name", NAMES)
def test_future_changes_do_not_alter_past(df1000, name):
    """Werden alle Kerzen nach `cut` verfälscht, darf sich bis `cut` nichts ändern."""
    s = STRATEGIES[name]
    cut = 700
    rng = np.random.default_rng(7)
    fake = df1000.copy()
    scale = rng.uniform(0.9, 1.1, len(fake) - cut)
    for col in ("open", "high", "low", "close"):
        fake.iloc[cut:, fake.columns.get_loc(col)] *= scale
    fake.iloc[cut:, fake.columns.get_loc("volume")] *= rng.uniform(0.2, 5.0, len(fake) - cut)
    a = s.positions(df1000).to_numpy()[:cut]
    b = s.positions(fake).to_numpy()[:cut]
    assert (a == b).all(), f"{name} reagiert auf zukünftige Kerzen"


# --------------------------------------------------------------------------- #
# Ausgabe, Parameter, Laufzeit
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", NAMES)
def test_positions_values_and_why_text(df2000, name):
    s = STRATEGIES[name]
    pos, why = s.evaluate(df2000)
    assert len(pos) == len(df2000) and pos.index.equals(df2000.index)
    assert not pos.isna().any()
    assert set(np.unique(pos)) <= {-1.0, 0.0, 1.0}
    assert isinstance(why, str) and len(why) > 10
    assert "→" in why and any(w in why for w in ("LONG", "SHORT", "neutral"))
    assert "nan" not in why.lower()


@pytest.mark.parametrize("name", NAMES)
def test_every_param_combo_runs(df1000, name):
    s = STRATEGIES[name]
    for params in s.param_combos():
        pos = s.positions(df1000.iloc[:600], params)
        assert set(np.unique(pos)) <= {-1.0, 0.0, 1.0}, f"{name} {params}"


@pytest.mark.parametrize("name", NAMES)
def test_short_and_empty_history_do_not_crash(df1000, name):
    s = STRATEGIES[name]
    for n in (0, 1, 5, 40):
        pos, why = s.evaluate(df1000.iloc[:n])
        assert len(pos) == n and isinstance(why, str) and why
        assert set(np.unique(pos)) <= {-1.0, 0.0, 1.0}


def test_most_strategies_trade_with_defaults(df2000):
    trading = [n for n, s in STRATEGIES.items() if (s.positions(df2000) != 0).any()]
    assert len(trading) >= 0.8 * len(STRATEGIES), f"Nur {len(trading)} von {len(STRATEGIES)} handeln"


@pytest.mark.parametrize("name", NAMES)
def test_runtime_budget(df1000, name):
    s = STRATEGIES[name]
    s.positions(df1000)  # Aufwärmen (Imports, Caches)
    best = min(_timed(lambda: s.positions(df1000)) for _ in range(3))
    assert best < 0.060, f"{name} braucht {best * 1000:.1f} ms für 1000 Kerzen"


def _timed(f) -> float:
    t = time.perf_counter()
    f()
    return time.perf_counter() - t


def test_optimizer_over_all_strategies_is_fast(df1000):
    t = time.perf_counter()
    reps = optimize(df1000, BacktestConfig())
    elapsed = time.perf_counter() - t
    assert set(reps) == set(STRATEGIES)
    assert elapsed < 20.0, f"Optimierung dauerte {elapsed:.1f} s"
    total = sum(r.weight for r in reps.values())
    assert total == pytest.approx(1.0) or total == 0


# --------------------------------------------------------------------------- #
# Positions-Helfer
# --------------------------------------------------------------------------- #
def test_state_machine_basic():
    le = [False, True, False, False, False, False]
    lx = [False, False, False, True, False, False]
    se = [False, False, False, False, True, False]
    sx = [False] * 6
    assert list(state_machine(le, lx, se, sx)) == [0, 1, 1, 0, -1, -1]


def test_managed_positions_stop_target_hold_and_trail():
    close = np.array([100, 101, 102, 99, 98, 100, 104, 106, 100, 100.0])
    entry = np.zeros(10, bool)
    entry[1] = True
    none = np.zeros(10, bool)
    # fester Stop 100 → Ausstieg bei Schluss 99 (Index 3)
    pos = managed_positions(entry, none, close, long_stop=np.full(10, 100.0))
    assert list(pos[:5]) == [0, 1, 1, 0, 0]
    # Ziel 103.5 → Ausstieg bei 104 (Index 6)
    pos = managed_positions(entry, none, close, long_stop=np.full(10, 90.0), long_target=np.full(10, 103.5))
    assert list(pos[:8]) == [0, 1, 1, 1, 1, 1, 0, 0]
    # Haltedauer 2 Kerzen
    pos = managed_positions(entry, none, close, max_hold=2)
    assert list(pos[:5]) == [0, 1, 1, 0, 0]
    # Trailing 3 Punkte: Hoch 106 → Stop 103 → Ausstieg bei 100 (Index 8)
    pos = managed_positions(entry, none, close, trail=np.full(10, 3.0))
    assert list(pos) == [0, 1, 1, 0, 0, 0, 0, 0, 0, 0]  # 99 < 102−3 → schon Index 3 raus
    pos = managed_positions(entry, none, close, trail=np.full(10, 5.0))
    assert list(pos) == [0, 1, 1, 1, 1, 1, 1, 1, 0, 0]
    # Einstieg ohne gültigen Stop (Stop über Kurs) wird verworfen
    assert not managed_positions(entry, none, close, long_stop=np.full(10, 200.0)).any()


def test_managed_positions_short_and_flip():
    close = np.array([100, 99, 98, 97, 99, 101, 100.0])
    le = np.array([0, 0, 0, 0, 0, 1, 0], bool)
    se = np.array([0, 1, 0, 0, 0, 0, 0], bool)
    pos = managed_positions(le, se, close, short_stop=np.full(7, 102.0), short_target=np.full(7, 90.0))
    assert list(pos) == [0, -1, -1, -1, -1, 1, 1]
    exit_short = np.array([0, 0, 0, 1, 0, 0, 0], bool)
    pos = managed_positions(np.zeros(7, bool), se, close, short_exit=exit_short)
    assert list(pos) == [0, -1, -1, 0, 0, 0, 0]


# --------------------------------------------------------------------------- #
# Kerzenmuster (handgebaute Kerzen)
# --------------------------------------------------------------------------- #
def candles(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    return df


def last(fn, rows) -> bool:
    return bool(fn(candles(rows)).iloc[-1])


def test_engulfing():
    assert last(pat.bullish_engulfing, [(10, 10.5, 8.8, 9), (8.9, 10.6, 8.7, 10.4)])
    assert not last(pat.bullish_engulfing, [(10, 10.5, 8.8, 9), (9.2, 10, 9.1, 9.8)])  # Körper nicht umschlossen
    assert not last(pat.bullish_engulfing, [(9, 10.5, 8.8, 10), (8.9, 10.6, 8.7, 10.4)])  # Vorkerze grün
    assert last(pat.bearish_engulfing, [(9, 10.2, 8.9, 10), (10.1, 10.3, 8.7, 8.8)])
    assert not last(pat.bearish_engulfing, [(9, 10.2, 8.9, 10), (10.1, 10.3, 9.5, 9.6)])


def test_hammer_and_shooting_star():
    assert last(pat.hammer, [(10, 10.05, 8, 9.9)])
    assert not last(pat.hammer, [(10, 11, 8, 9.9)])  # langer oberer Docht
    assert not last(pat.hammer, [(8.2, 10.1, 8, 10)])  # großer Körper
    assert last(pat.shooting_star, [(10, 12, 9.95, 10.1)])
    assert not last(pat.shooting_star, [(10, 10.05, 8, 9.9)])
    assert not last(pat.hammer, [(10, 10, 10, 10)])  # Kerze ohne Spanne


def test_morning_and_evening_star():
    first, star = (10, 10.1, 8.9, 9), (8.8, 8.9, 8.6, 8.75)
    assert last(pat.morning_star, [first, star, (8.8, 9.8, 8.75, 9.7)])
    assert not last(pat.morning_star, [first, star, (8.8, 9.4, 8.75, 9.3)])  # schließt unter der Mitte
    assert not last(pat.morning_star, [first, (8.9, 9.6, 8.6, 9.5), (8.8, 9.8, 8.75, 9.7)])  # Stern zu groß
    first, star = (9, 10.1, 8.9, 10), (10.2, 10.4, 10.1, 10.25)
    assert last(pat.evening_star, [first, star, (10.2, 10.25, 9.2, 9.3)])
    assert not last(pat.evening_star, [first, star, (10.2, 10.25, 9.6, 9.7)])


def test_three_soldiers_and_crows():
    soldiers = [(10, 11, 9.95, 10.9), (10.5, 11.9, 10.45, 11.8), (11.4, 12.8, 11.35, 12.7)]
    assert last(pat.three_white_soldiers, soldiers)
    assert not last(pat.three_white_soldiers, soldiers[:2] + [(11.9, 12.8, 11.85, 12.7)])  # Eröffnung über Vorschluss
    assert not last(pat.three_white_soldiers, [soldiers[0], (11.8, 11.9, 10.45, 10.5), soldiers[2]])
    crows = [(12.7, 12.75, 11.8, 11.9), (12.2, 12.25, 10.9, 11.0), (11.5, 11.55, 10.0, 10.1)]
    assert last(pat.three_black_crows, crows)
    assert not last(pat.three_black_crows, [crows[0], (11.0, 12.25, 10.9, 12.2), crows[2]])


def test_inside_bar_and_breakout():
    mother, inside = (10, 12, 8, 11), (10.5, 11.5, 9, 11)
    assert last(pat.inside_bar, [mother, inside])
    assert not last(pat.inside_bar, [mother, (10.5, 12.5, 9, 11)])
    up = pat.inside_bar_breakout(candles([mother, inside, (11, 12.5, 10.8, 12.3)])).iloc[-1]
    assert bool(up["bull"]) and not bool(up["bear"])
    flat = pat.inside_bar_breakout(candles([mother, inside, (11, 11.9, 10.8, 11.8)])).iloc[-1]
    assert not bool(flat["bull"]) and not bool(flat["bear"])
    down = pat.inside_bar_breakout(candles([mother, inside, (9, 9.1, 7.2, 7.5)])).iloc[-1]
    assert bool(down["bear"]) and not bool(down["bull"])


def test_pin_bars():
    prev = (10, 10.5, 9.5, 10.2)
    assert last(pat.bullish_pin_bar, [prev, (9.9, 10.0, 8.5, 9.95)])
    assert not last(pat.bullish_pin_bar, [(10, 10.5, 8.4, 10.2), (9.9, 10.0, 8.5, 9.95)])  # sticht nicht heraus
    assert not last(pat.bullish_pin_bar, [prev, (9.0, 10.0, 8.5, 9.2)])  # Körper nicht im oberen Drittel
    assert last(pat.bearish_pin_bar, [prev, (10.3, 11.6, 10.25, 10.3)])
    assert not last(pat.bearish_pin_bar, [prev, (10.6, 11.6, 10.55, 10.6)])  # Körper über dem Vorkerzen-Hoch


def test_detect_all_and_register(df2000):
    found = pat.detect_all(df2000)
    assert list(found.columns) == list(pat.PATTERNS)
    assert all(found[c].dtype == bool for c in found.columns)
    assert found.to_numpy().any()
    part = pat.detect_all(df2000.iloc[:777])
    assert (part.to_numpy() == found.iloc[:777].to_numpy()).all()
    assert {p.direction for p in pat.PATTERNS.values()} == {1, -1}
    assert all(p.label for p in pat.PATTERNS.values())
