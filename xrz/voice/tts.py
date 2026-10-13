"""Sprachausgabe: Anbieter-Kette ElevenLabs → OpenAI → Piper (offline) → macOS ``say``.

Jeder Anbieter liefert rohes PCM (int16 little-endian, mono, ``provider.sample_rate``), damit
ein einziger Player alles abspielen und die Lautstärke für den Avatar messen kann.
Ausgewichen wird nur, wenn ein Anbieter VOR seinem ersten Audio-Stück scheitert – Vincent
sagt also nie etwas doppelt. Bei kaputtem Key/Kontingent pausiert der Anbieter 10 Minuten.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

import httpx

from .audio import AudioUnavailable, PcmPlayer

log = logging.getLogger(__name__)

# Standardstimme, solange in den Einstellungen keine ElevenLabs-Stimme gewählt ist:
# "George" (vorgefertigte ElevenLabs-Stimme, ruhig und tief; spricht mit den mehrsprachigen
# Modellen Deutsch). Deutsche Stimmen aus der Bibliothek lassen sich in den Einstellungen wählen.
DEFAULT_ELEVENLABS_VOICE = "JBFqnCBsd6RMkjVDRZzb"
ELEVENLABS_URL = "https://api.elevenlabs.io"
OPENAI_TTS_INSTRUCTIONS = (
    "Sprich ausschließlich natürliches, akzentfreies Hochdeutsch. Du bist Vincent: ruhig, "
    "souverän und freundlich wie ein erfahrener Kollege. Mittleres Tempo; Zahlen, Preise und "
    "Prozentangaben klar und deutlich."
)
OPENAI_VOICES = ["cedar", "marin", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
                 "sage", "shimmer", "verse", "alloy"]
PIPER_GERMAN_VOICES = [
    ("de_DE-thorsten-high", "Thorsten (hohe Qualität)"),
    ("de_DE-thorsten-medium", "Thorsten (mittlere Qualität)"),
    ("de_DE-thorsten_emotional-medium", "Thorsten (emotional)"),
    ("de_DE-karlsson-low", "Karlsson"),
    ("de_DE-pavoque-low", "Pavoque"),
    ("de_DE-kerstin-low", "Kerstin"),
    ("de_DE-ramona-low", "Ramona"),
    ("de_DE-eva_k-x_low", "Eva K."),
    ("de_DE-mls-medium", "MLS"),
]
PROVIDER_ORDER = ("elevenlabs", "openai", "piper", "macos")
LOCAL_PROVIDERS = ("piper", "macos")
_PIPER_NAME = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)$")


class TTSError(RuntimeError):
    """Anbieter gescheitert. ``retryable=False`` → Anbieter pausieren (falscher Key, Kontingent …)."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class TTSProvider(Protocol):
    name: str

    @property
    def sample_rate(self) -> int: ...

    def configured(self) -> bool: ...

    def ready(self) -> bool: ...

    def stream(self, text: str, previous_text: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[bytes]: ...


# --------------------------------------------------------------------------- #
# Text vorbereiten
# --------------------------------------------------------------------------- #
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002300-\U000023FF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3]"
)
_ARROW = re.compile(r"\s*[←-⇿⟰-⟿]+\s*")
_PAIR = re.compile(r"\b([A-Z0-9]{2,10})/([A-Z0-9]{2,10})\b")
_LIST_MARK = re.compile(r"^(?:[-*+•▪◦]|\d{1,2}[.)])\s+")
_TERMINAL = ".!?…:;,"


def clean_for_speech(text: str) -> str:
    """Macht aus Chat-Text (Markdown, Emojis, Tabellen) gut sprechbaren Text.

    Zahlen bleiben unverändert (deutsche Schreibweise wie 1.234,56 liest jede Stimme richtig).
    """
    s = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)  # [Text](Link) → Text
    s = re.sub(r"<?https?://\S+|www\.\S+", "", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    lines: list[str] = []
    for raw in s.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"[-*_=|:\s]{3,}", line):  # Trennlinien, Tabellenköpfe
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = _LIST_MARK.sub("", line)
        if "|" in line:
            line = ", ".join(c.strip() for c in line.strip("|").split("|") if c.strip())
        lines.append(line)
    out: list[str] = []
    for line in lines:
        line = line.replace("**", "").replace("__", "").replace("~~", "").replace("*", "")
        line = re.sub(r"(?<!\w)_+|_+(?!\w)", "", line)
        line = _PAIR.sub(r"\1 \2", line)
        line = _ARROW.sub(" – ", line)
        line = _EMOJI.sub("", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if line[-1] not in _TERMINAL and not line.endswith(("“", '"', ")")):
            line += "."
        out.append(line)
    s = " ".join(out)
    s = re.sub(r"\s+([.,!?;:])", r"\1", s)
    s = re.sub(r"([.!?])\.+", r"\1", s)
    return s.strip()


_ABBREVIATIONS = (
    "z.B", "u.a", "d.h", "o.ä", "u.U", "bzw", "ca", "Nr", "Dr", "Prof", "Hr", "Fr", "inkl", "exkl",
    "evtl", "ggf", "usw", "vgl", "Mio", "Mrd", "Tsd", "Std", "Min", "Sek", "bspw", "sog", "ggü",
    "zzgl", "max", "Abs", "St", "etc", "Jan", "Feb", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept",
    "Okt", "Nov", "Dez",
)
_ABBR_END = re.compile(r"(?:^|[\s(])(?:" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.$",
                       re.I)
_ORDINAL_END = re.compile(r"(?:^|\s)\d{1,2}\.$")
_MONTH_START = re.compile(r"^(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|"
                          r"November|Dezember|Quartal)\b")
_SPACED_ABBR = ((re.compile(r"\b([zZ])\.\s+B\."), r"\1.B."), (re.compile(r"\b([dD])\.\s+h\."), r"\1.h."),
                (re.compile(r"\b([uU])\.\s+a\."), r"\1.a."), (re.compile(r"\b([oO])\.\s+ä\."), r"\1.ä."),
                (re.compile(r"\b([uU])\.\s+U\."), r"\1.U."))
_SENTENCE_GAP = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ0-9„\"'(\[])")
_SOFT_BREAK = re.compile(r"[,;:–] ")


def split_sentences(text: str, max_len: int = 220) -> list[str]:
    """Deutscher Satz-Splitter: trennt nie in Zahlen (1.234,56), nicht nach Abkürzungen
    ("z.B.", "ca.") und nicht nach Datumsangaben ("am 3. Oktober"). Lange Sätze werden an
    Kommas geteilt, damit die Stimme schnell anfängt und natürlich atmet."""
    s = (text or "").strip()
    for pattern, repl in _SPACED_ABBR:
        s = pattern.sub(repl, s)
    merged: list[str] = []
    for part in _SENTENCE_GAP.split(s):
        if merged and (_ABBR_END.search(merged[-1]) or
                       (_ORDINAL_END.search(merged[-1]) and _MONTH_START.match(part))):
            merged[-1] += " " + part
        else:
            merged.append(part)
    out: list[str] = []
    for sentence in merged:
        sentence = sentence.strip()
        while len(sentence) > max_len:
            cut = -1
            for m in _SOFT_BREAK.finditer(sentence, 0, max_len):
                cut = m.start() + 1
            if cut < max_len * 0.4:
                cut = sentence.rfind(" ", 0, max_len)
            if cut <= 0:
                cut = max_len
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            out.append(sentence)
    return out


# --------------------------------------------------------------------------- #
# Hilfen für HTTP-Anbieter
# --------------------------------------------------------------------------- #
def _align_int16(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """HTTP-Stücke können mitten in einem Sample enden – auf 2 Byte ausrichten (sonst Rauschen)."""
    carry = b""
    for c in chunks:
        if not c:
            continue
        c = carry + c
        cut = len(c) - (len(c) % 2)
        carry = c[cut:]
        if cut:
            yield c[:cut]


def _raise_for_status(r: httpx.Response, provider: str) -> None:
    if r.status_code < 400:
        return
    r.read()
    body = r.text[:300]
    # 401 = falscher Key ODER Kontingent aufgebraucht (ElevenLabs), 402/403 = Tarif, 404/422 = Stimme/Modell
    fatal = r.status_code in (400, 401, 402, 403, 404, 422)
    raise TTSError(f"{provider} HTTP {r.status_code}: {body}", retryable=not fatal)


_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


# --------------------------------------------------------------------------- #
# ElevenLabs
# --------------------------------------------------------------------------- #
@dataclass
class ElevenLabsTTS:
    http: httpx.Client
    api_key: str
    voice_id: str = ""
    model_id: str = "eleven_flash_v2_5"  # schnell; eleven_multilingual_v2 = Qualität; eleven_v3 = ausdrucksstark
    sample_rate: int = 24000  # pcm_16000/22050/24000 gehen in jedem Tarif
    library_cache: Path | None = None  # Zuordnung Bibliotheks-Stimme → eigene Stimmen-ID
    base_url: str = ELEVENLABS_URL
    stability: float = 0.5
    similarity_boost: float = 0.75
    name: str = "elevenlabs"
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def configured(self) -> bool:
        return bool(self.api_key)

    def ready(self) -> bool:
        return self.configured()

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key, "Content-Type": "application/json"}

    def resolve_voice(self) -> str:
        """Bibliotheks-Stimmen ("library:<besitzer>:<id>") werden einmalig zu den eigenen Stimmen
        hinzugefügt; die dabei vergebene ID wird gemerkt."""
        vid = (self.voice_id or DEFAULT_ELEVENLABS_VOICE).strip()
        if not vid.startswith("library:"):
            return vid
        parts = vid.split(":", 2)
        if len(parts) != 3 or not all(parts[1:]):
            raise TTSError(f"Ungültige Stimmen-ID: {vid}", retryable=False)
        _, owner, shared_id = parts
        with self._lock:
            cache: dict[str, str] = {}
            if self.library_cache and self.library_cache.exists():
                try:
                    cache = json.loads(self.library_cache.read_text("utf-8"))
                except (OSError, ValueError):
                    cache = {}
            if shared_id in cache:
                return cache[shared_id]
            r = self.http.post(f"{self.base_url}/v1/voices/add/{owner}/{shared_id}",
                               headers=self._headers(), json={"new_name": "Vincent"}, timeout=_HTTP_TIMEOUT)
            _raise_for_status(r, self.name)
            new_id = str(r.json()["voice_id"])
            cache[shared_id] = new_id
            if self.library_cache:
                self.library_cache.parent.mkdir(parents=True, exist_ok=True)
                self.library_cache.write_text(json.dumps(cache, indent=2), "utf-8")
            return new_id

    def stream(self, text: str, previous_text: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[bytes]:
        body: dict[str, Any] = {"text": text, "model_id": self.model_id}
        if self.model_id == "eleven_v3":
            body["voice_settings"] = {"stability": self.stability}
        else:
            body["voice_settings"] = {"stability": self.stability, "similarity_boost": self.similarity_boost,
                                      "style": 0.0, "use_speaker_boost": True, "speed": 1.0}
        if self.model_id != "eleven_multilingual_v2":  # laut API dort nicht erlaubt
            body["language_code"] = "de"
        if previous_text:
            body["previous_text"] = previous_text[-1000:]
        url = f"{self.base_url}/v1/text-to-speech/{self.resolve_voice()}/stream"
        params = {"output_format": f"pcm_{self.sample_rate}"}
        with self.http.stream("POST", url, params=params, headers=self._headers(), json=body,
                              timeout=_HTTP_TIMEOUT) as r:
            _raise_for_status(r, self.name)
            for chunk in _align_int16(r.iter_bytes(4096)):
                if cancel is not None and cancel.is_set():
                    return
                yield chunk


def elevenlabs_voices(http: httpx.Client, api_key: str, include_library: bool = True,
                      base_url: str = ELEVENLABS_URL) -> list[dict[str, str]]:
    """Eigene Stimmen (GET /v2/voices) + deutsche Stimmen aus der Bibliothek (GET /v1/shared-voices)."""
    headers = {"xi-api-key": api_key}
    voices: list[dict[str, str]] = []
    token: str | None = None
    for _page in range(5):
        params: dict[str, Any] = {"page_size": 100}
        if token:
            params["next_page_token"] = token
        r = http.get(f"{base_url}/v2/voices", params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        for v in data.get("voices", []):
            langs = {str(x.get("language", "")) for x in v.get("verified_languages") or []}
            lang = "de" if "de" in langs else str((v.get("labels") or {}).get("language") or "multi")
            voices.append({"id": str(v["voice_id"]), "name": str(v.get("name", v["voice_id"])), "lang": lang})
        token = data.get("next_page_token")
        if not data.get("has_more") or not token:
            break
    if include_library:
        try:
            r = http.get(f"{base_url}/v1/shared-voices", headers=headers, timeout=15,
                         params={"language": "de", "page_size": 30, "sort": "trending"})
            r.raise_for_status()
            for v in r.json().get("voices", []):
                desc = ", ".join(x for x in (v.get("gender"), v.get("accent")) if x)
                voices.append({
                    "id": f"library:{v['public_owner_id']}:{v['voice_id']}",
                    "name": f"{v.get('name', v['voice_id'])} (Bibliothek{', ' + desc if desc else ''})",
                    "lang": "de",
                })
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.info("ElevenLabs-Bibliothek nicht abrufbar: %s", exc)
    return voices


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
@dataclass
class OpenAITTS:
    http: httpx.Client
    api_key: str
    model: str = "gpt-4o-mini-tts"
    voice: str = "cedar"
    base_url: str = "https://api.openai.com/v1"
    instructions: str = OPENAI_TTS_INSTRUCTIONS
    sample_rate: int = 24000  # response_format=pcm ist fest 24 kHz, 16 bit, mono
    name: str = "openai"

    def configured(self) -> bool:
        return bool(self.api_key)

    def ready(self) -> bool:
        return self.configured()

    def stream(self, text: str, previous_text: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[bytes]:
        body: dict[str, Any] = {"model": self.model, "voice": self.voice or "cedar", "input": text[:4096],
                                "response_format": "pcm"}
        if not self.model.startswith("tts-1"):  # tts-1/tts-1-hd kennen keine Anweisungen
            body["instructions"] = self.instructions
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url.rstrip('/')}/audio/speech"
        with self.http.stream("POST", url, headers=headers, json=body, timeout=_HTTP_TIMEOUT) as r:
            _raise_for_status(r, self.name)
            for chunk in _align_int16(r.iter_bytes(4096)):
                if cancel is not None and cancel.is_set():
                    return
                yield chunk


# --------------------------------------------------------------------------- #
# Piper (lokal, offline)
# --------------------------------------------------------------------------- #
def piper_installed() -> bool:
    try:
        return importlib.util.find_spec("piper") is not None
    except (ImportError, ValueError):
        return False


def _piper_download(voice: str, target_dir: Path) -> None:
    from piper.download_voices import download_voice  # piper-tts 1.8: (voice, download_dir, force_redownload)

    download_voice(voice, target_dir)


class PiperVoiceStore:
    """Piper-Stimmen im Datenordner. ``ensure()`` lädt fehlende Stimmen im Hintergrund."""

    RETRY_S = 600.0

    def __init__(self, voices_dir: Path,
                 downloader: Callable[[str, Path], None] | None = None):
        self.dir = Path(voices_dir)
        self._downloader = downloader or _piper_download
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._failed: dict[str, tuple[float, str]] = {}

    @staticmethod
    def valid_name(voice: str) -> bool:
        return bool(_PIPER_NAME.match(voice or ""))

    def model_path(self, voice: str) -> Path:
        return self.dir / f"{voice}.onnx"

    def installed(self, voice: str) -> bool:
        if not self.valid_name(voice):
            return False
        model = self.model_path(voice)
        config = Path(str(model) + ".json")
        return all(p.is_file() and p.stat().st_size > 0 for p in (model, config))

    def downloading(self, voice: str) -> bool:
        with self._lock:
            return voice in self._running

    def last_error(self, voice: str) -> str:
        with self._lock:
            return self._failed.get(voice, (0.0, ""))[1]

    def ensure(self, voice: str) -> None:
        """Startet den Download im Hintergrund, falls nötig (nicht blockierend)."""
        if not piper_installed() or not self.valid_name(voice) or self.installed(voice):
            return
        with self._lock:
            if voice in self._running:
                return
            failed_at = self._failed.get(voice, (0.0, ""))[0]
            if failed_at and time.time() - failed_at < self.RETRY_S:
                return
            self._running.add(voice)
        threading.Thread(target=self._download_bg, args=(voice,), name="vincent-piper-download",
                         daemon=True).start()

    def _download_bg(self, voice: str) -> None:
        try:
            self.download(voice)
        except Exception as exc:
            log.warning("Piper-Stimme %s konnte nicht geladen werden: %s", voice, exc)
        finally:
            with self._lock:
                self._running.discard(voice)

    def download(self, voice: str) -> None:
        """Blockierend. Lädt erst in einen Zwischenordner und verschiebt dann – halbe Dateien
        landen so nie im Stimmenordner."""
        if not self.valid_name(voice):
            raise ValueError(f"Ungültiger Piper-Stimmenname: {voice!r}")
        if self.installed(voice):
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        log.info("Lade Piper-Stimme %s nach %s …", voice, self.dir)
        try:
            with tempfile.TemporaryDirectory(dir=self.dir, prefix=".download-") as tmp:
                self._downloader(voice, Path(tmp))
                for suffix in (".onnx.json", ".onnx"):  # Modell zuletzt → installed() erst danach wahr
                    src = Path(tmp) / f"{voice}{suffix}"
                    if not src.is_file() or src.stat().st_size == 0:
                        raise RuntimeError(f"Download unvollständig ({src.name} fehlt)")
                    os.replace(src, self.dir / src.name)
        except Exception as exc:
            with self._lock:
                self._failed[voice] = (time.time(), str(exc)[:200])
            raise
        with self._lock:
            self._failed.pop(voice, None)
        log.info("Piper-Stimme %s bereit", voice)


_PIPER_CACHE: dict[str, Any] = {}
_PIPER_CACHE_LOCK = threading.Lock()


@dataclass
class PiperTTS:
    model_path: Path
    length_scale: float = 1.0
    name: str = "piper"

    def configured(self) -> bool:
        return piper_installed()

    def ready(self) -> bool:
        return self.configured() and self.model_path.is_file() and Path(str(self.model_path) + ".json").is_file()

    def _voice(self) -> Any:
        key = str(self.model_path)
        with _PIPER_CACHE_LOCK:
            voice = _PIPER_CACHE.get(key)
            if voice is None:
                from piper import PiperVoice

                voice = PiperVoice.load(key)  # einmal laden (~0,5 s), danach wiederverwenden
                _PIPER_CACHE[key] = voice
            return voice

    @property
    def sample_rate(self) -> int:
        return int(self._voice().config.sample_rate)

    def stream(self, text: str, previous_text: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[bytes]:
        from piper import SynthesisConfig

        voice = self._voice()
        cfg = SynthesisConfig(length_scale=self.length_scale)
        for chunk in voice.synthesize(text, syn_config=cfg):
            if cancel is not None and cancel.is_set():
                return
            yield chunk.audio_int16_bytes


# --------------------------------------------------------------------------- #
# macOS "say" (letzte Rettung)
# --------------------------------------------------------------------------- #
_SAY_LINE = re.compile(r"^(?P<name>.+?)\s{2,}(?P<locale>[a-z]{2,3}[_-][A-Za-z0-9]+)\s+#")
_SAY_CACHE: dict[str, Any] = {"ts": 0.0, "voices": []}
_MALE_VOICES = ("Markus", "Yannick", "Martin", "Viktor")
_NOVELTY_VOICES = ("Grandma", "Grandpa", "Rocko", "Eddy", "Flo", "Reed", "Sandy", "Shelley", "Bubbles")


def say_available() -> bool:
    return sys.platform == "darwin" and shutil.which("say") is not None


def parse_say_voices(output: str) -> list[tuple[str, str]]:
    """``say -v ?`` → [(Name, Locale)], z. B. ("Anna (Premium)", "de_DE")."""
    voices = []
    for line in output.splitlines():
        m = _SAY_LINE.match(line)
        if m:
            voices.append((m.group("name").strip(), m.group("locale").replace("-", "_")))
    return voices


def list_say_voices() -> list[tuple[str, str]]:
    if not say_available():
        return []
    if time.time() - _SAY_CACHE["ts"] < 600:
        return list(_SAY_CACHE["voices"])
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("macOS-Stimmen nicht abfragbar: %s", exc)
        return []
    _SAY_CACHE.update(ts=time.time(), voices=parse_say_voices(out))
    return list(_SAY_CACHE["voices"])


def best_german_say_voice(voices: list[tuple[str, str]]) -> str | None:
    """Beste deutsche Stimme: Premium > Erweitert > Standard; männlich bevorzugt, keine Spaßstimmen."""
    german = [n for n, loc in voices if loc.startswith("de_")]
    if not german:
        return None

    def score(name: str) -> tuple[int, int, int]:
        quality = 2 if "(Premium)" in name else 1 if ("(Enhanced)" in name or "(Erweitert)" in name) else 0
        male = 1 if name.startswith(_MALE_VOICES) else 0
        serious = 0 if name.startswith(_NOVELTY_VOICES) else 1
        return (serious, quality, male)

    return max(german, key=score)


@dataclass
class MacSayTTS:
    voice: str = ""  # leer = beste installierte deutsche Stimme
    rate_wpm: int = 190
    sample_rate: int = 22050
    name: str = "macos"

    def configured(self) -> bool:
        return say_available()

    def ready(self) -> bool:
        return self.configured()

    def stream(self, text: str, previous_text: str | None = None,
               cancel: threading.Event | None = None) -> Iterator[bytes]:
        voice = self.voice or best_german_say_voice(list_say_voices())
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "say.wav")
            cmd = ["say", "-r", str(self.rate_wpm), "-o", out, "--file-format=WAVE",
                   f"--data-format=LEI16@{self.sample_rate}"]
            if voice:
                cmd[1:1] = ["-v", voice]
            # Text über stdin (nie als Argument) und ohne "[[…]]"-Steuerbefehle von say
            safe = text.replace("[[", "(").replace("]]", ")")
            p = subprocess.run(cmd, input=safe.encode("utf-8"), capture_output=True, timeout=120)
            if p.returncode != 0 or not os.path.exists(out):
                raise TTSError(f"say fehlgeschlagen: {p.stderr.decode(errors='replace')[:200]}")
            with wave.open(out, "rb") as w:
                if w.getsampwidth() != 2 or w.getnchannels() != 1:
                    raise TTSError("say lieferte ein unerwartetes Format")
                self.sample_rate = w.getframerate()
                while True:
                    if cancel is not None and cancel.is_set():
                        return
                    data = w.readframes(4096)
                    if not data:
                        break
                    yield data


# --------------------------------------------------------------------------- #
# Anbieter aus den Einstellungen
# --------------------------------------------------------------------------- #
def provider_order(preference: str) -> list[str]:
    """auto = alle (ElevenLabs → OpenAI → Piper → say). Ausdrücklich gewählter Anbieter zuerst,
    danach nur noch LOKALE Ausweichstimmen – wer Piper/say wählt, schickt keinen Text in die Cloud."""
    if preference not in PROVIDER_ORDER:
        return list(PROVIDER_ORDER)
    return [preference] + [p for p in LOCAL_PROVIDERS if p != preference]


def build_providers(settings: Any, secrets: Any, data_dir: Path, http: httpx.Client,
                    piper_store: PiperVoiceStore) -> list[TTSProvider]:
    v = settings.voice
    providers: list[TTSProvider] = []
    for name in provider_order(v.tts_provider):
        if name == "elevenlabs":
            key = secrets.get("elevenlabs_api_key")
            if key:
                providers.append(ElevenLabsTTS(
                    http=http, api_key=key, voice_id=v.elevenlabs_voice_id,
                    model_id=v.elevenlabs_model or "eleven_flash_v2_5",
                    library_cache=Path(data_dir) / "voices" / "elevenlabs_library.json"))
        elif name == "openai":
            key = secrets.get("openai_api_key")
            if key:
                providers.append(OpenAITTS(http=http, api_key=key, model=v.openai_tts_model or "gpt-4o-mini-tts",
                                           voice=v.openai_tts_voice or "cedar",
                                           base_url=settings.ai.openai_base_url or "https://api.openai.com/v1"))
        elif name == "piper":
            if piper_installed() and piper_store.valid_name(v.piper_voice):
                piper_store.ensure(v.piper_voice)
                providers.append(PiperTTS(model_path=piper_store.model_path(v.piper_voice)))
        elif name == "macos" and say_available():
            providers.append(MacSayTTS(voice=v.macos_voice))
    return providers


def list_voices_for(provider: TTSProvider | None, http: httpx.Client,
                    piper_store: PiperVoiceStore) -> list[dict[str, str]]:
    """Wählbare Stimmen des Anbieters (blockierend, ggf. Netzwerk)."""
    if provider is None:
        return []
    if isinstance(provider, ElevenLabsTTS):
        return elevenlabs_voices(http, provider.api_key, base_url=provider.base_url)
    if isinstance(provider, OpenAITTS):
        return [{"id": v, "name": v.capitalize(), "lang": "multi"} for v in OPENAI_VOICES]
    if isinstance(provider, PiperTTS):
        return [{"id": vid, "name": label + ("" if piper_store.installed(vid) else " – wird geladen"),
                 "lang": "de"} for vid, label in PIPER_GERMAN_VOICES]
    if isinstance(provider, MacSayTTS):
        return [{"id": n, "name": n, "lang": loc} for n, loc in list_say_voices() if loc.startswith("de_")]
    return []


# --------------------------------------------------------------------------- #
# Kette: Satz für Satz, nächster Satz wird schon erzeugt, während der aktuelle läuft
# --------------------------------------------------------------------------- #
class _Job:
    def __init__(self, sentence: str, prev: str | None):
        self.sentence, self.prev = sentence, prev
        self.q: queue.Queue[bytes | None] = queue.Queue()
        self.ready = threading.Event()
        self.sample_rate: int | None = None
        self.provider: str | None = None


def _drain(q: queue.Queue[bytes | None], cancel: threading.Event) -> Iterator[bytes]:
    while True:
        try:
            chunk = q.get(timeout=0.05)
        except queue.Empty:
            if cancel.is_set():
                return
            continue
        if chunk is None:
            return
        yield chunk


class TTSChain:
    def __init__(self, providers: Callable[[], list[TTSProvider]], player: PcmPlayer,
                 cooldown_s: float = 600.0):
        self._providers = providers
        self.player = player
        self.cooldown_s = cooldown_s
        self._cool: dict[str, float] = {}
        self._lock = threading.Lock()

    def cooling(self, name: str) -> bool:
        with self._lock:
            return self._cool.get(name, 0.0) > time.time()

    def providers(self) -> list[TTSProvider]:
        return self._providers()

    def active(self) -> TTSProvider | None:
        """Der Anbieter, der als Nächstes sprechen würde (oder, falls noch keiner bereit ist,
        der erste eingerichtete – z. B. Piper während des Downloads)."""
        providers = self.providers()
        for p in providers:
            if not self.cooling(p.name) and p.ready():
                return p
        return next((p for p in providers if p.configured()), None)

    def _produce(self, job: _Job, providers: list[TTSProvider], cancel: threading.Event) -> None:
        try:
            for p in providers:
                if cancel.is_set():
                    return
                if self.cooling(p.name) or not p.ready():
                    continue
                try:
                    it = iter(p.stream(job.sentence, previous_text=job.prev, cancel=cancel))
                    first = next(it, None)  # hier fällt die Anfrage – Key/Kontingent-Fehler zeigen sich jetzt
                    if first is None:
                        if cancel.is_set():
                            return
                        raise TTSError(f"{p.name}: leere Audiodaten")
                    rate = int(p.sample_rate)
                except TTSError as exc:
                    log.warning("Sprachausgabe %s fehlgeschlagen: %s", p.name, exc)
                    if not exc.retryable:
                        with self._lock:
                            self._cool[p.name] = time.time() + self.cooldown_s
                    continue
                except Exception as exc:  # Netzwerk, ONNX, Prozess …
                    log.warning("Sprachausgabe %s Fehler: %r", p.name, exc)
                    continue
                job.provider, job.sample_rate = p.name, rate
                job.q.put(first)
                job.ready.set()
                try:
                    for chunk in it:
                        job.q.put(chunk)
                except Exception as exc:  # Abbruch mitten im Satz: Satz bleibt unvollständig
                    log.warning("Sprachausgabe %s brach mitten im Satz ab: %r", p.name, exc)
                return
            log.error("Keine Sprachausgabe verfügbar für: %r", job.sentence[:80])
        finally:
            job.ready.set()
            job.q.put(None)

    def speak(self, text: str, cancel: threading.Event | None = None,
              on_sentence: Callable[[str], None] | None = None) -> list[str | None]:
        """Blockierend (eigener Thread!). ``cancel`` setzen = sofort verstummen.
        Liefert pro Satz den benutzten Anbieter (None = keiner ging). Wirft ``AudioUnavailable``,
        wenn der Lautsprecher nicht geht."""
        cancel = cancel or threading.Event()
        providers = self.providers()
        jobs: list[_Job] = []
        prev: str | None = None
        for sentence in split_sentences(clean_for_speech(text)):
            jobs.append(_Job(sentence, prev))
            prev = f"{prev} {sentence}" if prev else sentence

        def start(i: int) -> None:
            if i < len(jobs):
                threading.Thread(target=self._produce, args=(jobs[i], providers, cancel),
                                 name="vincent-tts-synth", daemon=True).start()

        used: list[str | None] = []
        start(0)
        try:
            for i, job in enumerate(jobs):
                while not job.ready.wait(0.05):
                    if cancel.is_set():
                        return used
                start(i + 1)  # nächsten Satz schon erzeugen, während dieser läuft
                if cancel.is_set():
                    return used
                used.append(job.provider)
                if job.sample_rate is None:
                    continue
                if on_sentence:
                    on_sentence(job.sentence)
                if not self.player.play(job.sample_rate, _drain(job.q, cancel), cancel):
                    return used
        except AudioUnavailable:
            cancel.set()  # laufende Erzeugung beenden
            raise
        return used
