"""Startpunkt des Vincent-Backends: verbindet Engine, KI, Gedächtnis, Stimme, TradingView,
Mac-Steuerung, Telegram und die lokale API. Die Mac-App startet es automatisch mit
`python -m xrz serve --port <port> --parent-pid <pid>` (Token in VINCENT_TOKEN)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import secrets
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__
from .ai.brain import Brain
from .ai.llm import LLMRouter
from .config import ConfigManager
from .events import EventBus
from .interfaces.telegram_bot import TelegramBot
from .mac import MacControl
from .market.data import MarketData, normalize_symbol
from .market.sentiment import Sentiment
from .memory import MemoryManager
from .storage import Storage
from .tradingview import TradingViewController
from .trading.engine import TradingEngine
from .voice import VoiceAssistant

log = logging.getLogger("xrz")


@dataclass
class AppContext:
    cfg: ConfigManager
    storage: Storage
    market: MarketData
    events: EventBus
    tradingview: TradingViewController
    engine: TradingEngine
    router: LLMRouter
    memory: MemoryManager
    brain: Brain
    mac: MacControl
    voice: VoiceAssistant
    telegram: TelegramBot
    token: str


def setup_logging(data_dir: Path, verbose: bool = False) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(logs / "vincent.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.handlers = [fh, ch]
    for noisy in ("httpx", "httpcore", "telegram", "ccxt", "urllib3", "anthropic", "websockets", "uvicorn.error",
                  "faster_whisper", "numba", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:180] if text else ""


def build(data_dir: Path | None = None, demo: bool = False, token: str | None = None) -> AppContext:
    cfg = ConfigManager(data_dir)
    if demo:
        cfg.settings.exchange.data_source = "demo"
    storage = Storage(cfg.data_dir / "vincent.db")
    market = MarketData(cfg.settings.exchange.name, cfg.settings.exchange.data_source)
    events = EventBus(storage)
    tradingview = TradingViewController(lambda: cfg.settings, cfg.data_dir)
    engine = TradingEngine(cfg, storage, market, events, tradingview)
    router = LLMRouter(lambda: cfg.settings, cfg.secrets)
    memory = MemoryManager(storage, router, lambda: cfg.settings)
    mac = MacControl(lambda: cfg.settings, cfg.data_dir)
    brain = Brain(cfg, storage, router, memory, engine, mac=mac, tradingview=tradingview,
                  sentiment=Sentiment(), events=events)
    engine.brain = brain
    engine.memory = memory
    voice = VoiceAssistant(lambda: cfg.settings, cfg.secrets, cfg.data_dir, emit=events.emit,
                           on_utterance=brain.handle_voice)
    brain.voice = voice
    telegram = TelegramBot(cfg, storage, engine, brain, memory, mac, tradingview)
    events.subscribe(telegram.on_event)

    async def side_effects(type_: str, data: dict[str, Any]) -> None:
        """Vorschläge ansagen, wichtige Meldungen als Mac-Mitteilung."""
        if type_ != "notify":
            return
        kind, text = data.get("kind"), data.get("text", "")
        if kind in ("trade", "warning", "error") and cfg.settings.mac.notifications:
            await mac.notify(APP_NAME, _first_line(text))
        if not cfg.settings.voice.enabled:
            return
        if kind == "proposal" and data.get("spoken"):
            await voice.say(data["spoken"])
        elif kind == "trade":
            await voice.say(_first_line(text))

    events.subscribe(side_effects)
    tok = token or os.environ.get("VINCENT_TOKEN") or secrets.token_hex(24)
    return AppContext(cfg, storage, market, events, tradingview, engine, router, memory, brain, mac, voice, telegram, tok)


def _parent_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def serve(ctx: AppContext, port: int, host: str = "127.0.0.1", parent_pid: int | None = None) -> None:
    import uvicorn

    from .server import QuietServer, create_api, create_webhook_app

    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("Aus Sicherheitsgründen läuft die Vincent-API nur auf 127.0.0.1.")
    ctx.events.bind_loop(asyncio.get_running_loop())
    s = ctx.cfg.settings
    servers = [QuietServer(uvicorn.Config(create_api(ctx), host=host, port=port, log_level="warning", access_log=False))]
    if s.web.webhook_enabled:
        if not ctx.cfg.secrets.has("tradingview_passphrase"):
            log.warning("Webhook aktiv, aber keine TradingView-Passphrase gesetzt – Alerts werden abgelehnt")
        servers.append(QuietServer(uvicorn.Config(create_webhook_app(ctx), host="127.0.0.1", port=s.web.webhook_port,
                                                  log_level="warning", access_log=False)))
    server_tasks = [asyncio.create_task(srv.serve()) for srv in servers]

    try:
        seeded = await ctx.memory.seed_if_empty()
        if seeded:
            log.info("Grundwissen geladen (%d Einträge)", seeded)
    except Exception as exc:
        log.warning("Gedächtnis-Start: %s", exc)
    await ctx.engine.start()
    await ctx.telegram.start()
    await ctx.voice.start()

    print(f"{APP_NAME} v{__version__} – API auf http://{host}:{port}  "
          f"(KI: {ctx.router.describe()}, Daten: {s.exchange.data_source})", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows
            pass

    async def watch_parent() -> None:
        while parent_pid and not stop.is_set():
            if not _parent_alive(parent_pid) or os.getppid() == 1:
                log.info("App beendet – Backend fährt herunter")
                stop.set()
            await asyncio.sleep(2)

    watcher = asyncio.create_task(watch_parent())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([stop_task, *server_tasks], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is not stop_task and t.exception():
                log.error("Server beendet: %s", t.exception())
    finally:
        for srv in servers:
            srv.should_exit = True
        watcher.cancel()
        stop_task.cancel()
        for coro in (ctx.voice.stop(), ctx.telegram.stop(), ctx.engine.stop(), ctx.tradingview.disconnect()):
            try:
                await asyncio.wait_for(coro, timeout=10)
            except Exception as exc:
                log.info("Beim Herunterfahren: %s", exc)
        await asyncio.gather(*server_tasks, return_exceptions=True)
        ctx.storage.close()


async def _cli_analyze(ctx: AppContext, symbol: str, timeframe: str | None) -> None:
    sym = normalize_symbol(symbol, ctx.cfg.settings.trading.watchlist)
    a = await ctx.engine.analyzer.analyze(sym, timeframe)
    print(a.to_text())


async def _cli_backtest(ctx: AppContext, symbol: str) -> None:
    from .server.api import _backtest_text

    sym = normalize_symbol(symbol, ctx.cfg.settings.trading.watchlist)
    print(_backtest_text(sym, await ctx.engine.analyzer.deep_analysis(sym)))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="xrz", description=f"{APP_NAME} – Backend")
    p.add_argument("--demo", action="store_true", help="künstliche Kursdaten (ohne Internet/Börse)")
    p.add_argument("--data-dir", type=Path, help="eigener Datenordner")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("serve", help="Backend für die App starten (Standard)")
    sp.add_argument("--port", type=int, default=0, help="Port (Standard aus den Einstellungen)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--parent-pid", type=int, default=0, help="beendet sich, wenn dieser Prozess endet")
    sp.add_argument("--demo", action="store_true", dest="serve_demo", help=argparse.SUPPRESS)
    a = sub.add_parser("analyze", help="Ein Symbol analysieren und beenden")
    a.add_argument("symbol")
    a.add_argument("timeframe", nargs="?")
    b = sub.add_parser("backtest", help="Walk-Forward-Backtest für ein Symbol")
    b.add_argument("symbol")
    sub.add_parser("where", help="Zeigt den Datenordner")
    sub.add_parser("prefetch", help="Lädt Sprach- und Stimmmodelle vorab herunter")
    args = p.parse_args(argv)

    demo = args.demo or bool(getattr(args, "serve_demo", False))
    ctx = build(args.data_dir, demo=demo)
    setup_logging(ctx.cfg.data_dir, args.verbose)
    if args.cmd == "where":
        print(ctx.cfg.data_dir)
        return
    if args.cmd == "prefetch":
        from .voice import prefetch

        for line in prefetch(ctx.cfg.settings, ctx.cfg.data_dir, progress=lambda m: print(m, flush=True)):
            print(line, flush=True)
        return
    if args.cmd == "analyze":
        asyncio.run(_cli_analyze(ctx, args.symbol, args.timeframe))
        return
    if args.cmd == "backtest":
        asyncio.run(_cli_backtest(ctx, args.symbol))
        return
    port = getattr(args, "port", 0) or ctx.cfg.settings.web.port
    host = getattr(args, "host", "127.0.0.1")
    parent = getattr(args, "parent_pid", 0) or None
    if not os.environ.get("VINCENT_TOKEN"):
        print(f"Entwickler-Modus: Token {ctx.token}\n"
              f"Overlay im Browser: app/renderer/overlay/index.html?port={port}&token={ctx.token}", flush=True)
    try:
        asyncio.run(serve(ctx, port, host, parent))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
