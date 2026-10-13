#!/usr/bin/env node
'use strict';
/**
 * Erzeugt die Icons von XRZ Vincent ohne externe Abhängigkeiten (eigener PNG-Encoder auf Basis von zlib):
 *   build/icon.png               1024×1024 App-Icon (dunkles abgerundetes Quadrat mit leuchtendem Orb)
 *   assets/trayTemplate.png      16×16  Menüleisten-Icon (schwarz + Alpha, macOS-Template)
 *   assets/trayTemplate@2x.png   32×32  dasselbe für Retina
 *
 * Aufruf: `node scripts/make-icons.js` (bzw. `npm run icons`) im Ordner app/.
 */
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const ROOT = path.join(__dirname, '..');

// ---------------------------------------------------------------- PNG-Encoder

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/** @param {Buffer} buf */
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** @param {string} type @param {Buffer} data */
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

/**
 * RGBA-Pixel (8 bit, nicht vormultipliziert) als PNG kodieren; pro Zeile wird der kleinste Filter gewählt.
 * @param {number} width @param {number} height @param {Uint8Array} rgba
 * @returns {Buffer}
 */
function encodePng(width, height, rgba) {
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  const candidate = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const row = y * stride;
    let bestFilter = 0;
    let bestScore = Infinity;
    let best = Buffer.alloc(stride);
    for (let filter = 0; filter <= 4; filter++) {
      let score = 0;
      for (let i = 0; i < stride; i++) {
        const x = rgba[row + i];
        const a = i >= 4 ? rgba[row + i - 4] : 0;
        const b = y > 0 ? rgba[row - stride + i] : 0;
        const c = y > 0 && i >= 4 ? rgba[row - stride + i - 4] : 0;
        let v;
        if (filter === 0) v = x;
        else if (filter === 1) v = x - a;
        else if (filter === 2) v = x - b;
        else if (filter === 3) v = x - ((a + b) >> 1);
        else {
          const p = a + b - c;
          const pa = Math.abs(p - a);
          const pb = Math.abs(p - b);
          const pc = Math.abs(p - c);
          v = x - (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
        }
        v &= 0xff;
        candidate[i] = v;
        score += v < 128 ? v : 256 - v;
      }
      if (score < bestScore) {
        bestScore = score;
        bestFilter = filter;
        best = Buffer.from(candidate);
      }
    }
    raw[y * (stride + 1)] = bestFilter;
    best.copy(raw, y * (stride + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // Bit-Tiefe
  ihdr[9] = 6; // RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------- Zeichenhilfen

/** @param {string} hex @returns {[number, number, number]} Werte 0..1 */
function rgb(hex) {
  const n = Number.parseInt(hex.replace('#', ''), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const mix = (a, b, t) => a + (b - a) * t;
const mix3 = (a, b, t) => [mix(a[0], b[0], t), mix(a[1], b[1], t), mix(a[2], b[2], t)];
const smoothstep = (e0, e1, x) => {
  const t = clamp01((x - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
};
/** Kantenglättung: Abdeckung aus vorzeichenbehaftetem Abstand (negativ = innen). */
const cover = (d, aa = 1) => clamp01(0.5 - d / aa);

/** Abstand zu einem abgerundeten Rechteck (Mittelpunkt 0,0). */
function sdRoundRect(x, y, halfW, halfH, r) {
  const qx = Math.abs(x) - halfW + r;
  const qy = Math.abs(y) - halfH + r;
  const ox = Math.max(qx, 0);
  const oy = Math.max(qy, 0);
  return Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - r;
}

/** Näherungsweiser Abstand zu einer Ellipse (Mittelpunkt 0,0). */
function sdEllipse(x, y, rx, ry) {
  const f = (x * x) / (rx * rx) + (y * y) / (ry * ry) - 1;
  const gx = (2 * x) / (rx * rx);
  const gy = (2 * y) / (ry * ry);
  return f / Math.max(1e-6, Math.hypot(gx, gy));
}

/** Leinwand mit vormultiplizierten Float-Farben. */
class Canvas {
  constructor(w, h) {
    this.w = w;
    this.h = h;
    this.px = new Float64Array(w * h * 4);
  }

  /** Normales Überblenden (source over). */
  over(i, color, alpha) {
    if (alpha <= 0) return;
    const p = this.px;
    const k = 1 - alpha;
    p[i] = color[0] * alpha + p[i] * k;
    p[i + 1] = color[1] * alpha + p[i + 1] * k;
    p[i + 2] = color[2] * alpha + p[i + 2] * k;
    p[i + 3] = alpha + p[i + 3] * k;
  }

  /** Additives Licht (Glühen), Deckkraft bleibt erhalten. */
  add(i, color, amount) {
    if (amount <= 0) return;
    const p = this.px;
    p[i] = Math.min(p[i + 3], p[i] + color[0] * amount * p[i + 3]);
    p[i + 1] = Math.min(p[i + 3], p[i + 1] + color[1] * amount * p[i + 3]);
    p[i + 2] = Math.min(p[i + 3], p[i + 2] + color[2] * amount * p[i + 3]);
  }

  /** @param {(x: number, y: number, i: number) => void} fn */
  each(fn) {
    for (let y = 0; y < this.h; y++) {
      for (let x = 0; x < this.w; x++) fn(x + 0.5, y + 0.5, (y * this.w + x) * 4);
    }
  }

  /** @returns {Uint8Array} RGBA, nicht vormultipliziert */
  toRgba() {
    const out = new Uint8Array(this.w * this.h * 4);
    for (let i = 0; i < out.length; i += 4) {
      const a = this.px[i + 3];
      out[i + 3] = Math.round(clamp01(a) * 255);
      if (a > 0) {
        out[i] = Math.round(clamp01(this.px[i] / a) * 255);
        out[i + 1] = Math.round(clamp01(this.px[i + 1] / a) * 255);
        out[i + 2] = Math.round(clamp01(this.px[i + 2] / a) * 255);
      }
    }
    return out;
  }
}

// ---------------------------------------------------------------- App-Icon

function drawAppIcon(size = 1024) {
  const s = size / 1024; // alle Maße für 1024 px entworfen
  const c = new Canvas(size, size);
  const cx = 512 * s;
  const cy = 512 * s;

  const bgTop = rgb('#1b2036');
  const bgBottom = rgb('#07080e');
  const violet = rgb('#7c5cff');
  const cyan = rgb('#22d3ee');
  const deep = rgb('#2a1a7a');
  const white = [1, 1, 1];
  const black = [0, 0, 0];

  const half = 412 * s; // macOS-Raster: 824 px Körper auf 1024 px
  const radius = 186 * s;
  const orb = { x: cx, y: 500 * s, r: 196 * s };
  const tilt = (-18 * Math.PI) / 180;
  const ring = { rx: 318 * s, ry: 96 * s, w: 7 * s };
  const stars = [
    [300, 300, 4.5],
    [742, 268, 3.5],
    [270, 702, 3],
    [776, 690, 4],
    [690, 196, 2.5],
    [222, 486, 2.5],
  ];

  c.each((x, y, i) => {
    // Schlagschatten unter dem Körper
    const dShadow = sdRoundRect(x - cx, y - cy - 14 * s, half, half, radius);
    c.over(i, black, 0.45 * (1 - smoothstep(-10 * s, 34 * s, dShadow)));

    const d = sdRoundRect(x - cx, y - cy, half, half, radius);
    const body = cover(d, 1.2 * s);
    if (body <= 0) return;

    // Körper: dunkler Verlauf mit leichtem Violett-Schimmer
    const t = clamp01((y - (cy - half)) / (2 * half));
    let col = mix3(bgTop, bgBottom, t);
    const tint = Math.exp(-(((x - cx) ** 2 + (y - orb.y) ** 2) / (430 * s) ** 2) * 1.6);
    col = mix3(col, deep, 0.35 * tint);
    c.over(i, col, body);

    // feine Lichtkante oben
    const rim = cover(Math.abs(d + 1.5 * s) - 1.2 * s, 1.2 * s) * (1 - t) * 0.18;
    c.over(i, white, rim * body);

    // Halo um den Orb (additiv)
    const dr = Math.hypot(x - orb.x, y - orb.y);
    const halo = Math.exp(-(((dr - orb.r * 0.6) / (190 * s)) ** 2));
    const haloCol = mix3(violet, cyan, clamp01((x - orb.x + 300 * s) / (600 * s)));
    c.add(i, haloCol, 0.55 * halo * body);

    // Umlaufring – Rotation in das Ring-Koordinatensystem
    const rx = (x - orb.x) * Math.cos(-tilt) - (y - orb.y) * Math.sin(-tilt);
    const ry = (x - orb.x) * Math.sin(-tilt) + (y - orb.y) * Math.cos(-tilt);
    const ringD = Math.abs(sdEllipse(rx, ry, ring.rx, ring.ry)) - ring.w / 2;
    const ringA = cover(ringD, 1.2 * s);
    const ringGlow = Math.exp(-((Math.max(0, ringD) / (16 * s)) ** 2)) * 0.35;
    const ringCol = mix3(cyan, violet, clamp01((rx + ring.rx) / (2 * ring.rx)));
    const front = ry > 0; // untere Hälfte liegt vor dem Orb

    if (!front) {
      c.add(i, ringCol, ringGlow * body);
      c.over(i, ringCol, 0.85 * ringA * body);
    }

    // Orb: Farbverlauf diagonal, 3D-Schattierung, innerer Glanz
    const orbA = cover(dr - orb.r, 1.2 * s);
    if (orbA > 0) {
      const u = clamp01(((x - orb.x) + (y - orb.y)) / (2.4 * orb.r) + 0.5);
      let oc = mix3(violet, cyan, u);
      const nx = (x - orb.x) / orb.r;
      const ny = (y - orb.y) / orb.r;
      const nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
      const light = clamp01(-0.45 * nx - 0.6 * ny + 0.65 * nz); // Licht von links oben
      oc = mix3(mix3(oc, deep, 0.6), oc, 0.25 + 0.75 * light);
      const edge = smoothstep(0.75, 1.0, Math.hypot(nx, ny));
      oc = mix3(oc, cyan, 0.35 * edge); // leuchtender Rand
      c.over(i, oc, orbA * body);
      // Glanzpunkt
      const hx = (x - (orb.x - 70 * s)) / (88 * s);
      const hy = (y - (orb.y - 92 * s)) / (54 * s);
      const spec = Math.exp(-(hx * hx + hy * hy) * 2.2) * 0.6;
      c.over(i, white, spec * orbA * body);
      // Kern-Leuchten
      const core = Math.exp(-((dr / (orb.r * 0.5)) ** 2)) * 0.12;
      c.add(i, violet, core * orbA * body);
    }

    if (front) {
      c.add(i, ringCol, ringGlow * body);
      c.over(i, ringCol, 0.9 * ringA * body);
    }

    // kleine Sterne
    for (const [sx, sy, sr] of stars) {
      const dd = Math.hypot(x - sx * s, y - sy * s);
      const a = cover(dd - sr * s, 1.2 * s) * 0.8 + Math.exp(-((dd / (sr * 3.2 * s)) ** 2)) * 0.25;
      if (a > 0.001) c.over(i, mix3(white, cyan, 0.3), clamp01(a) * body);
    }
  });
  return encodePng(size, size, c.toRgba());
}

// ---------------------------------------------------------------- Tray-Icon (Template)

/**
 * Schwarzes Template-Icon: Ring mit Orb in der Mitte, 4×4-Supersampling für saubere Kanten.
 * @param {number} size 16 oder 32
 */
function drawTrayIcon(size) {
  const rgba = new Uint8Array(size * size * 4);
  const u = size / 16;
  const cx = size / 2;
  const cy = size / 2;
  const ringR = 6.6 * u;
  const ringW = 1.4 * u;
  const orbR = 3.2 * u;
  const n = 4;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let hits = 0;
      for (let sy = 0; sy < n; sy++) {
        for (let sx = 0; sx < n; sx++) {
          const px = x + (sx + 0.5) / n;
          const py = y + (sy + 0.5) / n;
          const d = Math.hypot(px - cx, py - cy);
          if (Math.abs(d - ringR) <= ringW / 2 || d <= orbR) hits++;
        }
      }
      const i = (y * size + x) * 4;
      rgba[i + 3] = Math.round((hits / (n * n)) * 255);
    }
  }
  return encodePng(size, size, rgba);
}

// ---------------------------------------------------------------- Ausgabe

function main() {
  const outputs = [
    [path.join(ROOT, 'build', 'icon.png'), () => drawAppIcon(1024)],
    [path.join(ROOT, 'assets', 'trayTemplate.png'), () => drawTrayIcon(16)],
    [path.join(ROOT, 'assets', 'trayTemplate@2x.png'), () => drawTrayIcon(32)],
  ];
  for (const [file, render] of outputs) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const png = render();
    fs.writeFileSync(file, png);
    console.log(`${path.relative(ROOT, file)}: ${png.length} Bytes`);
  }
}

if (require.main === module) main();

module.exports = { encodePng, crc32, drawAppIcon, drawTrayIcon };
