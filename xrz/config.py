"""Einstellungen und Geheimnisse (API-Keys, Tokens).

* Normale Einstellungen liegen als YAML im Datenordner (``config.yaml``).
* Geheimnisse (Telegram-Token, API-Keys) liegen getrennt davon – bevorzugt im
  macOS-Schlüsselbund (über ``keyring``), sonst in ``secrets.json`` mit
  Dateirechten 600 (nur dein Benutzer kann sie lesen).
"""
from __future__ import annotations

import json
import logging
import os
import secrets as _secrets
import sys
import threading
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Datenordner
# --------------------------------------------------------------------------- #
def default_data_dir() -> Path:
    env = os.environ.get("XRZ_HOME")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "XRZ Vincent"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", home)) / "XRZ Vincent"
    return home / ".local" / "share" / "xrz-vincent"


# --------------------------------------------------------------------------- #
# Einstellungen
# --------------------------------------------------------------------------- #
class TradingSettings(BaseModel):
    watchlist: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
    )
    timeframe: str = "1h"
    higher_timeframe: str = "4h"
    history_candles: int = 1000
    scan_interval_minutes: int = 15
    manage_interval_seconds: int = 30
    deep_analysis_hours: int = 12
    entry_threshold: float = 0.35  # Ensemble-Score, ab dem ein Einstieg erwogen wird
    exit_threshold: float = -0.05  # Fällt der Score darunter, wird eine Long-Position geschlossen
    min_active_strategies: int = 2  # Mind. so viele Strategien müssen in die Richtung zeigen
    higher_tf_filter: bool = True  # Nicht gegen den übergeordneten Trend handeln
    max_extension_atr: float = 2.5  # Nicht hinterherspringen, wenn Kurs zu weit von EMA20 weg
    allow_short: bool = False  # Shorts nur im Paper-Modus möglich (Spot-Live = nur Long)
    use_ai_review: bool = True  # KI prüft jeden Trade (darf nur ablehnen/verkleinern)
    tradingview_signal_weight: float = 0.35
    tradingview_signal_minutes: int = 120
    # ask = jeder Trade braucht dein "Ja" (Stimme/Klick/Telegram); auto = Vincent handelt selbst
    # (Paper immer möglich, Echtgeld nur mit freigeschaltetem Echtgeld-Autopiloten)
    autonomy: Literal["ask", "auto"] = "ask"
    proposal_timeout_minutes: int = 10
    account_size_fallback: float = 10_000.0  # falls der Kontostand nicht aus TradingView lesbar ist

    @field_validator("watchlist")
    @classmethod
    def _norm_watchlist(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for s in v:
            s = s.strip().upper()
            if s and s not in out:
                out.append(s)
        return out


class RiskSettings(BaseModel):
    risk_per_trade_pct: float = 1.0  # % des Kontos, das pro Trade maximal verloren werden darf
    max_position_pct: float = 25.0  # max. Positionsgröße in % des Kontos
    max_open_positions: int = 3
    max_daily_loss_pct: float = 3.0  # Tagesverlust-Limit → keine neuen Trades mehr heute
    max_drawdown_pct: float = 15.0  # Notbremse: ab diesem Drawdown wird gestoppt
    stop_atr_mult: float = 2.0
    take_profit_r: float = 2.5  # Take-Profit als Vielfaches des Risikos (R)
    trailing_atr_mult: float = 2.5
    trailing_activate_r: float = 1.0  # Trailing-Stop ab +1R Gewinn aktivieren
    breakeven_at_r: float = 1.0  # Stop auf Einstand ziehen ab +1R
    cooldown_minutes_after_loss: int = 120
    max_live_order_usd: float = 100.0  # harte Obergrenze je Echtgeld-Order (Sicherheit!)
    fee_pct: float = 0.1
    slippage_pct: float = 0.05
    min_order_usd: float = 10.0
    # Klein anfangen: die ersten N Trades je Modus mit 25 % → 100 % der normalen Größe
    start_small_trades: int = 5


class ExchangeSettings(BaseModel):
    """Nur noch Kursdaten für die Analyse – gehandelt wird ausschließlich in TradingView."""

    name: str = "binance"  # jede ccxt-Börse: binance, kraken, bybit, okx, coinbase, bitget, ...
    data_source: Literal["exchange", "demo"] = "exchange"


class AISettings(BaseModel):
    # auto = Claude wenn Anthropic-Key da, sonst OpenAI wenn Key da, sonst lokale KI (Ollama)
    provider: Literal["auto", "ollama", "anthropic", "openai"] = "auto"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_vision_model: str = ""  # z. B. "qwen2.5vl:7b" oder "llava" für Chart-Bilder
    ollama_embed_model: str = "nomic-embed-text"
    ollama_think: bool = False  # "Nachdenken" lokaler Modelle (langsamer, teils besser)
    ollama_num_ctx: int = 8192
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"
    temperature: float = 0.3
    chat_history_messages: int = 16
    reflect_on_trades: bool = True  # nach jedem Trade eine Lektion ins Wissen schreiben
    personality: str = (
        "Du bist Vincent, ein extrem gründlicher, ehrlicher Trading-Analyst, Risiko-Manager und "
        "persönlicher Assistent. Du sprichst Deutsch, natürlich und locker wie ein guter Kollege, "
        "bist direkt und konkret, nennst Zahlen und Gründe und versprichst niemals sichere Gewinne."
    )


class TelegramSettings(BaseModel):
    enabled: bool = True
    allowed_chat_ids: list[int] = Field(default_factory=list)
    notify_trades: bool = True
    notify_signals: bool = False
    daily_report_time: str = "20:00"  # HH:MM lokale Zeit, leer = aus


class WebSettings(BaseModel):
    """Lokale Backend-API (die App verbindet sich hierüber) + optionaler TradingView-Webhook."""

    host: str = "127.0.0.1"
    port: int = 8765
    webhook_enabled: bool = False
    webhook_port: int = 8766


class TradingViewSettings(BaseModel):
    app_path: str = "/Applications/TradingView.app"
    cdp_port: int = 9333  # lokaler Steuer-Port der TradingView-App (nur 127.0.0.1)
    # Darf Vincent TradingView neu starten, um die Steuerung zu aktivieren? (sonst fragt er)
    allow_restart_without_asking: bool = False
    paper_account_name: str = ""  # optional: genauer Name deines Paper-Kontos
    chart_exchange_prefix: str = "BINANCE"  # BTC/USDT → BINANCE:BTCUSDT
    use_brackets: bool = True  # Stop-Loss & Take-Profit direkt im Order-Ticket setzen
    api_fallback: bool = False  # Notfall: Order über TradingViews interne Schnittstelle statt Klicks
    # Alerts per Webhook: signal = starkes Signal für die Entscheidung, execute = Vorschlag sofort
    alert_mode: Literal["signal", "execute"] = "signal"


class VoiceSettings(BaseModel):
    enabled: bool = True
    wake_word_enabled: bool = True  # "Hey Vincent"
    assistant_name: str = "Vincent"
    follow_up_seconds: float = 6.0  # so lange darfst du ohne "Hey Vincent" weiterreden
    stt_backend: Literal["auto", "mlx", "faster-whisper"] = "auto"
    stt_model: str = "large-v3-turbo"  # für Befehle (genau)
    stt_fast_model: str = "small"  # für die Wake-Word-Erkennung (schnell)
    tts_provider: Literal["auto", "elevenlabs", "openai", "piper", "macos"] = "auto"
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_flash_v2_5"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "cedar"
    piper_voice: str = "de_DE-thorsten-high"
    macos_voice: str = ""  # leer = beste installierte deutsche Stimme
    speak_replies: bool = True  # Antworten vorlesen (auch bei getippten Fragen)
    input_device: str = ""
    output_device: str = ""


class MemorySettings(BaseModel):
    max_memories: int = 4000  # Obergrenze: darüber wird verdichtet und Unwichtiges vergessen
    profile_max_chars: int = 2000  # Kernprofil, das Vincent immer im Kopf hat
    consolidate_hours: int = 24  # "Schlafphase": Erinnerungen ordnen und verdichten
    extract_facts: bool = True  # nach jedem Gespräch Fakten/Vorlieben herausziehen


class AppearanceSettings(BaseModel):
    skin: str = "mensch"  # mensch | nebula | hologramm | neon | glas | roboter | eigenes
    accent: str = "#7c5cff"
    size: int = 132
    edge: Literal["right", "left"] = "right"
    custom_image: str = ""  # Pfad zu eigenem Avatar-Bild (PNG/GIF/JPG)
    show_subtitles: bool = True


class MacSettings(BaseModel):
    automation_enabled: bool = True
    notifications: bool = True
    # Vincent darf den Mac per Maus/Tastatur bedienen (Claude-Computersteuerung, braucht API-Key)
    computer_use_enabled: bool = True
    computer_use_max_steps: int = 30


class Settings(BaseModel):
    trading: TradingSettings = Field(default_factory=TradingSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    ai: AISettings = Field(default_factory=AISettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    tradingview: TradingViewSettings = Field(default_factory=TradingViewSettings)
    mac: MacSettings = Field(default_factory=MacSettings)
    # Echtgeld muss hier bewusst freigeschaltet werden, bevor der Live-Modus überhaupt geht.
    live_trading_unlocked: bool = False
    # Echtgeld-Autopilot: Vincent darf im LIVE-Modus ohne Einzel-Freigabe handeln (nur am Mac freischaltbar)
    live_autopilot_unlocked: bool = False


# Felder, die per Telegram (/set) geändert werden dürfen. Bewusst begrenzt.
TELEGRAM_EDITABLE = {
    "trading.timeframe",
    "trading.higher_timeframe",
    "trading.scan_interval_minutes",
    "trading.entry_threshold",
    "trading.exit_threshold",
    "trading.min_active_strategies",
    "trading.higher_tf_filter",
    "trading.allow_short",
    "trading.use_ai_review",
    "trading.autonomy",
    "voice.wake_word_enabled",
    "voice.speak_replies",
    "voice.tts_provider",
    "appearance.skin",
    "risk.risk_per_trade_pct",
    "risk.max_position_pct",
    "risk.max_open_positions",
    "risk.max_daily_loss_pct",
    "risk.max_drawdown_pct",
    "risk.stop_atr_mult",
    "risk.take_profit_r",
    "risk.trailing_atr_mult",
    "risk.cooldown_minutes_after_loss",
    "ai.provider",
    "ai.ollama_model",
    "ai.anthropic_model",
    "ai.anthropic_effort",
    "ai.openai_model",
    "telegram.notify_trades",
    "telegram.notify_signals",
    "telegram.daily_report_time",
}

# Grenzen, die selbst über Einstellungen nicht überschritten werden können.
HARD_LIMITS = {
    "risk.risk_per_trade_pct": (0.05, 5.0),
    "risk.max_position_pct": (1.0, 100.0),
    "risk.max_open_positions": (1, 20),
    "risk.max_daily_loss_pct": (0.5, 20.0),
    "risk.max_drawdown_pct": (2.0, 50.0),
    "risk.stop_atr_mult": (0.5, 6.0),
    "risk.take_profit_r": (0.5, 10.0),
    "risk.trailing_atr_mult": (0.5, 8.0),
    "trading.entry_threshold": (0.1, 0.95),
    "trading.exit_threshold": (-0.9, 0.5),
}


def get_path(settings: Settings, dotted: str) -> Any:
    obj: Any = settings
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def set_path(settings: Settings, dotted: str, raw: Any) -> Any:
    """Setzt einen Wert per Punkt-Pfad (z. B. ``risk.risk_per_trade_pct``) mit Typprüfung."""
    parts = dotted.split(".")
    parent: Any = settings
    for part in parts[:-1]:
        parent = getattr(parent, part)
    field = parts[-1]
    if field not in type(parent).model_fields:
        raise KeyError(f"Unbekannte Einstellung: {dotted}")
    current = getattr(parent, field)
    value: Any = raw
    if isinstance(raw, str):
        txt = raw.strip()
        if isinstance(current, bool):
            if txt.lower() in {"1", "true", "ja", "an", "on", "yes"}:
                value = True
            elif txt.lower() in {"0", "false", "nein", "aus", "off", "no"}:
                value = False
            else:
                raise ValueError("Bitte ja/nein angeben")
        elif isinstance(current, int) and not isinstance(current, bool):
            value = int(float(txt.replace(",", ".")))
        elif isinstance(current, float):
            value = float(txt.replace(",", "."))
        elif isinstance(current, list):
            value = [x.strip() for x in txt.split(",") if x.strip()]
    if dotted in HARD_LIMITS and isinstance(value, (int, float)):
        lo, hi = HARD_LIMITS[dotted]
        if not lo <= value <= hi:
            raise ValueError(f"{dotted} muss zwischen {lo} und {hi} liegen")
    # Validierung über das Pydantic-Modell
    data = parent.model_dump()
    data[field] = value
    validated = type(parent).model_validate(data)
    setattr(parent, field, getattr(validated, field))
    return getattr(parent, field)


# --------------------------------------------------------------------------- #
# Geheimnisse
# --------------------------------------------------------------------------- #
SECRET_KEYS = {
    "anthropic_api_key": "Anthropic API-Key (Claude) – macht Vincent deutlich schlauer und kann den Mac bedienen",
    "openai_api_key": "OpenAI API-Key (oder kompatibler Anbieter)",
    "elevenlabs_api_key": "ElevenLabs API-Key – sehr realistische Stimme",
    "telegram_token": "Telegram-Bot-Token (von @BotFather, optional)",
    "tradingview_passphrase": "Passwort für TradingView-Webhooks (optional)",
}


class SecretStore:
    """Speichert Geheimnisse im Schlüsselbund oder in einer geschützten Datei."""

    SERVICE = "XRZ Vincent"

    def __init__(self, data_dir: Path, use_keyring: bool | None = None):
        self.path = data_dir / "secrets.json"
        self._lock = threading.Lock()
        self._keyring = None
        if use_keyring is None:
            use_keyring = sys.platform == "darwin" and os.environ.get("XRZ_NO_KEYRING") != "1"
        if use_keyring:
            try:
                import keyring  # type: ignore

                keyring.get_password(self.SERVICE, "__probe__")
                self._keyring = keyring
            except Exception as exc:  # pragma: no cover - plattformabhängig
                log.info("Schlüsselbund nicht verfügbar (%s) – nutze secrets.json", exc)
        self._cache: dict[str, str] = self._load_file()

    @property
    def backend(self) -> str:
        return "macOS-Schlüsselbund" if self._keyring else f"Datei ({self.path})"

    def _load_file(self) -> dict[str, str]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except Exception:
                log.warning("secrets.json ist beschädigt – ignoriere sie")
        return {}

    def _save_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover
            pass
        tmp.replace(self.path)

    def get(self, key: str) -> str:
        env = os.environ.get(f"XRZ_{key.upper()}")
        if env:
            return env
        with self._lock:
            if self._keyring:
                try:
                    val = self._keyring.get_password(self.SERVICE, key)
                    if val:
                        return val
                except Exception:  # pragma: no cover
                    pass
            return self._cache.get(key, "")

    def set(self, key: str, value: str) -> None:
        if key not in SECRET_KEYS and not key.startswith("internal_"):
            raise KeyError(f"Unbekanntes Geheimnis: {key}")
        value = (value or "").strip()
        with self._lock:
            if self._keyring:
                try:
                    if value:
                        self._keyring.set_password(self.SERVICE, key, value)
                    else:
                        try:
                            self._keyring.delete_password(self.SERVICE, key)
                        except Exception:
                            pass
                    self._cache.pop(key, None)
                    self._save_file()
                    return
                except Exception as exc:  # pragma: no cover
                    log.warning("Schlüsselbund-Fehler (%s) – speichere in Datei", exc)
            if value:
                self._cache[key] = value
            else:
                self._cache.pop(key, None)
            self._save_file()

    def has(self, key: str) -> bool:
        return bool(self.get(key))

    def masked(self) -> dict[str, dict[str, Any]]:
        out = {}
        for key, label in SECRET_KEYS.items():
            val = self.get(key)
            out[key] = {
                "label": label,
                "set": bool(val),
                "preview": (val[:3] + "…" + val[-3:]) if len(val) > 10 else ("•••" if val else ""),
            }
        return out


# --------------------------------------------------------------------------- #
# Laden / Speichern
# --------------------------------------------------------------------------- #
class ConfigManager:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "config.yaml"
        self._lock = threading.Lock()
        self.settings = self.load()
        self.secrets = SecretStore(self.data_dir)
        # Interner Zufallstoken für das Dashboard (Schutz gegen fremde Webseiten)
        if not self.secrets.get("internal_web_token"):
            self.secrets.set("internal_web_token", _secrets.token_urlsafe(24))

    def load(self) -> Settings:
        if self.path.exists():
            try:
                data = yaml.safe_load(self.path.read_text("utf-8")) or {}
                return Settings.model_validate(data)
            except Exception as exc:
                backup = self.path.with_suffix(".broken.yaml")
                self.path.replace(backup)
                log.error("config.yaml ungültig (%s) – Standardwerte, Backup: %s", exc, backup)
        settings = Settings()
        self._write(settings)
        return settings

    def _write(self, settings: Settings) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            "# XRZ Trading Bot – Einstellungen (am einfachsten im Dashboard ändern)\n"
            + yaml.safe_dump(settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            "utf-8",
        )
        tmp.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._write(self.settings)

    def update(self, data: dict[str, Any]) -> Settings:
        """Übernimmt ein (Teil-)Dict aus dem Dashboard, validiert und speichert."""
        with self._lock:
            merged = _deep_merge(self.settings.model_dump(mode="json"), data)
            new = Settings.model_validate(merged)
            for dotted, (lo, hi) in HARD_LIMITS.items():
                val = get_path(new, dotted)
                if not lo <= val <= hi:
                    raise ValueError(f"{dotted} muss zwischen {lo} und {hi} liegen")
            self.settings = new
            self._write(new)
            return new


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
