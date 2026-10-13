/**
 * Gemeinsamer Backend-Client für Overlay und Zentrale (ES-Modul, ohne Framework).
 *
 * - REST: `get/post/put/patch/del` gegen http://127.0.0.1:<port> mit Header `X-Vincent-Token`.
 * - Events: WebSocket `ws://127.0.0.1:<port>/ws?token=…` mit automatischem Wiederverbinden.
 *   Port und Token kommen aus `window.vincent.getBackend()` (Preload); ohne Preload (Entwicklung im
 *   normalen Browser) aus `?port=…&token=…` der Seiten-URL.
 *
 * @module shared/api
 */

/** @typedef {{port: number|null, token: string, status?: string, message?: string}} BackendInfo */
/** @typedef {{type: string, data: any, ts: number}} VincentEvent */
/** @typedef {(data: any, event: VincentEvent) => void} Listener */

const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 10_000;
const PING_INTERVAL_MS = 20_000;

/** @type {Map<string, Set<Listener>>} */
const listeners = new Map();

const conn = {
  /** @type {BackendInfo|null} */
  info: null,
  /** @type {WebSocket|null} */
  ws: null,
  connected: false,
  attempt: 0,
  /** @type {ReturnType<typeof setTimeout>|null} */
  retryTimer: null,
  /** @type {ReturnType<typeof setInterval>|null} */
  pingTimer: null,
  /** @type {any} */
  lastState: null,
  /** @type {Promise<void>|null} */
  firstOpen: null,
  /** @type {(() => void)|null} */
  resolveFirstOpen: null,
};

/** @returns {any} `window.vincent` oder null (z. B. im normalen Browser oder unter Node) */
function bridge() {
  return typeof window !== 'undefined' && window.vincent ? window.vincent : null;
}

/**
 * Port/Token des Backends ermitteln (Preload oder URL-Parameter).
 * @returns {Promise<BackendInfo>}
 */
async function fetchBackendInfo() {
  const v = bridge();
  if (v && typeof v.getBackend === 'function') {
    const info = await v.getBackend();
    return info || { port: null, token: '' };
  }
  if (typeof location !== 'undefined') {
    const params = new URLSearchParams(location.search);
    const port = Number(params.get('port'));
    return { port: Number.isInteger(port) && port > 0 ? port : null, token: params.get('token') || '', status: 'ready' };
  }
  return { port: null, token: '' };
}

/**
 * Event an alle passenden Listener verteilen (Typ-Listener und '*').
 * @param {VincentEvent} event
 */
function dispatch(event) {
  for (const key of [event.type, '*']) {
    const set = listeners.get(key);
    if (!set) continue;
    for (const cb of [...set]) {
      try {
        cb(event.data, event);
      } catch (err) {
        console.error(`[api] Listener für "${key}" fehlgeschlagen:`, err);
      }
    }
  }
}

/** @param {boolean} connected */
function setConnected(connected) {
  if (conn.connected === connected) return;
  conn.connected = connected;
  dispatch({ type: 'connection', data: { connected }, ts: Date.now() / 1000 });
}

function clearTimers() {
  if (conn.retryTimer) clearTimeout(conn.retryTimer);
  if (conn.pingTimer) clearInterval(conn.pingTimer);
  conn.retryTimer = null;
  conn.pingTimer = null;
}

function scheduleReconnect() {
  if (conn.retryTimer) return;
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** conn.attempt);
  conn.attempt += 1;
  conn.retryTimer = setTimeout(() => {
    conn.retryTimer = null;
    void openSocket();
  }, delay);
}

/** Schließt die aktuelle Verbindung, ohne einen automatischen Neuaufbau auszulösen. */
function dropSocket() {
  const ws = conn.ws;
  conn.ws = null;
  if (ws) {
    ws.onopen = ws.onclose = ws.onerror = ws.onmessage = null;
    try {
      ws.close();
    } catch {
      /* bereits geschlossen */
    }
  }
  if (conn.pingTimer) clearInterval(conn.pingTimer);
  conn.pingTimer = null;
  setConnected(false);
}

/** Holt Port/Token neu (der Port ändert sich nach einem Backend-Neustart) und öffnet den WebSocket. */
async function openSocket() {
  dropSocket();
  let info;
  try {
    info = await fetchBackendInfo();
  } catch (err) {
    console.warn('[api] Backend-Info nicht verfügbar:', err);
    scheduleReconnect();
    return;
  }
  conn.info = info;
  if (!info.port || (info.status && info.status !== 'ready')) {
    scheduleReconnect();
    return;
  }
  if (conn.ws) return; // parallel geöffnet
  let ws;
  try {
    ws = new WebSocket(`ws://127.0.0.1:${info.port}/ws?token=${encodeURIComponent(info.token)}`);
  } catch (err) {
    console.warn('[api] WebSocket konnte nicht geöffnet werden:', err);
    scheduleReconnect();
    return;
  }
  conn.ws = ws;
  ws.onopen = () => {
    conn.attempt = 0;
    setConnected(true);
    conn.pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, PING_INTERVAL_MS);
    if (conn.resolveFirstOpen) {
      conn.resolveFirstOpen();
      conn.resolveFirstOpen = null;
    }
  };
  ws.onmessage = (msg) => {
    let event;
    try {
      event = JSON.parse(typeof msg.data === 'string' ? msg.data : '');
    } catch {
      return;
    }
    if (!event || typeof event.type !== 'string') return;
    if (event.type === 'state') conn.lastState = event.data;
    dispatch({ type: event.type, data: event.data, ts: event.ts ?? Date.now() / 1000 });
  };
  ws.onclose = () => {
    if (conn.ws !== ws) return;
    conn.ws = null;
    if (conn.pingTimer) clearInterval(conn.pingTimer);
    conn.pingTimer = null;
    setConnected(false);
    scheduleReconnect();
  };
  ws.onerror = () => {
    /* onclose folgt und plant den Neuaufbau */
  };
}

/**
 * Verbindung zum Backend aufbauen (idempotent – mehrfacher Aufruf ist unschädlich).
 * Versucht es so lange erneut, bis das Backend erreichbar ist; nach Backend-Neustarts wird
 * automatisch neu verbunden.
 *
 * @returns {Promise<void>} erfüllt sich, sobald der WebSocket zum ersten Mal offen ist
 */
export function connect() {
  if (conn.firstOpen) return conn.firstOpen;
  conn.firstOpen = new Promise((resolve) => {
    conn.resolveFirstOpen = resolve;
  });
  const v = bridge();
  if (v && typeof v.onBackendStatus === 'function') {
    v.onBackendStatus((info) => {
      dispatch({ type: 'backend', data: info, ts: Date.now() / 1000 });
      if (!info) return;
      if (info.status === 'ready') {
        const changed = !conn.info || conn.info.port !== info.port || conn.info.token !== info.token;
        if (changed || !conn.connected) {
          clearTimers();
          conn.attempt = 0;
          void openSocket();
        }
      } else {
        conn.info = info;
        dropSocket();
      }
    });
  }
  void openSocket();
  return conn.firstOpen;
}

/**
 * Event abonnieren.
 * Typen: alle WS-Events (`state`, `voice.state`, `voice.level`, `chat`, `subtitle`, `proposal`, `notify`,
 * `analysis`, `tradingview`), `'*'` für alle, sowie die lokalen Typen `connection` (`{connected}`)
 * und `backend` (`{port, token, status, message}` – Status des Python-Prozesses).
 *
 * @param {string} type
 * @param {Listener} cb wird mit `(data, event)` aufgerufen
 * @returns {() => void} Abmelden
 */
export function on(type, cb) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(cb);
  return () => {
    const set = listeners.get(type);
    if (set) set.delete(cb);
  };
}

/** @returns {any} letzter `state`-Snapshot oder null */
export function getState() {
  return conn.lastState;
}

/** @returns {boolean} true, solange der WebSocket verbunden ist */
export function isConnected() {
  return conn.connected;
}

/**
 * Fehlermeldung aus einer Backend-Antwort ziehen (`detail` ist Text oder FastAPI-Validierungsliste).
 * @param {any} data
 * @param {number} status
 * @returns {string}
 */
export function errorMessage(data, status) {
  const detail = data && typeof data === 'object' ? data.detail : null;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => (d && typeof d === 'object' && d.msg ? String(d.msg) : String(d))).join('; ');
  }
  if (typeof data === 'string' && data.trim()) return data.trim().slice(0, 300);
  return `Anfrage fehlgeschlagen (HTTP ${status}).`;
}

/**
 * @param {string} method
 * @param {string} path z. B. '/api/state'
 * @param {unknown} [body]
 * @returns {Promise<any>}
 */
async function request(method, path, body) {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (!conn.info || !conn.info.port || attempt > 0) conn.info = await fetchBackendInfo();
    const { port, token } = conn.info;
    if (!port) throw new Error('Vincent ist noch nicht bereit.');
    /** @type {RequestInit} */
    const init = { method, headers: { Accept: 'application/json', 'X-Vincent-Token': token } };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(`http://127.0.0.1:${port}${path}`, init);
    } catch (err) {
      // Netzwerkfehler: Anfrage hat das Backend nicht erreicht – Port evtl. veraltet, einmal neu versuchen.
      if (attempt === 0) continue;
      throw new Error('Vincent ist nicht erreichbar. Läuft das Backend?');
    }
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!res.ok) throw new Error(errorMessage(data, res.status));
    return data;
  }
  throw new Error('Vincent ist nicht erreichbar.');
}

/** @param {string} path @returns {Promise<any>} */
export const get = (path) => request('GET', path);
/** @param {string} path @param {unknown} [body] @returns {Promise<any>} */
export const post = (path, body) => request('POST', path, body);
/** @param {string} path @param {unknown} [body] @returns {Promise<any>} */
export const put = (path, body) => request('PUT', path, body);
/** @param {string} path @param {unknown} [body] @returns {Promise<any>} */
export const patch = (path, body) => request('PATCH', path, body);
/** @param {string} path @returns {Promise<any>} */
export const del = (path) => request('DELETE', path);

// ---------------------------------------------------------------- Formatierung

/**
 * Zahl im deutschen Format (1.234,56). Ungültige Werte → '–'.
 * @param {number|null|undefined} x
 * @param {number} [digits=2] Nachkommastellen
 * @returns {string}
 */
export function fmtNumber(x, digits = 2) {
  const n = typeof x === 'string' ? Number(x) : x;
  if (typeof n !== 'number' || !Number.isFinite(n)) return '–';
  return n.toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/**
 * Preis mit sinnvoller Genauigkeit: ≥ 1000 → 2 Nachkommastellen, ≥ 1 → 2–4, < 1 → 2–4 signifikante Stellen.
 * @param {number|null|undefined} x
 * @returns {string}
 */
export function fmtPrice(x) {
  const n = typeof x === 'string' ? Number(x) : x;
  if (typeof n !== 'number' || !Number.isFinite(n)) return '–';
  const abs = Math.abs(n);
  if (abs >= 1000) return fmtNumber(n, 2);
  if (abs >= 1) return n.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  if (abs === 0) return fmtNumber(0, 2);
  return n.toLocaleString('de-DE', { minimumSignificantDigits: 2, maximumSignificantDigits: 4 });
}

/**
 * HTML-Sonderzeichen maskieren (für innerHTML-Vorlagen).
 * @param {unknown} s
 * @returns {string}
 */
export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

/**
 * Relative Zeitangabe auf Deutsch („gerade eben“, „vor 5 Min.“, „vor 2 Std.“, „vor 3 Tagen“).
 * @param {number|string|Date} ts Unix-Sekunden, Millisekunden, ISO-Text oder Date
 * @param {number} [now] Vergleichszeitpunkt in ms (für Tests)
 * @returns {string}
 */
export function timeAgo(ts, now = Date.now()) {
  let ms;
  if (ts instanceof Date) ms = ts.getTime();
  else if (typeof ts === 'number') ms = ts < 1e12 ? ts * 1000 : ts;
  else if (typeof ts === 'string' && ts) ms = Date.parse(ts);
  else return '';
  if (!Number.isFinite(ms)) return '';
  const sec = Math.round((now - ms) / 1000);
  if (sec < 0) return 'gleich';
  if (sec < 45) return 'gerade eben';
  const min = Math.round(sec / 60);
  if (min < 60) return `vor ${min} Min.`;
  const hours = Math.round(min / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  const days = Math.round(hours / 24);
  if (days === 1) return 'gestern';
  if (days < 30) return `vor ${days} Tagen`;
  const months = Math.round(days / 30);
  if (months < 12) return months === 1 ? 'vor einem Monat' : `vor ${months} Monaten`;
  const years = Math.round(days / 365);
  return years <= 1 ? 'vor einem Jahr' : `vor ${years} Jahren`;
}
