"""Vincents Langzeitgedächtnis: merkt sich alles Wichtige, lernt dazu und läuft nie über.

* Jede Gesprächsrunde wird sofort als Episode gespeichert; danach zieht EIN KI-Aufruf dauerhafte
  Fakten heraus und gleicht sie mit ähnlichen Erinnerungen ab (ADD/UPDATE/DELETE/NONE, mem0-Stil).
* Duplikate werden ohne KI erkannt und stattdessen verstärkt; Änderungen behalten ihren Verlauf.
* Abruf: Relevanz (BM25 + Embeddings) gewichtet mit Wichtigkeit, Aktualität und Priorität.
* "Schlafphase" (consolidate): abschwächen, zusammenführen, Gespräche zusammenfassen,
  Unwichtiges vergessen, Kernprofil neu schreiben.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from ..ai.llm import clean_text
from ..config import MemorySettings, Settings
from ..storage import Storage
from .consolidate import Consolidator, evict_over_cap, fit_lines
from .extract import (
    REDACTED,
    SYSTEM_EXTRACT,
    ExtractionError,
    Operation,
    build_extraction_prompt,
    chunk_text,
    contains_secret,
    document_text,
    explicit_request,
    grounded,
    heuristic_operations,
    html_to_text,
    is_small_talk,
    parse_operations,
    redact_secrets,
)
from .retrieve import (
    COSINE_DUP,
    compatible,
    cosine,
    jaccard,
    near_duplicate,
    rank,
    semantic_relevance,
    tokenize,
    unit,
)
from .store import (
    CATEGORIES,
    KV_LAST_CONSOLIDATION,
    KV_PROFILE,
    KV_PROFILE_MANUAL,
    KV_SEED_SECTIONS,
    KV_SEED_STRATEGIES,
    PERSONAL_CATEGORIES,
    MemoryStore,
    category_group,
)

log = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parent.parent / "ai" / "seed_knowledge.md"
LLM_TIMEOUT_S = 60.0
EMBED_RETRY_S = 600.0  # nach erfolglosem Embedding-Versuch so lange nur Stichwortsuche
EMBED_BATCH = 32
MAX_TEXT_CHARS = 1500  # längere Texte werden in Abschnitte geteilt
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_INGEST_CHARS = 500_000
MAX_WEB_CHARS = 200_000
CAP_TOLERANCE = 1.1  # zwischen den Schlafphasen höchstens 10 % über der Obergrenze


def _settings_getter(settings: Any) -> Callable[[], Settings]:
    """Akzeptiert einen Getter (bevorzugt), einen ConfigManager oder ein Settings-Objekt."""
    if settings is None:
        default = Settings()
        return lambda: default
    if callable(settings):
        return settings
    if hasattr(settings, "settings"):
        return lambda: settings.settings
    return lambda: settings


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sections(raw: str) -> list[tuple[str, str]]:
    out = []
    for section in re.split(r"\n(?=## )", raw):
        section = section.strip()
        if not section.startswith("## "):
            continue
        title, _, body = section.partition("\n")
        title = title[3:].strip()
        if title and body.strip():
            out.append((title, body.strip()))
    return out


def strategy_text(name: str, strategy: Any) -> str:
    label = getattr(strategy, "label", "") or name
    desc = str(getattr(strategy, "description", "") or "").strip()
    phases = ", ".join(str(p) for p in (getattr(strategy, "good_in", None) or [])) or "alle"
    kind = str(getattr(strategy, "category", "") or "").strip()
    extra = f"; Typ: {kind}" if kind else ""
    return f"{label}: {desc} (Marktphasen: {phases}{extra})"


def _blocked_host(host: str | None) -> bool:
    """Eigene Rechner-Adressen (localhost) werden nie abgerufen."""
    if not host:
        return True
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified or ip.is_link_local


class MemoryManager:
    def __init__(
        self,
        storage: Storage,
        router: Any = None,
        settings: Any = None,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.storage = storage
        self.router = router
        self.clock = clock
        self._settings = _settings_getter(settings)
        self.store = MemoryStore(storage, clock)
        self._lock = asyncio.Lock()
        self._embed_retry_at = 0.0
        try:
            n = self.store.migrate_legacy()
            if n:
                log.info("Gedächtnis: %d Einträge aus der alten Wissensdatenbank übernommen", n)
        except Exception:
            log.exception("Gedächtnis: Übernahme der alten Wissensdatenbank fehlgeschlagen")

    @property
    def mem(self) -> MemorySettings:
        return self._settings().memory

    # ----------------------------------------------------------- KI-Helfer
    async def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str | None:
        """Ein KI-Aufruf mit Zeitlimit; bei jedem Fehler None (nie eine Ausnahme)."""
        if self.router is None:
            return None
        try:
            raw = await asyncio.wait_for(self.router.complete(system, prompt, max_tokens=max_tokens), LLM_TIMEOUT_S)
        except Exception as exc:
            log.info("Gedächtnis: KI nicht verfügbar (%s)", exc or type(exc).__name__)
            return None
        text = clean_text(raw if isinstance(raw, str) else "")
        return text or None

    async def embed_texts(self, texts: list[str], force: bool = False) -> tuple[str, list[list[float]]] | None:
        """Embeddings über den Router; ohne Anbieter → None (dann nur Stichwortsuche)."""
        if self.router is None or not texts or not hasattr(self.router, "embed"):
            return None
        if not force and self.clock() < self._embed_retry_at:
            return None
        try:
            res = await self.router.embed(list(texts))
        except Exception as exc:
            log.debug("Embedding fehlgeschlagen: %s", exc)
            res = None
        if not res or len(res[1]) != len(texts):
            self._embed_retry_at = self.clock() + EMBED_RETRY_S
            return None
        return str(res[0]), [list(v) for v in res[1]]

    # -------------------------------------------------------------- Seeding
    async def seed_if_empty(self) -> int:
        """Grundwissen und Strategiekatalog laden. Idempotent: später hinzugekommene Abschnitte
        bzw. Strategien werden nachgetragen (gemerkt im kv-Speicher)."""
        n = 0
        try:
            n += await self._seed_sections()
        except Exception:
            log.exception("Gedächtnis: Grundwissen konnte nicht geladen werden")
        try:
            n += await self._seed_strategies()
        except Exception:
            log.exception("Gedächtnis: Strategiekatalog konnte nicht geladen werden")
        return n

    async def _seed_sections(self) -> int:
        if not SEED_FILE.exists():
            return 0
        known = set(self.storage.kv_get(KV_SEED_SECTIONS, []) or [])
        items: list[tuple[str, str, float, str, float]] = []
        titles = []
        for title, body in _sections(SEED_FILE.read_text("utf-8")):
            if title in known:
                continue
            titles.append(title)
            for chunk in chunk_text(body, size=max(300, 900 - len(title) - 2)):
                items.append((f"{title}: {chunk}", "wissen", 0.6, "grundwissen", 1.0))
        n = await self._bulk_insert(items)
        if titles:
            self.storage.kv_set(KV_SEED_SECTIONS, sorted(known | set(titles)))
        return n

    async def _seed_strategies(self) -> int:
        from ..strategies import STRATEGIES

        known = set(self.storage.kv_get(KV_SEED_STRATEGIES, []) or [])
        names = [name for name in STRATEGIES if name not in known]
        if not names:
            return 0
        items = [
            (strategy_text(name, STRATEGIES[name]), "strategie", 0.55, f"strategie:{name}", 1.0) for name in names
        ]
        n = await self._bulk_insert(items)
        self.storage.kv_set(KV_SEED_STRATEGIES, sorted(known | set(names)))
        return n

    async def _bulk_insert(self, items: list[tuple[str, str, float, str, float]]) -> int:
        """Viele Einträge ohne Duplikatprüfung anlegen, Embeddings stapelweise."""
        for start in range(0, len(items), EMBED_BATCH):
            part = items[start : start + EMBED_BATCH]
            emb = await self.embed_texts([p[0] for p in part])
            for i, (text, category, importance, source, confidence) in enumerate(part):
                self.store.insert(
                    text, category, importance, source, confidence=confidence,
                    embedding=(emb[0], emb[1][i]) if emb else None,
                )
        return len(items)

    # ------------------------------------------------------------ Schreiben
    def _find_duplicate(
        self, text: str, category: str, vec: tuple[str, Any] | None, exclude: int | None = None
    ) -> int | None:
        rows = self.store.active_rows()
        group = category_group(category)
        by_id = {r["id"]: r for r in rows if category_group(r["category"]) == group and r["id"] != exclude}
        if not by_id:
            return None
        index = self.store.index(rows)
        tokens = set(tokenize(text))
        best: tuple[float, int] | None = None
        for c in index.candidates(tokens):
            r = by_id.get(c)
            if r is None:
                continue
            other = index.terms(c)
            if near_duplicate(tokens, other, text, r["text"]):
                sim = jaccard(tokens, other)
                if best is None or sim > best[0]:
                    best = (sim, c)
        if vec is not None and vec[1] is not None:
            model, v = vec
            for i, (m, other_vec) in self.store.vectors(rows).items():
                r = by_id.get(i)
                if r is None or m != model:
                    continue
                cos = cosine(v, other_vec)
                if cos >= COSINE_DUP and compatible(text, r["text"]) and (best is None or cos > best[0]):
                    best = (cos, i)
        return best[1] if best else None

    async def store_memory(
        self,
        text: str,
        category: str,
        importance: float,
        source: str,
        pinned: bool = False,
        confidence: float = 1.0,
        exclude: int | None = None,
        created: float | None = None,
        access_count: int = 0,
    ) -> int:
        """Speichert eine Erinnerung – oder verstärkt ein vorhandenes Beinahe-Duplikat."""
        emb = await self.embed_texts([text])
        vec = (emb[0], unit(emb[1][0])) if emb else None
        dup = self._find_duplicate(text, category, vec, exclude=exclude)
        if dup is not None:
            self.store.reinforce(dup, 0.05, pinned=pinned)
            return dup
        item_id = self.store.insert(
            text, category, max(0.0, min(1.0, importance)), source, pinned=pinned, confidence=confidence,
            created=created, access_count=access_count, embedding=(emb[0], emb[1][0]) if emb else None,
        )
        self._enforce_cap()
        return item_id

    def _enforce_cap(self) -> None:
        cap = int(self.mem.max_memories)
        if self.store.count() > cap * CAP_TOLERANCE:
            n = evict_over_cap(self.store, cap, self.clock())
            log.info("Gedächtnis: Obergrenze überschritten – %d unwichtige Erinnerungen vergessen", n)

    async def add(
        self, text: str, category: str = "notiz", importance: float = 0.7, source: str = "nutzer", pinned: bool = False
    ) -> int:
        """Erinnerung hinzufügen (Geheimnisse werden entfernt). Gibt die id zurück – bei einem
        Beinahe-Duplikat die id der verstärkten Erinnerung, bei langen Texten die des ersten Abschnitts."""
        category = (category or "notiz").strip().lower()
        if category not in CATEGORIES:
            raise ValueError(f"Unbekannte Kategorie '{category}'. Erlaubt: {', '.join(CATEGORIES)}")
        text = redact_secrets((text or "").strip())
        if not text:
            raise ValueError("Bitte einen Text angeben.")
        chunks = chunk_text(text, size=1200) if len(text) > MAX_TEXT_CHARS else [" ".join(text.split())]
        ids = [await self.store_memory(c, category, importance, source, pinned=pinned) for c in chunks]
        return ids[0]

    async def add_lesson(self, text: str, source: str = "trade") -> int:
        return await self.add(text, category="lektion", importance=0.75, source=source)

    async def remember_turn(self, channel: str, user_text: str, assistant_text: str) -> None:
        """Gesprächsrunde merken: Episode sofort speichern, danach Fakten extrahieren.
        Wirft nie; gleichzeitige Aufrufe werden nacheinander abgearbeitet."""
        try:
            user = redact_secrets((user_text or "").strip())[:4000]
            assistant = redact_secrets((assistant_text or "").strip())[:4000]
            if not user and not assistant:
                return
            self.store.add_episode(channel or "text", user, assistant)
            if not self.mem.extract_facts or is_small_talk(user):
                return
            async with self._lock:
                await self._extract(channel or "text", user, assistant)
        except Exception:
            log.exception("Gedächtnis: Gesprächsrunde konnte nicht verarbeitet werden")

    async def _candidates(self, user: str) -> list[dict[str, Any]]:
        hits = await self._rank(user, categories=PERSONAL_CATEGORIES)
        out = {r["id"]: r for _, _, r in hits[:6]}
        profile_rows = sorted(self.store.active_rows(("profil",)), key=lambda r: -float(r["importance"]))
        for r in profile_rows[:4]:
            out.setdefault(r["id"], r)
        return [{"id": r["id"], "text": r["text"], "category": r["category"]} for r in out.values()]

    async def _extract(self, channel: str, user: str, assistant: str) -> None:
        ops: list[Operation] | None = None
        if self.router is not None:
            candidates = await self._candidates(user)
            raw = await self.complete(SYSTEM_EXTRACT, build_extraction_prompt(user, assistant, candidates))
            if raw is not None:
                try:
                    ops = parse_operations(raw, {c["id"] for c in candidates})
                except ExtractionError as exc:
                    log.info("Gedächtnis: Fakten-Antwort der KI unbrauchbar (%s) – einfache Erkennung", exc)
        if ops is None:
            ops = heuristic_operations(user)
        elif not any(o.op in ("ADD", "UPDATE") for o in ops) and explicit_request(user):
            ops += heuristic_operations(user)
        await self._apply(ops, user, f"gespräch:{channel}")

    def _acceptable(self, text: str, user: str) -> bool:
        if len(text) < 3 or REDACTED in text or contains_secret(text):
            return False
        return grounded(text, user)

    async def _apply(self, ops: list[Operation], user: str, source: str) -> None:
        for op in ops:
            if op.op == "ADD":
                if self._acceptable(op.text, user):
                    imp = 0.6 if op.importance is None else op.importance
                    await self.store_memory(op.text, op.category or "fakt", imp, source, confidence=op.confidence)
            elif op.op == "UPDATE" and op.id is not None:
                old = self.store.get(op.id)
                if old is None or old["superseded_by"] is not None or not self._acceptable(op.text, user):
                    continue
                if _norm(op.text) == _norm(old["text"]):
                    self.store.reinforce(old["id"])
                    continue
                new_id = await self.store_memory(
                    op.text, op.category or old["category"],
                    max(float(old["importance"]), op.importance or 0.0), source,
                    pinned=old["pinned"], confidence=op.confidence, exclude=old["id"],
                    created=old["created"], access_count=int(old["access_count"] or 0),
                )
                self.store.supersede([old["id"]], new_id)
            elif op.op == "DELETE" and op.id is not None:
                old = self.store.get(op.id)
                if old is not None and old["superseded_by"] is None and not old["pinned"]:
                    self.store.delete([old["id"]])

    # ----------------------------------------------------------- Einlesen
    async def ingest_bytes(self, data: bytes, filename: str, source: str = "dokument") -> int:
        """Dokument (PDF, HTML, TXT/MD) als Wissen speichern. Gibt die Zahl der Abschnitte zurück.
        Fehler → ValueError mit deutscher Meldung."""
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("Datei zu groß (max. 25 MB).")
        name = Path(filename or "dokument.txt").name
        text = document_text(data, name)
        return await self._ingest_text(text, f"{source}:{name}")

    async def ingest_url(self, url: str) -> int:
        """Webseite oder PDF-Link als Wissen speichern (nur http/https, 30 s Zeitlimit)."""
        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Nur http(s)-Links werden unterstützt.")
        if _blocked_host(parsed.hostname):
            raise ValueError("Lokale Adressen werden nicht eingelesen.")

        async def guard(request: httpx.Request) -> None:
            if request.url.scheme not in ("http", "https") or _blocked_host(request.url.host):
                raise ValueError("Weiterleitung auf eine lokale Adresse blockiert.")

        headers = {"User-Agent": "Mozilla/5.0 (XRZ Vincent)"}
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers=headers, event_hooks={"request": [guard]}
            ) as client:
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    buf = bytearray()
                    async for part in r.aiter_bytes():
                        buf.extend(part)
                        if len(buf) > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Webseite zu groß (max. 20 MB).")
                    ctype = r.headers.get("content-type", "").lower()
                    charset = r.charset_encoding or "utf-8"
                    final_path = r.url.path
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"Webseite antwortet mit Fehler {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise ValueError(f"Webseite nicht erreichbar: {exc or type(exc).__name__}") from exc
        data = bytes(buf)
        if "pdf" in ctype or final_path.lower().endswith(".pdf") or data[:5] == b"%PDF-":
            name = final_path.rsplit("/", 1)[-1] or "web.pdf"
            return await self.ingest_bytes(data, name if name.lower().endswith(".pdf") else f"{name}.pdf", source="web")
        try:
            text = data.decode(charset, "replace")
        except LookupError:
            text = data.decode("utf-8", "replace")
        if "html" in ctype or re.match(r"\s*<(!doctype|html)", text[:300], re.I):
            text = html_to_text(text)
        elif ctype and not (ctype.startswith("text/") or "json" in ctype or "xml" in ctype):
            raise ValueError(f"Inhaltstyp '{ctype.split(';')[0]}' wird nicht unterstützt.")
        return await self._ingest_text(text[:MAX_WEB_CHARS], f"web:{url}")

    async def _ingest_text(self, text: str, source: str) -> int:
        if len(text) > MAX_INGEST_CHARS:
            log.info("Gedächtnis: Dokument %s auf %d Zeichen gekürzt", source, MAX_INGEST_CHARS)
        chunks = chunk_text(redact_secrets(text[:MAX_INGEST_CHARS]))
        if not chunks:
            raise ValueError("Kein lesbarer Text gefunden.")
        existing = {_norm(r["text"]): r["id"] for r in self.store.active_rows(("wissen",))}
        new: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            key = _norm(chunk)
            if key in existing:
                self.store.reinforce(existing[key])
            elif key not in seen:
                seen.add(key)
                new.append(chunk)
        await self._bulk_insert([(c, "wissen", 0.5, source[:300], 0.9) for c in new])
        self._enforce_cap()
        return len(chunks)

    # --------------------------------------------------------------- Abruf
    async def _rank(self, query: str, categories: Any = None) -> list[tuple[float, float, dict[str, Any]]]:
        rows = self.store.active_rows()
        qemb = None
        if any(r["has_emb"] for r in rows):
            qemb = await self.embed_texts([query])
            if qemb:
                rows = self.store.active_rows()
        index = self.store.index(rows)
        bm25 = index.scores(tokenize(query))
        semantic: dict[int, float] = {}
        if qemb:
            qv = unit(qemb[1][0])
            if qv is not None:
                semantic = semantic_relevance(qv, self.store.vectors(rows), qemb[0])
        if categories is not None:
            cats = set(categories)
            rows = [r for r in rows if r["category"] in cats]
            keep = {r["id"] for r in rows}
            bm25 = {i: s for i, s in bm25.items() if i in keep}
        return rank(rows, bm25, semantic, self.clock())

    @staticmethod
    def _public(row: dict[str, Any], score: float | None = None) -> dict[str, Any]:
        d = {
            "id": row["id"],
            "text": row["text"],
            "category": row["category"],
            "importance": round(float(row["importance"]), 3),
            "confidence": round(float(row["confidence"]), 3),
            "source": row["source"] or "",
            "created": row["created"],
            "updated": row["updated"],
            "last_access": row["last_access"],
            "access_count": int(row["access_count"] or 0),
            "pinned": bool(row["pinned"]),
            "superseded_by": row["superseded_by"],
        }
        if score is not None:
            d["score"] = round(score, 4)
        return d

    async def search(self, query: str, k: int = 6, category: str | None = None) -> list[dict[str, Any]]:
        """Die k passendsten Erinnerungen (nur aktuelle Fassungen, Relevanz > 0)."""
        query = (query or "").strip()
        if not query or k <= 0:
            return []
        try:
            hits = (await self._rank(query, categories=[category] if category else None))[:k]
        except Exception:
            log.exception("Gedächtnis: Suche fehlgeschlagen")
            return []
        self.store.touch(r["id"] for _, _, r in hits)
        return [self._public(r, score) for score, _, r in hits]

    async def context_for(self, query: str, max_chars: int = 2500) -> str:
        """Prompt-fertiger Kontext: Kernprofil, wichtige Regeln, relevante Erinnerungen (≤ max_chars)."""
        try:
            return await self._context(query or "", max(200, int(max_chars)))
        except Exception:
            log.exception("Gedächtnis: Kontext konnte nicht erstellt werden")
            return ""

    async def _context(self, query: str, max_chars: int) -> str:
        blocks: list[str] = []
        shown: list[int] = []

        def room() -> int:
            used = sum(len(b) for b in blocks) + 2 * len(blocks)
            return max_chars - used

        def section(header: str, lines: list[tuple[int, str]], cap: int) -> None:
            cap = min(cap, room())
            body: list[str] = []
            size = len(header)
            for item_id, line in lines:
                if size + 1 + len(line) > cap:
                    continue
                body.append(line)
                shown.append(item_id)
                size += 1 + len(line)
            if body:
                blocks.append("\n".join([header, *body]))

        profile = self.profile().strip()
        if profile:
            limit = min(int(max_chars * 0.4), room()) - len("Kernprofil:\n")
            if limit > 20:
                blocks.append("Kernprofil:\n" + fit_lines(profile, limit))

        rules = [r for r in self.store.active_rows() if r["pinned"] or r["category"] == "regel"]
        rules.sort(key=lambda r: (not r["pinned"], -float(r["importance"]), -r["id"]))
        section("Wichtige Regeln:", [(r["id"], "- " + _short(r["text"], 300)) for r in rules], int(max_chars * 0.3))

        if query.strip():
            hits = await self._rank(query)
            lines = []
            for _, _, r in hits[:12]:
                if r["id"] in shown:
                    continue
                label = r["category"]
                if r["category"] == "wissen" and ":" in (r["source"] or ""):
                    label += f", Quelle: {_short(r['source'].split(':', 1)[1], 40)}"
                lines.append((r["id"], f"- [{label}] {_short(r['text'], 600)}"))
            section("Relevante Erinnerungen:", lines, max_chars)

        self.store.touch(shown)
        return "\n\n".join(blocks)

    # -------------------------------------------------------------- Profil
    def profile(self) -> str:
        return str(self.storage.kv_get(KV_PROFILE, "") or "")

    def set_profile(self, text: str) -> None:
        """Kernprofil von Hand setzen (wird bei der nächsten Schlafphase eingearbeitet, nicht verworfen)."""
        self.write_profile(fit_lines(redact_secrets(text or ""), int(self.mem.profile_max_chars)), manual=True)

    def write_profile(self, text: str, manual: bool) -> None:
        self.storage.kv_set(KV_PROFILE, text)
        self.storage.kv_set(KV_PROFILE_MANUAL, manual)

    # ----------------------------------------------------------- Verwalten
    def get(self, item_id: int) -> dict[str, Any] | None:
        row = self.store.get(item_id)
        return self._public(row) if row else None

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        q: str | None = None,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        """Erinnerungen fürs Durchblättern (angeheftete zuerst, dann zuletzt geändert)."""
        rows = self.store.list_rows(limit, offset, category, q, include_superseded)
        return [self._public(r) for r in rows]

    def count(self, category: str | None = None) -> int:
        return self.store.count(category)

    def stats(self) -> dict[str, Any]:
        last = self.storage.kv_get(KV_LAST_CONSOLIDATION)
        return {
            "total": self.store.count(),
            "by_category": self.store.count_by_category(),
            "profile_chars": len(self.profile()),
            "last_consolidation": float(last) if last else None,
            "max_memories": int(self.mem.max_memories),
            **self.store.counts(),
            **self.store.episode_counts(),
        }

    def update(
        self,
        item_id: int,
        text: str | None = None,
        importance: float | None = None,
        pinned: bool | None = None,
        category: str | None = None,
    ) -> bool:
        fields: dict[str, Any] = {}
        if text is not None:
            clean = " ".join(redact_secrets(text).split())
            if not clean:
                return False
            fields["text"] = clean
        if importance is not None:
            fields["importance"] = max(0.0, min(1.0, float(importance)))
        if pinned is not None:
            fields["pinned"] = bool(pinned)
        if category is not None:
            if category not in CATEGORIES:
                return False
            fields["category"] = category
        return self.store.update(item_id, **fields)

    def delete(self, item_id: int) -> bool:
        return self.store.delete([item_id]) > 0

    def recent_lessons(self, n: int = 5) -> list[str]:
        rows = self.storage.query(
            "SELECT text FROM memories WHERE category='lektion' AND superseded_by IS NULL "
            "ORDER BY created DESC, id DESC LIMIT ?",
            (max(0, int(n)),),
        )
        return [r["text"] for r in rows]

    def recent_episodes(self, channel: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.episodes(channel, limit)

    # ------------------------------------------------------------- Pflege
    async def reembed_missing(self, batch: int = EMBED_BATCH, limit: int = 2000) -> int:
        """Ergänzt Embeddings für Einträge ohne (z. B. nachdem Ollama installiert wurde)."""
        if self.router is None:
            return 0
        rows = self.store.missing_embeddings(limit)
        done = 0
        for start in range(0, len(rows), batch):
            part = rows[start : start + batch]
            emb = await self.embed_texts([r["text"] for r in part], force=True)
            if not emb:
                break
            for r, v in zip(part, emb[1]):
                self.store.set_embedding(r["id"], emb[0], v)
                done += 1
        return done

    async def consolidate(self) -> str:
        """"Schlafphase": zusammenführen, abschwächen, Gespräche zusammenfassen, vergessen, Profil."""
        async with self._lock:
            try:
                return await Consolidator(self).run()
            except Exception as exc:
                log.exception("Gedächtnis: Schlafphase fehlgeschlagen")
                return f"Schlafphase fehlgeschlagen: {exc}"

    async def maybe_consolidate(self) -> str | None:
        """Schlafphase nur, wenn die letzte länger als consolidate_hours zurückliegt."""
        last = float(self.storage.kv_get(KV_LAST_CONSOLIDATION, 0) or 0)
        if self.clock() - last < float(self.mem.consolidate_hours) * 3600:
            return None
        return await self.consolidate()
