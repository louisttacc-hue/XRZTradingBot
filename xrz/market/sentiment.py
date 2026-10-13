"""Marktstimmung: Krypto Fear-&-Greed-Index und aktuelle Schlagzeilen (RSS)."""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]


class Sentiment:
    def __init__(self, feeds: list[str] | None = None, ttl: float = 1800):
        self.feeds = feeds or DEFAULT_FEEDS
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    async def _cached(self, key: str, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        try:
            val = await fn()
        except Exception as exc:
            log.info("Stimmungsdaten (%s) nicht verfügbar: %s", key, exc)
            val = hit[1] if hit else None
        self._cache[key] = (time.time(), val)
        return val

    async def fear_greed(self) -> dict[str, Any] | None:
        async def fetch():
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://api.alternative.me/fng/?limit=7")
                r.raise_for_status()
                data = r.json()["data"]
            return {
                "value": int(data[0]["value"]),
                "label": data[0]["value_classification"],
                "week": [int(d["value"]) for d in data],
            }

        return await self._cached("fng", fetch)

    async def headlines(self, n: int = 8) -> list[str]:
        async def fetch():
            out: list[str] = []
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": "XRZBot"}) as c:
                for url in self.feeds:
                    try:
                        r = await c.get(url)
                        root = ET.fromstring(r.content)
                        for item in root.iter("item"):
                            t = item.findtext("title")
                            if t:
                                out.append(t.strip())
                            if len(out) >= n * 2:
                                break
                    except Exception as exc:
                        log.info("Feed %s: %s", url, exc)
            return out

        return (await self._cached("news", fetch) or [])[:n]

    async def summary(self) -> str:
        parts = []
        fg = await self.fear_greed()
        if fg:
            parts.append(f"Krypto Fear & Greed: {fg['value']} ({fg['label']}), letzte 7 Tage: {fg['week']}")
        news = await self.headlines(6)
        if news:
            parts.append("Schlagzeilen: " + " | ".join(news))
        return "\n".join(parts)
