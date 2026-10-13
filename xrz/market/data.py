"""Marktdaten: Krypto über ccxt (100+ Börsen), Aktien/ETFs/Forex über yfinance,
und ein deterministischer Demo-Markt für Tests ohne Internet."""
from __future__ import annotations

import asyncio
import logging
import math
import time
import zlib
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "1w": 604800,
}

KNOWN_QUOTES = ["USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "EUR", "USD", "GBP", "TRY", "BTC", "ETH", "BNB"]


def tf_seconds(tf: str) -> int:
    if tf not in TF_SECONDS:
        raise ValueError(f"Unbekannter Zeitrahmen: {tf} (erlaubt: {', '.join(TF_SECONDS)})")
    return TF_SECONDS[tf]


def normalize_symbol(raw: str, watchlist: list[str] | None = None) -> str:
    """Wandelt z. B. 'BINANCE:BTCUSDT', 'btcusdt', 'BTC-USDT' oder 'btc' in 'BTC/USDT' um."""
    s = raw.strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    s = s.replace("-", "/").replace("_", "/")
    if s.endswith(".P"):  # TradingView Perpetual-Suffix
        s = s[:-2]
    if "/" in s:
        return s
    watchlist = watchlist or []
    for w in watchlist:
        if w.replace("/", "") == s:
            return w
    for w in watchlist:  # nur Basis angegeben, z. B. "BTC"
        if "/" in w and w.split("/")[0] == s:
            return w
    for q in KNOWN_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}/{q}"
    return s  # z. B. Aktie wie AAPL


def to_tradingview_symbol(symbol: str, prefix: str) -> str:
    if "/" in symbol:
        return f"{prefix}:{symbol.replace('/', '')}"
    return symbol


# --------------------------------------------------------------------------- #
# Demo-Markt
# --------------------------------------------------------------------------- #
class DemoMarket:
    """Erzeugt realistisch wirkende, aber künstliche Kurse.

    Der Kurs zu jeder Minute ist deterministisch (Summe vieler Sinuswellen mit
    zufälligen Phasen ≈ Random Walk auf mehreren Zeitskalen + Rauschen). Dadurch
    sind die Daten über alle Abrufe und Zeitrahmen konsistent – ideal zum Testen.
    """

    BASE_PRICES = {"BTC": 65_000.0, "ETH": 3_200.0, "SOL": 150.0, "BNB": 580.0, "XRP": 0.6}

    def __init__(self) -> None:
        self._params: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, float, int]] = {}

    def _p(self, symbol: str):
        if symbol not in self._params:
            seed = zlib.crc32(symbol.encode())
            rng = np.random.default_rng(seed)
            periods = np.array([45, 120, 360, 900, 2400, 6000, 14_000, 30_000, 70_000, 150_000, 320_000], float)
            amps = 0.0003 * np.sqrt(periods) * rng.uniform(0.6, 1.4, len(periods))
            phases = rng.uniform(0, 2 * math.pi, len(periods))
            base = self.BASE_PRICES.get(symbol.split("/")[0], float(rng.uniform(5, 500)))
            self._params[symbol] = (periods, amps, phases, base, seed)
        return self._params[symbol]

    def _log_price(self, symbol: str, minutes: np.ndarray) -> np.ndarray:
        periods, amps, phases, base, seed = self._p(symbol)
        m = minutes.astype(np.float64)[:, None]
        waves = (amps[None, :] * np.sin(2 * math.pi * m / periods[None, :] + phases[None, :])).sum(axis=1)
        # deterministisches Rauschen pro Minute
        h = (minutes.astype(np.uint64) * np.uint64(2654435761) + np.uint64(seed)) % np.uint64(2**32)
        noise = (h.astype(np.float64) / 2**32 - 0.5) * 0.0025
        return math.log(base) + waves + noise

    def ohlcv(self, symbol: str, timeframe: str, limit: int, now: float | None = None) -> pd.DataFrame:
        now = now or time.time()
        tf_min = tf_seconds(timeframe) // 60
        last_open_min = int(now // 60) // tf_min * tf_min
        start_min = last_open_min - (limit - 1) * tf_min
        step = max(1, tf_min // 60)
        offsets = np.arange(0, tf_min, step)
        opens = np.arange(start_min, last_open_min + 1, tf_min)
        # aktuelle (unfertige) Kerze nur bis jetzt
        grid = opens[:, None] + offsets[None, :]
        lp = self._log_price(symbol, grid.ravel()).reshape(grid.shape)
        price = np.exp(lp)
        now_min = int(now // 60)
        mask = grid <= now_min
        price_masked = np.where(mask, price, np.nan)
        o = price[:, 0]
        c = np.array([row[m][-1] for row, m in zip(price, mask)])
        hi = np.nanmax(price_masked, axis=1)
        lo = np.nanmin(price_masked, axis=1)
        rets = np.abs(np.diff(np.log(np.r_[o[0], c])))
        vol = 1000 * (1 + 40 * rets) * (1 + 0.3 * np.sin(opens / 1440.0))
        idx = pd.to_datetime(opens * 60, unit="s", utc=True)
        return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": vol}, index=idx)

    def price(self, symbol: str, now: float | None = None) -> float:
        now = now or time.time()
        return float(np.exp(self._log_price(symbol, np.array([int(now // 60)])))[0])


# --------------------------------------------------------------------------- #
# Haupt-Datenquelle
# --------------------------------------------------------------------------- #
class MarketData:
    def __init__(self, exchange_name: str = "binance", source: str = "exchange"):
        self.exchange_name = exchange_name
        self.source = source
        self.demo = DemoMarket()
        self._exchange: Any = None
        self._cache: dict[tuple[str, str, int], tuple[float, pd.DataFrame]] = {}
        self._price_cache: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- helpers
    def kind(self, symbol: str) -> str:
        if self.source == "demo":
            return "demo"
        return "crypto" if "/" in symbol else "stock"

    def _ccxt(self):
        if self._exchange is None:
            import ccxt  # lazy import, ccxt ist groß

            cls = getattr(ccxt, self.exchange_name, None)
            if cls is None:
                raise ValueError(f"Börse '{self.exchange_name}' wird von ccxt nicht unterstützt")
            self._exchange = cls({"enableRateLimit": True, "timeout": 20_000})
        return self._exchange

    def reconfigure(self, exchange_name: str, source: str) -> None:
        if exchange_name != self.exchange_name or source != self.source:
            self.exchange_name = exchange_name
            self.source = source
            self._exchange = None
            self._cache.clear()
            self._price_cache.clear()

    # ------------------------------------------------------------------ OHLCV
    async def ohlcv(self, symbol: str, timeframe: str, limit: int = 500, closed_only: bool = True) -> pd.DataFrame:
        key = (symbol, timeframe, limit)
        ttl = min(60.0, tf_seconds(timeframe) / 6)
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < ttl:
            df = cached[1]
        else:
            kind = self.kind(symbol)
            fetch_n = limit + 1
            if kind == "demo":
                df = self.demo.ohlcv(symbol, timeframe, fetch_n)
            elif kind == "crypto":
                df = await asyncio.to_thread(self._fetch_ccxt, symbol, timeframe, fetch_n)
            else:
                df = await asyncio.to_thread(self._fetch_yf, symbol, timeframe, fetch_n)
            if df.empty:
                raise RuntimeError(f"Keine Kursdaten für {symbol} ({timeframe})")
            self._cache[key] = (time.time(), df)
        if closed_only and len(df) > 1:
            last_open = df.index[-1].timestamp()
            if last_open + tf_seconds(timeframe) > time.time():
                df = df.iloc[:-1]
        return df.tail(limit).copy()

    def _fetch_ccxt(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        ex = self._ccxt()
        per_call = 1000
        tf_ms = tf_seconds(timeframe) * 1000
        since = int(time.time() * 1000) - limit * tf_ms
        rows: list[list[float]] = []
        for _ in range(20):
            batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=min(per_call, limit - len(rows) + 5))
            if not batch:
                break
            rows.extend(batch)
            since = batch[-1][0] + tf_ms
            if len(rows) >= limit or len(batch) < 2 or since > time.time() * 1000:
                break
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("ts").sort_values("ts")
        df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
        return df.astype(float).tail(limit)

    def _fetch_yf(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Für Aktien bitte 'pip install yfinance' ausführen") from exc
        resample = None
        interval = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "1d": "1d", "1w": "1wk"}.get(
            timeframe
        )
        if interval is None:
            if timeframe in ("2h", "4h", "6h", "12h"):
                interval, resample = "60m", timeframe
            else:
                raise ValueError(f"Zeitrahmen {timeframe} für Aktien nicht unterstützt")
        period = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "60m": "730d", "1d": "10y", "1wk": "max"}[interval]
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index, utc=True)
        if resample:
            df = df.resample(resample).agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna()
        return df.astype(float).tail(limit)

    # ------------------------------------------------------------------ Preise
    async def price(self, symbol: str, max_age: float = 10.0) -> float:
        cached = self._price_cache.get(symbol)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        kind = self.kind(symbol)
        if kind == "demo":
            p = self.demo.price(symbol)
        elif kind == "crypto":
            t = await asyncio.to_thread(self._ccxt().fetch_ticker, symbol)
            p = float(t.get("last") or t.get("close") or 0)
        else:
            df = await self.ohlcv(symbol, "1m", 5, closed_only=False)
            p = float(df["close"].iloc[-1])
        if p <= 0:
            raise RuntimeError(f"Ungültiger Preis für {symbol}")
        self._price_cache[symbol] = (time.time(), p)
        return p

    async def prices(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        results = await asyncio.gather(*(self.price(s) for s in symbols), return_exceptions=True)
        for s, r in zip(symbols, results):
            if isinstance(r, Exception):
                log.warning("Preis für %s nicht verfügbar: %s", s, r)
            else:
                out[s] = r
        return out

    async def validate_symbol(self, symbol: str) -> bool:
        try:
            df = await self.ohlcv(symbol, "1h", 30, closed_only=False)
            return not df.empty
        except Exception as exc:
            log.info("Symbol %s ungültig: %s", symbol, exc)
            return False
