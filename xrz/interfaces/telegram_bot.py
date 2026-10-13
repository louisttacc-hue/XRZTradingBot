"""Telegram-Steuerung (optional): Befehle, freier Chat mit Vincent, Dokumente & Bilder.

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

HELP = """🤖 Vincent – Telegram-Befehle

📊 Markt
/status – Modus, TradingView-Konto, Positionen, Vorschläge
/markt – Überblick aller Märkte + Stimmung
/analyse BTC – komplette Analyse (optional Zeitrahmen: /analyse ETH 4h)
/backtest BTC – Walk-Forward-Backtest aller Strategien
/chart BTC – Symbol in TradingView öffnen + Screenshot + KI-Analyse
/screenshot – Bildschirmfoto vom Mac

💼 Trading (immer in der TradingView-App)
/beobachten – nur analysieren & Vorschläge machen
/paper – Paper Trading in TradingView
/live – Echtgeld anfragen → /bestaetigen CODE
/ja · /nein – offenen Vorschlag ausführen / ablehnen
/auto · /fragen – im Paper-Modus selbstständig handeln / vorher fragen
/pause · /weiter · /stop (aus) · /panik (alles schließen)
/positionen · /trades · /schliessen BTC

🧠 Gedächtnis
/lerne <Text> – Vincent etwas beibringen
/wissen <Suche> – im Gedächtnis suchen
/url <Link> – Webseite/PDF lernen
📎 Dokument senden (PDF/TXT/MD) → wird gelernt · 🖼 Foto → Chart-Analyse
/ki – welche KI aktiv ist · /neu – Chatverlauf leeren

⚙️ /watchlist · /add SOL · /remove XRP · /einstellungen · /set risk.risk_per_trade_pct 0.5 · /bericht

Oder einfach normal schreiben – z. B. „Wie sieht Bitcoin aus?“"""


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
    def __init__(self, cfg: Any, storage: Any, engine: Any, brain: Any, memory: Any, mac: Any = None, tradingview: Any = None):
        self.cfg = cfg
        self.storage = storage
        self.engine = engine
        self.brain = brain
        self.memory = memory
        self.mac = mac
        self.tv = tradingview
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
            "paper": self.cmd_paper, "beobachten": self.cmd_watch, "live": self.cmd_live, "bestaetigen": self.cmd_confirm,
            "ja": self.cmd_yes, "nein": self.cmd_no, "auto": self.cmd_auto, "fragen": self.cmd_ask,
            "pause": self.cmd_pause, "weiter": self.cmd_resume, "stop": self.cmd_stop, "panik": self.cmd_panic,
            "positionen": self.cmd_status, "trades": self.cmd_trades, "schliessen": self.cmd_close,
            "lerne": self.cmd_learn, "wissen": self.cmd_knowledge,
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
                BotCommand("analyse", "Analyse eines Symbols"), BotCommand("paper", "Paper Trading (TradingView)"),
                BotCommand("ja", "Vorschlag ausführen"), BotCommand("nein", "Vorschlag ablehnen"),
                BotCommand("live", "Echtgeld anfragen"), BotCommand("stop", "Trading aus"),
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

    async def on_event(self, type_: str, data: dict[str, Any]) -> None:
        """Empfängt Ereignisse vom EventBus und schickt relevante Meldungen an Telegram."""
        if type_ != "notify":
            return
        kind, text = data.get("kind", "info"), data.get("text", "")
        t = self.cfg.settings.telegram
        if kind == "trade" and not t.notify_trades:
            return
        if kind == "signal" and not t.notify_signals:
            return
        if kind in ("info",) or not text:
            return
        if kind == "proposal" and data.get("proposal_id"):
            text += "\n\n👉 /ja zum Ausführen · /nein zum Ablehnen"
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
        from ..market.data import to_tradingview_symbol

        wl = self.cfg.settings.trading.watchlist
        symbol = self._arg_symbol(context, wl[0] if wl else None)
        if self.tv is None or not symbol:
            await self._reply(update, "TradingView-Steuerung nicht verfügbar.")
            return
        tv_symbol = to_tradingview_symbol(symbol, self.cfg.settings.tradingview.chart_exchange_prefix)
        if not await self.tv.set_symbol(tv_symbol):
            st = await self.tv.status()
            await self._reply(update, f"Konnte {symbol} nicht in TradingView öffnen: {st.get('message', '')}")
            return
        await asyncio.sleep(2.5)
        shot = await self.tv.screenshot_b64()
        if shot:
            import base64

            data = base64.b64decode(shot)
            await update.effective_message.reply_photo(photo=data, caption=f"{symbol} in TradingView")
            await self._typing(update)
            analysis = await self.engine.analyzer.analyze(symbol)
            ai = await self.brain.analyze_image(
                data, f"Das ist ein TradingView-Screenshot von {symbol}. Berechnete Analyse:\n"
                      f"{analysis.to_text(compact=True)}\n\nGib eine präzise Chart-Einschätzung mit Szenarien.")
            await self._reply(update, ai)
        else:
            await self._reply(update, f"{symbol} ist in TradingView geöffnet (Screenshot nicht möglich).")

    async def cmd_screenshot(self, update, context) -> None:
        if self.mac is None:
            await self._reply(update, "Mac-Steuerung nicht verfügbar.")
            return
        shot = await self.mac.screenshot()
        if not shot:
            await self._reply(update, "Screenshot nicht möglich (Berechtigung „Bildschirmaufnahme“ für Vincent prüfen).")
            return
        await update.effective_message.reply_photo(photo=shot[0])

    async def cmd_paper(self, update, context) -> None:
        await self._reply(update, await self.engine.set_mode("paper"))

    async def cmd_watch(self, update, context) -> None:
        await self._reply(update, await self.engine.set_mode("watch"))

    async def cmd_live(self, update, context) -> None:
        res = await self.engine.request_live()
        text = res["text"]
        if res.get("needs_confirmation"):
            text = text.replace(f"Bestätigungscode (5 Minuten gültig): {res['code']}",
                                f"Zum Bestätigen innerhalb von 5 Minuten senden:  /bestaetigen {res['code']}")
        await self._reply(update, text)

    async def cmd_yes(self, update, context) -> None:
        p = self.engine.pending_proposal()
        if p is None:
            await self._reply(update, "Es gibt gerade keinen offenen Vorschlag.")
            return
        if p["account_kind"] == "live" and " ".join(context.args).lower() != "echtgeld":
            await self._reply(update, "Das ist ein ECHTGELD-Trade. Zum Ausführen bitte senden: /ja echtgeld")
            return
        await self._reply(update, await self.engine.approve(p["id"], source="telegram"))

    async def cmd_no(self, update, context) -> None:
        p = self.engine.pending_proposal()
        if p is None:
            await self._reply(update, "Es gibt gerade keinen offenen Vorschlag.")
            return
        await self._reply(update, await self.engine.reject(p["id"], source="telegram"))

    async def cmd_auto(self, update, context) -> None:
        await self._reply(update, await self.engine.set_autonomy("auto"))

    async def cmd_ask(self, update, context) -> None:
        await self._reply(update, await self.engine.set_autonomy("ask"))

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

    async def cmd_learn(self, update, context) -> None:
        text = " ".join(context.args)
        if not text:
            await self._reply(update, "Was soll ich lernen? Beispiel: /lerne Bei BTC am Wochenende keine Ausbrüche handeln.")
            return
        await self.memory.add(text, category="notiz", importance=0.85, source="telegram")
        await self._reply(update, f"🧠 Gemerkt. Gedächtnis: {self.memory.count()} Einträge.")

    async def cmd_knowledge(self, update, context) -> None:
        q = " ".join(context.args)
        if not q:
            await self._reply(update, f"🧠 Gedächtnis: {self.memory.count()} Einträge. Suche mit /wissen <Begriff>.")
            return
        hits = await self.memory.search(q, 5)
        await self._reply(update, "\n\n".join(f"#{h['id']} [{h['category']}]\n{h['text'][:700]}" for h in hits) or "Nichts gefunden.")

    async def cmd_url(self, update, context) -> None:
        if not context.args:
            await self._reply(update, "Bitte Link angeben: /url https://…")
            return
        await self._typing(update)
        n = await self.memory.ingest_url(context.args[0])
        await self._reply(update, f"🌐 Webseite gelernt: {n} Abschnitte gespeichert.")

    async def cmd_ai(self, update, context) -> None:
        h = await self.brain.router.health()
        await self._reply(
            update,
            f"🧠 KI-Kette: {h['chain']}\nOllama läuft: {'ja' if h['ollama_running'] else 'nein'} | "
            f"Modell bereit: {'ja' if h['ollama_model_ready'] else 'nein'}\n"
            f"Claude-Key: {'✅' if h['anthropic_key'] else '–'} | OpenAI-Key: {'✅' if h['openai_key'] else '–'}\n"
            f"Zuletzt genutzt: {h['last_used'] or '–'}\nGedächtnis: {self.memory.count()} Einträge"
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
            reply = await self.brain.chat(f"tg:{update.effective_chat.id}", update.effective_message.text, source="telegram")
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
        n = await self.memory.ingest_bytes(data, name, source="telegram")
        await self._reply(update, f"🧠 {name} gelernt: {n} Abschnitte. Gedächtnis gesamt: {self.memory.count()}.")

    async def on_photo(self, update, context) -> None:
        photo = update.effective_message.photo[-1]
        f = await photo.get_file()
        data = bytes(await f.download_as_bytearray())
        await self._typing(update)
        question = update.effective_message.caption or ""
        await self._reply(update, await self.brain.analyze_image(data, question))
