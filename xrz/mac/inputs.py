"""Maus und Tastatur per Quartz-CGEvents (macOS) – plus eine Attrappe für Tests.

Alle Koordinaten sind Bildschirm-Punkte (globales Koordinatensystem, Ursprung oben
links am Haupt-Bildschirm). Die Umrechnung aus Bildschirmfoto-Pixeln erledigt
``computer_use.MacExecutor``.

Tastennamen folgen der Schreibweise der Claude-Computersteuerung ("cmd+s",
"ctrl+shift+Escape", "Page_Up", "BackSpace" …), Groß-/Kleinschreibung egal.
Zeichentasten werden – wenn möglich – über das aktuelle Tastaturlayout aufgelöst
(z. B. deutsches QWERTZ), sonst über die US-Tastenpositionen.
Text wird layoutunabhängig als Unicode getippt.
"""
from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

log = logging.getLogger(__name__)

# Modifier: Flag-Masken (kCGEventFlagMask…) und virtuelle Tastencodes
MODIFIER_FLAGS = {"cmd": 0x100000, "ctrl": 0x40000, "alt": 0x80000, "shift": 0x20000, "fn": 0x800000}
MODIFIER_KEYCODES = {"cmd": 55, "ctrl": 59, "alt": 58, "shift": 56, "fn": 63}
_MODIFIER_ALIASES = {
    "cmd": "cmd", "command": "cmd", "super": "cmd", "meta": "cmd", "win": "cmd", "windows": "cmd",
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt",
    "shift": "shift",
    "fn": "fn", "function": "fn",
}

# Virtuelle macOS-Tastencodes (Positionen der US-ANSI-Tastatur)
KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
    "u": 32, "[": 33, "i": 34, "p": 35, "return": 36, "l": 37, "j": 38, "'": 39, "k": 40,
    ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49,
    "`": 50, "delete": 51, "escape": 53, "capslock": 57, "volumeup": 72, "volumedown": 73,
    "mute": 74, "kpenter": 76, "help": 114, "home": 115, "pageup": 116, "forwarddelete": 117,
    "end": 119, "pagedown": 121, "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}
_KEY_ALIASES = {
    "enter": "return", "ret": "return", "esc": "escape", "backspace": "delete",
    "deleteforward": "forwarddelete", "fwddelete": "forwarddelete",
    "spacebar": "space", "pgup": "pageup", "prior": "pageup", "pgdn": "pagedown", "pagedn": "pagedown",
    "next": "pagedown", "arrowup": "up", "uparrow": "up", "arrowdown": "down", "downarrow": "down",
    "arrowleft": "left", "leftarrow": "left", "arrowright": "right", "rightarrow": "right",
    "caps": "capslock", "keypadenter": "kpenter", "kpreturn": "kpenter",
    "minus": "-", "hyphen": "-", "equal": "=", "equals": "=", "plus": "+", "underscore": "_",
    "bracketleft": "[", "leftbracket": "[", "bracketright": "]", "rightbracket": "]",
    "backslash": "\\", "semicolon": ";", "colon": ":", "apostrophe": "'", "quote": "'",
    "quotedbl": '"', "grave": "`", "backtick": "`", "comma": ",", "period": ".", "dot": ".",
    "slash": "/", "question": "?", "exclam": "!", "at": "@", "numbersign": "#", "hash": "#",
    "dollar": "$", "percent": "%", "asciicircum": "^", "ampersand": "&", "asterisk": "*",
    "parenleft": "(", "parenright": ")", "less": "<", "greater": ">", "braceleft": "{",
    "braceright": "}", "bar": "|", "asciitilde": "~",
}
# Zeichen, die auf der US-Tastatur Shift + Grundtaste brauchen
_SHIFTED = {
    "+": "=", "_": "-", "?": "/", ":": ";", '"': "'", "<": ",", ">": ".", "{": "[", "}": "]",
    "|": "\\", "~": "`", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0",
}
SCROLL_LINES_PER_CLICK = 3
_TYPE_CHUNK_UNITS = 20  # CGEventKeyboardSetUnicodeString verarbeitet max. 20 UTF-16-Einheiten


class InputUnavailable(RuntimeError):
    """Maus-/Tastatursteuerung nicht möglich (kein Mac oder PyObjC fehlt) – deutsche Nachricht."""


# --------------------------------------------------------------------------- #
# Tastenkombinationen
# --------------------------------------------------------------------------- #
def resolve_key(key: str, layout: Mapping[str, int] | None = None,
                shifted_layout: Mapping[str, int] | None = None) -> tuple[int, bool]:
    """Tastenname/Zeichen → (virtueller Tastencode, Shift nötig?).

    ``layout`` / ``shifted_layout`` bilden Zeichen des aktuellen Tastaturlayouts auf
    Tastencodes ab (ohne bzw. mit Shift); fehlt ein Zeichen dort, gilt die US-Belegung.
    """
    if len(key) > 1:
        if key in KEYCODES:
            return KEYCODES[key], False
        raise ValueError(f"Unknown key: {key}")
    lower = key.lower()
    if layout and lower in layout:
        return layout[lower], False
    if shifted_layout and key in shifted_layout:
        return shifted_layout[key], True
    if lower in KEYCODES:
        return KEYCODES[lower], False
    if key in _SHIFTED:
        return KEYCODES[_SHIFTED[key]], True
    raise ValueError(f"Unknown key: {key}")


@dataclass(frozen=True)
class KeyCombo:
    modifiers: tuple[str, ...] = ()  # kanonisch: cmd, ctrl, alt, shift, fn
    key: str | None = None  # Tastenname (z. B. "return") oder einzelnes Zeichen (z. B. "s", "+")

    @property
    def flags(self) -> int:
        return sum(MODIFIER_FLAGS[m] for m in self.modifiers)

    @property
    def keycode(self) -> int | None:
        """Tastencode nach US-Belegung (ohne Layout-Anpassung)."""
        return resolve_key(self.key)[0] if self.key else None

    def __str__(self) -> str:
        return "+".join([*self.modifiers, *([self.key] if self.key else [])])


def _normalize(token: str) -> str:
    token = token.strip()
    if len(token) == 1:
        return token.lower()
    return token.lower().replace("_", "").replace("-", "").replace(" ", "")


def _modifier(name: str) -> str | None:
    if name in _MODIFIER_ALIASES:
        return _MODIFIER_ALIASES[name]
    for prefix in ("left", "right"):  # z. B. "leftshift"
        if name.startswith(prefix) and name[len(prefix):] in _MODIFIER_ALIASES:
            return _MODIFIER_ALIASES[name[len(prefix):]]
    if len(name) > 2 and name[-1] in "lr" and name[:-1] in _MODIFIER_ALIASES:  # z. B. "Shift_L"
        return _MODIFIER_ALIASES[name[:-1]]
    return None


def _split_combo(text: str) -> list[str]:
    if text == "+":
        return ["+"]
    if text.endswith("++"):
        parts = text[:-2].split("+") + ["+"]
    else:
        parts = text.split("+")
    if any(not p.strip() for p in parts):
        raise ValueError(f"Invalid key combination: {text}")
    return parts


def parse_combo(text: str) -> KeyCombo:
    """"cmd+shift+z" → KeyCombo(("cmd", "shift"), "z"). Nur Modifier ("shift") ist erlaubt."""
    raw = (text or "").strip()
    if not raw or any(c.isspace() for c in raw):
        raise ValueError(f"Invalid key combination: {text!r}")
    modifiers: list[str] = []
    key: str | None = None
    for token in _split_combo(raw):
        name = _normalize(token)
        mod = _modifier(name)
        if mod:
            if mod not in modifiers:
                modifiers.append(mod)
            continue
        if key is not None:
            raise ValueError(f"Only one non-modifier key per combination: {text}")
        key = _KEY_ALIASES.get(name, name)
        resolve_key(key)  # unbekannte Tasten sofort melden
    return KeyCombo(tuple(modifiers), key)


def parse_key_sequence(text: str) -> list[KeyCombo]:
    """Mehrere Kombinationen nacheinander, durch Leerzeichen getrennt ("ctrl+a Delete")."""
    parts = (text or "").split()
    if not parts:
        raise ValueError("Empty key combination")
    return [parse_combo(p) for p in parts]


def parse_modifiers(text: str | None) -> tuple[str, ...]:
    """Modifier, die während eines Klicks/Scrolls gehalten werden ("shift", "ctrl+shift")."""
    if not text or not text.strip():
        return ()
    combo = parse_combo(text)
    if combo.key is not None:
        raise ValueError(f"Only modifier keys (cmd, ctrl, alt, shift, fn) can be held: {text}")
    return combo.modifiers


def split_typing(text: str) -> list[tuple[str, str]]:
    """Zerlegt Text in Unicode-Stücke (≤ 20 UTF-16-Einheiten) und Tasten für Zeilenumbruch/Tab."""
    parts: list[tuple[str, str]] = []
    chunk, units = "", 0
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch in "\n\t":
            if chunk:
                parts.append(("text", chunk))
                chunk, units = "", 0
            parts.append(("key", "return" if ch == "\n" else "tab"))
            continue
        size = 2 if ord(ch) > 0xFFFF else 1
        if units + size > _TYPE_CHUNK_UNITS:
            parts.append(("text", chunk))
            chunk, units = "", 0
        chunk += ch
        units += size
    if chunk:
        parts.append(("text", chunk))
    return parts


# --------------------------------------------------------------------------- #
# Schnittstelle + Implementierungen
# --------------------------------------------------------------------------- #
class InputBackend(Protocol):
    """Maus/Tastatur in Bildschirm-Punkten. Methoden blockieren kurz (in Threads aufrufen)."""

    def move(self, x: float, y: float) -> None: ...
    def click(self, x: float, y: float, button: str = "left", count: int = 1,
              modifiers: Sequence[str] = ()) -> None: ...
    def button_down(self, x: float, y: float, button: str = "left") -> None: ...
    def button_up(self, x: float, y: float, button: str = "left") -> None: ...
    def drag(self, x0: float, y0: float, x1: float, y1: float, modifiers: Sequence[str] = ()) -> None: ...
    def scroll(self, x: float, y: float, direction: str, amount: int, modifiers: Sequence[str] = ()) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press(self, combo: KeyCombo) -> None: ...
    def key_down(self, combo: KeyCombo) -> None: ...
    def key_up(self, combo: KeyCombo) -> None: ...
    def cursor_position(self) -> tuple[float, float]: ...


class FakeInputs:
    """Attrappe: merkt sich alle Aktionen in ``calls`` statt sie auszuführen (für Tests)."""

    def __init__(self, position: tuple[float, float] = (0.0, 0.0)):
        self.calls: list[tuple[Any, ...]] = []
        self.position = position

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))
        self.position = (x, y)

    def click(self, x: float, y: float, button: str = "left", count: int = 1,
              modifiers: Sequence[str] = ()) -> None:
        self.calls.append(("click", x, y, button, count, tuple(modifiers)))
        self.position = (x, y)

    def button_down(self, x: float, y: float, button: str = "left") -> None:
        self.calls.append(("button_down", x, y, button))

    def button_up(self, x: float, y: float, button: str = "left") -> None:
        self.calls.append(("button_up", x, y, button))

    def drag(self, x0: float, y0: float, x1: float, y1: float, modifiers: Sequence[str] = ()) -> None:
        self.calls.append(("drag", x0, y0, x1, y1, tuple(modifiers)))
        self.position = (x1, y1)

    def scroll(self, x: float, y: float, direction: str, amount: int, modifiers: Sequence[str] = ()) -> None:
        self.calls.append(("scroll", x, y, direction, amount, tuple(modifiers)))

    def type_text(self, text: str) -> None:
        self.calls.append(("type", text))

    def press(self, combo: KeyCombo) -> None:
        self.calls.append(("press", str(combo)))

    def key_down(self, combo: KeyCombo) -> None:
        self.calls.append(("key_down", str(combo)))

    def key_up(self, combo: KeyCombo) -> None:
        self.calls.append(("key_up", str(combo)))

    def cursor_position(self) -> tuple[float, float]:
        return self.position


class QuartzInputs:
    """Echte Eingaben über Quartz-CGEvents (braucht die Bedienungshilfen-Berechtigung)."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise InputUnavailable("Maus- und Tastatursteuerung gibt es nur auf macOS.")
        try:
            import Quartz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise InputUnavailable(
                "Für die Maus- und Tastatursteuerung fehlt das Paket pyobjc-framework-Quartz."
            ) from exc
        self._q = Quartz
        self._held: set[str] = set()
        self._layout, self._shifted = self._read_layout()

    # ------------------------------------------------------------ Grundlagen
    def _post(self, event: Any) -> None:
        self._q.CGEventPost(self._q.kCGHIDEventTap, event)

    def _buttons(self, button: str) -> tuple[int, int, int, int]:
        q = self._q
        table = {
            "left": (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp, q.kCGEventLeftMouseDragged, q.kCGMouseButtonLeft),
            "right": (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp, q.kCGEventRightMouseDragged,
                      q.kCGMouseButtonRight),
            "middle": (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp, q.kCGEventOtherMouseDragged,
                       q.kCGMouseButtonCenter),
        }
        if button not in table:
            raise ValueError(f"Unknown mouse button: {button}")
        return table[button]

    def _mouse(self, kind: int, x: float, y: float, button: int, flags: int = 0, clicks: int = 0) -> None:
        q = self._q
        event = q.CGEventCreateMouseEvent(None, kind, (x, y), button)
        if clicks:
            q.CGEventSetIntegerValueField(event, q.kCGMouseEventClickState, clicks)
        q.CGEventSetFlags(event, flags)
        self._post(event)

    def _key_event(self, keycode: int, down: bool, flags: int) -> None:
        event = self._q.CGEventCreateKeyboardEvent(None, keycode, down)
        self._q.CGEventSetFlags(event, flags)
        self._post(event)

    def _mods_down(self, modifiers: Sequence[str]) -> int:
        flags = 0
        for mod in modifiers:
            flags |= MODIFIER_FLAGS[mod]
            self._key_event(MODIFIER_KEYCODES[mod], True, flags)
        return flags

    def _mods_up(self, modifiers: Sequence[str]) -> None:
        flags = sum(MODIFIER_FLAGS[m] for m in modifiers)
        for mod in reversed(modifiers):
            flags &= ~MODIFIER_FLAGS[mod]
            self._key_event(MODIFIER_KEYCODES[mod], False, flags)

    def _read_layout(self) -> tuple[dict[str, int], dict[str, int]]:
        """Liest, welches Zeichen jede Zeichentaste im aktuellen Tastaturlayout erzeugt."""
        def table(flags: int) -> dict[str, int]:
            found: dict[str, int] = {}
            for code in range(51):
                if code in (36, 48, 49):  # Return, Tab, Leertaste
                    continue
                event = self._q.CGEventCreateKeyboardEvent(None, code, True)
                self._q.CGEventSetFlags(event, flags)
                length, chars = self._q.CGEventKeyboardGetUnicodeString(event, 4, None, None)
                if not isinstance(chars, str):
                    chars = "".join(chr(c) for c in chars)
                chars = chars[:length]
                if len(chars) == 1 and chars.isprintable():
                    found.setdefault(chars, code)
            return found

        try:
            plain = {k.lower(): v for k, v in table(0).items()}
            shifted = table(MODIFIER_FLAGS["shift"])
        except Exception as exc:
            log.info("Tastaturlayout nicht lesbar (%s) – nutze US-Belegung", exc)
            return {}, {}
        if sum(1 for c in "abcdefghijklmnopqrstuvwxyz" if c in plain) < 20:
            return {}, {}
        # Liefert die Abfrage mit Shift dieselben Zeichen, wird Shift ignoriert → nicht verwenden
        if sum(1 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c in shifted) < 20:
            shifted = {}
        return plain, shifted

    def _resolve(self, combo: KeyCombo) -> tuple[int | None, tuple[str, ...]]:
        modifiers = list(combo.modifiers)
        if combo.key is None:
            return None, tuple(modifiers)
        keycode, needs_shift = resolve_key(combo.key, self._layout, self._shifted)
        if needs_shift and "shift" not in modifiers:
            modifiers.append("shift")
        return keycode, tuple(modifiers)

    # ------------------------------------------------------------ Maus
    def move(self, x: float, y: float) -> None:
        if "left" in self._held:
            self._mouse(self._q.kCGEventLeftMouseDragged, x, y, self._q.kCGMouseButtonLeft)
        else:
            self._mouse(self._q.kCGEventMouseMoved, x, y, self._q.kCGMouseButtonLeft)

    def click(self, x: float, y: float, button: str = "left", count: int = 1,
              modifiers: Sequence[str] = ()) -> None:
        down, up, _drag, btn = self._buttons(button)
        flags = self._mods_down(modifiers)
        try:
            self.move(x, y)
            time.sleep(0.03)
            for n in range(1, count + 1):  # clickState 1, 2, 3 → Doppel-/Dreifachklick
                self._mouse(down, x, y, btn, flags, n)
                time.sleep(0.02)
                self._mouse(up, x, y, btn, flags, n)
                time.sleep(0.04)
        finally:
            self._mods_up(modifiers)

    def button_down(self, x: float, y: float, button: str = "left") -> None:
        down, _up, _drag, btn = self._buttons(button)
        self._mouse(down, x, y, btn, clicks=1)
        self._held.add(button)

    def button_up(self, x: float, y: float, button: str = "left") -> None:
        _down, up, _drag, btn = self._buttons(button)
        self._mouse(up, x, y, btn, clicks=1)
        self._held.discard(button)

    def drag(self, x0: float, y0: float, x1: float, y1: float, modifiers: Sequence[str] = ()) -> None:
        down, up, dragged, btn = self._buttons("left")
        flags = self._mods_down(modifiers)
        try:
            self.move(x0, y0)
            time.sleep(0.05)
            self._mouse(down, x0, y0, btn, flags, 1)
            self._held.add("left")
            steps = min(60, max(8, int(math.hypot(x1 - x0, y1 - y0) / 15)))
            for i in range(1, steps + 1):
                self._mouse(dragged, x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, btn, flags)
                time.sleep(0.01)
            time.sleep(0.05)
            self._mouse(up, x1, y1, btn, flags, 1)
            self._held.discard("left")
        finally:
            if "left" in self._held:
                self._mouse(up, x1, y1, btn, flags, 1)
                self._held.discard("left")
            self._mods_up(modifiers)

    def scroll(self, x: float, y: float, direction: str, amount: int, modifiers: Sequence[str] = ()) -> None:
        # Quartz: wheel1 > 0 = nach oben, wheel2 > 0 = nach links
        lines = SCROLL_LINES_PER_CLICK
        wheel1, wheel2 = {"up": (lines, 0), "down": (-lines, 0), "left": (0, lines), "right": (0, -lines)}[direction]
        flags = self._mods_down(modifiers)
        try:
            self.move(x, y)
            time.sleep(0.03)
            for _ in range(amount):
                event = self._q.CGEventCreateScrollWheelEvent(None, self._q.kCGScrollEventUnitLine, 2, wheel1, wheel2)
                self._q.CGEventSetFlags(event, flags)
                self._post(event)
                time.sleep(0.02)
        finally:
            self._mods_up(modifiers)

    def cursor_position(self) -> tuple[float, float]:
        loc = self._q.CGEventGetLocation(self._q.CGEventCreate(None))
        return float(loc.x), float(loc.y)

    # ------------------------------------------------------------ Tastatur
    def type_text(self, text: str) -> None:
        for kind, value in split_typing(text):
            if kind == "key":
                self.press(KeyCombo(key=value))
                continue
            units = len(value.encode("utf-16-le")) // 2
            for down in (True, False):
                event = self._q.CGEventCreateKeyboardEvent(None, 0, down)
                self._q.CGEventSetFlags(event, 0)
                self._q.CGEventKeyboardSetUnicodeString(event, units, value)
                self._post(event)
            time.sleep(0.015)

    def key_down(self, combo: KeyCombo) -> None:
        keycode, modifiers = self._resolve(combo)
        flags = self._mods_down(modifiers)
        if keycode is not None:
            self._key_event(keycode, True, flags)

    def key_up(self, combo: KeyCombo) -> None:
        keycode, modifiers = self._resolve(combo)
        if keycode is not None:
            self._key_event(keycode, False, sum(MODIFIER_FLAGS[m] for m in modifiers))
        self._mods_up(modifiers)

    def press(self, combo: KeyCombo) -> None:
        self.key_down(combo)
        time.sleep(0.02)
        self.key_up(combo)
        time.sleep(0.02)
