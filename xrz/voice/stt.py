"""Spracherkennung (Deutsch) mit Whisper.

* Apple Silicon: ``mlx-whisper`` (GPU über MLX) – large-v3-turbo für Befehle, small für das Weckwort.
* Sonst: ``faster-whisper`` (CPU, int8) – medium für Befehle, small für das Weckwort.

Wichtig: MLX-Streams gehören zu dem Thread, der sie erzeugt hat. Die Modelle werden deshalb
im selben (Sprach-)Thread geladen UND benutzt, der später auch transkribiert.
Modelle landen im Hugging-Face-Cache (``HF_HOME``, standardmäßig ``<Datenordner>/models``).
"""
from __future__ import annotations

import importlib.util
import logging
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .vad import SR

log = logging.getLogger(__name__)

# Fachbegriffe als Hinweis für die Befehlserkennung – NIE für die Weckwort-Prüfung
# (ein Prompt mit "Vincent" lässt Whisper den Namen in Rauschen hineinhalluzinieren).
DOMAIN_PROMPT = (
    "Vincent, TradingView, Paper Trading, Echtgeld, Bitcoin, Ethereum, Solana, BTC USDT, "
    "Long, Short, Stop-Loss, Take-Profit, Limit-Order, Market-Order, RSI, DAX, Nasdaq, S&P 500."
)

FASTER_WHISPER_COMMAND_DEFAULT = "medium"  # large-v3-turbo braucht auf der CPU ~7 s pro Satz
_SETTINGS_DEFAULT_MODEL = "large-v3-turbo"

_MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}

# Typische Whisper-Halluzinationen bei Stille/Rauschen (deutsche Untertitel-Trainingsdaten)
_HALLUCINATION = re.compile(
    r"untertitel|amara\.org|copyright|"
    r"dank (fürs|für's|für das) zuschauen|abonniert? (den|meinen) kanal",
    re.I,
)


class SttUnavailable(RuntimeError):
    """Spracherkennung kann nicht genutzt werden (Text ist eine deutsche Meldung für die App)."""

    def __init__(self, message: str, missing_package: bool = False):
        super().__init__(message)
        self.missing_package = missing_package


@dataclass
class SttResult:
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0


class SttEngine(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, *, fast: bool, prompt: str | None = None) -> SttResult: ...


def is_hallucination(text: str) -> bool:
    """Kurzer Text, der nur aus einer typischen Whisper-Halluzination besteht."""
    return bool(_HALLUCINATION.search(text or "")) and len((text or "").split()) <= 12


def _merge(segs: Any) -> SttResult:
    segs = list(segs)
    if not segs:
        return SttResult("", -10.0, 1.0)

    def get(s: Any, key: str) -> Any:
        return s[key] if isinstance(s, dict) else getattr(s, key)

    text = " ".join(str(get(s, "text")).strip() for s in segs).strip()
    return SttResult(
        text,
        float(np.mean([get(s, "avg_logprob") for s in segs])),
        float(max(get(s, "no_speech_prob") for s in segs)),
    )


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def resolve_backend(preference: str) -> str | None:
    """"mlx" | "faster-whisper" | None (nichts installiert) – je nach Einstellung und Rechner."""
    mlx_ok = is_apple_silicon() and _has("mlx_whisper")
    fw_ok = _has("faster_whisper")
    if preference == "mlx" and not mlx_ok:
        log.warning("mlx-whisper ist hier nicht verfügbar – nutze faster-whisper")
    if preference in ("auto", "mlx") and mlx_ok:
        return "mlx"
    if fw_ok:
        return "faster-whisper"
    return "mlx" if mlx_ok else None


def model_names(backend: str, voice: Any) -> tuple[str, str]:
    """(Befehlsmodell, Weckwort-Modell) für das gewählte Backend."""
    full = (voice.stt_model or _SETTINGS_DEFAULT_MODEL).strip()
    fast = (voice.stt_fast_model or "small").strip()
    if backend == "mlx":
        return mlx_repo(full), mlx_repo(fast)
    if full == _SETTINGS_DEFAULT_MODEL:
        full = FASTER_WHISPER_COMMAND_DEFAULT
    return full, fast


def mlx_repo(name: str) -> str:
    if "/" in name:
        return name
    return _MLX_REPOS.get(name, f"mlx-community/whisper-{name}-mlx")


def hf_cache_dir(data_dir: Path) -> Path:
    """Hugging-Face-Cache: ``HF_HOME`` des Nutzers bleibt unangetastet, sonst ``<Datenordner>/models``."""
    if not os.environ.get("HF_HOME"):
        os.environ["HF_HOME"] = str(Path(data_dir) / "models")
    hub = os.environ.get("HF_HUB_CACHE") or str(Path(os.environ["HF_HOME"]) / "hub")
    path = Path(hub)
    path.mkdir(parents=True, exist_ok=True)
    return path


class MlxWhisperStt:
    """Apple Silicon (MLX). Hält beide Modelle selbst im Speicher und setzt vor jedem Aufruf
    den internen Modell-Cache von mlx-whisper (``ModelHolder``, geprüft mit mlx-whisper 0.4.3)."""

    def __init__(self, full_repo: str, fast_repo: str, cache_dir: Path):
        import mlx.core as mx
        import mlx_whisper
        from huggingface_hub import snapshot_download
        from mlx_whisper.load_models import load_model
        from mlx_whisper.transcribe import ModelHolder

        self._mlx_whisper, self._holder = mlx_whisper, ModelHolder
        self.name = f"mlx-whisper {full_repo.split('/')[-1]}"
        self._paths = {
            "full": snapshot_download(repo_id=full_repo, cache_dir=str(cache_dir)),
            "fast": snapshot_download(repo_id=fast_repo, cache_dir=str(cache_dir)),
        }
        models: dict[str, Any] = {}
        for path in set(self._paths.values()):
            models[path] = load_model(path, dtype=mx.float16)
        self._models = models
        for fast in (True, False):  # Metal-Kernel kompilieren, damit der erste Befehl schnell ist
            t0 = time.perf_counter()
            self.transcribe(np.zeros(SR, dtype=np.float32), fast=fast)
            log.info("MLX-Whisper (%s) bereit nach Aufwärmen in %.2f s",
                     "Weckwort" if fast else "Befehle", time.perf_counter() - t0)

    def transcribe(self, audio: np.ndarray, *, fast: bool, prompt: str | None = None) -> SttResult:
        path = self._paths["fast" if fast else "full"]
        self._holder.model, self._holder.model_path = self._models[path], path
        result = self._mlx_whisper.transcribe(
            np.ascontiguousarray(audio, dtype=np.float32), path_or_hf_repo=path,
            language="de", task="transcribe", temperature=0.0, fp16=True,
            condition_on_previous_text=False, initial_prompt=prompt, verbose=None,
        )
        return _merge(result.get("segments") or [])


class FasterWhisperStt:
    """CPU (int8) – läuft überall, auf dem Mac ohne GPU-Beschleunigung."""

    def __init__(self, full_size: str, fast_size: str, cache_dir: Path):
        from faster_whisper import WhisperModel

        self.name = f"faster-whisper {full_size}"
        self._fast = WhisperModel(fast_size, device="cpu", compute_type="int8",
                                  download_root=str(cache_dir))
        self._full = self._fast if full_size == fast_size else WhisperModel(
            full_size, device="cpu", compute_type="int8", download_root=str(cache_dir))

    def transcribe(self, audio: np.ndarray, *, fast: bool, prompt: str | None = None) -> SttResult:
        model = self._fast if fast else self._full
        segs, _info = model.transcribe(
            np.ascontiguousarray(audio, dtype=np.float32), language="de",
            beam_size=1 if fast else 5, temperature=0.0, condition_on_previous_text=False,
            without_timestamps=True, vad_filter=False, initial_prompt=prompt,
        )
        return _merge(segs)  # der Generator dekodiert erst beim Durchlaufen


def create_stt(voice: Any, data_dir: Path) -> SttEngine:
    """Lädt die Spracherkennung passend zu den Einstellungen (blockiert, ggf. mit Download).

    Wirft ``SttUnavailable`` mit einer deutschen Meldung.
    """
    backend = resolve_backend(voice.stt_backend)
    if backend is None:
        raise SttUnavailable(
            "Spracherkennung nicht installiert – bitte „pip install faster-whisper“ "
            "(auf Apple Silicon besser „pip install mlx-whisper“) ausführen.",
            missing_package=True,
        )
    cache_dir = hf_cache_dir(data_dir)
    if backend == "mlx":
        full, fast = model_names("mlx", voice)
        try:
            return MlxWhisperStt(full, fast, cache_dir)
        except Exception as exc:
            if not _has("faster_whisper"):
                raise SttUnavailable(_load_error_text(exc)) from exc
            log.warning("mlx-whisper konnte nicht starten (%s) – weiche auf faster-whisper aus", exc)
    full, fast = model_names("faster-whisper", voice)
    try:
        return FasterWhisperStt(full, fast, cache_dir)
    except Exception as exc:
        raise SttUnavailable(_load_error_text(exc)) from exc


def _load_error_text(exc: Exception) -> str:
    return (
        f"Spracherkennung konnte nicht geladen werden ({type(exc).__name__}: {str(exc)[:160]}). "
        "Beim ersten Start werden die Sprachmodelle (ca. 2 GB) aus dem Internet geladen – "
        "bitte Internetverbindung prüfen. Ich versuche es gleich noch einmal."
    )
