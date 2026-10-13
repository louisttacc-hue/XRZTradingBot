"""macOS-Berechtigungen prüfen (und auf Wunsch anfragen).

* Bedienungshilfen (Accessibility) → nötig, damit Vincent Maus und Tastatur bedienen darf.
* Bildschirmaufnahme (Screen Recording) → nötig für Bildschirmfotos.

Alle Funktionen geben ``None`` zurück, wenn der Status unbekannt ist (kein Mac,
PyObjC fehlt, Fehler der Systemabfrage) – sie werfen nie.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

ACCESSIBILITY_HINT = (
    "Damit ich Maus und Tastatur bedienen darf, erlaube „XRZ Vincent“ bitte unter "
    "Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen."
)
SCREEN_RECORDING_HINT = (
    "Ich kann deinen Bildschirm nicht sehen. Erlaube „XRZ Vincent“ bitte die Bildschirmaufnahme: "
    "Systemeinstellungen → Datenschutz & Sicherheit → Bildschirmaufnahme "
    "(bzw. „Bildschirm- & Systemaudioaufnahme“) – danach die App neu starten."
)


def accessibility(prompt: bool = False) -> bool | None:
    """Darf dieser Prozess Eingaben simulieren? ``prompt`` öffnet den System-Dialog, falls nicht."""
    if sys.platform != "darwin":
        return None
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception as exc:  # PyObjC fehlt oder Systemabfrage fehlgeschlagen
        log.debug("Bedienungshilfen-Status unbekannt: %s", exc)
        return None


def screen_recording(request: bool = False) -> bool | None:
    """Darf dieser Prozess den Bildschirm aufnehmen? ``request`` fragt einmalig beim System an."""
    if sys.platform != "darwin":
        return None
    try:
        from Quartz import (  # type: ignore[import-not-found]
            CGPreflightScreenCaptureAccess,
            CGRequestScreenCaptureAccess,
        )

        if CGPreflightScreenCaptureAccess():
            return True
        if request:
            return bool(CGRequestScreenCaptureAccess())
        return False
    except Exception as exc:
        log.debug("Bildschirmaufnahme-Status unbekannt: %s", exc)
        return None


def status(prompt: bool = False) -> dict[str, bool | None]:
    """Beide Berechtigungen auf einen Blick: ``{"accessibility", "screen_recording"}``."""
    return {"accessibility": accessibility(prompt), "screen_recording": screen_recording(prompt)}
