"""Komplett-Analyse eines Marktes: Indikatoren, Marktphase, übergeordneter Trend,
Unterstützungen/Widerstände, alle Strategien → gewichteter Ensemble-Score."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..analysis import indicators as ind
from ..analysis.regime import Regime, detect_regime, support_resistance
from ..backtest.engine import BacktestConfig
from ..backtest.optimizer import ensemble_backtest, optimize
from ..config import Settings
from ..market.data import MarketData, tf_seconds
from ..storage import Storage
from ..strategies import STRATEGIES

log = logging.getLogger(__name__)


@dataclass
class SignalDetail:
    name: str
    label: str
    signal: int
    weight: float
    why: str


@dataclass
class Analysis:
    symbol: str
    timeframe: str
    price: float
    ts: float
    regime: Regime
    htf_regime: Regime | None
    levels: dict[str, list[float]]
    indicators: dict[str, float]
    signals: list[SignalDetail]
    score: float
    raw_score: float
    direction: int
    active_long: int
    active_short: int
    atr: float
    extension_atr: float
    stop_long: float
    stop_short: float
    tp_long: float
    tp_short: float
    has_edge: bool
    tv_signal: str = ""
    notes: list[str] = field(default_factory=list)
    backtest: dict[str, float] = field(default_factory=dict)

    @property
    def direction_word(self) -> str:
        return {1: "LONG (kaufen)", -1: "SHORT (verkaufen)"}.get(self.direction, "abwarten")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["direction_word"] = self.direction_word
        return d

    def to_text(self, compact: bool = False) -> str:
        p = _fmt_price(self.price)
        lines = [
            f"📊 {self.symbol} ({self.timeframe}) – Kurs {p}",
            f"Marktphase: {self.regime.label} | ADX {self.regime.adx:.0f} | Volatilität {self.regime.volatility}",
        ]
        if self.htf_regime:
            lines.append(f"Übergeordneter Trend: {self.htf_regime.label}")
        i = self.indicators
        lines.append(
            f"RSI {i.get('rsi', 0):.0f} | MACD-Hist {i.get('macd_hist', 0):+.4g} | ATR {i.get('atr_pct', 0):.2f}% | "
            f"Abstand EMA20 {self.extension_atr:+.1f} ATR"
        )
        if self.levels.get("support") or self.levels.get("resistance"):
            sup = ", ".join(_fmt_price(x) for x in self.levels.get("support", [])[:3]) or "–"
            res = ", ".join(_fmt_price(x) for x in self.levels.get("resistance", [])[:3]) or "–"
            lines.append(f"Unterstützungen: {sup} | Widerstände: {res}")
        lines.append(
            f"Ensemble-Score: {self.score:+.2f} (−1 … +1) → {self.direction_word} "
            f"[{self.active_long} Strategien long, {self.active_short} short]"
        )
        if self.tv_signal:
            lines.append(f"TradingView-Signal: {self.tv_signal}")
        if not compact:
            lines.append("Strategien (Gewicht aus Backtest):")
            for s in sorted(self.signals, key=lambda x: -x.weight):
                mark = "🟢" if s.signal > 0 else "🔴" if s.signal < 0 else "⚪"
                lines.append(f"  {mark} {s.label} ({s.weight * 100:.0f}%): {s.why}")
            if self.direction != 0:
                stop = self.stop_long if self.direction > 0 else self.stop_short
                tp = self.tp_long if self.direction > 0 else self.tp_short
                lines.append(f"Vorschlag: Stop-Loss {_fmt_price(stop)}, Take-Profit {_fmt_price(tp)}")
            if self.backtest:
                b = self.backtest
                lines.append(
                    f"Ensemble-Backtest: {b.get('total_return_pct', 0):+.1f}% (Buy&Hold {b.get('buy_hold_pct', 0):+.1f}%), "
                    f"{int(b.get('trades', 0))} Trades, Trefferquote {b.get('win_rate_pct', 0):.0f}%, "
                    f"max. Drawdown {b.get('max_drawdown_pct', 0):.1f}%"
                )
        if not self.has_edge:
            lines.append("⚠️ Kein Strategie-Vorteil im Backtest nachweisbar → Bot handelt diesen Markt nicht.")
        for n in self.notes:
            lines.append(f"• {n}")
        return "\n".join(lines)


def _fmt_price(x: float) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    if x >= 1000:
        return f"{x:,.0f}".replace(",", ".")
    if x >= 1:
        return f"{x:.2f}"
    return f"{x:.5f}"


class Analyzer:
    def __init__(self, settings: Callable[[], Settings], market: MarketData, storage: Storage):
        self._settings = settings
        self.market = market
        self.storage = storage
        self._deep_lock = asyncio.Lock()

    @property
    def s(self) -> Settings:
        return self._settings()

    def bt_config(self, timeframe: str) -> BacktestConfig:
        bars_per_year = 365 * 86400 / tf_seconds(timeframe)
        return BacktestConfig.from_settings(self.s.risk, self.s.trading.allow_short, bars_per_year)

    # ------------------------------------------------------------ Tiefenanalyse
    async def deep_analysis(self, symbol: str, timeframe: str | None = None) -> dict[str, Any]:
        """Backtestet alle Strategien (Walk-Forward) und speichert Gewichte."""
        tf = timeframe or self.s.trading.timeframe
        async with self._deep_lock:
            df = await self.market.ohlcv(symbol, tf, self.s.trading.history_candles)
            cfg = self.bt_config(tf)
            reports = await asyncio.to_thread(optimize, df, cfg)
            ens = await asyncio.to_thread(ensemble_backtest, df, reports, cfg, self.s.trading.entry_threshold)
            for r in reports.values():
                self.storage.save_strategy_stats(
                    symbol, tf, r.name, r.params,
                    {"is": r.is_metrics, "oos": r.oos_metrics, "full": r.full_metrics,
                     "is_score": r.is_score, "oos_score": r.oos_score},
                    r.weight,
                )
            self.storage.kv_set(f"ensemble_bt:{symbol}:{tf}", ens)
            self.storage.kv_set(f"deep_ts:{symbol}:{tf}", time.time())
            return {"reports": reports, "ensemble": ens, "candles": len(df)}

    def deep_analysis_age(self, symbol: str, timeframe: str) -> float:
        ts = self.storage.kv_get(f"deep_ts:{symbol}:{timeframe}", 0) or 0
        return time.time() - ts

    # ----------------------------------------------------------- Lernfaktoren
    def learning_factors(self) -> dict[str, float]:
        """Bewertet Strategien anhand echter (Paper/Live) Trades: Strategien, die
        bei gewinnenden Trades richtig lagen, bekommen mehr Gewicht – und umgekehrt."""
        trades = self.storage.closed_trades(limit=40)
        factors = {name: 1.0 for name in STRATEGIES}
        for name in STRATEGIES:
            rs = [t.r_multiple or 0.0 for t in trades if np.sign(t.strategies.get(name, 0)) == t.direction]
            if rs:
                factors[name] = float(np.clip(1.0 + 0.12 * sum(rs[:20]), 0.4, 1.6))
        return factors

    # ------------------------------------------------------------------ Analyse
    async def analyze(self, symbol: str, timeframe: str | None = None) -> Analysis:
        s = self.s
        tf = timeframe or s.trading.timeframe
        df, htf_df = await asyncio.gather(
            self.market.ohlcv(symbol, tf, max(400, min(s.trading.history_candles, 1000))),
            self._safe_ohlcv(symbol, s.trading.higher_timeframe, 300),
        )
        stats = self.storage.strategy_stats(symbol, tf)
        if not stats:
            try:
                await self.deep_analysis(symbol, tf)
                stats = self.storage.strategy_stats(symbol, tf)
            except Exception as exc:
                log.warning("Tiefenanalyse für %s fehlgeschlagen: %s", symbol, exc)
        return await asyncio.to_thread(self._analyze_sync, symbol, tf, df, htf_df, stats)

    async def _safe_ohlcv(self, symbol: str, tf: str, n: int) -> pd.DataFrame | None:
        try:
            return await self.market.ohlcv(symbol, tf, n)
        except Exception as exc:
            log.info("Übergeordneter Zeitrahmen %s für %s nicht verfügbar: %s", tf, symbol, exc)
            return None

    def _analyze_sync(
        self, symbol: str, tf: str, df: pd.DataFrame, htf_df: pd.DataFrame | None, stats: dict[str, dict[str, Any]]
    ) -> Analysis:
        s = self.s
        x = ind.add_all(df)
        last = x.iloc[-1]
        price = float(last["close"])
        atr_v = float(last["atr"]) if not np.isnan(last["atr"]) else price * 0.01
        regime = detect_regime(df)
        htf_regime = detect_regime(htf_df) if htf_df is not None and len(htf_df) > 60 else None
        levels = support_resistance(df)
        learn = self.learning_factors()

        signals: list[SignalDetail] = []
        has_stats = bool(stats)
        for name, strat in STRATEGIES.items():
            st = stats.get(name, {})
            params = st.get("params") or strat.defaults
            weight = float(st.get("weight", 0.0)) if has_stats else 1.0 / len(STRATEGIES)
            weight *= learn.get(name, 1.0)
            try:
                pos, why = strat.evaluate(df, params)
                sig = int(np.sign(pos.iloc[-1]))
            except Exception as exc:  # pragma: no cover
                sig, why = 0, f"Fehler: {exc}"
            signals.append(SignalDetail(name, strat.label, sig, weight, why))

        total_w = sum(sd.weight for sd in signals)
        for sd in signals:
            sd.weight = sd.weight / total_w if total_w > 0 else 0.0
        raw = float(sum(sd.signal * sd.weight for sd in signals))
        # Ohne bestandenen Backtest wird nur analysiert, aber nie gehandelt.
        has_edge = total_w > 0 and has_stats and any(float(st.get("weight", 0)) > 0 for st in stats.values())
        score = raw
        notes: list[str] = []

        # TradingView-Alert einbeziehen
        tv_text = ""
        tv = self.storage.latest_signal("tradingview", symbol, s.trading.tradingview_signal_minutes * 60)
        if tv:
            act = tv["action"]
            tv_dir = 1 if act in ("buy", "long") else -1 if act in ("sell", "short") else 0
            if tv_dir:
                score += s.trading.tradingview_signal_weight * tv_dir
                tv_text = f"{act.upper()} vor {int((time.time() - tv['ts']) / 60)} Min."
        # Übergeordneter Trend als Filter
        if s.trading.higher_tf_filter and htf_regime is not None:
            if score > 0 and htf_regime.trend_dir < 0:
                score *= 0.4
                notes.append(f"Long gegen übergeordneten Abwärtstrend ({s.trading.higher_timeframe}) → Score gedämpft")
            elif score < 0 and htf_regime.trend_dir > 0:
                score *= 0.4
                notes.append(f"Short gegen übergeordneten Aufwärtstrend ({s.trading.higher_timeframe}) → Score gedämpft")
        score = float(np.clip(score, -1, 1))

        active_long = sum(1 for sd in signals if sd.signal > 0 and sd.weight > 0)
        active_short = sum(1 for sd in signals if sd.signal < 0 and sd.weight > 0)
        ext = float((price - last["ema20"]) / atr_v) if not np.isnan(last["ema20"]) and atr_v > 0 else 0.0

        direction = 0
        th = s.trading.entry_threshold
        if has_edge and score >= th and (active_long >= s.trading.min_active_strategies or tv_text):
            direction = 1
        elif has_edge and score <= -th and (active_short >= s.trading.min_active_strategies or tv_text):
            direction = -1
        if direction != 0 and direction * ext > s.trading.max_extension_atr:
            notes.append(
                f"Kurs ist {abs(ext):.1f} ATR von der EMA20 entfernt → zu weit gelaufen, warte auf Rücksetzer"
            )
            direction = 0
        if regime.volatility == "hoch" and regime.vol_percentile > 95:
            notes.append("Extrem hohe Volatilität – Positionsgröße wird automatisch kleiner (ATR-basiert)")

        risk = s.risk
        stop_dist = atr_v * risk.stop_atr_mult
        indicators = {
            k: float(last[k])
            for k in ("rsi", "macd", "macd_hist", "atr", "atr_pct", "adx", "plus_di", "minus_di", "bb_pctb",
                      "bb_width", "stoch_k", "ema20", "ema50", "ema200", "vwap", "vol_z", "supertrend_dir")
            if k in last and not pd.isna(last[k])
        }
        indicators["change_24h_pct"] = float(
            (price / df["close"].iloc[max(0, len(df) - 1 - int(86400 / tf_seconds(tf)))] - 1) * 100
        ) if len(df) > 2 else 0.0
        return Analysis(
            symbol=symbol, timeframe=tf, price=price, ts=time.time(), regime=regime, htf_regime=htf_regime,
            levels=levels, indicators=indicators, signals=signals, score=score, raw_score=raw,
            direction=direction, active_long=active_long, active_short=active_short, atr=atr_v,
            extension_atr=ext, stop_long=price - stop_dist, stop_short=price + stop_dist,
            tp_long=price + stop_dist * risk.take_profit_r, tp_short=price - stop_dist * risk.take_profit_r,
            has_edge=has_edge, tv_signal=tv_text, notes=notes,
            backtest=self.storage.kv_get(f"ensemble_bt:{symbol}:{tf}", {}) or {},
        )
