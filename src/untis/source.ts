import type { Logger } from 'pino';
import { authenticator } from 'otplib';
import { WebUntis, WebUntisAnonymousAuth, WebUntisSecretAuth, type Lesson } from 'webuntis';
import type { Config } from '../config.js';
import { untisDateToLocalDate } from '../domain/time.js';
import type { Holiday, TimeUnit, UntisDate } from '../domain/types.js';

/**
 * Zugriff auf WebUntis über die webuntis-Bibliothek (JSON-RPC-API, die auch die
 * Untis-Apps nutzen). Pro Abruf: anmelden → Daten holen → IMMER abmelden.
 * Es wird nur gelesen, was der konfigurierte Account in WebUntis selbst sehen darf.
 */

export type UntisErrorKind = 'auth' | 'permission' | 'config' | 'network' | 'unknown';

export class UntisError extends Error {
  override name = 'UntisError';
  constructor(
    readonly kind: UntisErrorKind,
    message: string,
    readonly code?: number,
  ) {
    super(message);
  }
}

/** Untis-JSON-RPC-Fehler mit Code (die Bibliothek verschluckt diese sonst). */
class UntisRpcError extends Error {
  constructor(
    readonly code: number,
    readonly rpcMessage: string,
  ) {
    super(`Untis-Fehler ${code}: ${rpcMessage}`);
  }
}

export interface FetchWindow {
  from: UntisDate;
  to: UntisDate;
}

export interface UntisFetchResult {
  lessons: Lesson[];
  classes: { id: number; name: string }[];
  timegrid?: TimeUnit[];
  holidays?: Holiday[];
}

export interface UntisSource {
  fetch(window: FetchWindow, opts: { masterData: boolean }): Promise<UntisFetchResult>;
}

type Client = WebUntis;

interface RawSchoolyear {
  id: number;
  name: string;
  startDate: number | string;
  endDate: number | string;
}

export interface WebUntisSourceOptions {
  /** Nur für Tests: alternative Basis-URL (z. B. lokaler Fake-Server). */
  baseUrlOverride?: string;
  timeoutMs?: number;
}

const IDENTITY = 'ef-untis-whatsapp-bot';

export class WebUntisSource implements UntisSource {
  private classCache = new Map<string, { id: number; name: string }[]>();

  constructor(
    private readonly cfg: Config['untis'],
    private readonly logger: Logger,
    private readonly opts: WebUntisSourceOptions = {},
  ) {}

  private createClient(): Client {
    const { school, server, username, password, secret } = this.cfg;
    let client: Client;
    switch (this.cfg.auth) {
      case 'secret':
        client = new WebUntisSecretAuth(school, username, secret, server, IDENTITY, authenticator as never);
        break;
      case 'anonymous':
        client = new WebUntisAnonymousAuth(school, server, IDENTITY);
        break;
      default:
        client = new WebUntis(school, username, password, server, IDENTITY);
    }
    client.axios.defaults.timeout = this.opts.timeoutMs ?? 30_000;
    if (this.opts.baseUrlOverride) client.axios.defaults.baseURL = this.opts.baseUrlOverride;
    client.axios.interceptors.response.use((res) => {
      const err = (res.data as { error?: { code?: number; message?: string } } | undefined)?.error;
      if (String(res.config.url ?? '').includes('jsonrpc') && err && typeof err === 'object') {
        throw new UntisRpcError(Number(err.code ?? 0), String(err.message ?? ''));
      }
      return res;
    });
    return client;
  }

  async fetch(window: FetchWindow, opts: { masterData: boolean }): Promise<UntisFetchResult> {
    const client = this.createClient();
    try {
      await client.login();
    } catch (e) {
      throw classify(e, 'login');
    }
    try {
      const years = await this.schoolyears(client);
      const ranges = years
        .map((y) => ({ year: y, from: Math.max(window.from, y.startDate), to: Math.min(window.to, y.endDate) }))
        .filter((r) => r.from <= r.to);

      const lessons: Lesson[] = [];
      let classes: { id: number; name: string }[] = [];
      for (const range of ranges) {
        classes = await this.resolveClasses(client, range.year.id);
        for (const cls of classes) {
          try {
            const part = await client.getTimetableForRange(
              untisDateToLocalDate(range.from),
              untisDateToLocalDate(range.to),
              cls.id,
              WebUntis.TYPES.CLASS,
              false,
            );
            lessons.push(...(Array.isArray(part) ? part : []));
          } catch (e) {
            if (e instanceof UntisRpcError && e.code === -7004) continue; // Datum außerhalb des Schuljahres
            throw e;
          }
        }
      }
      if (ranges.length === 0) {
        this.logger.info({ window }, 'Kein Schuljahr im Abrufzeitraum (Ferien) – keine Stunden');
      }

      const result: UntisFetchResult = { lessons, classes };
      if (opts.masterData) {
        result.timegrid = await this.timegrid(client).catch((e) => {
          this.logger.warn({ err: describe(e) }, 'Zeitraster konnte nicht geladen werden');
          return undefined;
        });
        result.holidays = await this.holidays(client).catch((e) => {
          this.logger.warn({ err: describe(e) }, 'Ferien konnten nicht geladen werden');
          return undefined;
        });
        if (!result.timegrid) delete result.timegrid;
        if (!result.holidays) delete result.holidays;
      }
      return result;
    } catch (e) {
      throw classify(e, 'fetch');
    } finally {
      await logout(client).catch((e: unknown) => this.logger.debug({ err: describe(e) }, 'Untis-Logout fehlgeschlagen'));
    }
  }

  private async schoolyears(client: Client): Promise<{ id: number; startDate: UntisDate; endDate: UntisDate }[]> {
    const raw = await client._request<RawSchoolyear[]>('getSchoolyears', {}, false);
    return (Array.isArray(raw) ? raw : []).map((y) => ({ id: y.id, startDate: Number(y.startDate), endDate: Number(y.endDate) }));
  }

  private async resolveClasses(client: Client, schoolyearId: number): Promise<{ id: number; name: string }[]> {
    const wanted = this.cfg.classes;
    const cacheKey = `${schoolyearId}|${wanted.join(',')}`;
    const cached = this.classCache.get(cacheKey);
    if (cached) return cached;

    const own = client.sessionInformation?.klasseId;
    let resolved: { id: number; name: string }[];

    if (wanted.length === 0) {
      if (typeof own !== 'number') {
        throw new UntisError('config', 'UNTIS_CLASSES ist leer und der Account hat keine eigene Klasse. Bitte z. B. UNTIS_CLASSES=EF setzen.');
      }
      const all = await client.getClasses(false, schoolyearId).catch(() => []);
      resolved = [{ id: own, name: all.find((k) => k.id === own)?.name ?? `Klasse ${own}` }];
    } else {
      let all: { id: number; name: string; longName?: string }[];
      try {
        all = await client.getClasses(false, schoolyearId);
      } catch (e) {
        if (typeof own === 'number' && e instanceof UntisRpcError) {
          this.logger.warn('Klassenliste nicht abrufbar – nutze die eigene Klasse des Accounts');
          resolved = [{ id: own, name: wanted.join('/') }];
          this.classCache.set(cacheKey, resolved);
          return resolved;
        }
        throw e;
      }
      const norm = (s: string) => s.toLowerCase().replace(/[\s_-]/g, '');
      resolved = [];
      const missing: string[] = [];
      for (const name of wanted) {
        const hit = all.find((k) => norm(k.name) === norm(name)) ?? all.find((k) => norm(k.longName ?? '') === norm(name));
        if (hit) resolved.push({ id: hit.id, name: hit.name });
        else missing.push(name);
      }
      if (missing.length) {
        const available = all.map((k) => k.name).sort((a, b) => a.localeCompare(b, 'de')).join(', ');
        throw new UntisError('config', `Klasse(n) ${missing.join(', ')} in Untis nicht gefunden. Vorhanden: ${available}`);
      }
    }
    this.classCache.set(cacheKey, resolved);
    return resolved;
  }

  private async timegrid(client: Client): Promise<TimeUnit[]> {
    const raw = (await client._request('getTimegridUnits', {}, false)) as unknown as {
      timeUnits?: { name?: string; startTime: number; endTime: number }[];
    }[];
    const units = new Map<number, TimeUnit>();
    for (const day of Array.isArray(raw) ? raw : []) {
      for (const u of day.timeUnits ?? []) {
        if (!units.has(u.startTime)) {
          units.set(u.startTime, { name: String(u.name ?? '').replace(/\.$/, '').trim() || String(units.size + 1), start: u.startTime, end: u.endTime });
        }
      }
    }
    return [...units.values()].sort((a, b) => a.start - b.start);
  }

  private async holidays(client: Client): Promise<Holiday[]> {
    const raw = (await client._request('getHolidays', {}, false)) as unknown as {
      name: string;
      longName: string;
      startDate: number | string;
      endDate: number | string;
    }[];
    return (Array.isArray(raw) ? raw : []).map((h) => ({
      name: h.name,
      longName: h.longName,
      startDate: Number(h.startDate),
      endDate: Number(h.endDate),
    }));
  }
}

/**
 * Abmelden MIT Session-Cookie. (WebUntis#logout der Bibliothek sendet kein Cookie und
 * beendet die Sitzung deshalb serverseitig nicht – Untis bittet aber ausdrücklich darum.)
 */
async function logout(client: Client): Promise<void> {
  if (!client.sessionInformation?.sessionId) return;
  await client.axios.post(
    '/WebUntis/jsonrpc.do',
    { id: IDENTITY, method: 'logout', params: {}, jsonrpc: '2.0' },
    { params: { school: client.school }, headers: { Cookie: client._buildCookies() } },
  );
  client.sessionInformation = null;
}

const RPC_MESSAGES: Record<number, [UntisErrorKind, string]> = {
  [-8504]: ['auth', 'Untis-Login fehlgeschlagen: Benutzername oder Passwort falsch.'],
  [-8500]: ['config', 'Untis kennt die Schule nicht – UNTIS_SCHOOL prüfen.'],
  [-8520]: ['auth', 'Untis-Sitzung ungültig (nicht angemeldet).'],
  [-8509]: ['permission', 'Der Untis-Account darf den Klassen-Stundenplan nicht sehen. Anderen Account nutzen oder UNTIS_CLASSES leer lassen (eigene Klasse).'],
  [-8998]: ['auth', 'Untis-Account vorübergehend gesperrt (zu viele Fehlversuche). Bitte später erneut.'],
  [-7004]: ['config', 'Untis: Datum außerhalb des Schuljahres.'],
};

function describe(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function classify(e: unknown, phase: 'login' | 'fetch'): UntisError {
  if (e instanceof UntisError) return e;
  if (e instanceof UntisRpcError) {
    const known = RPC_MESSAGES[e.code];
    if (known) return new UntisError(known[0], known[1], e.code);
    return new UntisError(phase === 'login' ? 'auth' : 'unknown', `Untis-Fehler ${e.code}: ${e.rpcMessage}`, e.code);
  }
  const err = e as { code?: string; response?: { status?: number }; message?: string };
  const status = err.response?.status;
  if (status && status >= 500) return new UntisError('network', `Untis-Server antwortet mit HTTP ${status} (Wartung/Überlastung).`);
  if (status === 404) return new UntisError('config', 'Untis-Server nicht gefunden (HTTP 404) – UNTIS_SERVER prüfen.');
  if (status === 401 || status === 403 || status === 429) {
    return new UntisError('network', `Untis-Server verweigert den Zugriff (HTTP ${status}) – Firewall/Proxy oder zu viele Anfragen.`);
  }
  if (err.code && /ENOTFOUND|EAI_AGAIN/.test(err.code)) return new UntisError('network', 'Untis-Server nicht erreichbar (DNS) – Internet/UNTIS_SERVER prüfen.');
  if (err.code && /ECONN|ETIMEDOUT|ECONNABORTED|EPIPE|ERR_NETWORK|ERR_SOCKET|EHOSTUNREACH|ENETUNREACH/.test(err.code)) {
    return new UntisError('network', `Untis nicht erreichbar (${err.code}).`);
  }
  const message = describe(e);
  if (phase === 'login' || /Failed to login|Login returned/i.test(message)) {
    return new UntisError('auth', `Untis-Login fehlgeschlagen: ${message.replace(/\{.*\}/s, '').trim()}`);
  }
  return new UntisError('unknown', `Untis-Abruf fehlgeschlagen: ${message}`);
}
