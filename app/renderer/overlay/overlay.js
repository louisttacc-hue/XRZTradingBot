/**
 * Vincent – Overlay am Bildschirmrand: animierter Avatar, Untertitel-Blase, Schnellaktionen,
 * Chat-Panel mit Handelsvorschlägen und Toasts.
 *
 * Oben stehen reine Hilfsfunktionen (ohne DOM, in Node testbar), darunter der DOM-Controller,
 * der nur im Browser startet. Alle Texte vom Backend (auch LLM-Ausgaben) gelten als nicht
 * vertrauenswürdig und landen ausschließlich per textContent im DOM.
 */
import * as api from '../shared/api.js';
import { Avatar } from './avatar.js';
import { DEFAULT_ACCENT, clamp, hexToRgb, isHexColor, normalizeSkinId, rgbToHex, shiftHue } from './skins.js';

// ================================================================ Reine Logik

/** Sprachzustände laut Backend. */
export const VOICE_STATES = Object.freeze(['idle', 'listening', 'thinking', 'speaking', 'muted', 'off', 'error']);
export const TOAST_KINDS = Object.freeze(['trade', 'warning', 'error', 'mode', 'report', 'info']);
export const MAX_MESSAGES = 100;
/** Dauer, in der ein optimistisch gesetzter Avatar-Zustand gilt, bis das Backend ihn bestätigt. */
export const OVERRIDE_MS = 2500;

const STATUS_LABELS = Object.freeze({
  idle: 'Bereit',
  listening: 'Hört zu …',
  thinking: 'Denkt nach …',
  speaking: 'Spricht …',
  muted: 'Mikrofon stumm',
  off: 'Sprache aus',
  error: 'Sprachfehler',
  disconnected: 'Nicht verbunden',
});

const TOAST_META = Object.freeze({
  trade: { icon: 'i-trade', title: 'Trade', tone: 'green' },
  warning: { icon: 'i-warning', title: 'Warnung', tone: 'amber' },
  error: { icon: 'i-error', title: 'Fehler', tone: 'red' },
  mode: { icon: 'i-mode', title: 'Modus', tone: 'blue' },
  report: { icon: 'i-report', title: 'Bericht', tone: 'violet' },
  info: { icon: 'i-info', title: 'Info', tone: 'neutral' },
});

const PROPOSAL_STATUS = Object.freeze({
  pending: 'Wartet auf Freigabe',
  approved: 'Freigegeben',
  rejected: 'Abgelehnt',
  executing: 'Wird ausgeführt …',
  done: 'Ausgeführt',
  failed: 'Fehlgeschlagen',
  expired: 'Abgelaufen',
});

/**
 * Visueller Avatar-Zustand aus Verbindung, Sprachzustand, Stummschaltung und optimistischem Override.
 * @param {{connected?: boolean, voiceState?: string, muted?: boolean,
 *          override?: {state: string, until: number}|null, now?: number}} o
 * @returns {string} einer von STATE_KEYS (skins.js)
 */
export function visualState({ connected, voiceState, muted, override, now = Date.now() } = {}) {
  if (!connected) return 'disconnected';
  let s = override && override.until > now ? override.state : voiceState;
  if (!VOICE_STATES.includes(s)) s = 'idle';
  if (s === 'idle' && muted) s = 'muted';
  return s;
}

/** @param {string} vstate @returns {string} deutscher Statustext */
export function statusLabel(vstate) {
  return STATUS_LABELS[vstate] || STATUS_LABELS.idle;
}

/**
 * Modus-Abzeichen am Avatar und Modus-Chip im Chat.
 * @param {string} mode off|watch|paper|live
 */
export function modeBadge(mode) {
  switch (mode) {
    case 'paper':
      return { label: 'PAPER', tone: 'blue', pulse: false, chip: 'Paper-Modus', title: 'Modus: Paper Trading (Spielgeld)' };
    case 'live':
      return { label: 'LIVE', tone: 'red', pulse: true, chip: 'LIVE · Echtgeld', title: 'Modus: Live – es wird mit echtem Geld gehandelt' };
    case 'watch':
      return { label: 'BEOBACHTEN', tone: 'amber', pulse: false, chip: 'Beobachten', title: 'Modus: Beobachten – keine Orders' };
    default:
      return { label: 'AUS', tone: 'grey', pulse: false, chip: 'Trading aus', title: 'Modus: Aus – kein Trading' };
  }
}

/**
 * TradingView-Konto-Chip (state.tradingview).
 * @param {{connected?: boolean, account?: {kind?: string, broker?: string, label?: string}, message?: string}|null} tv
 */
export function accountChip(tv) {
  const t = tv && typeof tv === 'object' ? tv : {};
  const acc = t.account && typeof t.account === 'object' ? t.account : {};
  const connected = t.connected !== false;
  const detail = [acc.broker, acc.label].filter((x) => typeof x === 'string' && x.trim()).join(' · ');
  if (connected && acc.kind === 'paper') {
    return { label: 'Paper Trading ✓', tone: 'blue', title: `TradingView: Paper-Trading-Konto aktiv${detail ? ` (${detail})` : ''}` };
  }
  if (connected && acc.kind === 'live') {
    return { label: 'ECHTGELD', tone: 'red', title: `TradingView: echtes Broker-Konto aktiv${detail ? ` (${detail})` : ''}` };
  }
  const msg = typeof t.message === 'string' && t.message.trim() ? t.message.trim() : 'TradingView ist nicht verbunden.';
  return { label: 'TradingView nicht verbunden', tone: 'grey', title: msg };
}

/**
 * Zeitstempel (Unix-Sekunden, Millisekunden, ISO-Text, Date) → Millisekunden oder null.
 * @param {unknown} x
 * @returns {number|null}
 */
export function parseTs(x) {
  if (x == null || x === '') return null;
  if (x instanceof Date) return Number.isFinite(x.getTime()) ? x.getTime() : null;
  if (typeof x === 'number') return Number.isFinite(x) ? (Math.abs(x) < 1e12 ? x * 1000 : x) : null;
  if (typeof x === 'string') {
    const s = x.trim();
    if (/^-?\d+(\.\d+)?$/.test(s)) return parseTs(Number(s));
    const ms = Date.parse(s);
    return Number.isFinite(ms) ? ms : null;
  }
  return null;
}

/**
 * Restzeit als Countdown („4:05“, „1:02:03“, „abgelaufen“).
 * @param {number|null} ms
 * @returns {string}
 */
export function formatCountdown(ms) {
  if (ms == null || !Number.isFinite(ms)) return '–';
  if (ms <= 0) return 'abgelaufen';
  const total = Math.ceil(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** @param {number} ms @returns {string} Uhrzeit „HH:MM“ */
export function formatClock(ms) {
  const d = new Date(ms);
  if (!Number.isFinite(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** Text für Vergleiche normalisieren (Leerraum zusammenfassen). */
export function normalizeText(s) {
  return String(s ?? '').replace(/\s+/g, ' ').trim();
}

/**
 * Erkennt doppelte Chatnachrichten aus zwei Quellen: lokal (optimistisch gesendet bzw. REST-Antwort)
 * und WebSocket-Echo. Jede Nachricht verbraucht höchstens einen passenden Eintrag der anderen Quelle,
 * sodass zweimal derselbe Text auch zweimal erscheint.
 */
export class MessageDeduper {
  /** @param {{user?: number, assistant?: number}} [windows] Zeitfenster je Rolle in ms */
  constructor(windows = {}) {
    this.windows = { user: windows.user ?? 5000, assistant: windows.assistant ?? 15000 };
    this.entries = [];
  }

  /**
   * @param {string} role user|assistant
   * @param {string} text
   * @param {'local'|'ws'} origin
   * @param {number} [now]
   * @returns {boolean} true = Echo einer bereits angezeigten Nachricht → ignorieren
   */
  isDuplicate(role, text, origin, now = Date.now()) {
    const win = (r) => this.windows[r] ?? 5000;
    this.entries = this.entries.filter((e) => now - e.at <= win(e.role));
    const key = normalizeText(text);
    const i = this.entries.findIndex((e) => e.role === role && e.key === key && e.origin !== origin);
    if (i >= 0) {
      this.entries.splice(i, 1);
      return true;
    }
    this.entries.push({ role, key, origin, at: now });
    return false;
  }
}

/**
 * Ignoriert das Echo eigener toggleChat()-Aufrufe (der Hauptprozess schickt sie als chat:toggle zurück).
 */
export class EchoFilter {
  constructor(windowMs = 1500) {
    this.windowMs = windowMs;
    this.pending = [];
  }

  /** @param {unknown} value erwarteter Echo-Wert @param {number} [now] */
  expect(value, now = Date.now()) {
    this.pending.push({ value, at: now });
  }

  /** @returns {boolean} true, wenn `value` das erwartete Echo ist (wird verbraucht) */
  consume(value, now = Date.now()) {
    this.pending = this.pending.filter((p) => now - p.at <= this.windowMs);
    if (this.pending.length && this.pending[0].value === value) {
      this.pending.shift();
      return true;
    }
    return false;
  }
}

/**
 * Positionen der Schnellaktionen im Bogen auf der Innenseite des Avatars.
 * @param {number} count
 * @param {number} radius Abstand zur Avatar-Mitte (px)
 * @param {'left'|'right'} [edge] Rand, an dem das Overlay klebt
 * @param {number} [spreadDeg] Öffnungswinkel des Bogens
 * @returns {{x: number, y: number}[]} relativ zur Avatar-Mitte, y nach unten
 */
export function radialPositions(count, radius, edge = 'right', spreadDeg = 96) {
  const out = [];
  const step = count > 1 ? spreadDeg / (count - 1) : 0;
  const start = 180 - (count > 1 ? spreadDeg / 2 : 0);
  for (let i = 0; i < count; i++) {
    const rad = ((start + i * step) * Math.PI) / 180;
    let x = Math.cos(rad) * radius;
    const y = -Math.sin(rad) * radius;
    if (edge === 'left') x = -x;
    out.push({ x: Math.round(x * 10) / 10 || 0, y: Math.round(y * 10) / 10 || 0 });
  }
  return out;
}

/** @param {string} kind @returns {{icon: string, title: string, tone: string}} */
export function toastMeta(kind) {
  return TOAST_META[kind] || TOAST_META.info;
}

/**
 * Anzeigedauer eines Toasts: Fehler/Warnungen bleiben länger, lange Texte auch.
 * @param {string} kind @param {string} [text] @returns {number} ms
 */
export function toastDuration(kind, text = '') {
  const base = kind === 'error' ? 12000 : kind === 'warning' ? 9000 : 5500;
  return base + Math.min(4000, String(text).length * 25);
}

/** @param {string} text @returns {number} Anzeigedauer der Chat-Vorschau in der Blase (ms) */
export function bubbleDuration(text) {
  return Math.round(clamp(2500 + String(text).length * 45, 3500, 9000));
}

/** Erste nicht-leere Zeile, gekürzt (für Statusmeldungen mit angehängtem Log). */
export function firstLine(text, max = 180) {
  const line = String(text ?? '').split('\n').map((l) => l.trim()).find(Boolean) || '';
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

/**
 * **fett** in Textsegmente zerlegen (sichere Mini-Formatierung für LLM-Antworten).
 * @param {string} text
 * @returns {{text: string, bold: boolean}[]}
 */
export function splitBold(text) {
  const s = String(text ?? '');
  const out = [];
  const re = /\*\*(?=\S)([^*]+?)\*\*/g;
  let last = 0;
  let m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ text: s.slice(last, m.index), bold: false });
    out.push({ text: m[1], bold: true });
    last = re.lastIndex;
  }
  if (last < s.length) out.push({ text: s.slice(last), bold: false });
  return out;
}

/** @param {object} p Proposal @param {number} [now] @returns {boolean} noch freizugeben? */
export function isPending(p, now = Date.now()) {
  if (!p || p.status !== 'pending') return false;
  const exp = parseTs(p.expires);
  return !(exp != null && exp > 0 && exp <= now);
}

/** @param {unknown} text @returns {boolean} true, wenn „ECHTGELD“ eingetippt wurde */
export function liveConfirmOk(text) {
  return String(text ?? '').trim().toUpperCase() === 'ECHTGELD';
}

/** @param {string} status @returns {string} */
export function proposalStatusLabel(status) {
  return PROPOSAL_STATUS[status] || String(status || '');
}

function plainNumber(x, maxDigits) {
  const n = typeof x === 'string' ? Number(x) : x;
  if (typeof n !== 'number' || !Number.isFinite(n)) return '–';
  return n.toLocaleString('de-DE', { maximumFractionDigits: maxDigits });
}

/**
 * Anzeigewerte einer Vorschlagskarte.
 * @param {object} p Proposal
 * @param {(x: number) => string} [price] Preisformatierer
 */
export function proposalView(p, price = (x) => plainNumber(x, 6)) {
  const close = p.action === 'close';
  const sell = p.side === 'sell';
  const live = p.account_kind === 'live';
  const has = (x) => x != null && x !== '' && Number(x) !== 0 && Number.isFinite(Number(x));
  return {
    side: close ? 'Schließen' : sell ? 'Verkauf' : 'Kauf',
    sideTone: close ? 'neutral' : sell ? 'red' : 'green',
    symbol: String(p.symbol || p.tv_symbol || '–'),
    live,
    account: live ? 'ECHTGELD' : 'Paper',
    qty: plainNumber(p.qty, 8),
    type: p.order_type === 'limit' ? 'Limit' : 'Markt',
    limit: p.order_type === 'limit' && has(p.limit_price) ? price(Number(p.limit_price)) : '–',
    stopLoss: has(p.stop_loss) ? price(Number(p.stop_loss)) : '–',
    takeProfit: has(p.take_profit) ? price(Number(p.take_profit)) : '–',
    reason: normalizeText(p.reason || p.ai_comment || ''),
  };
}

/**
 * Darstellung aus state.skin (Server liefert id, accent, size, edge, custom_image, show_subtitles).
 * @param {object} skin
 * @param {'left'|'right'} [fallbackEdge]
 */
export function resolveAppearance(skin, fallbackEdge = 'right') {
  const s = skin && typeof skin === 'object' ? skin : {};
  const size = Number(s.size);
  return {
    skin: normalizeSkinId(s.id ?? s.skin),
    accent: isHexColor(s.accent) ? s.accent.trim() : DEFAULT_ACCENT,
    size: Number.isFinite(size) && size > 0 ? Math.round(clamp(size, 72, 200)) : 132,
    edge: (s.edge ?? fallbackEdge) === 'left' ? 'left' : 'right',
    customImage: typeof s.custom_image === 'string' ? s.custom_image : '',
    showSubtitles: s.show_subtitles !== false,
  };
}

/** Großschreibung für Weckwort-Hinweise („hey vincent“ → „Hey Vincent“). */
export function titleCase(s) {
  return String(s ?? '').replace(/(^|\s)(\p{L})/gu, (_, sp, ch) => sp + ch.toUpperCase());
}

// ================================================================ DOM-Controller

const SVG_NS = 'http://www.w3.org/2000/svg';

function errorText(err) {
  return firstLine(err && err.message ? err.message : String(err || 'Unbekannter Fehler.'), 220);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function icon(id, className = 'ico') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', className);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `#${id}`);
  svg.append(use);
  return svg;
}

/** Text mit **fett** sicher (nur Textknoten) anhängen. */
function appendRich(node, text) {
  for (const seg of splitBold(text)) {
    if (seg.bold) node.append(el('strong', null, seg.text));
    else node.append(document.createTextNode(seg.text));
  }
}

class OverlayApp {
  constructor() {
    this.vin = window.vincent || null;
    const edge = new URLSearchParams(location.search).get('edge');
    this.appearance = resolveAppearance({}, edge === 'left' ? 'left' : 'right');
    this.applied = null;
    this.connected = false;
    this.everConnected = false;
    this.backend = { status: '', message: '' };
    this.voice = { state: 'off', muted: false, wake_word: 'hey vincent' };
    this.voiceEventAt = 0;
    this.override = null;
    this.vstate = 'disconnected';
    this.chatOpen = false;
    this.echo = new EchoFilter();
    this.dedupe = new MessageDeduper();
    this.msgNodes = [];
    this.sending = 0;
    this.proposals = new Map();
    this.cards = new Map();
    this.hovered = new Set();
    this.clickThrough = null;
    this.subtitleAt = 0;
    this.bubbleMode = '';
    this.bubbleTimer = 0;
    this.quickTimer = 0;
    this.connTimer = 0;
    this.reduced = false;
  }

  /** Aufruf einer Preload-Funktion – fehlt die Brücke (normaler Browser), passiert nichts. */
  callVin(name, ...args) {
    const fn = this.vin && this.vin[name];
    if (typeof fn !== 'function') return undefined;
    try {
      const res = fn.apply(this.vin, args);
      if (res && typeof res.catch === 'function') res.catch((err) => console.warn(`[overlay] ${name} fehlgeschlagen:`, err));
      return res;
    } catch (err) {
      console.warn(`[overlay] ${name} fehlgeschlagen:`, err);
      return undefined;
    }
  }

  init() {
    const $ = (id) => document.getElementById(id);
    this.el = {
      body: document.body,
      wrap: $('avatar-wrap'),
      canvas: $('avatar'),
      hit: $('avatar-hit'),
      badge: $('mode-badge'),
      propDot: $('prop-dot'),
      quick: $('quick'),
      bubble: $('bubble'),
      bubbleText: $('bubble-text'),
      toasts: $('toasts'),
      panel: $('panel'),
      close: $('panel-close'),
      phStatus: $('ph-status'),
      chipMode: $('chip-mode'),
      chipTv: $('chip-tv'),
      proposals: $('proposals'),
      messages: $('messages'),
      empty: $('empty'),
      emptyHint: $('empty-hint'),
      composer: $('composer'),
      input: $('input'),
      mic: $('btn-mic'),
      send: $('btn-send'),
    };
    this.qa = Object.fromEntries([...this.el.quick.querySelectorAll('.qa')].map((b) => [b.dataset.action, b]));

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.reduced = reduce.matches;
    this.avatar = new Avatar(this.el.canvas, {
      size: this.appearance.size,
      skin: this.appearance.skin,
      accent: this.appearance.accent,
      reducedMotion: this.reduced,
      onImageError: (url) => console.warn('[overlay] Eigenes Avatar-Bild nicht ladbar, nutze Nebula:', url),
    });
    reduce.addEventListener('change', (e) => {
      this.reduced = e.matches;
      this.avatar.setReducedMotion(e.matches);
    });
    this.applyAppearance(this.appearance);
    this.avatar.start();

    this.typingEl = el('div', 'msg msg-assistant typing');
    const dots = el('div', 'msg-bubble');
    dots.setAttribute('aria-label', 'Vincent schreibt …');
    dots.append(el('i'), el('i'), el('i'));
    this.typingEl.append(dots);

    this.initHits();
    this.initAvatar();
    this.initPanel();
    this.initBridge();
    this.initApi();
    this.refreshVisual();
    this.scheduleConnectionRender(450);
    setInterval(() => this.tick(), 1000);
  }

  // ------------------------------------------------------------ Klick-Durchlässigkeit

  initHits() {
    for (const node of document.querySelectorAll('[data-hit]')) this.registerHit(node);
    this.setClickThrough(true);
  }

  registerHit(node) {
    node.addEventListener('pointerenter', () => {
      this.hovered.add(node);
      this.syncClickThrough();
    });
    node.addEventListener('pointerleave', () => {
      this.hovered.delete(node);
      this.syncClickThrough();
    });
  }

  /** Für Elemente, die verschwinden, während der Zeiger darauf liegt (pointerleave käme zu spät). */
  releaseHit(node) {
    if (this.hovered.delete(node)) this.syncClickThrough();
  }

  syncClickThrough() {
    this.setClickThrough(this.hovered.size === 0);
  }

  setClickThrough(on) {
    if (this.clickThrough === on) return;
    this.clickThrough = on;
    this.callVin('setClickThrough', on);
  }

  // ------------------------------------------------------------ Avatar + Schnellaktionen

  initAvatar() {
    const { hit, wrap, propDot } = this.el;
    const show = () => {
      clearTimeout(this.quickTimer);
      if (this.chatOpen) return;
      wrap.classList.add('hovering');
      this.avatar.setHover(true);
    };
    const hideSoon = () => {
      clearTimeout(this.quickTimer);
      this.quickTimer = setTimeout(() => this.hideQuick(), 380);
    };
    hit.addEventListener('pointerenter', show);
    hit.addEventListener('pointerleave', hideSoon);
    hit.addEventListener('focus', show);
    hit.addEventListener('blur', hideSoon);
    hit.addEventListener('click', () => this.listen());
    for (const btn of Object.values(this.qa)) {
      btn.addEventListener('pointerenter', show);
      btn.addEventListener('pointerleave', hideSoon);
      btn.addEventListener('focus', show);
      btn.addEventListener('blur', hideSoon);
      btn.addEventListener('click', () => this.quickAction(btn.dataset.action));
    }
    propDot.addEventListener('click', () => this.setChatOpen(true));
  }

  hideQuick() {
    clearTimeout(this.quickTimer);
    this.el.wrap.classList.remove('hovering');
    this.avatar.setHover(false);
    for (const btn of Object.values(this.qa)) this.releaseHit(btn);
  }

  layoutQuick() {
    const btns = Object.values(this.qa);
    const radius = this.appearance.size / 2 + 32;
    radialPositions(btns.length, radius, this.appearance.edge).forEach((p, i) => {
      btns[i].style.setProperty('--x', `${p.x}px`);
      btns[i].style.setProperty('--y', `${p.y}px`);
      btns[i].style.setProperty('--i', String(i));
    });
  }

  quickAction(action) {
    if (action === 'listen') this.listen();
    else if (action === 'chat') {
      this.hideQuick();
      this.setChatOpen(!this.chatOpen);
    } else if (action === 'mute') this.toggleMute();
    else if (action === 'zentrale') {
      this.hideQuick();
      if (this.vin && typeof this.vin.openZentrale === 'function') this.callVin('openZentrale');
      else this.showToast('info', 'Die Zentrale gibt es nur in der Vincent-App.');
    }
  }

  setOverride(state) {
    this.override = { state, until: Date.now() + OVERRIDE_MS };
    this.refreshVisual();
    setTimeout(() => this.refreshVisual(), OVERRIDE_MS + 50);
  }

  /** Push-to-talk: startet Zuhören (bzw. beendet es, wenn Vincent gerade zuhört). */
  listen() {
    this.avatar.ping();
    if (!this.connected) {
      this.showToast('warning', 'Vincent ist noch nicht verbunden.');
      return;
    }
    const stopping = this.vstate === 'listening';
    this.setOverride(stopping ? 'idle' : 'listening');
    api.post('/api/voice/listen').catch((err) => {
      this.override = null;
      this.refreshVisual();
      this.showToast('error', `Zuhören nicht möglich: ${errorText(err)}`);
    });
  }

  toggleMute() {
    if (!this.connected) return;
    const next = !(this.voice.muted || this.voice.state === 'muted');
    const before = this.voice.muted;
    this.voice.muted = next;
    this.refreshVisual();
    api.post('/api/voice/mute', { muted: next })
      .then((res) => {
        if (res && typeof res.muted === 'boolean') this.voice.muted = res.muted;
        this.refreshVisual();
      })
      .catch((err) => {
        this.voice.muted = before;
        this.refreshVisual();
        this.showToast('error', `Stummschalten fehlgeschlagen: ${errorText(err)}`);
      });
  }

  refreshVisual() {
    const now = Date.now();
    if (this.override && this.override.until <= now) this.override = null;
    const vs = visualState({
      connected: this.connected,
      voiceState: this.voice.state,
      muted: this.voice.muted,
      override: this.override,
      now,
    });
    if (vs !== this.vstate || this.el.body.dataset.vstate !== vs) {
      this.vstate = vs;
      this.avatar.setState(vs);
      this.el.body.dataset.vstate = vs;
    }
    this.el.phStatus.textContent = statusLabel(vs);
    const listening = vs === 'listening';
    const muted = !!this.voice.muted || this.voice.state === 'muted';
    const lq = this.qa.listen;
    lq.querySelector('.tip').textContent = listening ? 'Zuhören beenden' : 'Zuhören';
    lq.setAttribute('aria-label', listening ? 'Zuhören beenden' : 'Zuhören');
    lq.classList.toggle('on', listening);
    this.el.hit.setAttribute('aria-label', listening ? 'Vincent – klicken, um das Zuhören zu beenden' : 'Vincent – klicken zum Zuhören');
    const mq = this.qa.mute;
    mq.querySelector('use').setAttribute('href', muted ? '#i-mic' : '#i-mic-off');
    mq.querySelector('.tip').textContent = muted ? 'Laut' : 'Stumm';
    mq.setAttribute('aria-label', muted ? 'Mikrofon wieder einschalten' : 'Mikrofon stummschalten');
    mq.classList.toggle('on', muted);
    this.el.mic.classList.toggle('active', listening);
    this.el.mic.setAttribute('aria-pressed', String(listening));
  }

  // ------------------------------------------------------------ Darstellung

  applyAppearance(a) {
    const prev = this.applied || {};
    this.appearance = a;
    const { body } = this.el;
    if (a.edge !== prev.edge) {
      body.classList.toggle('edge-left', a.edge === 'left');
      body.classList.toggle('edge-right', a.edge !== 'left');
    }
    if (a.size !== prev.size) {
      body.style.setProperty('--av', `${a.size}px`);
      this.avatar.setSize(a.size);
    }
    if (a.size !== prev.size || a.edge !== prev.edge) this.layoutQuick();
    if (a.accent !== prev.accent) {
      const rgb = hexToRgb(a.accent);
      const cool = shiftHue(rgb, -62, 0.06);
      body.style.setProperty('--accent', rgbToHex(rgb));
      body.style.setProperty('--accent-rgb', rgb.map(Math.round).join(', '));
      body.style.setProperty('--accent-2', rgbToHex(cool));
      body.style.setProperty('--accent-2-rgb', cool.map(Math.round).join(', '));
      this.avatar.setAccent(a.accent);
    }
    if (a.skin !== prev.skin) this.avatar.setSkin(a.skin);
    if (a.skin !== prev.skin || a.customImage !== prev.customImage) {
      this.avatar.setCustomImage(a.skin === 'eigenes' ? a.customImage : '');
    }
    if (!a.showSubtitles && (this.bubbleMode === 'subtitle' || this.bubbleMode === 'preview')) this.hideBubble();
    this.applied = a;
  }

  applyState(s) {
    if (!s || typeof s !== 'object') return;
    if (s.voice && typeof s.voice === 'object') {
      const fresh = Date.now() - this.voiceEventAt > 1500;
      const state = fresh && typeof s.voice.state === 'string' ? s.voice.state : this.voice.state;
      this.voice = { ...this.voice, ...s.voice, state };
      const ww = typeof s.voice.wake_word === 'string' && s.voice.wake_word.trim() ? titleCase(s.voice.wake_word.trim()) : '';
      this.el.emptyHint.textContent = ww && s.voice.wake_enabled !== false
        ? `Schreib mir oder sag „${ww}“.`
        : 'Schreib mir eine Nachricht.';
    }
    if (s.skin && typeof s.skin === 'object') this.applyAppearance(resolveAppearance(s.skin, this.appearance.edge));
    const mb = modeBadge(s.mode);
    this.el.badge.textContent = mb.label;
    this.el.badge.className = `mode-badge tone-${mb.tone}${mb.pulse ? ' pulse' : ''}`;
    this.el.badge.title = mb.title;
    this.el.chipMode.textContent = mb.chip;
    this.el.chipMode.className = `chip tone-${mb.tone}${mb.pulse ? ' pulse' : ''}`;
    this.el.chipMode.title = mb.title;
    const ac = accountChip(s.tradingview);
    this.el.chipTv.textContent = ac.label;
    this.el.chipTv.className = `chip tone-${ac.tone}`;
    this.el.chipTv.title = ac.title;
    if (Array.isArray(s.proposals)) this.setProposals(s.proposals);
    this.refreshVisual();
  }

  // ------------------------------------------------------------ Blase (Untertitel/Status)

  showBubble(text, mode, ttl = 0) {
    const { bubble, bubbleText } = this.el;
    clearTimeout(this.bubbleTimer);
    this.bubbleMode = mode;
    bubble.dataset.mode = mode;
    bubbleText.textContent = text;
    if (bubble.hidden) {
      bubble.hidden = false;
      void bubble.offsetWidth;
    }
    bubble.classList.add('show');
    if (ttl > 0) this.bubbleTimer = setTimeout(() => this.hideBubble(mode), ttl);
  }

  hideBubble(mode) {
    if (mode && this.bubbleMode !== mode) return;
    const { bubble } = this.el;
    clearTimeout(this.bubbleTimer);
    this.bubbleMode = '';
    bubble.classList.remove('show');
    this.releaseHit(bubble);
    setTimeout(() => {
      if (!bubble.classList.contains('show')) bubble.hidden = true;
    }, 360);
  }

  onSubtitle(d) {
    const text = normalizeText(d && d.text);
    if (text) {
      this.subtitleAt = Date.now();
      if (!this.appearance.showSubtitles || this.chatOpen || !this.connected) return;
      this.showBubble(text, 'subtitle', 15000);
    } else if (this.bubbleMode === 'subtitle') {
      clearTimeout(this.bubbleTimer);
      this.bubbleTimer = setTimeout(() => this.hideBubble('subtitle'), 2200);
    }
  }

  previewInBubble(text) {
    if (this.chatOpen || !this.connected || !this.appearance.showSubtitles) return;
    if (this.bubbleMode === 'subtitle' || this.vstate === 'speaking' || Date.now() - this.subtitleAt < 3000) return;
    const short = text.length > 240 ? `${text.slice(0, 239).trimEnd()}…` : text;
    this.showBubble(short, 'preview', bubbleDuration(short));
  }

  scheduleConnectionRender(delay) {
    clearTimeout(this.connTimer);
    this.connTimer = setTimeout(() => this.renderConnection(), delay);
  }

  renderConnection() {
    if (this.connected) {
      if (this.bubbleMode === 'status' || this.bubbleMode === 'error') this.hideBubble();
      return;
    }
    const { status, message } = this.backend;
    if (status === 'failed') {
      this.showBubble(firstLine(message) || 'Vincent konnte nicht gestartet werden.', 'error');
    } else if (status === 'stopped') {
      this.showBubble(firstLine(message) || 'Vincent wurde beendet.', 'error');
    } else if (status === 'crashed') {
      this.showBubble(firstLine(message) || 'Vincent startet neu …', 'status');
    } else {
      this.showBubble(this.everConnected ? 'Verbindung verloren – verbinde neu …' : 'Starte Vincent …', 'status');
    }
  }

  // ------------------------------------------------------------ Chat-Panel

  initPanel() {
    const { panel, close, input, composer, mic, bubble } = this.el;
    close.addEventListener('click', () => this.setChatOpen(false));
    bubble.addEventListener('click', () => {
      if (this.bubbleMode === 'subtitle' || this.bubbleMode === 'preview') this.setChatOpen(true);
    });
    input.addEventListener('input', () => this.syncComposer());
    input.addEventListener('focus', () => this.callVin('focusOverlay'));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        this.submit();
      }
    });
    composer.addEventListener('submit', (e) => {
      e.preventDefault();
      this.submit();
    });
    mic.addEventListener('click', () => this.listen());
    for (const b of panel.querySelectorAll('.sugg')) b.addEventListener('click', () => this.sendChat(b.textContent || ''));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.chatOpen) this.setChatOpen(false);
    });
  }

  syncComposer() {
    const { input, composer, send } = this.el;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    const has = input.value.trim().length > 0;
    composer.classList.toggle('has-text', has);
    send.disabled = !has;
  }

  submit() {
    const text = this.el.input.value;
    if (!text.trim()) return;
    this.el.input.value = '';
    this.syncComposer();
    this.sendChat(text);
  }

  /**
   * @param {boolean} open
   * @param {boolean} [fromMain] true, wenn der Hauptprozess das Öffnen/Schließen angestoßen hat
   */
  setChatOpen(open, fromMain = false) {
    const next = !!open;
    if (next === this.chatOpen) return;
    this.chatOpen = next;
    const { body, panel, input } = this.el;
    body.classList.toggle('chat-open', next);
    panel.toggleAttribute('inert', !next);
    panel.setAttribute('aria-hidden', String(!next));
    if (!fromMain && this.vin) {
      this.echo.expect(next);
      this.callVin('toggleChat', next);
    }
    if (next) {
      this.hideQuick();
      this.hideBubble();
      this.clearToasts();
      this.renderProposals();
      this.scrollToBottom(false);
      this.callVin('focusOverlay');
      setTimeout(() => {
        if (this.chatOpen) input.focus({ preventScroll: true });
      }, 80);
    } else {
      this.releaseHit(panel);
      input.blur();
    }
  }

  onChatToggle(payload) {
    const open = payload && typeof payload.open === 'boolean' ? payload.open : null;
    if (open !== null && this.echo.consume(open)) return;
    this.setChatOpen(open === null ? !this.chatOpen : open, true);
  }

  async sendChat(raw) {
    const text = String(raw ?? '').trim();
    if (!text) return;
    if (!this.connected) {
      this.addSystem('error', 'Keine Verbindung zu Vincent – Nachricht wurde nicht gesendet.');
      return;
    }
    this.addMessage({ role: 'user', text, source: 'text' }, 'local', true);
    this.setTyping(1);
    try {
      const res = await api.post('/api/chat', { text });
      const reply = res && typeof res.reply === 'string' ? res.reply : '';
      if (reply.trim()) this.addMessage({ role: 'assistant', text: reply, source: 'text' }, 'local');
    } catch (err) {
      this.addSystem('error', `Nachricht fehlgeschlagen: ${errorText(err)}`);
    } finally {
      this.setTyping(-1);
    }
  }

  setTyping(delta) {
    this.sending = Math.max(0, this.sending + delta);
    if (this.sending > 0 && delta > 0) {
      this.el.messages.append(this.typingEl);
      this.scrollToBottom(true);
    } else if (this.sending === 0) this.typingEl.remove();
  }

  onChat(d, evt) {
    if (!d || typeof d.text !== 'string') return;
    const ts = parseTs(evt && evt.ts) || Date.now();
    const added = this.addMessage({ role: d.role, text: d.text, source: d.source, ts }, 'ws');
    if (added && d.role === 'assistant') this.typingEl.remove();
  }

  /**
   * @param {{role: string, text: string, source?: string, ts?: number}} m
   * @param {'local'|'ws'} origin
   * @param {boolean} [own] eigene Nachricht → immer nach unten scrollen
   * @returns {boolean} true, wenn angezeigt (false = leer oder Duplikat)
   */
  addMessage(m, origin, own = false) {
    const role = m.role === 'user' ? 'user' : 'assistant';
    const text = String(m.text ?? '').trim();
    if (!text || this.dedupe.isDuplicate(role, text, origin)) return false;
    const row = el('div', `msg msg-${role}`);
    const bubble = el('div', 'msg-bubble');
    appendRich(bubble, text);
    const meta = el('div', 'msg-meta');
    if (m.source === 'voice') {
      const i = icon('i-mic');
      meta.append(i, el('span', 'src', 'Sprache'));
    } else if (m.source === 'telegram') {
      meta.append(icon('i-telegram'), el('span', 'src', 'Telegram'));
    }
    meta.append(el('time', null, formatClock(m.ts || Date.now())));
    row.append(bubble, meta);
    this.appendRow(row, own);
    if (role === 'assistant') this.previewInBubble(normalizeText(text));
    return true;
  }

  addSystem(kind, text, ts = Date.now()) {
    const meta = toastMeta(kind);
    const row = el('div', `msg-system tone-${meta.tone}`);
    row.append(icon(meta.icon), el('span', 'sys-text', text), el('time', null, formatClock(ts)));
    this.appendRow(row, false);
  }

  appendRow(row, forceScroll) {
    const box = this.el.messages;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
    if (this.typingEl.isConnected) box.insertBefore(row, this.typingEl);
    else box.append(row);
    this.msgNodes.push(row);
    while (this.msgNodes.length > MAX_MESSAGES) this.msgNodes.shift().remove();
    this.el.empty.hidden = true;
    if (forceScroll || nearBottom || !this.chatOpen) this.scrollToBottom(this.chatOpen);
  }

  scrollToBottom(smooth) {
    const box = this.el.messages;
    box.scrollTo({ top: box.scrollHeight, behavior: smooth && !this.reduced ? 'smooth' : 'auto' });
  }

  // ------------------------------------------------------------ Toasts

  onNotify(d) {
    const kind = d && TOAST_KINDS.includes(d.kind) ? d.kind : 'info';
    const text = normalizeText(d && d.text);
    if (!text) return;
    this.addSystem(kind, text);
    if (!this.chatOpen) this.showToast(kind, text);
  }

  showToast(kind, text) {
    const meta = toastMeta(kind);
    const dur = toastDuration(kind, text);
    const t = el('div', `toast glass tone-${meta.tone}`);
    t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    t.tabIndex = 0;
    t.style.setProperty('--dur', `${dur}ms`);
    const ic = el('span', 'toast-icon');
    ic.append(icon(meta.icon));
    const body = el('div', 'toast-body');
    body.append(el('div', 'toast-title', meta.title), el('div', 'toast-text', text));
    t.append(ic, body, el('i', 'toast-progress'));
    const timer = { left: dur, started: Date.now(), id: 0 };
    const arm = () => {
      timer.started = Date.now();
      timer.id = setTimeout(() => this.dismissToast(t), timer.left);
    };
    t.addEventListener('pointerenter', () => {
      clearTimeout(timer.id);
      timer.left = Math.max(800, timer.left - (Date.now() - timer.started));
      t.classList.add('paused');
    });
    t.addEventListener('pointerleave', () => {
      t.classList.remove('paused');
      arm();
    });
    const open = () => {
      this.dismissToast(t);
      this.setChatOpen(true);
    };
    t.addEventListener('click', open);
    t.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    });
    t.cancel = () => clearTimeout(timer.id);
    this.registerHit(t);
    this.el.toasts.prepend(t);
    const all = [...this.el.toasts.querySelectorAll('.toast:not(.leaving)')];
    for (const old of all.slice(3)) this.dismissToast(old);
    arm();
  }

  dismissToast(t) {
    if (!t || t.classList.contains('leaving')) return;
    if (t.cancel) t.cancel();
    t.classList.add('leaving');
    this.releaseHit(t);
    setTimeout(() => t.remove(), this.reduced ? 0 : 280);
  }

  clearToasts() {
    for (const t of this.el.toasts.querySelectorAll('.toast')) this.dismissToast(t);
  }

  // ------------------------------------------------------------ Handelsvorschläge

  setProposals(list) {
    const ids = new Set();
    for (const p of list) {
      if (!p || p.id == null) continue;
      const id = String(p.id);
      ids.add(id);
      this.proposals.set(id, p);
    }
    for (const id of [...this.proposals.keys()]) if (!ids.has(id)) this.proposals.delete(id);
    this.renderProposals();
  }

  upsertProposal(p) {
    if (!p || p.id == null) return;
    this.proposals.set(String(p.id), p);
    this.renderProposals();
  }

  renderProposals() {
    const now = Date.now();
    for (const [id, p] of this.proposals) {
      const card = this.cards.get(id);
      if (isPending(p, now)) {
        if (card && !card.finishing) this.updateCard(card, p, now);
        else if (!card) this.createCard(p, now);
      } else if (card && !card.finishing) {
        this.finishCard(card, p);
      }
    }
    for (const card of this.cards.values()) {
      if (!this.proposals.has(card.id) && !card.finishing) this.finishCard(card, null);
    }
    this.syncProposalIndicators();
  }

  syncProposalIndicators() {
    const open = [...this.cards.values()].filter((c) => !c.finishing).length;
    this.el.panel.classList.toggle('has-proposals', this.cards.size > 0);
    const dot = this.el.propDot;
    dot.textContent = String(open);
    dot.setAttribute('aria-label', open === 1 ? '1 offener Handelsvorschlag – Chat öffnen' : `${open} offene Handelsvorschläge – Chat öffnen`);
    dot.title = open === 1 ? '1 offener Handelsvorschlag' : `${open} offene Handelsvorschläge`;
    if (open > 0) dot.hidden = false;
    else {
      dot.hidden = true;
      this.releaseHit(dot);
    }
  }

  createCard(p, now) {
    const id = String(p.id);
    const view = proposalView(p, api.fmtPrice);
    const card = el('article', `prop${view.live ? ' live' : ''}`);
    card.dataset.id = id;
    card.setAttribute('aria-label', `Handelsvorschlag ${view.side} ${view.symbol}`);
    const bar = el('div', 'prop-bar');
    const barFill = el('i');
    bar.append(barFill);

    const head = el('div', 'prop-head');
    const side = el('span', `side tone-${view.sideTone}`);
    const sym = el('span', 'prop-sym');
    const acct = el('span', `acct tone-${view.live ? 'red' : 'blue'}`);
    head.append(side, sym, acct);

    const grid = el('dl', 'prop-grid');
    const cell = (label) => {
      const wrap = el('div');
      const dd = el('dd');
      wrap.append(el('dt', null, label), dd);
      grid.append(wrap);
      return dd;
    };
    const refs = {
      bar: barFill,
      side,
      sym,
      acct,
      qty: cell('Menge'),
      type: cell('Typ'),
      limit: cell('Limit'),
      sl: cell('Stop-Loss'),
      tp: cell('Take-Profit'),
      timer: cell('Läuft ab in'),
    };
    refs.timer.classList.add('timer');

    const reasonWrap = el('div', 'prop-reason-wrap');
    const reason = el('p', 'prop-reason');
    reason.title = 'Klicken für die ganze Begründung';
    reason.addEventListener('click', () => reason.classList.toggle('expanded'));
    reasonWrap.append(el('span', 'prop-label', 'Begründung'), reason);
    refs.reason = reason;
    refs.reasonWrap = reasonWrap;

    const c = { id, el: card, refs, busy: false, finishing: false, live: view.live, confirm: null, expired: false };
    card.append(bar, head, grid, reasonWrap);

    if (view.live) {
      const lab = el('label', 'prop-confirm');
      const inp = el('input');
      inp.type = 'text';
      inp.placeholder = 'ECHTGELD';
      inp.autocomplete = 'off';
      inp.spellcheck = false;
      inp.setAttribute('aria-label', 'Zur Bestätigung ECHTGELD eintippen');
      inp.addEventListener('input', () => this.syncCardButtons(c));
      inp.addEventListener('focus', () => this.callVin('focusOverlay'));
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (!refs.approve.disabled) this.decide(c, 'approve');
        }
      });
      lab.append(el('span', null, 'Echtes Geld! Zur Freigabe ECHTGELD eintippen:'), inp);
      card.append(lab);
      c.confirm = inp;
    }

    const actions = el('div', 'prop-actions');
    refs.reject = el('button', 'btn ghost', 'Ablehnen');
    refs.reject.type = 'button';
    refs.approve = el('button', 'btn primary', 'Ausführen');
    refs.approve.type = 'button';
    refs.reject.addEventListener('click', () => this.decide(c, 'reject'));
    refs.approve.addEventListener('click', () => this.decide(c, 'approve'));
    actions.append(refs.reject, refs.approve);
    refs.status = el('div', 'prop-status');
    refs.status.setAttribute('role', 'status');
    card.append(actions, refs.status);

    this.el.proposals.append(card);
    this.cards.set(id, c);
    this.updateCard(c, p, now);
  }

  updateCard(c, p, now) {
    const view = proposalView(p, api.fmtPrice);
    const { refs } = c;
    refs.side.textContent = view.side;
    refs.side.className = `side tone-${view.sideTone}`;
    refs.sym.textContent = view.symbol;
    refs.sym.title = view.symbol;
    refs.acct.textContent = view.account;
    refs.qty.textContent = view.qty;
    refs.type.textContent = view.type;
    refs.limit.textContent = view.limit;
    refs.sl.textContent = view.stopLoss;
    refs.tp.textContent = view.takeProfit;
    refs.reason.textContent = view.reason;
    refs.reasonWrap.hidden = !view.reason;
    this.updateCountdown(c, p, now);
    this.syncCardButtons(c);
  }

  updateCountdown(c, p, now) {
    const exp = parseTs(p.expires);
    const created = parseTs(p.created);
    const left = exp != null && exp > 0 ? exp - now : null;
    c.refs.timer.textContent = formatCountdown(left);
    c.refs.timer.classList.toggle('soon', left != null && left < 60000);
    const frac = left != null && created != null && exp > created ? clamp(left / (exp - created)) : 1;
    c.refs.bar.style.width = `${(frac * 100).toFixed(2)}%`;
    const expired = left != null && left <= 0;
    if (expired !== c.expired) {
      c.expired = expired;
      this.syncCardButtons(c);
    }
  }

  syncCardButtons(c) {
    const { approve, reject } = c.refs;
    const confirmed = !c.live || (c.confirm && liveConfirmOk(c.confirm.value));
    approve.disabled = c.busy || c.finishing || c.expired || !confirmed;
    reject.disabled = c.busy || c.finishing;
    approve.title = c.live && !confirmed ? 'Erst ECHTGELD eintippen' : '';
    if (c.confirm) c.confirm.disabled = c.busy || c.finishing;
  }

  async decide(c, action) {
    if (c.busy || c.finishing) return;
    if (action === 'approve' && c.live && !(c.confirm && liveConfirmOk(c.confirm.value))) return;
    c.busy = true;
    c.el.classList.remove('err');
    c.refs.status.textContent = action === 'approve' ? 'Wird ausgeführt …' : 'Wird abgelehnt …';
    this.syncCardButtons(c);
    try {
      const res = await api.post(`/api/proposals/${encodeURIComponent(c.id)}/${action}`);
      const text = res && typeof res.text === 'string' && res.text.trim() ? res.text.trim() : '';
      c.refs.status.textContent = text || (action === 'approve' ? 'Freigegeben.' : 'Abgelehnt.');
      c.el.classList.add('ok');
      c.decided = true;
      setTimeout(() => {
        if (!c.finishing) this.finishCard(c, null);
      }, 6000);
    } catch (err) {
      c.busy = false;
      c.el.classList.add('err');
      c.refs.status.textContent = errorText(err);
      this.syncCardButtons(c);
    }
  }

  finishCard(c, p) {
    c.finishing = true;
    c.busy = true;
    this.syncCardButtons(c);
    if (p && p.status && !c.decided) {
      const label = proposalStatusLabel(p.status === 'pending' ? 'expired' : p.status);
      const result = normalizeText(p.result);
      c.refs.status.textContent = result ? `${label}: ${result}` : label;
    }
    c.el.classList.add('done');
    const linger = c.refs.status.textContent ? 3800 : 200;
    setTimeout(() => {
      c.el.classList.add('leaving');
      setTimeout(() => {
        c.el.remove();
        if (this.cards.get(c.id) === c) this.cards.delete(c.id);
        this.syncProposalIndicators();
      }, this.reduced ? 0 : 320);
    }, linger);
    this.syncProposalIndicators();
  }

  tick() {
    const now = Date.now();
    let changed = false;
    for (const c of this.cards.values()) {
      if (c.finishing) continue;
      const p = this.proposals.get(c.id);
      if (!p) continue;
      if (!isPending(p, now)) changed = true;
      else this.updateCountdown(c, p, now);
    }
    if (changed) this.renderProposals();
  }

  // ------------------------------------------------------------ Brücke + Backend

  initBridge() {
    if (!this.vin) return;
    if (typeof this.vin.onShortcut === 'function') this.callVin('onShortcut', (p) => this.onShortcut(p));
    if (typeof this.vin.onChatToggle === 'function') this.callVin('onChatToggle', (p) => this.onChatToggle(p));
    if (typeof this.vin.getBackend === 'function') {
      Promise.resolve(this.callVin('getBackend'))
        .then((info) => {
          if (info && !this.connected) this.onBackend(info);
        })
        .catch(() => {});
    }
  }

  /** Globale Tastenkürzel: der Hauptprozess ruft das Backend selbst auf – hier nur animieren. */
  onShortcut(p) {
    const action = p && p.action;
    if (action === 'listen') {
      this.avatar.ping();
      if (this.connected) this.setOverride(this.vstate === 'listening' ? 'idle' : 'listening');
    } else if (action === 'stop') {
      if (this.connected) this.setOverride('idle');
      this.hideBubble('subtitle');
    }
  }

  onBackend(info) {
    if (!info || typeof info !== 'object') return;
    this.backend = { status: String(info.status || ''), message: String(info.message || '') };
    if (!this.connected) this.scheduleConnectionRender(0);
  }

  initApi() {
    api.on('connection', (d) => {
      const connected = !!(d && d.connected);
      this.connected = connected;
      if (connected) {
        this.everConnected = true;
        const s = api.getState();
        if (s) this.applyState(s);
      } else {
        this.typingEl.remove();
      }
      this.refreshVisual();
      this.scheduleConnectionRender(connected ? 0 : 1200);
    });
    api.on('backend', (d) => this.onBackend(d));
    api.on('state', (d) => this.applyState(d));
    api.on('voice.state', (d) => {
      if (!d || typeof d.state !== 'string') return;
      this.voice.state = d.state;
      if (d.state === 'muted') this.voice.muted = true;
      this.voiceEventAt = Date.now();
      this.override = null;
      this.refreshVisual();
    });
    api.on('voice.level', (d) => {
      if (d) this.avatar.pushLevel(d.level, d.source === 'tts' ? 'tts' : 'mic');
    });
    api.on('subtitle', (d) => this.onSubtitle(d));
    api.on('chat', (d, evt) => this.onChat(d, evt));
    api.on('proposal', (d) => this.upsertProposal(d && d.proposal ? d.proposal : d));
    api.on('notify', (d) => this.onNotify(d));
    api.connect().catch((err) => console.warn('[overlay] Verbindung fehlgeschlagen:', err));
  }
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const start = () => {
    const app = new OverlayApp();
    app.init();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
}
