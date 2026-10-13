"""Lädt Spracherkennungs- und Stimmmodelle vorab (z. B. während der Installation)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


def prefetch(settings: Any, data_dir: Path, progress: Callable[[str], None] = print) -> list[str]:
    """Lädt alles, was die Stimme braucht, an dieselben Orte wie zur Laufzeit. Wirft nie."""
    from .stt import SttUnavailable, create_stt
    from .tts import PiperVoiceStore, piper_installed

    voice = settings.voice
    lines: list[str] = []
    progress("Spracherkennung wird vorbereitet (einmaliger Download) …")
    try:
        engine = create_stt(voice, Path(data_dir))
        lines.append(f"Spracherkennung bereit: {getattr(engine, 'name', 'Whisper')}")
    except SttUnavailable as exc:
        lines.append(f"Spracherkennung nicht bereit: {exc}")
    except Exception as exc:  # pragma: no cover - abhängig von Netz/Modellen
        lines.append(f"Spracherkennung nicht bereit: {exc}")
    name = voice.piper_voice
    if not piper_installed():
        lines.append("Piper (Offline-Stimme) ist nicht installiert – Stimme nutzt ElevenLabs/OpenAI/macOS.")
        return lines
    store = PiperVoiceStore(Path(data_dir) / "voices")
    if store.installed(name):
        lines.append(f"Piper-Stimme {name} bereit")
        return lines
    progress(f"Lade Offline-Stimme {name} …")
    try:
        store.download(name)
        lines.append(f"Piper-Stimme {name} bereit")
    except Exception as exc:  # pragma: no cover - abhängig vom Netz
        lines.append(f"Piper-Stimme {name} fehlt: {exc}")
    return lines
