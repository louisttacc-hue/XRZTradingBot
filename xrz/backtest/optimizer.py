"""Walk-Forward-Optimierung: Parameter werden auf älteren Daten (In-Sample) gewählt
und auf neueren, ungesehenen Daten (Out-of-Sample) geprüft. Nur Strategien, die
auch out-of-sample funktionieren, bekommen Gewicht im Ensemble – das schützt vor
Überanpassung ("Curve Fitting")."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..analysis import indicators as ind
from ..strategies import STRATEGIES, Strategy
from .engine import BacktestConfig, run_backtest, score

log = logging.getLogger(__name__)

WARMUP = 200


@dataclass
class StrategyReport:
    name: str
    params: dict[str, Any]
    is_metrics: dict[str, float]
    oos_metrics: dict[str, float]
    full_metrics: dict[str, float]
    weight: float
    is_score: float
    oos_score: float


def optimize(
    df: pd.DataFrame,
    cfg: BacktestConfig,
    strategies: dict[str, Strategy] | None = None,
    split: float = 0.65,
    max_combos: int = 16,
) -> dict[str, StrategyReport]:
    strategies = strategies or STRATEGIES
    n = len(df)
    if n < WARMUP + 120:
        raise ValueError(f"Zu wenig Historie ({n} Kerzen) – mindestens {WARMUP + 120} nötig")
    split_idx = WARMUP + int((n - WARMUP) * split)
    atr_s = ind.atr(df, 14)
    reports: dict[str, StrategyReport] = {}
    for name, strat in strategies.items():
        best: tuple[float, dict[str, Any], dict[str, float], pd.Series] | None = None
        for params in strat.param_combos(max_combos):
            try:
                pos = strat.positions(df, params)
            except Exception as exc:  # pragma: no cover - defensiv
                log.warning("Strategie %s mit %s fehlgeschlagen: %s", name, params, exc)
                continue
            res = run_backtest(df, pos, cfg, WARMUP, split_idx, atr_s)
            s = score(res.metrics)
            if best is None or s > best[0]:
                best = (s, {**strat.defaults, **params}, res.metrics, pos)
        if best is None:
            continue
        is_s, params, is_m, pos = best
        oos = run_backtest(df, pos, cfg, split_idx, n, atr_s)
        full = run_backtest(df, pos, cfg, WARMUP, n, atr_s)
        oos_s = score(oos.metrics)
        raw = 0.6 * oos_s + 0.4 * is_s
        if oos.metrics["trades"] >= 2 and oos.metrics["profit_factor"] < 0.9:
            raw *= 0.3
        if oos.metrics["trades"] == 0:
            raw *= 0.5
        reports[name] = StrategyReport(
            name=name, params=params, is_metrics=is_m, oos_metrics=oos.metrics,
            full_metrics=full.metrics, weight=max(0.0, raw), is_score=is_s, oos_score=oos_s,
        )
    total = sum(r.weight for r in reports.values())
    for r in reports.values():
        r.weight = r.weight / total if total > 0 else 0.0
    return reports


def ensemble_backtest(
    df: pd.DataFrame, reports: dict[str, StrategyReport], cfg: BacktestConfig, threshold: float = 0.35
) -> dict[str, float]:
    """Testet das gewichtete Ensemble als Ganzes (so wie es live handeln würde)."""
    score_s = pd.Series(0.0, index=df.index)
    for name, r in reports.items():
        if r.weight > 0:
            score_s += STRATEGIES[name].positions(df, r.params).fillna(0) * r.weight
    target = pd.Series(0.0, index=df.index)
    target[score_s >= threshold] = 1.0
    if cfg.allow_short:
        target[score_s <= -threshold] = -1.0
    # Halten, solange der Score nicht klar dreht (Hysterese wie live)
    held = target.copy()
    cur = 0.0
    for i, (t, s) in enumerate(zip(target.to_numpy(), score_s.to_numpy())):
        if cur == 0 and t != 0:
            cur = t
        elif cur > 0 and s < 0:
            cur = -1.0 if t < 0 else 0.0
        elif cur < 0 and s > 0:
            cur = 1.0 if t > 0 else 0.0
        held.iloc[i] = cur
    return run_backtest(df, held, cfg, WARMUP, len(df)).metrics
