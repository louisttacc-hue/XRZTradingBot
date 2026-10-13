"""Lokales Dashboard (nur auf diesem Mac erreichbar) + TradingView-Webhook-Server."""
from __future__ import annotations

import base64
import contextlib
import hmac
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..config import SECRET_KEYS, TELEGRAM_EDITABLE
from ..market.data import normalize_symbol

log = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")


class QuietServer(uvicorn.Server):
    """uvicorn ohne eigene Signal-Handler (die App beendet alles gemeinsam)."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o)))


def create_dashboard(ctx: Any) -> FastAPI:
    """ctx: App-Kontext mit cfg, storage, engine, brain, kb, telegram, mac, market."""
    app = FastAPI(title="XRZ Trading Bot", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    token = ctx.cfg.secrets.get("internal_web_token")

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            given = request.headers.get("x-xrz-token", "")
            if not hmac.compare_digest(given, token):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        html = (STATIC / "index.html").read_text("utf-8")
        return html.replace("__XRZ_TOKEN__", token).replace("__XRZ_VERSION__", __version__)

    # ------------------------------------------------------------- Status
    @app.get("/api/status")
    async def status() -> Any:
        st = await ctx.engine.status()
        st["ai"] = await ctx.brain.router.health()
        st["telegram"] = {
            "running": ctx.telegram.running, "error": ctx.telegram.error, "username": ctx.telegram.username,
            "paired": ctx.cfg.settings.telegram.allowed_chat_ids,
            "pair_code": ctx.telegram.pairing_code(),
        }
        st["knowledge_count"] = ctx.kb.count()
        st["version"] = __version__
        st["secrets_backend"] = ctx.cfg.secrets.backend
        st["live_unlocked"] = ctx.cfg.settings.live_trading_unlocked
        st["mac"] = ctx.mac.info() if ctx.mac else {}
        st["webhook"] = {
            "enabled": ctx.cfg.settings.web.webhook_enabled, "port": ctx.cfg.settings.web.webhook_port,
            "passphrase_set": ctx.cfg.secrets.has("tradingview_passphrase"),
        }
        st["data_dir"] = str(ctx.cfg.data_dir)
        return _jsonable(st)

    @app.get("/api/analyses")
    async def analyses() -> Any:
        return [
            {"symbol": a.symbol, "score": a.score, "direction": a.direction, "direction_word": a.direction_word,
             "price": a.price, "regime": a.regime.label, "rsi": a.indicators.get("rsi"),
             "change_24h": a.indicators.get("change_24h_pct"), "has_edge": a.has_edge, "ts": a.ts}
            for a in ctx.engine.last_analyses.values()
        ]

    @app.post("/api/analyze")
    async def analyze(request: Request) -> Any:
        b = await body(request)
        symbol = normalize_symbol(b.get("symbol", ""), ctx.cfg.settings.trading.watchlist)
        if not symbol:
            raise HTTPException(400, "symbol fehlt")
        a = await ctx.engine.analyzer.analyze(symbol, b.get("timeframe") or None)
        return {"text": a.to_text(), "data": _jsonable(a.to_dict())}

    @app.post("/api/deep")
    async def deep(request: Request) -> Any:
        b = await body(request)
        tool = next(t for t in ctx.brain.tools if t.name == "deep_analysis")
        return {"text": await tool.handler({"symbol": b.get("symbol", "")})}

    @app.post("/api/scan")
    async def scan() -> Any:
        res = await ctx.engine.scan_once()
        return {"text": "\n\n".join(a.to_text(compact=True) for a in res)}

    @app.get("/api/candles")
    async def candles(symbol: str, timeframe: str | None = None, limit: int = 300) -> Any:
        sym = normalize_symbol(symbol, ctx.cfg.settings.trading.watchlist)
        tf = timeframe or ctx.cfg.settings.trading.timeframe
        df = await ctx.market.ohlcv(sym, tf, min(limit, 1000), closed_only=False)
        rows = [
            {"time": int(ts.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
            for ts, r in df.iterrows()
        ]
        trades = [t for t in ctx.storage.closed_trades(limit=200) if t.symbol == sym] + [
            t for t in ctx.storage.open_trades() if t.symbol == sym
        ]
        markers = []
        for t in trades:
            markers.append({"time": int(t.entry_time), "side": t.side, "kind": "entry", "price": t.entry_price})
            if t.exit_time:
                markers.append({"time": int(t.exit_time), "side": t.side, "kind": "exit", "price": t.exit_price, "pnl": t.pnl})
        return {"symbol": sym, "timeframe": tf, "candles": rows, "markers": markers}

    @app.get("/api/trades")
    async def trades(limit: int = 100) -> Any:
        return _jsonable([t.__dict__ for t in ctx.storage.closed_trades(limit=limit)])

    @app.get("/api/equity")
    async def equity(mode: str = "paper") -> Any:
        return ctx.storage.equity_curve(mode)

    @app.get("/api/events")
    async def events(limit: int = 150) -> Any:
        return ctx.storage.recent_events(limit)

    # ---------------------------------------------------------- Steuerung
    @app.post("/api/mode")
    async def mode(request: Request) -> Any:
        m = (await body(request)).get("mode", "")
        if m in ("paper", "off"):
            return {"text": await ctx.engine.set_mode(m)}
        if m == "pause":
            return {"text": await ctx.engine.pause()}
        if m == "resume":
            return {"text": await ctx.engine.resume()}
        raise HTTPException(400, "Unbekannter Modus")

    @app.post("/api/live/request")
    async def live_request() -> Any:
        return {"text": await ctx.engine.request_live()}

    @app.post("/api/live/confirm")
    async def live_confirm(request: Request) -> Any:
        return {"text": await ctx.engine.confirm_live(str((await body(request)).get("code", "")))}

    @app.post("/api/panic")
    async def panic() -> Any:
        return {"text": await ctx.engine.panic()}

    @app.post("/api/close")
    async def close(request: Request) -> Any:
        return {"text": await ctx.engine.close_symbol((await body(request)).get("symbol", ""), "Manuell im Dashboard")}

    @app.post("/api/paper/reset")
    async def paper_reset(request: Request) -> Any:
        if ctx.storage.open_trades("paper"):
            raise HTTPException(400, "Erst alle Paper-Positionen schließen")
        amount = (await body(request)).get("amount")
        bal = ctx.engine.paper.reset(float(amount) if amount else None)
        return {"text": f"Paper-Konto zurückgesetzt auf {bal:,.2f}"}

    @app.post("/api/chart/open")
    async def chart_open(request: Request) -> Any:
        sym = normalize_symbol((await body(request)).get("symbol", ""), ctx.cfg.settings.trading.watchlist)
        if ctx.mac is None:
            raise HTTPException(400, "Mac-Steuerung nicht verfügbar")
        return {"text": await ctx.mac.open_tradingview(sym)}

    # ------------------------------------------------------------- Chat
    @app.post("/api/chat")
    async def chat(request: Request) -> Any:
        msg = str((await body(request)).get("message", "")).strip()
        if not msg:
            raise HTTPException(400, "Nachricht fehlt")
        return {"reply": await ctx.brain.chat("web", msg)}

    @app.get("/api/chat/history")
    async def chat_history() -> Any:
        return ctx.storage.chat_history("web", 50)

    @app.post("/api/chat/clear")
    async def chat_clear() -> Any:
        ctx.storage.clear_chat("web")
        return {"ok": True}

    # ----------------------------------------------------------- Wissen
    @app.get("/api/knowledge")
    async def knowledge(q: str = "", limit: int = 50, offset: int = 0) -> Any:
        if q:
            return {"items": await ctx.kb.search(q, limit), "count": ctx.kb.count()}
        return {"items": ctx.kb.list(limit, offset), "count": ctx.kb.count()}

    @app.post("/api/knowledge")
    async def knowledge_add(request: Request) -> Any:
        b = await body(request)
        n = await ctx.kb.add(str(b.get("text", "")), source="dashboard", topic=b.get("topic") or "notiz", importance=1.5)
        return {"added": n}

    @app.post("/api/knowledge/url")
    async def knowledge_url(request: Request) -> Any:
        return {"added": await ctx.kb.ingest_url(str((await body(request)).get("url", "")))}

    @app.post("/api/knowledge/upload")
    async def knowledge_upload(request: Request) -> Any:
        b = await body(request)
        data = base64.b64decode(b.get("data_b64", ""))
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "Datei zu groß")
        return {"added": await ctx.kb.ingest_bytes(data, b.get("filename", "datei.txt"), source="dashboard")}

    @app.delete("/api/knowledge/{item_id}")
    async def knowledge_delete(item_id: int) -> Any:
        return {"deleted": ctx.kb.delete(item_id)}

    # ------------------------------------------------------ Einstellungen
    @app.get("/api/settings")
    async def get_settings() -> Any:
        return {
            "settings": ctx.cfg.settings.model_dump(mode="json"),
            "secrets": ctx.cfg.secrets.masked(),
            "telegram_editable": sorted(TELEGRAM_EDITABLE),
        }

    @app.post("/api/settings")
    async def save_settings(request: Request) -> Any:
        b = await body(request)
        old = ctx.cfg.settings.model_copy(deep=True)
        changed_secrets = []
        for key, val in (b.get("secrets") or {}).items():
            if key in SECRET_KEYS and isinstance(val, str) and val != "":
                ctx.cfg.secrets.set(key, "" if val == "__DELETE__" else val)
                changed_secrets.append(key)
        if b.get("settings"):
            try:
                ctx.cfg.update(b["settings"])
            except Exception as exc:
                raise HTTPException(400, f"Ungültige Einstellungen: {exc}")
        new = ctx.cfg.settings
        notes = []
        if "telegram_token" in changed_secrets or old.telegram.enabled != new.telegram.enabled:
            await ctx.telegram.restart()
            notes.append("Telegram neu gestartet" + (f" ({ctx.telegram.error})" if ctx.telegram.error else ""))
        if old.exchange.name != new.exchange.name or old.exchange.data_source != new.exchange.data_source:
            ctx.market.reconfigure(new.exchange.name, new.exchange.data_source)
            notes.append("Datenquelle gewechselt – Tiefenanalyse läuft neu")
        if old.trading.timeframe != new.trading.timeframe:
            notes.append("Zeitrahmen geändert – Tiefenanalyse läuft beim nächsten Scan")
        if any(k in changed_secrets for k in ("exchange_api_key", "exchange_secret", "exchange_password")):
            ctx.engine.live = None
            notes.append("Börsen-Keys aktualisiert")
        return {"ok": True, "notes": notes}

    @app.post("/api/telegram/test")
    async def telegram_test() -> Any:
        if not ctx.telegram.running:
            raise HTTPException(400, ctx.telegram.error or "Telegram läuft nicht")
        if not ctx.cfg.settings.telegram.allowed_chat_ids:
            raise HTTPException(400, "Noch kein Chat gekoppelt")
        await ctx.telegram.send("👋 Test vom XRZ Dashboard – alles funktioniert!")
        return {"ok": True}

    @app.post("/api/ai/test")
    async def ai_test() -> Any:
        try:
            t0 = time.time()
            reply = await ctx.brain.router.complete("Antworte kurz auf Deutsch.", "Sag in einem Satz hallo und nenne dein Modell.")
            return {"ok": True, "reply": reply, "seconds": round(time.time() - t0, 1), "provider": ctx.brain.router.last_used}
        except Exception as exc:
            return {"ok": False, "reply": str(exc)}

    return app


def create_webhook_app(ctx: Any) -> FastAPI:
    """Öffentlich erreichbarer Teil (z. B. über cloudflared/ngrok) – NUR für TradingView-Alerts."""
    app = FastAPI(title="XRZ Webhook", docs_url=None, redoc_url=None, openapi_url=None)
    hits: deque[float] = deque(maxlen=60)

    @app.post("/webhook/tradingview")
    async def tradingview(request: Request) -> Any:
        now = time.time()
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= 30:
            raise HTTPException(429, "Zu viele Anfragen")
        hits.append(now)
        raw = (await request.body())[:10_000].decode("utf-8", "ignore")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Body muss JSON sein, z. B. {\"passphrase\":\"…\",\"symbol\":\"BTCUSDT\",\"action\":\"buy\"}")
        if "passphrase" not in payload and request.query_params.get("key"):
            payload["passphrase"] = request.query_params["key"]
        result = await ctx.engine.handle_tradingview(payload)
        if not result.get("ok"):
            raise HTTPException(403 if "Passphrase" in str(result.get("error")) else 400, result.get("error"))
        return result

    @app.get("/webhook/health")
    async def health() -> Any:
        return {"ok": True}

    return app

