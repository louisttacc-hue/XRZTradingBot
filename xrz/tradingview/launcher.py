"""TradingView Desktop finden, prüfen und – nur mit Erlaubnis – mit Steuer-Port neu starten (macOS).

Wichtige Stolpersteine (Recherche Okt. 2026):
* ``ELECTRON_RUN_AS_NODE`` (VS Code/Cursor-Terminals, Electron-Kindprozesse) startet die App als
  reines Node → "bad option: --remote-debugging-port" mit Exit 9. Deshalb alle ``ELECTRON_*``,
  ``NODE_OPTIONS`` und ``NODE_EXTRA_CA_CERTS`` aus der Umgebung entfernen.
* TradingView läuft nur einmal: Ein zweiter Start gibt an die laufende App (ohne Port) ab und
  beendet sich. Also erst sauber beenden, warten, notfalls SIGKILL, dann direkt das Programm starten.
* Ein belegter Port wird von Chromium stillschweigend übersprungen → prüfen, WELCHER Prozess
  auf dem Port lauscht (muss TradingView selbst sein).

Alle Aufrufe externer Programme laufen mit Argumentlisten (keine Shell).
"""
from __future__ import annotations

import asyncio
import logging
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import Settings
from . import cdp, labels

log = logging.getLogger(__name__)

_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9.-]{3,200}$")


@dataclass
class AppInfo:
    path: Path
    binary: Path
    bundle_id: str
    version: str


def is_supported() -> bool:
    return sys.platform == "darwin"


def clean_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Umgebung ohne Variablen, die Electron als Node starten oder Node-Optionen einschleusen."""
    src = os.environ if env is None else env
    return {
        k: v for k, v in src.items()
        if not k.startswith(labels.ENV_BLOCKLIST_PREFIXES) and k not in labels.ENV_BLOCKLIST
    }


def launch_args(binary: Path, port: int) -> list[str]:
    """Programm DIREKT starten (nicht über ``open -a``) – nur mit lokalem Steuer-Port."""
    return [str(binary), f"--remote-debugging-port={int(port)}"]


def quit_args(bundle_id: str) -> list[str]:
    """AppleScript-Beenden; "if … is running" verhindert, dass AppleScript die App erst startet."""
    if not _BUNDLE_ID_RE.match(bundle_id):
        raise ValueError(f"Ungültige Bundle-ID: {bundle_id!r}")
    script = f'if application id "{bundle_id}" is running then tell application id "{bundle_id}" to quit'
    return [_tool("osascript", "/usr/bin/osascript"), "-e", script]


def _tool(name: str, default: str) -> str:
    return default if Path(default).exists() else (shutil.which(name) or name)


def read_app_info(app_path: Path) -> AppInfo | None:
    """Info.plist lesen (Bundle-ID, Version); None, wenn dort keine TradingView-App liegt."""
    plist = app_path / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as fh:
            data = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        log.debug("Info.plist nicht lesbar (%s): %s", plist, exc)
        return None
    exe = str(data.get("CFBundleExecutable") or "TradingView")
    binary = app_path / "Contents" / "MacOS" / exe
    if not binary.exists():
        return None
    return AppInfo(
        path=app_path,
        binary=binary,
        bundle_id=str(data.get("CFBundleIdentifier") or labels.DEFAULT_BUNDLE_ID),
        version=str(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or ""),
    )


class TradingViewLauncher:
    """Prozess- und Port-Verwaltung der TradingView-App (nur macOS, sonst freundliche Meldungen)."""

    def __init__(self, settings: Callable[[], Settings], data_dir: Path):
        self._settings = settings
        self.log_path = Path(data_dir) / "logs" / "tradingview.log"
        self._proc: subprocess.Popen | None = None

    @property
    def s(self) -> Settings:
        return self._settings()

    def supported(self) -> bool:
        return is_supported()

    # ------------------------------------------------------------- App finden
    def locate(self) -> AppInfo | None:
        configured = Path(self.s.tradingview.app_path).expanduser()
        for candidate in (configured, Path.home() / "Applications" / labels.APP_NAME):
            info = read_app_info(candidate)
            if info:
                return info
        return None

    # ------------------------------------------------------- Prozesse / Port
    async def _run(self, args: list[str], timeout: float = 15.0) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            return 127, str(exc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "Zeitüberschreitung"
        return proc.returncode or 0, (out or err or b"").decode("utf-8", "replace").strip()

    async def main_pids(self) -> list[int]:
        """PIDs des TradingView-Hauptprozesses dieses Benutzers (keine Helper-Prozesse)."""
        if not self.supported():
            return []
        code, out = await self._run([_tool("pgrep", "/usr/bin/pgrep"), "-u", str(os.getuid()), "-f",
                                     labels.MAIN_PROCESS_PATTERN])
        if code not in (0, 1):
            log.info("pgrep fehlgeschlagen (%s): %s", code, out)
        return [int(x) for x in out.split() if x.isdigit()] if code == 0 else []

    async def listener_pids(self, port: int) -> list[int]:
        """Welche Prozesse lauschen auf dem Port? (lsof)"""
        if not self.supported():
            return []
        code, out = await self._run([_tool("lsof", "/usr/sbin/lsof"), "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"])
        return [int(x) for x in out.split() if x.isdigit()] if code == 0 else []

    async def check_cdp(self, port: int) -> dict[str, Any]:
        """Antwortet auf dem Port ein DevTools-Server – und gehört er wirklich TradingView?

        → {"up", "owned", "browser", "listeners", "pids"}
        """
        info = await cdp.version(port)
        pids = await self.main_pids()
        listeners = await self.listener_pids(port) if info is not None else []
        owned = bool(info) and bool(listeners) and all(p in pids for p in listeners)
        return {"up": info is not None, "owned": owned, "browser": (info or {}).get("Browser", ""),
                "listeners": listeners, "pids": pids}

    async def chart_targets(self, port: int) -> list[dict[str, Any]]:
        """Chart-Seiten (type=page, URL tradingview.com/chart) – nur wenn der Port TradingView gehört."""
        chk = await self.check_cdp(port)
        if not chk["owned"]:
            return []
        return [t for t in await cdp.targets(port)
                if t.get("type") == "page" and labels.CHART_URL_RE.match(str(t.get("url") or ""))
                and t.get("webSocketDebuggerUrl")]

    # ------------------------------------------------------ Beenden / Starten
    async def quit_app(self, info: AppInfo, timeout: float = 25.0) -> tuple[bool, str]:
        """Sanft beenden (AppleScript), bis ``timeout`` warten, als letztes Mittel SIGKILL."""
        if not await self.main_pids():
            return True, "TradingView lief nicht."
        try:
            await self._run(quit_args(info.bundle_id), timeout=15.0)
        except ValueError as exc:
            log.warning("%s – beende per Signal", exc)
            for pid in await self.main_pids():
                _kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while await self.main_pids() and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        leftovers = await self.main_pids()
        if leftovers:
            log.warning("TradingView reagiert nicht auf Beenden – SIGKILL für %s", leftovers)
            for pid in leftovers:
                _kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 10.0
            while await self.main_pids() and time.monotonic() < deadline:
                await asyncio.sleep(0.3)
        if await self.main_pids():
            return False, "TradingView ließ sich nicht beenden."
        return True, "TradingView beendet."

    def spawn(self, info: AppInfo, port: int) -> subprocess.Popen:
        """Startet das TradingView-Programm direkt mit Steuer-Port (bereinigte Umgebung, eigene Sitzung)."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as logf:
            proc = subprocess.Popen(
                launch_args(info.binary, port), env=clean_env(), stdin=subprocess.DEVNULL, stdout=logf,
                stderr=logf, start_new_session=True, close_fds=True,
            )
        self._proc = proc
        return proc

    def _log_tail(self) -> str:
        try:
            return self.log_path.read_bytes()[-2000:].decode("utf-8", "replace")
        except OSError:
            return ""

    async def launch(self, info: AppInfo, port: int, wait: float = 240.0) -> tuple[bool, str]:
        """Starten und warten, bis eine Chart-Seite über den (eigenen) Port erreichbar ist."""
        if not self.supported():
            return False, "Das geht nur auf dem Mac."
        if (await self.check_cdp(port))["up"]:
            return False, (f"Port {port} ist schon von einem anderen Programm belegt – bitte in den Einstellungen "
                           "einen anderen TradingView-Steuer-Port wählen.")
        try:
            proc = await asyncio.to_thread(self.spawn, info, port)
        except OSError as exc:
            return False, f"TradingView ließ sich nicht starten: {exc}"
        await asyncio.sleep(3.0)
        code = proc.poll()
        if code is not None:
            tail = self._log_tail()
            if "bad option" in tail:
                return False, ("TradingView hat den Steuer-Port abgelehnt (die App wurde als Node gestartet). "
                               "Bitte Vincent neu starten und es noch einmal versuchen.")
            if code == 0:
                return False, ("TradingView lief offenbar noch und hat den Start übernommen – bitte TradingView "
                               "ganz beenden (Cmd+Q) und es noch einmal versuchen.")
            return False, f"TradingView hat sich direkt wieder beendet (Code {code})."
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, "TradingView wurde während des Starts beendet."
            if await self.chart_targets(port):
                return True, "TradingView läuft mit Steuerzugang."
            await asyncio.sleep(1.0)
        return False, ("TradingView ist gestartet, aber nach 4 Minuten ist noch kein Chart erreichbar. "
                       "Bitte einen Chart öffnen und noch einmal prüfen.")

    async def restart_with_cdp(self, info: AppInfo, port: int, wait: float = 240.0,
                               quit_timeout: float = 25.0) -> tuple[bool, str]:
        ok, msg = await self.quit_app(info, timeout=quit_timeout)
        if not ok:
            return False, msg + " Bitte TradingView von Hand beenden (Cmd+Q)."
        return await self.launch(info, port, wait=wait)


def _kill(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except OSError as exc:
        log.warning("Signal %s an %s fehlgeschlagen: %s", sig, pid, exc)
