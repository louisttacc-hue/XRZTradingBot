"""Sprachaktivitätserkennung: Silero VAD (v5/v6, ONNX) ohne torch – nur numpy + onnxruntime.

Modell: ``assets/silero_vad.onnx`` (MIT, siehe ``assets/LICENSE-silero-vad.txt``).
ONNX-Schnittstelle: Eingänge ``input`` float32 [1, 64+512], ``state`` float32 [2, 1, 128],
``sr`` int64; Ausgänge ``output`` [1, 1] (Sprachwahrscheinlichkeit), ``stateN`` [2, 1, 128].
Bei 16 kHz verarbeitet das Modell immer genau 512 Samples (32 ms) plus 64 Samples Kontext.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

SR = 16000  # Abtastrate der gesamten Spracheingabe
WIN = 512  # Samples pro VAD-Schritt (32 ms) – vom Modell vorgegeben
CTX = 64  # Kontext-Samples, die jedem Fenster vorangestellt werden
MODEL_PATH = Path(__file__).parent / "assets" / "silero_vad.onnx"


class SpeechProb(Protocol):
    """Alles, was pro 512-Sample-Fenster eine Sprachwahrscheinlichkeit liefert."""

    def reset(self) -> None: ...

    def prob(self, window: np.ndarray) -> float: ...


class SileroVAD:
    """Streaming-Silero-VAD für 16 kHz mono float32 ([-1, 1])."""

    def __init__(self, model_path: Path | str = MODEL_PATH):
        import onnxruntime as ort  # erst hier laden (schwer, und nur mit Mikrofon nötig)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self._sess = ort.InferenceSession(str(model_path), sess_options=opts,
                                          providers=["CPUExecutionProvider"])
        self._sr = np.array(SR, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros((1, CTX), dtype=np.float32)

    def prob(self, window: np.ndarray) -> float:
        x = np.asarray(window, dtype=np.float32).reshape(1, WIN)
        inp = np.concatenate([self._ctx, x], axis=1)
        out, self._state = self._sess.run(None, {"input": inp, "state": self._state, "sr": self._sr})
        self._ctx = inp[:, -CTX:]
        return float(out[0, 0])


class Endpointer:
    """Erkennt Anfang und Ende einer Äußerung (Hysterese wie Silero's VADIterator).

    ``feed()`` liefert pro Fenster ``("start" | "end" | None, wahrscheinlichkeit)``.
    """

    def __init__(self, vad: SpeechProb, threshold: float = 0.5,
                 min_silence_ms: int = 500, min_speech_ms: int = 200):
        self.vad = vad
        self.th_on = threshold
        self.th_off = max(threshold - 0.15, 0.01)
        self.min_sp = max(1, int(min_speech_ms * SR / 1000 / WIN))
        self.set_min_silence(min_silence_ms)
        self.reset()

    def set_min_silence(self, ms: int) -> None:
        """Wie lange Stille das Ende einer Äußerung markiert (Wach-Phase kürzer als Befehle)."""
        self.min_sil = max(1, int(ms * SR / 1000 / WIN))

    def reset(self) -> None:
        self.vad.reset()
        self.in_speech = False
        self.sil_run = 0
        self.sp_run = 0

    def feed(self, window: np.ndarray) -> tuple[str | None, float]:
        p = self.vad.prob(window)
        if not self.in_speech:
            self.sp_run = self.sp_run + 1 if p >= self.th_on else 0
            if self.sp_run >= self.min_sp:
                self.in_speech, self.sil_run = True, 0
                return "start", p
        else:
            if p < self.th_off:
                self.sil_run += 1
                if self.sil_run >= self.min_sil:
                    self.in_speech, self.sp_run = False, 0
                    return "end", p
            elif p >= self.th_on:
                self.sil_run = 0
        return None, p
