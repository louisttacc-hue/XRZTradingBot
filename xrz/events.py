"""Ereignis-Bus: verteilt Zustände und Meldungen an die App (WebSocket), Telegram,
Mac-Mitteilungen und das Protokoll. Thread-sicher: `emit()` darf aus jedem Thread
(z. B. Audio-Callbacks) aufgerufen werden."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]

# Meldungsarten für notify(): trade | warning | error | info | mode | report | signal
_LEVEL = {"error": "ERROR", "warning": "WARN"}


class EventBus:
    def __init__(self, storage: Any = None):
        self.storage = storage
        self._handlers: list[Handler] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Merkt sich die asyncio-Schleife, damit emit() aus fremden Threads funktioniert."""
        self._loop = loop or asyncio.get_running_loop()

    def subscribe(self, handler: Handler) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    async def publish(self, type_: str, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        with self._lock:
            handlers = list(self._handlers)
        if not handlers:
            return
        results = await asyncio.gather(*(h(type_, data) for h in handlers), return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                log.warning("Ereignis-Empfänger fehlgeschlagen (%s): %s", type_, r)

    def emit(self, type_: str, data: dict[str, Any] | None = None) -> None:
        """Nicht blockierend; sicher aus jedem Thread."""
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                return  # noch keine Schleife → Ereignis verwerfen (z. B. in Tests ohne asyncio)
        if loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self.publish(type_, data))
        else:
            asyncio.run_coroutine_threadsafe(self.publish(type_, data), loop)

    async def notify(self, kind: str, text: str, data: dict[str, Any] | None = None) -> None:
        """Für Menschen bestimmte Meldung (Trades, Warnungen, Modus-Wechsel …)."""
        level = _LEVEL.get(kind, "INFO")
        log.log(logging.ERROR if level == "ERROR" else logging.WARNING if level == "WARN" else logging.INFO,
                "[%s] %s", kind, text)
        if self.storage is not None:
            try:
                self.storage.log_event(level, text, kind)
            except Exception:  # pragma: no cover
                pass
        await self.publish("notify", {"kind": kind, "text": text, "ts": time.time(), **(data or {})})
