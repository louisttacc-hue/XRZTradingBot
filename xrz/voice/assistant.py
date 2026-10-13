"""Vincents Stimme: immer aktives Weckwort "Hey Vincent", deutsche Spracherkennung,
realistische Sprachausgabe und Pegel für die Avatar-Animation.

Ablauf: RUHE (lauscht auf "Hey Vincent") → Weckwort → kurzer Bestätigungston → ZUHÖREN bis
900 ms Stille (max. 30 s; 6 s Zeit zum Anfangen) → NACHDENKEN (Text an das Gehirn) →
SPRECHEN (Satz für Satz mit Untertitel) → NACHFRAGE-FENSTER (ohne Weckwort weiterreden) → RUHE.

Threads:
* PortAudio-Callback → Warteschlange (nur kopieren, nie blockieren)
* "vincent-voice": VAD, Weckwort-Prüfung und Spracherkennung (MLX braucht EINEN festen Thread)
* "vincent-tts": Sprachausgabe (Erzeugung + Wiedergabe) und Pegel für den Avatar
Alles, was in die asyncio-Welt geht, läuft über ``emit()`` (thread-sicher) bzw.
``asyncio.run_coroutine_threadsafe``. Halbduplex: Während Vincent spricht (+350 ms) wird das
Mikrofon ignoriert – er hört sich nie selbst.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import numpy as np

from .audio import AudioUnavailable, LevelMeter, MicStream, PcmPlayer, beep_pcm, check_input_device, \
    load_sounddevice, resolve_device
from .stt import DOMAIN_PROMPT, SttEngine, SttUnavailable, create_stt, is_hallucination
from .tts import PiperVoiceStore, TTSChain, TTSProvider, build_providers, list_voices_for, provider_order
from .vad import SR, WIN, Endpointer, SileroVAD, SpeechProb
from .wake import WakeMatcher

log = logging.getLogger(__name__)

OFF, IDLE, LISTENING, THINKING, SPEAKING, MUTED, ERROR = (
    "off", "idle", "listening", "thinking", "speaking", "muted", "error")

LOADING_TEXT = "Lade Spracherkennung …"
SILENCE_TEXT = (
    "Das Mikrofon liefert nur Stille. Bitte erlaube den Mikrofonzugriff in Systemeinstellungen → "
    "Datenschutz & Sicherheit → Mikrofon (für XRZ Vincent bzw. dein Terminal) und starte die App "
    "danach neu. Prüfe auch, ob das Mikrofon stummgeschaltet ist."
)
STALL_TEXT = "Das Mikrofon liefert keine Daten mehr – ich versuche es neu zu öffnen."
NO_VOICE_TEXT = ("Keine Stimme verfügbar – bitte in den Einstellungen einen ElevenLabs- oder "
                 "OpenAI-Key eintragen oder kurz warten, bis die Offline-Stimme geladen ist.")
REPLY_ERROR_TEXT = "Entschuldige, da ist bei mir gerade etwas schiefgelaufen."
LEVEL_INTERVAL_S = 1 / 15  # Pegel für den Avatar mit ~15 Hz


@dataclass
class VoiceBackends:
    """Austauschbare Bausteine – Tests ersetzen sie durch Attrappen."""

    load_sounddevice: Callable[[], Any] = load_sounddevice
    make_vad: Callable[[], SpeechProb] = SileroVAD
    make_stt: Callable[[Any, Path], SttEngine] = create_stt
    make_providers: Callable[..., list[TTSProvider]] = build_providers
    piper_downloader: Callable[[str, Path], None] | None = None
    http_transport: httpx.BaseTransport | None = None


@dataclass
class _SpeakJob:
    text: str
    follow_up: bool = False  # danach ohne Weckwort weiter zuhören
    turn: int | None = None  # Antwort auf diese Spracheingabe (veraltet → nicht sprechen)
    cue: bool = False  # nur Bestätigungston
    generation: int = 0


class VoiceAssistant:
    def __init__(self, settings: Any, secrets: Any, data_dir: Path,
                 emit: Callable[[str, dict[str, Any]], None],
                 on_utterance: Callable[[str], Awaitable[str | None]],
                 *, backends: VoiceBackends | None = None):
        self._settings_get: Callable[[], Any] = settings if callable(settings) else (lambda: settings)
        self.secrets = secrets
        self.data_dir = Path(data_dir)
        self._emit_fn = emit
        self._on_utterance = on_utterance
        self.backends = backends or VoiceBackends()
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._muted = False
        self._speaking = False
        self._mode = IDLE  # Zustand der Spracherkennung (Ruhe/Zuhören/Nachdenken/Sprechen)
        self._faults: dict[str, tuple[str, str]] = {}  # Quelle → (Zustand, Meldung)
        self._tts_error = ""
        self._stt_name = ""
        self._turn = 0
        self._published: tuple[str, str] | None = None
        self._http = httpx.Client(transport=self.backends.http_transport)
        self._piper = PiperVoiceStore(self.data_dir / "voices", self.backends.piper_downloader)
        self.player = PcmPlayer(self.backends.load_sounddevice)
        self.chain = TTSChain(self._providers, self.player)
        self._listener: _Listener | None = None
        self._speaker: _Speaker | None = None

    # ------------------------------------------------------------------ #
    # Öffentliche Schnittstelle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Startet Mikrofon-Thread und Sprachausgabe. Wirft nie; Probleme stehen in ``status()``."""
        try:
            self._loop = asyncio.get_running_loop()
            with self._lock:
                if self._started:
                    return
                self._started = True
                if self._http.is_closed:
                    self._http = httpx.Client(transport=self.backends.http_transport)
            self._ensure_speaker()
            self._ensure_listener()
            self._ensure_piper_voice()
        except Exception as exc:
            log.exception("Sprachsteuerung konnte nicht starten")
            self._set_fault("start", ERROR, f"Sprachsteuerung konnte nicht starten: {exc}")
        self._publish()

    async def stop(self) -> None:
        with self._lock:
            self._started = False
            self._turn += 1
            listener, speaker = self._listener, self._speaker
            self._listener = self._speaker = None
        for worker in (listener, speaker):
            if worker is not None:
                worker.request_stop()
        await asyncio.to_thread(self._join, listener, speaker)
        self._http.close()
        with self._lock:
            self._mode, self._speaking = IDLE, False
        self._publish()

    async def reload(self) -> None:
        """Einstellungen und API-Keys neu einlesen (nach Änderungen in der App, ohne Neustart).

        Wake-Wort, Geräte, Modelle und Stimmen liest der Sprach-Thread ohnehin laufend; hier
        werden Anbieter-Pausen (z. B. nach falschem Key) aufgehoben, Stimmenlisten erneuert und
        ein beendeter Mikrofon-Thread neu gestartet."""
        try:
            self.chain.reset_cooldowns()
            with self._lock:
                self._tts_error = ""
                self._faults.pop("start", None)
                started = self._started
            if started:
                self._ensure_speaker()
                self._ensure_listener()
                self._ensure_piper_voice()
        except Exception as exc:
            log.exception("Neu-Laden der Spracheinstellungen fehlgeschlagen")
            self._set_fault("start", ERROR, f"Spracheinstellungen konnten nicht übernommen werden: {exc}")
        self._publish()

    async def listen_now(self) -> None:
        """Push-to-Talk: ohne Weckwort zuhören (endet bei Stille). Erneut drücken = fertig/abbrechen."""
        self._capture_loop()
        if not self._voice().enabled:
            log.info("Push-to-Talk ignoriert: Sprachsteuerung ist ausgeschaltet")
            return
        self._ensure_listener()
        if self._speaking and self._speaker is not None:
            self._speaker.cancel_all()  # wer drückt, will reden – Vincent verstummt
        self._post(("ptt",))

    async def stop_all(self) -> None:
        with self._lock:
            self._turn += 1  # ausstehende Antworten nicht mehr vorlesen
        if self._speaker is not None:
            self._speaker.cancel_all()
        self._post(("reset",))
        self._emit("subtitle", {"text": ""})

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            self._muted = bool(muted)
        self._post(("mute", bool(muted)))
        self._publish()

    async def say(self, text: str, interrupt: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._capture_loop()
        speaker = self._ensure_speaker()
        if interrupt:
            speaker.cancel_all()
        speaker.submit(_SpeakJob(text))

    async def list_voices(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._list_voices_sync)

    def status(self) -> dict[str, Any]:
        v = self._voice()
        state, message = self._visible()
        try:
            active = self.chain.active()
        except Exception as exc:
            log.debug("TTS-Status: %s", exc)
            active = None
        return {
            "state": state,
            "wake_word": WakeMatcher(v.assistant_name).phrase,
            "wake_enabled": bool(v.wake_word_enabled),
            "muted": self._muted,
            "tts": active.name if active else "",
            "stt": self._stt_name,
            "error": message,
        }

    # ------------------------------------------------------------------ #
    # Intern: Zustand
    # ------------------------------------------------------------------ #
    def _voice(self) -> Any:
        return self._settings_get().voice

    def _providers(self) -> list[TTSProvider]:
        return self.backends.make_providers(self._settings_get(), self.secrets, self.data_dir,
                                            self._http, self._piper)

    def _ensure_piper_voice(self) -> None:
        """Offline-Stimme im Hintergrund laden, falls Piper in der Kette vorkommt (nicht blockierend)."""
        v = self._voice()
        if "piper" in provider_order(v.tts_provider):
            self._piper.ensure(v.piper_voice)

    def _visible(self) -> tuple[str, str]:
        v = self._voice()
        with self._lock:
            if self._speaking or self._mode == SPEAKING:
                active = SPEAKING
            elif self._mode in (LISTENING, THINKING):
                active = self._mode
            else:
                active = ""
            faults = sorted(self._faults.values(), key=lambda f: (OFF, ERROR, IDLE).index(f[0]))
            message = faults[0][1] if faults else self._tts_error
            if not self._started or not v.enabled:
                rest, message = OFF, ""
            elif faults and faults[0][0] in (OFF, ERROR):
                rest = faults[0][0]
            elif self._muted:
                rest = MUTED
            else:
                rest = IDLE
            return (active or rest), message

    def _publish(self) -> None:
        with self._lock:
            state, message = self._visible()
            if (state, message) == self._published:
                return
            self._published = (state, message)
            self._emit("voice.state", {"state": state, "error": message})

    def _emit(self, type_: str, data: dict[str, Any]) -> None:
        try:
            self._emit_fn(type_, data)
        except Exception as exc:  # ein Empfänger darf die Sprach-Threads nie stoppen
            log.debug("emit(%s) fehlgeschlagen: %s", type_, exc)

    def _set_fault(self, source: str, state: str, message: str) -> None:
        with self._lock:
            if message:
                self._faults[source] = (state, message)
            else:
                self._faults.pop(source, None)
        self._publish()

    def _fault_message(self, source: str) -> str:
        with self._lock:
            return self._faults.get(source, ("", ""))[1]

    def _set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = mode
        self._publish()

    def _new_turn(self) -> int:
        with self._lock:
            self._turn += 1
            return self._turn

    def _current_turn(self) -> int:
        with self._lock:
            return self._turn

    def _capture_loop(self) -> None:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def _ensure_listener(self) -> None:
        with self._lock:
            if not self._started or (self._listener is not None and self._listener.alive()):
                return
            self._listener = _Listener(self)
            self._listener.start()

    def _ensure_speaker(self) -> _Speaker:
        with self._lock:
            if self._speaker is None or not self._speaker.alive():
                self._speaker = _Speaker(self)
                self._speaker.start()
            return self._speaker

    def _post(self, msg: tuple[Any, ...]) -> None:
        listener = self._listener
        if listener is not None:
            listener.ctl.put(msg)

    @staticmethod
    def _join(*workers: Any) -> None:
        for worker in workers:
            if worker is not None:
                worker.join(3.0)

    # ------------------------------------------------------------------ #
    # Intern: Brücke zwischen Threads und asyncio
    # ------------------------------------------------------------------ #
    def _dispatch(self, text: str, turn: int) -> None:
        """Vom Sprach-Thread: erkannter Befehl → Chat-Ereignis → on_utterance im Event-Loop."""
        self._emit("chat", {"role": "user", "text": text, "source": "voice"})
        loop = self._loop
        if loop is None or loop.is_closed():
            self._post(("reply_done", turn, False))
            return
        try:
            asyncio.run_coroutine_threadsafe(self._handle_utterance(text, turn), loop)
        except RuntimeError:
            self._post(("reply_done", turn, False))

    async def _handle_utterance(self, text: str, turn: int) -> None:
        try:
            reply = await self._on_utterance(text)
        except Exception:
            log.exception("Spracheingabe konnte nicht verarbeitet werden")
            reply = REPLY_ERROR_TEXT
        if turn != self._current_turn():
            return  # inzwischen abgebrochen oder neue Eingabe
        reply = str(reply or "").strip()
        if reply and self._voice().speak_replies:
            self._ensure_speaker().submit(_SpeakJob(reply, follow_up=True, turn=turn))
        else:
            self._post(("reply_done", turn, bool(reply)))

    def _output_begin(self) -> None:
        listener = self._listener
        if listener is not None:
            listener.output_active.set()  # sofort: ab jetzt kein Mikrofon-Audio mehr auswerten
            listener.ctl.put(("speaking", True, False, time.monotonic()))
        with self._lock:
            self._speaking = True
        self._publish()

    def _output_end(self, follow_up: bool) -> None:
        self._post(("speaking", False, follow_up, time.monotonic()))
        with self._lock:
            self._speaking = False
        self._publish()

    def _cue(self) -> None:
        speaker = self._speaker
        if speaker is not None:
            speaker.cue()

    def _list_voices_sync(self) -> dict[str, Any]:
        provider = self.chain.active()
        name = provider.name if provider else ""
        try:
            voices = list_voices_for(provider, self._http, self._piper)
        except Exception as exc:
            log.warning("Stimmen von %s nicht abrufbar: %s", name, exc)
            return {"provider": name, "voices": [], "error": f"Stimmen konnten nicht geladen werden: {exc}"}
        return {"provider": name, "voices": voices}


# ---------------------------------------------------------------------- #
# Sprachausgabe-Thread
# ---------------------------------------------------------------------- #
class _Speaker:
    def __init__(self, va: VoiceAssistant):
        self.va = va
        self.jobs: queue.Queue[_SpeakJob | None] = queue.Queue()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._generation = 0
        self._busy = False
        self._output_open = False
        self._follow_up = False
        self._thread = threading.Thread(target=self._run, name="vincent-tts", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def request_stop(self) -> None:
        self.cancel_all()
        self.jobs.put(None)

    def submit(self, job: _SpeakJob) -> None:
        with self._lock:
            job.generation = self._generation
        self.jobs.put(job)

    def cue(self) -> None:
        """Bestätigungston – nur wenn gerade nichts gesprochen wird."""
        with self._lock:
            if self._busy or not self.jobs.empty():
                return
            generation = self._generation
        self.jobs.put(_SpeakJob("", cue=True, generation=generation))

    def cancel_all(self) -> None:
        with self._lock:
            self._generation += 1
            self._cancel.set()
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            if job is None:  # Stopp-Signal erhalten
                self.jobs.put(None)
                break

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                break
            try:
                self._handle(job)
            except Exception:
                log.exception("Sprachausgabe fehlgeschlagen")
            if self._output_open and self.jobs.empty():
                self._output_open = False
                self.va._output_end(self._follow_up)
        if self._output_open:
            self._output_open = False
            self.va._output_end(False)

    def _handle(self, job: _SpeakJob) -> None:
        with self._lock:
            if job.generation != self._generation:
                return  # abgebrochen
            cancel = self._cancel = threading.Event()
            self._busy = True
        try:
            if job.cue:
                self._play_cue(cancel)
                return
            if job.turn is not None and job.turn != self.va._current_turn():
                return  # veraltete Antwort
            self._speak(job, cancel)
        finally:
            with self._lock:
                self._busy = False

    def _play_cue(self, cancel: threading.Event) -> None:
        player = self.va.player
        player.device_spec = self.va._voice().output_device
        try:
            player.play(24000, [beep_pcm(24000)], cancel)
            player.close()
        except AudioUnavailable as exc:
            log.debug("Bestätigungston nicht möglich: %s", exc)

    def _speak(self, job: _SpeakJob, cancel: threading.Event) -> None:
        va, player = self.va, self.va.player
        player.device_spec = va._voice().output_device
        va._ensure_piper_voice()
        if not self._output_open:
            self._output_open = True
            va._output_begin()
        self._follow_up = job.follow_up
        stop_levels = threading.Event()
        pump = threading.Thread(target=self._pump_levels, args=(stop_levels,), name="vincent-tts-level",
                                daemon=True)
        pump.start()
        try:
            used = va.chain.speak(job.text, cancel,
                                  on_sentence=lambda s: va._emit("subtitle", {"text": s}))
            with va._lock:
                if used and all(u is None for u in used):
                    va._tts_error = NO_VOICE_TEXT
                elif used:
                    va._tts_error = ""
        except AudioUnavailable as exc:
            log.warning("Sprachausgabe: %s", exc)
            with va._lock:
                va._tts_error = str(exc)
        finally:
            player.close()
            stop_levels.set()
            pump.join(1.0)
            va._emit("voice.level", {"level": 0.0, "source": "tts"})
            va._emit("subtitle", {"text": ""})
        if cancel.is_set():
            self._follow_up = False
        va._publish()

    def _pump_levels(self, stop: threading.Event) -> None:
        last = -1.0
        while not stop.wait(LEVEL_INTERVAL_S):
            level = round(float(self.va.player.level), 3)
            if abs(level - last) >= 0.01:
                last = level
                self.va._emit("voice.level", {"level": level, "source": "tts"})


# ---------------------------------------------------------------------- #
# Mikrofon-/Erkennungs-Thread
# ---------------------------------------------------------------------- #
class _Listener:
    PREROLL_S = 0.6  # Audio vor dem VAD-Start behalten (VAD braucht ~200 ms zur Bestätigung)
    WAKE_PROBE_S = 1.8  # so weit in eine Äußerung hinein erstmals auf "Hey Vincent" prüfen
    PROBE_EVERY_S = 1.5  # bei langem Reden (Fernseher) alle 1,5 s die letzten 3 s prüfen
    PROBE_WIN_S = 3.0
    MAX_WAKE_SEG_S = 6.0
    IDLE_SILENCE_MS = 500
    COMMAND_SILENCE_MS = 900
    LISTEN_TIMEOUT_S = 6.0  # nach dem Weckwort so lange Zeit, mit dem Befehl anzufangen
    MAX_UTTER_S = 30.0
    TTS_TAIL_S = 0.35  # Nachhall: so lange nach dem Sprechen das Mikrofon weiter ignorieren
    THINK_TIMEOUT_S = 120.0  # danach wieder auf das Weckwort hören (Antwort kommt trotzdem)
    DIGITAL_SILENCE_S = 3.0  # verweigerter Mikrofonzugriff liefert exakte Nullen
    STALL_S = 3.0
    RETRY_MIC_S = 5.0
    RETRY_STT_S = 60.0
    FOLLOW_UP_MAX_DELAY_S = 1.5

    def __init__(self, va: VoiceAssistant):
        self.va = va
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=600)  # ~19 s Audio
        self.ctl: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.output_active = threading.Event()
        self.dropped = 0  # verworfene Blöcke (Warteschlange voll)
        self._last_audio_at = 0.0  # letzter PortAudio-Callback (für die Hänger-Erkennung)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vincent-voice", daemon=True)
        self.ep: Endpointer | None = None
        self.stt: SttEngine | None = None
        self.matcher = WakeMatcher()
        self.mode = IDLE
        self._sd: Any = None
        self._mic: MicStream | None = None
        self._mic_spec = ""
        self._mic_retry_at = 0.0
        self._stt_key: tuple[Any, ...] | None = None
        self._stt_retry_at = 0.0
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=int(self.PREROLL_S * SR / WIN) + 1)
        self._seg: list[np.ndarray] = []
        self._in_seg = False
        self._clock = 0  # verarbeitete Samples (Audio-Zeit)
        self._listen_deadline = 0
        self._mute_until = 0
        self._think_deadline = 0.0
        self._listen_kind = ""
        self._conversation = False
        self._silent_samples = 0
        self._mic_dead = False
        self._meter = LevelMeter()
        self._last_level_at = 0.0
        self._end_seg()

    # ---- Lebenszyklus ----------------------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def request_stop(self) -> None:
        self._stop.set()

    def _on_audio(self, block: np.ndarray) -> None:  # PortAudio-Thread
        self._last_audio_at = time.monotonic()
        try:
            self.q.put_nowait(block)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        try:
            self._main()
        except Exception as exc:
            log.exception("Sprach-Thread abgestürzt")
            self.va._set_fault("listener", ERROR, f"Spracherkennung abgestürzt ({exc}) – bitte App neu starten.")
        finally:
            self._close_mic()
            self.mode = IDLE
            self.va._set_mode(IDLE)

    def _main(self) -> None:
        va = self.va
        while not self._stop.is_set():
            v = va._voice()
            va._publish()
            if not v.enabled:
                self._close_mic()
                if self.mode != IDLE or self._conversation:
                    self._to_idle()
                self._handle_control(v)
                self._stop.wait(0.3)
                continue
            if not self._prepare(v):
                return
            if self.stt is None:
                self._handle_control(v)
                self._stop.wait(0.3)
                continue
            if v.assistant_name != self.matcher.name:
                self.matcher = WakeMatcher(v.assistant_name)
            self._reconcile_mic(v)
            try:
                block: np.ndarray | None = self.q.get(timeout=0.05)
            except queue.Empty:
                block = None
            self._handle_control(v)
            if block is not None:
                self._process_block(block, v)
            self._check_timers()

    def _prepare(self, v: Any) -> bool:
        """Lädt Audio, VAD und Spracherkennung. False = fehlende Software (Thread endet)."""
        va = self.va
        try:
            if self._sd is None:
                self._sd = va.backends.load_sounddevice()
            if self.ep is None:
                self.ep = Endpointer(va.backends.make_vad(), min_silence_ms=self.IDLE_SILENCE_MS)
        except AudioUnavailable as exc:
            va._set_fault("deps", OFF if exc.missing_package else ERROR, str(exc))
            return False
        except ImportError as exc:
            va._set_fault("deps", OFF, f"Paket fehlt: {exc.name or exc} – bitte „pip install onnxruntime“ "
                                       "ausführen.")
            return False
        except Exception as exc:
            log.exception("Sprachaktivitätserkennung konnte nicht geladen werden")
            va._set_fault("deps", ERROR, f"Sprachaktivitätserkennung konnte nicht geladen werden: {exc}")
            return False
        key = (v.stt_backend, v.stt_model, v.stt_fast_model)
        if self.stt is not None and key == self._stt_key:
            return True
        if time.monotonic() < self._stt_retry_at:
            return True
        self._close_mic()
        va._set_fault("stt", IDLE, LOADING_TEXT)
        try:
            stt = va.backends.make_stt(v, va.data_dir)
        except SttUnavailable as exc:
            if exc.missing_package:
                va._set_fault("stt", OFF, str(exc))
                return False
            va._set_fault("stt", ERROR, str(exc))
            self._stt_retry_at = time.monotonic() + self.RETRY_STT_S
            return True
        except Exception as exc:
            log.exception("Spracherkennung konnte nicht geladen werden")
            va._set_fault("stt", ERROR, f"Spracherkennung konnte nicht geladen werden: {exc}")
            self._stt_retry_at = time.monotonic() + self.RETRY_STT_S
            return True
        self.stt, self._stt_key = stt, key
        va._stt_name = getattr(stt, "name", "")
        va._set_fault("stt", "", "")
        log.info("Spracherkennung bereit: %s", va._stt_name)
        return True

    # ---- Mikrofon --------------------------------------------------------------------------
    def _want_mic(self, v: Any) -> bool:
        return self._conversation or (bool(v.wake_word_enabled) and not self.va._muted)

    def _reconcile_mic(self, v: Any) -> None:
        want = self._want_mic(v)
        spec = str(v.input_device or "")
        now = time.monotonic()
        if self._mic is not None and (not want or spec != self._mic_spec):
            self._close_mic()
        if self._mic is not None and (not self._mic.active or now - self._last_audio_at > self.STALL_S):
            log.warning("Mikrofon liefert keine Daten mehr – öffne es neu")
            self._close_mic()
            self.va._set_fault("mic", ERROR, STALL_TEXT)
            self._mic_retry_at = now + self.RETRY_MIC_S
        if self._mic is None and want and now >= self._mic_retry_at:
            try:
                device = resolve_device(self._sd, spec, "input")
                check_input_device(self._sd, device)
                mic = MicStream(self._sd, device, self._on_audio)
                mic.open()
            except AudioUnavailable as exc:
                self.va._set_fault("mic", ERROR, str(exc))
                self._mic_retry_at = now + self.RETRY_MIC_S
                return
            self._mic, self._mic_spec = mic, spec
            self._last_audio_at = now
            self._silent_samples = 0
            if self.va._fault_message("mic") != SILENCE_TEXT:
                self.va._set_fault("mic", "", "")

    def _close_mic(self) -> None:
        if self._mic is not None:
            self._mic.close()
            self._mic = None
            self._flush()

    def _check_signal(self, block: np.ndarray) -> None:
        if block.size and float(np.max(np.abs(block))) > 1e-6:
            self._silent_samples = 0
            if self._mic_dead or self.va._fault_message("mic"):
                self._mic_dead = False
                self.va._set_fault("mic", "", "")
            return
        self._silent_samples += block.size
        if not self._mic_dead and self._silent_samples >= self.DIGITAL_SILENCE_S * SR:
            log.warning("Mikrofon liefert digitale Stille (Zugriff verweigert oder stumm?)")
            self._mic_dead = True
            self._flush()
            self.va._set_fault("mic", ERROR, SILENCE_TEXT)

    # ---- Steuerbefehle aus anderen Threads -------------------------------------------------
    def _handle_control(self, v: Any) -> None:
        while True:
            try:
                msg = self.ctl.get_nowait()
            except queue.Empty:
                return
            kind = msg[0]
            if kind == "speaking":
                self._on_speaking(bool(msg[1]), bool(msg[2]), float(msg[3]), v)
            elif self.stt is None or not v.enabled:
                continue  # ohne Spracherkennung keine Zuhör-Befehle
            elif kind == "ptt":
                self._on_ptt()
            elif kind == "reset":
                self._flush()
                self._to_idle()
            elif kind == "mute":
                if msg[1] and self.mode == LISTENING:
                    self._flush()
                    self._to_idle()
            elif kind == "reply_done":
                turn, follow_up = msg[1], msg[2]
                if self.mode == THINKING and turn == self.va._current_turn():
                    if follow_up and not self.va._muted:
                        self._enter_listening("followup", cue=False)
                    else:
                        self._to_idle()

    def _on_ptt(self) -> None:
        if self.mode == LISTENING:  # nochmal gedrückt: fertig (falls schon gesprochen) bzw. abbrechen
            if self._in_seg and len(self._seg) * WIN >= 0.3 * SR:
                self._finish_command()
            else:
                self._to_idle()
            return
        self._flush()
        self.va._new_turn()  # eine noch ausstehende Antwort wird nicht mehr vorgelesen
        self._conversation = True
        self._enter_listening("ptt", cue=True)

    def _on_speaking(self, active: bool, follow_up: bool, posted_at: float, v: Any) -> None:
        if active:
            self.output_active.set()
            self._flush()
            if self.mode in (IDLE, LISTENING, THINKING):
                self.mode = SPEAKING
                self.va._set_mode(SPEAKING)
            return
        self.output_active.clear()
        self._flush()
        self._mute_until = self._clock + int(self.TTS_TAIL_S * SR)
        if self.mode != SPEAKING:
            return  # inzwischen per Push-to-Talk o. Ä. weiter
        fresh = time.monotonic() - posted_at < self.FOLLOW_UP_MAX_DELAY_S
        if (follow_up and fresh and self.stt is not None and v.enabled and not self.va._muted
                and not self._mic_dead):
            self._conversation = True
            self._enter_listening("followup", cue=False)
        else:
            self._to_idle()

    # ---- Audio-Verarbeitung ----------------------------------------------------------------
    def _process_block(self, block: np.ndarray, v: Any) -> None:
        if self.dropped:
            log.warning("%d Audio-Blöcke verworfen (Spracherkennung zu langsam?)", self.dropped)
            self.dropped = 0
        self._check_signal(block)
        self._pending = np.concatenate([self._pending, block.astype(np.float32, copy=False)])
        while len(self._pending) >= WIN:
            win, self._pending = self._pending[:WIN], self._pending[WIN:]
            self._clock += WIN
            self._process_window(win, v)
            if self.mode == LISTENING and not self._in_seg and self._clock > self._listen_deadline:
                self._to_idle()

    def _process_window(self, win: np.ndarray, v: Any) -> None:
        if self.output_active.is_set() or self._clock < self._mute_until or self._mic_dead:
            return  # Halbduplex: Vincent hört sich nie selbst
        if self.mode == LISTENING:
            self._mic_level(win)
        elif self.mode != IDLE or not v.wake_word_enabled or self.va._muted:
            return
        assert self.ep is not None
        ev, _p = self.ep.feed(win)
        if not self._in_seg:
            self._preroll.append(win)
            if ev == "start":
                self._in_seg = True
                self._seg = list(self._preroll)
                self._preroll.clear()
                self._probe_at = int(self.WAKE_PROBE_S * SR / WIN)
            return
        self._seg.append(win)
        if self.mode == IDLE:
            self._idle_segment(ev)
        elif ev == "end" or len(self._seg) * WIN >= self.MAX_UTTER_S * SR:
            self._finish_command()

    def _idle_segment(self, ev: str | None) -> None:
        n = len(self._seg)
        max_wake = int(self.MAX_WAKE_SEG_S * SR / WIN)
        if ev == "end":
            trailing = self._trimmed or n > max_wake
            self._wake_check(self._seg[-max_wake:], final=True, max_pos=12 if trailing else 3)
            if self.mode == IDLE:
                self._end_seg()
            return
        if n < self._probe_at:
            return
        if self._probed:  # weitere Prüfungen: nur das letzte Stück (begrenzt auch den Speicher)
            self._seg = self._seg[-int(self.PROBE_WIN_S * SR / WIN):]
            self._trimmed = True
        self._probed = True
        self._probe_at = len(self._seg) + int(self.PROBE_EVERY_S * SR / WIN)
        self._wake_check(self._seg, final=False, max_pos=12 if self._trimmed else 3)

    def _wake_check(self, windows: list[np.ndarray], final: bool, max_pos: int) -> None:
        assert self.stt is not None and self.ep is not None
        r = self.stt.transcribe(np.concatenate(windows), fast=True, prompt=None)  # nie mit Prompt!
        if r.no_speech_prob > 0.6 or r.avg_logprob < -1.0:
            return
        hit = self.matcher.find(r.text, require_prefix=True, max_pos=max_pos)
        log.debug("Weckwort-Prüfung %r → %s", r.text, bool(hit))
        if hit is None:
            return
        log.info("Weckwort erkannt: %r", r.text)
        self._conversation = True
        if final:
            audio = np.concatenate(self._seg)
            self._end_seg()
            if hit.rest_words >= 2:  # "Hey Vincent, wie steht Bitcoin?" in einem Atemzug
                self._command(audio, strip=True, max_pos=max_pos)
            else:
                self._enter_listening("wake", cue=True)
            return
        # Nutzer spricht noch: dieselbe Äußerung weiter aufnehmen, Weckwort später abschneiden
        self._strip, self._strip_max_pos = True, max_pos
        self._wake_only = hit.rest_words == 0
        self._wake_hit_at = len(self._seg)
        self.ep.set_min_silence(self.COMMAND_SILENCE_MS)
        self._listen_kind = "wake"
        self.mode = LISTENING
        self.va._set_mode(LISTENING)
        if self._wake_only:
            self.va._cue()

    def _finish_command(self) -> None:
        audio = np.concatenate(self._seg)
        after_hit_s = (len(self._seg) - self._wake_hit_at) * WIN / SR
        only_wake = self._strip and self._wake_only and after_hit_s < self.COMMAND_SILENCE_MS / 1000 + 0.5
        strip, max_pos = self._strip, self._strip_max_pos
        self._end_seg()
        if only_wake:  # nur "Hey Vincent" + Pause: jetzt auf den Befehl warten
            self._enter_listening("wake", cue=False)
            return
        self._command(audio, strip=strip, max_pos=max_pos)

    def _command(self, audio: np.ndarray, strip: bool, max_pos: int) -> None:
        assert self.stt is not None
        self.mode = THINKING
        self.va._set_mode(THINKING)
        self._emit_mic_level(0.0)
        r = self.stt.transcribe(audio, fast=False, prompt=DOMAIN_PROMPT)
        text = r.text.strip()
        hit = self.matcher.find(text, require_prefix=not strip, max_pos=max_pos if strip else 3)
        if hit is not None:
            text = hit.rest
        if r.no_speech_prob > 0.8 or is_hallucination(text) or not any(ch.isalnum() for ch in text):
            if strip:  # nur das Weckwort verstanden → weiter zuhören
                self._enter_listening("wake", cue=False)
            else:
                self._to_idle()
            return
        turn = self.va._new_turn()
        self._think_deadline = time.monotonic() + self.THINK_TIMEOUT_S
        log.info("Spracheingabe: %r", text)
        self.va._dispatch(text, turn)

    def _enter_listening(self, kind: str, cue: bool) -> None:
        assert self.ep is not None
        self._end_seg()
        self.ep.reset()
        self.ep.set_min_silence(self.COMMAND_SILENCE_MS)
        seconds = float(self.va._voice().follow_up_seconds) if kind == "followup" else self.LISTEN_TIMEOUT_S
        self._listen_deadline = self._clock + int(seconds * SR)
        self._listen_kind = kind
        self._conversation = True
        self.mode = LISTENING
        self.va._set_mode(LISTENING)
        if cue:
            self.va._cue()

    def _to_idle(self) -> None:
        self._end_seg()
        if self.ep is not None:
            self.ep.reset()
            self.ep.set_min_silence(self.IDLE_SILENCE_MS)
        was_listening = self.mode == LISTENING
        self._conversation = False
        self._listen_kind = ""
        self.mode = IDLE
        self.va._set_mode(IDLE)
        if was_listening:
            self._emit_mic_level(0.0)

    def _end_seg(self) -> None:
        self._seg = []
        self._in_seg = False
        self._strip = False
        self._strip_max_pos = 3
        self._wake_only = False
        self._wake_hit_at = 0
        self._probed = False
        self._trimmed = False
        self._probe_at = int(self.WAKE_PROBE_S * SR / WIN)
        self._preroll.clear()

    def _flush(self) -> None:
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self._pending = np.zeros(0, dtype=np.float32)
        self._end_seg()
        if self.ep is not None:
            self.ep.reset()

    def _check_timers(self) -> None:
        if self.mode == THINKING and time.monotonic() > self._think_deadline:
            log.info("Antwort dauert – höre wieder auf das Weckwort (Antwort wird trotzdem vorgelesen)")
            self._to_idle()

    def _mic_level(self, win: np.ndarray) -> None:
        level = self._meter.update(win)
        now = time.monotonic()
        if now - self._last_level_at >= LEVEL_INTERVAL_S:
            self._last_level_at = now
            self.va._emit("voice.level", {"level": round(level, 3), "source": "mic"})

    def _emit_mic_level(self, level: float) -> None:
        self._meter.reset()
        self.va._emit("voice.level", {"level": level, "source": "mic"})
