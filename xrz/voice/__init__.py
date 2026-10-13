"""Vincents Stimme: Wake-Word „Hey Vincent“, Spracherkennung und realistische Sprachausgabe.

Schwere Bibliotheken (onnxruntime, sounddevice, Whisper) werden erst beim Start geladen."""
from .assistant import VoiceAssistant
from .prefetch import prefetch

__all__ = ["VoiceAssistant", "prefetch"]
