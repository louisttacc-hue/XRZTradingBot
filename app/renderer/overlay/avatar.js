/**
 * Vincent – animierter Avatar auf einem Canvas.
 *
 * Kümmert sich um Canvas-Größe (DPR-bewusst), die Render-Schleife (requestAnimationFrame), weiche
 * Zustandsübergänge (Gewichte je Zustand), geglättete Audiopegel und das Laden eines eigenen Bildes.
 * Gezeichnet wird über paintAvatar() aus skins.js. Beim Import keine DOM-Zugriffe (Node-Tests).
 */
import { CANVAS_SCALE, DEFAULT_ACCENT, STATE_KEYS, clamp, finishVisual, isHexColor, noise1, normalizeSkinId, paintAvatar, smoothstep } from './skins.js';

/** Zustände, in denen der Avatar ruhig ist (Rendern mit ~30 fps spart Energie). */
const CALM_STATES = new Set(['idle', 'muted', 'off', 'error']);
const CALM_FRAME_MS = 1000 / 31;

/**
 * Frame-unabhängige Annäherung an einen Zielwert (exponentiell).
 * @param {number} current
 * @param {number} target
 * @param {number} lambda Geschwindigkeit (1/s)
 * @param {number} dt Sekunden
 */
export function damp(current, target, lambda, dt) {
  return target + (current - target) * Math.exp(-lambda * dt);
}

/**
 * Glättet Audiopegel (~15 Hz vom Backend) für flüssige 60-fps-Animationen: schneller Anstieg,
 * weicheres Abklingen; ohne neue Werte fällt der Pegel nach `hold` Sekunden auf 0.
 */
export class LevelSmoother {
  constructor({ attack = 22, release = 7, hold = 0.25 } = {}) {
    this.attack = attack;
    this.release = release;
    this.hold = hold;
    this.value = 0;
    this.target = 0;
    this.lastPush = -Infinity;
  }

  /** @param {number} level 0..1 @param {number} now Sekunden */
  push(level, now) {
    const n = Number(level);
    this.target = Number.isFinite(n) ? clamp(n) : 0;
    this.lastPush = now;
  }

  /** @param {number} now @returns {boolean} true, wenn kürzlich Pegel kamen */
  active(now) {
    return now - this.lastPush < 0.5;
  }

  /** @param {number} now @param {number} dt @returns {number} geglätteter Pegel */
  update(now, dt) {
    if (now - this.lastPush > this.hold) this.target = 0;
    const k = this.target > this.value ? this.attack : this.release;
    this.value = damp(this.value, this.target, k, dt);
    if (this.value < 1e-4) this.value = 0;
    return this.value;
  }
}

/** Überblendet Zustandsgewichte weich (aktueller Zustand → 1, alle anderen → 0). */
export class StateBlend {
  constructor(name = 'disconnected', speed = 9) {
    this.name = STATE_KEYS.includes(name) ? name : 'idle';
    this.speed = speed;
    this.w = {};
    for (const k of STATE_KEYS) this.w[k] = k === this.name ? 1 : 0;
  }

  /** @param {string} name */
  set(name) {
    if (STATE_KEYS.includes(name)) this.name = name;
  }

  /** @param {number} dt @returns {boolean} true, wenn die Überblendung abgeschlossen ist */
  update(dt) {
    let settled = true;
    for (const k of STATE_KEYS) {
      const target = k === this.name ? 1 : 0;
      const next = damp(this.w[k], target, this.speed, dt);
      if (Math.abs(next - target) < 0.002) this.w[k] = target;
      else {
        this.w[k] = next;
        settled = false;
      }
    }
    return settled;
  }
}

/**
 * Natürlich wirkender Sprechpegel, falls der TTS-Anbieter keine Pegel liefert (z. B. macOS „say“).
 * @param {number} t Sekunden
 * @returns {number} 0..1
 */
export function syntheticSpeech(t) {
  const syllables = 0.5 + 0.5 * Math.sin(t * 11.5 + 1.7 * Math.sin(t * 2.3));
  const phrase = smoothstep(0.15, 0.6, noise1(t * 1.6));
  return clamp(0.12 + 0.62 * syllables * phrase + 0.14 * noise1(t * 9));
}

/**
 * Dateipfad oder URL des eigenen Avatar-Bildes in eine ladbare URL verwandeln (file:// oder data:).
 * Relative Pfade und andere Schemata werden nicht unterstützt (→ '').
 * @param {unknown} p
 * @returns {string}
 */
export function toFileUrl(p) {
  if (typeof p !== 'string') return '';
  const s = p.trim();
  if (!s) return '';
  if (/^(file:|data:image\/)/i.test(s)) return s;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return '';
  let path = s.replace(/\\/g, '/');
  let drive = '';
  const m = /^([a-zA-Z]:)(\/.*)$/.exec(path);
  if (m) {
    drive = `/${m[1]}`;
    path = m[2];
  }
  if (!path.startsWith('/')) return '';
  return `file://${drive}${path.split('/').map((seg) => encodeURIComponent(seg)).join('/')}`;
}

/** Animierter Avatar. */
export class Avatar {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{size?: number, skin?: string, accent?: string, reducedMotion?: boolean, onImageError?: (url: string) => void}} [opts]
   */
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.size = opts.size || 132;
    this.skin = normalizeSkinId(opts.skin);
    this.accent = isHexColor(opts.accent) ? opts.accent : DEFAULT_ACCENT;
    this.reduced = !!opts.reducedMotion;
    this.onImageError = opts.onImageError || null;
    this.blend = new StateBlend('disconnected');
    this.mic = new LevelSmoother();
    this.tts = new LevelSmoother({ attack: 26, release: 9 });
    this.speech = 0;
    this.hover = 0;
    this.hoverTarget = 0;
    this.pulse = 0;
    this.clock = 0;
    this.image = null;
    this.imageUrl = '';
    this.running = false;
    this.raf = 0;
    this.t0 = 0;
    this.last = 0;
    this.lastPaint = 0;
    this.dpr = 1;
    this.visual = { name: this.blend.name, w: this.blend.w, hover: 0, pulse: 0, clock: 0 };
    this.loop = this.loop.bind(this);
    this.resize();
  }

  /** Canvas an Größe und Pixeldichte anpassen (auch beim Wechsel auf einen anderen Monitor). */
  resize() {
    const dpr = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
    const css = Math.round(this.size * CANVAS_SCALE);
    this.canvas.style.width = `${css}px`;
    this.canvas.style.height = `${css}px`;
    this.canvas.width = Math.round(css * dpr);
    this.canvas.height = Math.round(css * dpr);
    this.dpr = dpr;
    this.css = css;
    if (typeof window.matchMedia === 'function') {
      const mq = window.matchMedia(`(resolution: ${dpr}dppx)`);
      const onChange = () => this.resize();
      if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onChange, { once: true });
    }
    if (this.running) this.paint(this.elapsed(performance.now()));
  }

  /** @param {number} px Avatar-Durchmesser in CSS-Pixeln */
  setSize(px) {
    const n = Math.round(clamp(Number(px) || 132, 72, 220));
    if (n === this.size) return;
    this.size = n;
    this.resize();
  }

  setSkin(id) {
    this.skin = normalizeSkinId(id);
  }

  setAccent(hex) {
    this.accent = isHexColor(hex) ? hex : DEFAULT_ACCENT;
  }

  setReducedMotion(on) {
    this.reduced = !!on;
  }

  setHover(on) {
    this.hoverTarget = on ? 1 : 0;
  }

  /** @param {string} name visueller Zustand (siehe STATE_KEYS) */
  setState(name) {
    this.blend.set(name);
  }

  get state() {
    return this.blend.name;
  }

  /** @param {number} level 0..1 @param {'mic'|'tts'} source */
  pushLevel(level, source) {
    const now = performance.now() / 1000;
    if (source === 'tts') this.tts.push(level, now);
    else this.mic.push(level, now);
  }

  /** Kurzer Lichtimpuls als Klick-Feedback. */
  ping() {
    this.pulse = 1;
  }

  /**
   * Eigenes Avatar-Bild laden; bei Fehler zeichnet der Skin "eigenes" automatisch Nebula.
   * @param {string} path Dateipfad oder file://-URL
   */
  setCustomImage(path) {
    const url = toFileUrl(path);
    if (url === this.imageUrl) return;
    this.imageUrl = url;
    this.image = null;
    if (!url) return;
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      if (this.imageUrl === url) this.image = img;
    };
    img.onerror = () => {
      if (this.imageUrl !== url) return;
      this.image = null;
      if (this.onImageError) this.onImageError(url);
    };
    img.src = url;
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.t0 = performance.now();
    this.last = this.t0;
    this.raf = requestAnimationFrame(this.loop);
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }

  elapsed(now) {
    return ((now - this.t0) / 1000) * (this.reduced ? 0.45 : 1);
  }

  loop(now) {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(0.1, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    const sec = now / 1000;
    const t = this.elapsed(now);
    const settled = this.blend.update(dt);
    const w = this.blend.w;
    const mic = this.mic.update(sec, dt);
    const tts = this.tts.update(sec, dt);
    const speechSrc = this.tts.active(sec) ? tts : w.speaking > 0.01 ? syntheticSpeech(t) * 0.85 : 0;
    this.speech = damp(this.speech, speechSrc, 18, dt);
    this.hover = damp(this.hover, this.hoverTarget, 10, dt);
    this.pulse = this.pulse > 0.01 ? damp(this.pulse, 0, 3.2, dt) : 0;
    const scale = this.reduced ? 0.45 : 1;
    this.clock += dt * scale * (1 + 1.7 * w.thinking + 0.9 * w.speaking * this.speech + 0.5 * w.listening * mic);

    const calm = settled && CALM_STATES.has(this.blend.name) && this.pulse === 0
      && Math.abs(this.hover - this.hoverTarget) < 0.01 && mic === 0 && tts === 0;
    if (calm && now - this.lastPaint < CALM_FRAME_MS) return;
    this.lastPaint = now;
    this.paint(t, mic);
  }

  /** @param {number} t Sekunden @param {number} [mic] geglätteter Mikrofonpegel */
  paint(t, mic = this.mic.value) {
    const w = this.blend.w;
    const rest = Math.max(0, 1 - w.speaking - w.listening);
    const level = clamp(w.speaking * this.speech + w.listening * mic + rest * Math.max(mic, this.speech) * 0.5);
    const v = this.visual;
    v.name = this.blend.name;
    v.w = w;
    v.hover = this.hover;
    v.pulse = this.pulse;
    v.clock = this.clock;
    finishVisual(v);
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    paintAvatar(ctx, {
      skin: this.skin,
      t,
      size: this.size,
      state: v,
      level,
      accent: this.accent,
      image: this.image,
      reduced: this.reduced,
      cx: this.css / 2,
      cy: this.css / 2,
    });
  }
}
