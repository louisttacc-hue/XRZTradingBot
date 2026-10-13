"""Risiko-Management – hat IMMER das letzte Wort, auch über die KI."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..config import Settings
from ..storage import Storage, Trade


@dataclass
class RiskDecision:
    ok: bool
    reason: str
    qty: float = 0.0
    notional: float = 0.0
    risk_amount: float = 0.0


class RiskManager:
    def __init__(self, settings: Callable[[], Settings], storage: Storage):
        self._settings = settings
        self.storage = storage

    @property
    def s(self) -> Settings:
        return self._settings()

    # ---------------------------------------------------------- Notbremse etc.
    def update_equity_marks(self, mode: str, equity: float) -> str | None:
        """Aktualisiert Tagesstart & Höchststand. Gibt Grund zurück, falls die Notbremse greift."""
        today = time.strftime("%Y-%m-%d")
        day = self.storage.kv_get(f"day:{mode}")
        if not day or day.get("date") != today:
            self.storage.kv_set(f"day:{mode}", {"date": today, "equity": equity})
        peak = self.storage.kv_get(f"peak:{mode}", 0.0) or 0.0
        if equity > peak:
            self.storage.kv_set(f"peak:{mode}", equity)
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100
            if dd >= self.s.risk.max_drawdown_pct:
                return (
                    f"Notbremse: Drawdown {dd:.1f}% ≥ Limit {self.s.risk.max_drawdown_pct:.1f}% "
                    f"(Höchststand {peak:,.2f}, jetzt {equity:,.2f})"
                )
        return None

    def day_start_equity(self, mode: str, fallback: float) -> float:
        day = self.storage.kv_get(f"day:{mode}")
        if day and day.get("date") == time.strftime("%Y-%m-%d"):
            return float(day["equity"])
        return fallback

    def reset_peak(self, mode: str, equity: float) -> None:
        self.storage.kv_set(f"peak:{mode}", equity)

    # --------------------------------------------------------------- Einstieg
    def check_entry(
        self,
        *,
        mode: str,
        symbol: str,
        direction: int,
        price: float,
        stop: float,
        equity: float,
        cash: float,
        open_trades: list[Trade],
        size_factor: float = 1.0,
    ) -> RiskDecision:
        r = self.s.risk
        live = mode == "live"
        if mode not in ("paper", "live"):
            return RiskDecision(False, "Trading ist ausgeschaltet")
        if equity <= 0 or price <= 0:
            return RiskDecision(False, "Kein Kapital / kein Preis")
        if direction < 0 and not self.s.trading.allow_short:
            return RiskDecision(False, "Short-Trades sind ausgeschaltet (Einstellungen → Trading → Shorts erlauben)")
        mode_trades = [t for t in open_trades if t.mode == mode]
        if any(t.symbol == symbol for t in mode_trades):
            return RiskDecision(False, f"Es gibt bereits eine offene Position in {symbol}")
        if len(mode_trades) >= r.max_open_positions:
            return RiskDecision(False, f"Maximal {r.max_open_positions} offene Positionen erreicht")

        start = self.day_start_equity(mode, equity)
        if start > 0:
            day_pnl_pct = (equity - start) / start * 100
            if day_pnl_pct <= -r.max_daily_loss_pct:
                return RiskDecision(
                    False, f"Tagesverlust-Limit erreicht ({day_pnl_pct:.2f}% ≤ −{r.max_daily_loss_pct}%) – heute keine neuen Trades"
                )
        last = self.storage.closed_trades(mode, limit=20)
        for t in last:
            if t.symbol == symbol and (t.pnl or 0) < 0 and t.exit_time:
                mins = (time.time() - t.exit_time) / 60
                if mins < r.cooldown_minutes_after_loss:
                    return RiskDecision(
                        False, f"Abkühlphase nach Verlust in {symbol} (noch {r.cooldown_minutes_after_loss - mins:.0f} Min.)"
                    )
                break

        per_unit = abs(price - stop)
        if per_unit <= 0 or per_unit / price > 0.5:
            return RiskDecision(False, "Ungültiger Stop-Abstand")
        risk_amount = equity * r.risk_per_trade_pct / 100 * max(0.0, min(1.0, size_factor))
        qty = risk_amount / per_unit
        notional = qty * price
        caps = [equity * r.max_position_pct / 100]
        if direction > 0 or live:
            caps.append(cash * 0.98)  # Gebührenpuffer
        if live:
            caps.append(r.max_live_order_usd)
        cap = max(0.0, min(caps))
        if notional > cap:
            notional = cap
            qty = notional / price
            risk_amount = qty * per_unit
        if notional < r.min_order_usd:
            return RiskDecision(False, f"Ordergröße {notional:.2f} unter Minimum {r.min_order_usd:.2f} (zu wenig Kapital/Cash)")
        return RiskDecision(
            True,
            f"Risiko {risk_amount:,.2f} ({risk_amount / equity * 100:.2f}% des Kontos), Positionsgröße {notional:,.2f}",
            qty=qty, notional=notional, risk_amount=risk_amount,
        )

    # ------------------------------------------------------- Stop-Nachführung
    def trail(self, trade: Trade, price: float, atr: float) -> float | None:
        """Gibt einen neuen (engeren) Stop zurück oder None."""
        r = self.s.risk
        if not trade.stop:
            return None
        cur_r = trade.current_r(price)
        new = trade.stop
        d = trade.direction
        if cur_r >= r.breakeven_at_r:
            fee_buffer = trade.entry_price * (r.fee_pct * 2) / 100
            be = trade.entry_price + d * fee_buffer
            new = max(new, be) if d > 0 else min(new, be)
        if cur_r >= r.trailing_activate_r and atr > 0:
            tr = price - d * atr * r.trailing_atr_mult
            new = max(new, tr) if d > 0 else min(new, tr)
        if (d > 0 and new > trade.stop * 1.0001) or (d < 0 and new < trade.stop * 0.9999):
            return new
        return None
