"""Ausführung: Paper-Broker (Simulation mit echten Kursen) und Live-Broker (ccxt)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings
from ..storage import Storage, Trade

log = logging.getLogger(__name__)


@dataclass
class Fill:
    price: float
    qty: float
    fee: float
    order_id: str = ""
    info: dict[str, Any] = field(default_factory=dict)


class BrokerError(RuntimeError):
    pass


class PaperBroker:
    """Simuliert Orders mit Gebühren und Slippage. Konto liegt in der Datenbank."""

    mode = "paper"

    def __init__(self, settings: Callable[[], Settings], storage: Storage):
        self._settings = settings
        self.storage = storage
        if self.storage.kv_get("paper_cash") is None:
            self.reset()

    @property
    def s(self) -> Settings:
        return self._settings()

    def reset(self, balance: float | None = None) -> float:
        bal = float(balance if balance is not None else self.s.paper.starting_balance)
        self.storage.kv_set("paper_cash", bal)
        self.storage.kv_set("paper_start", bal)
        self.storage.kv_set("peak:paper", bal)
        self.storage.kv_set("day:paper", None)
        return bal

    async def cash(self) -> float:
        return float(self.storage.kv_get("paper_cash", 0.0))

    def _set_cash(self, v: float) -> None:
        self.storage.kv_set("paper_cash", float(v))

    async def open(self, symbol: str, side: str, qty: float, price_hint: float) -> Fill:
        slip = self.s.risk.slippage_pct / 100
        price = price_hint * (1 + slip) if side == "long" else price_hint * (1 - slip)
        notional = qty * price
        fee = notional * self.s.risk.fee_pct / 100
        cash = await self.cash()
        if notional + fee > cash + 1e-9:
            raise BrokerError(f"Nicht genug Paper-Guthaben ({cash:.2f} < {notional + fee:.2f})")
        self._set_cash(cash - notional - fee)  # Short: Nominalwert dient als Sicherheit
        return Fill(price=price, qty=qty, fee=fee, order_id="paper")

    async def close(self, trade: Trade, price_hint: float) -> Fill:
        slip = self.s.risk.slippage_pct / 100
        price = price_hint * (1 - slip) if trade.side == "long" else price_hint * (1 + slip)
        fee = trade.qty * price * self.s.risk.fee_pct / 100
        cash = await self.cash()
        if trade.side == "long":
            cash += trade.qty * price - fee
        else:
            cash += trade.qty * trade.entry_price + (trade.entry_price - price) * trade.qty - fee
        self._set_cash(cash)
        return Fill(price=price, qty=trade.qty, fee=fee, order_id="paper")

    async def equity(self, open_trades: list[Trade], prices: dict[str, float]) -> float:
        eq = await self.cash()
        for t in open_trades:
            if t.mode != "paper":
                continue
            p = prices.get(t.symbol, t.entry_price)
            if t.side == "long":
                eq += t.qty * p
            else:
                eq += t.qty * t.entry_price + (t.entry_price - p) * t.qty
        return eq

    async def place_stop(self, trade: Trade) -> str | None:  # Paper: Stops überwacht der Bot
        return None

    async def cancel_stop(self, trade: Trade) -> None:
        return None

    async def check_stop_filled(self, trade: Trade) -> Fill | None:
        return None

    def describe(self) -> str:
        return f"Paper-Trading (Simulation, Startkapital {self.storage.kv_get('paper_start', 0):,.2f})"


class LiveBroker:
    """Echte Orders über ccxt (Spot, nur Long). Nutzt Testnet, wenn eingestellt."""

    mode = "live"

    def __init__(self, settings: Callable[[], Settings], secrets: Any):
        self._settings = settings
        self.secrets = secrets
        self.ex: Any = None
        self.quote = "USDT"

    @property
    def s(self) -> Settings:
        return self._settings()

    async def connect(self) -> dict[str, float]:
        import ccxt

        name = self.s.exchange.name
        cls = getattr(ccxt, name, None)
        if cls is None:
            raise BrokerError(f"Börse {name} unbekannt")
        key, secret = self.secrets.get("exchange_api_key"), self.secrets.get("exchange_secret")
        if not key or not secret:
            raise BrokerError("Börsen-API-Key und Secret fehlen (im Dashboard unter Einstellungen eintragen)")
        cfg: dict[str, Any] = {
            "apiKey": key, "secret": secret, "enableRateLimit": True, "timeout": 20_000,
            "options": {"defaultType": "spot"},
        }
        pw = self.secrets.get("exchange_password")
        if pw:
            cfg["password"] = pw
        ex = cls(cfg)
        if self.s.exchange.sandbox:
            ex.set_sandbox_mode(True)
        await asyncio.to_thread(ex.load_markets)
        bal = await asyncio.to_thread(ex.fetch_balance)
        self.ex = ex
        quotes = [w.split("/")[1] for w in self.s.trading.watchlist if "/" in w]
        self.quote = max(set(quotes), key=quotes.count) if quotes else "USDT"
        totals = {k: float(v) for k, v in (bal.get("total") or {}).items() if v}
        return totals

    def _need(self) -> Any:
        if self.ex is None:
            raise BrokerError("Live-Börse nicht verbunden")
        return self.ex

    async def cash(self) -> float:
        ex = self._need()
        bal = await asyncio.to_thread(ex.fetch_balance)
        return float((bal.get("free") or {}).get(self.quote, 0) or 0)

    async def equity(self, open_trades: list[Trade], prices: dict[str, float]) -> float:
        ex = self._need()
        bal = await asyncio.to_thread(ex.fetch_balance)
        total = bal.get("total") or {}
        eq = float(total.get(self.quote, 0) or 0)
        for sym in set(self.s.trading.watchlist) | {t.symbol for t in open_trades}:
            if "/" not in sym or not sym.endswith("/" + self.quote):
                continue
            base = sym.split("/")[0]
            amt = float(total.get(base, 0) or 0)
            if amt > 0 and sym in prices:
                eq += amt * prices[sym]
        return eq

    def _check_limits(self, symbol: str, qty: float, price: float) -> float:
        ex = self._need()
        market = ex.market(symbol)
        amount = float(ex.amount_to_precision(symbol, qty))
        limits = market.get("limits") or {}
        min_amt = (limits.get("amount") or {}).get("min") or 0
        min_cost = (limits.get("cost") or {}).get("min") or 0
        if amount <= 0 or amount < min_amt:
            raise BrokerError(f"Menge {amount} unter Börsen-Minimum {min_amt}")
        if min_cost and amount * price < min_cost:
            raise BrokerError(f"Ordervolumen {amount * price:.2f} unter Börsen-Minimum {min_cost}")
        return amount

    async def _fill_from_order(self, order: dict[str, Any], symbol: str, price_hint: float, amount: float) -> Fill:
        ex = self._need()
        if (order.get("average") is None or not order.get("filled")) and ex.has.get("fetchOrder") and order.get("id"):
            await asyncio.sleep(1.0)
            try:
                order = await asyncio.to_thread(ex.fetch_order, order["id"], symbol)
            except Exception as exc:
                log.warning("fetch_order fehlgeschlagen: %s", exc)
        price = float(order.get("average") or order.get("price") or price_hint)
        filled = float(order.get("filled") or amount)
        fee_cost = 0.0
        fee_base = 0.0
        base = symbol.split("/")[0]
        for f in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            if not f or f.get("cost") is None:
                continue
            if f.get("currency") == base:
                fee_base += float(f["cost"])
                fee_cost += float(f["cost"]) * price
            elif f.get("currency") == self.quote:
                fee_cost += float(f["cost"])
        if fee_cost == 0:
            fee_cost = filled * price * self.s.risk.fee_pct / 100
        return Fill(price=price, qty=max(0.0, filled - fee_base), fee=fee_cost, order_id=str(order.get("id", "")), info={"raw_filled": filled})

    async def open(self, symbol: str, side: str, qty: float, price_hint: float) -> Fill:
        if side != "long":
            raise BrokerError("Live-Shorts werden nicht unterstützt (Spot)")
        ex = self._need()
        amount = self._check_limits(symbol, qty, price_hint)
        cap = self.s.risk.max_live_order_usd
        if amount * price_hint > cap * 1.02:
            raise BrokerError(f"Order {amount * price_hint:.2f} über Live-Limit {cap:.2f}")
        order = await asyncio.to_thread(ex.create_order, symbol, "market", "buy", amount, price_hint)
        return await self._fill_from_order(order, symbol, price_hint, amount)

    async def close(self, trade: Trade, price_hint: float) -> Fill:
        ex = self._need()
        await self.cancel_stop(trade)
        base = trade.symbol.split("/")[0]
        bal = await asyncio.to_thread(ex.fetch_balance)
        free = float((bal.get("free") or {}).get(base, 0) or 0)
        qty = min(trade.qty, free)
        if qty <= 0:
            raise BrokerError(f"Kein {base}-Bestand zum Verkaufen gefunden (evtl. schon an der Börse verkauft)")
        amount = self._check_limits(trade.symbol, qty, price_hint)
        order = await asyncio.to_thread(ex.create_order, trade.symbol, "market", "sell", amount, price_hint)
        return await self._fill_from_order(order, trade.symbol, price_hint, amount)

    async def place_stop(self, trade: Trade) -> str | None:
        """Platziert einen Stop-Loss direkt an der Börse (Schutz, falls der Mac aus ist)."""
        if not self.s.exchange.exchange_side_stops or not trade.stop:
            return None
        ex = self._need()
        try:
            amount = float(ex.amount_to_precision(trade.symbol, trade.qty * 0.999))
            stop = float(ex.price_to_precision(trade.symbol, trade.stop))
            order = await asyncio.to_thread(
                ex.create_order, trade.symbol, "market", "sell", amount, None, {"stopLossPrice": stop}
            )
            return str(order.get("id"))
        except Exception as exc:
            log.warning("Börsen-Stop für %s nicht möglich (%s) – Bot überwacht den Stop selbst", trade.symbol, exc)
            return None

    async def cancel_stop(self, trade: Trade) -> None:
        oid = trade.meta.get("stop_order_id")
        if not oid or self.ex is None:
            return
        try:
            await asyncio.to_thread(self.ex.cancel_order, oid, trade.symbol)
        except Exception as exc:
            log.info("Stop-Order %s konnte nicht storniert werden: %s", oid, exc)
        trade.meta.pop("stop_order_id", None)

    async def check_stop_filled(self, trade: Trade) -> Fill | None:
        oid = trade.meta.get("stop_order_id")
        if not oid or self.ex is None or not self.ex.has.get("fetchOrder"):
            return None
        try:
            order = await asyncio.to_thread(self.ex.fetch_order, oid, trade.symbol)
        except Exception:
            return None
        if order.get("status") == "closed":
            return Fill(
                price=float(order.get("average") or order.get("price") or trade.stop or 0),
                qty=float(order.get("filled") or trade.qty),
                fee=float((order.get("fee") or {}).get("cost") or 0),
                order_id=str(oid),
            )
        return None

    def describe(self) -> str:
        net = "TESTNET" if self.s.exchange.sandbox else "ECHTGELD"
        return f"Live-Trading auf {self.s.exchange.name} ({net}), max. {self.s.risk.max_live_order_usd:.0f} {self.quote} pro Order"
