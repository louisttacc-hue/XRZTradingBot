"""Startpunkt: verbindet Engine, KI, Telegram, Dashboard und Mac-Steuerung."""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__
from .ai.brain import Brain
from .ai.knowledge import KnowledgeBase
from .ai.llm import LLMRouter
from .config import ConfigManager
from .interfaces.telegram_bot import TelegramBot
from .mac.automation import MacAutomation
from .market.data import MarketData, normalize_symbol
from .market.sentiment import Sentiment
from .notify import Notifier
from .storage import Storage
from .trading.engine import TradingEngine

log = logging.getLogger("xrz")


@dataclass
class AppContext:
    cfg: ConfigManager
    storage: Storage
    market: MarketData
    notifier: Notifier
    engine: TradingEngine
    router: LLMRouter
    kb: KnowledgeBase
    brain: Brain
    mac: MacAutomation
    telegram: TelegramBot


def setup_logging(data_dir: Path, verbose: bool = False) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(logs / "xrz.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.handlers = [fh, ch]
    for noisy in ("httpx", "telegram", "ccxt", "urllib3", "apscheduler", "anthropic", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build(data_dir: Path | None = None, demo: bool = False) -> AppContext:
    cfg = ConfigManager(data_dir)
    if demo:
        cfg.settings.exchange.data_source = "demo"
    storage = Storage(cfg.data_dir / "xrz.db")
    market = MarketData(cfg.settings.exchange.name, cfg.settings.exchange.data_source)
    notifier = Notifier(storage)
    engine = TradingEngine(cfg, storage, market, notifier)
    router = LLMRouter(lambda: cfg.settings, cfg.secrets)
    kb = KnowledgeBase(storage, router)
    mac = MacAutomation(lambda: cfg.settings, cfg.data_dir)
    brain = Brain(cfg, storage, router, kb, engine, mac=mac, sentiment=Sentiment())
    engine.brain = brain
    telegram = TelegramBot(cfg, storage, engine, brain, kb, mac)
    notifier.subscribe(telegram.on_notify)

    async def mac_notify(kind: str, text: str, data: dict[str, Any]) -> None:
        if kind in ("trade", "warning", "error", "mode"):
            await mac.notify(APP_NAME, text.splitlines()[0])
            if kind == "trade" and cfg.settings.mac.speak_trades:
                await mac.say(text.splitlines()[0])

    notifier.subscribe(mac_notify)
    return AppContext(cfg, storage, market, notifier, engine, router, kb, brain, mac, telegram)


async def run_bot(ctx: AppContext, open_browser: bool = True) -> None:
    from .web.server import QuietServer, create_dashboard, create_webhook_app
    import uvicorn

    s = ctx.cfg.settings
    seeded = await ctx.kb.seed_if_empty()
    if seeded:
        log.info("Grundwissen geladen (%d Einträge)", seeded)
    await ctx.engine.start()
    await ctx.telegram.start()

    servers: list[QuietServer] = []
    dash = QuietServer(uvicorn.Config(create_dashboard(ctx), host=s.web.host, port=s.web.port, log_level="warning", access_log=False))
    servers.append(dash)
    if s.web.webhook_enabled:
        if not ctx.cfg.secrets.has("tradingview_passphrase"):
            log.warning("Webhook aktiv, aber keine TradingView-Passphrase gesetzt – Alerts werden abgelehnt")
        servers.append(QuietServer(uvicorn.Config(create_webhook_app(ctx), host="127.0.0.1", port=s.web.webhook_port,
                                                  log_level="warning", access_log=False)))
    server_tasks = [asyncio.create_task(srv.serve()) for srv in servers]

    url = f"http://{'127.0.0.1' if s.web.host in ('0.0.0.0', '') else s.web.host}:{s.web.port}"
    banner = [
        "",
        "=" * 64,
        f"  {APP_NAME} v{__version__} läuft",
        f"  Dashboard:  {url}",
        f"  Modus:      {ctx.engine.mode.upper()}  |  Daten: {s.exchange.data_source} ({s.exchange.name})",
        f"  KI:         {ctx.router.describe()}",
        f"  Telegram:   {'@' + ctx.telegram.username if ctx.telegram.running else ctx.telegram.error}",
    ]
    if ctx.telegram.running and not s.telegram.allowed_chat_ids:
        banner.append(f"  Koppeln:    sende  /start {ctx.telegram.pairing_code()}  an deinen Bot")
    if s.web.webhook_enabled:
        banner.append(f"  Webhook:    http://127.0.0.1:{s.web.webhook_port}/webhook/tradingview")
    banner += ["  Beenden:    Ctrl+C", "=" * 64, ""]
    print("\n".join(banner), flush=True)
    if open_browser and s.web.open_browser_on_start:
        asyncio.create_task(_open_later(ctx, url))
    asyncio.create_task(_reembed(ctx))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    try:
        await stop.wait()
    finally:
        print("\nFahre herunter …", flush=True)
        for srv in servers:
            srv.should_exit = True
        await ctx.engine.stop()
        await ctx.telegram.stop()
        await asyncio.gather(*server_tasks, return_exceptions=True)
        ctx.storage.close()


async def _open_later(ctx: AppContext, url: str) -> None:
    await asyncio.sleep(1.5)
    try:
        await ctx.mac.open_dashboard(url)
    except Exception:
        pass


async def _reembed(ctx: AppContext) -> None:
    await asyncio.sleep(20)
    try:
        n = await ctx.kb.reembed_missing()
        if n:
            log.info("Embeddings für %d Wissenseinträge ergänzt", n)
    except Exception as exc:
        log.debug("Re-Embedding: %s", exc)


async def _cli_analyze(ctx: AppContext, symbol: str, timeframe: str | None) -> None:
    sym = normalize_symbol(symbol, ctx.cfg.settings.trading.watchlist)
    a = await ctx.engine.analyzer.analyze(sym, timeframe)
    print(a.to_text())


async def _cli_backtest(ctx: AppContext, symbol: str) -> None:
    tool = next(t for t in ctx.brain.tools if t.name == "deep_analysis")
    print(await tool.handler({"symbol": symbol}))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="xrz", description=f"{APP_NAME} – lokaler KI-Trading-Bot")
    p.add_argument("--demo", action="store_true", help="künstliche Kursdaten (ohne Internet/Börse) verwenden")
    p.add_argument("--data-dir", type=Path, help="eigener Datenordner (Standard: ~/Library/Application Support/XRZTradingBot)")
    p.add_argument("--no-browser", action="store_true", help="Dashboard nicht automatisch öffnen")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Bot starten (Standard)")
    a = sub.add_parser("analyze", help="Ein Symbol analysieren und beenden")
    a.add_argument("symbol")
    a.add_argument("timeframe", nargs="?")
    b = sub.add_parser("backtest", help="Walk-Forward-Backtest für ein Symbol")
    b.add_argument("symbol")
    sub.add_parser("where", help="Zeigt den Datenordner")
    args = p.parse_args(argv)

    ctx = build(args.data_dir, demo=args.demo)
    setup_logging(ctx.cfg.data_dir, args.verbose)
    if args.cmd == "where":
        print(ctx.cfg.data_dir)
        return
    if args.cmd == "analyze":
        asyncio.run(_cli_analyze(ctx, args.symbol, args.timeframe))
        return
    if args.cmd == "backtest":
        asyncio.run(_cli_backtest(ctx, args.symbol))
        return
    try:
        asyncio.run(run_bot(ctx, open_browser=not args.no_browser))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
