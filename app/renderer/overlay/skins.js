/**
 * Vincent – Avatar-Skins (Canvas 2D).
 *
 * Jeder Skin ist eine Renderer-Funktion `(ctx, t, size, state, level, accent, extra)`:
 *  - ctx:    CanvasRenderingContext2D; der Ursprung liegt bereits in der Avatar-Mitte (CSS-Pixel)
 *  - t:      Zeit in Sekunden
 *  - size:   Avatar-Durchmesser in CSS-Pixeln; Glow und Effekte reichen bis ca. CANVAS_SCALE × size / 2
 *  - state:  Zustandsname ("idle", "listening", …) oder Visual-Objekt aus normalizeVisual()
 *  - level:  geglätteter Audiopegel 0..1 (Sprechen: TTS, Zuhören: Mikrofon)
 *  - accent: Akzentfarbe "#rrggbb" – färbt jeden Skin ein
 *  - extra:  optional {image?: HTMLImageElement, reduced?: boolean}
 *
 * paintAvatar() übernimmt Verschiebung, Skin und die gemeinsamen Zustandseffekte (Zuhören-Ring,
 * Denk-Partikel, Stumm-Symbol, Warnpunkt, Verbindungs-Spinner). Das Modul hat beim Import keine
 * DOM-Seiteneffekte – die Zentrale (Skin-Galerie) und die Node-Tests importieren es direkt.
 */

const TAU = Math.PI * 2;
const WHITE = [255, 255, 255];
const INK = [7, 6, 18];
const HOLO = [72, 228, 255];
const WARN_AMBER = [255, 181, 71];
const WARN_RED = [255, 77, 94];

export const DEFAULT_SKIN = 'mensch';
export const DEFAULT_ACCENT = '#7c5cff';
/** Canvas-Kantenlänge relativ zum Avatar-Durchmesser (Platz für Glow und Ringe). */
export const CANVAS_SCALE = 1.5;
/** Alle visuellen Zustände des Avatars. */
export const STATE_KEYS = Object.freeze(['idle', 'listening', 'thinking', 'speaking', 'muted', 'off', 'error', 'disconnected']);

/** Skin-Liste für Galerie und Einstellungen. */
export const SKINS = Object.freeze(
  [
    { id: 'mensch', name: 'Mensch', description: 'Kleiner animierter Mensch mit Headset – winkt, redet mit Händen, lauscht und grübelt.' },
    { id: 'nebula', name: 'Nebula', description: 'Wirbelnde Energiekugel in Violett und Cyan mit sanften Partikeln.' },
    { id: 'hologramm', name: 'Hologramm', description: 'Rotierende Drahtgitter-Kugel in Cyan mit Scanlines.' },
    { id: 'neon', name: 'Neon', description: 'Leuchtender Neonring mit Equalizer, der beim Sprechen tanzt.' },
    { id: 'glas', name: 'Glas', description: 'Minimalistische Milchglas-Kugel mit Lichtbrechung.' },
    { id: 'roboter', name: 'Roboter', description: 'Freundliches Roboter-Gesicht mit blinzelnden Augen.' },
    { id: 'eigenes', name: 'Eigenes Bild', description: 'Dein eigenes Bild im Kreis mit animiertem Statusring.' },
  ].map((s) => Object.freeze(s)),
);

const ALIASES = { human: 'mensch', person: 'mensch', figur: 'mensch', hologram: 'hologramm', glass: 'glas', robot: 'roboter', custom: 'eigenes', bild: 'eigenes' };

/**
 * Skin-ID normalisieren (unbekannt → Standard-Skin).
 * @param {unknown} id
 * @returns {string}
 */
export function normalizeSkinId(id) {
  const s = String(id ?? '').trim().toLowerCase();
  const key = ALIASES[s] || s;
  return SKINS.some((k) => k.id === key) ? key : DEFAULT_SKIN;
}

// ---------------------------------------------------------------- Mathe

export function clamp(x, a = 0, b = 1) {
  return x < a ? a : x > b ? b : x;
}

export function lerp(a, b, k) {
  return a + (b - a) * k;
}

export function smoothstep(e0, e1, x) {
  const u = clamp((x - e0) / (e1 - e0));
  return u * u * (3 - 2 * u);
}

function fract(x) {
  return x - Math.floor(x);
}

/** Deterministischer Pseudozufall 0..1 für eine Zahl. */
export function hash(n) {
  return fract(Math.sin(n * 127.1 + 311.7) * 43758.5453123);
}

/** Weiches 1D-Rauschen 0..1. */
export function noise1(x) {
  const i = Math.floor(x);
  const f = x - i;
  const u = f * f * (3 - 2 * f);
  return hash(i) * (1 - u) + hash(i + 1) * u;
}

// ---------------------------------------------------------------- Farben

/**
 * "#rgb"/"#rrggbb" → [r, g, b]; ungültig → Fallback (Standard-Akzent).
 * @param {unknown} hex
 * @param {number[]} [fallback]
 * @returns {number[]}
 */
export function hexToRgb(hex, fallback = [124, 92, 255]) {
  if (typeof hex !== 'string') return fallback.slice();
  let h = hex.trim().replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(h)) h = h.split('').map((c) => c + c).join('');
  if (!/^[0-9a-f]{6}$/i.test(h)) return fallback.slice();
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** @param {unknown} hex @returns {boolean} */
export function isHexColor(hex) {
  return typeof hex === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(hex.trim());
}

function rgbToHsl(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  return [h, s, l];
}

function hslToRgb(h, s, l) {
  const hh = (((h % 360) + 360) % 360) / 360;
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const f = (t) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  return [f(hh + 1 / 3) * 255, f(hh) * 255, f(hh - 1 / 3) * 255];
}

/** Farbton drehen (Grad) und optional aufhellen. */
export function shiftHue(rgb, deg, light = 0) {
  const [h, s, l] = rgbToHsl(rgb[0], rgb[1], rgb[2]);
  return hslToRgb(h + deg, s, clamp(l + light));
}

export function mixRgb(a, b, k) {
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
}

/** [r,g,b] + Alpha → CSS-Farbe. */
export function rgba(c, a) {
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${clamp(a).toFixed(3)})`;
}

/** [r,g,b] → "#rrggbb". */
export function rgbToHex(c) {
  return `#${c.map((x) => Math.round(clamp(x, 0, 255)).toString(16).padStart(2, '0')).join('')}`;
}

/**
 * Farbpalette eines Skins aus der Akzentfarbe; berücksichtigt Grau (aus/getrennt) und Abdunkeln (stumm).
 * @param {string} accent
 * @param {{grey?: number, dim?: number}} v
 */
export function makePalette(accent, v = {}) {
  const grey = clamp(v.grey || 0);
  const dim = clamp(v.dim || 0);
  const m = 1 - dim * 0.45;
  const tone = (c) => {
    const L = c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114;
    return [(c[0] + (L - c[0]) * grey) * m, (c[1] + (L - c[1]) * grey) * m, (c[2] + (L - c[2]) * grey) * m];
  };
  const a = hexToRgb(accent);
  return {
    tone,
    raw: a,
    accent: tone(a),
    cool: tone(shiftHue(a, -62, 0.06)),
    warm: tone(shiftHue(a, 46, 0.03)),
    light: tone(mixRgb(a, WHITE, 0.7)),
    deep: tone(mixRgb(a, INK, 0.84)),
    ink: tone(INK),
    white: tone(WHITE),
  };
}

// ---------------------------------------------------------------- Visual-Zustand

/**
 * Zustand (Name oder Visual-Objekt) in ein vollständiges Visual-Objekt überführen.
 * Visual: {name, w: {state: Gewicht 0..1}, hover, pulse, clock?, grey, dim}
 * @param {string|object} state
 */
export function normalizeVisual(state) {
  if (state && typeof state === 'object' && state.w) return finishVisual(state);
  const name = typeof state === 'string' && STATE_KEYS.includes(state) ? state : 'idle';
  const w = {};
  for (const k of STATE_KEYS) w[k] = k === name ? 1 : 0;
  return finishVisual({ name, w, hover: 0, pulse: 0 });
}

/** Abgeleitete Werte (grey/dim) aus den Zustandsgewichten berechnen. */
export function finishVisual(v) {
  const w = v.w;
  v.hover = v.hover || 0;
  v.pulse = v.pulse || 0;
  v.grey = clamp(Math.max((w.off || 0) * 0.82, (w.error || 0) * 0.72, (w.disconnected || 0) * 0.9));
  v.dim = clamp((w.muted || 0) * 0.55 + v.grey * 0.25);
  return v;
}

// ---------------------------------------------------------------- Zeichenhilfen

function circle(ctx, x, y, r) {
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0, r), 0, TAU);
}

/** Weicher radialer Lichtfleck (r0 → r1, Alpha a → 0). */
function glow(ctx, x, y, r0, r1, c, a) {
  if (a <= 0.003 || r1 <= 0) return;
  const g = ctx.createRadialGradient(x, y, Math.max(0, r0), x, y, r1);
  g.addColorStop(0, rgba(c, a));
  g.addColorStop(0.45, rgba(c, a * 0.38));
  g.addColorStop(1, rgba(c, 0));
  ctx.fillStyle = g;
  circle(ctx, x, y, r1);
  ctx.fill();
}

function rrect(ctx, x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

/** Konischer Verlauf (mit linearem Fallback für ältere Canvas-Implementierungen). */
function conic(ctx, angle, R, stops) {
  const g = typeof ctx.createConicGradient === 'function'
    ? ctx.createConicGradient(angle, 0, 0)
    : ctx.createLinearGradient(-R, -R, R, R);
  for (const [o, c, a] of stops) g.addColorStop(o, rgba(c, a));
  return g;
}

function ring(ctx, r, style, width) {
  ctx.strokeStyle = style;
  ctx.lineWidth = width;
  circle(ctx, 0, 0, r);
  ctx.stroke();
}

function specular(ctx, r, P, a) {
  if (a <= 0.003) return;
  ctx.save();
  ctx.translate(-r * 0.3, -r * 0.44);
  ctx.rotate(-0.5);
  ctx.scale(1, 0.6);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.44);
  g.addColorStop(0, rgba(P.white, a));
  g.addColorStop(1, rgba(P.white, 0));
  ctx.fillStyle = g;
  circle(ctx, 0, 0, r * 0.44);
  ctx.fill();
  ctx.restore();
}

function rim(ctx, r, P, top, bottom, col) {
  const g = ctx.createLinearGradient(0, -r, 0, r);
  g.addColorStop(0, rgba(P.white, top));
  g.addColorStop(0.5, rgba(P.white, 0.03));
  g.addColorStop(1, rgba(col || P.cool, bottom));
  ring(ctx, r - 0.6, g, 1.2);
}

function particles(ctx, t, c, R, P, v, n, reduced) {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const cols = [P.cool, P.light, P.warm];
  for (let i = 0; i < n; i++) {
    const h1 = hash(i * 3.1);
    const h2 = hash(i * 7.7 + 1);
    const h3 = hash(i * 1.3 + 5);
    const h4 = hash(i * 9.2 + 2);
    const dir = h3 > 0.5 ? 1 : -1;
    const a = h1 * TAU + c * (0.08 + 0.22 * h2) * dir;
    const rr = R * (0.8 + 0.42 * h4) + Math.sin(t * 0.7 + i) * R * 0.03;
    const tw = reduced ? 0.6 : 0.5 + 0.5 * Math.sin(t * (1.1 + 2 * h2) + i * 1.7);
    const alpha = (0.12 + 0.55 * tw) * (1 - v.dim * 0.6) * (0.75 + 0.25 * v.hover);
    ctx.fillStyle = rgba(cols[i % 3], alpha);
    circle(ctx, Math.cos(a) * rr, Math.sin(a) * rr * 0.92, (0.55 + 1.25 * h2) * (R / 66));
    ctx.fill();
  }
  ctx.restore();
}

/** Audiowelle (mehrere überlagerte Sinuskurven mit weichem Rand). */
function waveform(ctx, t, r, cols, weight, lv) {
  ctx.lineCap = 'round';
  for (let k = 0; k < cols.length; k++) {
    const amp = r * (0.05 + 0.34 * lv) * (1 - k * 0.22);
    const freq = 1.5 + k * 0.75;
    const ph = t * (5.5 + k * 1.9) + k * 1.3;
    ctx.beginPath();
    for (let i = 0; i <= 44; i++) {
      const u = (i / 44) * 2 - 1;
      const env = Math.pow(1 - u * u, 1.6);
      const x = u * r * 0.84;
      const y = Math.sin(u * freq * Math.PI + ph) * amp * env;
      if (i) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    ctx.strokeStyle = rgba(cols[k], 0.14 * weight);
    ctx.lineWidth = r * 0.13;
    ctx.stroke();
    ctx.strokeStyle = rgba(cols[k], (0.9 - k * 0.2) * weight);
    ctx.lineWidth = Math.max(1, r * 0.032);
    ctx.stroke();
  }
}

function breathing(t, extra) {
  return Math.sin(t * 1.3) * (extra.reduced ? 0.35 : 1);
}

// ---------------------------------------------------------------- Skins

const NEBULA_BLOBS = [
  { col: 'accent', sx: 0.62, sy: 0.81, p: 0.0, ax: 0.45, ay: 0.38, r: 0.85, a: 0.9 },
  { col: 'cool', sx: -0.5, sy: -0.66, p: 2.1, ax: 0.5, ay: 0.42, r: 0.72, a: 0.75 },
  { col: 'warm', sx: 0.41, sy: -0.53, p: 4.2, ax: 0.42, ay: 0.46, r: 0.6, a: 0.55 },
  { col: 'cool', sx: -0.33, sy: 0.47, p: 5.3, ax: 0.3, ay: 0.3, r: 0.42, a: 0.5 },
];

/** Nebula: wirbelnde Energiekugel (Standard). */
function nebula(ctx, t, size, v, level, accent, extra) {
  const P = makePalette(accent, v);
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const speak = w.speaking * lv;
  const listen = w.listening * lv;
  const breath = breathing(t, extra);
  const c = v.clock ?? t;
  const r = R * 0.76 * (1 + 0.014 * breath + 0.07 * speak + 0.035 * listen + 0.02 * v.hover);

  glow(ctx, 0, 0, r * 0.62, R * 1.45, P.accent, 0.34 + 0.05 * breath + 0.3 * speak + 0.14 * listen + 0.1 * v.hover);
  glow(ctx, Math.cos(c * 0.5) * R * 0.14, Math.sin(c * 0.37) * R * 0.12, r * 0.3, R * 1.25, P.cool, 0.15 + 0.12 * speak);

  ctx.save();
  circle(ctx, 0, 0, r);
  ctx.clip();
  const g = ctx.createRadialGradient(-r * 0.3, -r * 0.35, r * 0.05, 0, 0, r);
  g.addColorStop(0, rgba(mixRgb(P.accent, P.deep, 0.3), 1));
  g.addColorStop(0.6, rgba(mixRgb(P.accent, P.deep, 0.72), 1));
  g.addColorStop(1, rgba(P.deep, 1));
  ctx.fillStyle = g;
  ctx.fillRect(-r, -r, 2 * r, 2 * r);

  ctx.globalCompositeOperation = 'screen';
  const boost = 0.85 + 0.3 * speak + 0.15 * listen;
  for (const b of NEBULA_BLOBS) {
    const x = Math.cos(c * b.sx + b.p) * r * b.ax;
    const y = Math.sin(c * b.sy + b.p) * r * b.ay;
    glow(ctx, x, y, 0, r * b.r, P[b.col], b.a * boost);
  }
  // Spiralarme
  ctx.lineCap = 'round';
  for (let k = 0; k < 3; k++) {
    ctx.save();
    ctx.rotate(c * 0.42 + (k * TAU) / 3);
    ctx.beginPath();
    for (let i = 0; i <= 22; i++) {
      const u = i / 22;
      const ang = u * 2.7;
      const rr = r * (0.1 + 0.82 * u);
      const x = Math.cos(ang) * rr;
      const y = Math.sin(ang) * rr * 0.86;
      if (i) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    ctx.strokeStyle = rgba(P.light, 0.06 + 0.05 * speak);
    ctx.lineWidth = r * 0.17;
    ctx.stroke();
    ctx.strokeStyle = rgba(P.light, 0.11 + 0.1 * speak);
    ctx.lineWidth = r * 0.035;
    ctx.stroke();
    ctx.restore();
  }
  glow(ctx, 0, 0, 0, r * 0.62, P.white, 0.14 + 0.4 * speak + 0.18 * listen + 0.1 * w.thinking);
  if (w.speaking > 0.01) waveform(ctx, t, r, [P.white, P.cool, P.warm], w.speaking, lv);
  ctx.restore();

  specular(ctx, r, P, 0.36);
  rim(ctx, r, P, 0.42, 0.5);
  particles(ctx, t, c, R, P, v, 18, extra.reduced);
}

/** Projektionssockel unter dem Hologramm. */
function emitter(ctx, R, H, flick) {
  const y = R * 0.98;
  ctx.save();
  ctx.translate(0, y);
  ctx.scale(1, 0.22);
  glow(ctx, 0, 0, 0, R * 0.62, H, 0.42 * flick);
  ring(ctx, R * 0.4, rgba(H, 0.6 * flick), 3);
  ctx.restore();
  const g = ctx.createLinearGradient(0, y, 0, -R * 0.45);
  g.addColorStop(0, rgba(H, 0.16 * flick));
  g.addColorStop(1, rgba(H, 0));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.moveTo(-R * 0.4, y);
  ctx.lineTo(R * 0.4, y);
  ctx.lineTo(R * 0.74, -R * 0.45);
  ctx.lineTo(-R * 0.74, -R * 0.45);
  ctx.closePath();
  ctx.fill();
}

/** Hologramm: rotierende Drahtgitter-Kugel mit Scanlines. */
function hologramm(ctx, t, size, v, level, accent, extra) {
  const P = makePalette(accent, v);
  const H = P.tone(mixRgb(HOLO, P.raw, 0.25));
  const Hl = P.tone(mixRgb(HOLO, WHITE, 0.55));
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const speak = w.speaking * lv;
  const listen = w.listening * lv;
  const c = v.clock ?? t;
  const flick = extra.reduced ? 1 : 0.9 + 0.1 * noise1(t * 13);
  const r = R * 0.68 * (1 + 0.05 * speak + 0.03 * listen + 0.01 * breathing(t, extra));

  glow(ctx, 0, 0, r * 0.3, R * 1.42, H, (0.2 + 0.25 * speak + 0.1 * listen + 0.08 * v.hover) * flick);
  emitter(ctx, R, H, flick);
  glow(ctx, 0, 0, 0, r * 1.04, H, 0.13 * flick);

  const yaw = c * 0.5;
  const tilt = 0.38;
  const ct = Math.cos(tilt);
  const st = Math.sin(tilt);
  const project = (th, ph, extraR = 0) => {
    const k = 1 + extraR + speak * 0.12 * Math.sin(3 * th + 2 * ph + t * 8) + listen * 0.05 * Math.sin(5 * ph - t * 5);
    const sx = Math.sin(th) * Math.cos(ph + yaw) * r * k;
    const sy = Math.cos(th) * r * k;
    const sz = Math.sin(th) * Math.sin(ph + yaw) * r * k;
    return [sx, sy * ct - sz * st, sy * st + sz * ct];
  };
  const lines = [];
  for (let m = 0; m < 8; m++) {
    const ph = (m * Math.PI) / 8;
    const pts = [];
    for (let i = 0; i <= 36; i++) pts.push(project((i / 36) * TAU, ph));
    lines.push(pts);
  }
  for (let p = 1; p < 6; p++) {
    const th = (p * Math.PI) / 6;
    const pts = [];
    for (let i = 0; i <= 36; i++) pts.push(project(th, (i / 36) * TAU));
    lines.push(pts);
  }
  ctx.lineCap = 'round';
  for (const front of [false, true]) {
    ctx.beginPath();
    for (const pts of lines) {
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1];
        const b = pts[i];
        if ((a[2] + b[2] < 0) === front) {
          ctx.moveTo(a[0], a[1]);
          ctx.lineTo(b[0], b[1]);
        }
      }
    }
    ctx.strokeStyle = rgba(H, (front ? 0.82 : 0.16) * flick);
    ctx.lineWidth = front ? 1.1 : 0.8;
    ctx.stroke();
  }
  // Knotenpunkte (nur Vorderseite)
  ctx.beginPath();
  for (let m = 0; m < 16; m++) {
    for (let p = 1; p < 6; p++) {
      const q = project((p * Math.PI) / 6, (m * Math.PI) / 8);
      if (q[2] < 0) ctx.rect(q[0] - 1, q[1] - 1, 2, 2);
    }
  }
  ctx.fillStyle = rgba(Hl, 0.9 * flick);
  ctx.fill();
  // Äquator als audioreaktiver Ring
  const eq = [];
  for (let i = 0; i <= 64; i++) {
    const ph = (i / 64) * TAU;
    eq.push(project(Math.PI / 2, ph, 0.04 + (speak * 0.14 + listen * 0.06) * Math.sin(ph * 8 + t * 10)));
  }
  for (const front of [false, true]) {
    ctx.beginPath();
    for (let i = 1; i < eq.length; i++) {
      if ((eq[i - 1][2] + eq[i][2] < 0) === front) {
        ctx.moveTo(eq[i - 1][0], eq[i - 1][1]);
        ctx.lineTo(eq[i][0], eq[i][1]);
      }
    }
    ctx.strokeStyle = rgba(Hl, (front ? 0.95 : 0.25) * flick);
    ctx.lineWidth = front ? 1.6 + speak * 1.5 : 1;
    ctx.stroke();
  }
  // Scanlines + Lichtband
  ctx.save();
  circle(ctx, 0, 0, r * 1.12);
  ctx.clip();
  ctx.beginPath();
  const off = extra.reduced ? 0 : (t * 6) % 3;
  for (let y = -r * 1.12 + off; y < r * 1.12; y += 3) ctx.rect(-r * 1.12, y, r * 2.24, 1);
  ctx.fillStyle = rgba(H, 0.07 * flick);
  ctx.fill();
  const sy = fract(t * 0.32) * r * 3.2 - r * 1.6;
  const g = ctx.createLinearGradient(0, sy - r * 0.22, 0, sy + r * 0.22);
  g.addColorStop(0, rgba(Hl, 0));
  g.addColorStop(0.5, rgba(Hl, 0.2 * flick));
  g.addColorStop(1, rgba(Hl, 0));
  ctx.fillStyle = g;
  ctx.fillRect(-r * 1.12, sy - r * 0.22, r * 2.24, r * 0.44);
  ctx.restore();
}

/** Neon: Neonring mit Equalizer-Balken. */
function neon(ctx, t, size, v, level, accent, extra) {
  const P = makePalette(accent, v);
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const speak = w.speaking * lv;
  const listen = w.listening * lv;
  const c = v.clock ?? t;
  const ringR = R * 0.78;
  const lw = R * (0.055 + 0.035 * speak + 0.015 * listen);

  glow(ctx, 0, 0, ringR * 0.75, R * 1.45, P.accent, 0.26 + 0.3 * speak + 0.12 * listen + 0.1 * v.hover);
  const g = ctx.createRadialGradient(0, -ringR * 0.35, 0, 0, 0, ringR);
  g.addColorStop(0, rgba(mixRgb(P.deep, P.accent, 0.2), 0.96));
  g.addColorStop(1, rgba(P.ink, 0.97));
  ctx.fillStyle = g;
  circle(ctx, 0, 0, ringR);
  ctx.fill();

  // Equalizer
  const N = 9;
  const bw = R * 0.068;
  const gap = R * 0.046;
  const x0 = -(N * bw + (N - 1) * gap) / 2;
  const rest = Math.max(0, 1 - w.speaking - w.listening - w.thinking);
  ctx.beginPath();
  for (let i = 0; i < N; i++) {
    const ci = Math.abs(i - (N - 1) / 2) / ((N - 1) / 2);
    const shape = 1 - ci * ci * 0.6;
    const idle = 0.06 + 0.05 * (0.5 + 0.5 * Math.sin(t * 2 + i * 0.8));
    const sp = (0.06 + lv * (0.35 + 0.65 * noise1(t * 8 + i * 2.7)) * 0.46) * shape;
    const th = 0.07 + 0.2 * (0.5 + 0.5 * Math.sin(c * 4.5 - i * 0.85));
    const hh = R * clamp(idle * rest + sp * (w.speaking + w.listening * 0.8) + th * w.thinking, 0.03, 0.5);
    rrect(ctx, x0 + i * (bw + gap), -hh, bw, hh * 2, bw / 2);
  }
  const bg = ctx.createLinearGradient(0, -R * 0.5, 0, R * 0.5);
  bg.addColorStop(0, rgba(P.warm, 1));
  bg.addColorStop(0.5, rgba(mixRgb(P.cool, P.white, 0.35), 1));
  bg.addColorStop(1, rgba(P.accent, 1));
  ctx.save();
  ctx.shadowColor = rgba(P.cool, 0.8);
  ctx.shadowBlur = R * 0.14;
  ctx.fillStyle = bg;
  ctx.fill();
  ctx.restore();

  // Glasreflex auf der Scheibe
  ctx.save();
  circle(ctx, 0, 0, ringR);
  ctx.clip();
  const rg = ctx.createLinearGradient(0, -ringR, 0, 0);
  rg.addColorStop(0, rgba(P.white, 0.1));
  rg.addColorStop(1, rgba(P.white, 0));
  ctx.fillStyle = rg;
  ctx.beginPath();
  ctx.ellipse(0, -ringR * 0.55, ringR * 0.85, ringR * 0.5, 0, 0, TAU);
  ctx.fill();
  ctx.restore();

  // Neonröhre
  const tube = conic(ctx, c * 0.7, R, [
    [0, P.accent, 1],
    [0.33, P.warm, 1],
    [0.66, P.cool, 1],
    [1, P.accent, 1],
  ]);
  ctx.save();
  ctx.globalAlpha = 0.18;
  ring(ctx, ringR, tube, lw * 3.4);
  ctx.globalAlpha = 1;
  ctx.shadowColor = rgba(P.accent, 0.95);
  ctx.shadowBlur = R * 0.22;
  ring(ctx, ringR, tube, lw);
  ctx.restore();
  ring(ctx, ringR, rgba(P.white, 0.55 - 0.25 * v.dim), Math.max(1, lw * 0.28));
}

/** Glas: Milchglas-Kugel mit Lichtbrechung. */
function glas(ctx, t, size, v, level, accent, extra) {
  const P = makePalette(accent, v);
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const speak = w.speaking * lv;
  const listen = w.listening * lv;
  const c = v.clock ?? t;
  const r = R * 0.78 * (1 + 0.04 * speak + 0.02 * listen + 0.008 * breathing(t, extra));

  glow(ctx, 0, r * 0.25, r * 0.3, R * 1.35, P.accent, 0.2 + 0.25 * speak + 0.1 * listen + 0.08 * v.hover);

  ctx.save();
  circle(ctx, 0, 0, r);
  ctx.clip();
  const base = ctx.createRadialGradient(0, -r * 0.25, 0, 0, 0, r);
  base.addColorStop(0, rgba(mixRgb(P.deep, P.white, 0.12), 0.55));
  base.addColorStop(1, rgba(P.deep, 0.72));
  ctx.fillStyle = base;
  ctx.fillRect(-r, -r, 2 * r, 2 * r);
  // gebrochene "Umgebung" in der Kugel
  ctx.globalCompositeOperation = 'screen';
  glow(ctx, Math.cos(c * 0.45) * r * 0.36, r * 0.36 + Math.sin(c * 0.6) * r * 0.12, 0, r * 0.75, P.accent, 0.62 + 0.25 * speak);
  glow(ctx, -Math.cos(c * 0.38) * r * 0.42, r * 0.12 + Math.sin(c * 0.5) * r * 0.1, 0, r * 0.58, P.cool, 0.42 + 0.2 * listen);
  glow(ctx, 0, 0, 0, r * 0.5, P.white, 0.06 + 0.25 * listen + 0.12 * w.thinking);
  // Sprechen: Wellenringe von innen
  if (w.speaking > 0.01) {
    for (let k = 0; k < 3; k++) {
      const p = fract(t * 0.9 + k / 3);
      ring(ctx, r * (0.12 + 0.88 * p), rgba(P.light, w.speaking * (0.12 + lv * 0.6) * (1 - p)), 1 + lv * 2.5);
    }
  }
  ctx.globalCompositeOperation = 'source-over';
  // Fresnel-Rand
  const fr = ctx.createRadialGradient(0, 0, r * 0.7, 0, 0, r);
  fr.addColorStop(0, rgba(P.white, 0));
  fr.addColorStop(1, rgba(P.white, 0.26));
  ctx.fillStyle = fr;
  ctx.fillRect(-r, -r, 2 * r, 2 * r);
  ctx.restore();

  // Kaustik unten
  ctx.save();
  ctx.lineCap = 'round';
  const cg = ctx.createLinearGradient(-r, 0, r, 0);
  cg.addColorStop(0, rgba(P.cool, 0));
  cg.addColorStop(0.5, rgba(mixRgb(P.cool, P.white, 0.4), 0.75));
  cg.addColorStop(1, rgba(P.accent, 0));
  ctx.strokeStyle = cg;
  ctx.lineWidth = r * 0.07;
  ctx.beginPath();
  ctx.arc(0, 0, r * 0.86, Math.PI * 0.2, Math.PI * 0.8);
  ctx.stroke();
  ctx.restore();

  // Glanzlichter + Rand
  specular(ctx, r, P, 0.62);
  glow(ctx, -r * 0.44, -r * 0.5, 0, r * 0.09, P.white, 0.95);
  rim(ctx, r, P, 0.5, 0.25, P.light);
}

function blinkAmount(t) {
  const period = 4.2;
  const k = Math.floor(t / period);
  const local = t - k * period;
  const start = 2.2 + hash(k) * 1.4;
  const dur = 0.17;
  let b = 0;
  const d = local - start;
  if (d >= 0 && d < dur) b = Math.sin((d / dur) * Math.PI);
  if (hash(k + 7.3) > 0.7) {
    const d2 = d - 0.28;
    if (d2 >= 0 && d2 < dur) b = Math.max(b, Math.sin((d2 / dur) * Math.PI));
  }
  return b;
}

/** Sprunghafte, kurz überblendete Blickrichtung (-0.5..0.5). */
function saccade(t, seed, period) {
  const k = Math.floor(t / period);
  const u = smoothstep(0, 0.14, t - k * period);
  const a = hash(k - 1 + seed) - 0.5;
  const b = hash(k + seed) - 0.5;
  return a + (b - a) * u;
}

/** Roboter: freundliches Roboter-Gesicht. */
function roboter(ctx, t, size, v, level, accent, extra) {
  const P = makePalette(accent, v);
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const speak = w.speaking * lv;
  const listen = w.listening * lv;
  const sleepy = clamp(w.off + w.error + w.disconnected);
  const bob = extra.reduced ? 0 : Math.sin(t * 1.6) * R * 0.018 * (1 - sleepy) + speak * R * 0.012 * Math.sin(t * 13);

  glow(ctx, 0, 0, R * 0.5, R * 1.4, P.accent, 0.26 + 0.2 * speak + 0.12 * listen + 0.08 * v.hover);
  ctx.save();
  ctx.translate(0, bob);
  const hw = R * 1.3;
  const hh = R * 1.08;
  const hy = R * 0.08;
  const top = hy - hh / 2;

  // Antenne
  ctx.lineCap = 'round';
  ctx.strokeStyle = rgba(P.tone([150, 156, 186]), 0.9);
  ctx.lineWidth = R * 0.045;
  ctx.beginPath();
  ctx.moveTo(0, top + 1);
  ctx.lineTo(0, top - R * 0.18);
  ctx.stroke();
  const pulse = 0.55 + 0.45 * Math.sin(t * (2 + 6 * w.thinking));
  glow(ctx, 0, top - R * 0.22, 0, R * 0.24, P.accent, 0.45 * pulse + listen * 0.5 + w.thinking * 0.2);
  ctx.fillStyle = rgba(mixRgb(P.accent, P.white, 0.35), 1);
  circle(ctx, 0, top - R * 0.22, R * 0.068);
  ctx.fill();

  // Ohren
  for (const s of [-1, 1]) {
    const ex = s * (hw / 2 + R * 0.015);
    rrect(ctx, ex - R * 0.065, hy - R * 0.17, R * 0.13, R * 0.34, R * 0.06);
    ctx.fillStyle = rgba(mixRgb(P.tone([44, 48, 70]), P.accent, 0.35), 1);
    ctx.fill();
  }

  // Kopf
  rrect(ctx, -hw / 2, top, hw, hh, R * 0.36);
  const hg = ctx.createLinearGradient(0, top, 0, top + hh);
  hg.addColorStop(0, rgba(P.tone(mixRgb([62, 68, 98], P.raw, 0.16)), 1));
  hg.addColorStop(1, rgba(P.tone(mixRgb([20, 22, 36], P.raw, 0.08)), 1));
  ctx.fillStyle = hg;
  ctx.fill();
  const rg = ctx.createLinearGradient(0, top, 0, top + hh);
  rg.addColorStop(0, rgba(P.white, 0.4));
  rg.addColorStop(0.5, rgba(P.white, 0.05));
  rg.addColorStop(1, rgba(P.accent, 0.6));
  ctx.strokeStyle = rg;
  ctx.lineWidth = 1.3;
  ctx.stroke();

  // Visier
  const vw = hw * 0.8;
  const vh = hh * 0.66;
  const vy = hy - R * 0.02;
  rrect(ctx, -vw / 2, vy - vh / 2, vw, vh, R * 0.26);
  const vg = ctx.createLinearGradient(0, vy - vh / 2, 0, vy + vh / 2);
  vg.addColorStop(0, rgba(P.tone([12, 14, 28]), 1));
  vg.addColorStop(1, rgba(P.tone([4, 5, 12]), 1));
  ctx.fillStyle = vg;
  ctx.fill();
  ctx.strokeStyle = rgba(P.white, 0.07);
  ctx.lineWidth = 1;
  ctx.stroke();

  // Augen
  const eyeCol = mixRgb(P.cool, P.white, 0.25);
  const blink = extra.reduced ? 0 : blinkAmount(t);
  const awake = 1 - sleepy;
  const lookX = saccade(t, 1.7, 2.6) * R * 0.1 * (w.idle + w.speaking + w.muted) + R * 0.06 * w.thinking;
  const lookY = saccade(t, 4.1, 3.1) * R * 0.05 * w.idle - R * 0.07 * w.thinking;
  const open = (1 - blink) * (1 - 0.55 * w.muted) * (1 - 0.88 * sleepy) * (1 + 0.14 * w.listening);
  const ew = R * 0.2 * (1 + 0.08 * w.listening);
  const eh = Math.max(R * 0.03, R * 0.27 * open);
  for (const s of [-1, 1]) {
    const ex = s * R * 0.27 + lookX;
    const ey = vy - R * 0.06 + lookY;
    glow(ctx, ex, ey, 0, R * 0.28, P.cool, (0.32 + 0.2 * listen) * awake + 0.08);
    rrect(ctx, ex - ew / 2, ey - eh / 2, ew, eh, Math.min(ew, eh) / 2);
    ctx.fillStyle = rgba(eyeCol, 0.55 + 0.45 * awake);
    ctx.fill();
    if (eh > R * 0.12) {
      ctx.fillStyle = rgba(P.white, 0.85);
      circle(ctx, ex - ew * 0.18, ey - eh * 0.22, ew * 0.13);
      ctx.fill();
    }
  }

  // Wangen
  const cheek = 0.3 * clamp(w.idle + w.speaking + w.listening);
  if (cheek > 0.01) {
    for (const s of [-1, 1]) {
      ctx.fillStyle = rgba(P.warm, cheek);
      ctx.beginPath();
      ctx.ellipse(s * R * 0.42, vy + R * 0.12, R * 0.075, R * 0.04, 0, 0, TAU);
      ctx.fill();
    }
  }

  // Mund
  const my = vy + R * 0.2;
  ctx.lineCap = 'round';
  const smile = clamp(w.idle + w.muted * 0.6 + w.listening * 0.6 + sleepy * 0.45);
  if (smile > 0.01) {
    ctx.strokeStyle = rgba(eyeCol, 0.85 * smile);
    ctx.lineWidth = R * 0.042;
    ctx.beginPath();
    ctx.arc(0, my - R * 0.1, R * 0.13, Math.PI * (0.22 + 0.08 * sleepy), Math.PI * (0.78 - 0.08 * sleepy));
    ctx.stroke();
  }
  if (w.speaking > 0.01) {
    const mh = R * (0.035 + 0.2 * lv);
    const mw = R * (0.2 + 0.08 * lv);
    glow(ctx, 0, my, 0, R * 0.3, P.cool, 0.35 * w.speaking);
    rrect(ctx, -mw / 2, my - mh / 2, mw, mh, Math.min(mh, mw) / 2);
    ctx.fillStyle = rgba(eyeCol, w.speaking);
    ctx.fill();
  }
  if (w.thinking > 0.01) {
    ctx.strokeStyle = rgba(eyeCol, 0.85 * w.thinking);
    ctx.lineWidth = R * 0.042;
    ctx.beginPath();
    ctx.moveTo(-R * 0.02, my + R * 0.01);
    ctx.lineTo(R * 0.13, my - R * 0.02);
    ctx.stroke();
  }

  // Visier-Glanz
  ctx.save();
  rrect(ctx, -vw / 2, vy - vh / 2, vw, vh, R * 0.26);
  ctx.clip();
  const sg = ctx.createLinearGradient(0, vy - vh / 2, 0, vy);
  sg.addColorStop(0, rgba(P.white, 0.11));
  sg.addColorStop(1, rgba(P.white, 0));
  ctx.fillStyle = sg;
  ctx.fillRect(-vw / 2, vy - vh / 2, vw, vh / 2);
  ctx.restore();
  ctx.restore();
}

/** Eigenes Bild im Kreis mit animiertem Statusring; ohne geladenes Bild → Nebula. */
function eigenes(ctx, t, size, v, level, accent, extra) {
  const img = extra.image;
  if (!img || !(img.naturalWidth > 0) || !(img.naturalHeight > 0)) {
    nebula(ctx, t, size, v, level, accent, extra);
    return;
  }
  const P = makePalette(accent, v);
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const speak = w.speaking * lv;
  const listen = w.listening * lv;
  const c = v.clock ?? t;
  const r = R * 0.72 * (1 + 0.035 * speak + 0.02 * listen + 0.008 * breathing(t, extra));

  glow(ctx, 0, 0, r * 0.7, R * 1.45, P.accent, 0.3 + 0.3 * speak + 0.12 * listen + 0.1 * v.hover);
  ctx.save();
  circle(ctx, 0, 0, r);
  ctx.clip();
  const filtered = v.grey > 0.01 || v.dim > 0.01;
  if (filtered) ctx.filter = `grayscale(${v.grey.toFixed(2)}) brightness(${(1 - v.dim * 0.45).toFixed(2)})`;
  const s = Math.max((2 * r) / img.naturalWidth, (2 * r) / img.naturalHeight);
  const dw = img.naturalWidth * s;
  const dh = img.naturalHeight * s;
  ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
  if (filtered) ctx.filter = 'none';
  const gl = ctx.createLinearGradient(0, -r, 0, r * 0.2);
  gl.addColorStop(0, rgba(P.white, 0.2));
  gl.addColorStop(1, rgba(P.white, 0));
  ctx.fillStyle = gl;
  ctx.fillRect(-r, -r, 2 * r, 1.2 * r);
  const vg = ctx.createRadialGradient(0, 0, r * 0.7, 0, 0, r);
  vg.addColorStop(0, 'rgba(0,0,0,0)');
  vg.addColorStop(1, 'rgba(0,0,0,0.35)');
  ctx.fillStyle = vg;
  ctx.fillRect(-r, -r, 2 * r, 2 * r);
  ctx.restore();

  const rr = r + R * 0.075;
  ring(ctx, rr, rgba(P.white, 0.08), R * 0.05);
  const tube = conic(ctx, c * 0.8, R, [
    [0, P.accent, 1],
    [0.3, P.cool, 1],
    [0.55, P.warm, 0.2],
    [0.8, P.accent, 1],
    [1, P.accent, 1],
  ]);
  ctx.save();
  ctx.lineCap = 'round';
  ctx.shadowColor = rgba(P.accent, 0.8);
  ctx.shadowBlur = R * 0.12 + speak * R * 0.2;
  ring(ctx, rr, tube, R * (0.045 + 0.05 * speak + 0.025 * listen));
  ctx.restore();
}

// ---------------------------------------------------------------- Mensch (kleine animierte Figur)

const SKIN_TONE = [241, 199, 164];
const HAIR = [52, 36, 30];
const PANTS = [34, 38, 58];
const SHOE = [236, 238, 246];

/** Zwei-Gelenk-IK: Ellbogen zwischen Schulter s und Ziel tg (Ellbogen nach außen, Seite side ±1). */
function elbowFor(sx, sy, tx, ty, L1, L2, side) {
  let dx = tx - sx;
  let dy = ty - sy;
  let d = Math.hypot(dx, dy) || 1e-6;
  const maxD = (L1 + L2) * 0.999;
  const minD = Math.abs(L1 - L2) + 1e-3;
  if (d > maxD || d < minD) {
    const k = clamp(d, minD, maxD) / d;
    dx *= k;
    dy *= k;
    d = Math.hypot(dx, dy);
  }
  const base = Math.atan2(dy, dx);
  const off = Math.acos(clamp((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d), -1, 1));
  const a1 = base + off;
  const a2 = base - off;
  const e1 = [sx + Math.cos(a1) * L1, sy + Math.sin(a1) * L1];
  const e2 = [sx + Math.cos(a2) * L1, sy + Math.sin(a2) * L1];
  const score = (e) => side * e[0] + 0.35 * e[1];
  const e = score(e1) >= score(e2) ? e1 : e2;
  return { ex: e[0], ey: e[1], hx: sx + dx, hy: sy + dy };
}

function limb(ctx, pts, width, col) {
  ctx.strokeStyle = col;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(pts[0], pts[1]);
  for (let i = 2; i < pts.length; i += 2) ctx.lineTo(pts[i], pts[i + 1]);
  ctx.stroke();
}

/** Mensch: kleine, stehende Figur mit Headset – winkt, gestikuliert beim Sprechen, lauscht, grübelt. */
function mensch(ctx, t, size, v, level, accent, extra) {
  const P = makePalette(accent, v);
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const reduced = !!extra.reduced;
  const sleepy = clamp(w.off + w.error + w.disconnected);
  const awake = 1 - sleepy;
  const speak = w.speaking;
  const hover = clamp(v.hover) * awake * (1 - w.thinking) * (1 - w.listening);
  const mot = reduced ? 0.25 : 1;
  const skin = P.tone(SKIN_TONE);
  const hair = P.tone(HAIR);
  const pants = P.tone(mixRgb(PANTS, P.raw, 0.12));
  const shirt = P.accent;
  const shirtDark = mixRgb(P.accent, P.ink, 0.35);

  // Atmen, leichtes Wippen, Gewichtsverlagerung
  const breath = Math.sin(t * 1.6) * 0.5 + 0.5;
  const bob = (Math.sin(t * 1.6) * R * 0.012 + speak * lv * R * 0.02 * Math.sin(t * 11)) * mot * awake + sleepy * R * 0.03;
  const sway = Math.sin(t * 0.55) * R * 0.025 * mot * awake;

  // Boden-Schatten + Aura
  glow(ctx, 0, -R * 0.1, R * 0.2, R * 1.35, P.accent, 0.16 + 0.14 * speak * lv + 0.1 * w.listening + 0.06 * v.hover);
  ctx.fillStyle = rgba(P.ink, 0.45);
  ctx.beginPath();
  ctx.ellipse(sway * 0.3, R * 1.36, R * 0.42, R * 0.07, 0, 0, TAU);
  ctx.fill();

  ctx.save();
  ctx.translate(sway, bob);

  // Beine + Schuhe
  const hipY = R * 0.46;
  const footY = R * 1.28;
  const legW = R * 0.19;
  for (const s of [-1, 1]) {
    const hx = s * R * 0.13;
    const fx = s * R * 0.16 - sway * 0.6;
    limb(ctx, [hx, hipY, hx + (fx - hx) * 0.5 + s * R * 0.01, (hipY + footY) / 2, fx, footY - sway * 0], legW, rgba(pants, 1));
    ctx.fillStyle = rgba(SHOE, 1);
    ctx.beginPath();
    ctx.ellipse(fx + s * R * 0.04, footY + R * 0.04 - bob, R * 0.13, R * 0.065, 0, 0, TAU);
    ctx.fill();
  }

  // Oberkörper (Hoodie in Akzentfarbe)
  const shY = -R * 0.36;
  const tw = R * 0.82 * (1 + 0.012 * breath);
  ctx.save();
  ctx.translate(0, -breath * R * 0.01);
  rrect(ctx, -tw / 2, shY, tw, hipY - shY + R * 0.08, R * 0.22);
  const tg = ctx.createLinearGradient(-tw / 2, shY, tw / 2, hipY);
  tg.addColorStop(0, rgba(mixRgb(shirt, P.white, 0.18), 1));
  tg.addColorStop(1, rgba(shirtDark, 1));
  ctx.fillStyle = tg;
  ctx.fill();
  // Reißverschluss + Taschen + Logo
  ctx.strokeStyle = rgba(P.white, 0.35);
  ctx.lineWidth = Math.max(1, R * 0.018);
  ctx.beginPath();
  ctx.moveTo(0, shY + R * 0.12);
  ctx.lineTo(0, hipY + R * 0.06);
  ctx.stroke();
  ctx.strokeStyle = rgba(P.ink, 0.25);
  ctx.beginPath();
  ctx.moveTo(-tw * 0.36, hipY - R * 0.12);
  ctx.quadraticCurveTo(-tw * 0.18, hipY - R * 0.2, -R * 0.03, hipY - R * 0.12);
  ctx.moveTo(tw * 0.36, hipY - R * 0.12);
  ctx.quadraticCurveTo(tw * 0.18, hipY - R * 0.2, R * 0.03, hipY - R * 0.12);
  ctx.stroke();
  ctx.fillStyle = rgba(P.white, 0.85);
  ctx.font = `700 ${Math.max(6, R * 0.13)}px -apple-system, "SF Pro Text", sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('V', -tw * 0.22, shY + R * 0.2);
  ctx.restore();

  // Hals + Kopf
  const tilt = (0.1 * w.listening - 0.07 * w.thinking + 0.2 * sleepy + Math.sin(t * 0.8) * 0.03 * mot * awake
    + speak * lv * 0.05 * Math.sin(t * 5)) ;
  const headY = -R * 0.84;
  const hr = R * 0.34;
  ctx.fillStyle = rgba(mixRgb(skin, P.ink, 0.12), 1);
  rrect(ctx, -R * 0.08, shY - R * 0.16, R * 0.16, R * 0.22, R * 0.06);
  ctx.fill();
  // Kapuzen-Kragen
  ctx.fillStyle = rgba(shirtDark, 1);
  ctx.beginPath();
  ctx.ellipse(0, shY + R * 0.02, R * 0.24, R * 0.07, 0, 0, TAU);
  ctx.fill();

  ctx.save();
  ctx.translate(0, headY + R * 0.3);
  ctx.rotate(tilt);
  ctx.translate(0, -R * 0.3);

  // Haare hinten
  ctx.fillStyle = rgba(hair, 1);
  ctx.beginPath();
  ctx.ellipse(0, -R * 0.04, hr * 1.04, hr * 1.04, 0, Math.PI * 0.95, Math.PI * 2.05);
  ctx.fill();
  // Ohren
  for (const s of [-1, 1]) {
    ctx.fillStyle = rgba(mixRgb(skin, P.ink, 0.06), 1);
    circle(ctx, s * hr * 0.95, R * 0.03, R * 0.07);
    ctx.fill();
  }
  // Gesicht
  const fg = ctx.createRadialGradient(-hr * 0.3, -hr * 0.3, hr * 0.1, 0, 0, hr * 1.1);
  fg.addColorStop(0, rgba(mixRgb(skin, P.white, 0.18), 1));
  fg.addColorStop(1, rgba(mixRgb(skin, P.ink, 0.1), 1));
  ctx.fillStyle = fg;
  ctx.beginPath();
  ctx.ellipse(0, 0, hr * 0.92, hr, 0, 0, TAU);
  ctx.fill();
  // Haare vorne (Seitenscheitel)
  ctx.fillStyle = rgba(hair, 1);
  ctx.beginPath();
  ctx.moveTo(-hr * 0.98, -hr * 0.05);
  ctx.quadraticCurveTo(-hr * 1.02, -hr * 1.08, 0, -hr * 1.1);
  ctx.quadraticCurveTo(hr * 1.02, -hr * 1.06, hr * 0.97, -hr * 0.12);
  ctx.quadraticCurveTo(hr * 0.7, -hr * 0.55, hr * 0.05, -hr * 0.52);
  ctx.quadraticCurveTo(-hr * 0.45, -hr * 0.5, -hr * 0.62, -hr * 0.2);
  ctx.quadraticCurveTo(-hr * 0.8, -hr * 0.05, -hr * 0.98, -hr * 0.05);
  ctx.fill();
  ctx.fillStyle = rgba(P.white, 0.12);
  ctx.beginPath();
  ctx.ellipse(-hr * 0.25, -hr * 0.82, hr * 0.32, hr * 0.08, -0.2, 0, TAU);
  ctx.fill();

  // Augen
  const blink = reduced ? 0 : blinkAmount(t);
  const open = (1 - blink) * (1 - 0.92 * sleepy) * (1 - 0.35 * w.muted) * (1 + 0.15 * w.listening);
  const lookX = (saccade(t, 2.3, 2.8) * 0.06 * (w.idle + speak) + 0.05 * w.thinking + 0.04 * w.listening) * R;
  const lookY = (saccade(t, 5.2, 3.3) * 0.03 * w.idle - 0.05 * w.thinking) * R;
  const eyeY = hr * 0.02;
  for (const s of [-1, 1]) {
    const ex = s * hr * 0.36 + lookX * 0.5;
    const ey = eyeY + lookY * 0.5;
    const eh = Math.max(R * 0.012, R * 0.06 * open);
    ctx.fillStyle = rgba(P.tone([28, 22, 30]), 1);
    ctx.beginPath();
    ctx.ellipse(ex, ey, R * 0.042, eh, 0, 0, TAU);
    ctx.fill();
    if (eh > R * 0.03) {
      ctx.fillStyle = rgba(P.white, 0.9);
      circle(ctx, ex - R * 0.012, ey - eh * 0.35, R * 0.013);
      ctx.fill();
    }
    // Augenbrauen
    const raise = 0.05 * w.listening + 0.03 * speak * lv + (s > 0 ? 0.05 : -0.01) * w.thinking;
    ctx.strokeStyle = rgba(hair, 0.95);
    ctx.lineWidth = Math.max(1, R * 0.028);
    ctx.lineCap = 'round';
    ctx.beginPath();
    const by = ey - R * 0.1 - raise * R;
    ctx.moveTo(ex - R * 0.05, by + s * 0.0 + R * 0.008);
    ctx.quadraticCurveTo(ex, by - R * 0.018, ex + R * 0.05, by + R * 0.008 * (w.thinking > 0.3 ? -s : 1));
    ctx.stroke();
  }
  // Wangen
  const cheek = 0.28 * clamp(w.idle + speak + w.listening + v.hover * 0.5) * awake;
  if (cheek > 0.01) {
    for (const s of [-1, 1]) {
      ctx.fillStyle = rgba([255, 120, 130], cheek);
      ctx.beginPath();
      ctx.ellipse(s * hr * 0.55, hr * 0.3, R * 0.06, R * 0.032, 0, 0, TAU);
      ctx.fill();
    }
  }
  // Nase
  ctx.strokeStyle = rgba(mixRgb(skin, P.ink, 0.3), 0.7);
  ctx.lineWidth = Math.max(1, R * 0.018);
  ctx.beginPath();
  ctx.arc(lookX * 0.3, hr * 0.2, R * 0.03, 0.1 * Math.PI, 0.9 * Math.PI);
  ctx.stroke();
  // Mund
  const my = hr * 0.5;
  const talk = speak * (reduced ? lv * 0.6 : lv * (0.6 + 0.4 * Math.abs(Math.sin(t * 17))));
  const smile = clamp(w.idle + w.listening * 0.8 + v.hover * 0.6 + w.muted * 0.3) * (1 - talk);
  ctx.lineCap = 'round';
  if (talk > 0.02 || speak > 0.3) {
    const mh = R * (0.02 + 0.11 * talk);
    const mw = R * (0.1 + 0.04 * talk);
    ctx.fillStyle = rgba(P.tone([90, 30, 40]), 1);
    ctx.beginPath();
    ctx.ellipse(0, my + mh * 0.3, mw / 2, mh / 2 + R * 0.008, 0, 0, TAU);
    ctx.fill();
    if (mh > R * 0.05) {
      ctx.fillStyle = rgba(P.tone([235, 110, 120]), 0.9);
      ctx.beginPath();
      ctx.ellipse(0, my + mh * 0.62, mw * 0.3, mh * 0.18, 0, 0, TAU);
      ctx.fill();
    }
  } else if (w.thinking > 0.4) {
    ctx.strokeStyle = rgba(P.tone([120, 60, 60]), 0.9);
    ctx.lineWidth = Math.max(1, R * 0.024);
    ctx.beginPath();
    ctx.moveTo(-R * 0.02, my + R * 0.01);
    ctx.lineTo(R * 0.07, my - R * 0.012);
    ctx.stroke();
  } else {
    ctx.strokeStyle = rgba(P.tone([130, 60, 60]), 0.95);
    ctx.lineWidth = Math.max(1, R * 0.026);
    ctx.beginPath();
    const sm = 0.2 + 0.6 * smile * awake;
    ctx.arc(0, my - R * 0.06 * sm, R * 0.08, Math.PI * (0.5 - 0.32 * sm), Math.PI * (0.5 + 0.32 * sm));
    ctx.stroke();
  }

  // Headset (Bügel, Ohrmuschel, Mikrofon)
  ctx.strokeStyle = rgba(P.tone([40, 44, 64]), 1);
  ctx.lineWidth = Math.max(1.5, R * 0.04);
  ctx.beginPath();
  ctx.arc(0, -R * 0.02, hr * 1.08, Math.PI * 1.08, Math.PI * 1.92);
  ctx.stroke();
  for (const s of [-1, 1]) {
    rrect(ctx, s * hr * 1.0 - R * 0.05, -R * 0.06, R * 0.1, R * 0.17, R * 0.05);
    ctx.fillStyle = rgba(mixRgb(P.accent, P.ink, 0.2), 1);
    ctx.fill();
  }
  ctx.strokeStyle = rgba(P.tone([40, 44, 64]), 1);
  ctx.lineWidth = Math.max(1, R * 0.022);
  ctx.beginPath();
  ctx.moveTo(hr * 1.0, R * 0.08);
  ctx.quadraticCurveTo(hr * 0.9, my + R * 0.04, R * 0.12, my + R * 0.02);
  ctx.stroke();
  const micGlow = 0.25 + 0.75 * clamp(w.listening * (0.5 + lv) + speak * 0.3);
  glow(ctx, R * 0.11, my + R * 0.02, 0, R * 0.1, P.cool, 0.6 * micGlow);
  ctx.fillStyle = rgba(mixRgb(P.cool, P.white, 0.3), 0.6 + 0.4 * micGlow);
  circle(ctx, R * 0.11, my + R * 0.02, R * 0.025);
  ctx.fill();

  // Schlafend: kleines „z“
  if (sleepy > 0.3 && !reduced) {
    const zp = fract(t * 0.35);
    ctx.fillStyle = rgba(P.white, sleepy * (1 - zp) * 0.8);
    ctx.font = `700 ${Math.max(6, R * (0.1 + 0.08 * zp))}px -apple-system, sans-serif`;
    ctx.fillText('z', hr * (0.9 + 0.5 * zp), -hr * (0.9 + 0.9 * zp));
  }
  ctx.restore();

  // Arme (zuletzt, damit Hände vor Gesicht/Körper liegen)
  const L1 = R * 0.36;
  const L2 = R * 0.34;
  const armW = R * 0.15;
  const headCx = Math.sin(tilt) * R * 0.3;
  for (const s of [-1, 1]) {
    const sx = s * (tw / 2 - R * 0.06);
    const sy = shY + R * 0.1;
    const swing = Math.sin(t * 1.1 + (s > 0 ? 0 : 1.7)) * R * 0.03 * mot * awake;
    // Zielposition der Hand je Zustand, nach Gewicht überblendet
    let tx = s * R * 0.5 + swing;
    let ty = R * 0.4 + sleepy * R * 0.04;
    const acc = (wgt, x, y) => {
      if (wgt <= 0.001) return;
      tx = lerp(tx, x, wgt);
      ty = lerp(ty, y, wgt);
    };
    if (s > 0) {
      const g = speak * awake;
      acc(g, R * 0.62 + Math.sin(t * 3.1) * R * 0.08 * mot, -R * 0.02 - lv * R * 0.22 + Math.sin(t * 4.3) * R * 0.06 * mot);
      acc(w.listening * awake, headCx + hr * 1.12, headY + R * 0.04);
      acc(w.thinking * awake, headCx + R * 0.06, headY + hr * 0.95);
      acc(hover, R * 0.78 + Math.sin(t * 9) * R * 0.1 * mot, -R * 0.82 + Math.cos(t * 9) * R * 0.03 * mot);
    } else {
      acc(speak * awake * 0.6, -R * 0.56 + Math.sin(t * 2.3 + 1) * R * 0.06 * mot, R * 0.12 - lv * R * 0.08);
      acc(w.thinking * awake, R * 0.12, R * 0.12);
    }
    const k = elbowFor(sx, sy, tx, ty, L1, L2, s);
    limb(ctx, [sx, sy, k.ex, k.ey, k.hx, k.hy], armW, rgba(mixRgb(shirt, P.white, 0.06), 1));
    ctx.strokeStyle = rgba(P.ink, 0.12);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = rgba(skin, 1);
    circle(ctx, k.hx, k.hy, R * 0.075);
    ctx.fill();
  }
  ctx.restore();
}

/** Renderer je Skin-ID. */
export const RENDERERS = Object.freeze({ mensch, nebula, hologramm, neon, glas, roboter, eigenes });
/** Skins mit stehender Figur: Zustandseffekte sitzen am Kopf statt um die Mitte. */
export const FIGURE_SKINS = Object.freeze(new Set(['mensch']));

/** @param {unknown} id @returns {Function} Renderer (unbekannt → Nebula) */
export function getRenderer(id) {
  return RENDERERS[normalizeSkinId(id)];
}

// ---------------------------------------------------------------- Gemeinsame Zustandseffekte

function micOffGlyph(ctx, x, y, s, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.fillStyle = 'rgba(20,20,30,0.92)';
  circle(ctx, 0, 0, s);
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.22)';
  ctx.lineWidth = 1;
  ctx.stroke();
  const k = s / 11;
  ctx.scale(k, k);
  ctx.lineCap = 'round';
  ctx.strokeStyle = 'rgba(255,255,255,0.92)';
  ctx.lineWidth = 1.7;
  rrect(ctx, -2.6, -6.2, 5.2, 8.6, 2.6);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(0, -0.6, 5, 0.1 * Math.PI, 0.9 * Math.PI);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, 4.4);
  ctx.lineTo(0, 6.6);
  ctx.stroke();
  ctx.strokeStyle = 'rgba(255,107,122,1)';
  ctx.lineWidth = 1.9;
  ctx.beginPath();
  ctx.moveTo(-6, -6.5);
  ctx.lineTo(6, 6.5);
  ctx.stroke();
  ctx.restore();
}

/**
 * Gemeinsame Effekte über jedem Skin: Klick-Impuls, Zuhören-Ring, Denk-Kometen, Stumm-Symbol,
 * Warnpunkt (aus/Fehler) und Spinner (keine Verbindung). Ursprung = Avatar-Mitte.
 */
export function paintStateEffects(ctx, t, size, state, level, accent, extra = {}) {
  const v = normalizeVisual(state);
  const P = makePalette(accent, { grey: 0, dim: 0 });
  const R = size / 2;
  const w = v.w;
  const lv = clamp(level);
  const reduced = !!extra.reduced;
  const c = v.clock ?? t;
  ctx.save();
  ctx.lineCap = 'round';

  if (v.pulse > 0.01) {
    const p = 1 - v.pulse;
    ring(ctx, R * (0.84 + 0.5 * p), rgba(P.light, 0.55 * v.pulse), 1.5 + 2 * v.pulse);
  }

  if (w.listening > 0.01) {
    const L = w.listening;
    const rr = R * (0.9 + 0.1 * lv + (reduced ? 0 : 0.015 * Math.sin(t * 4)));
    ring(ctx, rr, rgba(P.cool, 0.13 * L), 6 + 10 * lv);
    ring(ctx, rr, rgba(mixRgb(P.cool, P.white, 0.3), (0.6 + 0.4 * lv) * L), 1.6 + 2.2 * lv);
    if (!reduced) {
      for (let k = 0; k < 3; k++) {
        const p = fract(t * 0.75 + k / 3);
        ring(ctx, R * (0.9 + 0.5 * p), rgba(P.cool, L * (1 - p) * (1 - p) * (0.25 + 0.5 * lv)), 1.2);
      }
    }
  }

  if (w.thinking > 0.01) {
    const T = w.thinking;
    const sh = conic(ctx, c * 2.6, R, [
      [0, P.light, 0],
      [0.12, P.light, 0.75 * T],
      [0.25, P.light, 0],
      [0.5, P.cool, 0],
      [0.62, P.cool, 0.6 * T],
      [0.75, P.cool, 0],
      [1, P.light, 0],
    ]);
    ring(ctx, R * 0.92, sh, 2);
    const cols = [P.light, P.cool, P.warm];
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let k = 0; k < 3; k++) {
      const tilt = k * 1.05 + 0.3;
      const ctl = Math.cos(tilt);
      const stl = Math.sin(tilt);
      const base = c * (2.1 + k * 0.35) + k * 2.1;
      for (let j = 0; j < 10; j++) {
        const ang = base - j * 0.075;
        const ox = Math.cos(ang) * R * 1.0;
        const oy = Math.sin(ang) * R * 0.38;
        const x = ox * ctl - oy * stl;
        const y = ox * stl + oy * ctl;
        const behind = Math.sin(ang) < 0 && Math.hypot(x, y) < R * 0.78;
        const a = T * Math.pow(1 - j / 10, 1.5) * (behind ? 0.16 : 0.95);
        ctx.fillStyle = rgba(cols[k], a);
        circle(ctx, x, y, (2.3 - j * 0.17) * (R / 66));
        ctx.fill();
      }
    }
    ctx.restore();
  }

  if (w.muted > 0.01) micOffGlyph(ctx, R * 0.58, -R * 0.58, R * 0.2, w.muted);

  const warn = Math.max(w.off, w.error);
  if (warn > 0.01) {
    const col = w.error >= w.off ? WARN_RED : WARN_AMBER;
    const x = R * 0.6;
    const y = -R * 0.6;
    const p = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(t * 3);
    glow(ctx, x, y, 0, R * 0.2, col, warn * (0.35 + 0.3 * p));
    ctx.fillStyle = rgba(col, warn);
    circle(ctx, x, y, R * 0.075);
    ctx.fill();
    ctx.strokeStyle = rgba(INK, 0.8 * warn);
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  if (w.disconnected > 0.01) {
    const D = w.disconnected;
    ring(ctx, R * 0.94, `rgba(255,255,255,${(0.1 * D).toFixed(3)})`, 2.5);
    const a = t * (reduced ? 1.6 : 4.2);
    const len = 1.1 + 0.5 * Math.sin(t * (reduced ? 0.8 : 2.1));
    ctx.strokeStyle = `rgba(235,235,245,${(0.8 * D).toFixed(3)})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.94, a, a + len);
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * Kompletten Avatar zeichnen (Skin + Zustandseffekte).
 * @param {CanvasRenderingContext2D} ctx
 * @param {{skin?: string, t?: number, size: number, state?: string|object, level?: number, accent?: string,
 *          image?: any, reduced?: boolean, cx?: number, cy?: number}} o
 *   cx/cy: Mittelpunkt im Canvas (Standard 0/0 = Ursprung bereits verschoben)
 */
export function paintAvatar(ctx, o) {
  const v = normalizeVisual(o.state);
  const t = o.t || 0;
  const size = o.size || 132;
  const level = clamp(o.level || 0);
  const accent = isHexColor(o.accent) ? o.accent : DEFAULT_ACCENT;
  const extra = { image: o.image || null, reduced: !!o.reduced };
  ctx.save();
  ctx.translate(o.cx || 0, o.cy || 0);
  ctx.save();
  getRenderer(o.skin)(ctx, t, size, v, level, accent, extra);
  ctx.restore();
  if (FIGURE_SKINS.has(normalizeSkinId(o.skin))) {
    ctx.translate(0, -size * 0.42);
    paintStateEffects(ctx, t, size * 0.6, v, level, accent, extra);
  } else {
    paintStateEffects(ctx, t, size, v, level, accent, extra);
  }
  ctx.restore();
}
