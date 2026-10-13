"""Einfacher Benachrichtigungs-Bus: Engine → Telegram, Mac-Mitteilungen, Dashboard-Log."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Handler = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class Notifier:
    """kind: trade | signal | warning | error | info | report | mode"""

    def __init__(self, storage: Any = None):
        self.storage = storage
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def send(self, kind: str, text: str, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        level = {"error": "ERROR", "warning": "WARN"}.get(kind, "INFO")
        log.log(logging.ERROR if level == "ERROR" else logging.WARNING if level == "WARN" else logging.INFO, "[%s] %s", kind, text)
        if self.storage is not None:
            try:
                self.storage.log_event(level, text, kind)
            except Exception:  # pragma: no cover
                pass
        if self._handlers:
            results = await asyncio.gather(*(h(kind, text, data) for h in self._handlers), return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    log.warning("Benachrichtigung fehlgeschlagen: %s", r)
