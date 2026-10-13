import json
import os
import stat
import sys

import pytest

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


def test_telegram_chunking():
    parts = _chunks("a\n" * 5000, 3900)
    assert all(len(p) <= 3900 for p in parts) and "".join(parts).count("a") == 5000


def test_event_bus_threadsafe_emit():
    import asyncio
    import threading

    from xrz.events import EventBus

    async def main():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        got = []

        async def handler(t, d):
            got.append((t, d))

        bus.subscribe(handler)
        th = threading.Thread(target=lambda: bus.emit("voice.level", {"level": 0.5}))
        th.start()
        th.join()
        bus.emit("chat", {"text": "hi"})
        await bus.notify("trade", "Kauf", {"x": 1})
        for _ in range(20):
            if len(got) >= 3:
                break
            await asyncio.sleep(0.01)
        types = [t for t, _ in got]
        assert "voice.level" in types and "chat" in types and "notify" in types

    asyncio.run(main())
