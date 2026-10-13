"""Wissensdatenbank der KI – wächst mit allem, was du ihr beibringst, mit Dokumenten,
Webseiten und mit Lektionen aus jedem abgeschlossenen Trade.

Suche: Hybrid aus Stichwortsuche (BM25) und semantischer Suche (Embeddings über
Ollama/OpenAI, falls verfügbar)."""
from __future__ import annotations

import io
import json
import logging
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from ..storage import Storage

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).with_name("seed_knowledge.md")

_STOP = set(
    """der die das und oder aber ist sind war wird werden ein eine einer eines einem einen nicht kein keine
    mit von zu zum zur im in am an auf aus bei für über unter vor nach als wie wenn dann dass da es er sie wir
    ihr ich du man auch nur noch schon sehr mehr so the a an and or of to in is are be for on with this that
    was hat haben kann können soll sollte muss bis durch um ob was wer wo""".split()
)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-ZäöüÄÖÜß0-9/]{2,}", text.lower())
    return [w for w in words if w not in _STOP]


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= size:
        return [text] if text else []
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
            continue
        if cur:
            chunks.append(cur)
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size - overlap :]
        cur = p
    if cur:
        chunks.append(cur)
    return chunks


def _cos(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class KnowledgeBase:
    def __init__(self, storage: Storage, router: Any = None):
        self.storage = storage
        self.router = router

    # ------------------------------------------------------------- Hinzufügen
    async def add(self, text: str, source: str = "nutzer", topic: str = "", importance: float = 1.0) -> int:
        chunks = chunk_text(text)
        if not chunks:
            return 0
        emb = None
        if self.router is not None:
            try:
                emb = await self.router.embed(chunks)
            except Exception:
                emb = None
        for i, ch in enumerate(chunks):
            vec = json.dumps({"m": emb[0], "v": emb[1][i]}) if emb else None
            self.storage.execute(
                "INSERT INTO knowledge(ts, source, topic, text, embedding, importance) VALUES(?,?,?,?,?,?)",
                (time.time(), source, topic, ch, vec, importance),
            )
        return len(chunks)

    async def ingest_bytes(self, data: bytes, filename: str, source: str = "dokument") -> int:
        name = filename.lower()
        if name.endswith(".pdf"):
            try:
                from pypdf import PdfReader
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("Für PDFs bitte 'pip install pypdf' ausführen") from exc
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
        elif name.endswith((".html", ".htm")):
            text = html_to_text(data.decode("utf-8", "ignore"))
        else:
            text = data.decode("utf-8", "ignore")
        if not text.strip():
            raise RuntimeError("Kein lesbarer Text im Dokument gefunden")
        return await self.add(text, source=f"{source}:{filename}", topic=Path(filename).stem)

    async def ingest_url(self, url: str) -> int:
        if not re.match(r"^https?://", url):
            raise ValueError("Nur http(s)-Links")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 XRZBot"}) as c:
            r = await c.get(url)
            r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype:
            return await self.ingest_bytes(r.content, url.rsplit("/", 1)[-1] or "web.pdf", source="web")
        text = html_to_text(r.text) if "html" in ctype else r.text
        return await self.add(text[:200_000], source=f"web:{url}", topic="web")

    async def seed_if_empty(self) -> int:
        if self.count() > 0 or not SEED_FILE.exists():
            return 0
        raw = SEED_FILE.read_text("utf-8")
        n = 0
        for section in re.split(r"\n(?=## )", raw):
            section = section.strip()
            if not section.startswith("## "):
                continue
            title = section.splitlines()[0][3:].strip()
            n += await self.add(section, source="grundwissen", topic=title, importance=1.2)
        return n

    # ------------------------------------------------------------------ Suche
    def _rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.storage.query("SELECT * FROM knowledge")]

    async def search(self, query: str, k: int = 5, topic: str | None = None) -> list[dict[str, Any]]:
        rows = self._rows()
        if topic:
            rows = [r for r in rows if (r.get("topic") or "").lower() == topic.lower()]
        if not rows or not query.strip():
            return []
        q_tokens = tokenize(query)
        docs = [tokenize(f"{r.get('topic') or ''} {r['text']}") for r in rows]
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / max(1, n)
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        bm25 = []
        for d in docs:
            tf = Counter(d)
            s = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / max(1.0, avgdl)))
            bm25.append(s)
        max_b = max(bm25) or 1.0
        sem = [0.0] * n
        if self.router is not None and any(r.get("embedding") for r in rows):
            try:
                qe = await self.router.embed([query])
            except Exception:
                qe = None
            if qe:
                model, vecs = qe
                for i, r in enumerate(rows):
                    if r.get("embedding"):
                        e = json.loads(r["embedding"])
                        if e.get("m") == model:
                            sem[i] = max(0.0, _cos(vecs[0], e["v"]))
        has_sem = any(sem)
        scored = []
        for i, r in enumerate(rows):
            sc = (bm25[i] / max_b) * (0.5 if has_sem else 1.0) + (sem[i] * 0.5 if has_sem else 0.0)
            sc *= float(r.get("importance") or 1.0)
            if sc > 0.05:
                scored.append((sc, r))
        scored.sort(key=lambda x: -x[0])
        return [
            {"id": r["id"], "text": r["text"], "source": r["source"], "topic": r["topic"], "score": round(sc, 3), "ts": r["ts"]}
            for sc, r in scored[:k]
        ]

    async def context_for(self, query: str, k: int = 4, max_chars: int = 3000) -> str:
        hits = await self.search(query, k)
        out, total = [], 0
        for h in hits:
            piece = f"[{h['topic'] or h['source']}] {h['text']}"
            if total + len(piece) > max_chars:
                break
            out.append(piece)
            total += len(piece)
        return "\n---\n".join(out)

    # -------------------------------------------------------------- Verwalten
    def count(self) -> int:
        return int(self.storage.query("SELECT COUNT(*) AS n FROM knowledge")[0]["n"])

    def list(self, limit: int = 50, offset: int = 0, topic: str | None = None) -> list[dict[str, Any]]:
        if topic:
            rows = self.storage.query(
                "SELECT id, ts, source, topic, text FROM knowledge WHERE topic=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (topic, limit, offset),
            )
        else:
            rows = self.storage.query(
                "SELECT id, ts, source, topic, text FROM knowledge ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        return [dict(r) for r in rows]

    def delete(self, item_id: int) -> bool:
        return self.storage.execute("DELETE FROM knowledge WHERE id=?", (item_id,)).rowcount > 0

    def recent_lessons(self, n: int = 5) -> list[str]:
        rows = self.storage.query(
            "SELECT text FROM knowledge WHERE topic='lektion' ORDER BY id DESC LIMIT ?", (n,)
        )
        return [r["text"] for r in rows]

    async def reembed_missing(self, batch: int = 32) -> int:
        """Ergänzt Embeddings für Einträge ohne (z. B. nachdem Ollama installiert wurde)."""
        if self.router is None:
            return 0
        rows = self.storage.query("SELECT id, text FROM knowledge WHERE embedding IS NULL LIMIT 2000")
        done = 0
        for i in range(0, len(rows), batch):
            part = rows[i : i + batch]
            emb = await self.router.embed([r["text"] for r in part])
            if not emb:
                break
            for r, v in zip(part, emb[1]):
                self.storage.execute("UPDATE knowledge SET embedding=? WHERE id=?", (json.dumps({"m": emb[0], "v": v}), r["id"]))
                done += 1
        return done
