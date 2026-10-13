from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import loosen
from xrz.server.api import create_api, create_webhook_app

TOKEN = "test-token"


@pytest.fixture
def client(ctx):
    app = create_api(ctx)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.headers["X-Vincent-Token"] = TOKEN
        yield c


def test_auth_host_and_cors(ctx):
    app = create_api(ctx)
    with TestClient(app, base_url="http://127.0.0.1:8765") as raw:
        assert raw.get("/api/health").json()["ok"] is True  # ohne Token erlaubt
        assert raw.get("/api/state").status_code == 401
        pre = raw.options("/api/state", headers={"Origin": "null", "Access-Control-Request-Method": "GET"})
        assert pre.status_code == 200 and pre.headers.get("access-control-allow-origin") == "null"
    with TestClient(app, base_url="http://evil.example") as evil:
        evil.headers["X-Vincent-Token"] = TOKEN
        assert evil.get("/api/state").status_code == 400


def test_state_shape(client):
    st = client.get("/api/state").json()
    for key in ("mode", "mode_word", "autonomy", "voice", "tradingview", "ai", "telegram", "memory", "skin",
                "proposals", "positions", "analyses", "watchlist", "version", "live_autopilot"):
        assert key in st, key
    assert st["mode"] == "off" and st["skin"]["id"] == "mensch" and st["tradingview"]["connected"] is True


def test_mode_proposal_flow_via_api(client, ctx):
    assert "PAPER" in client.post("/api/mode", json={"mode": "paper"}).json()["text"]
    loosen(ctx)
    res = client.post("/api/scan").json()
    assert res["analyses"]
    props = client.get("/api/state").json()["proposals"]
    assert props and props[0]["status"] == "pending"
    text = client.post(f"/api/proposals/{props[0]['id']}/approve").json()["text"]
    assert "ausgeführt" in text and ctx.tradingview.orders
    assert client.post("/api/mode", json={"mode": "quatsch"}).status_code == 400
    live = client.post("/api/mode", json={"mode": "live"}).json()
    assert live["needs_confirmation"] is False and "gesperrt" in live["text"]
    assert client.post("/api/autonomy", json={"level": "auto"}).status_code == 200
    assert client.post("/api/autonomy", json={"level": "x"}).status_code == 400


def test_chat_speaks_reply(client, ctx):
    reply = client.post("/api/chat", json={"text": "Status?"}).json()["reply"]
    assert reply.startswith("Status:")
    assert client.post("/api/chat", json={"text": "  "}).status_code == 400


def test_analysis_candles_strategies(client):
    r = client.post("/api/analyze", json={"symbol": "BTC"})
    assert r.status_code == 200 and "BTC/USDT" in r.json()["text"]
    c = client.get("/api/candles", params={"symbol": "BTC/USDT", "limit": 50}).json()
    assert len(c["candles"]) == 50
    strategies = client.get("/api/strategies").json()
    assert len(strategies) >= 7 and all("label" in s for s in strategies)


def test_memory_endpoints(client):
    mid = client.post("/api/memory", json={"text": "Ich handle nie vor FED-Terminen.", "category": "regel"}).json()["id"]
    assert client.get("/api/memory", params={"q": "FED"}).json()["items"]
    assert client.patch(f"/api/memory/{mid}", json={"pinned": True}).json()["ok"]
    assert client.get("/api/memory/stats").json()["total"] >= 1
    client.put("/api/memory/profile", json={"text": "Name: Louis"})
    assert client.get("/api/memory/profile").json()["text"] == "Name: Louis"
    assert client.delete(f"/api/memory/{mid}").json()["ok"]
    assert client.delete(f"/api/memory/{mid}").status_code == 404


def test_settings_and_secret_masking(client, ctx):
    r = client.post("/api/settings", json={"settings": {"risk": {"risk_per_trade_pct": 0.75}, "appearance": {"skin": "neon"}},
                                           "secrets": {"anthropic_api_key": "sk-ant-test-1234567890"}})
    assert r.status_code == 200
    assert ctx.cfg.settings.risk.risk_per_trade_pct == 0.75 and ctx.cfg.settings.appearance.skin == "neon"
    s = client.get("/api/settings").json()
    assert s["secrets"]["anthropic_api_key"]["set"] is True and "1234567890" not in str(s)
    assert client.post("/api/settings", json={"settings": {"risk": {"risk_per_trade_pct": 99}}}).status_code == 400


def test_voice_and_tradingview_endpoints(client, ctx):
    assert client.post("/api/voice/say", json={"text": "Hallo"}).json()["ok"]
    assert ctx.voice.said[-1] == "Hallo"
    assert client.post("/api/voice/mute", json={"muted": True}).json()["muted"] is True
    assert client.get("/api/voice/voices").json()["voices"]
    st = client.post("/api/tradingview/check", json={}).json()
    assert st["connected"] is True and st["account"]["kind"] == "paper"
    r = client.post("/api/tradingview/test-order", json={"symbol": "BTC", "side": "buy", "qty": 0.01}).json()
    assert r["ok"] and r["stage"] == "dry_run" and ctx.tradingview.orders == []


def test_websocket_sends_state_and_events(client, ctx):
    with client.websocket_connect(f"ws://127.0.0.1:8765/ws?token={TOKEN}") as ws:
        first = ws.receive_json()
        assert first["type"] == "state" and "mode" in first["data"]
        ws.send_text('{"type":"ping"}')
        assert ws.receive_json()["type"] == "pong"


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1:8765/ws?token=falsch") as ws:
            ws.receive_json()


def test_webhook_app(ctx):
    ctx.cfg.secrets.set("tradingview_passphrase", "pw")
    with TestClient(create_webhook_app(ctx)) as c:
        assert c.post("/webhook/tradingview", content="kein json").status_code == 400
        assert c.post("/webhook/tradingview", json={"passphrase": "nein", "symbol": "BTCUSDT", "action": "buy"}).status_code == 403
        r = c.post("/webhook/tradingview", json={"passphrase": "pw", "symbol": "BTCUSDT", "action": "buy"})
        assert r.status_code == 200 and r.json()["ok"]
        assert c.get("/api/state").status_code == 404


# --------------------------------------------------------------- Telegram
class _Msg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


def _update(chat_id):
    msg = _Msg()
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=msg), msg


async def test_telegram_pairing_guard_and_proposals(ctx):
    tg = ctx.telegram
    code = tg.pairing_code()
    upd, msg = _update(42)
    await tg.cmd_start(upd, SimpleNamespace(args=["000000" if code != "000000" else "111111"]))
    assert 42 not in ctx.cfg.settings.telegram.allowed_chat_ids and "Falsch" in msg.sent[-1]
    guarded = tg._guard(tg.cmd_status)
    await guarded(upd, SimpleNamespace(args=[]))
    assert "nicht gekoppelt" in msg.sent[-1]
    await tg.cmd_start(upd, SimpleNamespace(args=[code]))
    assert 42 in ctx.cfg.settings.telegram.allowed_chat_ids
    await guarded(upd, SimpleNamespace(args=[]))
    assert "Vincent" in msg.sent[-1]
    await tg._guard(tg.cmd_yes)(upd, SimpleNamespace(args=[]))
    assert "keinen offenen Vorschlag" in msg.sent[-1]
    await ctx.engine.set_mode("paper")
    loosen(ctx)
    await ctx.engine.scan_once()
    await tg._guard(tg.cmd_yes)(upd, SimpleNamespace(args=[]))
    assert "ausgeführt" in msg.sent[-1]


async def test_telegram_brute_force_rotates_code(ctx):
    tg = ctx.telegram
    code = tg.pairing_code()
    for _ in range(5):
        upd, _m = _update(99)
        await tg.cmd_start(upd, SimpleNamespace(args=["999999" if code != "999999" else "888888"]))
    assert ctx.storage.kv_get("telegram_pair_fails") == 0
