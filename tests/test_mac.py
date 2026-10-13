"""Tests der Mac-Steuerung mit Attrappen (läuft auf Linux, ohne macOS-APIs)."""
from __future__ import annotations

import base64
import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from xrz.config import Settings
from xrz.mac import MacControl
from xrz.mac import computer_use as cu
from xrz.mac import control as control_mod
from xrz.mac import permissions as perms
from xrz.mac import screen as screen_mod
from xrz.mac.computer_use import NOT_EXECUTED, MacExecutor, describe_action, run_computer_task
from xrz.mac.inputs import (
    FakeInputs,
    InputUnavailable,
    KeyCombo,
    QuartzInputs,
    parse_combo,
    parse_key_sequence,
    parse_modifiers,
    resolve_key,
    split_typing,
)
from xrz.mac.screen import Screenshot, ScreenCaptureError


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #
def make_png(width: int, height: int, blank: bool = False) -> bytes:
    """Minimales RGB-PNG: einfarbig oder mit vielen verschiedenen Werten."""
    row_len = width * 3
    row = bytes(row_len) if blank else (bytes(range(256)) * (row_len // 256 + 1))[:row_len]
    raw = (b"\x00" + row) * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


SHOT_PNG = make_png(32, 20)
# Bildschirm 1440×900 Punkte, Foto 1280×800 Pixel → Skalierung 0,888…
SCALE = 1280 / 1440


class FakeCapture:
    def __init__(self, png: bytes = SHOT_PNG):
        self.png = png
        self.calls: list[tuple[int, object]] = []

    async def __call__(self, max_long_edge: int, region=None) -> Screenshot:
        self.calls.append((max_long_edge, region))
        return Screenshot(png=self.png, width=1280, height=800, scale=SCALE)


def tool_use(tid: str, name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=args, toolset_name="computer")


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def response(stop_reason: str, *blocks) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeClient:
    """Liefert vorbereitete Antworten und merkt sich jede Anfrage."""

    def __init__(self, responses=None, repeat=None, error: Exception | None = None):
        self._responses = list(responses or [])
        self._repeat = repeat
        self._error = error
        self.calls: list[dict] = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if self._error is not None:
            raise self._error
        if self._responses:
            return self._responses.pop(0)
        return self._repeat()


def executor(inputs: FakeInputs | None = None, capture: FakeCapture | None = None) -> MacExecutor:
    return MacExecutor(inputs or FakeInputs(), capture or FakeCapture(), settle_seconds=0)


async def run(client: FakeClient, ex: MacExecutor, max_steps: int = 10, **kw) -> str:
    return await run_computer_task(client, "claude-opus-5-5", "high", "Öffne Safari", ex, max_steps, **kw)


def last_results(client: FakeClient) -> list[dict]:
    msg = client.calls[-1]["messages"][-1]
    assert msg["role"] == "user"
    return msg["content"]


# --------------------------------------------------------------------------- #
# Schleife der Computersteuerung
# --------------------------------------------------------------------------- #
async def test_single_action_and_request_shape():
    first = response("tool_use", text_block("Ich klicke auf Safari."),
                     tool_use("t1", "left_click", coordinate=[640, 400]))
    client = FakeClient([first, response("end_turn", text_block("Safari ist offen."))])
    inputs = FakeInputs()
    steps: list[str] = []

    result = await run(client, executor(inputs), on_step=steps.append)

    assert result == "Safari ist offen."
    assert inputs.calls == [("click", 720.0, 450.0, "left", 1, ())]
    assert steps == ["Klicke bei (640, 400)"]
    call = client.calls[0]
    assert call["tools"] == [{"type": "computer_toolset_20260801"}]
    assert call["max_tokens"] == 16000 and call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "high"}
    assert "passwords" in call["system"]
    # Antwort unverändert als Assistenten-Nachricht angehängt
    assert client.calls[1]["messages"][1] == {"role": "assistant", "content": first.content}
    assert last_results(client) == [
        {"type": "tool_result", "tool_use_id": "t1", "toolset_name": "computer", "content": "OK"}
    ]


async def test_batch_stops_at_first_failure():
    batch = response(
        "tool_use",
        tool_use("a", "left_click", coordinate=[10, 10]),
        tool_use("b", "key", text="cmd+gibtsnicht"),
        tool_use("c", "type", text="hallo"),
    )
    client = FakeClient([batch, response("end_turn", text_block("Fertig."))])
    inputs = FakeInputs()

    assert await run(client, executor(inputs)) == "Fertig."

    results = last_results(client)
    assert [r["tool_use_id"] for r in results] == ["a", "b", "c"]
    assert all(r["toolset_name"] == "computer" for r in results)
    assert results[0]["content"] == "OK" and "is_error" not in results[0]
    assert results[1]["is_error"] is True and "gibtsnicht" in results[1]["content"]
    assert results[2] == {"type": "tool_result", "tool_use_id": "c", "toolset_name": "computer",
                          "content": NOT_EXECUTED, "is_error": True}
    assert [c[0] for c in inputs.calls] == ["click"]  # „hallo“ wurde nie getippt


async def test_screenshot_and_zoom_return_image_blocks():
    capture = FakeCapture()
    batch = response("tool_use", tool_use("s", "screenshot"), tool_use("z", "zoom", region=[0, 0, 640, 400]))
    client = FakeClient([batch, response("end_turn", text_block("Gesehen."))])

    await run(client, executor(capture=capture))

    for result in last_results(client):
        assert result["toolset_name"] == "computer"
        image = result["content"][0]
        assert image["type"] == "image"
        assert image["source"]["media_type"] == "image/png"
        assert base64.b64decode(image["source"]["data"]) == SHOT_PNG
    # Zoom-Bereich (Pixel) wird in Bildschirm-Punkte umgerechnet
    assert capture.calls[1] == (1568, (0.0, 0.0, 720.0, 450.0))


async def test_sdk_blocks_with_pydantic_input():
    from anthropic.types import ComputerLeftClickToolUseBlock

    block = ComputerLeftClickToolUseBlock.model_validate({
        "id": "tu", "type": "tool_use", "name": "left_click", "toolset_name": "computer",
        "caller": {"type": "direct"}, "input": {"coordinate": [128, 80], "text": "cmd"},
    })
    client = FakeClient([response("tool_use", block), response("end_turn", text_block("ok"))])
    inputs = FakeInputs()

    await run(client, executor(inputs))

    assert inputs.calls == [("click", 144.0, 90.0, "left", 1, ("cmd",))]


async def test_refusal_stops_without_actions():
    client = FakeClient([response("refusal")])
    inputs = FakeInputs()
    result = await run(client, executor(inputs))
    assert "abgelehnt" in result
    assert inputs.calls == [] and len(client.calls) == 1


async def test_max_tokens_and_api_error():
    assert "abgeschnitten" in await run(FakeClient([response("max_tokens")]), executor())
    result = await run(FakeClient(error=RuntimeError("Netz weg")), executor())
    assert result.startswith("Die Mac-Steuerung wurde abgebrochen") and "Netz weg" in result


async def test_pause_turn_is_resent_unchanged():
    paused = response("pause_turn", text_block("…"))
    client = FakeClient([paused, response("end_turn", text_block("Erledigt!"))])
    assert await run(client, executor()) == "Erledigt!"
    second = client.calls[1]["messages"]
    assert second[-1] == {"role": "assistant", "content": paused.content}


async def test_max_steps_stops_gracefully():
    client = FakeClient(repeat=lambda: response("tool_use", tool_use("s", "screenshot")))
    capture = FakeCapture()
    result = await run(client, executor(capture=capture), max_steps=3)
    assert len(client.calls) == 3
    assert "3 Schritten" in result
    assert len(capture.calls) == 2  # Aktionen des letzten Zugs werden nicht mehr ausgeführt


async def test_image_limit_stops_before_sending_too_many_screenshots():
    shots = [tool_use(f"s{i}", "screenshot") for i in range(cu.MAX_IMAGES + 1)]
    client = FakeClient([response("tool_use", *shots)])
    result = await run(client, executor())
    assert len(client.calls) == 1
    assert "Bildschirmfotos aufgehört" in result


async def test_should_stop_between_actions():
    inputs = FakeInputs()
    batch = response("tool_use", tool_use("a", "type", text="eins"), tool_use("b", "type", text="zwei"))
    client = FakeClient([batch])
    result = await run(client, executor(inputs), should_stop=lambda: len(inputs.calls) >= 1)
    assert result == cu.STOPPED
    assert inputs.calls == [("type", "eins")]
    assert len(client.calls) == 1


async def test_screen_permission_error_ends_task():
    async def broken_capture(max_long_edge, region=None):
        raise ScreenCaptureError(perms.SCREEN_RECORDING_HINT)

    client = FakeClient([response("tool_use", tool_use("s", "screenshot"))])
    result = await run(client, MacExecutor(FakeInputs(), broken_capture))
    assert result == perms.SCREEN_RECORDING_HINT


async def test_async_on_step_and_descriptions():
    seen: list[str] = []

    async def on_step(text: str) -> None:
        seen.append(text)

    batch = response("tool_use", tool_use("w", "wait", duration=0), tool_use("k", "key", text="cmd+s", repeat=2))
    await run(FakeClient([batch, response("end_turn")]), executor(), on_step=on_step)
    assert seen == ["Warte 0 s", "Drücke cmd+s (2×)"]
    assert describe_action("scroll", {"scroll_direction": "down", "scroll_amount": 3, "coordinate": [5, 6]}) == \
        "Scrolle 3× nach unten bei (5, 6)"
    assert describe_action("type", {"text": "x" * 80}).endswith("…“")


# --------------------------------------------------------------------------- #
# Ausführung einzelner Aktionen / Koordinaten
# --------------------------------------------------------------------------- #
async def test_coordinate_scaling_and_actions():
    inputs = FakeInputs(position=(720.0, 450.0))
    ex = executor(inputs)
    assert await ex.to_points([640, 400]) == (720.0, 450.0)
    assert await ex.to_points([1280, 800]) == pytest.approx((1279 / SCALE, 799 / SCALE))
    with pytest.raises(cu.ActionError):
        await ex.to_points([1300, 10])
    with pytest.raises(cu.ActionError):
        await ex.to_points("640,400")

    assert (await ex.execute("cursor_position", {})).text == "X=640, Y=400"
    await ex.execute("double_click", {"coordinate": [0, 0]})
    await ex.execute("triple_click", {})
    await ex.execute("right_click", {"coordinate": [64, 80], "text": "ctrl+shift"})
    await ex.execute("scroll", {"scroll_direction": "down", "scroll_amount": 3, "coordinate": [640, 400]})
    await ex.execute("left_click_drag", {"start_coordinate": [0, 0], "coordinate": [640, 400], "text": "alt"})
    await ex.execute("mouse_move", {"coordinate": [128, 80]})
    await ex.execute("left_mouse_down", {})
    await ex.execute("left_mouse_up", {})
    await ex.execute("hold_key", {"text": "shift", "duration": 0})
    await ex.execute("key", {"text": "ctrl+a Delete"})
    assert inputs.calls == [
        ("click", 0.0, 0.0, "left", 2, ()),
        ("click", 0.0, 0.0, "left", 3, ()),
        ("click", 72.0, 90.0, "right", 1, ("ctrl", "shift")),
        ("scroll", 720.0, 450.0, "down", 3, ()),
        ("drag", 0.0, 0.0, 720.0, 450.0, ("alt",)),
        ("move", 144.0, 90.0),
        ("button_down", 144.0, 90.0, "left"),
        ("button_up", 144.0, 90.0, "left"),
        ("key_down", "shift"),
        ("key_up", "shift"),
        ("press", "ctrl+a"),
        ("press", "delete"),
    ]


@pytest.mark.parametrize("name,args", [
    ("scroll", {"scroll_direction": "diagonal", "scroll_amount": 1}),
    ("scroll", {"scroll_direction": "up", "scroll_amount": 0}),
    ("left_click", {"coordinate": [5, 5], "text": "cmd+s"}),
    ("type", {"text": ""}),
    ("key", {"text": "cmd+s", "repeat": 0}),
    ("zoom", {"region": [100, 100, 50, 50]}),
    ("wait", {"duration": -1}),
    ("fly", {}),
])
async def test_invalid_actions_raise_action_error(name, args):
    with pytest.raises(cu.ActionError):
        await executor().execute(name, args)


async def test_wait_is_capped_and_interruptible(monkeypatch):
    monkeypatch.setattr(cu, "MAX_WAIT_SECONDS", 0.05)
    out = await executor().execute("wait", {"duration": 5})
    assert "maximum" in out.text
    out = await executor().execute("wait", {"duration": 0.04}, should_stop=lambda: True)
    assert out.text == "OK"


# --------------------------------------------------------------------------- #
# Tastenkombinationen
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,modifiers,key,keycode", [
    ("cmd+s", ("cmd",), "s", 1),
    ("ctrl+shift+Escape", ("ctrl", "shift"), "escape", 53),
    ("super+a", ("cmd",), "a", 0),
    ("alt+Tab", ("alt",), "tab", 48),
    ("option+left", ("alt",), "left", 123),
    ("Return", (), "return", 36),
    ("enter", (), "return", 36),
    ("BackSpace", (), "delete", 51),
    ("Page_Up", (), "pageup", 116),
    ("pagedown", (), "pagedown", 121),
    ("Home", (), "home", 115),
    ("end", (), "end", 119),
    ("space", (), "space", 49),
    ("F12", (), "f12", 111),
    ("f1", (), "f1", 122),
    ("Up", (), "up", 126),
    ("cmd+Z", ("cmd",), "z", 6),
    ("ctrl+7", ("ctrl",), "7", 26),
    ("Shift_L", ("shift",), None, None),
    ("cmd+minus", ("cmd",), "-", 27),
])
def test_parse_combo(text, modifiers, key, keycode):
    combo = parse_combo(text)
    assert combo.modifiers == modifiers
    assert combo.key == key
    assert combo.keycode == keycode


def test_combo_flags_plus_and_sequences():
    assert parse_combo("cmd+shift+s").flags == 0x100000 | 0x20000
    assert parse_combo("cmd++") == KeyCombo(("cmd",), "+")
    assert resolve_key("+") == (24, True)  # US: Shift + "="
    assert parse_combo("ctrl+plus").key == "+"
    assert [str(c) for c in parse_key_sequence("ctrl+a Delete")] == ["ctrl+a", "delete"]
    assert parse_modifiers("ctrl+shift") == ("ctrl", "shift")
    assert parse_modifiers(None) == ()
    for bad in ("", "cmd+", "a+b", "cmd+gibtsnicht", "hyper+x"):
        with pytest.raises(ValueError):
            parse_combo(bad)
    with pytest.raises(ValueError):
        parse_modifiers("cmd+s")


def test_resolve_key_prefers_current_layout():
    german = {"z": 16, "y": 6, "+": 30}
    assert resolve_key("z", german) == (16, False)
    assert resolve_key("+", german) == (30, False)
    assert resolve_key("?", german, {"?": 27}) == (27, True)
    assert resolve_key("return", german) == (36, False)


def test_split_typing_chunks():
    assert split_typing("ab\ncd\t") == [("text", "ab"), ("key", "return"), ("text", "cd"), ("key", "tab")]
    chunks = split_typing("x" * 45)
    assert [len(v) for _, v in chunks] == [20, 20, 5]
    emoji = split_typing("😀" * 11)  # je 2 UTF-16-Einheiten
    assert [len(v) for _, v in emoji] == [10, 1]


@pytest.mark.skipif(sys.platform == "darwin", reason="prüft das Verhalten ohne macOS")
def test_quartz_inputs_unavailable_on_linux():
    with pytest.raises(InputUnavailable):
        QuartzInputs()


# --------------------------------------------------------------------------- #
# Bildschirmfotos
# --------------------------------------------------------------------------- #
def test_fit_size_and_png_helpers():
    w, h = screen_mod.fit_size(2880, 1800)
    assert max(w, h) <= 1568 and w * h <= 1_150_000
    assert abs(w / h - 1.6) < 0.01
    assert screen_mod.fit_size(800, 600) == (800, 600)
    assert max(screen_mod.fit_size(400, 300, upscale=True)) > 400
    assert screen_mod.png_size(make_png(7, 3)) == (7, 3)
    assert screen_mod.looks_blank(make_png(64, 64, blank=True))
    assert not screen_mod.looks_blank(make_png(64, 64))
    with pytest.raises(ValueError):
        screen_mod.png_size(b"kein png")


@pytest.fixture
def fake_mac_screen(monkeypatch):
    """screencapture/sips simulieren: Retina-Aufnahme 2880×1800 eines 1440×900-Bildschirms."""
    calls: list[tuple[str, ...]] = []
    state = {"blank": False, "fail": False}

    async def fake_run(*args, timeout=20):
        calls.append(args)
        if args[0] == "screencapture":
            if state["fail"]:
                return 1, "could not create image from display"
            Path(args[-1]).write_bytes(make_png(2880, 1800, blank=state["blank"]))
        elif args[0] == "sips":
            edge = int(args[2])
            Path(args[-1]).write_bytes(make_png(edge, round(edge / 1.6), blank=state["blank"]))
        return 0, ""

    monkeypatch.setattr(screen_mod, "IS_MAC", True)
    monkeypatch.setattr(screen_mod, "run_command", fake_run)
    monkeypatch.setattr(screen_mod, "screen_size_points", lambda: (1440.0, 900.0))
    return calls, state


async def test_capture_downscales_and_computes_scale(fake_mac_screen, tmp_path):
    calls, _state = fake_mac_screen
    shot = await screen_mod.capture(workdir=tmp_path)
    assert calls[0][:5] == ("screencapture", "-x", "-t", "png", "-m")
    assert calls[1][:2] == ("sips", "-Z")
    assert max(shot.width, shot.height) <= 1568 and shot.width * shot.height <= 1_150_000
    assert shot.scale == pytest.approx(shot.width / 1440)
    assert list(tmp_path.iterdir()) == []  # Zwischendateien sind aufgeräumt


async def test_capture_region_uses_points_rect(fake_mac_screen):
    calls, _state = fake_mac_screen
    await screen_mod.capture(region=(10, 20, 300, 200))
    assert calls[0][:6] == ("screencapture", "-x", "-t", "png", "-R", "10,20,300,200")


async def test_capture_detects_missing_permission(fake_mac_screen):
    _calls, state = fake_mac_screen
    state["blank"] = True
    with pytest.raises(ScreenCaptureError) as err:
        await screen_mod.capture()
    assert "Bildschirmaufnahme" in str(err.value)
    state.update(blank=False, fail=True)
    with pytest.raises(ScreenCaptureError):
        await screen_mod.capture()


async def test_capture_not_mac(monkeypatch):
    monkeypatch.setattr(screen_mod, "IS_MAC", False)
    with pytest.raises(ScreenCaptureError):
        await screen_mod.capture()


# --------------------------------------------------------------------------- #
# MacControl
# --------------------------------------------------------------------------- #
@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def mac(settings, tmp_path, monkeypatch):
    """MacControl, das sich wie auf einem Mac verhält – Programme werden nur protokolliert."""
    ctl = MacControl(lambda: settings, tmp_path)
    ctl.is_mac = True
    ctl.commands = []

    async def fake_run(*args, timeout=20):
        ctl.commands.append(args)
        return 0, ""

    monkeypatch.setattr(control_mod, "run_command", fake_run)
    monkeypatch.setattr(perms, "status", lambda prompt=False: {"accessibility": True, "screen_recording": True})
    return ctl


def fake_router(client=None, configured=True, answer="Du siehst TradingView."):
    seen: dict = {}

    async def complete(system, prompt, images=None, max_tokens=2000):
        seen.update(system=system, prompt=prompt, images=images)
        return answer

    provider = SimpleNamespace(
        configured=lambda: configured,
        _get_client=lambda: client,
        s=SimpleNamespace(anthropic_model="claude-opus-5-5", anthropic_effort="medium"),
    )
    return SimpleNamespace(anthropic=provider, complete=complete, seen=seen)


async def test_notify_escapes_applescript(mac):
    await mac.notify('Vincent "V"', 'Er sagte "hi" \\ ok\nzweite Zeile')
    assert mac.commands == [(
        "osascript", "-e",
        'display notification "Er sagte \\"hi\\" \\\\ ok zweite Zeile" with title "Vincent \\"V\\""',
    )]


async def test_notify_respects_settings(mac, settings):
    settings.mac.notifications = False
    await mac.notify("Titel", "Text")
    settings.mac.notifications = True
    settings.mac.automation_enabled = False
    await mac.notify("Titel", "Text")
    assert mac.commands == []


@pytest.mark.parametrize("name", ["Safari", "TradingView", "Google Chrome", "Bücher", "Visual Studio Code.app"])
async def test_open_app_valid(mac, name):
    assert "geöffnet" in await mac.open_app(name)
    assert mac.commands == [("open", "-a", name)]


@pytest.mark.parametrize("name", ["", "Safari; rm -rf ~", "../../bin/sh", "-n", ".hidden", "/Applications/X.app",
                                  "Safari\nTerminal", "$(whoami)", "a" * 80])
async def test_open_app_rejects_invalid_names(mac, name):
    assert "kann ich nicht öffnen" in await mac.open_app(name)
    assert mac.commands == []


async def test_open_url_validation(mac):
    assert "geöffnet" in await mac.open_url("https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT")
    assert mac.commands == [("open", "https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT")]
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "http://", "ftp://example.com",
                "https://exa mple.com", "https://example.com/\nx", "-a Terminal", "x-apple.systempreferences:"):
        assert "nur normale Webadressen" in await mac.open_url(bad)
    assert len(mac.commands) == 1


async def test_non_mac_behaviour(settings, tmp_path):
    ctl = MacControl(lambda: settings, tmp_path)
    ctl.is_mac = False
    router = fake_router(client=FakeClient())
    assert ctl.permissions() == {"accessibility": None, "screen_recording": None}
    assert await ctl.notify("a", "b") is None
    assert await ctl.screenshot() is None
    for result in (await ctl.open_app("Safari"), await ctl.open_url("https://example.com"),
                   await ctl.describe_screen(router), await ctl.computer_task(router, "Öffne Safari")):
        assert result == control_mod.NOT_MAC
    assert "macOS" in control_mod.NOT_MAC


@pytest.mark.skipif(sys.platform == "darwin", reason="prüft das Verhalten ohne macOS")
def test_permissions_module_on_linux():
    assert perms.status() == {"accessibility": None, "screen_recording": None}


async def test_screenshot_and_describe(mac, monkeypatch):
    async def fake_capture(max_long_edge=1568, region=None, workdir=None):
        return Screenshot(png=SHOT_PNG, width=1280, height=800, scale=SCALE)

    monkeypatch.setattr(screen_mod, "capture", fake_capture)
    assert await mac.screenshot() == (SHOT_PNG, SCALE)
    router = fake_router()
    assert await mac.describe_screen(router, "Welcher Kurs steht da?") == "Du siehst TradingView."
    assert router.seen["images"] == [SHOT_PNG]
    assert "Welcher Kurs steht da?" in router.seen["prompt"]
    assert "Deutsch" in router.seen["system"]


async def test_describe_screen_reports_capture_problem(mac, monkeypatch):
    async def no_permission(max_long_edge=1568, region=None, workdir=None):
        raise ScreenCaptureError(perms.SCREEN_RECORDING_HINT)

    monkeypatch.setattr(screen_mod, "capture", no_permission)
    assert await mac.screenshot() is None
    assert await mac.describe_screen(fake_router()) == perms.SCREEN_RECORDING_HINT


async def test_computer_task_without_key(mac):
    result = await mac.computer_task(fake_router(configured=False), "Öffne Safari")
    assert result == control_mod.NO_KEY
    assert "Claude-API-Key" in result


async def test_computer_task_respects_settings(mac, settings):
    router = fake_router(client=FakeClient())
    settings.mac.computer_use_enabled = False
    assert await mac.computer_task(router, "Öffne Safari") == control_mod.COMPUTER_USE_DISABLED
    settings.mac.computer_use_enabled = True
    settings.mac.automation_enabled = False
    assert await mac.computer_task(router, "Öffne Safari") == control_mod.DISABLED


async def test_computer_task_requires_permissions(mac, monkeypatch):
    monkeypatch.setattr(perms, "status", lambda prompt=False: {"accessibility": False, "screen_recording": True})
    assert await mac.computer_task(fake_router(client=FakeClient()), "Öffne Safari") == perms.ACCESSIBILITY_HINT


async def test_computer_task_runs_loop(mac, settings, monkeypatch):
    settings.mac.computer_use_max_steps = 5
    inputs = FakeInputs()
    monkeypatch.setattr(mac, "_make_executor", lambda: executor(inputs))
    client = FakeClient([
        response("tool_use", tool_use("t", "key", text="cmd+space")),
        response("end_turn", text_block("Spotlight ist offen.")),
    ])
    steps: list[str] = []
    result = await mac.computer_task(fake_router(client=client), "Öffne Spotlight", on_step=steps.append)
    assert result == "Spotlight ist offen."
    assert inputs.calls == [("press", "cmd+space")]
    assert steps == ["Drücke cmd+space"]
    assert client.calls[0]["output_config"] == {"effort": "medium"}
    assert client.calls[0]["messages"][0]["content"].endswith("Öffne Spotlight")


async def test_computer_task_without_input_backend(mac, monkeypatch):
    def unavailable():
        raise InputUnavailable("Für die Maus- und Tastatursteuerung fehlt das Paket pyobjc-framework-Quartz.")

    monkeypatch.setattr(control_mod, "QuartzInputs", unavailable)
    result = await mac.computer_task(fake_router(client=FakeClient()), "Öffne Safari")
    assert "pyobjc-framework-Quartz" in result
