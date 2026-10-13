"""XRZ-"Gehirn": Chat (Telegram/Dashboard) mit Werkzeugen, Trade-Prüfung,
Lernen aus Trades und Bildanalyse."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..config import TELEGRAM_EDITABLE, ConfigManager, get_path, set_path
from ..market.data import normalize_symbol
from ..market.sentiment import Sentiment
from ..storage import Storage, Trade
from .knowledge import KnowledgeBase
from .llm import LLMError, LLMRouter, ToolSpec, extract_json

log = logging.getLogger(__name__)

RULES = """
Arbeitsweise:
- Für aktuelle Kurse, Positionen, Kontostand und Analysen IMMER die Werkzeuge benutzen. Erfinde niemals Zahlen.
- Sei ehrlich: Es gibt keine sicheren Gewinne. Weise bei Bedarf auf Risiken hin, aber ohne Dauer-Disclaimer.
- Wenn der Nutzer dir etwas beibringen will ("merk dir", "lerne", "wichtig:"), speichere es mit dem Werkzeug learn.
- Paper-Trading darfst du auf Wunsch starten/stoppen. Live-Trading kannst du nur ANFRAGEN (request_live_trading);
  der Nutzer muss es selbst mit /bestaetigen CODE bestätigen. Bestätige niemals selbst.
- Einstellungen nur ändern, wenn der Nutzer es ausdrücklich möchte.
- Antworte auf Deutsch, kompakt und gut lesbar für Telegram: kurze Absätze, Emojis sparsam, keine Tabellen,
  keine Markdown-Überschriften.
- Begründe Markteinschätzungen mit konkreten Indikatoren, Marktphase und Niveaus.
"""


class Brain:
    def __init__(
        self,
        cfg: ConfigManager,
        storage: Storage,
        router: LLMRouter,
        kb: KnowledgeBase,
        engine: Any,
        mac: Any = None,
        sentiment: Sentiment | None = None,
    ):
        self.cfg = cfg
        self.storage = storage
        self.router = router
        self.kb = kb
        self.engine = engine
        self.mac = mac
        self.sentiment = sentiment or Sentiment()
        self.tools = self._build_tools()

    @property
    def s(self):
        return self.cfg.settings

    def system_prompt(self) -> str:
        return (
            f"{self.s.ai.personality}\n"
            "Du bist die KI des XRZ Trading Bots, der auf dem Mac des Nutzers läuft. Du kannst Märkte analysieren, "
            "Backtests ausführen, Positionen verwalten, TradingView auf dem Mac öffnen und lernst dauerhaft dazu "
            "(Wissensdatenbank).\n" + RULES
        )

    # --------------------------------------------------------------- Werkzeuge
    def _build_tools(self) -> list[ToolSpec]:
        e = self.engine

        def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
            return {"type": "object", "properties": props, "required": required or []}

        sym = {"type": "string", "description": "Symbol, z. B. BTC/USDT, ETH oder AAPL"}

        async def get_status(_: dict) -> str:
            return await e.status_text()

        async def analyze_market(a: dict) -> str:
            symbol = normalize_symbol(a.get("symbol", ""), self.s.trading.watchlist)
            an = await e.analyzer.analyze(symbol, a.get("timeframe") or None)
            e.last_analyses.setdefault(symbol, an)
            return an.to_text()

        async def market_overview(_: dict) -> str:
            res = await e.scan_once() if not e.last_analyses or time.time() - e.last_scan_ts > 600 else list(e.last_analyses.values())
            lines = [a.to_text(compact=True) for a in res]
            sent = await self.sentiment.summary()
            return "\n\n".join(lines + ([sent] if sent else []))

        async def recent_trades(a: dict) -> str:
            n = int(a.get("n", 10))
            trades = self.storage.closed_trades(limit=n)
            if not trades:
                return "Noch keine abgeschlossenen Trades."
            return "\n".join(
                f"#{t.id} [{t.mode}] {t.symbol} {t.side} {t.entry_price:.6g}→{t.exit_price:.6g} "
                f"PnL {t.pnl:+.2f} ({t.r_multiple:+.2f}R) – {t.exit_reason}; Einstieg: {t.entry_reason[:150]}"
                for t in trades
            )

        async def search_knowledge(a: dict) -> str:
            hits = await self.kb.search(a.get("query", ""), int(a.get("k", 5)))
            return "\n---\n".join(f"[{h['topic'] or h['source']}] {h['text']}" for h in hits) or "Nichts gefunden."

        async def learn(a: dict) -> str:
            n = await self.kb.add(a.get("text", ""), source="nutzer", topic=a.get("topic", "") or "notiz", importance=1.5)
            return f"Gespeichert ({n} Abschnitt(e)). Wissensdatenbank: {self.kb.count()} Einträge."

        async def deep_analysis(a: dict) -> str:
            symbol = normalize_symbol(a.get("symbol", ""), self.s.trading.watchlist)
            res = await e.analyzer.deep_analysis(symbol)
            lines = [f"Walk-Forward-Backtest {symbol} ({res['candles']} Kerzen):"]
            for r in sorted(res["reports"].values(), key=lambda r: -r.weight):
                m = r.oos_metrics
                lines.append(
                    f"{r.name}: Gewicht {r.weight * 100:.0f}% | Out-of-Sample {m['total_return_pct']:+.1f}%, "
                    f"{int(m['trades'])} Trades, PF {m['profit_factor']:.2f}, DD {m['max_drawdown_pct']:.1f}% | Parameter {r.params}"
                )
            ens = res["ensemble"]
            lines.append(
                f"Ensemble gesamt: {ens['total_return_pct']:+.1f}% vs. Buy&Hold {ens['buy_hold_pct']:+.1f}%, "
                f"{int(ens['trades'])} Trades, Trefferquote {ens['win_rate_pct']:.0f}%, max. DD {ens['max_drawdown_pct']:.1f}%"
            )
            return "\n".join(lines)

        async def set_trading_mode(a: dict) -> str:
            mode = a.get("mode", "")
            if mode == "paper":
                return await e.set_mode("paper")
            if mode == "off":
                return await e.set_mode("off")
            if mode == "pause":
                return await e.pause()
            if mode == "resume":
                return await e.resume()
            return "Unbekannter Modus. Erlaubt: paper, off, pause, resume. Für Live: request_live_trading."

        async def request_live_trading(_: dict) -> str:
            return await e.request_live()

        async def close_position(a: dict) -> str:
            return await e.close_symbol(a.get("symbol", ""), "Auf Wunsch des Nutzers (Chat)")

        async def open_chart(a: dict) -> str:
            if self.mac is None:
                return "Mac-Steuerung nicht verfügbar."
            symbol = normalize_symbol(a.get("symbol", ""), self.s.trading.watchlist)
            return await self.mac.open_tradingview(symbol, a.get("timeframe") or None)

        async def get_settings(_: dict) -> str:
            return json.dumps(
                {k: get_path(self.s, k) for k in sorted(TELEGRAM_EDITABLE)} | {"trading.watchlist": self.s.trading.watchlist},
                ensure_ascii=False, indent=1,
            )

        async def change_setting(a: dict) -> str:
            key, value = a.get("key", ""), a.get("value", "")
            if key not in TELEGRAM_EDITABLE:
                return f"Diese Einstellung darf per Chat nicht geändert werden: {key}"
            new = set_path(self.cfg.settings, key, str(value))
            self.cfg.save()
            return f"{key} = {new}"

        async def watchlist_add(a: dict) -> str:
            symbol = normalize_symbol(a.get("symbol", ""), self.s.trading.watchlist)
            if symbol in self.s.trading.watchlist:
                return f"{symbol} ist schon in der Watchlist."
            if not await e.market.validate_symbol(symbol):
                return f"{symbol} wurde an der Datenquelle nicht gefunden."
            self.s.trading.watchlist.append(symbol)
            self.cfg.save()
            return f"{symbol} hinzugefügt. Watchlist: {', '.join(self.s.trading.watchlist)}"

        async def watchlist_remove(a: dict) -> str:
            symbol = normalize_symbol(a.get("symbol", ""), self.s.trading.watchlist)
            if symbol not in self.s.trading.watchlist:
                return f"{symbol} ist nicht in der Watchlist."
            self.s.trading.watchlist.remove(symbol)
            self.cfg.save()
            return f"{symbol} entfernt. Watchlist: {', '.join(self.s.trading.watchlist)}"

        async def market_sentiment(_: dict) -> str:
            return await self.sentiment.summary() or "Keine Stimmungsdaten verfügbar."

        return [
            ToolSpec("get_status", "Aktueller Bot-Status: Modus, Kontostand, offene Positionen, letzte Signale.", obj({}), get_status),
            ToolSpec("analyze_market", "Vollständige technische Analyse eines Symbols (Indikatoren, Marktphase, Strategien, Ensemble-Score).",
                     obj({"symbol": sym, "timeframe": {"type": "string", "description": "optional, z. B. 1h, 4h, 1d"}}, ["symbol"]), analyze_market),
            ToolSpec("market_overview", "Überblick über alle Märkte der Watchlist plus Stimmung (Fear & Greed, News).", obj({}), market_overview),
            ToolSpec("recent_trades", "Liste der zuletzt abgeschlossenen Trades mit Ergebnis.", obj({"n": {"type": "integer"}}), recent_trades),
            ToolSpec("search_knowledge", "Durchsucht die Wissensdatenbank (gelerntes Wissen, Lektionen, Dokumente).",
                     obj({"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]), search_knowledge),
            ToolSpec("learn", "Speichert neues Wissen dauerhaft in der Wissensdatenbank.",
                     obj({"text": {"type": "string"}, "topic": {"type": "string"}}, ["text"]), learn),
            ToolSpec("deep_analysis", "Walk-Forward-Backtest aller Strategien für ein Symbol; aktualisiert die Strategie-Gewichte.",
                     obj({"symbol": sym}, ["symbol"]), deep_analysis),
            ToolSpec("set_trading_mode", "Startet/stoppt Paper-Trading oder pausiert/fortsetzt Einstiege. mode: paper | off | pause | resume.",
                     obj({"mode": {"type": "string", "enum": ["paper", "off", "pause", "resume"]}}, ["mode"]), set_trading_mode),
            ToolSpec("request_live_trading", "Fordert Live-Trading (Echtgeld) an. Der Nutzer muss danach selbst mit Code bestätigen.",
                     obj({}), request_live_trading),
            ToolSpec("close_position", "Schließt die offene Position in einem Symbol.", obj({"symbol": sym}, ["symbol"]), close_position),
            ToolSpec("open_tradingview_chart", "Öffnet den TradingView-Chart eines Symbols auf dem Mac.",
                     obj({"symbol": sym, "timeframe": {"type": "string"}}, ["symbol"]), open_chart),
            ToolSpec("get_settings", "Zeigt die per Chat änderbaren Einstellungen.", obj({}), get_settings),
            ToolSpec("change_setting", "Ändert eine Einstellung (nur auf ausdrücklichen Wunsch). key z. B. risk.risk_per_trade_pct.",
                     obj({"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]), change_setting),
            ToolSpec("watchlist_add", "Fügt ein Symbol zur Watchlist hinzu.", obj({"symbol": sym}, ["symbol"]), watchlist_add),
            ToolSpec("watchlist_remove", "Entfernt ein Symbol aus der Watchlist.", obj({"symbol": sym}, ["symbol"]), watchlist_remove),
            ToolSpec("market_sentiment", "Marktstimmung: Krypto Fear & Greed Index und aktuelle Schlagzeilen.", obj({}), market_sentiment),
        ]

    # -------------------------------------------------------------------- Chat
    async def chat(self, channel: str, text: str, images: list[bytes] | None = None) -> str:
        history = self.storage.chat_history(channel, self.s.ai.chat_history_messages)
        knowledge = await self.kb.context_for(text, k=4, max_chars=2500)
        lessons = self.kb.recent_lessons(3)
        context = [f"[Kontext – {time.strftime('%d.%m.%Y %H:%M')}, Modus: {self.engine.mode}]"]
        if knowledge:
            context.append(f"Relevantes Wissen aus deiner Datenbank:\n{knowledge}")
        if lessons:
            context.append("Zuletzt gelernte Lektionen:\n" + "\n".join(f"- {l}" for l in lessons))
        user_text = f"{text}\n\n" + "\n\n".join(context)
        try:
            reply = await self.router.agent(self.system_prompt(), history, user_text, self.tools, images=images)
        except LLMError as exc:
            reply = (
                "⚠️ Die KI ist gerade nicht erreichbar.\n"
                f"Details: {exc}\n\n"
                "Tipp: Ollama-App starten (lokale KI) oder im Dashboard einen API-Key eintragen. "
                "Befehle wie /status, /analyse, /markt funktionieren auch ohne KI."
            )
            return reply
        reply = reply or "(keine Antwort)"
        self.storage.add_chat(channel, "user", text if not images else f"[Bild] {text}")
        self.storage.add_chat(channel, "assistant", reply)
        return reply

    # ------------------------------------------------------------ Trade-Check
    async def review_trade(self, analysis: Any, side: str) -> dict[str, Any]:
        """Zweite Meinung vor einem Trade. Darf nur ablehnen oder verkleinern."""
        knowledge = await self.kb.context_for(f"{analysis.symbol} {analysis.regime.label} {side} Einstieg Risiko", k=3, max_chars=1500)
        lessons = self.kb.recent_lessons(5)
        recent = [t for t in self.storage.closed_trades(limit=30) if t.symbol == analysis.symbol][:5]
        sent = await self.sentiment.summary() if "/" in analysis.symbol else ""
        prompt = (
            f"Geplanter Trade: {side.upper()} {analysis.symbol}.\n\n{analysis.to_text()}\n\n"
            + (f"Marktstimmung:\n{sent}\n\n" if sent else "")
            + ("Letzte Trades in diesem Markt:\n" + "\n".join(
                f"- {t.side} {t.pnl:+.2f} ({t.r_multiple:+.2f}R) {t.exit_reason}" for t in recent) + "\n\n" if recent else "")
            + (f"Wissen:\n{knowledge}\n\n" if knowledge else "")
            + ("Gelernte Lektionen:\n" + "\n".join(f"- {l}" for l in lessons) + "\n\n" if lessons else "")
            + "Prüfe den Trade kritisch als Risiko-Manager. Antworte NUR mit JSON:\n"
            '{"approve": true|false, "confidence": 0-100, "size_factor": 0.25-1.0, "reason": "1-2 Sätze auf Deutsch"}\n'
            "approve=false nur bei klaren Warnsignalen (z. B. Trade gegen starken Trend, direkt vor Widerstand, "
            "wiederholte Verluste mit gleichem Muster, extreme Stimmung). size_factor < 1 bei erhöhter Unsicherheit."
        )
        try:
            raw = await self.router.complete(
                "Du bist ein strenger, erfahrener Risiko-Manager. Du antwortest ausschließlich mit gültigem JSON.", prompt, max_tokens=800
            )
        except LLMError as exc:
            factor = 0.5 if self.engine.mode == "live" else 1.0
            return {"approve": True, "size_factor": factor, "reason": f"KI nicht erreichbar – Entscheidung nur nach Regelwerk (Größe ×{factor}).", "error": str(exc)}
        data = extract_json(raw) or {}
        approve = data.get("approve", True)
        if isinstance(approve, str):
            approve = approve.strip().lower() in ("true", "ja", "yes", "1")
        try:
            factor = float(data.get("size_factor", 1.0))
        except (TypeError, ValueError):
            factor = 1.0
        return {
            "approve": bool(approve),
            "size_factor": max(0.25, min(1.0, factor)),
            "confidence": data.get("confidence"),
            "reason": str(data.get("reason") or raw[:200]).strip(),
        }

    # ----------------------------------------------------------------- Lernen
    async def reflect_on_trade(self, trade: Trade) -> None:
        prompt = (
            f"Abgeschlossener Trade: {trade.side.upper()} {trade.symbol} [{trade.mode}]\n"
            f"Einstieg {trade.entry_price:.6g}, Ausstieg {trade.exit_price:.6g}, Ergebnis {trade.pnl:+.2f} ({trade.r_multiple:+.2f}R)\n"
            f"Dauer: {((trade.exit_time or 0) - trade.entry_time) / 3600:.1f} h, Ausstiegsgrund: {trade.exit_reason}\n"
            f"Einstiegsgrund: {trade.entry_reason}\nMarktphase beim Einstieg: {trade.meta.get('regime', '?')}\n"
            f"KI-Kommentar beim Einstieg: {trade.ai_comment or '-'}\n\n"
            "Formuliere EINE konkrete, wiederverwendbare Lektion (max. 2 Sätze), die künftige Entscheidungen verbessert. "
            "Keine Floskeln. Nur die Lektion als Text."
        )
        try:
            lesson = (await self.router.complete("Du bist ein Trading-Coach, der aus Trades lernt.", prompt, max_tokens=400)).strip()
        except LLMError:
            lesson = (
                f"{trade.symbol} {trade.side} in Phase '{trade.meta.get('regime', '?')}' endete mit {trade.r_multiple:+.2f}R "
                f"({trade.exit_reason})."
            )
        if lesson:
            await self.kb.add(f"{time.strftime('%d.%m.%Y')} {trade.symbol}: {lesson}", source=f"trade:{trade.id}", topic="lektion")
            trade.meta["lesson"] = lesson
            if trade.id:
                self.storage.update_trade(trade)

    async def daily_comment(self, report: str) -> str:
        sent = await self.sentiment.summary()
        prompt = f"{report}\n\n{sent}\n\nGib eine kurze Einschätzung (max. 5 Sätze): Was lief gut/schlecht, worauf morgen achten?"
        try:
            return await self.router.complete(self.system_prompt(), prompt, max_tokens=800)
        except LLMError:
            return ""

    async def analyze_image(self, image: bytes, question: str = "") -> str:
        q = question or "Analysiere diesen Chart: Trend, wichtige Niveaus, Muster, mögliche Szenarien mit Einstieg/Stop."
        try:
            return await self.router.complete(self.system_prompt(), q, images=[image], max_tokens=2000)
        except LLMError as exc:
            return f"Bildanalyse nicht möglich: {exc}"
