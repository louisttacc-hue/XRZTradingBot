"""Telegram-Steuerung: Befehle, freier Chat mit der KI, Dokumente & Bilder.

Sicherheit: Nur gekoppelte Chats (allowed_chat_ids) dürfen den Bot steuern.
Kopplung: /start <CODE> – den Code zeigt das Dashboard bzw. die Konsole an."""
from __future__ import annotations

import asyncio
import functools
import logging
import secrets
from typing import Any, Awaitable, Callable

from ..config import TELEGRAM_EDITABLE, set_path
from ..market.data import normalize_symbol

log = logging.getLogger(__name__)

HELP = """🤖 XRZ Trading Bot – Befehle

📊 Markt
/status – Konto, Positionen, Signale
/markt – Überblick aller Märkte + Stimmung
/analyse BTC – komplette Analyse (optional Zeitrahmen: /analyse ETH 4h)
/backtest BTC – Walk-Forward-Backtest aller Strategien
/chart BTC – TradingView am Mac öffnen + Screenshot + KI-Analyse
/screenshot – Bildschirmfoto vom Mac

💼 Trading
/paper – Paper-Trading starten (Spielgeld)
/live – Live-Trading (Echtgeld) anfragen → /bestaetigen CODE
/pause – keine neuen Einstiege · /weiter – fortsetzen
/stop – Trading aus (Positionen werden weiter überwacht)
/panik – ALLE Positionen sofort schließen
/positionen · /trades · /schliessen BTC
/paperreset 10000 – Paper-Konto zurücksetzen

🧠 Wissen & KI
/lerne <Text> – der KI etwas beibringen
/wissen <Suche> – Wissensdatenbank durchsuchen
/url <Link> – Webseite/PDF als Wissen einlesen
📎 Dokument senden (PDF/TXT/MD) → wird gelernt
🖼 Foto senden → Chart-Analyse
/ki – welche KI gerade aktiv ist · /neu – Chatverlauf leeren

⚙️ Einstellungen
/watchlist · /add SOL · /remove XRP
/einstellungen · /set risk.risk_per_trade_pct 0.5
/bericht – Tagesbericht

Oder einfach normal schreiben – z. B. "Wie sieht Bitcoin aus?" oder "Starte Paper-Trading"."""


def _chunks(text: str, size: int = 3900) -> list[str]:
    out = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out


class TelegramBot:
    def __init__(self, cfg: Any, storage: Any, engine: Any, brain: Any, kb: Any, mac: Any = None):
        self.cfg = cfg
        self.storage = storage
        self.engine = engine
        self.brain = brain
        self.kb = kb
        self.mac = mac
        self.app: Any = None
        self.running = False
        self.error = ""
        self.username = ""

    # ------------------------------------------------------------- Kopplung
    @property
    def allowed(self) -> list[int]:
        return list(self.cfg.settings.telegram.allowed_chat_ids)

    def pairing_code(self) -> str:
        code = self.storage.kv_get("telegram_pair_code")
        if not code:
            code = f"{secrets.randbelow(900000) + 100000}"
            self.storage.kv_set("telegram_pair_code", code)
        return code

    def _authorized(self, update: Any) -> bool:
        chat = update.effective_chat
        return chat is not None and chat.id in self.allowed

    # ----------------------------------------------------------- Lebenszyklus
    async def start(self) -> None:
        token = self.cfg.secrets.get("telegram_token")
        if not self.cfg.settings.telegram.enabled:
            self.error = "In den Einstellungen deaktiviert"
            return
        if not token:
            self.error = "Kein Telegram-Token eingetragen"
            return
        from telegram import BotCommand
        from telegram.ext import Application, CommandHandler, MessageHandler, filters

        app = Application.builder().token(token).concurrent_updates(True).build()
        cmds: dict[str, Callable[..., Awaitable[None]]] = {
            "start": self.cmd_start, "hilfe": self.cmd_help, "help": self.cmd_help,
            "status": self.cmd_status, "markt": self.cmd_markt, "analyse": self.cmd_analyse,
            "backtest": self.cmd_backtest, "chart": self.cmd_chart, "screenshot": self.cmd_screenshot,
            "paper": self.cmd_paper, "live": self.cmd_live, "bestaetigen": self.cmd_confirm,
            "pause": self.cmd_pause, "weiter": self.cmd_resume, "stop": self.cmd_stop, "panik": self.cmd_panic,
            "positionen": self.cmd_status, "trades": self.cmd_trades, "schliessen": self.cmd_close,
            "paperreset": self.cmd_paperreset, "lerne": self.cmd_learn, "wissen": self.cmd_knowledge,
            "url": self.cmd_url, "ki": self.cmd_ai, "neu": self.cmd_reset_chat, "watchlist": self.cmd_watchlist,
            "add": self.cmd_add, "remove": self.cmd_remove, "einstellungen": self.cmd_settings,
            "set": self.cmd_set, "bericht": self.cmd_report,
        }
        for name, fn in cmds.items():
            wrapped = fn if name == "start" else self._guard(fn)
            app.add_handler(CommandHandler(name, wrapped))
        app.add_handler(MessageHandler(filters.Document.ALL, self._guard(self.on_document)))
        app.add_handler(MessageHandler(filters.PHOTO, self._guard(self.on_photo)))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._guard(self.on_text)))
        app.add_error_handler(self._on_error)
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            me = await app.bot.get_me()
            self.username = me.username or ""
            await app.bot.set_my_commands([
                BotCommand("status", "Konto & Positionen"), BotCommand("markt", "Marktüberblick"),
                BotCommand("analyse", "Analyse eines Symbols"), BotCommand("paper", "Paper-Trading starten"),
                BotCommand("live", "Live-Trading anfragen"), BotCommand("stop", "Trading aus"),
                BotCommand("panik", "Alles schließen"), BotCommand("chart", "TradingView + KI-Analyse"),
                BotCommand("lerne", "Wissen beibringen"), BotCommand("hilfe", "Alle Befehle"),
            ])
        except Exception as exc:
            self.error = f"Telegram-Start fehlgeschlagen: {exc}"
            log.error(self.error)
            try:
                await app.shutdown()
            except Exception:
                pass
            return
        self.app = app
        self.running = True
        self.error = ""
        log.info("Telegram-Bot @%s läuft", self.username)
        if not self.allowed:
            log.warning("Telegram noch nicht gekoppelt – sende /start %s an @%s", self.pairing_code(), self.username)

    async def stop(self) -> None:
        if self.app is None:
            return
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception as exc:  # pragma: no cover
            log.info("Telegram-Stopp: %s", exc)
        self.app = None
        self.running = False

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ---------------------------------------------------------------- Senden
    async def send(self, text: str, chat_ids: list[int] | None = None) -> None:
        if self.app is None:
            return
        for cid in chat_ids or self.allowed:
            for part in _chunks(text):
                try:
                    await self.app.bot.send_message(cid, part, disable_web_page_preview=True)
                except Exception as exc:
                    log.warning("Telegram-Senden an %s fehlgeschlagen: %s", cid, exc)

    async def on_notify(self, kind: str, text: str, data: dict[str, Any]) -> None:
        t = self.cfg.settings.telegram
        if kind == "trade" and not t.notify_trades:
            return
        if kind == "signal" and not t.notify_signals:
            return
        if kind == "info":
            return
        await self.send(text)

    # -------------------------------------------------------------- Hilfsmittel
    def _guard(self, fn):
        @functools.wraps(fn)
        async def wrapper(update, context):
            if not self._authorized(update):
                if update.effective_message:
                    await update.effective_message.reply_text(
                        "🔒 Dieser Chat ist nicht gekoppelt. Sende /start <CODE> – den Code findest du im Dashboard am Mac."
                    )
                return
            try:
                await fn(update, context)
            except Exception as exc:
                log.exception("Telegram-Befehl fehlgeschlagen")
                await update.effective_message.reply_text(f"❌ Fehler: {exc}")
        return wrapper

    async def _on_error(self, update, context) -> None:
        log.warning("Telegram-Fehler: %s", context.error)

    @staticmethod
    async def _reply(update, text: str) -> None:
        for part in _chunks(text or "(leer)"):
            await update.effective_message.reply_text(part, disable_web_page_preview=True)

    async def _typing(self, update) -> None:
        try:
            await update.effective_chat.send_action("typing")
        except Exception:
            pass

    def _arg_symbol(self, context, default: str | None = None) -> str | None:
        if context.args:
            return normalize_symbol(context.args[0], self.cfg.settings.trading.watchlist)
        return default

    # --------------------------------------------------------------- Befehle
    async def cmd_start(self, update, context) -> None:
        chat_id = update.effective_chat.id
        if chat_id in self.allowed:
            await self._reply(update, "✅ Gekoppelt.\n\n" + HELP)
            return
        code = context.args[0] if context.args else ""
        if code and not secrets.compare_digest(code, self.pairing_code()):
            fails = int(self.storage.kv_get("telegram_pair_fails", 0) or 0) + 1
            self.storage.kv_set("telegram_pair_fails", fails)
            if fails >= 5:  # Schutz gegen Durchprobieren: neuen Code erzeugen
                self.storage.kv_set("telegram_pair_code", None)
                self.storage.kv_set("telegram_pair_fails", 0)
                log.warning("Zu viele falsche Kopplungsversuche (Chat %s) – neuer Code erzeugt", chat_id)
            await self._reply(update, "❌ Falscher Code.")
            return
        if code:
            self.cfg.settings.telegram.allowed_chat_ids.append(chat_id)
            self.cfg.save()
            self.storage.kv_set("telegram_pair_code", None)
            await self._reply(update, "🎉 Kopplung erfolgreich! Ab jetzt höre ich nur auf diesen Chat.\n\n" + HELP)
            return
        await self._reply(update, "👋 Hallo! Zum Koppeln sende: /start <CODE>\nDen 6-stelligen Code zeigt das Dashboard am Mac.")

    async def cmd_help(self, update, context) -> None:
        await self._reply(update, HELP)

    async def cmd_status(self, update, context) -> None:
        await self._reply(update, await self.engine.status_text())

    async def cmd_markt(self, update, context) -> None:
        await self._typing(update)
        await self._reply(update, "🔎 Analysiere alle Märkte …")
        res = await self.engine.scan_once()
        text = "\n\n".join(a.to_text(compact=True) for a in res) or "Keine Daten."
        sent = await self.brain.sentiment.summary()
        await self._reply(update, text + (f"\n\n🌡 {sent}" if sent else ""))

    async def cmd_analyse(self, update, context) -> None:
        symbol = self._arg_symbol(context)
        if not symbol:
            await self._reply(update, "Bitte Symbol angeben, z. B. /analyse BTC oder /analyse ETH/USDT 4h")
            return
        tf = context.args[1] if len(context.args) > 1 else None
        await self._typing(update)
        a = await self.engine.analyzer.analyze(symbol, tf)
        await self._reply(update, a.to_text())

    async def cmd_backtest(self, update, context) -> None:
        symbol = self._arg_symbol(context)
        if not symbol:
            await self._reply(update, "Bitte Symbol angeben, z. B. /backtest BTC")
            return
        await self._reply(update, f"🧪 Walk-Forward-Backtest für {symbol} läuft …")
        tool = next(t for t in self.brain.tools if t.name == "deep_analysis")
        await self._reply(update, await tool.handler({"symbol": symbol}))

    async def cmd_chart(self, update, context) -> None:
        symbol = self._arg_symbol(context, self.cfg.settings.trading.watchlist[0] if self.cfg.settings.trading.watchlist else None)
        if self.mac is None or not symbol:
            await self._reply(update, "Mac-Steuerung nicht verfügbar.")
            return
        tf = context.args[1] if len(context.args) > 1 else None
        await self._reply(update, f"🖥 Öffne TradingView für {symbol} …")
        msg, path = await self.mac.chart_screenshot(symbol, tf)
        if path is None:
            await self._reply(update, f"{msg}\n⚠️ Screenshot nicht möglich (macOS: Bildschirmaufnahme für Terminal erlauben).")
            return
        data = path.read_bytes()
        await update.effective_message.reply_photo(photo=data, caption=f"{symbol} – {msg}"[:1000])
        await self._typing(update)
        analysis = await self.engine.analyzer.analyze(symbol, tf)
        ai = await self.brain.analyze_image(
            data, f"Das ist ein TradingView-Screenshot von {symbol}. Hier zusätzlich die berechnete Analyse:\n"
                  f"{analysis.to_text(compact=True)}\n\nGib eine präzise Chart-Einschätzung mit Szenarien."
        )
        await self._reply(update, ai)

    async def cmd_screenshot(self, update, context) -> None:
        if self.mac is None:
            await self._reply(update, "Mac-Steuerung nicht verfügbar.")
            return
        path = await self.mac.screenshot()
        if path is None:
            await self._reply(update, "Screenshot nicht möglich (Berechtigung 'Bildschirmaufnahme' prüfen).")
            return
        await update.effective_message.reply_photo(photo=path.read_bytes())

    async def cmd_paper(self, update, context) -> None:
        await self._reply(update, await self.engine.set_mode("paper"))

    async def cmd_live(self, update, context) -> None:
        await self._reply(update, await self.engine.request_live())

    async def cmd_confirm(self, update, context) -> None:
        if not context.args:
            await self._reply(update, "Bitte Code angeben: /bestaetigen 123456")
            return
        await self._reply(update, await self.engine.confirm_live(context.args[0]))

    async def cmd_pause(self, update, context) -> None:
        await self._reply(update, await self.engine.pause())

    async def cmd_resume(self, update, context) -> None:
        await self._reply(update, await self.engine.resume())

    async def cmd_stop(self, update, context) -> None:
        await self._reply(update, await self.engine.set_mode("off"))

    async def cmd_panic(self, update, context) -> None:
        await self._reply(update, await self.engine.panic())

    async def cmd_trades(self, update, context) -> None:
        tool = next(t for t in self.brain.tools if t.name == "recent_trades")
        await self._reply(update, await tool.handler({"n": 15}))

    async def cmd_close(self, update, context) -> None:
        symbol = self._arg_symbol(context)
        if not symbol:
            await self._reply(update, "Bitte Symbol angeben: /schliessen BTC")
            return
        await self._reply(update, await self.engine.close_symbol(symbol, "Manuell per Telegram"))

    async def cmd_paperreset(self, update, context) -> None:
        if self.storage.open_trades("paper"):
            await self._reply(update, "Bitte zuerst alle Paper-Positionen schließen (/schliessen …).")
            return
        amount = float(context.args[0].replace(",", ".")) if context.args else None
        bal = self.engine.paper.reset(amount)
        await self._reply(update, f"📄 Paper-Konto zurückgesetzt auf {bal:,.2f}.")

    async def cmd_learn(self, update, context) -> None:
        text = " ".join(context.args)
        if not text:
            await self._reply(update, "Was soll ich lernen? Beispiel: /lerne Bei BTC am Wochenende keine Ausbrüche handeln.")
            return
        n = await self.kb.add(text, source="telegram", topic="notiz", importance=1.5)
        await self._reply(update, f"🧠 Gelernt ({n} Abschnitt). Wissen gesamt: {self.kb.count()} Einträge.")

    async def cmd_knowledge(self, update, context) -> None:
        q = " ".join(context.args)
        if not q:
            await self._reply(update, f"🧠 Wissensdatenbank: {self.kb.count()} Einträge. Suche mit /wissen <Begriff>.")
            return
        hits = await self.kb.search(q, 5)
        await self._reply(update, "\n\n".join(f"#{h['id']} [{h['topic'] or h['source']}]\n{h['text'][:700]}" for h in hits) or "Nichts gefunden.")

    async def cmd_url(self, update, context) -> None:
        if not context.args:
            await self._reply(update, "Bitte Link angeben: /url https://…")
            return
        await self._typing(update)
        n = await self.kb.ingest_url(context.args[0])
        await self._reply(update, f"🌐 Webseite gelernt: {n} Abschnitte gespeichert.")

    async def cmd_ai(self, update, context) -> None:
        h = await self.brain.router.health()
        await self._reply(
            update,
            f"🧠 KI-Kette: {h['chain']}\nOllama läuft: {'ja' if h['ollama_running'] else 'nein'} | "
            f"Modell bereit: {'ja' if h['ollama_model_ready'] else 'nein'}\n"
            f"Claude-Key: {'✅' if h['anthropic_key'] else '–'} | OpenAI-Key: {'✅' if h['openai_key'] else '–'}\n"
            f"Zuletzt genutzt: {h['last_used'] or '–'}\nWissen: {self.kb.count()} Einträge"
            + (f"\nLetzter Fehler: {h['last_error']}" if h["last_error"] else ""),
        )

    async def cmd_reset_chat(self, update, context) -> None:
        self.storage.clear_chat(f"tg:{update.effective_chat.id}")
        await self._reply(update, "🧹 Chatverlauf geleert (Wissen bleibt erhalten).")

    async def cmd_watchlist(self, update, context) -> None:
        await self._reply(update, "👀 Watchlist: " + ", ".join(self.cfg.settings.trading.watchlist))

    async def cmd_add(self, update, context) -> None:
        tool = next(t for t in self.brain.tools if t.name == "watchlist_add")
        await self._reply(update, await tool.handler({"symbol": context.args[0] if context.args else ""}))

    async def cmd_remove(self, update, context) -> None:
        tool = next(t for t in self.brain.tools if t.name == "watchlist_remove")
        await self._reply(update, await tool.handler({"symbol": context.args[0] if context.args else ""}))

    async def cmd_settings(self, update, context) -> None:
        tool = next(t for t in self.brain.tools if t.name == "get_settings")
        await self._reply(update, "⚙️ Änderbar mit /set <key> <wert>:\n" + await tool.handler({}))

    async def cmd_set(self, update, context) -> None:
        if len(context.args) < 2:
            await self._reply(update, "Beispiel: /set risk.risk_per_trade_pct 0.5\nAlle Schlüssel: /einstellungen")
            return
        key, value = context.args[0], " ".join(context.args[1:])
        if key not in TELEGRAM_EDITABLE:
            await self._reply(update, "Diese Einstellung ist per Telegram nicht änderbar (bitte im Dashboard).")
            return
        new = set_path(self.cfg.settings, key, value)
        self.cfg.save()
        await self._reply(update, f"✅ {key} = {new}")

    async def cmd_report(self, update, context) -> None:
        await self._typing(update)
        await self._reply(update, await self.engine.daily_report())

    # ---------------------------------------------------------- Nachrichten
    async def on_text(self, update, context) -> None:
        await self._typing(update)
        stop_typing = asyncio.Event()

        async def keep_typing():
            while not stop_typing.is_set():
                await self._typing(update)
                try:
                    await asyncio.wait_for(stop_typing.wait(), 4.5)
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(keep_typing())
        try:
            reply = await self.brain.chat(f"tg:{update.effective_chat.id}", update.effective_message.text)
        finally:
            stop_typing.set()
            await task
        await self._reply(update, reply)

    async def on_document(self, update, context) -> None:
        doc = update.effective_message.document
        if doc.file_size and doc.file_size > 20 * 1024 * 1024:
            await self._reply(update, "Datei zu groß (max. 20 MB).")
            return
        f = await doc.get_file()
        data = bytes(await f.download_as_bytearray())
        name = doc.file_name or "dokument.txt"
        await self._reply(update, f"📥 Lese {name} …")
        n = await self.kb.ingest_bytes(data, name, source="telegram")
        await self._reply(update, f"🧠 {name} gelernt: {n} Abschnitte. Wissen gesamt: {self.kb.count()}.")

    async def on_photo(self, update, context) -> None:
        photo = update.effective_message.photo[-1]
        f = await photo.get_file()
        data = bytes(await f.download_as_bytearray())
        await self._typing(update)
        question = update.effective_message.caption or ""
        await self._reply(update, await self.brain.analyze_image(data, question))
