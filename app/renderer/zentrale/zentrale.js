// Zentrale – Hauptfenster von XRZ Vincent: Navigation, Kopfleiste (Modus, Autonomie, Konto, Verbindung),
// Live-Ereignisse vom Backend, Toasts und Verbindungs-Overlay.

import * as api from '../shared/api.js';
import { h, icon, replaceChildren, segmented, button } from './views/dom.js';
import { createUI } from './views/ui.js';
import {
  MODES, AUTONOMY, modeInfo, accountInfo, normalizeAccent, hexToRgb, unwrapEvent, notifyTone, sideInfo, fmtQty,
} from './views/format.js';
import { createOverview } from './views/overview.js';
import { createMarket } from './views/market.js';
import { createStrategies } from './views/strategies.js';
import { createMemory } from './views/memory.js';
import { createAppearance } from './views/appearance.js';
import { createTradingView } from './views/tradingview.js';
import { createSettings } from './views/settings.js';
import { createProtocol } from './views/protocol.js';

const VIEWS = [
  { id: 'uebersicht', label: 'Übersicht', icon: 'grid', create: createOverview },
  { id: 'markt', label: 'Markt', icon: 'candles', create: createMarket },
  { id: 'strategien', label: 'Strategien', icon: 'layers', create: createStrategies },
  { id: 'gedaechtnis', label: 'Gedächtnis', icon: 'brain', create: createMemory },
  { id: 'stimme', label: 'Stimme & Aussehen', icon: 'mic', create: createAppearance },
  { id: 'tradingview', label: 'TradingView', icon: 'monitor', create: createTradingView },
  { id: 'einstellungen', label: 'Einstellungen', icon: 'sliders', create: createSettings },
  { id: 'protokoll', label: 'Protokoll', icon: 'list', create: createProtocol },
];

const NOTIFY_TITLES = { trade: 'Trade', warning: 'Warnung', error: 'Fehler', mode: 'Modus', report: 'Bericht', info: '' };
const isMac = /Mac/i.test(globalThis.navigator?.platform || '') || globalThis.window?.vincent?.platform === 'darwin';

const $ = (sel) => document.querySelector(sel);
const bridge = globalThis.window?.vincent || null;

const ui = createUI({ toastRoot: $('#toasts'), modalRoot: $('#modal-root') });
let state = null;
let connected = false;
let currentView = null;
const instances = new Map();

// --------------------------------------------------------------------------
// Kontext für die Ansichten
// --------------------------------------------------------------------------

let refreshTimer = null;
const ctx = {
  api,
  ui,
  getState: () => state,
  patchState(partial) {
    state = { ...(state || {}), ...partial };
    broadcastState();
  },
  refreshState() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      try {
        applyState(await api.get('/api/state'));
      } catch {
        /* Verbindungsfehler zeigt das Overlay */
      }
    }, 120);
  },
  navigate(id, params) {
    showView(id, params);
  },
  setAccent,
};

// --------------------------------------------------------------------------
// Seitenleiste
// --------------------------------------------------------------------------

const navButtons = new Map();
const versionEl = h('span', { class: 'version', text: '' });

function buildSidebar() {
  const nav = h('nav', { class: 'nav', 'aria-label': 'Bereiche' });
  const list = h('ul', { class: 'nav-list' });
  VIEWS.forEach((v, i) => {
    const badge = h('span', { class: 'nav-badge', hidden: true });
    const btn = h('button', {
      type: 'button', class: 'nav-item', dataset: { view: v.id }, title: `${v.label} (${isMac ? '⌘' : 'Strg+'}${i + 1})`,
      onClick: () => showView(v.id),
    }, icon(v.icon, 18), h('span', { class: 'nav-label', text: v.label }), badge);
    navButtons.set(v.id, { btn, badge });
    list.append(h('li', null, btn));
  });
  list.addEventListener('keydown', (ev) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(ev.key)) return;
    const items = [...list.querySelectorAll('.nav-item')];
    const idx = items.indexOf(document.activeElement);
    if (idx < 0) return;
    ev.preventDefault();
    const next = ev.key === 'Home' ? 0 : ev.key === 'End' ? items.length - 1
      : (idx + (ev.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  });
  nav.append(list);

  const panicBtn = button('Notstopp', { variant: 'danger-ghost', icon: 'power', title: 'Alle Positionen schließen und Modus auf AUS', onClick: () => panic(panicBtn) });

  replaceChildren($('#sidebar'),
    h('div', { class: 'brand' },
      h('div', { class: 'brand-orb', 'aria-hidden': 'true' }),
      h('div', { class: 'brand-text' },
        h('div', { class: 'brand-name' }, h('span', { class: 'brand-xrz', text: 'XRZ' }), ' Vincent'),
        h('div', { class: 'brand-sub', text: 'Kontrollzentrum' }))),
    nav,
    h('div', { class: 'sidebar-foot' }, panicBtn, versionEl));
}

async function panic(btn) {
  const ok = await ui.confirm({
    title: 'Notstopp auslösen?',
    tone: 'bad',
    icon: 'power',
    text: 'Vincent schließt alle offenen Positionen in TradingView und schaltet den Modus auf AUS.',
    confirmLabel: 'Notstopp',
  });
  if (!ok) return;
  const res = await ui.run(btn, () => api.post('/api/panic', {}));
  if (res) ui.toast(res.text || 'Notstopp ausgeführt.', 'warn', { title: 'Notstopp' });
  ctx.refreshState();
}

// --------------------------------------------------------------------------
// Kopfleiste
// --------------------------------------------------------------------------

const modeCtl = segmented(MODES.map((m) => ({ value: m.id, label: m.label, tone: m.tone, title: m.info })), {
  label: 'Handelsmodus', onSelect: (mode, btn) => requestMode(mode, btn),
});
const autonomyBtn = h('button', { type: 'button', class: 'autonomy', role: 'switch', 'aria-checked': 'false' },
  h('span', { class: 'mini-switch', 'aria-hidden': 'true' }, h('span')),
  h('span', { class: 'autonomy-text' },
    h('span', { class: 'autonomy-k', text: 'Autonomie' }),
    h('span', { class: 'autonomy-v', text: AUTONOMY.ask.label })));
autonomyBtn.addEventListener('click', () => toggleAutonomy());
const tvBadge = h('button', { type: 'button', class: 'tv-badge tone-neutral', onClick: () => showView('tradingview') },
  icon('monitor', 16), h('span', { class: 'tv-badge-text' },
    h('span', { class: 'tv-badge-k', text: 'TradingView' }),
    h('span', { class: 'tv-badge-v', text: 'nicht verbunden' })));
const connEl = h('div', { class: 'conn', role: 'status', 'aria-live': 'polite' },
  h('span', { class: 'conn-dot' }), h('span', { class: 'conn-label', text: 'Getrennt' }));

function buildTopbar() {
  replaceChildren($('#topbar'),
    h('div', { class: 'tb-mode' }, h('span', { class: 'tb-k', text: 'Modus' }), modeCtl.el),
    autonomyBtn,
    h('div', { class: 'spacer' }),
    tvBadge,
    connEl);
}

async function requestMode(mode, btn) {
  if (!state || mode === state.mode) return;
  if (mode !== 'live') {
    const res = await ui.run(btn, () => api.post('/api/mode', { mode }));
    if (res) ui.toast(res.text || `Modus: ${modeInfo(mode).name}`, mode === 'off' ? 'info' : 'accent', { title: `Modus ${modeInfo(mode).name}` });
    ctx.refreshState();
    return;
  }
  const res = await ui.run(btn, () => api.post('/api/mode', { mode: 'live' }));
  if (!res) return;
  if (!res.needs_confirmation) {
    await ui.info('Echtgeld-Modus', res.text || 'Der Echtgeld-Modus ist gerade nicht möglich.', 'warn');
    ctx.refreshState();
    return;
  }
  const code = res.code != null ? String(res.code) : '';
  const typed = await ui.prompt({
    title: 'Echtgeld-Modus aktivieren',
    tone: 'bad',
    icon: 'alert',
    text: res.text || '',
    body: code ? h('div', { class: 'code-box' }, h('span', { class: 'code-k', text: 'Bestätigungscode' }), h('span', { class: 'code-v mono', text: code })) : null,
    input: { label: 'Code abtippen', placeholder: code ? 'Code' : 'Bestätigungscode', expected: code },
    confirmLabel: 'LIVE aktivieren',
  });
  if (typed === null) return;
  const done = await ui.run(btn, () => api.post('/api/mode/confirm', { code: typed }));
  if (done) ui.toast(done.text || 'Echtgeld-Modus aktiv.', 'warn', { title: 'Modus Echtgeld' });
  ctx.refreshState();
}

async function toggleAutonomy() {
  if (!state) return;
  const level = state.autonomy === 'auto' ? 'ask' : 'auto';
  const res = await ui.run(autonomyBtn, () => api.post('/api/autonomy', { level }));
  if (res) ui.toast(res.text || AUTONOMY[level].label, 'info', { title: 'Autonomie' });
  ctx.refreshState();
}

function renderChrome() {
  const s = state || {};
  modeCtl.set(s.mode || '');
  document.body.dataset.mode = s.mode || '';

  const auto = s.autonomy === 'auto';
  const canAuto = s.mode === 'paper' || auto;
  autonomyBtn.setAttribute('aria-checked', String(auto));
  autonomyBtn.disabled = !connected || !canAuto;
  autonomyBtn.title = canAuto ? (AUTONOMY[s.autonomy] || AUTONOMY.ask).info : 'Selbstständiges Handeln gibt es nur im Paper-Modus.';
  autonomyBtn.querySelector('.autonomy-v').textContent = (AUTONOMY[s.autonomy] || AUTONOMY.ask).label;

  const acc = accountInfo(s.tradingview);
  tvBadge.className = `tv-badge tone-${acc.tone}`;
  tvBadge.querySelector('.tv-badge-v').textContent = acc.kind === 'none' ? 'nicht verbunden' : acc.short;
  tvBadge.title = acc.text;

  const pending = (s.proposals || []).filter((p) => (p.status || 'pending') === 'pending').length;
  const nb = navButtons.get('uebersicht');
  if (nb) {
    nb.badge.hidden = pending === 0;
    nb.badge.textContent = String(pending);
  }
  if (s.version) versionEl.textContent = `Version ${s.version}`;
  if (s.skin?.accent) setAccent(s.skin.accent);
}

function setAccent(color) {
  const c = normalizeAccent(color);
  const root = document.documentElement;
  if (root.style.getPropertyValue('--accent') === c) return;
  root.style.setProperty('--accent', c);
  root.style.setProperty('--accent-rgb', hexToRgb(c));
}

// --------------------------------------------------------------------------
// Verbindung
// --------------------------------------------------------------------------

const offline = $('#offline');
const offlineMsg = $('#offline-msg');
let offlineTimer = null;

function setConnected(on) {
  connected = on;
  connEl.classList.toggle('is-on', on);
  connEl.querySelector('.conn-label').textContent = on ? 'Verbunden' : 'Getrennt';
  connEl.title = on ? 'Live-Verbindung zum Vincent-Backend steht.' : 'Keine Verbindung zum Vincent-Backend.';
  clearTimeout(offlineTimer);
  if (on) {
    offline.classList.remove('is-visible');
    offline.setAttribute('aria-hidden', 'true');
    document.querySelector('.app')?.removeAttribute('inert');
    ctx.refreshState();
  } else {
    // Kurze Aussetzer (Reconnect) nicht sofort mit dem Overlay quittieren.
    offlineTimer = setTimeout(() => {
      offline.classList.add('is-visible');
      offline.removeAttribute('aria-hidden');
      document.querySelector('.app')?.setAttribute('inert', '');
    }, state ? 1500 : 0);
  }
  renderChrome();
}

function setOfflineMessage(status) {
  let text = '';
  let bad = false;
  if (typeof status === 'string') text = status;
  else if (status && typeof status === 'object') {
    text = String(status.message || status.text || status.detail || '');
    bad = status.ok === false || ['error', 'crashed', 'failed', 'stopped'].includes(String(status.state || status.status || ''));
    if (!text && status.state) text = String(status.state);
  }
  if (text) offlineMsg.textContent = text;
  offline.classList.toggle('is-error', bad);
}

// --------------------------------------------------------------------------
// Zustand & Ereignisse
// --------------------------------------------------------------------------

function applyState(s) {
  if (!s || typeof s !== 'object') return;
  state = s;
  if (!connected) setConnected(true);
  broadcastState();
}

function broadcastState() {
  renderChrome();
  for (const inst of instances.values()) {
    try {
      inst.update?.(state);
    } catch (err) {
      console.error('Ansicht konnte nicht aktualisiert werden', err);
    }
  }
}

function forward(type, data) {
  for (const inst of instances.values()) {
    try {
      inst.event?.(type, data);
    } catch (err) {
      console.error('Ereignis konnte nicht verarbeitet werden', err);
    }
  }
}

function upsert(list, item, key) {
  const arr = Array.isArray(list) ? [...list] : [];
  const i = arr.findIndex((x) => x?.[key] === item[key]);
  if (i >= 0) arr[i] = { ...arr[i], ...item };
  else arr.unshift(item);
  return arr;
}

function onProposal(data) {
  const p = data?.proposal || data;
  if (!p || p.id === undefined) return;
  const isNew = !(state?.proposals || []).some((x) => x.id === p.id);
  ctx.patchState({ proposals: upsert(state?.proposals, p, 'id') });
  if (isNew && (p.status || 'pending') === 'pending') {
    const side = sideInfo(p.side).label.toUpperCase();
    ui.toast(`${side} ${fmtQty(p.qty)} ${p.symbol} – bitte in der Übersicht prüfen.`, p.account_kind === 'live' ? 'bad' : 'accent',
      { title: p.account_kind === 'live' ? 'Neuer ECHTGELD-Vorschlag' : 'Neuer Trade-Vorschlag', timeout: 9000 });
  }
}

function wireEvents() {
  api.on('connection', (d) => {
    const v = unwrapEvent(d);
    setConnected(typeof v === 'boolean' ? v : Boolean(v?.connected));
  });
  api.on('state', (d) => applyState(unwrapEvent(d)));
  api.on('proposal', (d) => {
    const v = unwrapEvent(d);
    onProposal(v);
    forward('proposal', v);
  });
  api.on('notify', (d) => {
    const v = unwrapEvent(d) || {};
    ui.toast(String(v.text || ''), notifyTone(v.kind), { title: NOTIFY_TITLES[v.kind] || '' });
    forward('notify', v);
  });
  api.on('analysis', (d) => {
    const v = unwrapEvent(d);
    const summary = v?.summary || v;
    if (summary?.symbol) ctx.patchState({ analyses: upsert(state?.analyses, summary, 'symbol') });
    forward('analysis', v);
  });
  api.on('tradingview', (d) => {
    const v = unwrapEvent(d);
    const status = v?.status || v;
    if (status && typeof status === 'object') ctx.patchState({ tradingview: { ...(state?.tradingview || {}), ...status } });
    forward('tradingview', { status });
  });
  api.on('chat', (d) => forward('chat', unwrapEvent(d)));
  api.on('voice.state', (d) => {
    const v = unwrapEvent(d);
    if (v?.state && state) ctx.patchState({ voice: { ...(state.voice || {}), state: v.state } });
    forward('voice.state', v);
  });
}

// --------------------------------------------------------------------------
// Ansichten
// --------------------------------------------------------------------------

function viewFromHash() {
  const id = decodeURIComponent((location.hash || '').replace(/^#\/?/, '').split('?')[0]);
  return VIEWS.some((v) => v.id === id) ? id : 'uebersicht';
}

function getInstance(id) {
  if (!instances.has(id)) {
    const def = VIEWS.find((v) => v.id === id);
    const inst = def.create(ctx);
    inst.el.hidden = true;
    inst.el.id = `view-${id}`;
    $('#content').append(inst.el);
    instances.set(id, inst);
    if (state) inst.update?.(state);
  }
  return instances.get(id);
}

function showView(id, params) {
  const def = VIEWS.find((v) => v.id === id) || VIEWS[0];
  if (currentView !== def.id) {
    if (currentView) {
      const prev = instances.get(currentView);
      prev?.hide?.();
      if (prev) prev.el.hidden = true;
    }
    currentView = def.id;
    if (location.hash !== `#/${def.id}`) history.replaceState(null, '', `#/${def.id}`);
    for (const [vid, { btn }] of navButtons) {
      if (vid === def.id) btn.setAttribute('aria-current', 'page');
      else btn.removeAttribute('aria-current');
    }
    document.title = `${def.label} · XRZ Vincent`;
    $('#content').scrollTop = 0;
  }
  const inst = getInstance(def.id);
  inst.el.hidden = false;
  inst.el.classList.remove('is-entering');
  void inst.el.offsetWidth; // Animation neu starten
  inst.el.classList.add('is-entering');
  try {
    inst.show?.(params);
  } catch (err) {
    console.error('Ansicht konnte nicht geöffnet werden', err);
  }
}

function wireKeyboard() {
  document.addEventListener('keydown', (ev) => {
    const mod = isMac ? ev.metaKey : ev.ctrlKey;
    if (mod && !ev.altKey && !ev.shiftKey && /^[1-8]$/.test(ev.key)) {
      ev.preventDefault();
      showView(VIEWS[Number(ev.key) - 1].id);
      navButtons.get(currentView)?.btn.focus();
      return;
    }
    const t = ev.target;
    const typing = t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
    if (ev.key === '/' && !typing && !mod) {
      const search = instances.get(currentView)?.el.querySelector('[data-search]');
      if (search) {
        ev.preventDefault();
        search.focus();
      }
    }
  });
  window.addEventListener('hashchange', () => showView(viewFromHash()));
}

// --------------------------------------------------------------------------
// Start
// --------------------------------------------------------------------------

async function start() {
  buildSidebar();
  buildTopbar();
  wireKeyboard();
  wireEvents();
  if (bridge?.onBackendStatus) {
    try {
      bridge.onBackendStatus(setOfflineMessage);
    } catch (err) {
      console.error('Backend-Status nicht verfügbar', err);
    }
  }
  getInstance('uebersicht');
  showView(viewFromHash());
  renderChrome();

  let delay = 1000;
  for (;;) {
    try {
      await api.connect();
      ctx.refreshState();
      return;
    } catch (err) {
      setOfflineMessage(err?.message ? `Noch keine Verbindung: ${err.message}` : 'Noch keine Verbindung zum Backend …');
      await new Promise((r) => setTimeout(r, delay));
      delay = Math.min(10000, delay * 1.6);
    }
  }
}

start();
