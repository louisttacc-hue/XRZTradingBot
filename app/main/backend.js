'use strict';
/**
 * Python-Sidecar-Supervisor: startet `python -m xrz serve`, wartet auf /api/health,
 * startet nach Abstürzen mit Backoff neu und beendet den Prozess beim App-Ende sauber.
 *
 * Bewusst ohne Electron-Abhängigkeit (Pfade/Flags werden injiziert), damit die reine
 * Logik mit `node --test` geprüft werden kann.
 */
const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');

const HEALTH_INTERVAL_MS = 300;
const HEALTH_TIMEOUT_MS = 120_000;
const STOP_GRACE_MS = 5_000;
const LOG_MAX_BYTES = 5 * 1024 * 1024;
const LOG_TAIL_LINES = 200;
const FAILED_TAIL_LINES = 30;
const MAX_RESTARTS = 5;
const RESTART_WINDOW_MS = 5 * 60_000;
const MAX_BACKOFF_MS = 30_000;
const REQUEST_TIMEOUT_MS = 15_000;

/**
 * Ermittelt Python-Interpreter und Arbeitsverzeichnis des Backends.
 * Reihenfolge: env VINCENT_PYTHON (+ VINCENT_CWD) → <resourcesPath>/backend.json {python, cwd}
 * (gepackte App) → Entwicklungs-Fallback <repo>/.venv/bin/python mit cwd <repo>.
 *
 * @param {object} [opts]
 * @param {NodeJS.ProcessEnv} [opts.env]
 * @param {string} [opts.resourcesPath] process.resourcesPath (nur in Electron gesetzt)
 * @param {string} [opts.repoRoot]
 * @param {(p: string) => boolean} [opts.exists]
 * @param {(p: string) => string} [opts.readFile]
 * @returns {{python: string, cwd: string, source: 'env'|'packaged'|'dev'}}
 * @throws {Error} mit deutscher Meldung, wenn kein Interpreter gefunden wird
 */
function resolveBackendCommand(opts = {}) {
  const env = opts.env || process.env;
  const resourcesPath = opts.resourcesPath === undefined ? process.resourcesPath : opts.resourcesPath;
  const repoRoot = opts.repoRoot || REPO_ROOT;
  const exists = opts.exists || fs.existsSync;
  const readFile = opts.readFile || ((p) => fs.readFileSync(p, 'utf8'));

  const checked = (python, cwd, source) => {
    if (!exists(python)) {
      throw new Error(`Python-Interpreter nicht gefunden: ${python}`);
    }
    if (!exists(cwd)) {
      throw new Error(`Arbeitsverzeichnis des Backends nicht gefunden: ${cwd}`);
    }
    return { python, cwd, source };
  };

  if (env.VINCENT_PYTHON) {
    const python = path.resolve(env.VINCENT_PYTHON);
    const cwd = path.resolve(env.VINCENT_CWD || repoRoot);
    return checked(python, cwd, 'env');
  }

  if (resourcesPath) {
    const cfgPath = path.join(resourcesPath, 'backend.json');
    if (exists(cfgPath)) {
      let cfg;
      try {
        cfg = JSON.parse(readFile(cfgPath));
      } catch (err) {
        throw new Error(`backend.json ist ungültig (${cfgPath}): ${err.message}`);
      }
      if (!cfg || typeof cfg.python !== 'string' || !cfg.python) {
        throw new Error(`backend.json enthält keinen Eintrag "python" (${cfgPath}).`);
      }
      const python = path.resolve(cfg.python);
      // Ohne cwd: Projektordner = zwei Ebenen über <venv>/bin/python
      const cwd = path.resolve(typeof cfg.cwd === 'string' && cfg.cwd ? cfg.cwd : path.join(path.dirname(python), '..', '..'));
      return checked(python, cwd, 'packaged');
    }
  }

  const python = path.join(repoRoot, '.venv', 'bin', 'python');
  if (!exists(python)) {
    throw new Error(
      'Kein Python-Backend gefunden. Erwartet wird VINCENT_PYTHON, ' +
        (resourcesPath ? `${path.join(resourcesPath, 'backend.json')} oder ` : '') +
        `${python} (bitte install_mac.sh ausführen).`,
    );
  }
  return checked(python, repoRoot, 'dev');
}

/**
 * Kopie der Umgebung für das Backend: ohne ELECTRON_*-Variablen und NODE_OPTIONS,
 * dafür mit Token und ungepufferter Python-Ausgabe.
 *
 * @param {NodeJS.ProcessEnv} env
 * @param {Record<string, string>} [extra] zusätzliche Variablen (überschreiben)
 * @param {NodeJS.Platform} [platform]
 * @returns {Record<string, string>}
 */
function cleanEnv(env, extra = {}, platform = process.platform) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const [key, value] of Object.entries(env || {})) {
    if (value === undefined) continue;
    const upper = key.toUpperCase();
    if (upper.startsWith('ELECTRON_') || upper === 'NODE_OPTIONS') continue;
    out[key] = String(value);
  }
  if (platform === 'darwin') {
    // Aus dem Finder gestartete Apps erben nur /usr/bin:/bin:/usr/sbin:/sbin – Homebrew-Tools ergänzen.
    const parts = (out.PATH || '/usr/bin:/bin:/usr/sbin:/sbin').split(':').filter(Boolean);
    for (const dir of ['/usr/local/bin', '/opt/homebrew/bin']) {
      if (!parts.includes(dir)) parts.unshift(dir);
    }
    out.PATH = parts.join(':');
  }
  out.PYTHONUNBUFFERED = '1';
  return Object.assign(out, extra);
}

/**
 * Baut die Kommandozeilen-Argumente für `python -m xrz serve`.
 * @param {number} port
 * @param {number} parentPid
 * @param {boolean} demo
 * @returns {string[]}
 */
function buildArgs(port, parentPid, demo) {
  const args = ['-m', 'xrz', 'serve', '--port', String(port), '--parent-pid', String(parentPid)];
  if (demo) args.push('--demo');
  return args;
}

/**
 * Backoff vor dem n-ten Neustart innerhalb des Zeitfensters (1 s, 2 s, 4 s … max. 30 s).
 * @param {number} attempt 1-basiert
 * @returns {number} Millisekunden
 */
function backoffMs(attempt) {
  return Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.max(0, attempt - 1));
}

/**
 * Freien TCP-Port auf 127.0.0.1 finden.
 * @returns {Promise<number>}
 */
function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = /** @type {net.AddressInfo} */ (srv.address());
      srv.close(() => resolve(port));
    });
  });
}

/**
 * Ein HTTP-Request an das lokale Backend.
 * @param {{port: number, token?: string, method: string, path: string, body?: unknown, timeoutMs?: number}} o
 * @returns {Promise<{status: number, data: any}>}
 */
function httpJson(o) {
  return new Promise((resolve, reject) => {
    const payload = o.body === undefined ? null : Buffer.from(JSON.stringify(o.body));
    const headers = { Accept: 'application/json' };
    if (o.token) headers['X-Vincent-Token'] = o.token;
    if (payload) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = String(payload.length);
    }
    const req = http.request(
      { host: '127.0.0.1', port: o.port, method: o.method, path: o.path, headers, timeout: o.timeoutMs || REQUEST_TIMEOUT_MS },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let data = text;
          try {
            data = text ? JSON.parse(text) : null;
          } catch {
            /* kein JSON – Rohtext zurückgeben */
          }
          resolve({ status: res.statusCode || 0, data });
        });
        res.on('error', reject);
      },
    );
    req.on('timeout', () => req.destroy(new Error('Zeitüberschreitung bei der Anfrage an das Backend.')));
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

/**
 * Schreibt die Backend-Ausgabe in eine Logdatei (Rotation bei > 5 MB) und hält die letzten Zeilen im Speicher.
 */
class BackendLog {
  /**
   * @param {string|null} file
   * @param {(line: string) => void} [echo]
   */
  constructor(file, echo) {
    this.file = file;
    this.echo = echo;
    /** @type {string[]} */
    this.lines = [];
    this.partial = '';
    this.size = 0;
    /** @type {fs.WriteStream|null} */
    this.stream = null;
    if (file) {
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        this.size = fs.existsSync(file) ? fs.statSync(file).size : 0;
        if (this.size > LOG_MAX_BYTES) this.rotate();
      } catch (err) {
        console.error('[backend] Logdatei nicht nutzbar:', err.message);
        this.file = null;
      }
    }
  }

  rotate() {
    if (!this.file) return;
    if (this.stream) {
      this.stream.end();
      this.stream = null;
    }
    try {
      fs.renameSync(this.file, `${this.file}.1`);
    } catch (err) {
      if (err.code !== 'ENOENT') console.error('[backend] Log-Rotation fehlgeschlagen:', err.message);
    }
    this.size = 0;
  }

  /** @param {string} text */
  write(text) {
    const lines = (this.partial + text).split(/\r?\n/);
    this.partial = lines.pop() || '';
    for (const line of lines) {
      this.lines.push(line);
      if (this.echo) this.echo(line);
    }
    if (this.lines.length > LOG_TAIL_LINES) this.lines.splice(0, this.lines.length - LOG_TAIL_LINES);
    if (!this.file) return;
    if (this.size > LOG_MAX_BYTES) this.rotate();
    if (!this.stream) {
      this.stream = fs.createWriteStream(this.file, { flags: 'a' });
      this.stream.on('error', (err) => {
        console.error('[backend] Schreiben ins Log fehlgeschlagen:', err.message);
        this.stream = null;
        this.file = null;
      });
    }
    const buf = Buffer.from(text, 'utf8');
    this.stream.write(buf);
    this.size += buf.length;
  }

  /** @param {number} n */
  tail(n) {
    const all = this.partial ? [...this.lines, this.partial] : this.lines;
    return all.slice(-n);
  }

  /** @returns {Promise<void>} erfüllt, sobald alles geschrieben ist */
  close() {
    const stream = this.stream;
    this.stream = null;
    if (!stream) return Promise.resolve();
    return new Promise((resolve) => {
      stream.once('close', resolve);
      stream.once('error', resolve);
      stream.end();
    });
  }
}

/**
 * @typedef {'idle'|'starting'|'ready'|'crashed'|'failed'|'stopped'} BackendStatus
 * @typedef {{status: BackendStatus, message: string, port: number|null}} BackendStatusEvent
 */

/**
 * Überwacht den Python-Backend-Prozess. Events: `status` ({status, message, port}).
 */
class BackendSupervisor extends EventEmitter {
  /**
   * @param {object} [opts]
   * @param {string} [opts.logDir] Ordner für backend.log (z. B. userData/logs)
   * @param {string} [opts.resourcesPath]
   * @param {boolean} [opts.echo] Backend-Ausgabe zusätzlich auf die Konsole schreiben
   * @param {NodeJS.ProcessEnv} [opts.env] Umgebung (Standard process.env; VINCENT_DEMO=1 → `--demo`)
   * @param {number} [opts.healthTimeoutMs] max. Wartezeit auf /api/health (Standard 120 s)
   * @param {number} [opts.stopGraceMs] Wartezeit zwischen SIGTERM und SIGKILL (Standard 5 s)
   */
  constructor(opts = {}) {
    super();
    this.opts = opts;
    this.env = opts.env || process.env;
    this.healthTimeoutMs = opts.healthTimeoutMs || HEALTH_TIMEOUT_MS;
    this.stopGraceMs = opts.stopGraceMs || STOP_GRACE_MS;
    this.token = crypto.randomBytes(24).toString('hex');
    /** @type {number|null} */
    this.port = null;
    /** @type {BackendStatus} */
    this.status = 'idle';
    this.message = 'Backend noch nicht gestartet.';
    /** @type {import('node:child_process').ChildProcess|null} */
    this.child = null;
    this.stopping = false;
    /** @type {number[]} */
    this.crashTimes = [];
    /** @type {NodeJS.Timeout|null} */
    this.restartTimer = null;
    /** @type {NodeJS.Timeout|null} */
    this.healthTimer = null;
    this.generation = 0;
    this.log = new BackendLog(
      opts.logDir ? path.join(opts.logDir, 'backend.log') : null,
      opts.echo ? (line) => console.log(`[backend] ${line}`) : undefined,
    );
  }

  /** @returns {{port: number|null, token: string, status: BackendStatus, message: string}} */
  info() {
    return { port: this.port, token: this.token, status: this.status, message: this.message };
  }

  /**
   * @param {BackendStatus} status
   * @param {string} message
   */
  setStatus(status, message) {
    this.status = status;
    this.message = message;
    this.emit('status', { status, message, port: this.port });
  }

  /** Startet das Backend (nach `failed`/`stopped` mit frischem Neustart-Zähler). */
  async start() {
    if (this.child || this.status === 'starting') return;
    this.stopping = false;
    this.crashTimes = [];
    await this.spawnBackend();
  }

  async spawnBackend() {
    this.clearTimers();
    const generation = ++this.generation;
    let cmd;
    try {
      cmd = resolveBackendCommand({ env: this.env, resourcesPath: this.opts.resourcesPath });
    } catch (err) {
      this.log.write(`[vincent] ${err.message}\n`);
      this.setStatus('failed', err.message);
      return;
    }
    try {
      this.port = await pickFreePort();
    } catch (err) {
      this.setStatus('failed', `Kein freier Port für das Backend: ${err.message}`);
      return;
    }
    if (this.stopping || generation !== this.generation) return;

    const demo = this.env.VINCENT_DEMO === '1';
    const args = buildArgs(this.port, process.pid, demo);
    const env = cleanEnv(this.env, { VINCENT_TOKEN: this.token });
    this.log.write(
      `\n[vincent] ${new Date().toISOString()} Starte Backend (${cmd.source}): ${cmd.python} ${args.join(' ')} (cwd ${cmd.cwd})\n`,
    );
    this.setStatus('starting', 'Vincent wird gestartet …');

    let child;
    try {
      child = spawn(cmd.python, args, { cwd: cmd.cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (err) {
      this.setStatus('failed', `Backend konnte nicht gestartet werden: ${err.message}`);
      return;
    }
    this.child = child;
    let exited = false;
    /** @type {(code: number|null, signal: NodeJS.Signals|null, errorText: string) => void} */
    const finish = (code, signal, errorText) => {
      if (exited) return;
      exited = true;
      if (this.child === child) this.child = null;
      this.onExit(generation, code, signal, errorText);
    };
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (d) => this.log.write(d));
    child.stderr.on('data', (d) => this.log.write(d));
    child.on('error', (err) => {
      this.log.write(`[vincent] Prozessfehler: ${err.message}\n`);
      // spawn selbst ist fehlgeschlagen (z. B. EACCES) – dann folgt kein 'exit'
      if (child.pid === undefined) finish(null, null, err.message);
    });
    child.once('exit', (code, signal) => finish(code, signal, ''));
    this.pollHealth(generation, Date.now());
  }

  /**
   * @param {number} generation
   * @param {number} startedAt
   */
  pollHealth(generation, startedAt) {
    const tick = async () => {
      if (generation !== this.generation || !this.child || this.stopping) return;
      let ok = false;
      try {
        const res = await httpJson({ port: /** @type {number} */ (this.port), method: 'GET', path: '/api/health', timeoutMs: 2000 });
        ok = res.status === 200;
      } catch {
        ok = false;
      }
      if (generation !== this.generation || !this.child || this.stopping) return;
      if (ok) {
        this.log.write(`[vincent] Backend bereit auf Port ${this.port}.\n`);
        this.setStatus('ready', 'Vincent ist bereit.');
        return;
      }
      if (Date.now() - startedAt > this.healthTimeoutMs) {
        const tail = this.log.tail(FAILED_TAIL_LINES).join('\n');
        this.generation++;
        this.setStatus('failed', `Das Backend hat nach ${Math.round(this.healthTimeoutMs / 1000)} s nicht geantwortet.\n${tail}`);
        void this.terminateChild();
        return;
      }
      this.healthTimer = setTimeout(tick, HEALTH_INTERVAL_MS);
    };
    this.healthTimer = setTimeout(tick, HEALTH_INTERVAL_MS);
  }

  /**
   * @param {number} generation
   * @param {number|null} code
   * @param {NodeJS.Signals|null} signal
   * @param {string} errorText
   */
  onExit(generation, code, signal, errorText) {
    const how = errorText || (signal ? `Signal ${signal}` : `Code ${code}`);
    this.log.write(`[vincent] Backend beendet (${how}).\n`);
    if (this.stopping || generation !== this.generation) return;
    this.clearTimers();

    const now = Date.now();
    this.crashTimes = this.crashTimes.filter((t) => now - t < RESTART_WINDOW_MS);
    this.crashTimes.push(now);
    if (this.crashTimes.length > MAX_RESTARTS) {
      const tail = this.log.tail(FAILED_TAIL_LINES).join('\n');
      this.setStatus(
        'failed',
        `Das Backend ist zu oft abgestürzt (${MAX_RESTARTS} Neustarts in 5 Minuten) und wird nicht mehr neu gestartet.\n${tail}`,
      );
      return;
    }
    const delay = backoffMs(this.crashTimes.length);
    this.setStatus('crashed', `Das Backend ist abgestürzt (${how}). Neustart in ${Math.round(delay / 1000)} s …`);
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (!this.stopping) void this.spawnBackend();
    }, delay);
  }

  clearTimers() {
    if (this.restartTimer) clearTimeout(this.restartTimer);
    if (this.healthTimer) clearTimeout(this.healthTimer);
    this.restartTimer = null;
    this.healthTimer = null;
  }

  /**
   * SIGTERM, bis zu 5 s (stopGraceMs) warten, dann SIGKILL.
   * @returns {Promise<void>}
   */
  terminateChild() {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) {
      this.child = null;
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const killTimer = setTimeout(() => {
        this.log.write('[vincent] Backend reagiert nicht auf SIGTERM – SIGKILL.\n');
        try {
          child.kill('SIGKILL');
        } catch {
          /* Prozess bereits weg */
        }
      }, this.stopGraceMs);
      child.once('exit', () => {
        clearTimeout(killTimer);
        if (this.child === child) this.child = null;
        resolve();
      });
      try {
        child.kill('SIGTERM');
      } catch {
        clearTimeout(killTimer);
        resolve();
      }
    });
  }

  /**
   * Backend beenden (kein automatischer Neustart mehr).
   * @returns {Promise<void>}
   */
  async stop() {
    this.stopping = true;
    this.generation++;
    this.clearTimers();
    await this.terminateChild();
    if (this.status !== 'stopped') this.setStatus('stopped', 'Vincent wurde beendet.');
    await this.log.close();
  }

  /** Letzter Ausweg bei hartem Prozessende (synchron): Kindprozess sofort töten. */
  killNow() {
    if (this.child && this.child.exitCode === null) {
      try {
        this.child.kill('SIGKILL');
      } catch {
        /* bereits beendet */
      }
    }
  }

  /**
   * Ruft das Backend mit Token auf.
   * @param {string} method
   * @param {string} urlPath z. B. '/api/voice/listen'
   * @param {unknown} [body]
   * @returns {Promise<any>} JSON-Antwort
   * @throws {Error} mit deutscher Meldung (`detail` des Backends)
   */
  async request(method, urlPath, body) {
    if (this.status !== 'ready' || !this.port) {
      throw new Error('Vincent ist noch nicht bereit.');
    }
    const res = await httpJson({ port: this.port, token: this.token, method, path: urlPath, body });
    if (res.status < 200 || res.status >= 300) {
      const detail = res.data && typeof res.data === 'object' && res.data.detail ? res.data.detail : `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return res.data;
  }
}

module.exports = {
  BackendSupervisor,
  BackendLog,
  resolveBackendCommand,
  cleanEnv,
  buildArgs,
  backoffMs,
  pickFreePort,
  httpJson,
  REPO_ROOT,
};
