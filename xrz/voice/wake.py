"""Erkennung des Weckworts ("Hey Vincent") in Whisper-Text.

Whisper schreibt den Namen je nach Aussprache unterschiedlich: Vincent, Vinzent,
Winzent, Finzent, Vinzenz, Vinson … Deshalb vergleichen wir nicht die Schreibweise,
sondern den Klang über die *Kölner Phonetik* (das deutsche Gegenstück zu Soundex):
Vincent/Vinzent/Winzent/Finzent/Wincent → "36862".

Damit Gespräche über Vincent (oder ein Fernseher im Hintergrund) Vincent nicht
ständig wecken, muss vor dem Namen eine Anrede stehen ("hey", "hallo", "ok" …) und
der Name muss am Anfang der Äußerung stehen.

Bewusste Entscheidung: "Hey Vincenzo" weckt Vincent ebenfalls. "Vincenzo" hat den
gleichen Klang-Code wie "Vinzenz" (36868) – und "Vinzenz" ist eine häufige
Whisper-Schreibweise für ein deutsch ausgesprochenes "Vinzent". Ein seltener Fehlalarm
(Vincent hört dann 6 Sekunden zu) ist harmloser als ein überhörtes "Hey Vincent".
"Hey Vince" weckt dagegen nicht (Name abgeschnitten, Code 368).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Anreden, die vor dem Namen stehen dürfen (so schreibt Whisper "Hey" auf Deutsch)
PREFIXES = frozenset({
    "hey", "hei", "hay", "he", "hej", "heh", "ey", "eh", "hi", "hallo", "halo", "hello",
    "ok", "okay", "oke", "jo", "yo", "moin", "servus", "na",
})
# Verzögerungslaute und Füllwörter zwischen Anrede und Name ("Hey, äh, Vincent")
HESITATIONS = frozenset({"aeh", "aehm", "ah", "hm", "hmm"})
FILLERS = HESITATIONS | {"also", "du", "mal"}
# Zusätzliche Klang-Codes, die Whisper für "Vincent" liefert
_VINCENT_VARIANTS = frozenset({"3686", "36868"})  # Vinson/Wenson | Vinzenz/Vincents/Vincenzo
_VINCENT_CODE = "36862"

_VOWELS = frozenset("AEIJOUY")
_C_HARD_START = frozenset("AHKLOQRUX")
_C_HARD = frozenset("AHKOQUX")
_DT_SOFT = frozenset("CSZ")
_X_SOFT = frozenset("CKQ")
_SZ = frozenset("SZ")


def koelner_phonetik(word: str) -> str:
    """Kölner Phonetik eines Wortes (z. B. "Vincent" → "36862")."""
    w = word.upper().replace("Ä", "A").replace("Ö", "O").replace("Ü", "U").replace("ß", "S")
    w = re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode())
    if not w:
        return ""
    codes: list[str] = []
    for i, ch in enumerate(w):
        prev = w[i - 1] if i > 0 else ""
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in _VOWELS:
            c = "0"
        elif ch == "H":
            c = ""
        elif ch == "B":
            c = "1"
        elif ch == "P":
            c = "3" if nxt == "H" else "1"
        elif ch in "DT":
            c = "8" if nxt in _DT_SOFT else "2"
        elif ch in "FVW":
            c = "3"
        elif ch in "GKQ":
            c = "4"
        elif ch == "C":
            if i == 0:
                c = "4" if nxt in _C_HARD_START else "8"
            else:
                c = "8" if prev in _SZ or nxt not in _C_HARD else "4"
        elif ch == "X":
            c = "8" if prev in _X_SOFT else "48"
        elif ch == "L":
            c = "5"
        elif ch in "MN":
            c = "6"
        elif ch == "R":
            c = "7"
        elif ch in "SZ":
            c = "8"
        else:
            c = ""
        codes.append(c)
    collapsed: list[str] = []
    for ch in "".join(codes):
        if not collapsed or collapsed[-1] != ch:
            collapsed.append(ch)
    s = "".join(collapsed)
    return s[:1] + s[1:].replace("0", "")


def _norm_word(word: str) -> str:
    t = word.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", t)


@dataclass(frozen=True)
class WakeHit:
    """Treffer: ``rest`` ist der Text nach dem Namen (ein im selben Atemzug gesprochener Befehl)."""

    rest: str
    name: str

    @property
    def rest_words(self) -> int:
        return len(re.findall(r"\w+", self.rest))


class WakeMatcher:
    """Findet "<Anrede> <Name>" am Anfang eines Whisper-Textes."""

    def __init__(self, name: str = "Vincent"):
        self.name = (name or "Vincent").strip() or "Vincent"
        code = koelner_phonetik(self.name)
        self.codes = {code} | (_VINCENT_VARIANTS if code == _VINCENT_CODE else set())
        # abgeschnittene Namen ("Vince") nicht akzeptieren
        self.min_len = max(3, len(_norm_word(self.name)) - 2)

    @property
    def phrase(self) -> str:
        return f"hey {self.name.lower()}"

    def is_name(self, word: str) -> bool:
        w = _norm_word(word)
        return len(w) >= self.min_len and koelner_phonetik(w) in self.codes

    def find(self, text: str, require_prefix: bool = True, max_pos: int = 3) -> WakeHit | None:
        """Sucht den Namen unter den ersten ``max_pos + 1`` Wörtern.

        ``require_prefix``: vor dem Namen muss eine Anrede stehen (Füllwörter erlaubt).
        """
        toks = list(re.finditer(r"\w+", text or ""))
        norm = [_norm_word(m.group(0)) for m in toks]
        for i, w in enumerate(norm[: max_pos + 1]):
            if not self.is_name(w):
                continue
            before = [x for x in norm[:i] if x and x not in FILLERS]
            if require_prefix and not (before and before[-1] in PREFIXES):
                continue
            j = i + 1
            while j < len(toks) and norm[j] in HESITATIONS:
                j += 1  # "Hey Vincent, äh, …" → Füllwort nicht in den Befehl übernehmen
            cut = toks[j - 1].end()
            rest = text[cut:].lstrip(" ,.!?:;-–—\"'„“").strip()
            if rest:
                rest = rest[0].upper() + rest[1:]
            return WakeHit(rest=rest, name=toks[i].group(0))
        return None
