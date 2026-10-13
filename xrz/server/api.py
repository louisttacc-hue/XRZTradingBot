"""Lokale Backend-API für die Vincent-App (REST + WebSocket) und der optionale
TradingView-Webhook. Erreichbar nur über 127.0.0.1 und nur mit dem Sitzungs-Token."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import time
from collections import deque
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..config import SECRET_KEYS, TELEGRAM_EDITABLE
from ..market.data import normalize_symbol
from ..strategies import STRATEGIES

log = logging.getLogger(__name__)


class QuietServer(uvicorn.Server):
    """uvicorn ohne eigene Signal-Handler (die App beendet alles gemeinsam)."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: o.to_dict() if hasattr(o, "to_dict")
                                 else o.__dict__ if hasattr(o, "__dict__") else str(o)))


class StateHub:
    """Verteilt Ereignisse an alle verbundenen App-Fenster und drosselt Zustands-Updates."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.clients: set[WebSocket] = set()
        self._dirty = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._state_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def on_event(self, type_: str, data: dict[str, Any]) -> None:
        if type_ == "state.dirty":
            self._dirty.set()
            return
        if type_ in ("proposal", "notify", "voice.state", "analysis", "tradingview"):
            self._dirty.set()
        await self.broadcast({"type": type_, "data": data, "ts": time.time()})

    async def broadcast(self, msg: dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(_jsonable(msg), ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def _state_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
            self._dirty.clear()
            if self.clients:
                try:
                    await self.broadcast({"type": "state", "data": await build_state(self.ctx), "ts": time.time()})
                except Exception as exc:  # pragma: no cover - defensiv
                    log.warning("Zustand nicht sendbar: %s", exc)
            await asyncio.sleep(0.5)  # max. 2 Zustands-Updates pro Sekunde


async def build_state(ctx: Any) -> dict[str, Any]:
    s = ctx.cfg.settings
    st = ctx.engine.state_snapshot_trading()
    st["version"] = __version__
    st["voice"] = ctx.voice.status() if ctx.voice is not None else {"state": "off", "error": "Stimme nicht geladen"}
    st["ai"] = {"chain": ctx.router.describe(), "last_used": ctx.router.last_used, "last_error": ctx.router.last_error}
    st["telegram"] = {
        "running": ctx.telegram.running, "username": ctx.telegram.username, "error": ctx.telegram.error,
        "paired": len(s.telegram.allowed_chat_ids), "pair_code": ctx.telegram.pairing_code(),
    }
    try:
        mstats = ctx.memory.stats()
        st["memory"] = {"total": mstats.get("total", 0), "profile_chars": mstats.get("profile_chars", 0)}
    except Exception:
        st["memory"] = {"total": 0, "profile_chars": 0}
    a = s.appearance
    st["skin"] = {"id": a.skin, "accent": a.accent, "size": a.size, "edge": a.edge,
                  "custom_image": a.custom_image or None, "show_subtitles": a.show_subtitles}
    st["live_unlocked"] = s.live_trading_unlocked
    st["live_autopilot"] = bool(s.live_trading_unlocked and s.live_autopilot_unlocked)
    return st


def create_api(ctx: Any) -> FastAPI:
    hub = StateHub(ctx)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        ctx.events.subscribe(hub.on_event)
        hub.start()
        try:
            yield
        finally:
            ctx.events.unsubscribe(hub.on_event)
            await hub.stop()

    app = FastAPI(title="XRZ Vincent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.hub = hub

    def token() -> str:
        return ctx.token

    @app.middleware("http")
    async def auth(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health" and request.method != "OPTIONS":
            if not hmac.compare_digest(request.headers.get("x-vincent-token", ""), token()):
                return JSONResponse({"detail": "Nicht autorisiert"}, status_code=401)
        response = await call_next(request)
        # Die App-Fenster laden von file:// – nur dafür CORS erlauben
        origin = request.headers.get("origin", "")
        if origin in ("null", "file://"):
            response.headers["Access-Control-Allow-Origin"] = origin
        return response

    @app.options("/{rest:path}")
    async def preflight(rest: str, request: Request) -> Any:
        origin = request.headers.get("origin", "")
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Vincent-Token",
            "Access-Control-Max-Age": "600",
        }
        if origin in ("null", "file://"):
            headers["Access-Control-Allow-Origin"] = origin
        return JSONResponse({}, headers=headers)

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def need(value: Any, msg: str) -> Any:
        if not value:
            raise HTTPException(400, msg)
        return value

    # ------------------------------------------------------------- Basis
    @app.get("/api/health")
    async def health() -> Any:
        return {"ok": True, "version": __version__}

    @app.get("/api/state")
    async def state() -> Any:
        return _jsonable(await build_state(ctx))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not hmac.compare_digest(ws.query_params.get("token", ""), token()):
            await ws.close(code=4401)
            return
        await ws.accept()
        hub.clients.add(ws)
        try:
            await ws.send_text(json.dumps(_jsonable({"type": "state", "data": await build_state(ctx), "ts": time.time()}),
                                          ensure_ascii=False))
            while True:
                msg = await ws.receive_text()
                if msg and '"ping"' in msg:
                    await ws.send_text(json.dumps({"type": "pong", "data": {}, "ts": time.time()}))
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.debug("WebSocket beendet: %s", exc)
        finally:
            hub.clients.discard(ws)

    # ------------------------------------------------------------- Chat & Stimme
    @app.post("/api/chat")
    async def chat(request: Request) -> Any:
        text = str(need((await body(request)).get("text", "").strip(), "Nachricht fehlt"))
        reply = await ctx.brain.chat("app", text, source="text")
        if ctx.voice is not None and ctx.cfg.settings.voice.speak_replies:
            asyncio.create_task(ctx.voice.say(reply))
        return {"reply": reply}

    def voice_or_400():
        if ctx.voice is None:
            raise HTTPException(400, "Die Stimme ist nicht verfügbar")
        return ctx.voice

    @app.post("/api/voice/listen")
    async def voice_listen() -> Any:
        await voice_or_400().listen_now()
        return {"ok": True}

    @app.post("/api/voice/stop")
    async def voice_stop() -> Any:
        await voice_or_400().stop_all()
        return {"ok": True}

    @app.post("/api/voice/mute")
    async def voice_mute(request: Request) -> Any:
        muted = bool((await body(request)).get("muted", True))
        voice_or_400().set_muted(muted)
        ctx.events.emit("state.dirty", {})
        return {"ok": True, "muted": muted}

    @app.post("/api/voice/say")
    async def voice_say(request: Request) -> Any:
        text = str(need((await body(request)).get("text", "").strip(), "Text fehlt"))
        await voice_or_400().say(text[:500], interrupt=True)
        return {"ok": True}

    @app.get("/api/voice/voices")
    async def voice_voices() -> Any:
        return await voice_or_400().list_voices()

    # ------------------------------------------------------------- Trading
    @app.post("/api/mode")
    async def mode(request: Request) -> Any:
        m = (await body(request)).get("mode", "")
        if m in ("off", "watch", "paper"):
            return {"text": await ctx.engine.set_mode(m)}
        if m == "live":
            return await ctx.engine.request_live()
        if m == "pause":
            return {"text": await ctx.engine.pause()}
        if m == "resume":
            return {"text": await ctx.engine.resume()}
        raise HTTPException(400, "Unbekannter Modus")

    @app.post("/api/mode/confirm")
    async def mode_confirm(request: Request) -> Any:
        return {"text": await ctx.engine.confirm_live(str((await body(request)).get("code", "")))}

    @app.post("/api/autonomy")
    async def autonomy(request: Request) -> Any:
        level = (await body(request)).get("level", "")
        if level not in ("ask", "auto"):
            raise HTTPException(400, "level muss ask oder auto sein")
        return {"text": await ctx.engine.set_autonomy(level)}

    @app.post("/api/proposals/{pid}/approve")
    async def approve(pid: str) -> Any:
        return {"text": await ctx.engine.approve(pid, source="app")}

    @app.post("/api/proposals/{pid}/reject")
    async def reject(pid: str) -> Any:
        return {"text": await ctx.engine.reject(pid, source="app")}

    @app.post("/api/scan")
    async def scan() -> Any:
        res = await ctx.engine.scan_once(list(ctx.cfg.settings.trading.watchlist))
        return {"analyses": [ctx.engine.analysis_summary(a) for a in res]}

    @app.get("/api/analyses")
    async def analyses() -> Any:
        return [ctx.engine.analysis_summary(a) for a in ctx.engine.last_analyses.values()]

    @app.post("/api/analyze")
    async def analyze(request: Request) -> Any:
        b = await body(request)
        symbol = normalize_symbol(str(need(b.get("symbol", ""), "Symbol fehlt")), ctx.cfg.settings.trading.watchlist)
        try:
            a = await ctx.engine.analyzer.analyze(symbol, b.get("timeframe") or None)
        except Exception as exc:
            raise HTTPException(400, f"Analyse fehlgeschlagen: {exc}")
        ctx.engine.last_analyses[symbol] = a
        return {"text": a.to_text(), "data": _jsonable(a.to_dict())}

    @app.get("/api/candles")
    async def candles(symbol: str, timeframe: str | None = None, limit: int = 300) -> Any:
        sym = normalize_symbol(symbol, ctx.cfg.settings.trading.watchlist)
        tf = timeframe or ctx.cfg.settings.trading.timeframe
        try:
            df = await ctx.market.ohlcv(sym, tf, max(10, min(limit, 1000)), closed_only=False)
        except Exception as exc:
            raise HTTPException(400, f"Keine Kursdaten: {exc}")
        rows = [{"time": int(ts.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                for ts, r in df.iterrows()]
        trades = [t for t in ctx.storage.closed_trades(limit=200) if t.symbol == sym] + [
            t for t in ctx.storage.open_trades() if t.symbol == sym]
        markers = []
        for t in trades:
            markers.append({"time": int(t.entry_time), "side": t.side, "kind": "entry", "price": t.entry_price})
            if t.exit_time:
                markers.append({"time": int(t.exit_time), "side": t.side, "kind": "exit", "price": t.exit_price, "pnl": t.pnl})
        return {"symbol": sym, "timeframe": tf, "candles": rows, "markers": markers}

    @app.post("/api/backtest")
    async def backtest(request: Request) -> Any:
        symbol = normalize_symbol(str(need((await body(request)).get("symbol", ""), "Symbol fehlt")),
                                  ctx.cfg.settings.trading.watchlist)
        try:
            res = await ctx.engine.analyzer.deep_analysis(symbol)
        except Exception as exc:
            raise HTTPException(400, f"Backtest fehlgeschlagen: {exc}")
        reports = sorted(res["reports"].values(), key=lambda r: -r.weight)
        return {
            "text": _backtest_text(symbol, res),
            "ensemble": res["ensemble"],
            "reports": [{"name": r.name, "label": STRATEGIES[r.name].label if r.name in STRATEGIES else r.name,
                         "weight": r.weight, "params": r.params, "oos": r.oos_metrics, "full": r.full_metrics}
                        for r in reports],
        }

    @app.get("/api/strategies")
    async def strategies() -> Any:
        return [{"name": s.name, "label": s.label, "description": s.description,
                 "category": getattr(s, "category", ""), "good_in": s.good_in, "params": s.defaults}
                for s in STRATEGIES.values()]

    @app.get("/api/events")
    async def events(limit: int = 150) -> Any:
        return ctx.storage.recent_events(max(1, min(limit, 1000)))

    @app.post("/api/panic")
    async def panic() -> Any:
        return {"text": await ctx.engine.panic()}

    # ------------------------------------------------------------- Gedächtnis
    @app.get("/api/memory")
    async def memory_list(q: str = "", category: str | None = None, limit: int = 50, offset: int = 0) -> Any:
        limit = max(1, min(limit, 500))
        if q:
            items = await ctx.memory.search(q, k=limit, category=category or None)
        else:
            items = ctx.memory.list(limit=limit, offset=max(0, offset), category=category or None)
        return {"items": _jsonable(items), "count": ctx.memory.count()}

    @app.get("/api/memory/stats")
    async def memory_stats() -> Any:
        return _jsonable(ctx.memory.stats())

    @app.get("/api/memory/profile")
    async def memory_profile() -> Any:
        return {"text": ctx.memory.profile()}

    @app.put("/api/memory/profile")
    async def memory_profile_set(request: Request) -> Any:
        ctx.memory.set_profile(str((await body(request)).get("text", "")))
        return {"ok": True}

    @app.post("/api/memory")
    async def memory_add(request: Request) -> Any:
        b = await body(request)
        text = str(need(b.get("text", "").strip(), "Text fehlt"))
        mid = await ctx.memory.add(text, category=b.get("category") or "notiz", importance=0.85, source="app")
        return {"id": mid}

    @app.patch("/api/memory/{item_id}")
    async def memory_update(item_id: int, request: Request) -> Any:
        b = await body(request)
        ok = ctx.memory.update(item_id, text=b.get("text"), importance=b.get("importance"), pinned=b.get("pinned"))
        if not ok:
            raise HTTPException(404, "Erinnerung nicht gefunden")
        return {"ok": True}

    @app.delete("/api/memory/{item_id}")
    async def memory_delete(item_id: int) -> Any:
        if not ctx.memory.delete(item_id):
            raise HTTPException(404, "Erinnerung nicht gefunden")
        return {"ok": True}

    @app.post("/api/memory/consolidate")
    async def memory_consolidate() -> Any:
        return {"text": await ctx.memory.consolidate()}

    @app.post("/api/memory/url")
    async def memory_url(request: Request) -> Any:
        try:
            n = await ctx.memory.ingest_url(str((await body(request)).get("url", "")))
        except Exception as exc:
            raise HTTPException(400, f"Konnte die Seite nicht lesen: {exc}")
        return {"added": n}

    @app.post("/api/memory/upload")
    async def memory_upload(request: Request) -> Any:
        b = await body(request)
        try:
            data = base64.b64decode(b.get("data_b64", ""), validate=True)
        except Exception:
            raise HTTPException(400, "Ungültige Datei")
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "Datei zu groß (max. 25 MB)")
        try:
            n = await ctx.memory.ingest_bytes(data, str(b.get("filename") or "datei.txt"), source="app")
        except Exception as exc:
            raise HTTPException(400, str(exc))
        return {"added": n}

    # ------------------------------------------------------------- Einstellungen
    @app.get("/api/settings")
    async def get_settings() -> Any:
        return {
            "settings": ctx.cfg.settings.model_dump(mode="json"),
            "secrets": ctx.cfg.secrets.masked(),
            "telegram_editable": sorted(TELEGRAM_EDITABLE),
            "data_dir": str(ctx.cfg.data_dir),
            "secrets_backend": ctx.cfg.secrets.backend,
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
        notes: list[str] = []
        if new.live_autopilot_unlocked and not new.live_trading_unlocked:
            new.live_autopilot_unlocked = False
            ctx.cfg.save()
            notes.append("Der Echtgeld-Autopilot braucht zuerst die Echtgeld-Freischaltung")
        if new.trading.autonomy == "auto" and ctx.engine.mode == "live" and not ctx.engine.live_autopilot:
            new.trading.autonomy = "ask"
            ctx.cfg.save()
            notes.append("Im Echtgeld-Modus bleibt „vorher fragen“ aktiv, solange der Echtgeld-Autopilot gesperrt ist")
        if "telegram_token" in changed_secrets or old.telegram.enabled != new.telegram.enabled:
            await ctx.telegram.restart()
            notes.append("Telegram neu gestartet" + (f" ({ctx.telegram.error})" if ctx.telegram.error else ""))
        if old.exchange.name != new.exchange.name or old.exchange.data_source != new.exchange.data_source:
            ctx.market.reconfigure(new.exchange.name, new.exchange.data_source)
            notes.append("Datenquelle gewechselt – Backtests laufen neu")
        if old.voice != new.voice or any(k in changed_secrets for k in ("elevenlabs_api_key", "openai_api_key")):
            if ctx.voice is not None and hasattr(ctx.voice, "reload"):
                await ctx.voice.reload()
            notes.append("Stimme aktualisiert")
        ctx.events.emit("state.dirty", {})
        return {"ok": True, "notes": notes}

    # ------------------------------------------------------------- TradingView
    @app.post("/api/tradingview/check")
    async def tv_check(request: Request) -> Any:
        b = await body(request)
        allow = bool(b.get("allow_restart")) or ctx.cfg.settings.tradingview.allow_restart_without_asking
        st = await ctx.tradingview.connect(allow_restart=allow)
        await ctx.engine.refresh_tradingview()
        return _jsonable(st)

    @app.post("/api/tradingview/test-order")
    async def tv_test_order(request: Request) -> Any:
        from ..market.data import to_tradingview_symbol
        from ..tradingview.models import AccountKind, OrderRequest

        b = await body(request)
        symbol = normalize_symbol(str(need(b.get("symbol", ""), "Symbol fehlt")), ctx.cfg.settings.trading.watchlist)
        side = b.get("side", "buy")
        if side not in ("buy", "sell"):
            raise HTTPException(400, "side muss buy oder sell sein")
        try:
            qty = float(b.get("qty", 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "Ungültige Menge")
        if qty <= 0:
            raise HTTPException(400, "Menge muss größer 0 sein")
        acc = await ctx.tradingview.read_account()
        expected = AccountKind.LIVE if acc.kind == AccountKind.LIVE else AccountKind.PAPER
        req = OrderRequest(symbol=to_tradingview_symbol(symbol, ctx.cfg.settings.tradingview.chart_exchange_prefix),
                           side=side, qty=qty)
        res = await ctx.tradingview.place_order(req, expected, dry_run=True)
        return _jsonable(res.to_dict())

    return app


def _backtest_text(symbol: str, res: dict[str, Any]) -> str:
    reps = sorted(res["reports"].values(), key=lambda r: -r.weight)
    lines = [f"Walk-Forward-Backtest {symbol} ({res['candles']} Kerzen):"]
    for r in reps:
        m = r.oos_metrics
        label = STRATEGIES[r.name].label if r.name in STRATEGIES else r.name
        lines.append(f"{label}: Gewicht {r.weight * 100:.0f}% | Out-of-Sample {m['total_return_pct']:+.1f}%, "
                     f"{int(m['trades'])} Trades, PF {m['profit_factor']:.2f}, DD {m['max_drawdown_pct']:.1f}%")
    ens = res["ensemble"]
    lines.append(f"Ensemble: {ens['total_return_pct']:+.1f}% vs. Buy&Hold {ens['buy_hold_pct']:+.1f}%, "
                 f"{int(ens['trades'])} Trades, Trefferquote {ens['win_rate_pct']:.0f}%, max. DD {ens['max_drawdown_pct']:.1f}%")
    return "\n".join(lines)


def create_webhook_app(ctx: Any) -> FastAPI:
    """Öffentlich erreichbarer Teil (z. B. über cloudflared) – NUR für TradingView-Alerts."""
    app = FastAPI(title="Vincent Webhook", docs_url=None, redoc_url=None, openapi_url=None)
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
