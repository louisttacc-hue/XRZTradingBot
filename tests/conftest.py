import os
import time

import pytest

os.environ["XRZ_NO_KEYRING"] = "1"
os.environ.pop("VINCENT_TOKEN", None)


class FakeRouter:
    """Ersetzt die echte KI in Tests."""

    def __init__(self, review='{"approve": true, "size_factor": 1.0, "confidence": 70, "reason": "ok"}'):
        self.review = review
        self.calls = []
        self.last_used = "Fake"
        self.last_error = ""

    async def complete(self, system, prompt, images=None, max_tokens=2000):
        self.calls.append(("complete", prompt))
        if "JSON" in prompt and "Trade" in prompt:
            return self.review
        if "JSON" in prompt:
            return "[]"
        return "Lektion: Test-Lektion."

    async def agent(self, system, history, user_text, tools, images=None):
        self.calls.append(("agent", user_text))
        status = next(t for t in tools if t.name == "get_status")
        return "Status: " + (await status.handler({}))[:60]

    async def embed(self, texts):
        return None

    def describe(self):
        return "Fake"

    async def health(self):
        return {"chain": "Fake", "ollama_running": False, "ollama_models": [], "ollama_model_ready": False,
                "anthropic_key": False, "openai_key": False, "last_used": "", "last_error": ""}


class FakeTV:
    """Simuliert die TradingView-App (Paper- oder Echtgeld-Konto)."""

    def __init__(self, kind: str = "paper", connected: bool = True):
        self.kind = kind
        self.connected = connected
        self.orders = []
        self.closed = []
        self.held = []
        self.symbol = ""
        self.fail_next = ""

    def _account(self):
        from xrz.tradingview.models import AccountInfo, AccountKind

        live = self.kind == "live"
        return AccountInfo(kind=AccountKind(self.kind), broker_id="Oanda" if live else "Paper",
                           broker_title="OANDA" if live else "Paper Trading", account_name="Testkonto",
                           account_type="live" if live else "demo", balance=10_000.0, equity=10_000.0, currency="USD",
                           evidence=["fake"])

    async def status(self):
        if not self.connected:
            return {"connected": False, "driver": "none", "account": None, "message": "nicht verbunden"}
        return {"connected": True, "driver": "cdp", "account": self._account().to_dict(), "message": "ok", "version": "test"}

    async def connect(self, allow_restart: bool = False):
        return await self.status()

    async def read_account(self):
        return self._account()

    async def set_symbol(self, tv_symbol: str) -> bool:
        self.symbol = tv_symbol
        return True

    async def quote(self, tv_symbol):
        return {"bid": 99.0, "ask": 101.0, "last": 100.0}

    async def positions(self):
        return [{"symbol": s, "side": "long", "qty": 1.0, "avg_price": 100.0, "take_profit": None, "stop_loss": None, "id": s}
                for s in self.held]

    async def place_order(self, req, expected, dry_run=False):
        from xrz.tradingview.models import OrderResult

        if expected.value != self.kind:
            return OrderResult(ok=False, stage="account", message="Falsches Konto aktiv – abgebrochen")
        if self.fail_next:
            msg, self.fail_next = self.fail_next, ""
            return OrderResult(ok=False, stage="submit", message=msg)
        if dry_run:
            return OrderResult(ok=True, stage="dry_run", message="Test ohne Absenden")
        self.orders.append((req, expected))
        self.held.append(req.symbol)
        return OrderResult(ok=True, stage="done", message="ausgeführt", order_id=str(len(self.orders)),
                           filled_qty=req.qty, avg_price=100.0)

    async def close_position(self, tv_symbol, expected):
        from xrz.tradingview.models import OrderResult

        if expected.value != self.kind:
            return OrderResult(ok=False, stage="account", message="Falsches Konto aktiv – abgebrochen")
        self.closed.append(tv_symbol)
        if tv_symbol in self.held:
            self.held.remove(tv_symbol)
        return OrderResult(ok=True, stage="done", message="geschlossen", avg_price=105.0)

    async def screenshot_b64(self):
        return None

    async def disconnect(self):
        return None


class FakeVoice:
    def __init__(self):
        self.said = []
        self.muted = False
        self.stopped = 0

    def status(self):
        return {"state": "muted" if self.muted else "idle", "wake_word": "hey vincent", "wake_enabled": True,
                "muted": self.muted, "tts": "fake", "stt": "fake", "error": ""}

    async def start(self):
        return None

    async def stop(self):
        return None

    async def listen_now(self):
        return None

    async def stop_all(self):
        self.stopped += 1

    def set_muted(self, muted):
        self.muted = muted

    async def say(self, text, interrupt=False):
        self.said.append(text)

    async def list_voices(self):
        return {"provider": "fake", "voices": [{"id": "v1", "name": "Vincent", "lang": "de"}]}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XRZ_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ctx(data_dir):
    from xrz.app import build

    c = build(data_dir, demo=True, token="test-token")
    c.cfg.settings.trading.watchlist = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    tv = FakeTV()
    c.tradingview = tv
    c.engine.tv = tv
    c.brain.tv = tv
    c.telegram.tv = tv
    voice = FakeVoice()
    c.voice = voice
    c.brain.voice = voice
    router = FakeRouter()
    c.brain.router = router
    c.memory.router = None
    c.engine.tv_status = {"connected": True, "driver": "cdp", "account": tv._account().to_dict(), "message": "ok"}
    yield c
    c.storage.close()


def loosen(ctx):
    """Niedrige Schwellen, damit in Tests sicher Vorschläge entstehen."""
    t = ctx.cfg.settings.trading
    t.entry_threshold = 0.1
    t.min_active_strategies = 1
    t.max_extension_atr = 99
    t.allow_short = True
    return ctx


def now() -> float:
    return time.time()
