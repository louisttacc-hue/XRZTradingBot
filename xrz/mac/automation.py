"""Steuerung des Macs: TradingView öffnen, Symbol wechseln, Screenshots, Mitteilungen.

Bewusst KEINE beliebigen Shell-Befehle per Telegram – wer deinen Telegram-Zugang
oder Bot-Token erbeutet, könnte sonst deinen ganzen Mac übernehmen. Stattdessen
gibt es eine feste Liste sicherer Aktionen.

macOS-Berechtigungen (Systemeinstellungen → Datenschutz & Sicherheit):
* Bildschirmaufnahme → für Screenshots (Terminal/Python erlauben)
* Bedienungshilfen → für das Tippen von Symbolen in die TradingView-App
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..market.data import to_tradingview_symbol

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
TV_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120",
               "4h": "240", "6h": "360", "12h": "720", "1d": "D", "1w": "W"}


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


class MacAutomation:
    def __init__(self, settings: Callable[[], Settings], data_dir: Path):
        self._settings = settings
        self.shots = data_dir / "screenshots"
        self.shots.mkdir(parents=True, exist_ok=True)

    @property
    def s(self) -> Settings:
        return self._settings()

    @property
    def enabled(self) -> bool:
        return self.s.mac.automation_enabled

    async def _run(self, *args: str, timeout: float = 20) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 1, "Zeitüberschreitung"
        return proc.returncode or 0, (out or err or b"").decode("utf-8", "ignore").strip()

    async def osascript(self, script: str) -> tuple[int, str]:
        if not IS_MAC:
            return 1, "Nur auf macOS verfügbar"
        return await self._run("osascript", "-e", script)

    # ---------------------------------------------------------------- Aktionen
    async def notify(self, title: str, text: str) -> None:
        if not (self.enabled and self.s.mac.notifications and IS_MAC):
            return
        await self.osascript(f'display notification "{_esc(text[:200])}" with title "{_esc(title)}"')

    async def say(self, text: str) -> None:
        if IS_MAC and self.enabled:
            await self._run("say", "-v", "Anna", text[:300])

    def tradingview_url(self, symbol: str, timeframe: str | None = None) -> str:
        tv = to_tradingview_symbol(symbol, self.s.tradingview.chart_exchange_prefix)
        interval = TV_INTERVAL.get(timeframe or self.s.trading.timeframe, "60")
        return f"https://www.tradingview.com/chart/?symbol={urllib.parse.quote(tv)}&interval={interval}"

    def tradingview_app_installed(self) -> bool:
        return IS_MAC and Path("/Applications/TradingView.app").exists()

    async def open_tradingview(self, symbol: str, timeframe: str | None = None) -> str:
        if not self.enabled:
            return "Mac-Steuerung ist in den Einstellungen deaktiviert."
        url = self.tradingview_url(symbol, timeframe)
        tv_sym = to_tradingview_symbol(symbol, self.s.tradingview.chart_exchange_prefix)
        if self.s.tradingview.open_with == "app" and self.tradingview_app_installed():
            # App aktivieren und Symbol direkt eintippen (TradingView öffnet dann die Symbolsuche)
            script = (
                'tell application "TradingView" to activate\n'
                "delay 1.5\n"
                'tell application "System Events"\n'
                f'  keystroke "{_esc(tv_sym)}"\n'
                "  delay 1.2\n"
                "  key code 36\n"
                "end tell"
            )
            code, out = await self.osascript(script)
            if code == 0:
                return f"TradingView-App geöffnet: {tv_sym}"
            log.info("TradingView-App-Steuerung fehlgeschlagen (%s) – öffne Browser", out)
        if IS_MAC:
            code, out = await self._run("open", url)
            if code != 0:
                return f"Konnte TradingView nicht öffnen: {out}"
        else:
            webbrowser.open(url)
        return f"TradingView geöffnet: {url}"

    async def screenshot(self) -> Path | None:
        if not self.enabled:
            return None
        path = self.shots / f"screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if IS_MAC:
            code, out = await self._run("screencapture", "-x", "-t", "png", str(path))
            if code != 0 or not path.exists() or path.stat().st_size < 1000:
                log.warning("Screenshot fehlgeschlagen (%s) – Bildschirmaufnahme-Berechtigung erteilen?", out)
                return None
        else:
            try:
                from PIL import ImageGrab  # type: ignore

                ImageGrab.grab().save(path)
            except Exception as exc:
                log.info("Screenshot nicht möglich: %s", exc)
                return None
        self._cleanup()
        return path

    async def chart_screenshot(self, symbol: str, timeframe: str | None = None) -> tuple[str, Path | None]:
        msg = await self.open_tradingview(symbol, timeframe)
        await asyncio.sleep(self.s.tradingview.chart_load_seconds)
        return msg, await self.screenshot()

    def _cleanup(self, keep: int = 50) -> None:
        files = sorted(self.shots.glob("screen_*.png"))
        for f in files[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass

    async def open_dashboard(self, url: str) -> None:
        if IS_MAC:
            await self._run("open", url)
        else:
            webbrowser.open(url)

    def info(self) -> dict[str, object]:
        return {
            "platform": sys.platform,
            "is_mac": IS_MAC,
            "tradingview_app": self.tradingview_app_installed(),
            "caffeinate": bool(shutil.which("caffeinate")),
            "tmp": tempfile.gettempdir(),
        }
