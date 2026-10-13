'use strict';
/**
 * Preload (sandboxed): stellt `window.vincent` bereit – die einzige Brücke der Renderer zum Hauptprozess.
 * Nur `require('electron')` ist hier erlaubt.
 */
const { contextBridge, ipcRenderer } = require('electron');

const versionArg = process.argv.find((a) => a.startsWith('--vincent-version='));
const version = versionArg ? versionArg.slice('--vincent-version='.length) : '';

/**
 * Abonniert einen Kanal vom Hauptprozess.
 * @param {string} channel
 * @param {(payload: any) => void} cb
 * @returns {() => void} Abmelden
 */
function subscribe(channel, cb) {
  if (typeof cb !== 'function') throw new TypeError('Callback erwartet.');
  const handler = (_event, payload) => cb(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld('vincent', {
  /** @returns {Promise<{port: number|null, token: string, status: string, message: string}>} */
  getBackend: () => ipcRenderer.invoke('backend:info'),
  /** cb({port, token, status, message}) bei jeder Statusänderung des Backends; liefert Abmeldefunktion */
  onBackendStatus: (cb) => subscribe('backend:status', cb),
  openZentrale: () => ipcRenderer.invoke('zentrale:open'),
  /** @param {boolean} [open] ohne Angabe: umschalten */
  toggleChat: (open) => ipcRenderer.invoke('overlay:toggleChat', typeof open === 'boolean' ? open : null),
  /** @param {boolean} enabled true = Mausklicks gehen durch das Overlay hindurch */
  setClickThrough: (enabled) => ipcRenderer.invoke('overlay:clickThrough', Boolean(enabled)),
  /** Overlay fokussieren, damit ins Chat-Feld getippt werden kann */
  focusOverlay: () => ipcRenderer.invoke('overlay:focus'),
  /** @param {number} dy Overlay vertikal verschieben (Pixel) */
  moveOverlay: (dy) => ipcRenderer.invoke('overlay:move', Number(dy)),
  /**
   * Overlay frei verschieben (Ziehen); zone = Avatar-Bereich im Fenster {left, right} in Pixeln.
   * @returns {Promise<{x: number, y: number, width: number, height: number, workArea: object}|null>}
   */
  moveOverlayBy: (dx, dy, zone) => ipcRenderer.invoke('overlay:moveBy', Number(dx), Number(dy),
    zone ? { left: Number(zone.left), right: Number(zone.right) } : null),
  /** @param {'left'|'right'} edge Overlay an den linken/rechten Rand setzen; dock=false: nur merken */
  setOverlayEdge: (edge, dock = true) => ipcRenderer.invoke('overlay:setEdge', edge === 'left' ? 'left' : 'right', dock !== false),
  /** cb({action: 'listen'|'stop'|'toggle', visible?}) bei globalen Tastenkürzeln/Tray-Aktionen */
  onShortcut: (cb) => subscribe('shortcut', cb),
  /** cb({open: boolean|null}) wenn der Chat geöffnet/geschlossen werden soll */
  onChatToggle: (cb) => subscribe('chat:toggle', cb),
  /** @returns {Promise<{microphone: string, screen: string, accessibility: boolean|string}>} */
  getPermissions: () => ipcRenderer.invoke('permissions:get'),
  /** @returns {Promise<boolean>} */
  requestMicrophone: () => ipcRenderer.invoke('permissions:request'),
  /** @param {'microphone'|'screen'|'accessibility'} kind */
  openPermissionSettings: (kind) => ipcRenderer.invoke('permissions:open', String(kind)),
  quit: () => ipcRenderer.invoke('app:quit'),
  platform: process.platform,
  version,
});
