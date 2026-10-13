"""Bildschirmfotos des Haupt-Bildschirms (macOS, ``screencapture`` + ``sips``).

Die Bilder werden so verkleinert, dass sie in die Bildgrenzen der KI passen
(≤ 1568 px lange Kante, ≈ 1,15 Megapixel). ``scale`` gibt an, wie viele
Bildpunkte des Fotos auf einen Bildschirm-Punkt kommen – damit lassen sich
Klick-Koordinaten aus dem Foto zurück in Bildschirm-Punkte (für CGEvents) umrechnen.
Auf Retina-Displays liefert ``screencapture`` doppelt so viele Pixel wie Punkte.
"""
from __future__ import annotations

import asyncio
import logging
import math
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import permissions

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
MAX_LONG_EDGE = 1568
MAX_PIXELS = 1_150_000
NOT_MAC = "Bildschirmfotos sind nur auf macOS verfügbar."
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ScreenCaptureError(RuntimeError):
    """Bildschirmfoto nicht möglich – die Nachricht ist ein deutscher Hinweis für den Nutzer."""


@dataclass
class Screenshot:
    png: bytes
    width: int  # Pixel des (verkleinerten) Fotos
    height: int
    scale: float  # Foto-Pixel pro Bildschirm-Punkt


async def run_command(*args: str, timeout: float = 20) -> tuple[int, str]:
    """Startet ein Programm mit fester Argumentliste (keine Shell) und liefert (Code, Ausgabe)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "Zeitüberschreitung"
    code = proc.returncode or 0
    first, second = (err, out) if code else (out, err)
    return code, (first or second or b"").decode("utf-8", "ignore").strip()


# --------------------------------------------------------------------------- #
# PNG-Hilfen (ohne Bildbibliothek)
# --------------------------------------------------------------------------- #
def png_size(data: bytes) -> tuple[int, int]:
    """Breite und Höhe aus dem PNG-Kopf (IHDR)."""
    if len(data) < 24 or data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise ValueError("Keine gültige PNG-Datei")
    return struct.unpack(">II", data[16:24])


def _idat(data: bytes) -> bytes:
    pos, chunks = 8, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if kind == b"IDAT":
            chunks.append(data[pos + 8 : pos + 8 + length])
        elif kind == b"IEND":
            break
        pos += 12 + length
    return b"".join(chunks)


def looks_blank(data: bytes) -> bool:
    """Erkennt einfarbige Bilder – so sieht ein Foto ohne Bildschirmaufnahme-Berechtigung oft aus."""
    try:
        raw = zlib.decompressobj().decompress(_idat(data), 64_000_000)
    except (zlib.error, ValueError):
        return True
    if not raw:
        return True
    step = max(1, len(raw) // 200_000)
    return len(set(raw[::step])) <= 6


def fit_size(width: int, height: int, max_long_edge: int = MAX_LONG_EDGE,
             max_pixels: int = MAX_PIXELS, upscale: bool = False) -> tuple[int, int]:
    """Zielgröße, die lange Kante und Pixelbudget einhält (Seitenverhältnis bleibt)."""
    factor = min(max_long_edge / max(width, height), math.sqrt(max_pixels / (width * height)))
    if not upscale:
        factor = min(factor, 1.0)
    return max(1, int(width * factor)), max(1, int(height * factor))


def screen_size_points() -> tuple[float, float] | None:
    """Größe des Haupt-Bildschirms in Punkten (Quartz), ``None`` wenn unbekannt."""
    if not IS_MAC:
        return None
    try:
        import Quartz  # type: ignore[import-not-found]

        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return float(bounds.size.width), float(bounds.size.height)
    except Exception as exc:
        log.debug("Bildschirmgröße über Quartz nicht lesbar: %s", exc)
        return None


def _points_width(raw_width: int) -> float:
    size = screen_size_points()
    if size and size[0] > 0:
        return size[0]
    # Ohne Quartz: Retina (Faktor 2) bei hochauflösenden Aufnahmen annehmen
    return raw_width / (2.0 if raw_width >= 2400 else 1.0)


def _failure_message(detail: str) -> str:
    if permissions.screen_recording() is False:
        return permissions.SCREEN_RECORDING_HINT
    return f"Bildschirmfoto fehlgeschlagen ({detail or 'unbekannter Fehler'})."


async def capture(max_long_edge: int = MAX_LONG_EDGE,
                  region: tuple[float, float, float, float] | None = None,
                  workdir: Path | None = None, max_pixels: int = MAX_PIXELS) -> Screenshot:
    """Fotografiert den Haupt-Bildschirm (oder ``region`` = x, y, Breite, Höhe in Punkten).

    Ganze Bildschirme werden nur verkleinert; Ausschnitte (Zoom) werden bis zum
    Bildbudget vergrößert, damit Details lesbar werden. Wirft ``ScreenCaptureError``.
    """
    if not IS_MAC:
        raise ScreenCaptureError(NOT_MAC)
    if permissions.screen_recording() is False:
        raise ScreenCaptureError(permissions.SCREEN_RECORDING_HINT)
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vincent-shot-", dir=workdir) as tmp:
        raw_path, out_path = Path(tmp) / "raw.png", Path(tmp) / "shot.png"
        args = ["screencapture", "-x", "-t", "png"]
        if region is not None:
            x, y, w, h = region
            args += ["-R", f"{int(x)},{int(y)},{max(1, round(w))},{max(1, round(h))}"]
        else:
            args.append("-m")  # nur der Haupt-Bildschirm
        code, out = await run_command(*args, str(raw_path))
        if code != 0 or not raw_path.exists() or raw_path.stat().st_size == 0:
            raise ScreenCaptureError(_failure_message(out))
        data = raw_path.read_bytes()
        try:
            raw_w, raw_h = png_size(data)
        except ValueError as exc:
            raise ScreenCaptureError(_failure_message(str(exc))) from exc
        width, height = raw_w, raw_h
        target = fit_size(raw_w, raw_h, max_long_edge, max_pixels, upscale=region is not None)
        if target != (raw_w, raw_h):
            code, out = await run_command("sips", "-Z", str(max(target)), str(raw_path), "--out", str(out_path),
                                          timeout=30)
            if code != 0 or not out_path.exists():
                raise ScreenCaptureError(f"Bildschirmfoto konnte nicht verkleinert werden ({out or code}).")
            data = out_path.read_bytes()
            try:
                width, height = png_size(data)
            except ValueError as exc:
                raise ScreenCaptureError(f"Verkleinertes Bildschirmfoto ist ungültig ({exc}).") from exc
    if region is None and looks_blank(data):
        raise ScreenCaptureError(permissions.SCREEN_RECORDING_HINT)
    base_width = region[2] if region is not None else _points_width(raw_w)
    return Screenshot(png=data, width=width, height=height, scale=width / max(base_width, 1.0))
