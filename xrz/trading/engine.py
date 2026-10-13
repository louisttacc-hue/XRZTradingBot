"""Die Trading-Engine: scannt Märkte, entscheidet, führt aus, verwaltet Positionen."""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
from typing import Any

from ..config import ConfigManager, Settings
from ..market.data import MarketData, normalize_symbol
from ..notify import Notifier
from ..storage import Storage, Trade
from .analyzer import Analysis, Analyzer, _fmt_price
from .brokers import BrokerError, LiveBroker, PaperBroker
from .risk import RiskManager

log = logging.getLogger(__name__)

MODES = ("off", "paper", "live")
MODE_WORD = {"off": "AUS", "paper": "PAPER-TRADING (Spielgeld)", "live": "LIVE (ECHTGELD)"}


class TradingEngine:
    def __init__(
        self,
        cfg: ConfigManager,
        storage: Storage,
        market: MarketData,
        notifier: Notifier,
    ):
        self.cfg = cfg
        self.storage = storage
        self.market = market
        self.notifier = notifier
        self.analyzer = Analyzer(lambda: self.cfg.settings, market, storage)
        self.risk = RiskManager(lambda: self.cfg.settings, storage)
        self.paper = PaperBroker(lambda: self.cfg.settings, storage)
        self.live: LiveBroker | None = None
        self.brain: Any = None  # wird von app.py gesetzt
        self.last_analyses: dict[str, Analysis] = {}
        self._tasks: list[asyncio.Task] = []
        self._live_code: tuple[str, float] | None = None
        self._trade_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self.last_scan_ts = 0.0
        self.started_ts = time.time()

    # ------------------------------------------------------------------ state
    @property
    def s(self) -> Settings:
        return self.cfg.settings

    @property
    def mode(self) -> str:
        return self.storage.kv_get("mode", "off") or "off"

    @property
    def entries_paused(self) -> bool:
        return bool(self.storage.kv_get("entries_paused", False))

    @property
    def halted(self) -> str:
        return self.storage.kv_get("halted", "") or ""

    def broker_for(self, mode: str):
        if mode == "paper":
            return self.paper
        if mode == "live":
            if self.live is None or self.live.ex is None:
                raise BrokerError("Live-Börse nicht verbunden")
            return self.live
        raise BrokerError("Trading ist aus")

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self.mode == "live" or self.storage.open_trades("live"):
            try:
                await self._connect_live()
            except Exception as exc:
                await self.notifier.send("error", f"Live-Börse beim Start nicht erreichbar: {exc}")
            if self.mode == "live":
                self.storage.kv_set("entries_paused", True)
                await self.notifier.send(
                    "warning",
                    "Neustart im LIVE-Modus: Offene Positionen werden weiter überwacht, neue Einstiege sind "
                    "pausiert. Mit /weiter wieder freigeben.",
                )
        self._tasks = [
            asyncio.create_task(self._loop("scan", self.s.trading.scan_interval_minutes * 60, self.scan_once, initial_delay=5)),
            asyncio.create_task(self._loop("manage", self.s.trading.manage_interval_seconds, self.manage_once, initial_delay=3)),
            asyncio.create_task(self._loop("deep", 3600, self._deep_if_due, initial_delay=1)),
            asyncio.create_task(self._loop("equity", 300, self.snapshot_equity, initial_delay=10)),
            asyncio.create_task(self._loop("report", 60, self._daily_report_if_due, initial_delay=30)),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self, name: str, interval: float, fn, initial_delay: float = 0) -> None:
        await asyncio.sleep(initial_delay)
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Fehler in Schleife %s", name)
                self.storage.log_event("ERROR", f"{name}: {exc}", "error")
            # Intervalle können sich durch Einstellungen ändern
            if name == "scan":
                interval = self.s.trading.scan_interval_minutes * 60
            elif name == "manage":
                interval = self.s.trading.manage_interval_seconds
            await asyncio.sleep(max(5.0, interval))

    # ----------------------------------------------------------- Modus-Wechsel
    async def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(f"Unbekannter Modus {mode}")
        if mode == "live":
            raise PermissionError("Live-Modus nur über request_live()/confirm_live()")
        old = self.mode
        self.storage.kv_set("mode", mode)
        self.storage.kv_set("entries_paused", False)
        msg = f"Modus: {MODE_WORD[old]} → {MODE_WORD[mode]}" if old != mode else f"Läuft bereits: {MODE_WORD[mode]}"
        if mode == "paper":
            equity = await self.current_equity("paper")
            cash = await self.paper.cash()
            msg += (
                f"\nPaper-Konto: Wert {equity:,.2f}, davon frei {cash:,.2f}. "
                "Ich analysiere jetzt die Märkte und handle selbstständig."
            )
            asyncio.create_task(self.scan_once())
        if mode == "off":
            n = len(self.storage.open_trades())
            if n:
                msg += f"\n{n} offene Position(en) werden weiter mit Stop-Loss/Take-Profit überwacht. /panik schließt alles."
        await self.notifier.send("mode", msg)
        return msg

    async def _connect_live(self) -> dict[str, float]:
        if self.live is None:
            self.live = LiveBroker(lambda: self.cfg.settings, self.cfg.secrets)
        return await self.live.connect()

    async def request_live(self) -> str:
        if not self.s.live_trading_unlocked:
            return (
                "🔒 Live-Trading ist gesperrt. Bitte zuerst im Dashboard unter Einstellungen → Sicherheit "
                "'Live-Trading freischalten' aktivieren (bewusste Entscheidung am Mac)."
            )
        if self.s.exchange.data_source == "demo":
            return "Live-Trading ist mit Demo-Daten nicht möglich. Bitte Datenquelle 'exchange' wählen."
        try:
            totals = await self._connect_live()
        except Exception as exc:
            return f"❌ Verbindung zur Börse fehlgeschlagen: {exc}"
        code = f"{secrets.randbelow(900000) + 100000}"
        self._live_code = (code, time.time() + 300)
        assert self.live is not None
        bal = ", ".join(f"{k}: {v:g}" for k, v in sorted(totals.items())[:8]) or "leer"
        r = self.s.risk
        return (
            "⚠️ ACHTUNG – ECHTGELD ⚠️\n"
            f"{self.live.describe()}\n"
            f"Kontostand: {bal}\n"
            f"Risiko pro Trade: {r.risk_per_trade_pct}% | Tagesverlust-Limit: {r.max_daily_loss_pct}% | "
            f"Notbremse bei {r.max_drawdown_pct}% Drawdown\n"
            "Es gibt KEINE Gewinngarantie – Verluste sind möglich.\n\n"
            f"Zum Bestätigen innerhalb von 5 Minuten senden:  /bestaetigen {code}"
        )

    async def confirm_live(self, code: str) -> str:
        if not self._live_code:
            return "Es liegt keine Live-Anfrage vor. Erst /live senden."
        expected, until = self._live_code
        if time.time() > until:
            self._live_code = None
            return "Code abgelaufen. Bitte /live erneut senden."
        if not hmac.compare_digest(code.strip(), expected):
            return "Falscher Code."
        self._live_code = None
        old = self.mode
        self.storage.kv_set("mode", "live")
        self.storage.kv_set("entries_paused", False)
        equity = await self.current_equity("live")
        self.risk.reset_peak("live", equity)
        msg = f"🔴 LIVE-TRADING AKTIV (vorher: {MODE_WORD[old]}). Konto-Wert ≈ {equity:,.2f}. /stop beendet, /panik schließt alles."
        await self.notifier.send("mode", msg)
        asyncio.create_task(self.scan_once())
        return msg

    async def pause(self) -> str:
        self.storage.kv_set("entries_paused", True)
        return "⏸ Neue Einstiege pausiert. Offene Positionen werden weiter verwaltet. /weiter zum Fortsetzen."

    async def resume(self) -> str:
        self.storage.kv_set("entries_paused", False)
        msg = "▶️ Trading fortgesetzt."
        if self.halted:
            equity = await self.current_equity(self.mode) if self.mode in ("paper", "live") else 0
            self.storage.kv_set("halted", "")
            if self.mode in ("paper", "live"):
                self.risk.reset_peak(self.mode, equity)
            msg += " Notbremse zurückgesetzt (Höchststand = aktueller Kontowert)."
        return msg

    # ----------------------------------------------------------------- Konto
    async def prices_for_open(self) -> dict[str, float]:
        syms = sorted({t.symbol for t in self.storage.open_trades()})
        return await self.market.prices(syms) if syms else {}

    async def current_equity(self, mode: str) -> float:
        broker = self.broker_for(mode)
        open_trades = self.storage.open_trades(mode)
        prices = await self.market.prices(sorted({t.symbol for t in open_trades} | set(self.s.trading.watchlist))) if mode == "live" else await self.prices_for_open()
        return await broker.equity(open_trades, prices)

    async def snapshot_equity(self) -> None:
        modes = {self.mode} | {t.mode for t in self.storage.open_trades()}
        for mode in modes:
            if mode not in ("paper", "live"):
                continue
            try:
                broker = self.broker_for(mode)
                eq = await self.current_equity(mode)
                cash = await broker.cash()
            except Exception as exc:
                log.info("Equity-Snapshot %s nicht möglich: %s", mode, exc)
                continue
            self.storage.record_equity(mode, eq, cash)
            reason = self.risk.update_equity_marks(mode, eq)
            if reason and mode == self.mode and not self.halted:
                self.storage.kv_set("halted", reason)
                await self.notifier.send("warning", f"🛑 {reason}\nKeine neuen Trades mehr. Prüfe die Lage; /weiter setzt zurück.")

    # ------------------------------------------------------------------ Scan
    async def scan_once(self, symbols: list[str] | None = None) -> list[Analysis]:
        async with self._scan_lock:
            results: list[Analysis] = []
            for sym in symbols or list(self.s.trading.watchlist):
                try:
                    a = await self.analyzer.analyze(sym)
                except Exception as exc:
                    log.warning("Analyse %s fehlgeschlagen: %s", sym, exc)
                    self.storage.log_event("WARN", f"Analyse {sym}: {exc}", "analysis")
                    continue
                self.last_analyses[sym] = a
                results.append(a)
                try:
                    await self._act_on(a)
                except Exception as exc:
                    log.exception("Handeln auf %s fehlgeschlagen", sym)
                    await self.notifier.send("error", f"Fehler bei {sym}: {exc}")
            self.last_scan_ts = time.time()
            return results

    async def _act_on(self, a: Analysis, force_direction: int | None = None, source: str = "Ensemble") -> str:
        mode = self.mode
        th_exit = self.s.trading.exit_threshold
        # Ausstieg, wenn das Gesamtbild gedreht hat (für Positionen jedes Modus)
        closed_any = False
        for t in [t for t in self.storage.open_trades() if t.symbol == a.symbol]:
            if t.meta.get("source") == "TradingView":
                continue  # direkt per Alert eröffnet → Ausstieg über Stop/Ziel oder TradingView-"close"
            if (t.side == "long" and a.score < th_exit) or (t.side == "short" and a.score > -th_exit):
                await self.close_trade(t, f"Signal gedreht (Score {a.score:+.2f})", price=a.price)
                closed_any = True
        if closed_any:
            return "closed"
        open_t = [t for t in self.storage.open_trades() if t.symbol == a.symbol and t.mode == mode]
        if open_t or mode not in ("paper", "live"):
            return "skip"
        if self.entries_paused or self.halted:
            return "paused"
        direction = force_direction if force_direction is not None else a.direction
        if direction == 0:
            return "no-signal"
        if direction < 0 and (mode == "live" or not self.s.trading.allow_short):
            return "no-short"
        if mode == "live" and "/" not in a.symbol:
            return "stocks-paper-only"  # Aktien/ETFs nur im Paper-Modus (Daten via yfinance)
        if self.s.telegram.notify_signals:
            await self.notifier.send("signal", f"Signal {a.symbol}: {a.direction_word} (Score {a.score:+.2f})")
        return await self.open_trade(a, direction, source=source)

    async def open_trade(self, a: Analysis, direction: int, source: str = "Ensemble") -> str:
        side = "long" if direction > 0 else "short"
        # 0) Schnelle Vorprüfung – spart KI-Aufrufe (und API-Kosten), wenn ohnehin kein Trade möglich ist
        mode = self.mode
        broker = self.broker_for(mode)
        pre = self.risk.check_entry(
            mode=mode, symbol=a.symbol, direction=direction, price=a.price,
            stop=a.stop_long if direction > 0 else a.stop_short,
            equity=await self.current_equity(mode), cash=await broker.cash(), open_trades=self.storage.open_trades(),
        )
        if not pre.ok:
            self.storage.log_event("INFO", f"Risiko blockt {side} {a.symbol}: {pre.reason}", "risk")
            return "risk-blocked"
        # 1) KI-Prüfung (darf nur ablehnen oder verkleinern) – außerhalb des Locks,
        #    damit Stop-Loss-Überwachung währenddessen weiterläuft.
        size_factor, ai_comment = 1.0, ""
        if self.s.trading.use_ai_review and self.brain is not None:
            review = await self.brain.review_trade(a, side)
            ai_comment = review.get("reason", "")
            if not review.get("approve", True):
                self.storage.log_event("INFO", f"KI lehnt {side} {a.symbol} ab: {ai_comment}", "ai")
                if self.s.telegram.notify_signals:
                    await self.notifier.send("signal", f"🤖 KI hat {side.upper()} {a.symbol} abgelehnt: {ai_comment}")
                return "ai-rejected"
            size_factor = float(review.get("size_factor", 1.0))

        async with self._trade_lock:
            mode = self.mode
            if mode not in ("paper", "live") or self.entries_paused or self.halted:
                return "paused"
            broker = self.broker_for(mode)
            price = await self.market.price(a.symbol, max_age=5)
            stop_dist = abs(a.price - (a.stop_long if direction > 0 else a.stop_short))
            stop = price - direction * stop_dist

            # 2) Risiko-Management (hat immer das letzte Wort)
            open_trades = self.storage.open_trades()
            equity = await self.current_equity(mode)
            cash = await broker.cash()
            dec = self.risk.check_entry(
                mode=mode, symbol=a.symbol, direction=direction, price=price, stop=stop,
                equity=equity, cash=cash, open_trades=open_trades, size_factor=size_factor,
            )
            if not dec.ok:
                self.storage.log_event("INFO", f"Risiko blockt {side} {a.symbol}: {dec.reason}", "risk")
                return "risk-blocked"

            # 3) Ausführung
            try:
                fill = await broker.open(a.symbol, side, dec.qty, price)
            except Exception as exc:
                await self.notifier.send("error", f"Order {side} {a.symbol} fehlgeschlagen: {exc}")
                return "order-failed"
            stop = fill.price - direction * stop_dist
            tp = fill.price + direction * stop_dist * self.s.risk.take_profit_r
            reasons = [f"{sd.label}: {sd.why}" for sd in a.signals if sd.signal == direction and sd.weight > 0]
            trade = Trade(
                mode=mode, symbol=a.symbol, side=side, qty=fill.qty, entry_price=fill.price,
                entry_time=time.time(), stop=stop, initial_stop=stop, take_profit=tp, fees=fill.fee,
                entry_reason=f"{source}: Score {a.score:+.2f}; " + " | ".join(reasons[:4]),
                score=a.score, strategies={sd.name: float(sd.signal) for sd in a.signals},
                ai_comment=ai_comment,
                meta={"order_id": fill.order_id, "atr": a.atr, "regime": a.regime.label, "risk_amount": dec.risk_amount,
                      "source": source},
            )
            self.storage.insert_trade(trade)
            stop_id = await broker.place_stop(trade)
            if stop_id:
                trade.meta["stop_order_id"] = stop_id
                self.storage.update_trade(trade)
            emoji = "🟢" if side == "long" else "🔴"
            txt = (
                f"{emoji} NEUER TRADE [{mode.upper()}] {side.upper()} {a.symbol}\n"
                f"Einstieg {_fmt_price(fill.price)} | Menge {fill.qty:.6g} | Wert {fill.qty * fill.price:,.2f}\n"
                f"Stop-Loss {_fmt_price(stop)} | Take-Profit {_fmt_price(tp)} | {dec.reason}\n"
                f"Grund: Score {a.score:+.2f}, {a.regime.label}"
            )
            if ai_comment:
                txt += f"\n🤖 KI: {ai_comment}"
            await self.notifier.send("trade", txt, {"trade_id": trade.id})
            return "opened"

    # --------------------------------------------------------------- Verwalten
    async def manage_once(self) -> None:
        trades = self.storage.open_trades()
        if not trades:
            return
        prices = await self.market.prices(sorted({t.symbol for t in trades}))
        for t in trades:
            price = prices.get(t.symbol)
            if price is None:
                continue
            try:
                broker = self.broker_for(t.mode)
            except BrokerError:
                continue
            filled = await broker.check_stop_filled(t)
            if filled:
                await self._finalize(t, filled.price, filled.fee, "Stop-Loss (an der Börse ausgelöst)")
                continue
            d = t.direction
            if t.stop and ((d > 0 and price <= t.stop) or (d < 0 and price >= t.stop)):
                reason = "Stop-Loss" if t.current_r(t.stop) < 0 else "Trailing-Stop (Gewinn gesichert)"
                await self.close_trade(t, reason, price=price)
                continue
            if t.take_profit and ((d > 0 and price >= t.take_profit) or (d < 0 and price <= t.take_profit)):
                await self.close_trade(t, "Take-Profit erreicht 🎯", price=price)
                continue
            a = self.last_analyses.get(t.symbol)
            atr = a.atr if a else float(t.meta.get("atr", 0))
            new_stop = self.risk.trail(t, price, atr)
            if new_stop:
                old = t.stop or 0
                t.stop = new_stop
                if t.mode == "live" and abs(new_stop - old) / max(old, 1e-9) > 0.003 and t.meta.get("stop_order_id"):
                    await broker.cancel_stop(t)
                    sid = await broker.place_stop(t)
                    if sid:
                        t.meta["stop_order_id"] = sid
                self.storage.update_trade(t)

    async def close_trade(self, t: Trade, reason: str, price: float | None = None) -> str:
        async with self._trade_lock:
            fresh = self.storage.get_trade(t.id) if t.id else t
            if fresh is None or fresh.status != "open":
                return "Position war bereits geschlossen."
            t = fresh
            broker = self.broker_for(t.mode)
            px = price or await self.market.price(t.symbol, max_age=5)
            try:
                fill = await broker.close(t, px)
            except Exception as exc:
                await self.notifier.send("error", f"Schließen von {t.symbol} fehlgeschlagen: {exc}")
                return f"Fehler: {exc}"
            return await self._finalize(t, fill.price, fill.fee, reason)

    async def _finalize(self, t: Trade, exit_price: float, exit_fee: float, reason: str) -> str:
        t.exit_price = exit_price
        t.exit_time = time.time()
        t.fees = (t.fees or 0) + exit_fee
        t.pnl = (exit_price - t.entry_price) * t.qty * t.direction - t.fees
        t.pnl_pct = t.pnl / (t.entry_price * t.qty) * 100 if t.qty else 0.0
        rpu = t.risk_per_unit()
        t.r_multiple = (t.pnl / (rpu * t.qty)) if rpu > 0 and t.qty else 0.0
        t.status = "closed"
        t.exit_reason = reason
        self.storage.update_trade(t)
        emoji = "✅" if t.pnl > 0 else "❌"
        dur_h = (t.exit_time - t.entry_time) / 3600
        txt = (
            f"{emoji} TRADE GESCHLOSSEN [{t.mode.upper()}] {t.side.upper()} {t.symbol}\n"
            f"{_fmt_price(t.entry_price)} → {_fmt_price(exit_price)} | Ergebnis {t.pnl:+,.2f} ({t.pnl_pct:+.2f}%, {t.r_multiple:+.2f}R)\n"
            f"Grund: {reason} | Dauer {dur_h:.1f} h"
        )
        await self.notifier.send("trade", txt, {"trade_id": t.id})
        if self.brain is not None and self.s.ai.reflect_on_trades:
            asyncio.create_task(self.brain.reflect_on_trade(t))
        return txt

    async def close_symbol(self, symbol: str, reason: str = "Manuell geschlossen") -> str:
        symbol = normalize_symbol(symbol, self.s.trading.watchlist)
        trades = [t for t in self.storage.open_trades() if t.symbol == symbol]
        if not trades:
            return f"Keine offene Position in {symbol}."
        out = [await self.close_trade(t, reason) for t in trades]
        return "\n\n".join(out)

    async def panic(self) -> str:
        self.storage.kv_set("entries_paused", True)
        trades = self.storage.open_trades()
        results = []
        for t in trades:
            results.append(await self.close_trade(t, "PANIK – alles schließen"))
        self.storage.kv_set("mode", "off")
        msg = f"🚨 PANIK: {len(trades)} Position(en) geschlossen, Trading AUS."
        await self.notifier.send("mode", msg)
        return msg + ("\n\n" + "\n\n".join(results) if results else "")

    # ---------------------------------------------------------- TradingView
    async def handle_tradingview(self, payload: dict[str, Any]) -> dict[str, Any]:
        expected = self.cfg.secrets.get("tradingview_passphrase")
        given = str(payload.get("passphrase", ""))
        if not expected or not hmac.compare_digest(given, expected):
            return {"ok": False, "error": "Falsche oder fehlende Passphrase"}
        raw_symbol = str(payload.get("symbol") or payload.get("ticker") or "")
        action = str(payload.get("action") or payload.get("side") or "").lower().strip()
        action = {"long": "buy", "short": "sell", "exit": "close", "flat": "close"}.get(action, action)
        if not raw_symbol or action not in ("buy", "sell", "close"):
            return {"ok": False, "error": "symbol und action (buy/sell/close) erforderlich"}
        symbol = normalize_symbol(raw_symbol, self.s.trading.watchlist)
        self.storage.add_signal("tradingview", symbol, action, {k: v for k, v in payload.items() if k != "passphrase"})
        await self.notifier.send("signal", f"📡 TradingView-Alert: {action.upper()} {symbol} {payload.get('comment', '')}".strip())
        if action == "close":
            return {"ok": True, "result": await self.close_symbol(symbol, "TradingView-Alert: close")}
        try:
            a = await self.analyzer.analyze(symbol)
        except Exception as exc:
            return {"ok": False, "error": f"Analyse fehlgeschlagen: {exc}"}
        self.last_analyses[symbol] = a
        if self.s.tradingview.alert_mode == "execute":
            result = await self._act_on(a, force_direction=1 if action == "buy" else -1, source="TradingView")
        else:
            result = await self._act_on(a, source="Ensemble+TradingView")
        return {"ok": True, "result": result, "score": a.score}

    # ------------------------------------------------------------- Berichte
    async def status(self) -> dict[str, Any]:
        mode = self.mode
        out: dict[str, Any] = {
            "mode": mode, "mode_word": MODE_WORD[mode], "entries_paused": self.entries_paused,
            "halted": self.halted, "watchlist": self.s.trading.watchlist, "timeframe": self.s.trading.timeframe,
            "last_scan": self.last_scan_ts, "uptime_h": (time.time() - self.started_ts) / 3600,
            "data_source": self.s.exchange.data_source, "exchange": self.s.exchange.name,
        }
        prices = await self.prices_for_open()
        positions = []
        for t in self.storage.open_trades():
            p = prices.get(t.symbol, t.entry_price)
            positions.append({
                "id": t.id, "mode": t.mode, "symbol": t.symbol, "side": t.side, "qty": t.qty,
                "entry": t.entry_price, "price": p, "stop": t.stop, "take_profit": t.take_profit,
                "pnl": t.unrealized(p), "pnl_pct": t.unrealized(p) / (t.entry_price * t.qty) * 100 if t.qty else 0,
                "r": t.current_r(p), "since_h": (time.time() - t.entry_time) / 3600,
            })
        out["positions"] = positions
        for m in ("paper", "live"):
            if m == "live" and (self.live is None or self.live.ex is None):
                continue
            try:
                eq = await self.current_equity(m)
                start = self.risk.day_start_equity(m, eq)
                out[m] = {
                    "equity": eq, "cash": await self.broker_for(m).cash(),
                    "day_pnl": eq - start, "day_pnl_pct": (eq - start) / start * 100 if start else 0,
                    "stats": self.storage.trade_stats(m),
                }
                if m == "paper":
                    st = float(self.storage.kv_get("paper_start", eq) or eq)
                    out[m]["total_pnl_pct"] = (eq / st - 1) * 100 if st else 0
            except Exception as exc:
                out[m] = {"error": str(exc)}
        return out

    async def status_text(self) -> str:
        st = await self.status()
        lines = [f"🤖 XRZ Status – Modus: {st['mode_word']}"]
        if st["halted"]:
            lines.append(f"🛑 {st['halted']}")
        elif st["entries_paused"]:
            lines.append("⏸ Neue Einstiege pausiert")
        for m in ("paper", "live"):
            if m in st and "equity" in st[m]:
                d = st[m]
                extra = f", gesamt {d['total_pnl_pct']:+.2f}%" if "total_pnl_pct" in d else ""
                lines.append(
                    f"{'📄 Paper' if m == 'paper' else '💶 Live'}: Wert {d['equity']:,.2f} | Cash {d['cash']:,.2f} | "
                    f"heute {d['day_pnl']:+,.2f} ({d['day_pnl_pct']:+.2f}%{extra})"
                )
                s = d["stats"]
                if s["trades"]:
                    lines.append(
                        f"   {s['trades']} Trades, Trefferquote {s['win_rate']:.0f}%, Profit-Faktor {s['profit_factor']:.2f}, Ø {s['avg_r']:+.2f}R"
                    )
        if st["positions"]:
            lines.append("Offene Positionen:")
            for p in st["positions"]:
                lines.append(
                    f"  {'🟢' if p['side'] == 'long' else '🔴'} [{p['mode']}] {p['symbol']} {p['side']} @ {_fmt_price(p['entry'])} → "
                    f"{_fmt_price(p['price'])}: {p['pnl']:+,.2f} ({p['pnl_pct']:+.2f}%, {p['r']:+.2f}R) | SL {_fmt_price(p['stop'])}"
                )
        else:
            lines.append("Keine offenen Positionen.")
        if self.last_analyses:
            lines.append("Letzte Signale:")
            for sym, a in self.last_analyses.items():
                lines.append(f"  {sym}: Score {a.score:+.2f} → {a.direction_word}")
        if st["data_source"] == "demo":
            lines.append("ℹ️ DEMO-DATEN aktiv (künstliche Kurse).")
        return "\n".join(lines)

    async def daily_report(self) -> str:
        text = await self.status_text()
        since = time.time() - 86400
        closed = self.storage.closed_trades(limit=50, since=since)
        lines = [f"📅 Tagesbericht {time.strftime('%d.%m.%Y')}", text, ""]
        if closed:
            lines.append(f"Heute geschlossene Trades ({len(closed)}):")
            for t in closed:
                lines.append(f"  {t.symbol} {t.side}: {t.pnl:+,.2f} ({t.r_multiple:+.2f}R) – {t.exit_reason}")
        else:
            lines.append("Heute keine Trades geschlossen.")
        report = "\n".join(lines)
        if self.brain is not None:
            try:
                comment = await self.brain.daily_comment(report)
                if comment:
                    report += f"\n\n🤖 Einschätzung:\n{comment}"
            except Exception as exc:
                log.info("Tageskommentar nicht möglich: %s", exc)
        return report

    async def _daily_report_if_due(self) -> None:
        when = (self.s.telegram.daily_report_time or "").strip()
        if not when:
            return
        try:
            h, m = (int(x) for x in when.split(":", 1))
            when = f"{h:02d}:{m:02d}"
        except ValueError:
            return
        now = time.strftime("%H:%M")
        today = time.strftime("%Y-%m-%d")
        if now >= when and self.storage.kv_get("last_report_day") != today:
            self.storage.kv_set("last_report_day", today)
            await self.notifier.send("report", await self.daily_report())

    async def _deep_if_due(self) -> None:
        tf = self.s.trading.timeframe
        max_age = self.s.trading.deep_analysis_hours * 3600
        for sym in list(self.s.trading.watchlist):
            if self.analyzer.deep_analysis_age(sym, tf) > max_age:
                try:
                    await self.analyzer.deep_analysis(sym, tf)
                    self.storage.log_event("INFO", f"Tiefenanalyse {sym} aktualisiert", "analysis")
                except Exception as exc:
                    log.warning("Tiefenanalyse %s: %s", sym, exc)
