// Reine Hilfsfunktionen der Zentrale: Formatierung, deutsche Bezeichnungen, Daten-Normalisierung.
// Kein DOM-Zugriff – das Modul ist auch in Node (node --test) importierbar.

const NBSP = ' ';
const LOCALE = 'de-DE';
const nfCache = new Map();

function nf(minDigits, maxDigits = minDigits) {
  const key = `${minDigits}:${maxDigits}`;
  if (!nfCache.has(key)) {
    nfCache.set(key, new Intl.NumberFormat(LOCALE, { minimumFractionDigits: minDigits, maximumFractionDigits: maxDigits }));
  }
  return nfCache.get(key);
}

export function toNum(x) {
  if (typeof x === 'number') return Number.isFinite(x) ? x : null;
  if (typeof x === 'string' && x.trim() !== '') {
    const n = Number(x.trim().replace(',', '.'));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function fmtNumber(x, digits = 2) {
  const n = toNum(x);
  return n === null ? '–' : nf(digits).format(n);
}

export function fmtInt(x) {
  const n = toNum(x);
  return n === null ? '–' : nf(0).format(Math.round(n));
}

/** Kurs mit sinnvoller Anzahl Nachkommastellen (BTC 2, XRP 4, Kleinstbeträge signifikant). */
export function fmtPrice(x) {
  const n = toNum(x);
  if (n === null) return '–';
  const a = Math.abs(n);
  if (a === 0) return nf(2).format(0);
  if (a >= 10) return nf(2).format(n);
  if (a >= 1) return nf(3).format(n);
  if (a >= 0.01) return nf(4).format(n);
  return new Intl.NumberFormat(LOCALE, { maximumSignificantDigits: 4 }).format(n);
}

/** Menge ohne überflüssige Nullen (bis 8 Nachkommastellen). */
export function fmtQty(x) {
  const n = toNum(x);
  return n === null ? '–' : nf(0, 8).format(n);
}

export function fmtSigned(x, digits = 2) {
  const n = toNum(x);
  if (n === null) return '–';
  const s = nf(digits).format(Math.abs(n));
  if (n > 0) return `+${s}`;
  if (n < 0) return `−${s}`;
  return s;
}

/** Prozentwert (bereits in Prozent, z. B. 1.23 → "+1,23 %"). */
export function fmtPct(x, digits = 2, signed = true) {
  const n = toNum(x);
  if (n === null) return '–';
  return `${signed ? fmtSigned(n, digits) : nf(digits).format(n)}${NBSP}%`;
}

export function fmtMoney(x, currency = '') {
  const n = toNum(x);
  if (n === null) return '–';
  const cur = String(currency || '').toUpperCase();
  if (/^[A-Z]{3}$/.test(cur)) {
    try {
      return new Intl.NumberFormat(LOCALE, { style: 'currency', currency: cur }).format(n);
    } catch {
      /* unbekannte Währung → unten als Zahl */
    }
  }
  return cur ? `${nf(2).format(n)}${NBSP}${cur}` : nf(2).format(n);
}

/** Zeitstempel (Sekunden, Millisekunden oder ISO-Text) → Date oder null. */
export function toDate(ts) {
  if (ts instanceof Date) return Number.isNaN(ts.getTime()) ? null : ts;
  const n = toNum(ts);
  if (n !== null) {
    if (n <= 0) return null;
    return new Date(n < 1e12 ? n * 1000 : n);
  }
  if (typeof ts === 'string' && ts) {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  return null;
}

const pad = (v) => String(v).padStart(2, '0');

export function fmtTime(ts) {
  const d = toDate(ts);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` : '–';
}

export function fmtDateTime(ts) {
  const d = toDate(ts);
  if (!d) return '–';
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function timeAgo(ts, now = Date.now()) {
  const d = toDate(ts);
  if (!d) return '–';
  const sec = Math.round((now - d.getTime()) / 1000);
  if (sec < 0) return 'gleich';
  if (sec < 45) return 'gerade eben';
  const min = Math.round(sec / 60);
  if (min < 60) return `vor ${min}${NBSP}Min.`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return `vor ${hrs}${NBSP}Std.`;
  const days = Math.round(hrs / 24);
  if (days === 1) return 'gestern';
  if (days < 30) return `vor ${days}${NBSP}Tagen`;
  return fmtDateTime(d).slice(0, 10);
}

/** Restzeit bis ts als "m:ss" bzw. "h:mm:ss"; abgelaufen → "abgelaufen". */
export function fmtCountdown(ts, now = Date.now()) {
  const d = toDate(ts);
  if (!d) return '';
  let s = Math.floor((d.getTime() - now) / 1000);
  if (s <= 0) return 'abgelaufen';
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// --------------------------------------------------------------------------
// Bezeichnungen
// --------------------------------------------------------------------------

export const MODES = [
  { id: 'off', label: 'AUS', name: 'Aus', tone: 'neutral',
    info: 'Vincent analysiert nicht automatisch und handelt nicht.' },
  { id: 'watch', label: 'BEOBACHTEN', name: 'Beobachten', tone: 'info',
    info: 'Vincent analysiert den Markt und meldet Chancen – gehandelt wird nicht.' },
  { id: 'paper', label: 'PAPER', name: 'Paper-Trading', tone: 'good',
    info: 'Handel mit Übungsgeld im TradingView-Paper-Konto – kein echtes Risiko.' },
  { id: 'live', label: 'LIVE', name: 'Echtgeld', tone: 'bad',
    info: 'Echtes Geld über deinen Broker in TradingView – jede Order braucht deine Freigabe.' },
];

export function modeInfo(mode) {
  return MODES.find((m) => m.id === mode) || { id: mode || 'unknown', label: '?', name: 'Unbekannt', tone: 'neutral', info: 'Modus unbekannt.' };
}

export const AUTONOMY = {
  ask: { label: 'Fragt vor jedem Trade', info: 'Jeder Trade braucht dein „Ja“ – per Klick, Stimme oder Telegram.' },
  auto: { label: 'Handelt selbst', info: 'Vincent handelt selbstständig (klein anfangen, nach deinen Risiko-Grenzen). Mit Echtgeld nur, wenn der Echtgeld-Autopilot freigeschaltet ist.' },
};

/** Account-Infos aus TradingView-Status vereinheitlichen (Server- und Controller-Schreibweise). */
export function normalizeAccount(acc) {
  const a = acc && typeof acc === 'object' ? acc : {};
  const kind = ['paper', 'live'].includes(String(a.kind || '').toLowerCase()) ? String(a.kind).toLowerCase() : 'unknown';
  return {
    kind,
    broker: String(a.broker || a.broker_title || a.broker_id || ''),
    label: String(a.label || a.account_name || ''),
    type: String(a.account_type || ''),
    balance: toNum(a.balance),
    equity: toNum(a.equity),
    currency: String(a.currency || ''),
    evidence: Array.isArray(a.evidence) ? a.evidence.map((e) => String(e)) : [],
  };
}

/** Farbton + Texte für das aktive TradingView-Konto. */
export function accountInfo(tv) {
  if (!tv || !tv.connected) {
    return { tone: 'neutral', kind: 'none', title: 'Nicht verbunden', short: 'TradingView getrennt',
      text: 'TradingView ist nicht mit Vincent verbunden – es kann nicht gehandelt werden.' };
  }
  const acc = normalizeAccount(tv.account);
  if (acc.kind === 'paper') {
    return { tone: 'good', kind: 'paper', title: 'Paper Trading', short: 'Paper-Konto',
      text: 'Übungskonto – hier wird kein echtes Geld bewegt.' };
  }
  if (acc.kind === 'live') {
    const who = acc.broker ? ` (${acc.broker})` : '';
    return { tone: 'bad', kind: 'live', title: 'Echtgeld', short: 'ECHTGELD',
      text: `Echtes Broker-Konto${who} – Orders bewegen echtes Geld.` };
  }
  return { tone: 'warn', kind: 'unknown', title: 'Konto unklar', short: 'Konto unklar',
    text: 'Verbunden, aber das aktive Konto ist nicht eindeutig erkennbar – Vincent handelt dann nicht.' };
}

export const VOICE_STATES = {
  idle: { label: 'Bereit', tone: 'good' },
  listening: { label: 'Hört zu', tone: 'accent' },
  thinking: { label: 'Denkt nach', tone: 'info' },
  speaking: { label: 'Spricht', tone: 'accent' },
  muted: { label: 'Stumm', tone: 'warn' },
  off: { label: 'Aus', tone: 'neutral' },
  error: { label: 'Fehler', tone: 'bad' },
};

export function voiceStateInfo(state) {
  return VOICE_STATES[state] || { label: state ? String(state) : 'Unbekannt', tone: 'neutral' };
}

export const TTS_PROVIDERS = [
  { id: 'auto', label: 'Automatisch', info: 'Nimmt die beste verfügbare Stimme: ElevenLabs → OpenAI → Piper → macOS.' },
  { id: 'elevenlabs', label: 'ElevenLabs', badge: 'Am realistischsten',
    info: 'Klingt wie ein echter Mensch – die realistischste Wahl. Braucht einen ElevenLabs-API-Key.' },
  { id: 'openai', label: 'OpenAI', info: 'Sehr natürlich und ausdrucksstark. Braucht einen OpenAI-API-Key.' },
  { id: 'piper', label: 'Piper (lokal)', info: 'Läuft komplett offline auf deinem Mac – solide, aber hörbar künstlich.' },
  { id: 'macos', label: 'macOS-Systemstimme', info: 'Eingebaute Stimme von macOS – funktioniert immer, klingt am künstlichsten.' },
];

/** Welche Einstellung die ausgewählte Stimme eines Anbieters speichert. */
export function voiceSettingKey(provider) {
  return {
    elevenlabs: 'elevenlabs_voice_id',
    openai: 'openai_tts_voice',
    piper: 'piper_voice',
    macos: 'macos_voice',
  }[provider] || null;
}

export const MEMORY_CATEGORIES = {
  profil: 'Profil',
  vorliebe: 'Vorliebe',
  regel: 'Regel',
  fakt: 'Fakt',
  lektion: 'Lektion',
  strategie: 'Strategie',
  wissen: 'Wissen',
  notiz: 'Notiz',
  ereignis: 'Ereignis',
};

export function memoryCategoryLabel(cat) {
  if (!cat) return 'Ohne Kategorie';
  return MEMORY_CATEGORIES[cat] || String(cat).charAt(0).toUpperCase() + String(cat).slice(1);
}

export const STRATEGY_CATEGORIES = ['Trend', 'Momentum', 'Mean-Reversion', 'Ausbruch', 'Volumen', 'Kerzenmuster'];

const PHASES = {
  'aufwärts': 'Aufwärtstrend',
  'abwärts': 'Abwärtstrend',
  'seitwärts': 'Seitwärtsphase',
  volatil: 'Hohe Volatilität',
  hoch: 'Hohe Volatilität',
  ruhig: 'Ruhige Phase',
  niedrig: 'Ruhige Phase',
  trend: 'Trend',
};

/** Marktphase aus der Strategie-Bibliothek ("aufwärts") → deutsches Schlagwort. */
export function phaseLabel(p) {
  const key = String(p || '').toLowerCase();
  if (PHASES[key]) return PHASES[key];
  return key ? key.charAt(0).toUpperCase() + key.slice(1) : '';
}

/** Marktphase einer Analyse: Text oder Regime-Objekt {label, trend}. */
export function regimeText(r) {
  if (!r) return '–';
  if (typeof r === 'string') return r;
  if (typeof r === 'object') return String(r.label || phaseLabel(r.trend) || '–');
  return String(r);
}

export function directionInfo(direction, hasEdge = true) {
  const d = toNum(direction) || 0;
  if (d > 0) return { label: 'Kaufen', tone: 'good', icon: 'arrowUp', hint: hasEdge ? 'Long-Signal' : 'Long-Signal ohne Backtest-Vorteil' };
  if (d < 0) return { label: 'Verkaufen', tone: 'bad', icon: 'arrowDown', hint: hasEdge ? 'Short-Signal' : 'Short-Signal ohne Backtest-Vorteil' };
  return { label: 'Abwarten', tone: 'neutral', icon: 'minus', hint: hasEdge ? 'Kein klares Signal' : 'Kein nachweisbarer Strategie-Vorteil' };
}

export function sideInfo(side) {
  const s = String(side || '').toLowerCase();
  if (s === 'buy' || s === 'long') return { label: 'Kaufen', short: 'Long', tone: 'good' };
  if (s === 'sell' || s === 'short') return { label: 'Verkaufen', short: 'Short', tone: 'bad' };
  return { label: s || '–', short: s || '–', tone: 'neutral' };
}

export const PROPOSAL_STATUS = {
  pending: { label: 'Wartet auf Freigabe', tone: 'warn' },
  approved: { label: 'Freigegeben', tone: 'good' },
  rejected: { label: 'Abgelehnt', tone: 'neutral' },
  executing: { label: 'Wird ausgeführt', tone: 'info' },
  done: { label: 'Ausgeführt', tone: 'good' },
  failed: { label: 'Fehlgeschlagen', tone: 'bad' },
  expired: { label: 'Abgelaufen', tone: 'neutral' },
};

export function proposalStatusInfo(status) {
  return PROPOSAL_STATUS[status] || { label: status ? String(status) : '–', tone: 'neutral' };
}

/** Log-Stufe → Farbton + deutsches Etikett. */
export function levelInfo(level) {
  const l = String(level || '').toLowerCase();
  if (['error', 'critical', 'fatal', 'fehler'].includes(l)) return { tone: 'bad', label: 'Fehler', group: 'error' };
  if (['warning', 'warn', 'warnung'].includes(l)) return { tone: 'warn', label: 'Warnung', group: 'warning' };
  if (['trade', 'order'].includes(l)) return { tone: 'accent', label: 'Trade', group: 'trade' };
  if (['success', 'ok'].includes(l)) return { tone: 'good', label: 'Erfolg', group: 'info' };
  if (l === 'debug') return { tone: 'neutral', label: 'Debug', group: 'info' };
  return { tone: 'info', label: 'Info', group: 'info' };
}

/** notify-Ereignis (kind) → Toast-Ton. */
export function notifyTone(kind) {
  return {
    trade: 'good',
    warning: 'warn',
    error: 'bad',
    info: 'info',
    mode: 'accent',
    report: 'info',
  }[kind] || 'info';
}

/** Berechtigungsstatus (bool oder Electron-Text) → Etikett. */
export function permissionInfo(v) {
  if (v === true) return { tone: 'good', label: 'Erlaubt', granted: true };
  if (v === false) return { tone: 'bad', label: 'Nicht erlaubt', granted: false };
  const s = String(v ?? '').toLowerCase();
  if (['granted', 'authorized', 'allowed'].includes(s)) return { tone: 'good', label: 'Erlaubt', granted: true };
  if (['denied', 'restricted'].includes(s)) return { tone: 'bad', label: 'Nicht erlaubt', granted: false };
  if (['not-determined', 'prompt', 'notdetermined'].includes(s)) return { tone: 'warn', label: 'Noch nicht gefragt', granted: false };
  return { tone: 'neutral', label: 'Unbekannt', granted: null };
}

// --------------------------------------------------------------------------
// Validierung / Normalisierung
// --------------------------------------------------------------------------

export function isHexColor(c) {
  return typeof c === 'string' && /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(c.trim());
}

/** Akzentfarbe prüfen und auf #rrggbb bringen (für <input type=color>). */
export function normalizeAccent(c, fallback = '#7c5cff') {
  if (!isHexColor(c)) return fallback;
  let s = c.trim().toLowerCase();
  if (s.length === 4) s = `#${s[1]}${s[1]}${s[2]}${s[2]}${s[3]}${s[3]}`;
  return s;
}

/** Hex → "r, g, b" (für rgba() in CSS-Variablen). */
export function hexToRgb(c) {
  const s = normalizeAccent(c);
  const n = parseInt(s.slice(1), 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

/** Screenshot (Base64 oder data:-URL) → sichere data:-URL, sonst null. */
export function screenshotSrc(b64) {
  if (typeof b64 !== 'string' || !b64) return null;
  let mime = 'image/png';
  let data = b64.trim();
  const m = /^data:(image\/(?:png|jpeg|webp));base64,(.*)$/s.exec(data);
  if (m) {
    mime = m[1];
    data = m[2];
  }
  data = data.replace(/\s+/g, '');
  if (!data || !/^[A-Za-z0-9+/]+={0,2}$/.test(data)) return null;
  return `data:${mime};base64,${data}`;
}

/** WS-Callbacks können {type, data} oder direkt data liefern – vereinheitlichen. */
export function unwrapEvent(payload) {
  if (payload && typeof payload === 'object' && !Array.isArray(payload) && 'type' in payload && 'data' in payload) {
    return payload.data;
  }
  return payload;
}

/** Stabile Signatur für "hat sich etwas geändert?" */
export function signature(value) {
  try {
    return JSON.stringify(value) ?? '';
  } catch {
    return String(Math.random());
  }
}

/** Positions-Gewinn: pnl wenn vorhanden, sonst aus Kurs berechnet. */
export function positionPnl(p) {
  const pnl = toNum(p?.pnl);
  if (pnl !== null) return pnl;
  const price = toNum(p?.price);
  const entry = toNum(p?.entry);
  const qty = toNum(p?.qty);
  if (price === null || entry === null || qty === null) return null;
  const dir = sideInfo(p.side).tone === 'bad' ? -1 : 1;
  return (price - entry) * qty * dir;
}

/** Markt-Marker aus der API → Lightweight-Charts-Marker (auf vorhandene Kerzen eingerastet). */
export function toChartMarkers(candles, markers, colors = { up: '#2bd99f', down: '#ff5d73', exit: '#a9aec0' }) {
  if (!Array.isArray(candles) || !candles.length || !Array.isArray(markers)) return [];
  const times = candles.map((c) => Number(c.time)).filter(Number.isFinite).sort((a, b) => a - b);
  const first = times[0];
  const last = times[times.length - 1];
  const step = times.length > 1 ? times[1] - times[0] : 0;
  const snap = (t) => {
    let lo = 0;
    let hi = times.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (times[mid] <= t) lo = mid;
      else hi = mid - 1;
    }
    return times[lo];
  };
  const out = [];
  for (const m of markers) {
    const t = toNum(m?.time);
    if (t === null) continue;
    const sec = t > 1e12 ? Math.floor(t / 1000) : t;
    if (sec < first || sec > last + step) continue;
    const buy = sideInfo(m.side).tone === 'good';
    if (m.kind === 'exit') {
      const pnl = toNum(m.pnl);
      out.push({
        time: snap(sec),
        position: buy ? 'aboveBar' : 'belowBar',
        shape: 'circle',
        color: pnl === null ? colors.exit : pnl >= 0 ? colors.up : colors.down,
        text: pnl === null ? 'Ausstieg' : `Ausstieg ${fmtSigned(pnl, 2)}`,
      });
    } else {
      out.push({
        time: snap(sec),
        position: buy ? 'belowBar' : 'aboveBar',
        shape: buy ? 'arrowUp' : 'arrowDown',
        color: buy ? colors.up : colors.down,
        text: buy ? 'Kauf' : 'Verkauf',
      });
    }
  }
  return out.sort((a, b) => a.time - b.time);
}

/** Backtest-Bericht einer Strategie vereinheitlichen (verschachtelte oder flache Kennzahlen). */
export function normalizeBacktestReport(r) {
  const src = r && typeof r === 'object' ? r : {};
  const m = src.oos_metrics || src.metrics || src.full_metrics || src;
  return {
    name: String(src.name || ''),
    label: String(src.label || src.name || '–'),
    weight: toNum(src.weight),
    ret: toNum(m.total_return_pct),
    buyHold: toNum(m.buy_hold_pct),
    trades: toNum(m.trades),
    winRate: toNum(m.win_rate_pct),
    profitFactor: toNum(m.profit_factor),
    maxDrawdown: toNum(m.max_drawdown_pct),
    sharpe: toNum(m.sharpe),
  };
}

/** Score (−1…+1) → Position eines Markers auf einer 0…100-%-Skala. */
export function scorePercent(score) {
  const n = toNum(score);
  if (n === null) return 50;
  return Math.max(0, Math.min(100, (n + 1) * 50));
}
