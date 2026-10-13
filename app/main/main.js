'use strict';
/**
 * XRZ Vincent – Hauptprozess: App-Lebenszyklus, Single-Instance, Tray, globale Tastenkürzel,
 * IPC-Brücke zu den Renderern und Überwachung des Python-Backends.
 */
const path = require('node:path');
const { app, BrowserWindow, Menu, Tray, globalShortcut, ipcMain, nativeImage, session } = require('electron');

const { BackendSupervisor } = require('./backend');
const permissions = require('./permissions');
const windows = require('./windows');

const isMac = process.platform === 'darwin';
const ASSETS = path.join(__dirname, '..', 'assets');

/** @type {BackendSupervisor|null} */
let backend = null;
/** @type {Tray|null} */
let tray = null;
let micMuted = false;
let cleanupDone = false;
let cleanupRunning = false;

/** Status an alle offenen Fenster senden. */
function broadcast(channel, payload) {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed() && !win.webContents.isDestroyed()) win.webContents.send(channel, payload);
  }
}

/** @param {string} channel @param {unknown} payload */
function sendToOverlay(channel, payload) {
  const win = windows.getOverlay();
  if (win && !win.webContents.isDestroyed()) win.webContents.send(channel, payload);
}

/**
 * Backend-Aufruf aus dem Hauptprozess (Tastenkürzel, Tray); Fehler werden nur protokolliert.
 * @param {string} method @param {string} urlPath @param {unknown} [body]
 */
async function callBackend(method, urlPath, body) {
  if (!backend) return null;
  try {
    return await backend.request(method, urlPath, body);
  } catch (err) {
    console.warn(`[main] ${method} ${urlPath} fehlgeschlagen: ${err.message}`);
    return null;
  }
}

function openZentrale() {
  windows.createZentrale();
  if (isMac) app.focus({ steal: true });
}

function listen() {
  windows.setOverlayVisible(true);
  sendToOverlay('shortcut', { action: 'listen' });
  void callBackend('POST', '/api/voice/listen');
}

function stopVoice() {
  sendToOverlay('shortcut', { action: 'stop' });
  void callBackend('POST', '/api/voice/stop');
}

function toggleOverlay() {
  const visible = windows.setOverlayVisible();
  sendToOverlay('shortcut', { action: 'toggle', visible });
  updateTrayMenu();
}

async function setMuted(muted) {
  const res = await callBackend('POST', '/api/voice/mute', { muted });
  if (res && typeof res.muted === 'boolean') micMuted = res.muted;
  updateTrayMenu();
}

// ---------------------------------------------------------------- Menüs & Tray

function buildAppMenu() {
  /** @type {Electron.MenuItemConstructorOptions[]} */
  const template = [];
  if (isMac) {
    template.push({
      label: app.name,
      submenu: [
        { role: 'about', label: `Über ${app.name}` },
        { type: 'separator' },
        { label: 'Zentrale öffnen …', accelerator: 'Cmd+,', click: openZentrale },
        { type: 'separator' },
        { role: 'services', label: 'Dienste' },
        { type: 'separator' },
        { role: 'hide', label: `${app.name} ausblenden` },
        { role: 'hideOthers', label: 'Andere ausblenden' },
        { role: 'unhide', label: 'Alle einblenden' },
        { type: 'separator' },
        { role: 'quit', label: `${app.name} beenden` },
      ],
    });
  }
  template.push(
    {
      label: 'Bearbeiten',
      submenu: [
        { role: 'undo', label: 'Widerrufen' },
        { role: 'redo', label: 'Wiederholen' },
        { type: 'separator' },
        { role: 'cut', label: 'Ausschneiden' },
        { role: 'copy', label: 'Kopieren' },
        { role: 'paste', label: 'Einsetzen' },
        { role: 'selectAll', label: 'Alles auswählen' },
      ],
    },
    {
      label: 'Ansicht',
      submenu: [
        { label: 'Vincent zeigen/verstecken', accelerator: 'Alt+Shift+V', registerAccelerator: false, click: toggleOverlay },
        { label: 'Zentrale öffnen', click: openZentrale },
        { type: 'separator' },
        { role: 'reload', label: 'Neu laden' },
        { role: 'toggleDevTools', label: 'Entwicklerwerkzeuge' },
        { type: 'separator' },
        { role: 'resetZoom', label: 'Originalgröße' },
        { role: 'zoomIn', label: 'Vergrößern' },
        { role: 'zoomOut', label: 'Verkleinern' },
      ],
    },
    {
      label: 'Fenster',
      submenu: [
        { role: 'minimize', label: 'Im Dock ablegen' },
        { role: 'close', label: 'Fenster schließen' },
        ...(isMac ? [{ type: 'separator' }, { role: 'front', label: 'Alle nach vorne bringen' }] : []),
      ],
    },
  );
  if (!isMac) {
    template.push({ label: 'Vincent', submenu: [{ role: 'quit', label: 'Beenden' }] });
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function updateTrayMenu() {
  if (!tray || tray.isDestroyed()) return;
  const status = backend ? backend.info() : null;
  const ready = status?.status === 'ready';
  tray.setToolTip(status ? `XRZ Vincent – ${status.message}` : 'XRZ Vincent');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Vincent zeigen/verstecken', click: toggleOverlay },
      { label: 'Zentrale öffnen', click: openZentrale },
      { label: 'Zuhören (⌥ Leertaste)', enabled: ready && !micMuted, click: listen },
      { label: 'Mikrofon stumm', type: 'checkbox', checked: micMuted, enabled: ready, click: (item) => void setMuted(item.checked) },
      { type: 'separator' },
      { label: 'Beenden', click: () => app.quit() },
    ]),
  );
}

function createTray() {
  const image = nativeImage.createFromPath(path.join(ASSETS, 'trayTemplate.png'));
  if (isMac) image.setTemplateImage(true);
  tray = new Tray(image);
  updateTrayMenu();
}

// ---------------------------------------------------------------- Tastenkürzel

function registerShortcuts() {
  const shortcuts = [
    ['Alt+Space', listen],
    ['Alt+Shift+V', toggleOverlay],
    ['Alt+Shift+S', stopVoice],
  ];
  for (const [accelerator, handler] of shortcuts) {
    let ok = false;
    try {
      ok = globalShortcut.register(accelerator, handler);
    } catch (err) {
      console.warn(`[main] Tastenkürzel ${accelerator} ungültig: ${err.message}`);
    }
    if (!ok) console.warn(`[main] Tastenkürzel ${accelerator} ist bereits belegt und wurde nicht registriert.`);
  }
}

// ---------------------------------------------------------------- IPC

/**
 * Nur Anfragen aus den eigenen Renderer-Seiten (file://…/renderer/) annehmen.
 * @param {Electron.IpcMainInvokeEvent} event
 */
function trusted(event) {
  const url = event.senderFrame ? event.senderFrame.url : '';
  return windows.isAppUrl(url);
}

/**
 * @param {string} channel
 * @param {(event: Electron.IpcMainInvokeEvent, ...args: any[]) => any} handler
 */
function handle(channel, handler) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!trusted(event)) throw new Error('Nicht erlaubt.');
    return handler(event, ...args);
  });
}

function registerIpc() {
  handle('backend:info', () => (backend ? backend.info() : { port: null, token: '', status: 'idle', message: 'Backend nicht gestartet.' }));
  handle('zentrale:open', () => {
    openZentrale();
    return true;
  });
  handle('overlay:toggleChat', (_e, open) => {
    const value = typeof open === 'boolean' ? open : null;
    if (value !== false) windows.setOverlayVisible(true);
    sendToOverlay('chat:toggle', { open: value });
    return true;
  });
  handle('overlay:clickThrough', (_e, enabled) => {
    const win = windows.getOverlay();
    if (win) win.setIgnoreMouseEvents(Boolean(enabled), { forward: true });
    return Boolean(win);
  });
  handle('overlay:focus', () => {
    const win = windows.getOverlay();
    if (!win) return false;
    if (!win.isVisible()) win.showInactive();
    win.focus();
    return true;
  });
  handle('overlay:move', (_e, dy) => {
    windows.moveOverlay(Number(dy));
    return true;
  });
  handle('overlay:setEdge', (_e, edge) => {
    windows.setOverlayEdge(edge === 'left' ? 'left' : 'right');
    return true;
  });
  handle('permissions:get', () => permissions.getStatus());
  handle('permissions:request', () => permissions.requestMicrophone());
  handle('permissions:open', (_e, kind) => permissions.openSettings(kind));
  handle('app:quit', () => {
    setImmediate(() => app.quit());
    return true;
  });
}

// ---------------------------------------------------------------- Backend

/** Nach dem Start Rand des Overlays und Mikrofon-Status aus dem Backend übernehmen. */
async function syncFromBackend() {
  const settings = await callBackend('GET', '/api/settings');
  const edge = settings?.settings?.appearance?.edge;
  if (edge === 'left' || edge === 'right') windows.setOverlayEdge(edge);
  const state = await callBackend('GET', '/api/state');
  if (typeof state?.voice?.muted === 'boolean') micMuted = state.voice.muted;
  updateTrayMenu();
}

function startBackend() {
  backend = new BackendSupervisor({
    logDir: path.join(app.getPath('userData'), 'logs'),
    resourcesPath: app.isPackaged ? process.resourcesPath : undefined,
    echo: !app.isPackaged,
  });
  backend.on('status', (event) => {
    console.log(`[main] Backend: ${event.status} – ${event.message.split('\n')[0]}`);
    broadcast('backend:status', backend ? backend.info() : event);
    updateTrayMenu();
    if (event.status === 'ready') void syncFromBackend();
  });
  void backend.start();
}

// ---------------------------------------------------------------- Lebenszyklus

/** Sauber beenden: Tastenkürzel lösen, Backend stoppen (SIGTERM → SIGKILL), dann wirklich beenden. */
async function cleanup() {
  cleanupRunning = true;
  windows.setQuitting(true);
  globalShortcut.unregisterAll();
  try {
    if (backend) await backend.stop();
  } catch (err) {
    console.error('[main] Backend ließ sich nicht sauber beenden:', err.message);
    backend?.killNow();
  }
  if (tray && !tray.isDestroyed()) tray.destroy();
  tray = null;
  cleanupDone = true;
}

/** Registriert alle App-Ereignisse; läuft nur in der ersten Instanz. */
function main() {
  app.on('before-quit', (event) => {
    windows.setQuitting(true);
    if (cleanupDone) return;
    event.preventDefault();
    if (cleanupRunning) return;
    void cleanup().finally(() => app.quit());
  });

  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    backend?.killNow();
  });

  process.on('exit', () => backend?.killNow());
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    process.on(signal, () => app.quit());
  }

  app.on('second-instance', () => {
    if (app.isReady()) openZentrale();
  });

  app.on('activate', () => {
    if (app.isReady()) openZentrale();
  });

  // Overlay kann versteckt sein – beenden nur über Tray, Menü oder ⌘Q.
  app.on('window-all-closed', () => {});

  // Jeder WebContents (auch künftige) darf keine Fenster öffnen und nicht wegnavigieren.
  app.on('web-contents-created', (_event, contents) => windows.harden(contents));

  app.whenReady().then(async () => {
    buildAppMenu();
    // Renderer brauchen keine Geräte-/Standortrechte – nur Zwischenablage & Benachrichtigungen.
    const allowed = new Set(['clipboard-sanitized-write', 'notifications']);
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => callback(allowed.has(permission)));

    if (isMac) {
      app.dock?.show();
      // Zuerst Mikrofon anfragen: macOS ordnet den Zugriff des Python-Kindprozesses dieser App zu.
      await permissions.requestMicrophone();
    }
    registerIpc();
    startBackend();
    windows.createOverlay({ edge: 'right' });
    if (isMac) app.dock?.show(); // setVisibleOnAllWorkspaces kann das Dock-Icon ausblenden
    createTray();
    registerShortcuts();
  }).catch((err) => {
    console.error('[main] Start fehlgeschlagen:', err);
  });
}

if (app.requestSingleInstanceLock()) {
  main();
} else {
  app.quit();
}
