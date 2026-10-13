from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import FakeRouter
from xrz.web.server import create_dashboard, create_webhook_app


@pytest.fixture
def client(ctx):
    ctx.brain.router = FakeRouter()
    ctx.kb.router = None
    app = create_dashboard(ctx)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    c.headers["X-XRZ-Token"] = ctx.cfg.secrets.get("internal_web_token")
    return c


def test_dashboard_requires_token_and_local_host(ctx, client):
    raw = TestClient(create_dashboard(ctx), base_url="http://127.0.0.1:8765")
    assert raw.get("/api/status").status_code == 401
    evil = TestClient(create_dashboard(ctx), base_url="http://evil.example")
    evil.headers["X-XRZ-Token"] = ctx.cfg.secrets.get("internal_web_token")
    assert evil.get("/api/status").status_code == 400
    page = raw.get("/")
    assert page.status_code == 200 and ctx.cfg.secrets.get("internal_web_token") in page.text


def test_dashboard_endpoints(client):
    st = client.get("/api/status").json()
    assert st["mode"] == "off" and "telegram" in st and st["knowledge_count"] >= 0
    r = client.post("/api/mode", json={"mode": "paper"})
    assert r.status_code == 200 and "PAPER" in r.json()["text"]
    r = client.post("/api/analyze", json={"symbol": "BTC"})
    assert r.status_code == 200 and "BTC/USDT" in r.json()["text"]
    c = client.get("/api/candles", params={"symbol": "BTC/USDT", "limit": 50}).json()
    assert len(c["candles"]) == 50
    r = client.post("/api/knowledge", json={"text": "Testwissen über Ichimoku", "topic": "test"})
    assert r.json()["added"] == 1
    assert client.get("/api/knowledge", params={"q": "Ichimoku"}).json()["items"]
    assert "Status" in client.post("/api/chat", json={"message": "Status?"}).json()["reply"]


def test_settings_update_and_secret_masking(client, ctx):
    r = client.post("/api/settings", json={"settings": {"risk": {"risk_per_trade_pct": 0.75}}, "secrets": {"anthropic_api_key": "sk-ant-test-1234567890"}})
    assert r.status_code == 200
    assert ctx.cfg.settings.risk.risk_per_trade_pct == 0.75
    s = client.get("/api/settings").json()
    assert s["secrets"]["anthropic_api_key"]["set"] is True
    assert "1234567890" not in str(s)
    r = client.post("/api/settings", json={"settings": {"risk": {"risk_per_trade_pct": 99}}})
    assert r.status_code == 400


def test_webhook_app(ctx):
    ctx.brain.router = FakeRouter()
    ctx.cfg.secrets.set("tradingview_passphrase", "pw")
    c = TestClient(create_webhook_app(ctx))
    assert c.post("/webhook/tradingview", content="kein json").status_code == 400
    assert c.post("/webhook/tradingview", json={"passphrase": "nein", "symbol": "BTCUSDT", "action": "buy"}).status_code == 403
    r = c.post("/webhook/tradingview", json={"passphrase": "pw", "symbol": "BTCUSDT", "action": "buy"})
    assert r.status_code == 200 and r.json()["ok"]
    # Dashboard-Routen sind über den Webhook-Server NICHT erreichbar
    assert c.get("/api/status").status_code == 404


class _Msg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


def _update(chat_id):
    msg = _Msg()
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=msg), msg


async def test_telegram_pairing_and_guard(ctx):
    tg = ctx.telegram
    code = tg.pairing_code()
    upd, msg = _update(42)
    await tg.cmd_start(upd, SimpleNamespace(args=["000000" if code != "000000" else "111111"]))
    assert 42 not in ctx.cfg.settings.telegram.allowed_chat_ids and "Falsch" in msg.sent[-1]
    # Nicht gekoppelt → Befehle werden blockiert
    guarded = tg._guard(tg.cmd_status)
    await guarded(upd, SimpleNamespace(args=[]))
    assert "nicht gekoppelt" in msg.sent[-1]
    await tg.cmd_start(upd, SimpleNamespace(args=[code]))
    assert 42 in ctx.cfg.settings.telegram.allowed_chat_ids
    await guarded(upd, SimpleNamespace(args=[]))
    assert "XRZ Status" in msg.sent[-1]


async def test_telegram_brute_force_rotates_code(ctx):
    tg = ctx.telegram
    code = tg.pairing_code()
    for _ in range(5):
        upd, _m = _update(99)
        await tg.cmd_start(upd, SimpleNamespace(args=["999999" if code != "999999" else "888888"]))
    assert ctx.storage.kv_get("telegram_pair_fails") == 0
