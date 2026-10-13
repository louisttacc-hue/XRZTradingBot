"""Datenmodelle der TradingView-Steuerung (Konto, Order-Wunsch, Ergebnis)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class AccountKind(str, Enum):
    PAPER = "paper"
    LIVE = "live"
    UNKNOWN = "unknown"


class TradingViewError(RuntimeError):
    """Lesefehler der TradingView-Steuerung (Text ist deutsch und für den Nutzer gedacht)."""


@dataclass
class AccountInfo:
    """Was in TradingView gerade als Handelskonto aktiv ist – inkl. der Belege dafür."""

    kind: AccountKind = AccountKind.UNKNOWN
    broker_id: str = ""
    broker_title: str = ""
    account_name: str = ""
    account_type: str = ""
    balance: float | None = None
    equity: float | None = None
    currency: str = ""
    evidence: list[str] = field(default_factory=list)  # positive Belege (deutsch)
    problems: list[str] = field(default_factory=list)  # Gründe, warum die Einordnung unsicher ist

    @property
    def label(self) -> str:
        if self.kind == AccountKind.PAPER:
            name = self.account_name.strip()
            return f"Paper Trading ({name})" if name and name.lower() != "paper trading" else "Paper Trading"
        if self.kind == AccountKind.LIVE:
            broker = self.broker_title or self.broker_id or "Broker"
            return f"{broker} – {self.account_name} (Echtgeld)" if self.account_name else f"{broker} (Echtgeld)"
        if self.broker_title or self.broker_id:
            return f"unklar ({self.broker_title or self.broker_id})"
        return "unbekannt"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "broker": self.broker_title or self.broker_id,
            "broker_id": self.broker_id,
            "broker_title": self.broker_title,
            "account_name": self.account_name,
            "account_type": self.account_type,
            "label": self.label,
            "balance": self.balance,
            "equity": self.equity,
            "currency": self.currency,
            "evidence": list(self.evidence),
            "problems": list(self.problems),
        }


@dataclass
class OrderRequest:
    symbol: str  # TradingView-Schreibweise, z. B. "BINANCE:BTCUSDT"
    side: Literal["buy", "sell"]
    qty: float
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        side = "Kauf" if self.side == "buy" else "Verkauf"
        text = f"{side} {self.qty:g} {self.symbol} ({'Limit ' + format(self.limit_price, 'g') if self.order_type == 'limit' and self.limit_price else 'Markt'})"
        extras = []
        if self.stop_loss:
            extras.append(f"SL {self.stop_loss:g}")
        if self.take_profit:
            extras.append(f"TP {self.take_profit:g}")
        return text + (", " + ", ".join(extras) if extras else "")


@dataclass
class OrderResult:
    ok: bool
    stage: str  # Schritt, an dem der Ablauf endete (z. B. "account", "readback", "done", "dry_run")
    message: str  # deutsch, für Nutzer und Overlay
    order_id: str = ""
    filled_qty: float | None = None
    avg_price: float | None = None
    screenshot_b64: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_screenshot: bool = True) -> dict[str, Any]:
        out = asdict(self)
        if not include_screenshot:
            out.pop("screenshot_b64", None)
        return out
