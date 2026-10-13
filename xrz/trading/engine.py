"""Die Trading-Engine: analysiert Märkte, macht Trade-Vorschläge, führt sie nach deiner
Freigabe in der TradingView-App aus (Paper Trading oder Echtgeld) und führt Buch.

Ablauf: Analyse → Vorschlag (Risiko-Check + KI-Prüfung) → dein „Ja“ (Stimme, Klick, Telegram)
→ Order im TradingView-Order-Ticket mit Stop-Loss/Take-Profit → Kontrolle → Journal."""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import ConfigManager, Settings
from ..events import EventBus
from ..market.data import MarketData, normalize_symbol, to_tradingview_symbol
from ..storage import Storage, Trade
from .analyzer import Analysis, Analyzer, _fmt_price
from .risk import RiskManager

log = logging.getLogger(__name__)

MODES = ("off", "watch", "paper", "live")
MODE_WORD = {
    "off": "AUS",
    "watch": "BEOBACHTEN (nur Vorschläge)",
    "paper": "PAPER TRADING (TradingView)",
    "live": "ECHTGELD (TradingView)",
}
SIDE_WORD = {"buy": "KAUFEN", "sell": "VERKAUFEN"}
ACTIVE = ("pending", "approved", "executing")


@dataclass
class Proposal:
    id: str
    symbol: str
    tv_symbol: str
    side: str  # buy | sell
    action: str  # open | close
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    price: float = 0.0
    score: float = 0.0
    reason: str = ""
    risk_text: str = ""
    ai_comment: str = ""
    account_kind: str = "paper"  # paper | live | watch
    status: str = "pending"  # pending|approved|rejected|executing|done|failed|expired
    created: float = field(default_factory=time.time)
    expires: float = 0.0
    result: str = ""
    trade_id: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        if self.action == "close":
            return f"{self.symbol}-Position schließen ({'Echtgeld' if self.account_kind == 'live' else 'Paper'})"
        parts = [f"{SIDE_WORD[self.side]} {self.qty:g} {self.symbol}", "zum Marktpreis" if self.order_type == "market"
                 else f"Limit {_fmt_price(self.limit_price or 0)}"]
        if self.stop_loss:
            parts.append(f"Stop-Loss {_fmt_price(self.stop_loss)}")
        if self.take_profit:
            parts.append(f"Ziel {_fmt_price(self.take_profit)}")
        return ", ".join(parts)

    def spoken(self) -> str:
        if self.action == "close":
            return f"Soll ich die {self.symbol.split('/')[0]}-Position schließen?"
        what = "kaufen" if self.side == "buy" else "verkaufen"
        konto = "mit ECHTGELD" if self.account_kind == "live" else "im Paper-Konto"
        return (f"Ich würde {self.symbol.split('/')[0]} {what}, {konto}. "
                f"Stop-Loss bei {_fmt_price(self.stop_loss or 0)}, Ziel bei {_fmt_price(self.take_profit or 0)}. "
                + ("Sag „Ja, mit Echtgeld“, wenn ich es machen soll." if self.account_kind == "live"
                   else "Soll ich?"))


class TradingEngine:
    def __init__(self, cfg: ConfigManager, storage: Storage, market: MarketData, events: EventBus, tradingview: Any):
        self.cfg = cfg
        self.storage = storage
        self.market = market
        self.events = events
        self.tv = tradingview
        self.analyzer = Analyzer(lambda: self.cfg.settings, market, storage)
        self.risk = RiskManager(lambda: self.cfg.settings, storage)
        self.brain: Any = None
        self.memory: Any = None
        self.last_analyses: dict[str, Analysis] = {}
        self.proposals: dict[str, Proposal] = {}
        self.tv_status: dict[str, Any] = {"connected": False, "driver": "none", "account": None,
                                          "message": "Noch nicht geprüft"}
        self._tasks: list[asyncio.Task] = []
        self._live_code: tuple[str, float] | None = None
        self._exec_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self.last_scan_ts = 0.0
        self.started_ts = time.time()

    # ------------------------------------------------------------------ Zustand
    @property
    def s(self) -> Settings:
        return self.cfg.settings

    @property
    def mode(self) -> str:
        m = self.storage.kv_get("mode", "off") or "off"
        return m if m in MODES else "off"

    @property
    def autonomy(self) -> str:
        return self.s.trading.autonomy

    @property
    def live_autopilot(self) -> bool:
        """Echtgeld ohne Einzel-Freigabe: nur wenn Echtgeld UND Autopilot am Mac freigeschaltet sind."""
        return bool(self.s.live_trading_unlocked and self.s.live_autopilot_unlocked)

    @property
    def auto_execute(self) -> bool:
        """Selbst handeln nur, wenn du es erlaubt hast (Echtgeld zusätzlich nur mit Autopilot-Freigabe)."""
        if self.autonomy != "auto":
            return False
        return self.mode == "paper" or (self.mode == "live" and self.live_autopilot)

    @property
    def entries_paused(self) -> bool:
        return bool(self.storage.kv_get("entries_paused", False))

    @property
    def halted(self) -> str:
        return self.storage.kv_get("halted", "") or ""

    def _emit_state(self) -> None:
        self.events.emit("state.dirty", {})

    # --------------------------------------------------------------- Lebenszyklus
    async def start(self) -> None:
        if self.mode == "live":
            self.storage.kv_set("entries_paused", True)
            await self.events.notify(
                "warning",
                "Neustart im Echtgeld-Modus: Neue Vorschläge sind pausiert. Sag „weiter“ oder tippe auf Fortsetzen.",
            )
        self._tasks = [
            asyncio.create_task(self._loop("tv", 20, self.refresh_tradingview, initial_delay=2)),
            asyncio.create_task(self._loop("scan", self.s.trading.scan_interval_minutes * 60, self.scan_once, initial_delay=8)),
            asyncio.create_task(self._loop("manage", 30, self.manage_once, initial_delay=15)),
            asyncio.create_task(self._loop("deep", 3600, self._deep_if_due, initial_delay=4)),
            asyncio.create_task(self._loop("equity", 300, self.snapshot_equity, initial_delay=30)),
            asyncio.create_task(self._loop("report", 60, self._daily_report_if_due, initial_delay=45)),
            asyncio.create_task(self._loop("memory", 3600, self._memory_maintenance, initial_delay=120)),
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
            if name == "scan":
                interval = self.s.trading.scan_interval_minutes * 60
            await asyncio.sleep(max(5.0, interval))

    # ---------------------------------------------------------------- TradingView
    async def refresh_tradingview(self) -> dict[str, Any]:
        try:
            st = await self.tv.status()
        except Exception as exc:  # Controller soll nie werfen – trotzdem absichern
            st = {"connected": False, "driver": "none", "account": None, "message": f"Fehler: {exc}"}
        changed = (st.get("connected"), (st.get("account") or {}).get("kind")) != (
            self.tv_status.get("connected"), (self.tv_status.get("account") or {}).get("kind"))
        self.tv_status = st
        if changed:
            self.events.emit("tradingview", {"status": st})
            self._emit_state()
        return st

    def account_kind(self) -> str:
        acc = self.tv_status.get("account") or {}
        return acc.get("kind") or "unknown"

    async def account_equity(self) -> float:
        acc = self.tv_status.get("account") or {}
        for key in ("equity", "balance"):
            v = acc.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return float(self.s.trading.account_size_fallback)

    # -------------------------------------------------------------- Modus-Wechsel
    async def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(f"Unbekannter Modus {mode}")
        if mode == "live":
            raise PermissionError("Echtgeld nur über request_live()/confirm_live()")
        old = self.mode
        self.storage.kv_set("mode", mode)
        self.storage.kv_set("entries_paused", False)
        if old == "live" and mode != "live":
            self.risk.reset_peak("live", 0.0)
        msg = f"Modus: {MODE_WORD[old]} → {MODE_WORD[mode]}" if old != mode else f"Läuft bereits: {MODE_WORD[mode]}"
        if mode in ("watch", "paper"):
            st = await self.refresh_tradingview()
            kind = (st.get("account") or {}).get("kind")
            if mode == "paper":
                if not st.get("connected"):
                    msg += ("\nTradingView ist noch nicht verbunden – ich analysiere schon und verbinde mich, "
                            "sobald du mir die Steuerung erlaubst (Zentrale → TradingView).")
                elif kind != "paper":
                    msg += ("\n⚠️ In TradingView ist gerade KEIN Paper-Trading-Konto aktiv. Bitte unten im "
                            "Handelspanel „Paper Trading“ auswählen – vorher führe ich nichts aus.")
                else:
                    msg += "\nTradingView Paper Trading ist aktiv ✓"
                msg += ("\nIch handle selbstständig – klein anfangen, nach deinen Risiko-Einstellungen."
                        if self.autonomy == "auto" else "\nVor jedem Trade frage ich dich.")
            else:
                msg += "\nIch analysiere und mache Vorschläge, führe aber nichts aus."
            asyncio.create_task(self.scan_once())
        if mode == "off":
            self._expire_all("Modus ausgeschaltet")
        await self.events.notify("mode", msg)
        self._emit_state()
        return msg

    async def request_live(self) -> dict[str, Any]:
        if not self.s.live_trading_unlocked:
            return {"needs_confirmation": False, "text": (
                "🔒 Echtgeld ist gesperrt. Schalte es zuerst in der Zentrale unter Einstellungen → Sicherheit frei "
                "(bewusste Entscheidung am Mac).")}
        if self.s.exchange.data_source == "demo":
            return {"needs_confirmation": False, "text": "Mit Demo-Kursdaten ist Echtgeld nicht möglich."}
        st = await self.refresh_tradingview()
        acc = st.get("account") or {}
        if not st.get("connected"):
            return {"needs_confirmation": False, "text": f"TradingView ist nicht verbunden: {st.get('message', '')}"}
        if acc.get("kind") != "live":
            return {"needs_confirmation": False, "text": (
                "In TradingView ist kein Echtgeld-Broker aktiv (gerade: "
                f"{acc.get('broker_title') or acc.get('kind') or 'unbekannt'}). Verbinde zuerst deinen Broker in TradingView.")}
        code = f"{secrets.randbelow(900000) + 100000}"
        self._live_code = (code, time.time() + 300)
        r = self.s.risk
        text = (
            "⚠️ ACHTUNG – ECHTGELD ⚠️\n"
            f"Broker in TradingView: {acc.get('broker_title') or acc.get('broker_id')} · Konto {acc.get('account_name', '')}\n"
            f"Risiko pro Trade: {r.risk_per_trade_pct}% · max. {r.max_live_order_usd:g} pro Order · Tagesverlust-Limit "
            f"{r.max_daily_loss_pct}% · Notbremse bei {r.max_drawdown_pct}% Drawdown\n"
            + ("Echtgeld-Autopilot ist freigeschaltet: Im Auto-Modus handle ich selbst – klein anfangen, "
               "harte Obergrenze je Order, Notbremse aktiv.\n" if self.live_autopilot else
               "Jeder einzelne Trade braucht trotzdem dein ausdrückliches „Ja, mit Echtgeld“.\n")
            + "Es gibt KEINE Gewinngarantie – Verluste sind möglich.\n"
            f"Bestätigungscode (5 Minuten gültig): {code}"
        )
        return {"needs_confirmation": True, "text": text, "code": code}

    async def confirm_live(self, code: str) -> str:
        if not self._live_code:
            return "Es liegt keine Echtgeld-Anfrage vor."
        expected, until = self._live_code
        if time.time() > until:
            self._live_code = None
            return "Code abgelaufen. Bitte Echtgeld erneut anfragen."
        if not hmac.compare_digest(str(code).strip(), expected):
            return "Falscher Code."
        self._live_code = None
        st = await self.refresh_tradingview()
        if (st.get("account") or {}).get("kind") != "live":
            return "In TradingView ist inzwischen kein Echtgeld-Konto mehr aktiv – abgebrochen."
        self.storage.kv_set("mode", "live")
        self.storage.kv_set("entries_paused", False)
        if self.s.trading.autonomy == "auto" and not self.live_autopilot:
            self.s.trading.autonomy = "ask"
            self.cfg.save()
        equity = await self.account_equity()
        self.risk.reset_peak("live", equity)
        if self.auto_execute:
            msg = ("🔴 ECHTGELD-MODUS AKTIV – Autopilot: Ich handle selbst, starte klein und halte mich an deine "
                   "Risiko-Grenzen. „Stopp“ oder Panik beendet alles.")
        else:
            msg = "🔴 ECHTGELD-MODUS AKTIV. Jeder Trade braucht dein „Ja, mit Echtgeld“. „Stopp“ oder Panik beendet alles."
        await self.events.notify("mode", msg)
        self._emit_state()
        return msg

    async def set_autonomy(self, level: str) -> str:
        if level not in ("ask", "auto"):
            raise ValueError("Autonomie muss ask oder auto sein")
        if level == "auto" and self.mode == "live" and not self.live_autopilot:
            return ("Im Echtgeld-Modus frage ich vor jedem Trade. Selbstständig mit Echtgeld handle ich erst, wenn du "
                    "in der Zentrale unter Einstellungen → Sicherheit den Echtgeld-Autopiloten freischaltest.")
        self.s.trading.autonomy = level
        self.cfg.save()
        self._emit_state()
        if level == "auto":
            if self.mode == "live":
                return "Okay: Ich handle ab jetzt selbstständig mit Echtgeld – klein anfangen, nach deinen Grenzen."
            extra = (" Mit Echtgeld ebenfalls (Autopilot freigeschaltet)." if self.live_autopilot
                     else " Bei Echtgeld frage ich weiter vor jedem Trade.")
            return "Okay: Im Paper-Modus handle ich ab jetzt selbstständig." + extra
        return "Okay: Ich frage dich vor jedem Trade."

    async def pause(self) -> str:
        self.storage.kv_set("entries_paused", True)
        self._emit_state()
        return "⏸ Keine neuen Trades mehr, bis du „weiter“ sagst. Offene Positionen schützen Stop-Loss/Ziel in TradingView."

    async def resume(self) -> str:
        self.storage.kv_set("entries_paused", False)
        msg = "▶️ Weiter geht's."
        if self.halted:
            self.storage.kv_set("halted", "")
            if self.mode in ("paper", "live"):
                self.risk.reset_peak(self.mode, await self.account_equity())
            msg += " Notbremse zurückgesetzt."
        self._emit_state()
        return msg

    # ----------------------------------------------------------------- Analyse
    async def scan_once(self, symbols: list[str] | None = None) -> list[Analysis]:
        if self.mode == "off" and symbols is None and self.last_scan_ts and time.time() - self.last_scan_ts < 3600:
            return list(self.last_analyses.values())
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
                self.events.emit("analysis", {"summary": self.analysis_summary(a)})
                try:
                    await self._act_on(a)
                except Exception as exc:
                    log.exception("Auswertung %s fehlgeschlagen", sym)
                    await self.events.notify("error", f"Fehler bei {sym}: {exc}")
            self.last_scan_ts = time.time()
            self._emit_state()
            return results

    @staticmethod
    def analysis_summary(a: Analysis) -> dict[str, Any]:
        return {
            "symbol": a.symbol, "price": a.price, "change_24h": a.indicators.get("change_24h_pct"),
            "regime": a.regime.label, "rsi": a.indicators.get("rsi"), "score": a.score, "direction": a.direction,
            "direction_word": a.direction_word, "has_edge": a.has_edge, "ts": a.ts,
        }

    def _open_trades(self, symbol: str | None = None, mode: str | None = None) -> list[Trade]:
        return [t for t in self.storage.open_trades(mode) if symbol is None or t.symbol == symbol]

    def _has_active_proposal(self, symbol: str) -> bool:
        return any(p.symbol == symbol and p.status in ACTIVE for p in self.proposals.values())

    async def _act_on(self, a: Analysis, force_direction: int | None = None, source: str = "Ensemble") -> str:
        mode = self.mode
        if mode == "off":
            return "off"
        th_exit = self.s.trading.exit_threshold
        # Ausstieg vorschlagen, wenn das Gesamtbild dreht
        for t in self._open_trades(a.symbol, mode if mode in ("paper", "live") else None):
            if t.meta.get("source") == "TradingView":
                continue
            flipped = (t.side == "long" and a.score < th_exit) or (t.side == "short" and a.score > -th_exit)
            if flipped and not self._has_active_proposal(a.symbol):
                p = self._new_proposal(
                    a, side="sell" if t.side == "long" else "buy", action="close", qty=t.qty,
                    reason=f"Signal gedreht (Score {a.score:+.2f})", account_kind=t.mode, trade_id=t.id,
                )
                await self._submit_proposal(p)
                return "close-proposed"
        if self.entries_paused or self.halted:
            return "paused"
        direction = force_direction if force_direction is not None else a.direction
        if direction == 0:
            return "no-signal"
        if direction < 0 and not self.s.trading.allow_short:
            return "no-short"
        if self._open_trades(a.symbol, mode if mode in ("paper", "live") else None) or self._has_active_proposal(a.symbol):
            return "exists"
        p = await self._entry_proposal(a, direction, source)
        if p is None:
            return "rejected"
        await self._submit_proposal(p)
        return "proposed"

    def _new_proposal(self, a: Analysis, side: str, action: str, qty: float, reason: str,
                      account_kind: str, trade_id: int | None = None) -> Proposal:
        now = time.time()
        return Proposal(
            id=uuid.uuid4().hex[:10], symbol=a.symbol,
            tv_symbol=to_tradingview_symbol(a.symbol, self.s.tradingview.chart_exchange_prefix),
            side=side, action=action, qty=qty, price=a.price, score=a.score, reason=reason,
            account_kind=account_kind, created=now, expires=now + self.s.trading.proposal_timeout_minutes * 60,
            trade_id=trade_id, meta={"regime": a.regime.label, "atr": a.atr},
        )

    async def _entry_proposal(self, a: Analysis, direction: int, source: str) -> Proposal | None:
        mode = self.mode
        account_kind = mode if mode in ("paper", "live") else "watch"
        side = "buy" if direction > 0 else "sell"
        try:
            price = await self.market.price(a.symbol, max_age=10)
        except Exception:
            price = a.price
        stop_dist = abs(a.price - (a.stop_long if direction > 0 else a.stop_short))
        stop = price - direction * stop_dist
        tp = price + direction * stop_dist * self.s.risk.take_profit_r
        equity = await self.account_equity()
        sizing_mode = mode if mode in ("paper", "live") else "paper"
        dec = self.risk.check_entry(
            mode=sizing_mode, symbol=a.symbol, direction=direction, price=price, stop=stop, equity=equity,
            cash=equity, open_trades=[t for t in self.storage.open_trades() if t.mode == sizing_mode],
        )
        if not dec.ok:
            self.storage.log_event("INFO", f"Risiko blockt {side} {a.symbol}: {dec.reason}", "risk")
            return None
        reasons = [f"{sd.label}: {sd.why}" for sd in a.signals if sd.signal == direction and sd.weight > 0]
        p = self._new_proposal(a, side=side, action="open", qty=_round_qty(dec.qty),
                               reason=f"{source}: Score {a.score:+.2f}, {a.regime.label}. " + " | ".join(reasons[:3]),
                               account_kind=account_kind)
        p.price, p.stop_loss, p.take_profit, p.risk_text = price, stop, tp, dec.reason
        p.meta["source"] = source
        if self.s.trading.use_ai_review and self.brain is not None:
            review = await self.brain.review_trade(a, "long" if direction > 0 else "short")
            p.ai_comment = review.get("reason", "")
            if not review.get("approve", True):
                self.storage.log_event("INFO", f"KI verwirft {side} {a.symbol}: {p.ai_comment}", "ai")
                return None
            factor = float(review.get("size_factor", 1.0))
            if factor < 1.0:
                p.qty = _round_qty(p.qty * max(0.25, factor))
        ramp = self.start_small_factor(sizing_mode)
        if ramp < 1.0:
            floor_qty = self.s.risk.min_order_usd / price if price > 0 else 0.0
            p.qty = _round_qty(min(p.qty, max(p.qty * ramp, floor_qty)))
            p.risk_text = f"{p.risk_text} · Startphase: {ramp:.0%} der normalen Größe".strip(" ·")
        if p.qty <= 0 or p.qty * price < self.s.risk.min_order_usd * 0.999:
            return None
        return p

    def start_small_factor(self, mode: str) -> float:
        """Klein anfangen: die ersten Trades je Modus mit 25 % → 100 % der berechneten Größe."""
        n_ramp = max(0, int(self.s.risk.start_small_trades))
        if n_ramp == 0:
            return 1.0
        done = len(self.storage.closed_trades(mode)) + len(self.storage.open_trades(mode))
        if done >= n_ramp:
            return 1.0
        return 0.25 + 0.75 * done / n_ramp

    async def _submit_proposal(self, p: Proposal) -> None:
        self.proposals[p.id] = p
        self._prune_proposals()
        self.events.emit("proposal", {"proposal": p.to_dict()})
        if p.account_kind == "watch":
            text = f"💡 Idee (nur Beobachten): {p.describe()}\nGrund: {p.reason}"
        else:
            text = f"🔔 Vorschlag: {p.describe()}\nGrund: {p.reason}"
            if p.ai_comment:
                text += f"\n🤖 {p.ai_comment}"
        await self.events.notify("proposal", text, {"proposal_id": p.id, "spoken": p.spoken()})
        if self.auto_execute and p.account_kind == self.mode and p.account_kind in ("paper", "live"):
            await self.approve(p.id, source="auto")
        self._emit_state()

    def _prune_proposals(self, keep: int = 50) -> None:
        if len(self.proposals) > keep:
            for pid in sorted(self.proposals, key=lambda k: self.proposals[k].created)[: len(self.proposals) - keep]:
                if self.proposals[pid].status not in ACTIVE:
                    del self.proposals[pid]

    def _expire_all(self, reason: str) -> None:
        for p in self.proposals.values():
            if p.status == "pending":
                p.status, p.result = "expired", reason

    # ----------------------------------------------------------- Vorschläge
    def list_proposals(self, active_only: bool = True) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for p in sorted(self.proposals.values(), key=lambda x: -x.created):
            if p.status == "pending" and now > p.expires:
                p.status, p.result = "expired", "Zeit abgelaufen"
            if active_only and p.status not in ACTIVE:
                continue
            out.append(p.to_dict())
        return out

    def pending_proposal(self) -> dict[str, Any] | None:
        pending = [p for p in self.list_proposals(True) if p["status"] == "pending" and p["account_kind"] != "watch"]
        return pending[0] if pending else None

    async def reject(self, pid: str, source: str = "ui") -> str:
        p = self.proposals.get(pid)
        if p is None or p.status != "pending":
            return "Diesen Vorschlag gibt es nicht (mehr)."
        p.status, p.result = "rejected", f"Abgelehnt ({source})"
        self.events.emit("proposal", {"proposal": p.to_dict()})
        self._emit_state()
        return f"Okay, ich lasse {p.symbol} aus."

    async def approve(self, pid: str, source: str = "ui") -> str:
        p = self.proposals.get(pid)
        if p is None:
            return "Diesen Vorschlag gibt es nicht."
        if p.status != "pending":
            return f"Der Vorschlag ist bereits {p.status}."
        if time.time() > p.expires:
            p.status, p.result = "expired", "Zeit abgelaufen"
            self.events.emit("proposal", {"proposal": p.to_dict()})
            return "Der Vorschlag ist abgelaufen – die Lage kann sich geändert haben. Ich analysiere neu."
        mode = self.mode
        if p.account_kind == "watch" or mode in ("off", "watch"):
            return "Im Beobachten-Modus führe ich keine Orders aus. Schalte auf Paper Trading, wenn ich handeln soll."
        if p.account_kind != mode:
            p.status, p.result = "expired", "Modus hat sich geändert"
            self.events.emit("proposal", {"proposal": p.to_dict()})
            return "Der Vorschlag gehört zu einem anderen Modus – verworfen."
        if mode == "live" and not self.s.live_trading_unlocked:
            return "Echtgeld ist gesperrt."
        p.status = "approved"
        self.events.emit("proposal", {"proposal": p.to_dict()})
        return await self._execute(p, source)

    async def _execute(self, p: Proposal, source: str) -> str:
        from ..tradingview.models import AccountKind, OrderRequest  # spät importieren (eigenes Modul)

        expected = AccountKind.LIVE if p.account_kind == "live" else AccountKind.PAPER
        async with self._exec_lock:
            p.status = "executing"
            self.events.emit("proposal", {"proposal": p.to_dict()})
            try:
                if p.action == "close":
                    res = await self.tv.close_position(p.tv_symbol, expected)
                else:
                    req = OrderRequest(
                        symbol=p.tv_symbol, side=p.side, qty=p.qty, order_type=p.order_type,
                        limit_price=p.limit_price, take_profit=p.take_profit if self.s.tradingview.use_brackets else None,
                        stop_loss=p.stop_loss if self.s.tradingview.use_brackets else None,
                    )
                    res = await self.tv.place_order(req, expected)
            except Exception as exc:
                log.exception("TradingView-Ausführung fehlgeschlagen")
                p.status, p.result = "failed", f"Fehler: {exc}"
                self.events.emit("proposal", {"proposal": p.to_dict()})
                await self.events.notify("error", f"❌ {p.symbol}: Ausführung fehlgeschlagen – {exc}")
                return p.result
            if not res.ok:
                p.status, p.result = "failed", res.message
                self.events.emit("proposal", {"proposal": p.to_dict()})
                await self.events.notify("error", f"❌ {p.describe()} – nicht ausgeführt: {res.message}")
                self._emit_state()
                return f"Nicht ausgeführt: {res.message}"
            p.status, p.result = "done", res.message
            if p.action == "close":
                text = await self._journal_close(p, res)
            else:
                text = await self._journal_open(p, res, source)
            self.events.emit("proposal", {"proposal": p.to_dict()})
            self._emit_state()
            return text

    async def _journal_open(self, p: Proposal, res: Any, source: str) -> str:
        price = res.avg_price or p.price
        direction = 1 if p.side == "buy" else -1
        stop_dist = abs(p.price - (p.stop_loss or p.price))
        stop = price - direction * stop_dist if stop_dist else p.stop_loss
        trade = Trade(
            mode=p.account_kind, symbol=p.symbol, side="long" if p.side == "buy" else "short",
            qty=res.filled_qty or p.qty, entry_price=price, entry_time=time.time(), stop=stop, initial_stop=stop,
            take_profit=p.take_profit, entry_reason=p.reason, score=p.score, ai_comment=p.ai_comment,
            strategies={sd.name: float(sd.signal) for sd in (self.last_analyses[p.symbol].signals
                                                              if p.symbol in self.last_analyses else [])},
            meta={"order_id": res.order_id, "tv_symbol": p.tv_symbol, "proposal": p.id, "approved_by": source,
                  "source": p.meta.get("source", ""), "regime": p.meta.get("regime", ""), "atr": p.meta.get("atr", 0)},
        )
        self.storage.insert_trade(trade)
        p.trade_id = trade.id
        konto = "ECHTGELD" if p.account_kind == "live" else "PAPER"
        text = (f"{'🟢' if p.side == 'buy' else '🔴'} [{konto}] {p.describe()} – in TradingView ausgeführt"
                f"{f' zu {_fmt_price(price)}' if res.avg_price else ''}.")
        await self.events.notify("trade", text, {"trade_id": trade.id})
        return text

    async def _journal_close(self, p: Proposal, res: Any) -> str:
        trades = [t for t in self._open_trades(p.symbol, p.account_kind) if p.trade_id in (None, t.id)]
        price = res.avg_price
        if price is None:
            try:
                price = await self.market.price(p.symbol, max_age=5)
            except Exception:
                price = p.price
        texts = [await self._finalize(t, price, f"Geschlossen ({p.reason})") for t in trades]
        return "\n".join(texts) or f"{p.symbol}-Position in TradingView geschlossen."

    async def _finalize(self, t: Trade, exit_price: float, reason: str) -> str:
        t.exit_price = exit_price
        t.exit_time = time.time()
        t.pnl = (exit_price - t.entry_price) * t.qty * t.direction - (t.fees or 0)
        t.pnl_pct = t.pnl / (t.entry_price * t.qty) * 100 if t.qty and t.entry_price else 0.0
        rpu = t.risk_per_unit()
        t.r_multiple = (t.pnl / (rpu * t.qty)) if rpu > 0 and t.qty else 0.0
        t.status, t.exit_reason = "closed", reason
        self.storage.update_trade(t)
        konto = "ECHTGELD" if t.mode == "live" else "PAPER"
        text = (f"{'✅' if t.pnl > 0 else '❌'} [{konto}] {t.symbol} {t.side} geschlossen: {_fmt_price(t.entry_price)} → "
                f"{_fmt_price(exit_price)} = {t.pnl:+,.2f} ({t.pnl_pct:+.2f}%, {t.r_multiple:+.2f}R). {reason}")
        await self.events.notify("trade", text, {"trade_id": t.id})
        if self.brain is not None and self.s.ai.reflect_on_trades:
            asyncio.create_task(self.brain.reflect_on_trade(t))
        return text

    # -------------------------------------------------------------- Verwalten
    async def manage_once(self) -> None:
        self.list_proposals(True)  # lässt abgelaufene Vorschläge verfallen
        if not self.tv_status.get("connected"):
            return
        kind = self.account_kind()
        journal = [t for t in self.storage.open_trades() if t.mode == kind]
        if not journal:
            return
        try:
            positions = await self.tv.positions()
        except Exception as exc:
            log.info("Positionen nicht lesbar: %s", exc)
            return
        held = {(p.get("symbol") or "").upper() for p in positions}
        for t in journal:
            tv_sym = (t.meta.get("tv_symbol") or to_tradingview_symbol(t.symbol, self.s.tradingview.chart_exchange_prefix)).upper()
            if tv_sym in held or tv_sym.split(":")[-1] in {h.split(":")[-1] for h in held}:
                continue
            try:
                price = await self.market.price(t.symbol, max_age=10)
            except Exception:
                price = t.entry_price
            if t.take_profit and t.stop:
                near_tp = abs(price - t.take_profit) < abs(price - t.stop)
                exit_price, why = (t.take_profit, "Ziel erreicht 🎯") if near_tp else (t.stop, "Stop-Loss")
            else:
                exit_price, why = price, "in TradingView geschlossen"
            await self._finalize(t, exit_price, f"{why} (in TradingView)")

    async def close_symbol(self, symbol: str, reason: str = "Auf deinen Wunsch") -> str:
        symbol = normalize_symbol(symbol, self.s.trading.watchlist)
        mode = self.mode
        trades = self._open_trades(symbol, mode if mode in ("paper", "live") else None)
        if not trades:
            return f"Ich habe keine offene {symbol}-Position im Journal."
        t = trades[0]
        a = self.last_analyses.get(symbol)
        price = a.price if a else t.entry_price
        p = Proposal(
            id=uuid.uuid4().hex[:10], symbol=symbol,
            tv_symbol=t.meta.get("tv_symbol") or to_tradingview_symbol(symbol, self.s.tradingview.chart_exchange_prefix),
            side="sell" if t.side == "long" else "buy", action="close", qty=t.qty, price=price, reason=reason,
            account_kind=t.mode, expires=time.time() + self.s.trading.proposal_timeout_minutes * 60, trade_id=t.id,
        )
        self.proposals[p.id] = p
        if t.mode == "live":
            self.events.emit("proposal", {"proposal": p.to_dict()})
            await self.events.notify("proposal", f"🔔 {p.describe()} – bitte bestätigen.", {"proposal_id": p.id,
                                                                                          "spoken": p.spoken()})
            return f"Soll ich die {symbol}-Position mit Echtgeld schließen? Sag „Ja, mit Echtgeld“."
        return await self.approve(p.id, source="nutzer")

    async def panic(self) -> str:
        from ..tradingview.models import AccountKind

        self.storage.kv_set("entries_paused", True)
        self._expire_all("Panik")
        results = []
        for t in self.storage.open_trades():
            expected = AccountKind.LIVE if t.mode == "live" else AccountKind.PAPER
            tv_sym = t.meta.get("tv_symbol") or to_tradingview_symbol(t.symbol, self.s.tradingview.chart_exchange_prefix)
            try:
                res = await self.tv.close_position(tv_sym, expected)
            except Exception as exc:
                results.append(f"{t.symbol}: Fehler {exc}")
                continue
            if res.ok:
                price = res.avg_price or (await self._safe_price(t.symbol, t.entry_price))
                results.append(await self._finalize(t, price, "PANIK – alles schließen"))
            else:
                results.append(f"{t.symbol}: {res.message} – bitte in TradingView selbst prüfen!")
        self.storage.kv_set("mode", "off")
        msg = f"🚨 PANIK: Trading AUS, {len(results)} Position(en) bearbeitet."
        await self.events.notify("mode", msg)
        self._emit_state()
        return msg + ("\n" + "\n".join(results) if results else "")

    async def _safe_price(self, symbol: str, fallback: float) -> float:
        try:
            return await self.market.price(symbol, max_age=5)
        except Exception:
            return fallback

    # ------------------------------------------------------------- Konto
    async def snapshot_equity(self) -> None:
        mode = self.mode
        if mode not in ("paper", "live") or not self.tv_status.get("connected"):
            return
        if self.account_kind() != mode:
            return
        acc = self.tv_status.get("account") or {}
        equity = acc.get("equity") or acc.get("balance")
        if not isinstance(equity, (int, float)) or equity <= 0:
            return
        self.storage.record_equity(mode, float(equity), float(acc.get("balance") or equity))
        reason = self.risk.update_equity_marks(mode, float(equity))
        if reason and not self.halted:
            self.storage.kv_set("halted", reason)
            await self.events.notify("warning", f"🛑 {reason}\nKeine neuen Trades mehr. „weiter“ setzt zurück.")
            self._emit_state()

    # ------------------------------------------------------- TradingView-Alerts
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
        await self.events.notify("signal", f"📡 TradingView-Alert: {action.upper()} {symbol}")
        if action == "close":
            return {"ok": True, "result": await self.close_symbol(symbol, "TradingView-Alert")}
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
    def positions(self) -> list[dict[str, Any]]:
        out = []
        for t in self.storage.open_trades():
            a = self.last_analyses.get(t.symbol)
            price = a.price if a else None
            out.append({
                "id": t.id, "symbol": t.symbol, "side": t.side, "qty": t.qty, "entry": t.entry_price, "stop": t.stop,
                "take_profit": t.take_profit, "mode": t.mode, "opened": t.entry_time, "price": price,
                "pnl": t.unrealized(price) if price else None,
            })
        return out

    def state_snapshot_trading(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "mode_word": MODE_WORD[self.mode], "autonomy": self.autonomy,
            "entries_paused": self.entries_paused, "halted": self.halted,
            "proposals": self.list_proposals(True), "positions": self.positions(),
            "analyses": [self.analysis_summary(a) for a in self.last_analyses.values()],
            "watchlist": list(self.s.trading.watchlist), "timeframe": self.s.trading.timeframe,
            "data_source": self.s.exchange.data_source, "tradingview": self.tv_status,
        }

    async def status(self) -> dict[str, Any]:
        st = self.state_snapshot_trading()
        st["stats"] = {m: self.storage.trade_stats(m) for m in ("paper", "live")}
        st["last_scan"] = self.last_scan_ts
        return st

    async def status_text(self) -> str:
        st = await self.status()
        tv = st["tradingview"]
        acc = tv.get("account") or {}
        konto = {"paper": "Paper Trading ✓", "live": "ECHTGELD ⚠️"}.get(acc.get("kind"), "unbekannt")
        lines = [f"🤖 Vincent – Modus: {st['mode_word']} · {'fragt vor jedem Trade' if st['autonomy'] == 'ask' else 'handelt selbstständig'}"]
        lines.append(f"TradingView: {'verbunden' if tv.get('connected') else 'nicht verbunden'} · Konto: {konto}"
                     + (f" · Wert {acc['equity']:,.2f}" if isinstance(acc.get("equity"), (int, float)) else ""))
        if st["halted"]:
            lines.append(f"🛑 {st['halted']}")
        elif st["entries_paused"]:
            lines.append("⏸ Neue Trades pausiert")
        for m, label in (("paper", "Paper"), ("live", "Echtgeld")):
            s = st["stats"][m]
            if s["trades"]:
                lines.append(f"{label}: {s['trades']} Trades, Trefferquote {s['win_rate']:.0f}%, "
                             f"Ergebnis {s['pnl']:+,.2f}, Ø {s['avg_r']:+.2f}R")
        if st["positions"]:
            lines.append("Offene Positionen:")
            for p in st["positions"]:
                pnl = f" · {p['pnl']:+,.2f}" if p.get("pnl") is not None else ""
                lines.append(f"  [{p['mode']}] {p['symbol']} {p['side']} {p['qty']:g} @ {_fmt_price(p['entry'])}"
                             f" · SL {_fmt_price(p['stop'] or 0)}{pnl}")
        if st["proposals"]:
            lines.append("Offene Vorschläge:")
            for p in st["proposals"]:
                lines.append(f"  {p['symbol']}: {SIDE_WORD.get(p['side'], p['side'])} {p['qty']:g} ({p['status']})")
        if self.last_analyses:
            lines.append("Letzte Signale:")
            for sym, a in self.last_analyses.items():
                lines.append(f"  {sym}: Score {a.score:+.2f} → {a.direction_word}")
        if st["data_source"] == "demo":
            lines.append("ℹ️ DEMO-KURSE aktiv (künstlich).")
        return "\n".join(lines)

    async def daily_report(self) -> str:
        text = await self.status_text()
        closed = self.storage.closed_trades(limit=50, since=time.time() - 86400)
        lines = [f"📅 Tagesbericht {time.strftime('%d.%m.%Y')}", text, ""]
        if closed:
            lines.append(f"Heute geschlossen ({len(closed)}):")
            lines += [f"  {t.symbol} {t.side}: {t.pnl:+,.2f} ({t.r_multiple:+.2f}R) – {t.exit_reason}" for t in closed]
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
        except ValueError:
            return
        today = time.strftime("%Y-%m-%d")
        if time.strftime("%H:%M") >= f"{h:02d}:{m:02d}" and self.storage.kv_get("last_report_day") != today:
            self.storage.kv_set("last_report_day", today)
            await self.events.notify("report", await self.daily_report())

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

    async def _memory_maintenance(self) -> None:
        if self.memory is not None and hasattr(self.memory, "maybe_consolidate"):
            report = await self.memory.maybe_consolidate()
            if report:
                self.storage.log_event("INFO", f"Gedächtnis: {report}", "memory")


def _round_qty(q: float) -> float:
    """Rundet auf 6 signifikante Stellen (TradingView prüft die genaue Schrittweite selbst)."""
    if q <= 0:
        return 0.0
    from math import floor, log10

    digits = max(0, 5 - int(floor(log10(q))))
    return round(q, digits)


__all__ = ["TradingEngine", "Proposal", "MODES", "MODE_WORD"]
