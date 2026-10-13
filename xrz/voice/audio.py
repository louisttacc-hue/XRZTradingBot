"""Mikrofon und Lautsprecher über ``sounddevice`` (PortAudio).

``sounddevice`` wird erst bei Bedarf geladen: Unter Linux ohne PortAudio wirft schon der
Import einen ``OSError`` – das darf die App nie zum Absturz bringen.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable

import numpy as np

from .vad import SR, WIN

log = logging.getLogger(__name__)


class AudioUnavailable(RuntimeError):
    """Audio geht nicht – Text ist eine deutsche Meldung für die App.

    ``missing_package``: es fehlt Software (→ Zustand "off"), sonst ein Geräteproblem (→ "error").
    """

    def __init__(self, message: str, missing_package: bool = False):
        super().__init__(message)
        self.missing_package = missing_package


def load_sounddevice() -> Any:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise AudioUnavailable(
            "Audio-Paket fehlt – bitte „pip install sounddevice“ ausführen.", missing_package=True
        ) from exc
    except OSError as exc:  # PortAudio-Bibliothek nicht gefunden
        raise AudioUnavailable(
            "PortAudio (Audio-Bibliothek) fehlt – unter Linux z. B. „sudo apt install libportaudio2“; "
            "auf dem Mac bringt sounddevice PortAudio selbst mit.",
            missing_package=True,
        ) from exc
    return sd


def resolve_device(sd: Any, spec: str | int | None, kind: str) -> int | None:
    """Gerät aus den Einstellungen: Nummer oder Teil des Namens; leer/unbekannt = Standardgerät."""
    spec = str(spec if spec is not None else "").strip()
    if not spec:
        return None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    try:
        devices = list(sd.query_devices())
    except Exception as exc:
        log.warning("Audiogeräte nicht abfragbar: %s", exc)
        return None
    if spec.isdigit():
        idx = int(spec)
        if 0 <= idx < len(devices) and devices[idx].get(key, 0) > 0:
            return idx
    else:
        for idx, dev in enumerate(devices):
            if spec.lower() in str(dev.get("name", "")).lower() and dev.get(key, 0) > 0:
                return idx
    log.warning("Audiogerät %r (%s) nicht gefunden – nutze das Standardgerät", spec, kind)
    return None


def check_input_device(sd: Any, device: int | None) -> dict[str, Any]:
    """Liefert die Geräte-Infos des Mikrofons oder wirft ``AudioUnavailable``."""
    try:
        info = sd.query_devices(device, "input")
    except Exception as exc:
        raise AudioUnavailable(
            "Kein Mikrofon gefunden. Bitte ein Mikrofon anschließen bzw. in den Einstellungen "
            "das richtige Eingabegerät wählen."
        ) from exc
    return dict(info)


class LevelMeter:
    """RMS → dBFS → 0..1 mit schnellem Anstieg und langsamem Abfall (für die Avatar-Animation)."""

    def __init__(self, floor_db: float = -50.0, attack: float = 0.6, release: float = 0.15):
        self.floor_db, self.attack, self.release = floor_db, attack, release
        self.value = 0.0

    def update(self, block: np.ndarray) -> float:
        if block.size == 0:
            x = 0.0
        else:
            f = block.astype(np.float32)
            if block.dtype == np.int16:
                f /= 32768.0
            rms = float(np.sqrt(np.mean(f * f)) + 1e-9)
            db = 20.0 * np.log10(rms)
            x = min(1.0, max(0.0, (db - self.floor_db) / -self.floor_db))
        k = self.attack if x > self.value else self.release
        self.value += k * (x - self.value)
        return self.value

    def reset(self) -> None:
        self.value = 0.0


class _Resampler:
    """Notlösung, falls das Mikrofon keine 16 kHz kann: Glättung (gegen Aliasing) + Interpolation."""

    def __init__(self, rate_in: float, rate_out: int = SR):
        self.step = rate_in / rate_out
        self.k = max(1, int(round(self.step)))
        self._kernel = np.full(self.k, 1.0 / self.k, dtype=np.float32)
        self._tail = np.zeros(self.k - 1, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.k > 1:
            ext = np.concatenate([self._tail, x])
            self._tail = ext[len(ext) - (self.k - 1):]
            x = np.convolve(ext, self._kernel, mode="valid").astype(np.float32)
        self._buf = np.concatenate([self._buf, x])
        last = len(self._buf) - 1
        if last < self._pos:
            return np.zeros(0, dtype=np.float32)
        n = int((last - self._pos) / self.step) + 1
        idx = self._pos + self.step * np.arange(n)
        out = np.interp(idx, np.arange(len(self._buf)), self._buf).astype(np.float32)
        self._pos = float(idx[-1] + self.step)
        drop = min(int(self._pos), len(self._buf))
        self._buf = self._buf[drop:]
        self._pos -= drop
        return out


class MicStream:
    """Mikrofon-Strom: 16 kHz mono float32 in 32-ms-Blöcken an ``on_block`` (PortAudio-Thread!).

    ``on_block`` muss sofort zurückkehren (z. B. ``queue.put_nowait``).
    """

    def __init__(self, sd: Any, device: int | None, on_block: Callable[[np.ndarray], None]):
        self._sd, self.device, self._on_block = sd, device, on_block
        self._stream: Any = None
        self._resample: _Resampler | None = None
        self.rate = SR

    def open(self) -> None:
        sd = self._sd
        try:
            self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=WIN,
                                          device=self.device, callback=self._callback)
        except Exception as exc:
            info = check_input_device(sd, self.device)
            rate = int(info.get("default_samplerate") or 48000)
            if rate == SR:
                raise AudioUnavailable(f"Mikrofon lässt sich nicht öffnen: {exc}") from exc
            log.info("Mikrofon kann keine 16 kHz (%s) – nehme mit %d Hz auf und rechne um", exc, rate)
            self._resample, self.rate = _Resampler(rate), rate
            try:
                self._stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                              blocksize=int(WIN * rate / SR), device=self.device,
                                              callback=self._callback)
            except Exception as exc2:
                raise AudioUnavailable(f"Mikrofon lässt sich nicht öffnen: {exc2}") from exc2
        try:
            self._stream.start()
        except Exception as exc:
            self.close()
            raise AudioUnavailable(f"Mikrofon lässt sich nicht starten: {exc}") from exc

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            log.debug("Mikrofon-Status: %s", status)
        block = np.array(indata[:, 0], dtype=np.float32)  # Kopie: PortAudio nutzt den Puffer wieder
        if self._resample is not None:
            block = self._resample(block)
        self._on_block(block)

    @property
    def active(self) -> bool:
        try:
            return bool(self._stream is not None and self._stream.active)
        except Exception:
            return False

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        for call in (stream.stop, stream.close):
            try:
                call()
            except Exception as exc:
                log.debug("Mikrofon schließen: %s", exc)


def beep_pcm(sample_rate: int = 24000) -> bytes:
    """Kurzer, leiser Bestätigungston (zwei Töne aufwärts, weich ein- und ausgeblendet)."""
    parts = []
    for freq, dur in ((660.0, 0.07), (880.0, 0.09)):
        t = np.arange(int(sample_rate * dur)) / sample_rate
        fade = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.012)
        parts.append(np.sin(2 * np.pi * freq * t) * fade)
    tone = np.concatenate(parts) * 0.12
    return (tone * 32767).astype("<i2").tobytes()


class PcmPlayer:
    """Spielt int16-mono-PCM über PortAudio. Ein Ausgabestrom bleibt über mehrere Sätze offen
    (keine Klicks zwischen Sätzen) und wird mit ``close()`` beendet.

    ``level`` (0..1) wird im Audio-Thread aktualisiert und von außen abgefragt.
    """

    PREBUFFER_S = 0.12  # erst nach ~120 ms Audio loslegen (sonst Aussetzer bei Streaming)

    def __init__(self, sd_loader: Callable[[], Any] = load_sounddevice, device: str | int | None = None):
        self._sd_loader = sd_loader
        self.device_spec = device
        self.level = 0.0
        self._meter = LevelMeter()
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._stream: Any = None
        self._rate = 0
        self._gate_open = False
        self._feeding_done = True
        self._drained = threading.Event()
        self._prebuffer = 0

    # ---- Audio-Thread --------------------------------------------------------------------
    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        need = frames * 2
        with self._lock:
            if not self._gate_open and (len(self._buf) >= self._prebuffer or self._feeding_done):
                self._gate_open = True
            n = min(need, len(self._buf)) if self._gate_open else 0
            data = bytes(self._buf[:n])
            del self._buf[:n]
            if self._gate_open and not self._buf:
                if self._feeding_done:
                    self._drained.set()
                else:
                    self._gate_open = False  # Puffer leer → kurz nachladen lassen
        arr = np.frombuffer(data, dtype="<i2")
        out = outdata[:, 0]
        out[: len(arr)] = arr
        out[len(arr):] = 0
        self.level = self._meter.update(arr)

    # ---- Steuerung (Sprach-Thread) ----------------------------------------------------------
    def _ensure_stream(self, sample_rate: int) -> None:
        if self._stream is not None and self._rate == sample_rate:
            return
        self.close()
        sd = self._sd_loader()
        device = resolve_device(sd, self.device_spec, "output")
        try:
            stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16",
                                     blocksize=max(64, sample_rate // 50), latency="low",
                                     device=device, callback=self._callback)
            stream.start()
        except Exception as exc:
            raise AudioUnavailable(f"Lautsprecher lässt sich nicht öffnen: {exc}") from exc
        self._stream, self._rate = stream, sample_rate
        self._prebuffer = int(sample_rate * 2 * self.PREBUFFER_S)

    def play(self, sample_rate: int, chunks: Iterable[bytes], cancel: threading.Event) -> bool:
        """Blockiert bis der Satz gespielt ist. ``False`` = abgebrochen (``cancel`` gesetzt)."""
        self._ensure_stream(sample_rate)
        with self._lock:
            self._buf.clear()
            self._gate_open = False
            self._feeding_done = False
            self._drained.clear()
        try:
            for chunk in chunks:
                if cancel.is_set():
                    break
                with self._lock:
                    self._buf.extend(chunk)
        finally:
            with self._lock:
                self._feeding_done = True
                if not self._buf:
                    self._drained.set()
        if not cancel.is_set():
            with self._lock:
                remaining = len(self._buf) / (2 * sample_rate)
            deadline = time.monotonic() + remaining + 3.0  # Schutz, falls der Strom hängt
            while not self._drained.wait(0.02):
                if cancel.is_set() or time.monotonic() > deadline:
                    break
        if cancel.is_set():
            self.abort()
            return False
        return True

    def abort(self) -> None:
        """Sofort verstummen (Unterbrechung)."""
        stream, self._stream = self._stream, None
        with self._lock:
            self._buf.clear()
            self._drained.set()
        if stream is not None:
            for call in (stream.abort, stream.close):
                try:
                    call()
                except Exception as exc:
                    log.debug("Lautsprecher abbrechen: %s", exc)
        self._reset_level()

    def close(self) -> None:
        """Nach dem letzten Satz: Strom ausklingen lassen und schließen."""
        stream, self._stream = self._stream, None
        if stream is not None:
            for call in (stream.stop, stream.close):
                try:
                    call()
                except Exception as exc:
                    log.debug("Lautsprecher schließen: %s", exc)
        self._reset_level()

    def _reset_level(self) -> None:
        self._meter.reset()
        self.level = 0.0
