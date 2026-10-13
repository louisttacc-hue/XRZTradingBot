'use strict';
/**
 * Fenster-Fabrik: Overlay (Vincent am Bildschirmrand, immer im Vordergrund, klick-durchlässig)
 * und Zentrale (normales App-Fenster). Beide mit abgesicherten webPreferences.
 */
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { app, BrowserWindow, screen, shell } = require('electron');

const isMac = process.platform === 'darwin';
const RENDERER_DIR = path.join(__dirname, '..', 'renderer');
const RENDERER_URL = pathToFileURL(RENDERER_DIR + path.sep).href;
const PRELOAD = path.join(__dirname, 'preload.js');

const OVERLAY_SIZE = { width: 440, height: 680 };
const ZENTRALE_BG = '#0b0d14';

/** @type {BrowserWindow|null} */
let overlay = null;
/** @type {BrowserWindow|null} */
let zentrale = null;
/** @type {'left'|'right'} */
let overlayEdge = 'right';
let quitting = false;

/**
 * @typedef {{x: number, y: number, width: number, height: number}} Rect
 */

/**
 * Position des Overlays: am rechten (oder linken) Rand der Arbeitsfläche, vertikal zentriert.
 * Breite/Höhe werden auf die Arbeitsfläche begrenzt.
 *
 * @param {Rect} workArea
 * @param {{width: number, height: number, edge?: 'left'|'right', margin?: number}} size
 * @returns {Rect}
 */
function computeOverlayBounds(workArea, { width, height, edge = 'right', margin = 0 }) {
  const w = Math.max(1, Math.min(Math.round(width), workArea.width - 2 * margin));
  const h = Math.max(1, Math.min(Math.round(height), workArea.height));
  const x = edge === 'left' ? workArea.x + margin : workArea.x + workArea.width - w - margin;
  const y = workArea.y + Math.round((workArea.height - h) / 2);
  return { x: Math.round(x), y, width: w, height: h };
}

/**
 * Verschiebt ein Fenster vertikal um dy, ohne die Arbeitsfläche zu verlassen.
 *
 * @param {Rect} bounds
 * @param {Rect} workArea
 * @param {number} dy
 * @returns {Rect}
 */
function shiftWithin(bounds, workArea, dy) {
  const minY = workArea.y;
  const maxY = workArea.y + Math.max(0, workArea.height - bounds.height);
  const y = Math.min(maxY, Math.max(minY, Math.round(bounds.y + dy)));
  return { ...bounds, y };
}

/**
 * Prüft, ob eine URL zu den eigenen Renderer-Seiten gehört.
 * @param {string} url
 */
function isAppUrl(url) {
  return typeof url === 'string' && url.startsWith(RENDERER_URL);
}

/** @param {string} url */
function isWebUrl(url) {
  try {
    const { protocol } = new URL(url);
    return protocol === 'https:' || protocol === 'http:';
  } catch {
    return false;
  }
}

/** @param {string} url */
function openExternalSafe(url) {
  if (!isWebUrl(url)) return;
  shell.openExternal(url).catch((err) => console.error('[windows] Link nicht geöffnet:', err.message));
}

/**
 * Keine neuen Fenster, keine Navigation weg von den eigenen Seiten; Web-Links gehen in den Standardbrowser.
 * @param {Electron.WebContents} wc
 */
function harden(wc) {
  wc.setWindowOpenHandler(({ url }) => {
    openExternalSafe(url);
    return { action: 'deny' };
  });
  wc.on('will-navigate', (event, url) => {
    if (isAppUrl(url)) return;
    event.preventDefault();
    openExternalSafe(url);
  });
  wc.on('will-redirect', (event, url) => {
    if (!isAppUrl(url)) event.preventDefault();
  });
  wc.on('will-attach-webview', (event) => event.preventDefault());
}

/** @returns {Electron.WebPreferences} */
function securePreferences(extra = {}) {
  return {
    preload: PRELOAD,
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    webSecurity: true,
    spellcheck: false,
    additionalArguments: [`--vincent-version=${app.getVersion()}`],
    ...extra,
  };
}

/** Overlay an den eingestellten Rand der Hauptanzeige setzen. */
function dockOverlay() {
  if (!overlay || overlay.isDestroyed()) return;
  const { workArea } = screen.getPrimaryDisplay();
  overlay.setBounds(computeOverlayBounds(workArea, { ...OVERLAY_SIZE, edge: overlayEdge }));
}

/**
 * Erstellt das Overlay-Fenster (einmalig) und zeigt es, ohne TradingView den Fokus zu nehmen.
 * @param {{edge?: 'left'|'right'}} [opts]
 * @returns {BrowserWindow}
 */
function createOverlay(opts = {}) {
  if (overlay && !overlay.isDestroyed()) return overlay;
  overlayEdge = opts.edge === 'left' ? 'left' : 'right';

  /** @type {Electron.BrowserWindowConstructorOptions} */
  const options = {
    ...OVERLAY_SIZE,
    title: 'Vincent',
    transparent: true,
    frame: false,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    hasShadow: false,
    skipTaskbar: true,
    fullscreenable: false,
    focusable: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: securePreferences({ backgroundThrottling: false }),
  };
  // NSPanel: schwebt auch über Vollbild-Apps und aktiviert die App beim Klick nicht (Electron ≥ 26)
  const electronMajor = Number.parseInt(process.versions.electron || '0', 10);
  if (isMac && electronMajor >= 26) options.type = 'panel';

  const win = new BrowserWindow(options);
  overlay = win;
  win.setAlwaysOnTop(true, 'floating');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  harden(win.webContents);
  dockOverlay();

  const redock = () => dockOverlay();
  screen.on('display-metrics-changed', redock);
  screen.on('display-removed', redock);
  screen.on('display-added', redock);

  win.on('close', (event) => {
    // Overlay nur beim Beenden wirklich schließen, sonst nur verstecken (z. B. ⌘W)
    if (!quitting) {
      event.preventDefault();
      win.hide();
    }
  });
  win.on('closed', () => {
    screen.removeListener('display-metrics-changed', redock);
    screen.removeListener('display-removed', redock);
    screen.removeListener('display-added', redock);
    if (overlay === win) overlay = null;
  });
  win.webContents.on('render-process-gone', (_e, details) => {
    console.error('[windows] Overlay-Renderer beendet:', details.reason);
    if (!quitting && details.reason !== 'clean-exit' && !win.isDestroyed()) win.webContents.reload();
  });
  win.once('ready-to-show', () => {
    if (!win.isDestroyed()) win.showInactive();
  });
  win.loadFile(path.join(RENDERER_DIR, 'overlay', 'index.html')).catch((err) => {
    console.error('[windows] Overlay konnte nicht geladen werden:', err.message);
  });
  return win;
}

/**
 * Öffnet die Zentrale bzw. holt ein bereits offenes Fenster nach vorne.
 * @returns {BrowserWindow}
 */
function createZentrale() {
  if (zentrale && !zentrale.isDestroyed()) {
    if (zentrale.isMinimized()) zentrale.restore();
    zentrale.show();
    zentrale.focus();
    return zentrale;
  }
  /** @type {Electron.BrowserWindowConstructorOptions} */
  const options = {
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    title: 'XRZ Vincent – Zentrale',
    show: false,
    backgroundColor: ZENTRALE_BG,
    webPreferences: securePreferences(),
  };
  if (isMac) options.titleBarStyle = 'hiddenInset';

  const win = new BrowserWindow(options);
  zentrale = win;
  harden(win.webContents);
  win.once('ready-to-show', () => {
    if (win.isDestroyed()) return;
    win.show();
    win.focus();
  });
  win.on('closed', () => {
    if (zentrale === win) zentrale = null;
  });
  win.loadFile(path.join(RENDERER_DIR, 'zentrale', 'index.html')).catch((err) => {
    console.error('[windows] Zentrale konnte nicht geladen werden:', err.message);
  });
  return win;
}

/** @returns {BrowserWindow|null} */
function getOverlay() {
  return overlay && !overlay.isDestroyed() ? overlay : null;
}

/** @returns {BrowserWindow|null} */
function getZentrale() {
  return zentrale && !zentrale.isDestroyed() ? zentrale : null;
}

/**
 * Overlay ein-/ausblenden.
 * @param {boolean} [visible] ohne Angabe: umschalten
 * @returns {boolean} neuer Sichtbarkeitszustand
 */
function setOverlayVisible(visible) {
  const win = getOverlay();
  if (!win) return false;
  const show = visible === undefined ? !win.isVisible() : Boolean(visible);
  if (show) {
    dockOverlayIfOffscreen(win);
    win.showInactive();
  } else {
    win.hide();
  }
  return show;
}

/**
 * Holt das Overlay zurück, falls es (z. B. nach Monitorwechsel) außerhalb jeder Anzeige liegt.
 * @param {BrowserWindow} win
 */
function dockOverlayIfOffscreen(win) {
  const b = win.getBounds();
  const visible = screen.getAllDisplays().some(({ workArea: a }) => b.x < a.x + a.width && b.x + b.width > a.x && b.y < a.y + a.height && b.y + b.height > a.y);
  if (!visible) dockOverlay();
}

/**
 * Overlay vertikal verschieben (innerhalb der Anzeige, auf der es gerade liegt).
 * @param {number} dy
 */
function moveOverlay(dy) {
  const win = getOverlay();
  if (!win || !Number.isFinite(dy)) return;
  const bounds = win.getBounds();
  const { workArea } = screen.getDisplayMatching(bounds);
  win.setBounds(shiftWithin(bounds, workArea, dy));
}

/**
 * Rand für das Overlay setzen und neu andocken.
 * @param {'left'|'right'} edge
 */
function setOverlayEdge(edge) {
  const next = edge === 'left' ? 'left' : 'right';
  if (next === overlayEdge) return;
  overlayEdge = next;
  dockOverlay();
}

/** Beim Beenden aufrufen, damit das Overlay wirklich geschlossen wird. */
function setQuitting(value) {
  quitting = Boolean(value);
}

module.exports = {
  computeOverlayBounds,
  shiftWithin,
  isAppUrl,
  isWebUrl,
  createOverlay,
  createZentrale,
  getOverlay,
  getZentrale,
  setOverlayVisible,
  moveOverlay,
  setOverlayEdge,
  setQuitting,
  OVERLAY_SIZE,
  RENDERER_URL,
};
