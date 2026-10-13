import json
import os
import stat
import sys

import pytest

from xrz.ai.knowledge import KnowledgeBase, chunk_text, html_to_text
from xrz.ai.llm import ToolSpec, _run_tool, clean_text, extract_json
from xrz.config import ConfigManager, set_path
from xrz.interfaces.telegram_bot import _chunks
from xrz.storage import Storage


def test_config_roundtrip_and_validation(data_dir):
    cm = ConfigManager(data_dir)
    assert cm.path.exists()
    set_path(cm.settings, "risk.risk_per_trade_pct", "0,5")
    set_path(cm.settings, "trading.allow_short", "ja")
    cm.save()
    cm2 = ConfigManager(data_dir)
    assert cm2.settings.risk.risk_per_trade_pct == 0.5
    assert cm2.settings.trading.allow_short is True
    with pytest.raises(ValueError):
        set_path(cm.settings, "risk.risk_per_trade_pct", "50")  # harte Obergrenze
    with pytest.raises(KeyError):
        set_path(cm.settings, "risk.gibtsnicht", "1")
    with pytest.raises(ValueError):
        cm.update({"risk": {"max_drawdown_pct": 99}})


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX-Rechte")
def test_secrets_file_is_private(data_dir):
    cm = ConfigManager(data_dir)
    cm.secrets.set("telegram_token", "123:ABC")
    mode = stat.S_IMODE(os.stat(cm.secrets.path).st_mode)
    assert mode == 0o600
    assert cm.secrets.get("telegram_token") == "123:ABC"
    masked = cm.secrets.masked()["telegram_token"]
    assert masked["set"] and "ABC" not in json.dumps(masked["preview"])[1:4]
    with pytest.raises(KeyError):
        cm.secrets.set("unbekannt", "x")


def test_extract_json_variants():
    assert extract_json('{"approve": false}') == {"approve": False}
    assert extract_json('<think>hmm {x}</think> Ergebnis: ```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Text davor {"a": {"b": 2}} danach') == {"a": {"b": 2}}
    assert extract_json("kein json") is None
    assert clean_text("<think>abc</think>Hallo") == "Hallo"


async def test_tool_runner_handles_errors():
    async def boom(_):
        raise RuntimeError("kaputt")

    async def ok(a):
        return f"hi {a.get('x')}"

    tools = {"boom": ToolSpec("boom", "", {}, boom), "ok": ToolSpec("ok", "", {}, ok)}
    assert (await _run_tool(tools, "ok", '{"x": 1}')) == ("hi 1", False)
    out, err = await _run_tool(tools, "boom", {})
    assert err and "kaputt" in out
    out, err = await _run_tool(tools, "fehlt", {})
    assert err


def test_chunking():
    text = "\n\n".join(f"Absatz {i} " + "x" * 300 for i in range(10))
    chunks = chunk_text(text, size=900)
    assert len(chunks) > 1 and all(len(c) <= 900 for c in chunks)
    assert html_to_text("<p>Hallo<script>evil()</script> <b>Welt</b></p>") == "Hallo Welt"
    parts = _chunks("a\n" * 5000, 3900)
    assert all(len(p) <= 3900 for p in parts) and "".join(parts).count("a") == 5000


async def test_knowledge_search(tmp_path):
    st = Storage(tmp_path / "k.db")
    kb = KnowledgeBase(st)
    n = await kb.seed_if_empty()
    assert n > 10
    await kb.add("Bei Bitcoin am Wochenende keine Ausbrüche handeln, die Liquidität ist zu gering.", topic="notiz")
    hits = await kb.search("Bitcoin Wochenende Ausbruch", 3)
    assert hits and "Wochenende" in hits[0]["text"]
    hits = await kb.search("Stop-Loss ATR Position", 3)
    assert hits
    assert kb.delete(hits[0]["id"])
    st.close()


def test_storage_trades_and_stats(tmp_path):
    from xrz.storage import Trade

    st = Storage(tmp_path / "t.db")
    t = st.insert_trade(Trade(mode="paper", symbol="BTC/USDT", side="long", qty=1, entry_price=100, entry_time=1, strategies={"a": 1}))
    assert st.open_trades("paper")[0].strategies == {"a": 1}
    t.status, t.pnl, t.exit_time, t.r_multiple = "closed", 10, 2, 1
    st.update_trade(t)
    s = st.trade_stats("paper")
    assert s["trades"] == 1 and s["win_rate"] == 100
    st.kv_set("x", {"a": [1, 2]})
    assert st.kv_get("x") == {"a": [1, 2]}
    st.close()
