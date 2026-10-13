"""Schlanker Chrome-DevTools-Protocol-Client (CDP) für EIN Seitenziel der TradingView-App.

Bewusst ohne Playwright (``connect_over_cdp`` hängt laut Berichten an TradingView Desktop):
roher WebSocket über ``websockets`` (ohne Origin-Header, sonst lehnt Chromium ab), HTTP-Abfragen
von ``/json/list`` über ``httpx``. Klicks sind "echte" (trusted) Maus-Ereignisse über
``Input.dispatchMouseEvent``.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

import httpx

from . import labels

log = logging.getLogger(__name__)

MAX_MESSAGE = 64 * 2**20  # Screenshots können groß sein


class CdpError(RuntimeError):
    """Fehler bei einem CDP-Befehl."""


class CdpConnectionError(CdpError):
    """Verbindung zur Seite weg (TradingView beendet, Seite neu geladen, …)."""


class CdpTimeout(CdpError):
    """Keine Antwort in der erlaubten Zeit."""


class JsException(CdpError):
    """Im Browser ausgelöste JavaScript-Ausnahme."""


async def _get_json(port: int, path: str, timeout: float) -> Any:
    # trust_env=False: niemals über einen System-Proxy zu 127.0.0.1
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.get(f"http://127.0.0.1:{int(port)}{path}")
        resp.raise_for_status()
        return resp.json()


async def version(port: int, timeout: float = 2.0) -> dict[str, Any] | None:
    """``/json/version`` – None, wenn auf dem Port kein DevTools-Server antwortet."""
    try:
        data = await _get_json(port, "/json/version", timeout)
    except (httpx.HTTPError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


async def targets(port: int, timeout: float = 3.0) -> list[dict[str, Any]]:
    """``/json/list`` – alle Ziele (Seiten, Worker …); leer bei Fehler."""
    try:
        data = await _get_json(port, "/json/list", timeout)
    except (httpx.HTTPError, ValueError, OSError):
        return []
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


class CdpClient:
    """Eine WebSocket-Verbindung zu genau einem Seitenziel."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    @classmethod
    async def connect(cls, ws_url: str, open_timeout: float = 10.0) -> "CdpClient":
        client = cls(ws_url)
        await client._open(open_timeout)
        return client

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._reader is not None and not self._reader.done()

    async def _open(self, open_timeout: float) -> None:
        from websockets.asyncio.client import connect

        try:
            # Kein Origin-Header (Standard), kein Proxy, keine Kompression, große Nachrichten erlaubt
            self._ws = await connect(self.ws_url, max_size=MAX_MESSAGE, compression=None, proxy=None,
                                     open_timeout=open_timeout, ping_interval=None)
        except Exception as exc:
            raise CdpConnectionError(f"Keine Verbindung zu {self.ws_url}: {exc}") from exc
        self._reader = asyncio.create_task(self._read_loop())

    async def reconnect(self, open_timeout: float = 10.0) -> None:
        """Baut die Verbindung zum selben Ziel neu auf (z. B. nach einem Neuladen der Seite)."""
        await self.close()
        self._ids = itertools.count(1)
        await self._open(open_timeout)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                fut = self._pending.pop(msg.get("id"), None) if isinstance(msg, dict) else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)  # Ereignisse ohne id werden ignoriert
        except Exception as exc:  # Verbindungsabbruch
            log.info("CDP-Verbindung beendet: %s", exc)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CdpConnectionError("Verbindung zu TradingView unterbrochen"))
            self._pending.clear()

    async def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
        if not self.connected:
            raise CdpConnectionError("Nicht mit TradingView verbunden")
        msg_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise CdpTimeout(f"{method}: keine Antwort nach {timeout:.0f} s") from exc
        except CdpError:
            raise
        except Exception as exc:
            raise CdpConnectionError(f"{method}: {exc}") from exc
        finally:
            self._pending.pop(msg_id, None)
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}
            raise CdpError(f"{method}: {err.get('message', err)}")
        return msg.get("result") or {}

    async def evaluate(self, expression: str, await_promise: bool = True, timeout: float = 15.0) -> Any:
        """Führt JS in der Seite aus und liefert das Ergebnis als Python-Wert (JSON)."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        }, timeout=timeout)
        if "exceptionDetails" in result:
            det = result["exceptionDetails"] or {}
            exc = det.get("exception") or {}
            raise JsException(str(exc.get("description") or exc.get("value") or det.get("text") or "JS-Fehler")[:500])
        return (result.get("result") or {}).get("value")

    async def move_to(self, x: float, y: float) -> None:
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0})

    async def click_at(self, x: float, y: float) -> None:
        """Echter Linksklick (mouseMoved → mousePressed → mouseReleased) an Viewport-Koordinaten."""
        await self.move_to(x, y)
        await self.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left",
                                                     "buttons": 1, "clickCount": 1})
        await self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left",
                                                     "buttons": 0, "clickCount": 1})

    async def insert_text(self, text: str) -> None:
        """Fügt Text in das fokussierte Feld ein (wie Einfügen – keine Tastenkürzel)."""
        await self.send("Input.insertText", {"text": text})

    async def key(self, key: str, code: str, key_code: int, modifiers: int = 0) -> None:
        """Drückt eine Taste als ``rawKeyDown``/``keyUp`` (ohne Zeichen-Eingabe).

        Shift+B / Shift+S lösen in TradingView sofort Market-Orders aus und werden hier hart verweigert.
        """
        if (key.upper(), modifiers & 8) in {(k, m & 8) for k, m in labels.FORBIDDEN_KEYS}:
            raise CdpError(f"Tastenkürzel Shift+{key.upper()} ist verboten (Sofort-Order)")
        base = {"key": key, "code": code, "windowsVirtualKeyCode": key_code, "nativeVirtualKeyCode": key_code,
                "modifiers": modifiers}
        await self.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    async def screenshot(self, timeout: float = 15.0) -> str | None:
        """PNG als Base64 (``Page.captureScreenshot``) – braucht keine macOS-Berechtigung."""
        result = await self.send("Page.captureScreenshot", {"format": "png"}, timeout=timeout)
        return result.get("data")

    async def bring_to_front(self) -> None:
        await self.send("Page.bringToFront", {})

    async def close(self) -> None:
        reader, ws = self._reader, self._ws
        self._reader, self._ws = None, None
        if ws is not None:
            try:
                await ws.close()
            except Exception as exc:
                log.debug("CDP schließen: %s", exc)
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
