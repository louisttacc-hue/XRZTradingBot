"""Tests für Vincents Langzeitgedächtnis (xrz.memory)."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from xrz.ai.llm import LLMError
from xrz.config import Settings
from xrz.memory import MemoryManager, chunk_text, contains_secret, html_to_text, redact_secrets
from xrz.memory.consolidate import SYSTEM_MERGE, SYSTEM_PROFILE, SYSTEM_SUMMARY, day_key
from xrz.memory.extract import SYSTEM_EXTRACT, ExtractionError, heuristic_operations, parse_operations
from xrz.memory.retrieve import recency, tokenize
from xrz.storage import Storage

DAY = 86400.0


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #
class Clock:
    def __init__(self, t: float | None = None):
        self.t = t if t is not None else time.mktime((2026, 3, 10, 12, 0, 0, 0, 0, -1))

    def __call__(self) -> float:
        return self.t

    def advance(self, days: float = 0, hours: float = 0) -> None:
        self.t += days * DAY + hours * 3600


class FakeRouter:
    """Liefert vorgegebene Antworten je nach Aufgabe (erkannt am System-Prompt)."""

    def __init__(self, extract=None, merge="", summary="", profile="", embed_fn=None, delay=0.0):
        self.replies = {"extract": extract, "merge": merge, "summary": summary, "profile": profile}
        self.embed_fn = embed_fn
        self.delay = delay
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def complete(self, system, prompt, images=None, max_tokens=2000):
        kind = {SYSTEM_EXTRACT: "extract", SYSTEM_MERGE: "merge", SYSTEM_SUMMARY: "summary", SYSTEM_PROFILE: "profile"}[system]
        self.calls.append((kind, prompt))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            reply = self.replies[kind]
            if isinstance(reply, Exception):
                raise reply
            if callable(reply):
                reply = reply(prompt)
            return reply if reply is not None else '{"operations": []}'
        finally:
            self.active -= 1

    async def embed(self, texts):
        if self.embed_fn is None:
            return None
        return "fake:model", [self.embed_fn(t) for t in texts]

    def prompts(self, kind):
        return [p for k, p in self.calls if k == kind]


def ops(*items) -> str:
    return json.dumps({"operations": list(items)})


@pytest.fixture
def storage(tmp_path):
    st = Storage(tmp_path / "mem.db")
    yield st
    st.close()


@pytest.fixture
def clock():
    return Clock()


def make(storage, router=None, clock=None, **memory) -> MemoryManager:
    s = Settings()
    for k, v in memory.items():
        setattr(s.memory, k, v)
    return MemoryManager(storage, router, lambda: s, clock=clock or Clock())


def texts(mm: MemoryManager, **kw) -> list[str]:
    return [m["text"] for m in mm.list(limit=500, **kw)]


# --------------------------------------------------------------------------- #
# Migration & Seeding
# --------------------------------------------------------------------------- #
async def test_migration_from_old_knowledge_table(storage):
    rows = [
        (1000.0, "grundwissen", "MACD", "## MACD\nMACD = Differenz aus EMA12 und EMA26.", None, 1.2),
        (1001.0, "trade:5", "lektion", "BTC long in Seitwärtsphase endete mit -1R.", json.dumps({"m": "x", "v": [1.0, 0.0]}), 1.0),
        (1002.0, "web:https://example.com", "web", "Webwissen über die Ichimoku-Wolke.", None, 1.0),
        (1003.0, "telegram", "notiz", "Ich trade nur am Wochenende.", None, 1.5),
        (1004.0, "dokument:buch.pdf", "buch", "Kapitel 1: Risiko zuerst.", None, 1.0),
    ]
    for r in rows:
        storage.execute("INSERT INTO knowledge(ts, source, topic, text, embedding, importance) VALUES(?,?,?,?,?,?)", r)

    mm = make(storage)
    items = {m["text"]: m for m in mm.list(limit=50)}
    assert mm.count() == 5
    macd = items["MACD\nMACD = Differenz aus EMA12 und EMA26."]
    assert macd["category"] == "wissen" and macd["created"] == 1000.0
    assert items["BTC long in Seitwärtsphase endete mit -1R."]["category"] == "lektion"
    assert items["Webwissen über die Ichimoku-Wolke."]["category"] == "wissen"
    assert items["Ich trade nur am Wochenende."]["category"] == "notiz"
    assert items["Kapitel 1: Risiko zuerst."]["category"] == "wissen"
    assert mm.store.counts()["embedded"] == 1
    assert mm.recent_lessons(3) == ["BTC long in Seitwärtsphase endete mit -1R."]

    # zweiter Start: keine doppelte Übernahme; Grundwissen "MACD" wird nicht erneut gesät
    mm2 = make(storage)
    assert mm2.count() == 5
    added = await mm2.seed_if_empty()
    assert added > 20
    assert not any(t.startswith("MACD:") for t in texts(mm2))


async def test_migration_without_old_table(storage):
    storage.execute("DROP TABLE knowledge")
    mm = make(storage)
    assert mm.count() == 0


async def test_seed_idempotent_and_adds_new_strategies(storage, monkeypatch):
    mm = make(storage)
    n = await mm.seed_if_empty()
    assert n > 30
    by_cat = mm.stats()["by_category"]
    assert by_cat["wissen"] > 30 and by_cat["strategie"] >= 7
    strat = mm.list(category="strategie", limit=100)
    assert all("Marktphasen:" in s["text"] for s in strat)
    assert await mm.seed_if_empty() == 0

    import xrz.strategies as strategies

    extra = dict(strategies.STRATEGIES)
    extra["vwap_bounce"] = SimpleNamespace(
        name="vwap_bounce", label="VWAP-Abpraller", description="Kauft Rücksetzer an den VWAP.",
        good_in=["aufwärts"], category="Volumen",
    )
    monkeypatch.setattr(strategies, "STRATEGIES", extra)
    assert await mm.seed_if_empty() == 1
    hit = await mm.search("VWAP Abpraller", category="strategie")
    assert hit[0]["text"] == "VWAP-Abpraller: Kauft Rücksetzer an den VWAP. (Marktphasen: aufwärts; Typ: Volumen)"


async def test_seed_search_finds_knowledge(storage):
    mm = make(storage)
    await mm.seed_if_empty()
    await mm.add("Bei Bitcoin am Wochenende keine Ausbrüche handeln, die Liquidität ist zu gering.")
    hits = await mm.search("Bitcoin Wochenende Ausbruch", 3)
    assert hits and "Wochenende" in hits[0]["text"]
    assert await mm.search("Stop-Loss ATR Position", 3)


# --------------------------------------------------------------------------- #
# Fakten-Extraktion
# --------------------------------------------------------------------------- #
async def test_extraction_add_update_delete_none(storage, clock):
    mm = make(storage, clock=clock)
    old_btc = await mm.add("Der Nutzer handelt am liebsten Bitcoin.", "vorliebe")
    old_city = await mm.add("Der Nutzer wohnt in Hamburg.", "profil", importance=0.8)

    def reply(prompt):
        assert f"[{old_city}]" in prompt and f"[{old_btc}]" in prompt  # ähnlichste Erinnerungen mitgegeben
        return ops(
            {"op": "UPDATE", "id": old_city, "text": "Der Nutzer wohnt in München.", "category": "profil"},
            {"op": "DELETE", "id": old_btc},
            {"op": "ADD", "text": "Der Nutzer handelt lieber Ethereum.", "category": "vorliebe", "importance": 0.7},
            {"op": "NONE"},
            {"op": "ADD", "text": "Der Nutzer besitzt eine Yacht.", "category": "fakt"},  # nur von Vincent behauptet
            {"op": "UPDATE", "id": 9999, "text": "Unbekannte id."},
            {"op": "DELETE", "id": str(old_city + 1000)},
        )

    router = FakeRouter(extract=reply)
    mm.router = router
    await mm.remember_turn(
        "text",
        "Ich wohne jetzt in München. Bitcoin interessiert mich nicht mehr, lieber Ethereum.",
        "Alles klar! Übrigens: Du besitzt doch eine Yacht, oder?",
    )
    assert len(router.prompts("extract")) == 1
    active = texts(mm)
    assert "Der Nutzer wohnt in München." in active
    assert "Der Nutzer handelt lieber Ethereum." in active
    assert "Der Nutzer wohnt in Hamburg." not in active
    assert mm.get(old_btc) is None
    assert not any("Yacht" in t for t in active)

    # UPDATE behält den Verlauf: alte Fassung ist ersetzt und unsichtbar
    new_city = next(m for m in mm.list() if m["text"] == "Der Nutzer wohnt in München.")
    old = mm.get(old_city)
    assert old["superseded_by"] == new_city["id"]
    assert new_city["importance"] == pytest.approx(0.8)
    assert "Der Nutzer wohnt in Hamburg." in texts(mm, include_superseded=True)
    assert await mm.search("Hamburg") == []
    assert mm.store.episode_counts()["episodes"] == 1


async def test_parse_operations_is_strict():
    with pytest.raises(ExtractionError):
        parse_operations("Das ist kein JSON", set())
    with pytest.raises(ExtractionError):
        parse_operations('{"facts": []}', set())
    with pytest.raises(ExtractionError):
        parse_operations('{"operations": ["ADD"]}', set())
    raw = '```json\n{"operations": [{"op": "add", "text": "Der Nutzer mag Gold.", "category": "quatsch", "importance": 7}]}\n```'
    (op,) = parse_operations(raw, set())
    assert (op.op, op.category, op.importance) == ("ADD", "fakt", 1.0)
    listed = parse_operations('[{"op": "DELETE", "id": "3"}, {"op": "DELETE", "id": 4}, {"op": "UPDATE", "id": 3, "text": ""}]', {3})
    assert [(o.op, o.id) for o in listed] == [("DELETE", 3)]


async def test_secrets_are_never_stored(storage):
    router = FakeRouter(
        extract=ops(
            {"op": "ADD", "text": "Der API-Key des Nutzers ist sk-ant-api03-abcdefghijklmnopqrstuv.", "category": "fakt"},
            {"op": "ADD", "text": "Der Nutzer hat den API-Key [entfernt].", "category": "fakt"},
            {"op": "ADD", "text": "Der Nutzer mag Gold.", "category": "vorliebe"},
        )
    )
    mm = make(storage, router)
    await mm.remember_turn("text", "Mein API-Key ist sk-ant-api03-abcdefghijklmnopqrstuv und ich mag Gold.", "Notiert.")
    assert texts(mm) == ["Der Nutzer mag Gold."]
    assert "sk-ant" not in router.prompts("extract")[0]  # Geheimnis geht nicht einmal an die KI
    ep = mm.recent_episodes()[0]
    assert "sk-ant" not in ep["user_text"] and "[entfernt]" in ep["user_text"]

    # ohne KI (Heuristik) ebenso
    mm2 = make(storage)
    await mm2.remember_turn("text", "Merk dir: mein Passwort ist Sommer2024!", "")
    assert not any("Sommer2024" in t for t in texts(mm2))
    item = await mm2.add("Telegram-Token: 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0")
    assert "AAHdq" not in mm2.get(item)["text"]


def test_secret_detection():
    assert contains_secret("mein API-Key ist sk-proj-abc123def456ghi")
    assert contains_secret("PIN: 4711")
    assert contains_secret("Meine IBAN ist DE89 3704 0044 0532 0130 00")
    assert contains_secret("Karte 4111 1111 1111 1111")
    assert not contains_secret("Die Pin-Bar ist ein Kerzenmuster.")
    assert not contains_secret("Der Token ist stark gestiegen.")
    assert not contains_secret("Ich riskiere maximal 1 % pro Trade bei BTC/USDT.")
    assert redact_secrets("Passwort: hunter22 bitte") == "Passwort: [entfernt] bitte"


async def test_heuristic_fallback_without_llm(storage):
    mm = make(storage)  # kein Router
    await mm.remember_turn("voice", "Ich heiße Louis. Meine Regel: nie mehr als 1 % pro Trade riskieren.", "Gemerkt!")
    by_cat = {m["category"]: m["text"] for m in mm.list()}
    assert by_cat["profil"] == "Der Nutzer heißt Louis."
    assert by_cat["regel"] == "Regel des Nutzers: nie mehr als 1 % pro Trade riskieren."
    await mm.remember_turn("voice", "Hallo Vincent!", "Hallo!")
    await mm.remember_turn("voice", "Wie steht Bitcoin gerade?", "Bei 65.000 Dollar.")
    assert mm.count() == 2
    assert mm.store.episode_counts()["episodes"] == 3
    assert heuristic_operations("Magst du Gold? Keine Ahnung.") == []


@pytest.mark.parametrize("failure", ["Das ist kein JSON, sorry.", LLMError("offline"), RuntimeError("kaputt")])
async def test_llm_failure_falls_back_to_heuristic(storage, failure):
    router = FakeRouter(extract=failure)
    mm = make(storage, router)
    await mm.remember_turn("text", "Merk dir, dass ich nur Spot handle und keine Futures.", "Okay.")
    assert texts(mm) == ["Nutzer: ich nur Spot handle und keine Futures."]


async def test_explicit_request_honoured_when_llm_finds_nothing(storage):
    mm = make(storage, FakeRouter(extract=ops({"op": "NONE"})))
    await mm.remember_turn("text", "Vergiss nicht: Ich trade nie während der FOMC-Sitzung.", "Mache ich.")
    assert [m["category"] for m in mm.list()] == ["regel"]


async def test_extract_facts_disabled(storage):
    router = FakeRouter(extract=ops({"op": "ADD", "text": "Der Nutzer heißt Louis.", "category": "profil"}))
    mm = make(storage, router, extract_facts=False)
    await mm.remember_turn("text", "Ich heiße Louis.", "Hallo Louis!")
    assert mm.count() == 0 and router.calls == []
    assert mm.store.episode_counts()["episodes"] == 1


async def test_remember_turn_never_raises(storage, monkeypatch):
    mm = make(storage, FakeRouter(extract=RuntimeError("weg")))

    def boom(*a, **k):
        raise RuntimeError("Datenbank kaputt")

    monkeypatch.setattr(mm.store, "add_episode", boom)
    await mm.remember_turn("text", "Ich heiße Louis.", "Hallo")


async def test_concurrent_turns_are_serialized(storage):
    router = FakeRouter(
        extract=lambda p: ops({"op": "ADD", "text": "Der Nutzer handelt nur Gold und Silber.", "category": "vorliebe"}),
        delay=0.02,
    )
    mm = make(storage, router)
    await asyncio.gather(
        mm.remember_turn("text", "Ich handle nur Gold und Silber.", "Ok."),
        mm.remember_turn("telegram:1", "Wie gesagt: nur Gold und Silber handle ich.", "Verstanden."),
    )
    assert router.max_active == 1
    items = mm.list()
    assert [m["text"] for m in items] == ["Der Nutzer handelt nur Gold und Silber."]
    assert items[0]["access_count"] == 1  # zweites Mal verstärkt statt doppelt
    assert mm.store.episode_counts()["episodes"] == 2


# --------------------------------------------------------------------------- #
# Duplikate
# --------------------------------------------------------------------------- #
async def test_dedup_reinforces_instead_of_adding(storage, clock):
    mm = make(storage, clock=clock)
    a = await mm.add("Der Nutzer handelt nur Bitcoin und Ethereum am Wochenende.", "vorliebe", importance=0.6)
    clock.advance(hours=1)
    b = await mm.add("Am Wochenende handelt der Nutzer nur Bitcoin und Ethereum.", "notiz", importance=0.6)
    assert a == b and mm.count() == 1
    item = mm.get(a)
    assert item["importance"] == pytest.approx(0.65) and item["access_count"] == 1 and item["updated"] == clock()
    # andere Zahl oder Verneinung → kein Duplikat
    r1 = await mm.add("Der Nutzer riskiert pro Trade maximal 1 % seines Kontos.", "regel")
    r2 = await mm.add("Der Nutzer riskiert pro Trade maximal 2 % seines Kontos.", "regel")
    g1 = await mm.add("Der Nutzer mag Gold als Absicherung.", "vorliebe")
    g2 = await mm.add("Der Nutzer mag kein Gold als Absicherung.", "vorliebe")
    assert len({r1, r2, g1, g2}) == 4
    # Wissen und persönliche Erinnerungen werden nicht vermischt
    w = await mm.add("Der Nutzer handelt nur Bitcoin und Ethereum am Wochenende.", "wissen")
    assert w != a


def _topic_embed(text: str) -> list[float]:
    t = text.lower()
    groups = [("bitcoin", "krypto", "btc"), ("koch", "pasta", "italien"), ("jogg", "lauf", "sport"), ("krimi", "buch", "lies")]
    return [0.05] + [1.0 if any(w in t for w in g) else 0.0 for g in groups]


async def test_dedup_and_semantic_search_with_embeddings(storage):
    router = FakeRouter(embed_fn=_topic_embed)
    mm = make(storage, router)
    a = await mm.add("Der Nutzer hält Bitcoin langfristig.", "vorliebe")
    b = await mm.add("Langfristige BTC-Position ist dem Nutzer wichtig.", "vorliebe")  # Cosinus ≈ 1 → Duplikat
    assert a == b
    await mm.add("Der Nutzer kocht gerne italienisch.", "vorliebe")
    await mm.add("Der Nutzer joggt morgens.", "vorliebe")
    await mm.add("Der Nutzer liest Krimis.", "vorliebe")
    assert mm.stats()["embedded"] == 4
    hits = await mm.search("Was denkst du über Krypto?")  # kein gemeinsames Wort, aber gleiche Bedeutung
    assert [h["id"] for h in hits] == [a]


async def test_reembed_missing(storage):
    router = FakeRouter()
    mm = make(storage, router)
    await mm.add("Der Nutzer liest Krimis.")
    assert mm.stats()["embedded"] == 0
    router.embed_fn = _topic_embed
    assert await mm.reembed_missing() == 1
    assert mm.stats()["embedded"] == 1


# --------------------------------------------------------------------------- #
# Abruf
# --------------------------------------------------------------------------- #
async def test_retrieval_ranking(storage, clock):
    mm = make(storage, clock=clock)
    rel = await mm.add("Bei Bitcoin am Wochenende keine Ausbrüche handeln, die Liquidität ist zu gering.")
    irrelevant = await mm.add("Der Nutzer kocht gerne italienisch.", "vorliebe")
    hits = await mm.search("Bitcoin Wochenende Ausbruch")
    assert [h["id"] for h in hits] == [rel]
    assert set(hits[0]) >= {"id", "text", "category", "importance", "score", "source", "created", "updated", "pinned"}
    assert irrelevant not in [h["id"] for h in hits]
    item = mm.get(rel)
    assert item["access_count"] == 1 and item["last_access"] == clock()

    low = await mm.add("Ethereum Staking ist riskant.", importance=0.1)
    high = await mm.add("Ethereum Staking bringt jährliche Zinsen.", importance=0.9)
    ranked = [h["id"] for h in await mm.search("Ethereum Staking")]
    assert ranked == [high, low]
    assert await mm.search("") == []


async def test_recency_decay_in_ranking(storage, clock):
    assert recency(clock() - 30 * DAY, clock()) == pytest.approx(0.5)
    mm = make(storage, clock=clock)
    old = await mm.add("Gold steigt bei Krisen.", importance=0.5)
    clock.advance(days=120)
    new = await mm.add("Gold fällt bei Zinsen.", importance=0.5)
    hits = await mm.search("Gold")
    assert [h["id"] for h in hits] == [new, old]
    assert hits[0]["score"] > hits[1]["score"]


async def test_context_for_includes_profile_rules_and_respects_budget(storage):
    mm = make(storage)
    mm.set_profile("- Name: Louis\n- Ziel: ruhiges Swing-Trading")
    rule = await mm.add("Niemals mehr als 2 % pro Trade riskieren.", "regel", pinned=True)
    pinned_note = await mm.add("Steuerberater Termin immer im März.", "notiz", pinned=True)
    await mm.add("Ichimoku-Wolke als Trendfilter nutzen.", "wissen")
    ctx = await mm.context_for("Wie ist das Wetter morgen?")
    assert ctx.startswith("Kernprofil:\n- Name: Louis")
    assert "Wichtige Regeln:\n- Niemals mehr als 2 % pro Trade riskieren." in ctx
    assert "Steuerberater" in ctx and "Relevante Erinnerungen" not in ctx
    assert mm.get(rule)["access_count"] == 1 and mm.get(pinned_note)["access_count"] == 1

    ctx = await mm.context_for("Ichimoku Wolke Trend")
    assert "Relevante Erinnerungen:\n- [wissen] Ichimoku-Wolke als Trendfilter nutzen." in ctx

    for i in range(40):
        await mm.add(f"Ichimoku Erfahrung {i}: " + "Die Wolke hat beim Trend sehr geholfen. " * 8, "lektion")
    for budget in (200, 400, 800, 2500):
        ctx = await mm.context_for("Ichimoku Wolke Trend", max_chars=budget)
        assert len(ctx) <= budget
    assert "Niemals mehr als 2 %" in await mm.context_for("Ichimoku", max_chars=400)


# --------------------------------------------------------------------------- #
# Schlafphase
# --------------------------------------------------------------------------- #
def _insert(mm: MemoryManager, text: str, category: str = "notiz", importance: float = 0.5, pinned: bool = False, access: int = 0) -> int:
    return mm.store.insert(text, category, importance, "test", pinned=pinned, access_count=access)


async def test_consolidation_decay(storage, clock):
    mm = make(storage, clock=clock)
    stale = await mm.add("Alte Notiz über Silber.", importance=0.5)
    pinned = await mm.add("Angeheftete Notiz über Platin.", importance=0.5, pinned=True)
    floor = await mm.add("Fast vergessene Notiz über Kupfer.", importance=0.05)
    clock.advance(days=40)
    report = await mm.consolidate()
    assert "abgeschwächt" in report
    assert mm.get(stale)["importance"] == pytest.approx(0.45)
    assert mm.get(pinned)["importance"] == pytest.approx(0.5)
    assert mm.get(floor)["importance"] == pytest.approx(0.05)
    clock.advance(hours=1)
    await mm.consolidate()
    assert mm.get(stale)["importance"] == pytest.approx(0.45)  # nicht mehrfach am selben Tag
    clock.advance(hours=13)
    await mm.consolidate()
    assert mm.get(stale)["importance"] == pytest.approx(0.405)


async def test_consolidation_merges_without_llm(storage):
    mm = make(storage)
    a = _insert(mm, "Der Nutzer trinkt morgens Kaffee vor dem Trading.", "vorliebe", 0.5, access=1)
    b = _insert(mm, "Der Nutzer trinkt morgens Kaffee vor dem Trading, immer.", "vorliebe", 0.6, access=2)
    c = _insert(mm, "Der Nutzer trinkt morgens vor dem Trading Kaffee.", "vorliebe", 0.4)
    other = _insert(mm, "Der Nutzer mag Tee am Abend.", "vorliebe")
    report = await mm.consolidate()
    assert "3 ähnliche Erinnerungen zu 1 zusammengeführt" in report
    assert mm.get(a)["superseded_by"] == b and mm.get(c)["superseded_by"] == b
    kept = mm.get(b)
    assert kept["superseded_by"] is None and kept["importance"] == pytest.approx(0.65) and kept["access_count"] == 3
    assert mm.get(other)["superseded_by"] is None
    assert mm.count() == 2


async def test_consolidation_merges_with_llm(storage):
    router = FakeRouter(merge="Der Nutzer trinkt jeden Morgen vor dem Trading Kaffee.")
    mm = make(storage, router)
    ids = [
        _insert(mm, "Der Nutzer trinkt morgens Kaffee vor dem Trading.", "vorliebe", access=1),
        _insert(mm, "Der Nutzer trinkt morgens vor dem Trading Kaffee.", "vorliebe", access=1),
    ]
    await mm.consolidate()
    assert len(router.prompts("merge")) == 1
    (merged,) = [m for m in mm.list() if m["category"] == "vorliebe"]
    assert merged["text"] == "Der Nutzer trinkt jeden Morgen vor dem Trading Kaffee."
    assert merged["access_count"] == 2 and merged["source"] == "konsolidiert"
    assert all(mm.get(i)["superseded_by"] == merged["id"] for i in ids)


async def test_eviction_respects_cap_and_pinned(storage):
    mm = make(storage, max_memories=5)
    notes = [_insert(mm, f"Notiz Nummer {i} zum Thema {w}.", "notiz", 0.1 + i * 0.05) for i, w in enumerate("abcdefghij")]
    pins = [_insert(mm, f"Angeheftet {i}.", "notiz", 0.05, pinned=True) for i in range(2)]
    rules = [_insert(mm, f"Regel {i}: Stop setzen.", "regel", 0.05) for i in range(2)]
    report = await mm.consolidate()
    assert "9 unwichtige vergessen" in report
    assert mm.count() == 5
    remaining = {m["id"] for m in mm.list()}
    assert set(pins) | set(rules) <= remaining
    assert notes[-1] in remaining  # die wichtigste Notiz bleibt

    # auch zwischen den Schlafphasen läuft das Gedächtnis nicht über
    for i in range(6):
        await mm.add(f"Zusatz {i} über {'xyzuvw'[i]}-Märkte.", importance=0.9)
    assert mm.count() <= 5 * 1.1


async def test_episode_summaries_and_deletion_window(storage, clock):
    router = FakeRouter(summary="Der Nutzer fragte nach Bitcoin und plante einen Swing-Trade.")
    mm = make(storage, router, clock=clock, extract_facts=False)
    start = clock()
    clock.t = start - 40 * DAY
    await mm.remember_turn("text", "Wie steht Bitcoin?", "Bei 60.000.")
    clock.t = start - 2 * DAY
    await mm.remember_turn("text", "Plane einen Swing-Trade.", "Gute Idee.")
    await mm.remember_turn("telegram:1", "Status?", "Alles ruhig.")
    clock.t = start
    await mm.remember_turn("text", "Und heute?", "Heute nichts.")

    report = await mm.consolidate()
    assert "3 Gesprächstage zusammengefasst" in report and "1 alte Gesprächsrunden gelöscht" in report
    assert len(router.prompts("summary")) == 3
    summaries = mm.store.summaries()
    assert {(s["day"], s["channel"]) for s in summaries} == {
        (day_key(start - 40 * DAY), "text"), (day_key(start - 2 * DAY), "text"), (day_key(start - 2 * DAY), "telegram:1"),
    }
    eps = mm.recent_episodes(limit=10)
    assert sorted((e["user_text"], e["summarized"]) for e in eps) == [
        ("Plane einen Swing-Trade.", 1), ("Status?", 1), ("Und heute?", 0),
    ]
    events = mm.list(category="ereignis")
    assert len(events) == 2  # gleiche Zusammenfassung für zwei Tage desselben Kanals → verstärkt
    assert all(e["text"].startswith("Gespräch am ") for e in events)


async def test_episode_summary_fallback_without_llm(storage, clock):
    mm = make(storage, clock=clock)
    clock.advance(days=-3)
    await mm.remember_turn("text", "Hallo, zeig mir den DAX.", "Hier ist der DAX.")
    clock.advance(days=3)
    await mm.consolidate()
    (summary,) = mm.store.summaries()
    assert summary["summary"].startswith("1 Gesprächsrunden. Themen: Hallo, zeig mir den DAX.")


async def test_profile_rebuild_respects_limit(storage):
    mm = make(storage, profile_max_chars=200)
    for i, cat in enumerate(["profil", "regel", "vorliebe"] * 4):
        _insert(mm, f"Eintrag {i} ({cat}): " + "wichtige Information " * 2, cat, 0.5 + i / 100)
    report = await mm.consolidate()
    assert "Profil aktualisiert" in report
    profile = mm.profile()
    assert 0 < len(profile) <= 200 and profile.startswith("- Eintrag")
    assert mm.stats()["profile_chars"] == len(profile)
    # unverändert → kein neuer Profil-Lauf
    assert "Profil aktualisiert" not in await mm.consolidate()

    long_router = FakeRouter(profile="\n".join(f"- Punkt {i}: " + "x" * 30 for i in range(50)))
    mm2 = make(storage, long_router, profile_max_chars=200)
    _insert(mm2, "Der Nutzer heißt Louis.", "profil", 0.9)
    await mm2.consolidate()
    assert len(long_router.prompts("profile")) == 1
    assert 0 < len(mm2.profile()) <= 200 and mm2.profile().startswith("- Punkt 0")

    mm2.set_profile("Handgeschrieben: " + "y" * 500)
    assert len(mm2.profile()) <= 200
    mm3 = make(storage, profile_max_chars=300)
    mm3.set_profile("- Von Hand: mag ruhige Märkte")
    _insert(mm3, "Der Nutzer wohnt in Köln.", "profil", 0.95)
    await mm3.consolidate()
    assert mm3.profile().startswith("- Von Hand: mag ruhige Märkte") and "Köln" in mm3.profile()
    assert len(mm3.profile()) <= 300


async def test_maybe_consolidate(storage, clock):
    mm = make(storage, clock=clock, consolidate_hours=24)
    assert mm.stats()["last_consolidation"] is None
    assert (await mm.maybe_consolidate()).startswith("Schlafphase abgeschlossen")
    assert mm.stats()["last_consolidation"] == clock()
    clock.advance(hours=23)
    assert await mm.maybe_consolidate() is None
    clock.advance(hours=2)
    assert await mm.maybe_consolidate() is not None


# --------------------------------------------------------------------------- #
# Einlesen
# --------------------------------------------------------------------------- #
def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


async def test_ingest_bytes_formats(storage):
    mm = make(storage)
    txt = "\n\n".join(f"Absatz {i}: Der Hammer ist ein Umkehrmuster an Unterstützungen. " + "Mehr Text. " * 20 for i in range(6))
    n = await mm.ingest_bytes(txt.encode(), "kerzen.txt")
    assert n > 1 and mm.count("wissen") == n
    assert all(m["source"] == "dokument:kerzen.txt" for m in mm.list(category="wissen", limit=50))
    # gleiches Dokument erneut → verstärkt, nicht doppelt
    assert await mm.ingest_bytes(txt.encode(), "kerzen.txt") == n
    assert mm.count("wissen") == n

    assert await mm.ingest_bytes("# Fibonacci\n\nDas 61,8-%-Retracement ist beliebt.".encode(), "fibo.md", source="telegram") == 1
    html = b"<html><head><script>evil()</script></head><body><h1>VWAP</h1><p>Der VWAP &amp; das Volumenprofil.</p></body></html>"
    assert await mm.ingest_bytes(html, "seite.html") == 1
    hits = await mm.search("VWAP Volumenprofil")
    assert hits and "evil" not in hits[0]["text"] and "VWAP & das Volumenprofil" in hits[0]["text"]
    assert (await mm.search("Fibonacci Retracement"))[0]["source"] == "telegram:fibo.md"

    with pytest.raises(ValueError):
        await mm.ingest_bytes(b"\x00\x01\x02binary", "bild.png")
    with pytest.raises(ValueError):
        await mm.ingest_bytes(b"   ", "leer.txt")


async def test_ingest_pdf(storage):
    pytest.importorskip("pypdf")
    mm = make(storage)
    assert await mm.ingest_bytes(_pdf("Die Ichimoku Wolke zeigt den Trend"), "ichimoku.pdf") == 1
    assert "Ichimoku Wolke" in (await mm.search("Ichimoku Wolke"))[0]["text"]
    with pytest.raises(ValueError):
        await mm.ingest_bytes(b"%PDF-1.4 kaputt", "kaputt.pdf")


async def test_ingest_url(storage, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "127.0.0.1":
            raise AssertionError("lokale Adresse darf nie abgefragt werden")
        if path == "/artikel":
            body = "<html><body><p>Die Marktstruktur zeigt höhere Hochs und höhere Tiefs.</p></body></html>"
            return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})
        if path == "/doc.pdf":
            return httpx.Response(200, content=_pdf("Funding Rate bei Perpetuals"), headers={"content-type": "application/pdf"})
        if path == "/umleitung":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8765/api/state"})
        if path == "/bild":
            return httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"})
        return httpx.Response(404, text="weg")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    mm = make(storage)
    assert await mm.ingest_url("https://example.com/artikel") == 1
    hit = (await mm.search("Marktstruktur Hochs"))[0]
    assert hit["source"] == "web:https://example.com/artikel" and hit["category"] == "wissen"
    if __import__("importlib").util.find_spec("pypdf"):
        assert await mm.ingest_url("https://example.com/doc.pdf") == 1
    for bad in ("ftp://example.com/x", "file:///etc/passwd", "http://localhost:8765/api", "https://example.com/umleitung",
                "https://example.com/fehlt", "https://example.com/bild"):
        with pytest.raises(ValueError):
            await mm.ingest_url(bad)


def test_chunking_and_html():
    text = "\n\n".join(f"Absatz {i} " + "x" * 300 for i in range(10))
    chunks = chunk_text(text, size=900)
    assert len(chunks) > 1 and all(len(c) <= 900 for c in chunks)
    long = "Satz mit Inhalt. " * 200
    assert all(len(c) <= 900 for c in chunk_text(long))
    assert html_to_text("<p>Hallo<script>evil()</script> <b>Welt</b></p>") == "Hallo Welt"
    assert tokenize("Ausbrüche bei BTC/USDT am Wochenende") == ["ausbruch", "btc/usdt", "btc", "usdt", "wochenend"]


# --------------------------------------------------------------------------- #
# Verwalten
# --------------------------------------------------------------------------- #
async def test_crud_list_and_stats(storage, clock):
    mm = make(storage, clock=clock)
    a = await mm.add("Der Nutzer handelt gern den DAX.", "vorliebe")
    b = await mm.add("Lektion: Nie gegen den Trend shorten.", "notiz")
    lesson = await mm.add_lesson("BTC: Ausbrüche ohne Volumen meiden.")
    assert mm.recent_lessons(5) == ["BTC: Ausbrüche ohne Volumen meiden."]
    assert mm.get(lesson)["category"] == "lektion"

    assert mm.update(a, text="Der Nutzer handelt gern den DAX und den S&P 500.", importance=1.7, pinned=True)
    item = mm.get(a)
    assert item["text"].endswith("S&P 500.") and item["importance"] == 1.0 and item["pinned"]
    assert mm.update(b, category="regel") and mm.get(b)["category"] == "regel"
    assert not mm.update(b, category="quatsch")
    assert not mm.update(b, text="   ")
    assert not mm.update(99999, importance=0.3)
    assert (await mm.search("DAX S&P"))[0]["id"] == a  # Text-Änderung ist sofort suchbar

    assert [m["id"] for m in mm.list(category="regel")] == [b]
    assert [m["id"] for m in mm.list(q="dax 500")] == [a]
    assert mm.list(limit=1)[0]["id"] == a  # angeheftete zuerst
    assert len(mm.list(limit=2, offset=1)) == 2

    stats = mm.stats()
    assert stats["total"] == 3 and stats["by_category"] == {"vorliebe": 1, "regel": 1, "lektion": 1}
    assert stats["pinned"] == 1 and stats["profile_chars"] == 0 and stats["last_consolidation"] is None

    assert mm.delete(b) and not mm.delete(b)
    assert mm.count() == 2

    with pytest.raises(ValueError):
        await mm.add("Text", category="unbekannt")
    with pytest.raises(ValueError):
        await mm.add("   ")
    long_id = await mm.add("Langer Text über Risiko. " * 200, "wissen")
    assert mm.count("wissen") > 1 and mm.get(long_id)["category"] == "wissen"
