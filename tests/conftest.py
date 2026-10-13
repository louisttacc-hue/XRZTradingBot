import os

import pytest

os.environ["XRZ_NO_KEYRING"] = "1"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XRZ_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ctx(data_dir):
    from xrz.app import build

    c = build(data_dir, demo=True)
    c.cfg.settings.trading.watchlist = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    yield c
    c.storage.close()


class FakeRouter:
    """Ersetzt die echte KI in Tests."""

    def __init__(self, review='{"approve": true, "size_factor": 1.0, "confidence": 70, "reason": "ok"}'):
        self.review = review
        self.calls = []

    async def complete(self, system, prompt, images=None, max_tokens=2000):
        self.calls.append(("complete", prompt))
        if "JSON" in prompt:
            return self.review
        return "Lektion: Test-Lektion."

    async def agent(self, system, history, user_text, tools, images=None):
        self.calls.append(("agent", user_text))
        status = next(t for t in tools if t.name == "get_status")
        return "Status: " + (await status.handler({}))[:50]

    async def embed(self, texts):
        return None

    def describe(self):
        return "Fake"

    async def health(self):
        return {"chain": "Fake", "ollama_running": False, "ollama_models": [], "ollama_model_ready": False,
                "anthropic_key": False, "openai_key": False, "last_used": "", "last_error": ""}
