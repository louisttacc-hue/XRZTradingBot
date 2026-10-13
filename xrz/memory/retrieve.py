"""Suche im Gedächtnis: Stichwortsuche (BM25), semantische Ähnlichkeit (Embeddings),
Duplikat-Erkennung und die Gewichtung nach Relevanz, Wichtigkeit, Aktualität und Priorität.

Gesamtwert einer Erinnerung für eine Anfrage::

    0,55 · Relevanz + 0,20 · Wichtigkeit + 0,15 · Aktualität + 0,10 · Priorität

* Relevanz: normierte BM25-Punkte; gibt es Embeddings desselben Modells für Anfrage und
  Erinnerung, wird 50/50 mit der semantischen Ähnlichkeit gemischt.
* Aktualität: exponentieller Zerfall mit 30 Tagen Halbwertszeit (letzter Zugriff bzw. Änderung).
* Priorität: angeheftet oder Kategorie regel/profil/vorliebe.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable

import numpy as np

HALF_LIFE_DAYS = 30.0
JACCARD_DUP = 0.75  # ab dieser Wort-Überlappung gilt eine Erinnerung als Duplikat
COSINE_DUP = 0.9  # ab dieser Embedding-Ähnlichkeit ebenso
W_RELEVANCE, W_IMPORTANCE, W_RECENCY, W_PRIORITY = 0.55, 0.2, 0.15, 0.1
PRIORITY_CATEGORIES = frozenset({"regel", "profil", "vorliebe"})

_STOP = frozenset(
    """der die das und oder aber ist sind war wird werden ein eine einer eines einem einen
    mit von zu zum zur im in am an auf aus bei für über unter vor nach als wie wenn dann dass da es er sie wir
    ihr ich du man auch nur noch schon sehr mehr so the a an and or of to in is are be for on with this that
    was hat haben kann können soll sollte muss bis durch um ob wer wo den dem des sich mir mich dir dich
    mein meine meinen meinem meiner dein deine sein seine ihre bitte mal nutzer nutzers user""".split()
)
# Verneinungen sind bewusst KEINE Stoppwörter: "mag Gold" ≠ "mag kein Gold".
NEGATIONS = frozenset(
    "nicht kein keine keinen keinem keiner keines nie niemals nichts no not never".split()
)
_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[a-zäöüß][a-zäöüß0-9]*(?:/[a-zäöüß0-9]+)?")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
_SUFFIXES = ("ern", "em", "en", "er", "es", "e", "n", "s")


def stem(word: str) -> str:
    """Sehr leichter deutscher Stemmer (Umlaute falten, häufige Endungen abschneiden)."""
    w = word.translate(_FOLD)
    if any(c.isdigit() for c in w):
        return w
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    """Zerlegt Text in normalisierte Suchwörter (ohne Stoppwörter, Zahlen bleiben erhalten)."""
    out: list[str] = []
    for w in _TOKEN_RE.findall((text or "").lower()):
        if w[0].isdigit():
            out.append(w.replace(",", "."))
            continue
        if len(w) < 2 or w in _STOP:
            continue
        if "/" in w:  # z. B. btc/usdt → "btc/usdt", "btc", "usdt"
            out.append(w.translate(_FOLD))
            out.extend(stem(p) for p in w.split("/") if len(p) >= 2 and p not in _STOP)
            continue
        out.append(stem(w))
    return out


def numbers(text: str) -> frozenset[str]:
    return frozenset(n.replace(",", ".") for n in _NUMBER_RE.findall(text or ""))


def negated(text: str) -> bool:
    return any(w in NEGATIONS for w in re.findall(r"[a-zäöüß]+", (text or "").lower()))


def compatible(text_a: str, text_b: str) -> bool:
    """Zwei Texte dürfen nur als Duplikat gelten, wenn Zahlen und Verneinung übereinstimmen
    ("max. 1 % Risiko" ist etwas anderes als "max. 2 % Risiko")."""
    return numbers(text_a) == numbers(text_b) and negated(text_a) == negated(text_b)


def jaccard(a: set[str] | frozenset[str], b: set[str] | frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def near_duplicate(tokens_a: set[str], tokens_b: set[str], text_a: str, text_b: str, cos: float | None = None) -> bool:
    if not compatible(text_a, text_b):
        return False
    return jaccard(tokens_a, tokens_b) >= JACCARD_DUP or (cos is not None and cos >= COSINE_DUP)


def candidate_prefix(tokens: set[str], df: Any, threshold: float = JACCARD_DUP) -> list[str]:
    """Präfix-Filter für Jaccard-Suche: Ein Text mit Ähnlichkeit ≥ threshold muss mindestens
    eines dieser (seltensten) Wörter enthalten – so muss nicht jeder Eintrag verglichen werden."""
    if not tokens:
        return []
    keep = len(tokens) - math.ceil(threshold * len(tokens)) + 1
    return sorted(tokens, key=lambda t: (df(t), t))[: max(1, keep)]


def unit(vec: Iterable[float]) -> np.ndarray | None:
    v = np.asarray(list(vec), dtype=np.float32)
    if v.ndim != 1 or not v.size:
        return None
    n = float(np.linalg.norm(v))
    return v / n if n > 0 and math.isfinite(n) else None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b))


def recency(ts: float | None, now: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    if not ts:
        return 0.0
    age_days = max(0.0, now - ts) / 86400.0
    return 0.5 ** (age_days / half_life_days)


def reference_time(row: dict[str, Any]) -> float:
    return max(float(row.get("last_access") or 0.0), float(row.get("updated") or 0.0))


def priority(row: dict[str, Any]) -> float:
    return 1.0 if row.get("pinned") or row.get("category") in PRIORITY_CATEGORIES else 0.0


def hybrid_score(relevance: float, importance: float, rec: float, prio: float) -> float:
    return W_RELEVANCE * relevance + W_IMPORTANCE * importance + W_RECENCY * rec + W_PRIORITY * prio


def retention_score(row: dict[str, Any], now: float) -> float:
    """Wie wertvoll eine Erinnerung ohne konkrete Anfrage ist (fürs Vergessen bei Überfüllung)."""
    access = min(1.0, math.log1p(int(row.get("access_count") or 0)) / math.log1p(20))
    return 0.5 * float(row.get("importance") or 0.0) + 0.3 * recency(reference_time(row), now) + 0.2 * access


class Bm25Index:
    """Invertierter Index für BM25 – wird inkrementell gepflegt (k1 = 1,2; b = 0,75)."""

    k1 = 1.2
    b = 0.75

    def __init__(self) -> None:
        self.docs: dict[int, Counter[str]] = {}
        self.lengths: dict[int, int] = {}
        self.postings: dict[str, set[int]] = {}
        self.total = 0

    def __len__(self) -> int:
        return len(self.docs)

    def __contains__(self, doc_id: object) -> bool:
        return doc_id in self.docs

    def add(self, doc_id: int, tokens: list[str]) -> None:
        self.remove(doc_id)
        tf = Counter(tokens)
        self.docs[doc_id] = tf
        self.lengths[doc_id] = len(tokens)
        self.total += len(tokens)
        for t in tf:
            self.postings.setdefault(t, set()).add(doc_id)

    def remove(self, doc_id: int) -> None:
        tf = self.docs.pop(doc_id, None)
        if tf is None:
            return
        self.total -= self.lengths.pop(doc_id, 0)
        for t in tf:
            ids = self.postings.get(t)
            if ids is not None:
                ids.discard(doc_id)
                if not ids:
                    del self.postings[t]

    def terms(self, doc_id: int) -> set[str]:
        return set(self.docs.get(doc_id, ()))

    def df(self, term: str) -> int:
        return len(self.postings.get(term, ()))

    def candidates(self, tokens: set[str], threshold: float = JACCARD_DUP) -> set[int]:
        """Alle Einträge, die bei Jaccard ≥ threshold überhaupt in Frage kommen."""
        out: set[int] = set()
        for t in candidate_prefix(tokens, self.df, threshold):
            out |= self.postings.get(t, set())
        return out

    def scores(self, query_tokens: Iterable[str]) -> dict[int, float]:
        n = len(self.docs)
        if not n:
            return {}
        avgdl = (self.total / n) or 1.0
        out: dict[int, float] = {}
        for t in set(query_tokens):
            ids = self.postings.get(t)
            if not ids:
                continue
            idf = math.log(1 + (n - len(ids) + 0.5) / (len(ids) + 0.5))
            for i in ids:
                f = self.docs[i][t]
                norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / avgdl)
                out[i] = out.get(i, 0.0) + idf * f * (self.k1 + 1) / (f + norm)
        return out


def semantic_relevance(query_vec: np.ndarray, vectors: dict[int, tuple[str, np.ndarray]], model: str) -> dict[int, float]:
    """Semantische Relevanz 0..1 für alle Einträge mit Embedding desselben Modells.

    Cosinus-Werte sind je nach Modell unterschiedlich hoch. Deshalb zählt nur, was deutlich
    ähnlicher ist als der Durchschnitt (über Mittelwert + Standardabweichung); der beste Treffer
    bekommt 1. Liegt nichts klar vorne, gibt es keine semantische Relevanz."""
    ids = [i for i, (m, v) in vectors.items() if m == model and v.shape == query_vec.shape]
    if not ids:
        return {}
    sims = np.stack([vectors[i][1] for i in ids]) @ query_vec
    top = float(sims.max())
    mean = float(sims.mean())
    thr = mean + float(sims.std()) if len(ids) >= 3 else 0.5
    if top - mean < 0.05 and len(ids) >= 3 or top <= thr:
        return {i: 0.0 for i in ids}
    rel = np.clip((sims - thr) / (top - thr), 0.0, 1.0)
    return {i: float(r) for i, r in zip(ids, rel)}


def rank(
    rows: list[dict[str, Any]],
    bm25: dict[int, float],
    semantic: dict[int, float],
    now: float,
) -> list[tuple[float, float, dict[str, Any]]]:
    """Bewertet Zeilen → [(Gesamtwert, Relevanz, Zeile)], absteigend sortiert, nur Relevanz > 0."""
    max_b = max(bm25.values(), default=0.0)
    out = []
    for r in rows:
        b = bm25.get(r["id"], 0.0) / max_b if max_b > 0 else 0.0
        rel = 0.5 * b + 0.5 * semantic[r["id"]] if r["id"] in semantic else b
        if rel <= 0:
            continue
        score = hybrid_score(rel, float(r.get("importance") or 0.0), recency(reference_time(r), now), priority(r))
        out.append((score, rel, r))
    out.sort(key=lambda x: (-x[0], -x[2]["id"]))
    return out
