'use strict';
/**
 * Unit-Tests für die Electron-Hülle ohne Electron: reine Logik (Pfadauflösung, Umgebung, Overlay-Position),
 * Berechtigungen mit Fakes, Backend-Supervisor gegen einen Python-Stub und der Renderer-Client api.js.
 * Aufruf: `npm test` (= `node --test test/`).
 */
const { describe, it, before, after } = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

// ---------------------------------------------------------------- Electron-Fake

const fakeElectron = {
  app: { getVersion: () => '2.0.0' },
  BrowserWindow: class {},
  screen: {},
  shell: {
    opened: /** @type {string[]} */ ([]),
    async openExternal(url) {
      if (typeof url !== 'string') throw new TypeError('url');
      fakeElectron.shell.opened.push(url);
    },
  },
  systemPreferences: {
    getMediaAccessStatus: (kind) => (kind === 'microphone' ? 'granted' : 'denied'),
    isTrustedAccessibilityClient: (prompt) => prompt === false,
    askForMediaAccess: async (kind) => kind === 'microphone',
  },
};
const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === 'electron') return fakeElectron;
  return originalLoad.call(this, request, parent, isMain);
};

/**
 * Modul frisch laden und dabei process.platform vortäuschen.
 * @param {string} rel @param {NodeJS.Platform} platform
 */
function loadWithPlatform(rel, platform) {
  const file = require.resolve(rel);
  delete require.cache[file];
  const desc = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: platform, configurable: true });
  try {
    return require(rel);
  } finally {
    Object.defineProperty(process, 'platform', desc);
    delete require.cache[file];
  }
}

const backend = require('../main/backend');
const windows = require('../main/windows');

// ---------------------------------------------------------------- Pfadauflösung

describe('resolveBackendCommand', () => {
  const repo = '/repo';
  const existing = (...paths) => (p) => paths.includes(p);

  it('nutzt VINCENT_PYTHON und VINCENT_CWD aus der Umgebung', () => {
    const r = backend.resolveBackendCommand({
      env: { VINCENT_PYTHON: '/opt/py/bin/python', VINCENT_CWD: '/work' },
      resourcesPath: '/res',
      repoRoot: repo,
      exists: existing('/opt/py/bin/python', '/work', '/res/backend.json'),
    });
    assert.deepEqual(r, { python: '/opt/py/bin/python', cwd: '/work', source: 'env' });
  });

  it('nimmt ohne VINCENT_CWD das Repo als Arbeitsverzeichnis', () => {
    const r = backend.resolveBackendCommand({
      env: { VINCENT_PYTHON: '/opt/py/bin/python' },
      repoRoot: repo,
      resourcesPath: '',
      exists: existing('/opt/py/bin/python', repo),
    });
    assert.equal(r.cwd, repo);
  });

  it('meldet einen fehlenden Interpreter auf Deutsch', () => {
    assert.throws(
      () => backend.resolveBackendCommand({ env: { VINCENT_PYTHON: '/nope/python' }, repoRoot: repo, exists: () => false }),
      /Python-Interpreter nicht gefunden: \/nope\/python/,
    );
  });

  it('liest backend.json in der gepackten App', () => {
    const r = backend.resolveBackendCommand({
      env: {},
      resourcesPath: '/res',
      repoRoot: repo,
      exists: existing('/res/backend.json', '/Users/a/xrz/.venv/bin/python', '/Users/a/xrz'),
      readFile: () => JSON.stringify({ python: '/Users/a/xrz/.venv/bin/python', cwd: '/Users/a/xrz' }),
    });
    assert.deepEqual(r, { python: '/Users/a/xrz/.venv/bin/python', cwd: '/Users/a/xrz', source: 'packaged' });
  });

  it('leitet cwd aus dem venv ab, wenn backend.json keins enthält', () => {
    const r = backend.resolveBackendCommand({
      env: {},
      resourcesPath: '/res',
      repoRoot: repo,
      exists: existing('/res/backend.json', '/Users/a/xrz/.venv/bin/python', '/Users/a/xrz'),
      readFile: () => JSON.stringify({ python: '/Users/a/xrz/.venv/bin/python' }),
    });
    assert.equal(r.cwd, '/Users/a/xrz');
  });

  it('meldet eine kaputte backend.json', () => {
    assert.throws(
      () =>
        backend.resolveBackendCommand({
          env: {},
          resourcesPath: '/res',
          repoRoot: repo,
          exists: existing('/res/backend.json'),
          readFile: () => '{kaputt',
        }),
      /backend\.json ist ungültig/,
    );
  });

  it('fällt in der Entwicklung auf <repo>/.venv/bin/python zurück', () => {
    const r = backend.resolveBackendCommand({
      env: {},
      resourcesPath: '/res',
      repoRoot: repo,
      exists: existing('/repo/.venv/bin/python', repo),
    });
    assert.deepEqual(r, { python: '/repo/.venv/bin/python', cwd: repo, source: 'dev' });
  });

  it('erklärt, was fehlt, wenn gar nichts gefunden wird', () => {
    assert.throws(
      () => backend.resolveBackendCommand({ env: {}, resourcesPath: '/res', repoRoot: repo, exists: () => false }),
      (err) => /Kein Python-Backend gefunden/.test(err.message) && err.message.includes('/res/backend.json') && /install_mac\.sh/.test(err.message),
    );
  });
});

// ---------------------------------------------------------------- Umgebung & Argumente

describe('cleanEnv / buildArgs / backoffMs', () => {
  it('entfernt ELECTRON_* und NODE_OPTIONS und setzt Token + ungepufferte Ausgabe', () => {
    const env = backend.cleanEnv(
      {
        PATH: '/usr/bin:/bin',
        HOME: '/home/x',
        ELECTRON_RUN_AS_NODE: '1',
        ELECTRON_ENABLE_LOGGING: '1',
        electron_custom: 'x',
        NODE_OPTIONS: '--inspect',
        UNDEF: undefined,
      },
      { VINCENT_TOKEN: 'abc' },
      'linux',
    );
    assert.deepEqual(env, { PATH: '/usr/bin:/bin', HOME: '/home/x', PYTHONUNBUFFERED: '1', VINCENT_TOKEN: 'abc' });
  });

  it('ergänzt auf macOS die Homebrew-Pfade', () => {
    const env = backend.cleanEnv({ PATH: '/usr/bin:/bin' }, {}, 'darwin');
    assert.deepEqual(env.PATH.split(':'), ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']);
    const again = backend.cleanEnv({ PATH: '/opt/homebrew/bin:/usr/bin' }, {}, 'darwin');
    assert.equal(again.PATH.split(':').filter((p) => p === '/opt/homebrew/bin').length, 1);
  });

  it('baut die serve-Kommandozeile (mit und ohne --demo)', () => {
    assert.deepEqual(backend.buildArgs(5123, 42, false), ['-m', 'xrz', 'serve', '--port', '5123', '--parent-pid', '42']);
    assert.deepEqual(backend.buildArgs(5123, 42, true).slice(-1), ['--demo']);
  });

  it('verdoppelt den Backoff bis maximal 30 s', () => {
    assert.deepEqual([1, 2, 3, 4, 5, 6, 7].map(backend.backoffMs), [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });

  it('findet einen freien Port auf 127.0.0.1', async () => {
    const port = await backend.pickFreePort();
    assert.ok(Number.isInteger(port) && port > 0 && port < 65536);
  });
});

// ---------------------------------------------------------------- Overlay-Geometrie

describe('computeOverlayBounds / shiftWithin', () => {
  const workArea = { x: 0, y: 25, width: 1512, height: 920 };

  it('dockt rechts an und zentriert vertikal', () => {
    assert.deepEqual(windows.computeOverlayBounds(workArea, { width: 440, height: 680 }), {
      x: 1512 - 440,
      y: 25 + 120,
      width: 440,
      height: 680,
    });
  });

  it('dockt links an', () => {
    const b = windows.computeOverlayBounds(workArea, { width: 440, height: 680, edge: 'left' });
    assert.equal(b.x, 0);
    assert.equal(b.y, 145);
  });

  it('berücksichtigt Anzeigen mit Versatz und Rand', () => {
    const b = windows.computeOverlayBounds({ x: -1920, y: 0, width: 1920, height: 1080 }, { width: 440, height: 680, margin: 8 });
    assert.deepEqual(b, { x: -1920 + 1920 - 440 - 8, y: 200, width: 440, height: 680 });
  });

  it('verkleinert das Overlay auf kleinen Bildschirmen', () => {
    const b = windows.computeOverlayBounds({ x: 0, y: 0, width: 400, height: 600 }, { width: 440, height: 680 });
    assert.deepEqual(b, { x: 0, y: 0, width: 400, height: 600 });
  });

  it('verschiebt vertikal nur innerhalb der Arbeitsfläche', () => {
    const b = { x: 1072, y: 145, width: 440, height: 680 };
    assert.equal(windows.shiftWithin(b, workArea, 50).y, 195);
    assert.equal(windows.shiftWithin(b, workArea, -1000).y, 25);
    assert.equal(windows.shiftWithin(b, workArea, 1000).y, 25 + 920 - 680);
  });

  it('erkennt eigene Renderer-URLs und Web-Links', () => {
    assert.ok(windows.isAppUrl(`${windows.RENDERER_URL}overlay/index.html`));
    assert.ok(!windows.isAppUrl('file:///etc/passwd'));
    assert.ok(!windows.isAppUrl('https://example.com'));
    assert.ok(windows.isWebUrl('https://de.tradingview.com/chart/'));
    assert.ok(!windows.isWebUrl('javascript:alert(1)'));
    assert.ok(!windows.isWebUrl('file:///etc/passwd'));
  });
});

// ---------------------------------------------------------------- Berechtigungen

describe('permissions', () => {
  it('liefert auf Nicht-Mac "not-applicable"', async () => {
    const p = loadWithPlatform('../main/permissions', 'linux');
    assert.deepEqual(p.getStatus(), { microphone: 'not-applicable', screen: 'not-applicable', accessibility: 'not-applicable' });
    assert.equal(await p.requestMicrophone(), true);
    assert.equal(await p.openSettings('microphone'), false);
  });

  it('fragt auf macOS die Systemeinstellungen ab (Fake)', async () => {
    const p = loadWithPlatform('../main/permissions', 'darwin');
    assert.deepEqual(p.getStatus(), { microphone: 'granted', screen: 'denied', accessibility: true });
    assert.equal(await p.requestMicrophone(), true);
    fakeElectron.shell.opened.length = 0;
    assert.equal(await p.openSettings('screen'), true);
    assert.equal(await p.openSettings('__proto__'), false);
    assert.equal(await p.openSettings('kamera'), false);
    assert.deepEqual(fakeElectron.shell.opened, ['x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture']);
  });
});

// ---------------------------------------------------------------- Supervisor gegen Python-Stub

const STUB_MAIN = `
import json, os, signal, sys, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
print("stub gestartet", args, flush=True)
if os.environ.get("STUB_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.environ.get("STUB_NO_HEALTH"):
    while True:
        time.sleep(1)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print("stub", fmt % a, flush=True)

    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/health":
            return self.send_json(200, {"ok": True, "version": "stub"})
        self.send_json(404, {"detail": "Nicht gefunden"})

    def do_POST(self):
        if self.headers.get("X-Vincent-Token") != os.environ.get("VINCENT_TOKEN"):
            return self.send_json(401, {"detail": "Token ungültig"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null")
        if self.path == "/api/echo":
            return self.send_json(200, {
                "argv": args, "body": body, "cwd": os.getcwd(),
                "electron_keys": [k for k in os.environ if k.upper().startswith("ELECTRON_")],
                "node_options": os.environ.get("NODE_OPTIONS"),
                "unbuffered": os.environ.get("PYTHONUNBUFFERED"),
            })
        if self.path == "/api/fail":
            return self.send_json(400, {"detail": "Absichtlicher Fehler"})
        if self.path == "/api/crash":
            self.send_json(200, {"ok": True})
            os._exit(3)
        self.send_json(404, {"detail": "Nicht gefunden"})

ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
`;

function findPython() {
  const candidates = [path.join(backend.REPO_ROOT, '.venv', 'bin', 'python'), 'python3'];
  for (const c of candidates) {
    const r = spawnSync(c, ['--version'], { encoding: 'utf8' });
    if (r.status === 0) return c === 'python3' ? spawnSync('which', ['python3'], { encoding: 'utf8' }).stdout.trim() : c;
  }
  return null;
}

/**
 * @param {import('../main/backend').BackendSupervisor} sup @param {string} wanted @param {number} [ms]
 * @returns {Promise<{status: string, message: string}>}
 */
function waitStatus(sup, wanted, ms = 20_000) {
  return new Promise((resolve, reject) => {
    if (sup.status === wanted) return resolve(sup.info());
    const timer = setTimeout(() => {
      sup.off('status', handler);
      reject(new Error(`Status ${wanted} nicht erreicht (zuletzt ${sup.status}: ${sup.message})`));
    }, ms);
    const handler = (event) => {
      if (event.status !== wanted) return;
      clearTimeout(timer);
      sup.off('status', handler);
      resolve(event);
    };
    sup.on('status', handler);
  });
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

describe('BackendSupervisor (Python-Stub)', () => {
  const python = findPython();
  let tmp = '';
  before(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vincent-stub-'));
    fs.mkdirSync(path.join(tmp, 'xrz'));
    fs.writeFileSync(path.join(tmp, 'xrz', '__init__.py'), '');
    fs.writeFileSync(path.join(tmp, 'xrz', '__main__.py'), STUB_MAIN);
  });
  after(() => fs.rmSync(tmp, { recursive: true, force: true }));

  const makeEnv = (extra = {}) => ({
    ...process.env,
    VINCENT_PYTHON: python,
    VINCENT_CWD: tmp,
    ELECTRON_RUN_AS_NODE: '1',
    NODE_OPTIONS: '--max-old-space-size=64',
    ...extra,
  });

  it('startet, wird bereit, ruft mit Token auf, startet nach Absturz neu und beendet sauber', { skip: !python && 'kein Python gefunden' }, async () => {
    const sup = new backend.BackendSupervisor({ logDir: path.join(tmp, 'logs'), env: makeEnv() });
    const seen = [];
    sup.on('status', (e) => seen.push(e.status));
    await sup.start();
    await waitStatus(sup, 'ready');
    const firstPid = sup.child.pid;
    assert.match(sup.token, /^[0-9a-f]{48}$/);

    const echo = await sup.request('POST', '/api/echo', { a: 1 });
    assert.deepEqual(echo.argv, ['serve', '--port', String(sup.port), '--parent-pid', String(process.pid)]);
    assert.deepEqual(echo.body, { a: 1 });
    assert.deepEqual(echo.electron_keys, []);
    assert.equal(echo.node_options, null);
    assert.equal(echo.unbuffered, '1');
    assert.equal(fs.realpathSync(echo.cwd), fs.realpathSync(tmp));

    await assert.rejects(sup.request('POST', '/api/fail', {}), /Absichtlicher Fehler/);

    await sup.request('POST', '/api/crash').catch(() => null);
    await waitStatus(sup, 'crashed');
    assert.ok(!alive(firstPid));
    await waitStatus(sup, 'ready');
    assert.notEqual(sup.child.pid, firstPid);
    const secondPid = sup.child.pid;

    await sup.stop();
    assert.equal(sup.status, 'stopped');
    assert.ok(!alive(secondPid), 'Backend-Prozess läuft nach stop() noch');
    assert.deepEqual(seen, ['starting', 'ready', 'crashed', 'starting', 'ready', 'stopped']);
    const log = fs.readFileSync(path.join(tmp, 'logs', 'backend.log'), 'utf8');
    assert.match(log, /stub gestartet/);
    assert.match(log, /Backend bereit auf Port/);
  });

  it('beendet einen Prozess, der SIGTERM ignoriert, per SIGKILL', { skip: !python && 'kein Python gefunden' }, async () => {
    const sup = new backend.BackendSupervisor({ env: makeEnv({ STUB_IGNORE_TERM: '1' }), stopGraceMs: 800 });
    await sup.start();
    await waitStatus(sup, 'ready');
    const pid = sup.child.pid;
    const t0 = Date.now();
    await sup.stop();
    assert.ok(Date.now() - t0 >= 700, 'SIGKILL kam zu früh');
    assert.ok(!alive(pid));
  });

  it('meldet "failed", wenn /api/health nie antwortet, und räumt den Prozess ab', { skip: !python && 'kein Python gefunden' }, async () => {
    const sup = new backend.BackendSupervisor({ env: makeEnv({ STUB_NO_HEALTH: '1' }), healthTimeoutMs: 1200, stopGraceMs: 1000 });
    await sup.start();
    const pid = sup.child.pid;
    const failed = await waitStatus(sup, 'failed');
    assert.match(failed.message, /nicht geantwortet/);
    assert.match(failed.message, /stub gestartet/);
    await sup.stop();
    assert.ok(!alive(pid));
  });

  it('meldet "failed" mit fehlendem Interpreter', async () => {
    const sup = new backend.BackendSupervisor({ env: { VINCENT_PYTHON: '/gibt/es/nicht/python' } });
    const failed = waitStatus(sup, 'failed', 2000);
    await sup.start();
    assert.match((await failed).message, /Python-Interpreter nicht gefunden/);
    await sup.stop();
  });

  it('gibt nach mehr als 5 Abstürzen in 5 Minuten auf (mit den letzten Logzeilen)', async () => {
    const sup = new backend.BackendSupervisor({ env: {} });
    sup.spawnBackend = async () => {};
    sup.log.write('Traceback (most recent call last):\nRuntimeError: kaputt\n');
    const statuses = [];
    sup.on('status', (e) => statuses.push(e));
    for (let i = 0; i < 6; i++) sup.onExit(sup.generation, 1, null, '');
    sup.clearTimers();
    assert.deepEqual(
      statuses.map((s) => s.status),
      ['crashed', 'crashed', 'crashed', 'crashed', 'crashed', 'failed'],
    );
    assert.match(statuses[0].message, /Neustart in 1 s/);
    assert.match(statuses[4].message, /Neustart in 16 s/);
    assert.match(statuses[5].message, /RuntimeError: kaputt/);
  });

  it('rotiert backend.log oberhalb von 5 MB', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vincent-log-'));
    const file = path.join(dir, 'backend.log');
    fs.writeFileSync(file, Buffer.alloc(5 * 1024 * 1024 + 10, 'a'));
    const log = new backend.BackendLog(file);
    log.write('neu\n');
    await log.close();
    assert.equal(fs.readFileSync(file, 'utf8'), 'neu\n');
    assert.ok(fs.existsSync(`${file}.1`));
    assert.equal(fs.statSync(`${file}.1`).size, 5 * 1024 * 1024 + 10);
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

// ---------------------------------------------------------------- Renderer-Client api.js

describe('renderer/shared/api.js', () => {
  /** @type {any} */
  let api;
  const saved = { window: globalThis.window, fetch: globalThis.fetch, WebSocket: globalThis.WebSocket };
  const calls = [];
  let backendInfo = { port: 4100, token: 'tok', status: 'ready', message: '' };
  /** @type {((info: any) => void)|null} */
  let statusCb = null;
  /** @type {Set<number>} */
  const deadPorts = new Set();

  class FakeWebSocket {
    static OPEN = 1;
    static instances = [];
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.sent = [];
      FakeWebSocket.instances.push(this);
      setTimeout(() => {
        if (this.readyState !== 0) return;
        this.readyState = 1;
        this.onopen && this.onopen({});
      }, 5);
    }
    send(data) {
      this.sent.push(data);
    }
    close() {
      this.readyState = 3;
    }
    /** vom Test ausgelöst: Server schickt ein Event */
    emit(obj) {
      this.onmessage && this.onmessage({ data: JSON.stringify(obj) });
    }
    /** vom Test ausgelöst: Verbindung bricht ab */
    drop() {
      this.readyState = 3;
      this.onclose && this.onclose({});
    }
  }

  before(async () => {
    globalThis.window = {
      vincent: {
        getBackend: async () => ({ ...backendInfo }),
        onBackendStatus: (cb) => {
          statusCb = cb;
          return () => {
            statusCb = null;
          };
        },
      },
    };
    globalThis.WebSocket = FakeWebSocket;
    globalThis.fetch = async (url, init = {}) => {
      const u = new URL(url);
      calls.push({ url, init });
      if (deadPorts.has(Number(u.port))) throw new TypeError('fetch failed');
      const respond = (status, obj) => new Response(obj === undefined ? '' : JSON.stringify(obj), { status });
      if (u.pathname === '/api/state') return respond(200, { mode: 'paper' });
      if (u.pathname === '/api/chat') return respond(200, { reply: 'Hallo', got: JSON.parse(init.body) });
      if (u.pathname === '/api/mode') return respond(400, { detail: 'Live-Modus ist gesperrt.' });
      if (u.pathname === '/api/memory/1') return respond(422, { detail: [{ msg: 'Feld fehlt' }, { msg: 'Zahl erwartet' }] });
      if (u.pathname === '/api/empty') return respond(200);
      return respond(404, { detail: 'Nicht gefunden' });
    };
    api = await import(pathToFileURL(path.join(__dirname, '..', 'renderer', 'shared', 'api.js')).href);
  });

  after(() => {
    globalThis.window = saved.window;
    globalThis.fetch = saved.fetch;
    globalThis.WebSocket = saved.WebSocket;
  });

  it('formatiert Zahlen und Preise deutsch', () => {
    assert.equal(api.fmtNumber(1234.5), '1.234,50');
    assert.equal(api.fmtNumber(0.123456, 4), '0,1235');
    assert.equal(api.fmtNumber(null), '–');
    assert.equal(api.fmtNumber(Number.NaN), '–');
    assert.equal(api.fmtPrice(64250.123), '64.250,12');
    assert.equal(api.fmtPrice(1.23456), '1,2346');
    assert.equal(api.fmtPrice(0.000123456), '0,0001235');
    assert.equal(api.fmtPrice(0), '0,00');
    assert.equal(api.fmtPrice(undefined), '–');
  });

  it('maskiert HTML', () => {
    assert.equal(api.escapeHtml(`<b onclick="x('y')">&</b>`), '&lt;b onclick=&quot;x(&#39;y&#39;)&quot;&gt;&amp;&lt;/b&gt;');
    assert.equal(api.escapeHtml(null), '');
    assert.equal(api.escapeHtml(42), '42');
  });

  it('gibt relative Zeiten auf Deutsch aus', () => {
    const now = Date.UTC(2026, 9, 13, 12, 0, 0);
    const sec = now / 1000;
    assert.equal(api.timeAgo(sec - 10, now), 'gerade eben');
    assert.equal(api.timeAgo(sec - 5 * 60, now), 'vor 5 Min.');
    assert.equal(api.timeAgo(now - 2 * 3600_000, now), 'vor 2 Std.');
    assert.equal(api.timeAgo(new Date(now - 26 * 3600_000), now), 'gestern');
    assert.equal(api.timeAgo(new Date(now - 3 * 86400_000).toISOString(), now), 'vor 3 Tagen');
    assert.equal(api.timeAgo(sec - 400 * 86400, now), 'vor einem Jahr');
    assert.equal(api.timeAgo('', now), '');
  });

  it('schickt REST-Aufrufe mit Token und JSON', async () => {
    const res = await api.post('/api/chat', { text: 'Hallo Vincent' });
    assert.equal(res.reply, 'Hallo');
    assert.deepEqual(res.got, { text: 'Hallo Vincent' });
    const last = calls.at(-1);
    assert.equal(last.url, 'http://127.0.0.1:4100/api/chat');
    assert.equal(last.init.method, 'POST');
    assert.equal(last.init.headers['X-Vincent-Token'], 'tok');
    assert.equal(last.init.headers['Content-Type'], 'application/json');
    assert.deepEqual(await api.get('/api/state'), { mode: 'paper' });
    assert.equal(await api.post('/api/empty'), null);
  });

  it('wirft deutsche Fehlermeldungen aus "detail"', async () => {
    await assert.rejects(api.post('/api/mode', { mode: 'live' }), { message: 'Live-Modus ist gesperrt.' });
    await assert.rejects(api.patch('/api/memory/1', {}), { message: 'Feld fehlt; Zahl erwartet' });
    await assert.rejects(api.del('/api/unbekannt'), { message: 'Nicht gefunden' });
  });

  it('holt nach einem Netzwerkfehler Port/Token neu (Backend-Neustart)', async () => {
    deadPorts.add(4100);
    backendInfo = { port: 4200, token: 'tok2', status: 'ready', message: '' };
    assert.deepEqual(await api.get('/api/state'), { mode: 'paper' });
    assert.equal(calls.at(-1).url, 'http://127.0.0.1:4200/api/state');
    assert.equal(calls.at(-1).init.headers['X-Vincent-Token'], 'tok2');
  });

  it('verbindet den WebSocket, verteilt Events und verbindet nach Abbruch neu', async () => {
    const got = [];
    const all = [];
    const conn = [];
    const offState = api.on('state', (data) => got.push(data));
    api.on('*', (_d, ev) => all.push(ev.type));
    api.on('connection', (d) => conn.push(d.connected));

    const p1 = api.connect();
    assert.equal(api.connect(), p1, 'connect() muss idempotent sein');
    await p1;
    const ws1 = FakeWebSocket.instances.at(-1);
    assert.equal(ws1.url, 'ws://127.0.0.1:4200/ws?token=tok2');
    assert.equal(api.isConnected(), true);

    ws1.emit({ type: 'state', data: { mode: 'watch' }, ts: 1 });
    ws1.emit({ type: 'subtitle', data: { text: 'Hallo' }, ts: 2 });
    assert.deepEqual(got, [{ mode: 'watch' }]);
    assert.deepEqual(api.getState(), { mode: 'watch' });
    offState();
    ws1.emit({ type: 'state', data: { mode: 'paper' }, ts: 3 });
    assert.equal(got.length, 1, 'abgemeldeter Listener wurde trotzdem aufgerufen');

    // Backend startet neu → neuer Port; Abbruch → Neuaufbau mit frisch geholten Daten
    backendInfo = { port: 4300, token: 'tok3', status: 'ready', message: '' };
    ws1.drop();
    assert.equal(api.isConnected(), false);
    await new Promise((r) => setTimeout(r, 700));
    const ws2 = FakeWebSocket.instances.at(-1);
    assert.notEqual(ws2, ws1);
    assert.equal(ws2.url, 'ws://127.0.0.1:4300/ws?token=tok3');
    assert.equal(api.isConnected(), true);

    // Statusmeldung "ready" mit neuem Port → sofort neu verbinden
    backendInfo = { port: 4400, token: 'tok4', status: 'ready', message: '' };
    statusCb({ ...backendInfo });
    await new Promise((r) => setTimeout(r, 50));
    const ws3 = FakeWebSocket.instances.at(-1);
    assert.equal(ws3.url, 'ws://127.0.0.1:4400/ws?token=tok4');
    assert.equal(ws2.readyState, 3, 'alte Verbindung wurde nicht geschlossen');

    assert.deepEqual(conn, [true, false, true, false, true]);
    assert.ok(all.includes('subtitle') && all.includes('connection') && all.includes('backend'));

    // Aufräumen: Backend gestoppt → keine neuen Verbindungsversuche mehr bis "ready"
    statusCb({ port: null, token: 'tok4', status: 'stopped', message: '' });
    assert.equal(api.isConnected(), false);
  });
});
