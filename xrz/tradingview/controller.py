"""Steuert die TradingView-Desktop-App: Konto prüfen, Order-Ticket ausfüllen, absenden, kontrollieren.

Grundsätze:
* Lesen über die interne Broker-API (Wahrheitsquelle), Handeln über echte Klicks in der Oberfläche.
* Vor JEDER Order wird geprüft, ob das aktive Konto zum Modus passt (Paper ↔ Echtgeld).
* Nach dem Ausfüllen wird alles zurückgelesen; nur wenn alles stimmt, wird geklickt.
* Ein Absenden wird NIE automatisch wiederholt (keine Doppel-Orders). Im Zweifel: abbrechen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from . import js, labels
from .cdp import CdpClient, CdpError
from .launcher import TradingViewLauncher
from .models import AccountInfo, AccountKind, OrderRequest, OrderResult
from .safety import (
    check_account, check_brackets, check_close_dialog, check_dialog, check_ticket, classify_account, evaluate_close,
    evaluate_fill, floor_to_step, format_number, net_position, parse_number, price_tolerance, split_symbol, unambiguous_qty,
    validate_request,
)

log = logging.getLogger(__name__)

HEALTH_TTL = 600.0


class _Abort(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


class TradingViewController:
    def __init__(self, settings: Callable[[], Settings], data_dir: Path):
        self._settings = settings
        self.data_dir = Path(data_dir)
        self.launcher = TradingViewLauncher(settings, data_dir)
        self._client: CdpClient | None = None
        self._lock = asyncio.Lock()
        self._health: tuple[float, dict[str, Any]] | None = None

    @property
    def s(self) -> Settings:
        return self._settings()

    # ------------------------------------------------------------- Verbindung
    async def _ensure_client(self) -> CdpClient | None:
        if self._client is not None and self._client.connected:
            return self._client
        if self._client is not None:
            await self._close_client()
        if not self.launcher.supported():
            return None
        try:
            targets = await self.launcher.chart_targets(self.s.tradingview.cdp_port)
        except Exception as exc:
            log.debug("CDP-Ziele nicht lesbar: %s", exc)
            return None
        if not targets:
            return None
        try:
            self._client = await CdpClient.connect(targets[0]["webSocketDebuggerUrl"])
        except Exception as exc:
            log.info("Verbindung zur TradingView-Seite fehlgeschlagen: %s", exc)
            self._client = None
        return self._client

    async def _close_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None

    async def disconnect(self) -> None:
        await self._close_client()

    async def _eval(self, expr: str, timeout: float = 15.0) -> Any:
        client = await self._ensure_client()
        if client is None:
            raise _Abort("connect", "TradingView ist nicht verbunden.")
        try:
            return await client.evaluate(expr, await_promise=True, timeout=timeout)
        except CdpError as exc:
            await self._close_client()
            raise _Abort("cdp", f"TradingView antwortet nicht wie erwartet: {exc}") from exc

    def _base_status(self, message: str, version: str = "") -> dict[str, Any]:
        return {"connected": False, "driver": "none", "account": None, "message": message, "version": version}

    async def status(self) -> dict[str, Any]:
        try:
            return await self._status()
        except Exception as exc:  # status() darf nie werfen
            log.warning("TradingView-Status: %s", exc)
            return self._base_status(f"Fehler beim Prüfen von TradingView: {exc}")

    async def _status(self) -> dict[str, Any]:
        if not self.launcher.supported():
            return self._base_status("Die TradingView-Steuerung funktioniert nur auf dem Mac.")
        info = self.launcher.locate()
        if info is None:
            return self._base_status("TradingView Desktop wurde nicht gefunden (bitte von tradingview.com/desktop installieren).")
        client = await self._ensure_client()
        if client is None:
            running = bool(await self.launcher.main_pids())
            msg = ("TradingView läuft ohne Steuer-Port. Tippe in der Zentrale auf „Verbinden“ – ich starte TradingView "
                   "dann einmal neu." if running else "TradingView ist nicht gestartet. „Verbinden“ startet es mit Steuerung.")
            return self._base_status(msg, info.version)
        account = await self.read_account()
        health = await self.selector_health()
        msg = account.label if health.get("trading_ok") else f"{account.label} – {health.get('message', '')}"
        return {"connected": True, "driver": "cdp", "account": account.to_dict(), "message": msg,
                "version": info.version, "health": health}

    async def connect(self, allow_restart: bool = False) -> dict[str, Any]:
        st = await self.status()
        if st.get("connected") or not self.launcher.supported():
            return st
        info = self.launcher.locate()
        if info is None:
            return st
        port = self.s.tradingview.cdp_port
        running = bool(await self.launcher.main_pids())
        if running and not allow_restart:
            st["message"] = ("Darf ich TradingView einmal neu starten, damit ich es steuern kann? "
                             "Bestätige in der Zentrale mit „Neu starten & verbinden“.")
            st["needs_restart"] = True
            return st
        ok, msg = await (self.launcher.restart_with_cdp(info, port) if running else self.launcher.launch(info, port))
        if not ok:
            return self._base_status(msg, info.version)
        self._health = None
        return await self.status()

    # ---------------------------------------------------------------- Lesen
    async def read_account(self) -> AccountInfo:
        try:
            state = await self._eval(js.broker_state())
        except _Abort as exc:
            return AccountInfo(problems=[exc.message])
        return classify_account(state if isinstance(state, dict) else None, self.s.tradingview.paper_account_name)

    async def selector_health(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._health and now - self._health[0] < HEALTH_TTL:
            return self._health[1]
        try:
            res = await self._eval(js.health())
        except _Abort as exc:
            return {"trading_ok": False, "missing": [], "message": exc.message}
        api = (res or {}).get("api") or {}
        missing = [k for k in labels.REQUIRED_API if not api.get(k)]
        out = {"trading_ok": not missing, "missing": missing, "api": api, "dom": (res or {}).get("dom") or {},
               "message": ("Alle benötigten TradingView-Funktionen gefunden." if not missing else
                           "TradingView hat sich verändert (fehlt: " + ", ".join(missing) + ") – Handeln ist gesperrt.")}
        self._health = (now, out)
        return out

    async def set_symbol(self, tv_symbol: str) -> bool:
        try:
            res = await self._eval(js.set_symbol(tv_symbol), timeout=20)
        except _Abort:
            return False
        return bool((res or {}).get("ok"))

    async def quote(self, tv_symbol: str) -> dict[str, Any] | None:
        try:
            res = await self._eval(js.quote(tv_symbol))
        except _Abort:
            return None
        if not (res or {}).get("ok"):
            return None
        return {"bid": res.get("bid"), "ask": res.get("ask"), "last": res.get("last")}

    async def _positions_orders(self) -> dict[str, Any]:
        res = await self._eval(js.positions_orders())
        if not (res or {}).get("ok"):
            raise _Abort("positions", f"Positionen nicht lesbar: {(res or {}).get('reason', 'unbekannt')}")
        return res

    async def positions(self) -> list[dict[str, Any]]:
        try:
            res = await self._positions_orders()
        except _Abort:
            return []
        return [{"symbol": p.get("symbol"), "side": "long" if (p.get("side") or 1) > 0 else "short",
                 "qty": p.get("qty"), "avg_price": p.get("avgPrice"), "take_profit": p.get("takeProfit"),
                 "stop_loss": p.get("stopLoss"), "id": p.get("id")} for p in res.get("positions") or []]

    async def screenshot_b64(self) -> str | None:
        client = await self._ensure_client()
        if client is None:
            return None
        try:
            return await client.screenshot()
        except Exception:
            return None

    # ------------------------------------------------------------ Bausteine
    async def _find(self, sels, **kw) -> dict[str, Any]:
        res = await self._eval(js.find(sels, **kw))
        return res if isinstance(res, dict) else {"count": 0}

    async def _click(self, el: dict[str, Any], what: str) -> None:
        if el.get("count") != 1:
            raise _Abort("ui", f"„{what}“ nicht eindeutig gefunden ({el.get('count', 0)} Treffer).")
        if el.get("forbidden"):
            raise _Abort("ui", f"„{what}“ liegt auf einem gesperrten Knopf (Ein-Klick-Handel) – abgebrochen.")
        if el.get("disabled"):
            raise _Abort("ui", f"„{what}“ ist gerade nicht klickbar.")
        if el.get("covered"):
            raise _Abort("ui", f"„{what}“ ist verdeckt (anderes Fenster/Popup?).")
        client = await self._ensure_client()
        if client is None:
            raise _Abort("connect", "TradingView ist nicht verbunden.")
        await client.click_at(float(el["x"]), float(el["y"]))
        await asyncio.sleep(0.35)

    async def _ticket(self) -> dict[str, Any]:
        res = await self._eval(js.ticket_state())
        return res if isinstance(res, dict) else {"open": False}

    async def _open_ticket(self) -> None:
        ts = await self._ticket()
        if ts.get("open"):
            return
        client = await self._ensure_client()
        if ts.get("focusedEditable"):
            await self._eval(js.blur_active())
        k = labels.OPEN_TICKET_KEY
        await client.key(k["key"], k["code"], k["key_code"], k["modifiers"])
        for _ in range(12):
            await asyncio.sleep(0.25)
            if (await self._ticket()).get("open"):
                return
        btn = await self._find(labels.OPEN_TICKET_BUTTONS, text=labels.OPEN_TICKET_TEXT_RE)
        if btn.get("count") == 1:
            await self._click(btn, "Handeln")
            for _ in range(12):
                await asyncio.sleep(0.25)
                if (await self._ticket()).get("open"):
                    return
        raise _Abort("ticket", "Das Order-Ticket in TradingView ließ sich nicht öffnen (Handelspanel sichtbar?).")

    async def _discard_ticket(self) -> None:
        try:
            for sels in (labels.DISCARD_BUTTON, labels.TICKET_CLOSE_BUTTON):
                el = await self._find(sels, scope=labels.TICKET_CONTAINERS)
                if el.get("count") == 1 and not el.get("disabled") and not el.get("covered"):
                    await self._click(el, "Verwerfen")
                    return
        except Exception as exc:
            log.info("Ticket nicht verworfen: %s", exc)

    async def _set_input(self, sels, value: float, what: str, label: str | None = None) -> None:
        res = await self._eval(js.set_input(sels, format_number(value), label=label, scope=labels.TICKET_CONTAINERS))
        if not (res or {}).get("ok"):
            raise _Abort("ui", f"{what} ließ sich nicht eintragen: {(res or {}).get('reason', 'unbekannt')}.")
        got = parse_number((res or {}).get("value"))
        if got is None or abs(got - value) > price_tolerance(value, None) + abs(value) * 1e-6:
            raise _Abort("readback", f"{what}: eingetragen {value:g}, TradingView zeigt {res.get('value')!r}.")

    async def _bracket(self, toggle, field, value: float, what: str) -> None:
        tg = await self._find(toggle, scope=labels.TICKET_CONTAINERS, loose=True)
        if tg.get("count") != 1:
            raise _Abort("brackets", f"Schalter für {what} nicht gefunden.")
        if not tg.get("on"):
            await self._click(tg, what)
            tg = await self._find(toggle, scope=labels.TICKET_CONTAINERS, loose=True)
            if not tg.get("on"):
                raise _Abort("brackets", f"{what} ließ sich nicht einschalten.")
        await self._set_input(field, value, what)

    async def _confirm_dialog(self, check: Callable[[str], tuple[bool, str]], confirm_re: str,
                              expected: AccountKind, wait: float = 3.0) -> str:
        """Bestätigungsdialog behandeln: nur bestätigen, wenn sein Text zur Order passt."""
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            ds = await self._eval(js.dialogs())
            if isinstance(ds, list) and ds:
                d = ds[0]
                ok, why = check(str(d.get("text") or ""))
                buttons = d.get("buttons") or []
                if ok:
                    ok2, why2 = check_account(expected, await self.read_account())
                    if not ok2:
                        why, ok = why2, False
                if ok:
                    btn = next((b for b in buttons if re.search(confirm_re, str(b.get("text") or ""), re.I)
                                and not b.get("forbidden") and not b.get("disabled") and not b.get("covered")), None)
                    if btn is None:
                        ok, why = False, "Bestätigen-Knopf im Dialog nicht gefunden."
                    else:
                        await self._click({**btn, "count": 1}, "Bestätigen")
                        return "bestätigt"
                cancel = next((b for b in buttons if re.search(labels.CANCEL_BUTTON_RE, str(b.get("text") or ""), re.I)
                               and not b.get("covered")), None)
                if cancel:
                    await self._click({**cancel, "count": 1}, "Abbrechen")
                raise _Abort("dialog", f"Bestätigungsdialog passte nicht: {why}")
            await asyncio.sleep(0.3)
        return "kein Dialog"

    def _audit(self, kind: str, req: Any, result: OrderResult, expected: AccountKind) -> None:
        try:
            path = self.data_dir / "logs" / "tradingview_audit.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            line = {"ts": time.time(), "kind": kind, "expected": expected.value,
                    "request": req.to_dict() if hasattr(req, "to_dict") else req,
                    "result": result.to_dict(include_screenshot=False)}
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.info("Audit-Log nicht schreibbar: %s", exc)

    # --------------------------------------------------------------- Order
    async def place_order(self, req: OrderRequest, expected: AccountKind, dry_run: bool = False) -> OrderResult:
        async with self._lock:
            ticket_opened = False
            try:
                result = await self._place(req, expected, dry_run)
            except _Abort as exc:
                result = OrderResult(ok=False, stage=exc.stage, message=exc.message)
                ticket_opened = exc.stage not in ("validate", "connect", "health", "account", "symbol", "brackets_check")
            except Exception as exc:  # nie mit halbem Zustand weiterlaufen
                log.exception("Unerwarteter Fehler bei der Order")
                result = OrderResult(ok=False, stage="error", message=f"Unerwarteter Fehler: {exc}")
                ticket_opened = True
            if not result.ok and ticket_opened and result.stage not in ("verify", "submitted"):
                await self._discard_ticket()
            if not dry_run:
                self._audit("order", req, result, expected)
            return result

    async def _place(self, req: OrderRequest, expected: AccountKind, dry_run: bool) -> OrderResult:
        ok, why = validate_request(req)
        if not ok:
            raise _Abort("validate", why)
        if await self._ensure_client() is None:
            raise _Abort("connect", "TradingView ist nicht verbunden (Zentrale → TradingView → Verbinden).")
        health = await self.selector_health(force=True)
        if not health.get("trading_ok"):
            raise _Abort("health", health.get("message", "TradingView-Funktionen fehlen."))
        ok, why = check_account(expected, await self.read_account())
        if not ok:
            raise _Abort("account", why)
        client = await self._ensure_client()
        await client.bring_to_front()
        res = await self._eval(js.set_symbol(req.symbol), timeout=20)
        if not (res or {}).get("ok"):
            raise _Abort("symbol", f"Symbol {req.symbol} ließ sich in TradingView nicht öffnen ({(res or {}).get('reason', '')}).")
        si = await self._eval(js.symbol_info(req.symbol))
        si = si if isinstance(si, dict) and si.get("ok") else {}
        step, min_tick = si.get("qtyStep"), si.get("minTick")
        qty = unambiguous_qty(floor_to_step(req.qty, step), step, si.get("qtyMin"))
        if qty <= 0:
            raise _Abort("validate", f"Menge {req.qty:g} liegt unter dem Minimum für {req.symbol}.")
        req = replace(req, qty=qty)
        if req.take_profit or req.stop_loss:
            ok, why = check_brackets(req, await self.quote(req.symbol))
            if not ok:
                raise _Abort("brackets_check", why)

        await self._open_ticket()
        side = await self._find(labels.SIDE_CONTROLS[req.side], text=labels.SIDE_TEXT_RE[req.side],
                                scope=labels.TICKET_CONTAINERS)
        await self._click(side, "Kaufen" if req.side == "buy" else "Verkaufen")
        tab = await self._find(labels.ORDER_TYPE_TABS[req.order_type], text=labels.ORDER_TYPE_TEXT_RE[req.order_type],
                               scope=labels.TICKET_CONTAINERS)
        if tab.get("ariaSelected") != "true":
            await self._click(tab, "Ordertyp")
            tab = await self._find(labels.ORDER_TYPE_TABS[req.order_type], text=labels.ORDER_TYPE_TEXT_RE[req.order_type],
                                   scope=labels.TICKET_CONTAINERS)
            if tab.get("ariaSelected") != "true":
                raise _Abort("ui", "Ordertyp ließ sich nicht auswählen.")
        await self._set_input(labels.QUANTITY_INPUT, qty, "Menge")
        if req.order_type == "limit" and req.limit_price:
            await self._set_input(labels.LIMIT_PRICE_INPUT, req.limit_price, "Limitpreis", label=labels.LIMIT_PRICE_LABEL_RE)
        if req.take_profit or req.stop_loss:
            exits = await self._find([], text=labels.EXITS_TEXT_RE, scope=labels.TICKET_CONTAINERS)
            if exits.get("count") == 1 and exits.get("ariaExpanded") == "false":
                await self._click(exits, "Ausstiege")
            if req.take_profit:
                await self._bracket(labels.TP_TOGGLE, labels.TP_INPUT, req.take_profit, "Take-Profit")
            if req.stop_loss:
                await self._bracket(labels.SL_TOGGLE, labels.SL_INPUT, req.stop_loss, "Stop-Loss")

        ts = await self._ticket()
        ok, why = check_ticket(req, str(ts.get("submitText") or ""), step)
        if not ok:
            raise _Abort("readback", why)
        if ts.get("submitDisabled"):
            raise _Abort("readback", "TradingView lässt das Absenden nicht zu (Ticket unvollständig?).")
        order = js.api_order(req.symbol, req.side, qty, req.order_type, req.limit_price, req.take_profit, req.stop_loss)
        pv = await self._eval(js.preview_order(order))
        if isinstance(pv, dict) and pv.get("available") and not pv.get("ok"):
            raise _Abort("preview", f"TradingView lehnt die Order in der Vorschau ab: {pv.get('error', '')}")
        if dry_run:
            shot = await self.screenshot_b64()
            await self._discard_ticket()
            return OrderResult(ok=True, stage="dry_run", message=f"Test erfolgreich – Ticket war korrekt: {ts.get('submitText')}. "
                               "Nichts abgeschickt.", screenshot_b64=shot)

        ok, why = check_account(expected, await self.read_account())
        if not ok:
            raise _Abort("account", why)
        before = await self._positions_orders()
        submit = await self._find(labels.SUBMIT_BUTTON, scope=labels.TICKET_CONTAINERS)
        ok, why = check_ticket(req, str(submit.get("text") or ""), step)
        if not ok:
            raise _Abort("readback", why)
        await self._click(submit, "Absenden")
        try:
            await self._confirm_dialog(lambda text: check_dialog(text, req, step), labels.CONFIRM_BUTTON_RE, expected)
        except _Abort as exc:
            exc.stage = "dialog"
            raise
        deadline = time.monotonic() + 8.0
        ev: dict[str, Any] = {"status": "none"}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.6)
            try:
                after = await self._positions_orders()
            except _Abort:
                continue
            ev = evaluate_fill(before, after, req, qty, step, min_tick)
            if ev["status"] in ("filled", "working", "rejected"):
                break
        shot = await self.screenshot_b64()
        if ev["status"] == "rejected":
            return OrderResult(ok=False, stage="rejected", message=f"TradingView hat die Order abgelehnt: {ev.get('message', '')}",
                               order_id=ev.get("order_id", ""), screenshot_b64=shot, details=ev)
        if ev["status"] in ("filled", "partial"):
            msg = f"Ausgeführt: {req.describe()}"
            if ev["status"] == "partial":
                msg = f"Teilweise ausgeführt ({ev.get('filled_qty')}): {req.describe()}"
            if ev.get("brackets_ok") is False:
                msg += " – ACHTUNG: Stop-Loss/Take-Profit sind in TradingView nicht sichtbar, bitte prüfen!"
            return OrderResult(ok=True, stage="done", message=msg, order_id=ev.get("order_id", ""),
                               filled_qty=ev.get("filled_qty"), avg_price=ev.get("avg_price"), screenshot_b64=shot, details=ev)
        if ev["status"] == "working":
            return OrderResult(ok=True, stage="working", message=f"Limit-Order liegt in TradingView: {req.describe()}",
                               order_id=ev.get("order_id", ""), screenshot_b64=shot, details=ev)
        return OrderResult(ok=False, stage="verify", screenshot_b64=shot, details=ev,
                           message="Abgeschickt, aber in TradingView nicht bestätigt – bitte selbst prüfen. Ich wiederhole nichts.")

    # ------------------------------------------------------------ Schließen
    async def close_position(self, tv_symbol: str, expected: AccountKind) -> OrderResult:
        async with self._lock:
            try:
                result = await self._close(tv_symbol, expected)
            except _Abort as exc:
                result = OrderResult(ok=False, stage=exc.stage, message=exc.message)
            except Exception as exc:
                log.exception("Unerwarteter Fehler beim Schließen")
                result = OrderResult(ok=False, stage="error", message=f"Unerwarteter Fehler: {exc}")
            self._audit("close", {"symbol": tv_symbol}, result, expected)
            return result

    async def _close(self, sym: str, expected: AccountKind) -> OrderResult:
        if await self._ensure_client() is None:
            raise _Abort("connect", "TradingView ist nicht verbunden.")
        ok, why = check_account(expected, await self.read_account())
        if not ok:
            raise _Abort("account", why)
        before = await self._positions_orders()
        if abs(net_position(before.get("positions") or [], sym)) < 1e-12:
            return OrderResult(ok=True, stage="none", message=f"Keine offene {sym}-Position in TradingView (schon geschlossen).")
        client = await self._ensure_client()
        await client.bring_to_front()
        ticker = split_symbol(sym)[1]
        closed_via = ""
        tab = await self._find(labels.POSITIONS_TAB)
        if tab.get("count") == 1 and tab.get("ariaSelected") != "true":
            await self._click(tab, "Positionen")
        row = await self._eval(js.position_row(ticker))
        if isinstance(row, dict) and row.get("count") == 1 and row.get("row"):
            await client.move_to(float(row["row"]["x"]), float(row["row"]["y"]))
            await asyncio.sleep(0.4)
            row = await self._eval(js.position_row(ticker))
            btn = (row or {}).get("close")
            if btn and not btn.get("covered") and not btn.get("disabled") and not btn.get("forbidden"):
                await self._click({**btn, "count": 1}, "Position schließen")
                await self._confirm_dialog(lambda text: check_close_dialog(text, sym), labels.CLOSE_CONFIRM_BUTTON_RE, expected)
                closed_via = "Oberfläche"
        if not closed_via:
            if not self.s.tradingview.api_fallback:
                raise _Abort("ui", "Schließen-Knopf der Position nicht gefunden – bitte in TradingView selbst schließen.")
            pos = next((p for p in before.get("positions") or [] if split_symbol(str(p.get("symbol")))[1] == ticker), None)
            if not pos:
                raise _Abort("ui", "Position nicht eindeutig gefunden.")
            res = await self._eval(js.close_position_api(str(pos.get("id"))))
            if not (res or {}).get("ok"):
                raise _Abort("api", f"Schließen über TradingView fehlgeschlagen: {(res or {}).get('error', '')}")
            closed_via = "Schnittstelle"
        deadline = time.monotonic() + 8.0
        ev = {"status": "none"}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.6)
            try:
                ev = evaluate_close(before, await self._positions_orders(), sym)
            except _Abort:
                continue
            if ev["status"] == "closed":
                break
        q = await self.quote(sym)
        price = (q or {}).get("last")
        if ev["status"] == "closed":
            return OrderResult(ok=True, stage="done", message=f"{sym}-Position geschlossen ({closed_via}).",
                               filled_qty=ev.get("closed_qty"), avg_price=price, details=ev)
        return OrderResult(ok=False, stage="verify", details=ev,
                           message="Schließen nicht bestätigt – bitte in TradingView prüfen (Stop/Ziel noch aktiv?).")
