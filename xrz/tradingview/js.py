"""JavaScript-Bausteine, die per CDP in der TradingView-Seite laufen.

Jeder Baustein ist eine Funktion, die einen fertigen Ausdruck liefert:

    /*xrz:<name>*/(async (A) => { …gemeinsame Helfer… …Rumpf… })(/*xrz-args*/<JSON>)

* Daten (Symbole, Selektoren, Werte) gehen AUSSCHLIESSLICH als JSON-Argument ``A`` hinein
  (``json.dumps``) – nie per Text-Verkettung in den Code.
* Die Marke ``/*xrz:<name>*/`` am Anfang ist stabil: Tests (Fake-Seite) erkennen daran,
  welcher Baustein gemeint ist, und lesen die Argumente hinter ``/*xrz-args*/``.
* Alle Aufrufe der internen TradingView-API haben eine Zeitgrenze im Browser und liefern
  Fehler als Daten zurück (keine Ausnahmen), damit die Python-Seite sauber abbrechen kann.

Die interne API (``TradingViewApi.waitTrading()`` → ``activeBroker()`` …) ist undokumentiert
(siehe labels.py) – alle Felder werden defensiv gelesen.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from . import labels

ARGS_MARKER = "/*xrz-args*/"

# Gemeinsame Helfer für alle Bausteine (werden in jeden Ausdruck eingebettet)
_PRELUDE = r"""
const wv = (x) => { try { return (x && typeof x.value === 'function') ? x.value() : x; } catch (e) { return null; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const TIMEOUT = {timeout: true};
const within = (p, ms) => Promise.race([Promise.resolve(p), sleep(ms).then(() => TIMEOUT)]);
const errText = (e) => String((e && e.message) || e).slice(0, 300);
const call = async (obj, name, args, ms) => {
  if (!obj || typeof obj[name] !== 'function') return {missing: true};
  try {
    const r = await within(obj[name](...(args || [])), ms || 4000);
    if (r === TIMEOUT) return {error: name + ': Zeitüberschreitung'};
    return {value: wv(r)};
  } catch (e) { return {error: name + ': ' + errText(e)}; }
};
const num = (x) => {
  if (typeof x === 'number') return isFinite(x) ? x : null;
  if (typeof x === 'string' && x.trim() !== '' && isFinite(Number(x))) return Number(x);
  return null;
};
const vis = (el) => {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity || '1') > 0.05;
};
const visLoose = (el) => {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
};
const q = (sels, root) => {
  const out = [];
  for (const s of (sels || [])) {
    try { (root || document).querySelectorAll(s).forEach((e) => { if (!out.includes(e)) out.push(e); }); } catch (e) {}
  }
  return out;
};
const txt = (el) => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
const lang = () => String(document.documentElement.lang || navigator.language || '');
const editableFocused = () => {
  const ae = document.activeElement;
  return !!(ae && ae !== document.body && (ae.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName)));
};
const trading = async () => {
  const api = window.TradingViewApi;
  if (!api) return {error: 'TradingViewApi fehlt'};
  let r = await call(api, 'waitTrading', [], 5000);
  if (!r.value) r = await call(api, 'trading', [], 3000);
  const t = r.value;
  if (!t) return {error: r.error || 'Handelsmodul nicht verfügbar'};
  let b = null, cs = null;
  try { b = wv(typeof t.activeBroker === 'function' ? t.activeBroker() : t.activeBroker); } catch (e) {}
  try { cs = wv(typeof t.connectStatus === 'function' ? t.connectStatus() : t.connectStatus); } catch (e) {}
  return {t, b, cs};
};
const labelsOf = (e) => {
  const parts = [e.getAttribute('aria-label') || '', e.getAttribute('placeholder') || ''];
  if (e.id) { try { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) parts.push(txt(l)); } catch (x) {} }
  const lab = e.closest('label'); if (lab) parts.push(txt(lab));
  let p = e.parentElement;
  for (let i = 0; i < 4 && p; i++, p = p.parentElement) { const t = txt(p); if (t) { if (t.length < 80) parts.push(t); break; } }
  return parts.filter(Boolean);
};
// Genau EIN sichtbares Element finden: Selektoren zuerst, Text/Beschriftung als Rückfall.
const locate = (S) => {
  const isVis = S.loose ? visLoose : vis;
  const roots = (S.scope && S.scope.length) ? q(S.scope).filter((e) => vis(e)) : [document];
  if (!roots.length) return {els: [], scope: false, via: 'scope'};
  const collect = (sels) => { const out = []; for (const r of roots) for (const e of q(sels, r)) if (!out.includes(e)) out.push(e); return out; };
  const ex = (S.exclude && S.exclude.length) ? collect(S.exclude) : [];
  const ok = (e) => isVis(e) && !ex.some((x) => x === e || x.contains(e));
  const re = S.text ? new RegExp(S.text, 'i') : null;
  const lre = S.label ? new RegExp(S.label, 'i') : null;
  let via = 'selector';
  let els = collect(S.sels).filter(ok);
  if (re && els.length > 1) els = els.filter((e) => re.test(txt(e)));
  if (!els.length && (re || lre)) {
    via = lre ? 'label' : 'text';
    els = collect(S.fallback).filter((e) => ok(e) && (lre ? labelsOf(e).some((t) => lre.test(t)) : re.test(txt(e))));
    els = els.filter((e) => !els.some((o) => o !== e && e.contains(o)));
  }
  return {els, scope: true, via};
};
const describe = (el, S) => {
  if (S.scroll !== false) { try { el.scrollIntoView({block: 'nearest', inline: 'nearest'}); } catch (x) {} }
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const near = S.loose && el.parentElement;
  const hit = !!top && (top === el || el.contains(top) || (S.loose && (top.contains(el) || (near && near.contains(top)))));
  const forbid = (S.forbid || []).join(',');
  const sw = el.matches('input') ? el : el.querySelector('input[type="checkbox"],input[role="switch"],[role="switch"],[aria-checked]');
  return {
    count: 1, scope: true, x, y, w: r.width, h: r.height, covered: !hit,
    forbidden: !!(forbid && (el.closest(forbid) || (top && top.closest && top.closest(forbid)))),
    disabled: !!(el.disabled || el.closest('[aria-disabled="true"],fieldset[disabled]')),
    readOnly: !!el.readOnly,
    text: txt(el).slice(0, 200), value: (typeof el.value === 'string') ? el.value : null,
    tag: el.tagName.toLowerCase(), id: el.id || '', dataName: el.getAttribute('data-name') || '',
    ariaSelected: el.getAttribute('aria-selected'), ariaExpanded: el.getAttribute('aria-expanded'),
    on: el.getAttribute('aria-checked') === 'true' || el.checked === true ||
        !!(sw && (sw.getAttribute('aria-checked') === 'true' || sw.checked === true)),
  };
};
"""


def _snippet(name: str, body: str, args: Any = None) -> str:
    payload = json.dumps({} if args is None else args, ensure_ascii=True, separators=(",", ":"))
    return f"/*xrz:{name}*/(async (A) => {{{_PRELUDE}\n{body}\n}})({ARGS_MARKER}{payload})"


def _spec(sels: Sequence[str], *, text: str | None = None, label: str | None = None,
          scope: Sequence[str] | None = None, exclude: Sequence[str] | None = None,
          loose: bool = False, inputs: bool = False) -> dict[str, Any]:
    return {
        "sels": list(sels), "text": text, "label": label, "scope": list(scope or []), "exclude": list(exclude or []),
        "loose": loose, "forbid": list(labels.FORBIDDEN_CLICK),
        "fallback": list(labels.INPUT_FALLBACK_TAGS if (inputs or label) else labels.TEXT_FALLBACK_TAGS),
    }


# --------------------------------------------------------------------------- #
# Zustand lesen
# --------------------------------------------------------------------------- #
def probe() -> str:
    """Ist die Seite bereit? → {api, visibility, focus, url, title, ready, lang}"""
    return _snippet("probe", r"""
return {api: !!window.TradingViewApi, visibility: document.visibilityState, focus: document.hasFocus(),
        url: location.href, title: document.title, ready: document.readyState, lang: lang()};
""")


def broker_state() -> str:
    """Aktiver Broker + Konto + Paper-Merkmale im Fenster (Eingabe für ``safety.classify_account``).

    → {ok, reason, connectStatus, brokerId, brokerTitle, accountMetaType, accountMetaName,
       accountMetaCurrency, accountMetaId, accountType, accountId, summary:[{text,value}],
       dom:{paperMarker, tables, accountName}, errors, lang}
    """
    return _snippet("broker_state", r"""
const out = {ok: false, reason: '', lang: lang(), brokerId: '', brokerTitle: '', summary: [], errors: []};
const tables = q(A.tables).map((e) => e.getAttribute('data-name') || '').filter(Boolean);
out.dom = {paperMarker: q(A.paper).length > 0, tables: [...new Set(tables)],
           accountName: txt(q(A.accountName).filter((e) => vis(e))[0]).slice(0, 120)};
const tr = await trading();
if (tr.error) { out.reason = tr.error; return out; }
out.connectStatus = num(tr.cs);
const b = tr.b;
if (!b) { out.ok = true; out.reason = 'kein aktiver Broker'; return out; }
const meta = await call(b, 'metainfo', [], 3000);
const am = await call(b, 'accountMetainfo', [], 3000);
const at = await call(b, 'currentAccountType', [], 3000);
const ca = await call(b, 'currentAccount', [], 3000);
const ami = await call(b, 'accountManagerInfo', [], 3000);
const m = meta.value || {}, a = am.value || {};
// Kontotyp: Text ('demo'); falls Zahl (ConnectionType: Demo=1, Real=0 – UNVERIFIZIERT) übersetzen
const typ = (v) => typeof v === 'string' ? v : (v === 1 ? 'demo' : (v === 0 ? 'real' : (v == null ? null : String(v))));
out.brokerId = m.id == null ? '' : String(m.id);
out.brokerTitle = typeof m.title === 'string' ? m.title : '';
out.accountMetaType = typ(a.type);
out.accountMetaName = typeof a.name === 'string' ? a.name : '';
out.accountMetaCurrency = typeof a.currency === 'string' ? a.currency : '';
out.accountMetaId = a.id == null ? '' : String(a.id);
out.accountType = typ(at.value);
out.accountId = (typeof ca.value === 'string' || typeof ca.value === 'number') ? String(ca.value) : '';
const sum = (ami.value && ami.value.summary) || [];
out.summary = (Array.isArray(sum) ? sum : []).slice(0, 16).map((s) => {
  const v = wv(s && (s.wValue !== undefined ? s.wValue : s.value));
  return {text: String((s && (s.text || s.title)) || ''), value: (typeof v === 'number' || typeof v === 'string') ? v : null};
});
out.errors = [meta, am, at, ca, ami].filter((r) => r.error).map((r) => r.error);
out.ok = !!meta.value;
if (!out.ok) out.reason = meta.error || 'metainfo fehlt';
return out;
""", {"paper": list(labels.PAPER_MARKERS), "tables": list(labels.POSITION_TABLES + labels.ORDER_TABLES),
      "accountName": list(labels.ACCOUNT_NAME)})


def positions_orders() -> str:
    """Positionen und Orders über die Broker-API → {ok, reason, brokerId, positions:[…], orders:[…]}"""
    return _snippet("positions_orders", r"""
const tr = await trading();
if (tr.error || !tr.b) return {ok: false, reason: tr.error || 'kein aktiver Broker'};
const b = tr.b;
const meta = await call(b, 'metainfo', [], 3000);
const p = await call(b, 'positions', [], 6000);
const o = await call(b, 'orders', [], 6000);
if (!Array.isArray(p.value) || !Array.isArray(o.value)) {
  return {ok: false, reason: p.error || o.error || 'positions()/orders() nicht verfügbar'};
}
const sid = (x) => (x == null ? '' : String(x));
const msg = (x) => { const m = x && x.message; return m ? String(m.text || m).slice(0, 200) : ''; };
const positions = p.value.map((x) => ({id: sid(x.id), symbol: sid(x.symbol), side: num(x.side), qty: num(x.qty),
  avgPrice: num(x.avgPrice ?? x.price), takeProfit: num(x.takeProfit), stopLoss: num(x.stopLoss)})).filter((x) => x.qty);
const orders = o.value.slice(-300).map((x) => ({id: sid(x.id), symbol: sid(x.symbol), side: num(x.side),
  type: num(x.type), status: num(x.status), qty: num(x.qty), filledQty: num(x.filledQty), avgPrice: num(x.avgPrice),
  limitPrice: num(x.limitPrice), stopPrice: num(x.stopPrice), takeProfit: num(x.takeProfit), stopLoss: num(x.stopLoss),
  parentId: sid(x.parentId), message: msg(x)}));
return {ok: true, brokerId: sid(meta.value && meta.value.id), positions, orders};
""")


def quote(symbol: str) -> str:
    """Kurs über ``quotesSnapshot`` → {ok, bid, ask, last, tradable, delayed}"""
    return _snippet("quote", r"""
const tr = await trading();
if (tr.error || !tr.b) return {ok: false, reason: tr.error || 'kein aktiver Broker'};
const r = await call(tr.b, 'quotesSnapshot', [A.symbol], 4000);
const v = r.value;
if (!v || typeof v !== 'object') return {ok: false, reason: r.error || 'quotesSnapshot nicht verfügbar'};
return {ok: true, bid: num(v.bid), ask: num(v.ask), last: num(v.trade ?? v.last ?? v.lp),
        tradable: v.is_tradable ?? v.isTradable ?? null, delayed: v.isDelayed ?? null};
""", {"symbol": symbol})


def symbol_info(symbol: str) -> str:
    """Mengen-/Preisraster über ``symbolInfo`` → {ok, minTick, qtyMin, qtyMax, qtyStep, currency}"""
    return _snippet("symbol_info", r"""
const tr = await trading();
if (tr.error || !tr.b) return {ok: false, reason: tr.error || 'kein aktiver Broker'};
const r = await call(tr.b, 'symbolInfo', [A.symbol], 4000);
const v = r.value;
if (!v || typeof v !== 'object') return {ok: false, reason: r.error || 'symbolInfo nicht verfügbar'};
const qq = v.qty || {};
return {ok: true, minTick: num(v.minTick), qtyMin: num(qq.min), qtyMax: num(qq.max), qtyStep: num(qq.step),
        currency: String(v.currency || v.currency_code || '')};
""", {"symbol": symbol})


def set_symbol(symbol: str, timeout_ms: int = 12000) -> str:
    """Chart-Symbol über ``activeChart().setSymbol`` setzen und bestätigen (3× stabil, kein Lade-Spinner).

    → {ok, symbol, header, reason}
    """
    return _snippet("set_symbol", r"""
const api = window.TradingViewApi;
let chart = null;
try { chart = api && api.activeChart && api.activeChart(); } catch (e) {}
if (!chart || typeof chart.symbol !== 'function' || typeof chart.setSymbol !== 'function') {
  return {ok: false, symbol: '', header: '', reason: 'Chart-Schnittstelle fehlt'};
}
const want = String(A.symbol).toUpperCase();
const same = (s) => {
  s = String(s || '').toUpperCase();
  if (s === want) return true;
  const a = s.split(':'), b = want.split(':');
  return a[a.length - 1] === b[b.length - 1] && (a.length === 1 || b.length === 1);
};
const cur = () => { try { return String(chart.symbol() || ''); } catch (e) { return ''; } };
if (!same(cur())) {
  try { chart.setSymbol(A.symbol, {}); } catch (e) { return {ok: false, symbol: cur(), header: '', reason: errText(e)}; }
}
let stable = 0;
const t0 = Date.now();
while (Date.now() - t0 < A.timeoutMs) {
  await sleep(200);
  const busy = q(A.loaders).some((e) => vis(e));
  if (same(cur()) && !busy) { if (++stable >= 3) break; } else { stable = 0; }
}
const header = txt(q(A.header).filter((e) => vis(e))[0]).slice(0, 80);
return {ok: stable >= 3, symbol: cur(), header, reason: stable >= 3 ? '' : 'Symbol nicht bestätigt'};
""", {"symbol": symbol, "timeoutMs": int(timeout_ms), "loaders": list(labels.LOADERS),
      "header": list(labels.SYMBOL_HEADER)})


def ticket_state() -> str:
    """Order-Ticket: offen? Text des Absende-Knopfs, Mengen-Einheit, fokussiertes Eingabefeld.

    → {open, containers, focusedEditable, submitCount, submitText, submitDisabled, unitText, text, lang}
    """
    return _snippet("ticket_state", r"""
const cs = q(A.containers).filter((e) => vis(e));
const out = {open: cs.length > 0, containers: cs.map((c) => c.getAttribute('data-name') || 'ticket'),
             focusedEditable: editableFocused(), lang: lang(), submitCount: 0, submitText: '',
             submitDisabled: null, unitText: '', text: ''};
if (!cs.length) return out;
const subs = [];
for (const c of cs) for (const e of q(A.submit, c)) if (vis(e) && !subs.includes(e)) subs.push(e);
out.submitCount = subs.length;
if (subs.length === 1) {
  out.submitText = txt(subs[0]).slice(0, 200);
  out.submitDisabled = !!(subs[0].disabled || subs[0].getAttribute('aria-disabled') === 'true');
}
const unit = q(A.unit, cs[0]).filter((e) => vis(e))[0];
out.unitText = unit ? txt(unit).slice(0, 40) : '';
out.text = txt(cs[0]).slice(0, 800);
return out;
""", {"containers": list(labels.TICKET_CONTAINERS), "submit": list(labels.SUBMIT_BUTTON),
      "unit": list(labels.QUANTITY_UNIT_BUTTON)})


def dialogs() -> str:
    """Sichtbare Dialoge außerhalb des Order-Tickets mit Text und Knöpfen → [{name, text, buttons:[…]}]"""
    return _snippet("dialogs", r"""
const ex = q(A.exclude);
const ds = q(A.sels).filter((d) => vis(d) && !ex.some((x) => x === d || x.contains(d) || d.contains(x)));
const outer = ds.filter((d) => !ds.some((o) => o !== d && o.contains(d)));
const forbid = A.forbid.join(',');
return outer.slice(0, 5).map((d) => ({
  name: d.getAttribute('data-name') || d.getAttribute('data-dialog-name') || d.getAttribute('role') || '',
  text: txt(d).slice(0, 800),
  buttons: q(A.buttons, d).filter((b) => vis(b)).slice(0, 12).map((b) => {
    const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const top = document.elementFromPoint(x, y);
    return {text: txt(b).slice(0, 80), x, y, dataName: b.getAttribute('data-name') || '',
            disabled: !!(b.disabled || b.getAttribute('aria-disabled') === 'true'),
            covered: !(top && (top === b || b.contains(top))), forbidden: !!(forbid && b.closest(forbid))};
  }),
}));
""", {"sels": list(labels.DIALOGS), "exclude": list(labels.TICKET_CONTAINERS),
      "buttons": list(labels.DIALOG_BUTTONS), "forbid": list(labels.FORBIDDEN_CLICK)})


def position_row(ticker: str) -> str:
    """Zeile der Positionstabelle zu einem Ticker + ggf. sichtbarer Schließen-Knopf.

    → {tables, count, row:{x,y,text}, close:{x,y,covered,disabled,forbidden}|null}
    """
    return _snippet("position_row", r"""
const tables = q(A.tables).filter((e) => vis(e));
const esc = String(A.ticker).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const re = new RegExp('(^|[^A-Z0-9])' + esc + '([^A-Z0-9]|$)', 'i');
const rows = [];
for (const t of tables) for (const r of q(A.rows, t)) {
  if (!vis(r) || rows.includes(r)) continue;
  const first = r.querySelector('td');
  if (re.test(txt(first || r).split(' ')[0] || '')) rows.push(r);
}
const out = {tables: tables.map((t) => t.getAttribute('data-name') || ''), count: rows.length, row: null, close: null};
if (rows.length !== 1) return out;
const row = rows[0];
try { row.scrollIntoView({block: 'nearest'}); } catch (e) {}
const rr = row.getBoundingClientRect();
out.row = {x: rr.left + rr.width / 2, y: rr.top + rr.height / 2, text: txt(row).slice(0, 200)};
const btn = q(A.close, row).filter((e) => vis(e))[0];
if (btn) {
  const r = btn.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const forbid = A.forbid.join(',');
  out.close = {x, y, covered: !(top && (top === btn || btn.contains(top))),
               disabled: !!(btn.disabled || btn.getAttribute('aria-disabled') === 'true'),
               forbidden: !!(forbid && btn.closest(forbid))};
}
return out;
""", {"ticker": ticker, "tables": list(labels.POSITION_TABLES), "rows": list(labels.TABLE_ROWS),
      "close": list(labels.POSITION_CLOSE_BUTTON), "forbid": list(labels.FORBIDDEN_CLICK)})


def health() -> str:
    """Nur lesender Gesundheitscheck: welche API-Teile und Oberflächen-Elemente gibt es? → {api:{…}, dom:{…}}"""
    return _snippet("health", r"""
const out = {api: {}, dom: {}, url: location.href, lang: lang()};
const api = window.TradingViewApi;
out.api['TradingViewApi'] = !!api;
let chart = null;
try { chart = api && api.activeChart && api.activeChart(); } catch (e) {}
out.api['chart.symbol'] = !!chart && typeof chart.symbol === 'function';
out.api['chart.setSymbol'] = !!chart && typeof chart.setSymbol === 'function';
const tr = api ? await trading() : {error: 'TradingViewApi fehlt'};
out.api['trading'] = !tr.error;
out.api['activeBroker'] = !!tr.b;
for (const m of A.methods) out.api['broker.' + m] = !!tr.b && typeof tr.b[m] === 'function';
for (const [name, sels] of Object.entries(A.dom)) out.dom[name] = q(sels).filter((e) => vis(e)).length;
return out;
""", {"methods": list(labels.BROKER_METHODS), "dom": {k: list(v) for k, v in labels.HEALTH_DOM.items()}})


# --------------------------------------------------------------------------- #
# Oberfläche bedienen (Klicks selbst laufen als echte CDP-Mausereignisse)
# --------------------------------------------------------------------------- #
def find(sels: Sequence[str], *, text: str | None = None, label: str | None = None,
         scope: Sequence[str] | None = None, exclude: Sequence[str] | None = None, loose: bool = False) -> str:
    """Genau ein sichtbares Element suchen → {count, x, y, covered, forbidden, disabled, text, value, on, …}

    ``count != 1`` heißt: nicht eindeutig → nicht klicken.
    """
    return _snippet("find", r"""
const S = A;
const L = locate(S);
if (L.els.length !== 1) return {count: L.els.length, scope: L.scope, via: L.via,
                                texts: L.els.slice(0, 5).map((e) => txt(e).slice(0, 60))};
return Object.assign(describe(L.els[0], S), {via: L.via});
""", _spec(sels, text=text, label=label, scope=scope, exclude=exclude, loose=loose))


def set_input(sels: Sequence[str], value: str, *, label: str | None = None, scope: Sequence[str] | None = None,
              exclude: Sequence[str] | None = None) -> str:
    """Wert in genau ein Eingabefeld schreiben (nativer Setter + input/change, dann blur) → {ok, count, value, reason}"""
    args = _spec(sels, label=label, scope=scope, exclude=exclude, inputs=True)
    args["value"] = value
    return _snippet("set_input", r"""
const S = A;
const L = locate(S);
if (L.els.length !== 1) return {ok: false, count: L.els.length, reason: 'Feld nicht eindeutig gefunden'};
const el = L.els[0];
if (!/^(INPUT|TEXTAREA)$/.test(el.tagName)) return {ok: false, count: 1, reason: 'kein Eingabefeld'};
if (el.disabled || el.readOnly) return {ok: false, count: 1, reason: 'Feld ist gesperrt'};
el.focus();
try { el.select(); } catch (e) {}
const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(S.value));
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
el.blur();
await sleep(120);
return {ok: true, count: 1, value: el.value};
""", args)


def focus_input(sels: Sequence[str], *, label: str | None = None, scope: Sequence[str] | None = None,
                exclude: Sequence[str] | None = None) -> str:
    """Eingabefeld fokussieren und Inhalt markieren (danach ``Input.insertText``) → {ok, count}"""
    return _snippet("focus_input", r"""
const L = locate(A);
if (L.els.length !== 1) return {ok: false, count: L.els.length};
const el = L.els[0];
if (el.disabled || el.readOnly || !/^(INPUT|TEXTAREA)$/.test(el.tagName)) return {ok: false, count: 1};
el.focus();
try { el.select(); } catch (e) {}
return {ok: document.activeElement === el, count: 1};
""", _spec(sels, label=label, scope=scope, exclude=exclude, inputs=True))


def blur_active() -> str:
    """Fokus vom aktiven Feld nehmen (TradingView übernimmt den Wert) → {ok, value}"""
    return _snippet("blur_active", r"""
const el = document.activeElement;
if (!el || el === document.body) return {ok: false, value: null};
el.blur();
await sleep(120);
return {ok: true, value: (typeof el.value === 'string') ? el.value : null};
""")


# --------------------------------------------------------------------------- #
# Broker-API (Vorschau, Notfall-Weg)
# --------------------------------------------------------------------------- #
def api_order(symbol: str, side: str, qty: float, order_type: str, limit_price: float | None,
              take_profit: float | None, stop_loss: float | None) -> dict[str, Any]:
    """Order-Objekt im Format der TradingView-Broker-API (Enums aus broker-api.d.ts)."""
    order: dict[str, Any] = {
        "symbol": symbol,
        "side": labels.SIDE_BUY if side == "buy" else labels.SIDE_SELL,
        "type": labels.ORDER_TYPE[order_type],
        "qty": qty,
    }
    if order_type == "limit" and limit_price:
        order["limitPrice"] = limit_price
    if take_profit:
        order["takeProfit"] = take_profit
    if stop_loss:
        order["stopLoss"] = stop_loss
    return order


def preview_order(order: Mapping[str, Any]) -> str:
    """Trockenlauf ``previewOrder`` (sendet nichts) → {available, ok, error, info}"""
    return _snippet("preview_order", r"""
const tr = await trading();
if (tr.error || !tr.b) return {available: false, ok: false, error: tr.error || 'kein aktiver Broker'};
const r = await call(tr.b, 'previewOrder', [A.order], 6000);
if (r.missing) return {available: false, ok: true, error: ''};
let info = '';
try { info = JSON.stringify(r.value).slice(0, 600); } catch (e) { info = ''; }
return {available: true, ok: !r.error, error: r.error || '', info};
""", {"order": dict(order)})


def place_order_api(order: Mapping[str, Any]) -> str:
    """Notfall-Weg: ``placeOrder`` über die Broker-API (nur mit Einstellung api_fallback) → {ok, id, error}"""
    return _snippet("place_order_api", r"""
const tr = await trading();
if (tr.error || !tr.b) return {ok: false, id: '', error: tr.error || 'kein aktiver Broker'};
const r = await call(tr.b, 'placeOrder', [A.order], 10000);
if (r.missing) return {ok: false, id: '', error: 'placeOrder nicht verfügbar'};
const v = r.value || {};
return {ok: !r.error, id: String(v.orderId || v.id || ''), error: r.error || ''};
""", {"order": dict(order)})


def close_position_api(position_id: str) -> str:
    """Notfall-Weg: ``closePosition`` über die Broker-API → {ok, error}"""
    return _snippet("close_position_api", r"""
const tr = await trading();
if (tr.error || !tr.b) return {ok: false, error: tr.error || 'kein aktiver Broker'};
const r = await call(tr.b, 'closePosition', [A.id], 10000);
if (r.missing) return {ok: false, error: 'closePosition nicht verfügbar'};
return {ok: !r.error, error: r.error || ''};
""", {"id": position_id})
