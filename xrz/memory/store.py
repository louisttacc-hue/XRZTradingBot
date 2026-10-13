"""Gedächtnis-Speicher in der gemeinsamen SQLite-Datenbank.

Tabellen:
* ``memories`` – einzelne Erinnerungen (Fakten, Vorlieben, Regeln, Wissen …) mit Wichtigkeit,
  Zugriffsstatistik, optionalem Embedding und Versionsgeschichte (``superseded_by``).
* ``episodes`` – rohe Gesprächsrunden (werden später zusammengefasst und gelöscht).
* ``episode_summaries`` – eine Zusammenfassung pro Tag und Kanal.

Kernprofil und Zeitstempel liegen im ``kv``-Speicher der Storage-Klasse.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable

import numpy as np

from ..storage import Storage
from .retrieve import Bm25Index, tokenize, unit

log = logging.getLogger(__name__)

CATEGORIES = ("profil", "vorliebe", "regel", "fakt", "lektion", "strategie", "wissen", "notiz", "ereignis")
PROTECTED_CATEGORIES = frozenset({"regel", "profil"})  # werden nie automatisch vergessen
KNOWLEDGE_CATEGORIES = frozenset({"wissen", "strategie"})
PERSONAL_CATEGORIES = frozenset({"profil", "vorliebe", "regel", "fakt", "lektion", "notiz"})

KV_PROFILE = "memory_profile"
KV_PROFILE_MANUAL = "memory_profile_manual"
KV_PROFILE_FP = "memory_profile_fp"
KV_LAST_CONSOLIDATION = "memory_last_consolidation"
KV_LAST_DECAY = "memory_last_decay"
KV_MIGRATED = "memory_migrated"
KV_SEED_SECTIONS = "memory_seed_sections"
KV_SEED_STRATEGIES = "memory_seed_strategies"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'notiz',
        importance REAL NOT NULL DEFAULT 0.5,
        confidence REAL NOT NULL DEFAULT 1.0,
        access_count INTEGER NOT NULL DEFAULT 0,
        last_access REAL,
        created REAL NOT NULL,
        updated REAL NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        source TEXT,
        superseded_by INTEGER,
        embedding TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(superseded_by, category)",
    """CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        channel TEXT NOT NULL,
        user_text TEXT NOT NULL,
        assistant_text TEXT NOT NULL,
        summarized INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_episodes ON episodes(summarized, ts)",
    """CREATE TABLE IF NOT EXISTS episode_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        channel TEXT NOT NULL,
        summary TEXT NOT NULL,
        created REAL NOT NULL
    )""",
)

_COLS = (
    "id, text, category, importance, confidence, access_count, last_access, created, updated, pinned, "
    "source, superseded_by, embedding IS NOT NULL AS has_emb"
)
_EDITABLE = {"text", "category", "importance", "confidence", "pinned", "access_count", "source"}


def category_group(category: str) -> str:
    """Duplikate werden nur innerhalb einer Gruppe gesucht (persönlich / Wissen / Ereignisse)."""
    if category in KNOWLEDGE_CATEGORIES:
        return "wissen"
    if category == "ereignis":
        return "ereignis"
    return "persoenlich"


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["pinned"] = bool(d.get("pinned"))
    if "has_emb" in d:
        d["has_emb"] = bool(d["has_emb"])
    return d


class MemoryStore:
    def __init__(self, storage: Storage, clock: Callable[[], float]):
        self.db = storage
        self.clock = clock
        self._index: Bm25Index | None = None
        self._vectors: dict[int, tuple[str, np.ndarray]] | None = None
        for stmt in _SCHEMA:
            storage.execute(stmt)

    # ------------------------------------------------------------- Migration
    def migrate_legacy(self) -> int:
        """Übernimmt einmalig die alte Tabelle ``knowledge`` (Wissensdatenbank v1)."""
        if self.db.kv_get(KV_MIGRATED):
            return 0
        exists = self.db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge'")
        n = 0
        titles: list[str] = []
        if exists:
            titles = [
                r["topic"]
                for r in self.db.query(
                    "SELECT DISTINCT topic FROM knowledge WHERE source='grundwissen' AND topic IS NOT NULL"
                )
            ]
            cur = self.db.execute(
                """INSERT INTO memories(text, category, importance, confidence, access_count, last_access,
                                         created, updated, pinned, source, superseded_by, embedding)
                SELECT
                    CASE WHEN text LIKE '## %' THEN substr(text, 4) ELSE text END,
                    CASE WHEN lower(coalesce(topic, '')) = 'lektion' THEN 'lektion'
                         WHEN coalesce(source, '') = 'grundwissen' THEN 'wissen'
                         WHEN instr(coalesce(source, ''), ':') > 0 AND coalesce(source, '') NOT LIKE 'trade:%'
                              THEN 'wissen'
                         ELSE 'notiz' END,
                    CASE WHEN lower(coalesce(topic, '')) = 'lektion' THEN 0.7
                         WHEN coalesce(source, '') = 'grundwissen' THEN 0.6
                         WHEN instr(coalesce(source, ''), ':') > 0 AND coalesce(source, '') NOT LIKE 'trade:%'
                              THEN 0.5
                         ELSE 0.75 END,
                    1.0, 0, NULL, ts, ts, 0, coalesce(source, 'nutzer'), NULL, embedding
                FROM knowledge ORDER BY id"""
            )
            n = max(0, cur.rowcount)
        if titles:
            known = set(self.db.kv_get(KV_SEED_SECTIONS, []) or [])
            self.db.kv_set(KV_SEED_SECTIONS, sorted(known | set(titles)))
        self.db.kv_set(KV_MIGRATED, True)
        self._invalidate()
        return n

    def _invalidate(self) -> None:
        self._index = None
        self._vectors = None

    # ----------------------------------------------------------- Erinnerungen
    def insert(
        self,
        text: str,
        category: str,
        importance: float,
        source: str,
        pinned: bool = False,
        confidence: float = 1.0,
        created: float | None = None,
        access_count: int = 0,
        embedding: tuple[str, list[float]] | None = None,
    ) -> int:
        now = self.clock()
        emb = json.dumps({"m": embedding[0], "v": list(embedding[1])}) if embedding else None
        cur = self.db.execute(
            """INSERT INTO memories(text, category, importance, confidence, access_count, last_access,
                                    created, updated, pinned, source, superseded_by, embedding)
               VALUES(?,?,?,?,?,NULL,?,?,?,?,NULL,?)""",
            (text, category, importance, confidence, access_count, created or now, now, int(pinned), source, emb),
        )
        item_id = int(cur.lastrowid)
        if self._index is not None:
            self._index.add(item_id, tokenize(text))
        if embedding and self._vectors is not None:
            v = unit(embedding[1])
            if v is not None:
                self._vectors[item_id] = (embedding[0], v)
        return item_id

    def get(self, item_id: int) -> dict[str, Any] | None:
        rows = self.db.query(f"SELECT {_COLS} FROM memories WHERE id=?", (item_id,))
        return _row(rows[0]) if rows else None

    def active_rows(self, categories: Iterable[str] | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {_COLS} FROM memories WHERE superseded_by IS NULL"
        params: list[Any] = []
        cats = list(categories) if categories is not None else None
        if cats is not None:
            if not cats:
                return []
            sql += f" AND category IN ({','.join('?' for _ in cats)})"
            params.extend(cats)
        return [_row(r) for r in self.db.query(sql + " ORDER BY id", params)]

    def list_rows(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        q: str | None = None,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        sql = f"SELECT {_COLS} FROM memories WHERE 1=1"
        params: list[Any] = []
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        if category:
            sql += " AND category=?"
            params.append(category)
        for term in (q or "").split():
            sql += " AND text LIKE ? ESCAPE '\\'"
            esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{esc}%")
        sql += " ORDER BY pinned DESC, updated DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([max(0, int(limit)), max(0, int(offset))])
        return [_row(r) for r in self.db.query(sql, params)]

    def count(self, category: str | None = None, include_superseded: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM memories WHERE 1=1"
        params: list[Any] = []
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        if category:
            sql += " AND category=?"
            params.append(category)
        return int(self.db.query(sql, params)[0]["n"])

    def count_by_category(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT category, COUNT(*) AS n FROM memories WHERE superseded_by IS NULL GROUP BY category"
        )
        return {r["category"]: int(r["n"]) for r in rows}

    def counts(self) -> dict[str, int]:
        r = self.db.query(
            """SELECT
                 SUM(CASE WHEN superseded_by IS NULL AND pinned=1 THEN 1 ELSE 0 END) AS pinned,
                 SUM(CASE WHEN superseded_by IS NULL AND embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
                 SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) AS superseded
               FROM memories"""
        )[0]
        return {k: int(r[k] or 0) for k in ("pinned", "embedded", "superseded")}

    def update(self, item_id: int, **fields: Any) -> bool:
        fields = {k: v for k, v in fields.items() if k in _EDITABLE and v is not None}
        if not fields:
            return self.get(item_id) is not None
        if "pinned" in fields:
            fields["pinned"] = int(bool(fields["pinned"]))
        sets = [f"{k}=?" for k in fields]
        params: list[Any] = list(fields.values())
        if "text" in fields:
            sets.append("embedding=NULL")
        sets.append("updated=?")
        params.extend([self.clock(), item_id])
        ok = self.db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", params).rowcount > 0
        if ok and "text" in fields:
            if self._index is not None:
                self._index.add(item_id, tokenize(fields["text"]))
            if self._vectors is not None:
                self._vectors.pop(item_id, None)
        return ok

    def touch(self, ids: Iterable[int]) -> None:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return
        self.db.execute(
            f"UPDATE memories SET access_count=access_count+1, last_access=? WHERE id IN ({','.join('?' for _ in ids)})",
            [self.clock(), *ids],
        )

    def reinforce(self, item_id: int, delta: float = 0.05, pinned: bool = False, access: int = 1) -> None:
        """Bestätigte Erinnerung stärken statt ein Duplikat anzulegen."""
        self.db.execute(
            """UPDATE memories SET importance=MIN(1.0, importance + ?), access_count=access_count + ?,
               updated=?, last_access=?, pinned=MAX(pinned, ?) WHERE id=?""",
            (delta, access, self.clock(), self.clock(), int(pinned), item_id),
        )

    def supersede(self, ids: Iterable[int], by_id: int) -> None:
        """Alte Fassungen ausblenden (bleiben als Verlauf erhalten)."""
        ids = [i for i in dict.fromkeys(ids) if i != by_id]
        if not ids:
            return
        self.db.execute(
            f"UPDATE memories SET superseded_by=?, updated=? WHERE id IN ({','.join('?' for _ in ids)})",
            [by_id, self.clock(), *ids],
        )
        self._forget_cached(ids)

    def delete(self, ids: Iterable[int]) -> int:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return 0
        n = self.db.execute(f"DELETE FROM memories WHERE id IN ({','.join('?' for _ in ids)})", ids).rowcount
        self._forget_cached(ids)
        return max(0, n)

    def _forget_cached(self, ids: Iterable[int]) -> None:
        for i in ids:
            if self._index is not None:
                self._index.remove(i)
            if self._vectors is not None:
                self._vectors.pop(i, None)

    def decay(self, before: float, factor: float, floor: float) -> int:
        """Wichtigkeit lange nicht genutzter (nicht angehefteter) Erinnerungen abschwächen."""
        cur = self.db.execute(
            """UPDATE memories SET importance=MAX(?, importance * ?)
               WHERE superseded_by IS NULL AND pinned=0 AND importance > ?
                 AND MAX(coalesce(last_access, 0), updated) < ?""",
            (floor, factor, floor, before),
        )
        return max(0, cur.rowcount)

    def purge_superseded(self, before: float) -> int:
        """Sehr alte, längst ersetzte Fassungen endgültig löschen."""
        return max(0, self.db.execute(
            "DELETE FROM memories WHERE superseded_by IS NOT NULL AND updated < ?", (before,)
        ).rowcount)

    # ------------------------------------------------------------- Embeddings
    def set_embedding(self, item_id: int, model: str, vec: list[float]) -> None:
        self.db.execute(
            "UPDATE memories SET embedding=? WHERE id=?", (json.dumps({"m": model, "v": list(vec)}), item_id)
        )
        if self._vectors is not None:
            v = unit(vec)
            if v is not None:
                self._vectors[item_id] = (model, v)

    def missing_embeddings(self, limit: int = 2000) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, text FROM memories WHERE superseded_by IS NULL AND embedding IS NULL ORDER BY id LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def vectors(self, rows: list[dict[str, Any]]) -> dict[int, tuple[str, np.ndarray]]:
        """Embeddings der aktiven Zeilen (zwischengespeichert, repariert sich selbst)."""
        want = {r["id"] for r in rows if r.get("has_emb")}
        if self._vectors is None:
            self._vectors = {}
        cache = self._vectors
        for i in list(cache):
            if i not in want:
                cache.pop(i)
        missing = [i for i in want if i not in cache]
        for start in range(0, len(missing), 500):
            part = missing[start : start + 500]
            for r in self.db.query(
                f"SELECT id, embedding FROM memories WHERE id IN ({','.join('?' for _ in part)})", part
            ):
                try:
                    e = json.loads(r["embedding"])
                    v = unit(e["v"])
                except (TypeError, ValueError, KeyError):
                    v = None
                    e = {}
                if v is not None:
                    cache[int(r["id"])] = (str(e.get("m", "")), v)
        return {i: cache[i] for i in want if i in cache}

    def index(self, rows: list[dict[str, Any]]) -> Bm25Index:
        """BM25-Index passend zu den übergebenen aktiven Zeilen (baut bei Abweichung neu)."""
        ids = {r["id"] for r in rows}
        if self._index is None or len(self._index) != len(ids) or any(i not in self._index for i in ids):
            idx = Bm25Index()
            for r in rows:
                idx.add(r["id"], tokenize(r["text"]))
            self._index = idx
        return self._index

    # --------------------------------------------------------------- Episoden
    def add_episode(self, channel: str, user_text: str, assistant_text: str) -> int:
        cur = self.db.execute(
            "INSERT INTO episodes(ts, channel, user_text, assistant_text, summarized) VALUES(?,?,?,?,0)",
            (self.clock(), channel, user_text, assistant_text),
        )
        return int(cur.lastrowid)

    def episodes(self, channel: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if channel:
            rows = self.db.query(
                "SELECT * FROM episodes WHERE channel=? ORDER BY id DESC LIMIT ?", (channel, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def unsummarized_before(self, before: float) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM episodes WHERE summarized=0 AND ts < ? ORDER BY ts, id", (before,))
        return [dict(r) for r in rows]

    def mark_summarized(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            self.db.execute(
                f"UPDATE episodes SET summarized=1 WHERE id IN ({','.join('?' for _ in part)})", part
            )

    def delete_summarized_before(self, before: float) -> int:
        return max(0, self.db.execute("DELETE FROM episodes WHERE summarized=1 AND ts < ?", (before,)).rowcount)

    def add_summary(self, day: str, channel: str, summary: str) -> int:
        cur = self.db.execute(
            "INSERT INTO episode_summaries(day, channel, summary, created) VALUES(?,?,?,?)",
            (day, channel, summary, self.clock()),
        )
        return int(cur.lastrowid)

    def summaries(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM episode_summaries ORDER BY day DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def episode_counts(self) -> dict[str, int]:
        e = self.db.query("SELECT COUNT(*) AS n FROM episodes")[0]["n"]
        s = self.db.query("SELECT COUNT(*) AS n FROM episode_summaries")[0]["n"]
        return {"episodes": int(e), "summaries": int(s)}
